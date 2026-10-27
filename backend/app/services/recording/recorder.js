(() => {
  // Injected before page scripts. Emits user actions to Python via __pwEmit.
  // 嵌入模式下外壳页就是 AutoCase 自己：在这里装录制会把「插入断言」这类点击也录进去。
  try {
    const skipOrigin = window.__AUTOCASE_SKIP_ORIGIN;
    if (skipOrigin && location.origin === skipOrigin && location.pathname.indexOf('/recording') >= 0) return;
  } catch (e) { /* 拿不到 location 就算了 */ }
  if (window.__pwRecorderInstalled) return;
  window.__pwRecorderInstalled = true;
  const state = { pick: false, paused: false };
  // 只有这些 input 类型能用 Playwright 的 fill() 赋值；其余（checkbox/radio/range/color/file）
  // 的交互由 click 记录，input 事件必须忽略，否则会生成跑不通的 fill 步骤。
  const FILL_INPUT_TYPES = new Set([
    'text', 'search', 'tel', 'url', 'email', 'password', 'number',
    'date', 'datetime-local', 'month', 'week', 'time',
  ]);
  // 暂停：浏览器照常操作，但不再产生步骤（同 Playwright codegen 的 Pause）。
  window.__pwSetPaused = (v) => { state.paused = !!v; };
  window.__pwSetPick = (v) => {
    state.pick = !!v;
    // 面板上的状态跟着拾取模式走，不用等 Python 再推一轮。
    if (typeof window.__pwHudPick === 'function') window.__pwHudPick(state.pick);
  };

  const labelText = (el) => {
    if (el.id) {
      const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (l) return l.innerText.trim();
    }
    const p = el.closest('label');
    return p ? p.innerText.trim() : null;
  };
  const implicitRole = (el) => {
    const t = el.tagName.toLowerCase();
    if (t === 'button') return 'button';
    if (t === 'a' && el.href) return 'link';
    if (t === 'select') return 'combobox';
    if (t === 'textarea') return 'textbox';
    if (t === 'input') {
      const ty = (el.type || 'text');
      if (ty === 'button' || ty === 'submit' || ty === 'reset') return 'button';
      if (ty === 'checkbox') return 'checkbox';
      if (ty === 'radio') return 'radio';
      return 'textbox';
    }
    return null;
  };
  const cssPath = (el) => {
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType === 1 && parts.length < 6) {
      let part = cur.tagName.toLowerCase();
      if (cur === document.documentElement) { parts.unshift(part); break; }
      const sibs = cur.parentElement ? Array.from(cur.parentElement.children).filter(c => c.tagName === cur.tagName) : [];
      if (sibs.length > 1) part += ':nth-of-type(' + (sibs.indexOf(cur) + 1) + ')';
      parts.unshift(part);
      cur = cur.parentElement;
    }
    return parts.join(' > ');
  };
  const snap = (el) => {
    // 下拉框的 innerText 包含全部选项文字，不能作为名称。
    const text = el.tagName === 'SELECT' ? null : ((el.innerText || '').trim().slice(0, 80) || null);
    const label = labelText(el);
    // Python 侧拿不到 DOM 节点，只能靠一个临时属性认出「用户点的到底是哪一个」，
    // 它在计数完就被摘掉（session._count 末尾的 CLEAR_MARK_JS），不会留在页面上。
    // pointerdown 已经打过标记时要复用：click 的预计数缓存就是按这个 mark 对上的。
    let mark = null;
    try {
      mark = el.getAttribute('data-autocase-target');
      if (!mark) {
        mark = 'ac' + Math.random().toString(36).slice(2, 10);
        el.setAttribute('data-autocase-target', mark);
      }
    } catch (e) {
      mark = null;
    }
    const noTextName = ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName);
    const name = el.getAttribute('aria-label') || label || (noTextName ? null : text);
    return {
      tag: el.tagName.toLowerCase(),
      type: el.getAttribute('type'),
      role: el.getAttribute('role') || implicitRole(el),
      // role_attr 只保留显式写的 role。隐式角色（<button> 的 button）在 xpath 里不存在，
      // RF 投影必须区分这两种情况，否则会生成 //*[@role='button'] 这种命中 0 条的候选。
      role_attr: el.getAttribute('role'),
      name: name ? name.slice(0, 80) : null,
      label: label,
      // aria-label 单独留一份：name 会被 label / 文本覆盖，投影 xpath 时需要原始值。
      aria_label: el.getAttribute('aria-label'),
      placeholder: el.getAttribute('placeholder'),
      testid: el.getAttribute('data-testid'),
      text: text,
      id: el.id || null,
      attr_name: el.getAttribute('name'),
      // 只读框（日期选择器、下拉筛选的载体）点它是要展开面板，不是聚焦输入：
      // 这类元素上的 click 和按键都要如实记录。
      readonly: !!(el.readOnly || el.getAttribute('readonly') !== null),
      css: cssPath(el),
      // 这条操作发生在哪个页面：停止录制时只在这个页面上重算定位。
      // 在页面里读，比 Python 侧事后读 frame.url / timeOrigin 更准（不会和导航抢时序）。
      _doc_url: location.href,
      _doc_stamp: (window.performance && performance.timeOrigin) || null,
      _mark: mark,
    };
  };
  const emit = (payload) => {
    try { window.__pwEmit(JSON.stringify(payload)); } catch (e) { /* binding not ready */ }
  };
  const INTERACTIVE = 'a,button,input,select,textarea,label,[role=button],[role=link],[role=checkbox],[role=option]';

  // ---- 按键 ----
  // 只录「有语义的键」：文字输入本身已经由 fill 覆盖，如果把 keydown 全录下来，
  // 打一个词就会变成十几个步骤。Playwright 的键名和 KeyboardEvent.key 基本一致，
  // 少数几个（Esc / Left / Space）需要映射。
  const KEY_ALIASES = {
    ' ': 'Space', 'Spacebar': 'Space', 'Esc': 'Escape',
    'Left': 'ArrowLeft', 'Right': 'ArrowRight', 'Up': 'ArrowUp', 'Down': 'ArrowDown', 'Del': 'Delete'
  };
  const RECORD_KEYS = new Set([
    'Enter', 'Escape', 'Tab', 'Backspace', 'Delete',
    'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight',
    'Home', 'End', 'PageUp', 'PageDown', 'Space'
  ]);
  // 单独按下修饰键没有意义，它只作为组合键的前缀，单独触发时丢掉。
  const MODIFIER_KEYS = new Set(['Shift', 'Control', 'Alt', 'Meta', 'CapsLock']);
  // 光标在文本框里时，这些键只改文本或移动光标，不构成独立动作：
  // 值的变化已经由 input 事件的全量快照（fill）表达，回放时 fill 是直接赋值，
  // 多出来的退格 / 方向键不但没用，还会打断「同一元素连续输入合并成一条」的规则
  // （输错一个字改一下，就变成 退格 + 输入、退格 + 输入 好几条）。
  const TEXT_EDIT_KEYS = new Set([
    'Backspace', 'Delete', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight',
    'Home', 'End', 'PageUp', 'PageDown', 'Space'
  ]);
  // 只有能被 fill 赋值的文本框才算「文本编辑」；contenteditable / 富文本没有 fill 步骤，
  // 它上面的按键仍然是唯一能表达动作的东西，不能吞。
  const isTextEntry = (el) => {
    if (!el || el.nodeType !== 1) return false;
    // 只读框里的按键不是「改文本」：它多半是日期/下拉组件自己的快捷键，值也不由 fill 表达。
    if (el.readOnly === true) return false;
    const tag = el.tagName.toLowerCase();
    if (tag === 'textarea') return true;
    if (tag === 'input') return FILL_INPUT_TYPES.has((el.type || 'text').toLowerCase());
    return false;
  };
  let lastKeyDownAt = 0;
  // 上次按下的键落在哪个元素上。区分两种「键盘合成的 click」：
  // 焦点本来就在按钮上（要的就是这次点击），还是回车提交表单顺带点了提交按钮（不该另记一步）。
  let lastKeyOnClickable = false;

  const isClickable = (el) => {
    if (!el || el.nodeType !== 1) return false;
    const tag = el.tagName.toLowerCase();
    if (tag === 'button') return true;
    if (tag === 'a' && el.hasAttribute('href')) return true;
    if (tag === 'input') {
      const ty = (el.type || '').toLowerCase();
      return ty === 'button' || ty === 'submit' || ty === 'reset' || ty === 'image'
        || ty === 'checkbox' || ty === 'radio';
    }
    return !!el.closest('[role=button],[role=link],[role=checkbox],[role=tab]');
  };

  document.addEventListener('keydown', (e) => {
    if (isHudEvent(e)) return;
    lastKeyDownAt = Date.now();
    lastKeyOnClickable = isClickable(e.target);
    // 拾取模式里 Alt 是「只拾取不拦截」的修饰键，按下去不该产生步骤。
    if (state.paused || state.pick) return;
    if (MODIFIER_KEYS.has(e.key)) return;
    const base = KEY_ALIASES[e.key] || e.key;
    if (!RECORD_KEYS.has(base)) return;
    const el = e.target || {};
    // 焦点在按钮/链接上按 Enter 或空格，浏览器会合成一次 click；click 那边已经录过了，
    // 再记一条 press 会让回放点两次（提交表单、删两次）。
    if ((base === 'Enter' || base === 'Space') && lastKeyOnClickable) return;
    const combo = [];
    if (e.ctrlKey) combo.push('Control');
    if (e.altKey) combo.push('Alt');
    if (e.shiftKey) combo.push('Shift');
    if (e.metaKey) combo.push('Meta');
    combo.push(base);
    // 文本框里的退格、方向键、空格：记下来只会把一次输入切成几段，结果由 fill 表达。
    // Enter / Escape / Tab 例外——它们在单行输入框里是「提交 / 取消 / 跳焦点」，有独立语义。
    if (isTextEntry(el) && TEXT_EDIT_KEYS.has(base)) return;
    lastPoint = { x: e.clientX || 0, y: e.clientY || 0 };
    lastEl = el;
    emit({ type: 'press', snap: snap(el), key: combo.join('+') });
  }, true);

  // ---- 录制浮层：常驻面板（只在顶层文档）+ 即时徽标（每个 frame 各自一份）----
  // 面板状态由 Python 推送（__pwHudUpdate），徽标由 Python 在算出唯一定位后推送（__pwHudBadge）。
  const HUD_ID = '__autocase_hud';
  // 悬浮面板由后端开关控制（RECORDING_HUD_PANEL，默认关）；徽标始终保留。
  const hudPanelOn = window.__AUTOCASE_HUD_PANEL !== false;
  const isTopFrame = (() => { try { return window.top === window; } catch (e) { return false; } })();
  const hudState = { status: 'recording', pick: false, total: 0, ambiguous: 0, steps: [] };
  const KIND_LABEL = { goto: '打开', click: '点击', fill: '输入', select: '选择', press: '按键', dialog: '弹窗', new_page: '标签页', assert: '断言', wait: '等待', sleep: '等待' };
  const STATUS_LABEL = { unique: '唯一', fragile: '结构', ambiguous: '不唯一', stale: '已变化' };
  const STATE_LABEL = { recording: '录制中', stopped: '已停止', cancelled: '已取消', idle_timeout: '已超时', closed: '浏览器已关闭', failed: '录制失败' };
  let hudHost = null;
  let hudRoot = null;
  let hudNodes = null;
  let hudCollapsed = false;
  let lastPoint = null;
  let lastEl = null;
  let badgeTimer = null;

  const HUD_PANEL_HTML = `
    <div class="ac-panel">
      <div class="ac-head">
        <span class="ac-dot"></span>
        <span class="ac-title">AutoCase 录制</span>
        <span class="ac-count">0 步</span>
        <button class="ac-fold" type="button" title="收起面板">‒</button>
      </div>
      <ol class="ac-list"></ol>
      <div class="ac-empty">在页面上操作，这里会实时显示定位</div>
      <div class="ac-foot">
        <span class="ac-state">录制中</span>
        <button class="ac-undo" type="button" title="删除最后一步">撤销上一步</button>
      </div>
    </div>
    <button class="ac-pill" type="button" hidden>录制中 · 0 步</button>`;

  const HUD_STYLE = `
    :host { all: initial; }
    .ac-panel, .ac-pill, .ac-badge {
      box-sizing: border-box; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
      -webkit-font-smoothing: antialiased;
    }
    .ac-panel {
      pointer-events: auto; width: 300px; border-radius: 12px; overflow: hidden;
      background: rgba(24,27,34,.95); color: #e6e8ee; border: 1px solid rgba(255,255,255,.09);
      box-shadow: 0 14px 36px rgba(0,0,0,.34);
    }
    .ac-head { display: flex; align-items: center; gap: 8px; padding: 9px 11px; cursor: move; user-select: none;
      background: rgba(255,255,255,.04); border-bottom: 1px solid rgba(255,255,255,.07); }
    .ac-dot { width: 7px; height: 7px; border-radius: 50%; background: #34c759; flex: none; box-shadow: 0 0 0 3px rgba(52,199,89,.18); }
    .ac-dot.is-pick { background: #ff9f0a; box-shadow: 0 0 0 3px rgba(255,159,10,.2); }
    .ac-title { font-size: 12.5px; font-weight: 600; letter-spacing: .2px; }
    .ac-count { margin-left: auto; font-size: 11.5px; color: #9aa3b2; font-variant-numeric: tabular-nums; }
    .ac-fold { border: 0; background: transparent; color: #9aa3b2; font-size: 15px; line-height: 1; cursor: pointer; padding: 2px 4px; border-radius: 5px; }
    .ac-fold:hover { background: rgba(255,255,255,.1); color: #e6e8ee; }
    .ac-list { list-style: none; margin: 0; padding: 6px; max-height: 168px; overflow-y: auto; }
    .ac-list::-webkit-scrollbar { width: 6px; }
    .ac-list::-webkit-scrollbar-thumb { background: rgba(255,255,255,.16); border-radius: 3px; }
    .ac-step { padding: 6px 7px; border-radius: 8px; }
    .ac-step + .ac-step { margin-top: 2px; }
    .ac-step:last-child { background: rgba(255,255,255,.06); }
    .ac-row { display: flex; align-items: center; gap: 6px; min-width: 0; }
    .ac-no { flex: none; font-size: 11px; color: #7d8798; font-variant-numeric: tabular-nums; }
    .ac-kind { flex: none; font-size: 11px; color: #8ab4ff; }
    .ac-name { flex: 1; min-width: 0; font-size: 12px; color: #e6e8ee; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .ac-tag { flex: none; font-size: 10px; line-height: 1.6; padding: 0 5px; border-radius: 4px; }
    .ac-tag.ok { color: #6ee7a0; background: rgba(52,199,89,.16); }
    .ac-tag.warn { color: #f5c26b; background: rgba(255,159,10,.16); }
    .ac-tag.bad { color: #ff8a80; background: rgba(255,69,58,.18); }
    .ac-tag.none { display: none; }
    .ac-loc { display: block; margin-top: 3px; padding-left: 24px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 10.5px; color: #93a0b4; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .ac-empty { padding: 14px 12px 16px; font-size: 11.5px; color: #8b95a6; text-align: center; }
    .ac-foot { display: flex; align-items: center; gap: 8px; padding: 7px 9px; border-top: 1px solid rgba(255,255,255,.07); background: rgba(0,0,0,.14); }
    .ac-state { font-size: 11px; color: #9aa3b2; }
    .ac-undo { margin-left: auto; border: 1px solid rgba(255,255,255,.14); background: transparent; color: #cdd4df;
      font-size: 11px; padding: 3px 9px; border-radius: 6px; cursor: pointer; }
    .ac-undo:hover:not(:disabled) { background: rgba(255,255,255,.1); color: #fff; }
    .ac-undo:disabled { opacity: .4; cursor: default; }
    .ac-pill { pointer-events: auto; border: 0; cursor: pointer; padding: 7px 12px; border-radius: 999px;
      background: rgba(24,27,34,.95); color: #e6e8ee; font-size: 12px; border: 1px solid rgba(255,255,255,.12);
      box-shadow: 0 8px 22px rgba(0,0,0,.3); }
    .ac-badge { position: fixed; pointer-events: none; padding: 5px 10px; border-radius: 7px; font-size: 11.5px; font-weight: 600;
      color: #fff; white-space: nowrap; opacity: 0; transition: opacity .25s ease; z-index: 2147483647;
      box-shadow: 0 6px 18px rgba(0,0,0,.28); }
    .ac-badge.ok { background: rgba(28,138,74,.96); }
    .ac-badge.warn { background: rgba(190,103,0,.96); }
    .ac-badge.bad { background: rgba(196,42,35,.96); }
  `;

  const isHudEvent = (e) => {
    if (!hudHost) return false;
    const path = typeof e.composedPath === 'function' ? e.composedPath() : [];
    return path.indexOf(hudHost) !== -1;
  };

  // 记录成功的瞬间在被操作元素上描边，配合 Python 推来的徽标说明定位结果。
  const flash = (el, tone) => {
    try {
      const prevOutline = el.style.outline;
      const prevOffset = el.style.outlineOffset;
      el.style.outline = tone === 'bad' ? '2px solid #ff453a' : tone === 'warn' ? '2px solid #ff9f0a' : '2px solid #34c759';
      el.style.outlineOffset = '2px';
      setTimeout(() => { el.style.outline = prevOutline; el.style.outlineOffset = prevOffset; }, 1200);
    } catch (e) { /* 元素可能已被移除 */ }
  };

  const showBadge = (payload) => {
    if (!hudRoot) return;
    let badge = hudRoot.querySelector('.ac-badge');
    if (!badge) {
      badge = document.createElement('div');
      badge.className = 'ac-badge';
      hudRoot.appendChild(badge);
    }
    badge.className = 'ac-badge ' + (payload.tone || 'ok');
    badge.textContent = payload.text || '';
    const point = lastPoint || { x: 24, y: 96 };
    const x = Math.min(Math.max(8, point.x + 14), window.innerWidth - 280);
    const y = Math.max(8, point.y - 38);
    badge.style.left = x + 'px';
    badge.style.top = y + 'px';
    badge.style.opacity = '1';
    if (lastEl) flash(lastEl, payload.tone || 'ok');
    clearTimeout(badgeTimer);
    badgeTimer = setTimeout(() => { badge.style.opacity = '0'; }, 1700);
  };

  const renderHud = () => {
    if (!hudNodes) return;
    const { list, empty, count, state, dot, undo, pill } = hudNodes;
    count.textContent = hudState.total + ' 步';
    state.textContent = hudState.pick ? '拾取模式' : (STATE_LABEL[hudState.status] || hudState.status);
    dot.className = 'ac-dot' + (hudState.pick ? ' is-pick' : '');
    undo.disabled = !hudState.total;
    pill.textContent = (hudState.pick ? '拾取中' : '录制中') + ' · ' + hudState.total + ' 步' +
      (hudState.ambiguous ? ' · ' + hudState.ambiguous + ' 处不唯一' : '');
    list.innerHTML = '';
    (hudState.steps || []).forEach((step) => {
      const tone = step.status === 'unique' ? 'ok' : step.status === 'ambiguous' ? 'bad' : step.status === 'fragile' ? 'warn' : 'none';
      const li = document.createElement('li');
      li.className = 'ac-step';
      li.innerHTML = '<div class="ac-row"><span class="ac-no">#' + step.no + '</span>' +
        '<span class="ac-kind">' + (KIND_LABEL[step.type] || step.type) + '</span>' +
        '<span class="ac-name"></span>' +
        '<span class="ac-tag ' + tone + '">' + (STATUS_LABEL[step.status] || '') + '</span></div>' +
        (step.locator ? '<code class="ac-loc"></code>' : '');
      li.querySelector('.ac-name').textContent = step.title || '（无名称）';
      const code = li.querySelector('.ac-loc');
      if (code) code.textContent = step.locator;
      list.appendChild(li);
    });
    empty.hidden = (hudState.steps || []).length > 0;
    list.hidden = (hudState.steps || []).length === 0;
  };

  const setCollapsed = (on) => {
    if (!hudNodes) return;
    hudCollapsed = on;
    hudNodes.panel.hidden = on;
    hudNodes.pill.hidden = !on;
  };

  const enableDrag = (handle, host) => {
    let start = null;
    handle.addEventListener('pointerdown', (e) => {
      // header 里的按钮（收起）要自己接 click；拖拽一旦 setPointerCapture，
      // 后续 click 会被重定向到 header，按钮就永远点不动了。
      if (e.target && e.target.closest && e.target.closest('button')) return;
      const rect = host.getBoundingClientRect();
      start = { x: e.clientX, y: e.clientY, left: rect.left, top: rect.top };
      try { handle.setPointerCapture(e.pointerId); } catch (err) { /* 忽略 */ }
      e.preventDefault();
    });
    handle.addEventListener('pointermove', (e) => {
      if (!start) return;
      const left = Math.min(Math.max(8, start.left + e.clientX - start.x), Math.max(8, window.innerWidth - 130));
      const top = Math.min(Math.max(8, start.top + e.clientY - start.y), Math.max(8, window.innerHeight - 50));
      host.style.left = left + 'px';
      host.style.top = top + 'px';
      host.style.right = 'auto';
      host.style.bottom = 'auto';
    });
    const end = () => { start = null; };
    handle.addEventListener('pointerup', end);
    handle.addEventListener('pointercancel', end);
  };

  const buildHud = () => {
    if (hudRoot || !document.documentElement) return;
    try {
      // 必须是 closed shadow root：Playwright 的 text / css 引擎会穿透 open shadow DOM，
      // 面板里显示的元素名会被数成同名候选，把「唯一定位」误判成不唯一（实测复现过）。
      // closed 树对外不可见，内部引用只存在本闭包和 window.__pwHudSnapshot 里。
      const stale = document.getElementById(HUD_ID);
      if (stale) stale.remove();
      const host = document.createElement('div');
      host.id = HUD_ID;
      host.setAttribute('data-autocase-hud', '1');
      // 宽度用 max-content：收起成胶囊时宿主跟着变窄，胶囊才会贴在右下角而不是跑到面板左边缘。
      host.style.cssText = 'position:fixed;right:18px;bottom:18px;width:max-content;height:auto;z-index:2147483647;pointer-events:none;';
      if (!host.attachShadow) return;
      const root = host.attachShadow({ mode: 'closed' });
      // ShadowRoot 是 DocumentFragment，没有 insertAdjacentHTML，只能用 innerHTML。
      if (isTopFrame && hudPanelOn) root.innerHTML = HUD_PANEL_HTML;
      // 用构造式样式表，避免被被测站点的 CSP style-src 拦掉。
      let styled = false;
      if ('adoptedStyleSheets' in root && typeof CSSStyleSheet === 'function') {
        try {
          const sheet = new CSSStyleSheet();
          sheet.replaceSync(HUD_STYLE);
          root.adoptedStyleSheets = [sheet];
          styled = true;
        } catch (e) { /* 退回 <style> */ }
      }
      if (!styled) {
        const style = document.createElement('style');
        style.textContent = HUD_STYLE;
        root.appendChild(style);
      }
      document.documentElement.appendChild(host);
      hudHost = host;
      hudRoot = root;
      // 面板关闭时只留宿主给徽标用（徽标是 position:fixed，不依赖面板）。
      if (!isTopFrame || !hudPanelOn) return;
      const pick = (sel) => root.querySelector(sel);
      hudNodes = {
        panel: pick('.ac-panel'), pill: pick('.ac-pill'), list: pick('.ac-list'), empty: pick('.ac-empty'),
        count: pick('.ac-count'), state: pick('.ac-state'), dot: pick('.ac-dot'), undo: pick('.ac-undo')
      };
      pick('.ac-fold').addEventListener('click', () => setCollapsed(true));
      hudNodes.pill.addEventListener('click', () => setCollapsed(false));
      hudNodes.undo.addEventListener('click', () => {
        try { window.__pwHudAction('undo'); } catch (e) { /* binding not ready */ }
      });
      enableDrag(pick('.ac-head'), host);
      renderHud();
    } catch (e) {
      // 浮层是附加能力：任何异常都不该影响录制本身。
      hudRoot = null;
      hudNodes = null;
    }
  };

  const ensureHud = () => {
    if (!document.documentElement) { setTimeout(ensureHud, 30); return; }
    buildHud();
  };

  window.__pwHudUpdate = (payload) => {
    Object.assign(hudState, payload || {});
    if (!hudRoot) buildHud();
    renderHud();
  };
  window.__pwHudBadge = (payload) => {
    if (!hudRoot) buildHud();
    showBadge(payload || {});
  };
  window.__pwHudPick = (on) => {
    hudState.pick = !!on;
    if (hudRoot) renderHud();
  };
  // 只给自测和排障用：closed shadow root 外部拿不到面板内容，这里读闭包里的渲染状态。
  window.__pwHudSnapshot = () => ({
    mounted: !!hudRoot,
    collapsed: hudCollapsed,
    total: hudState.total,
    ambiguous: hudState.ambiguous,
    pick: hudState.pick,
    status: hudState.status,
    steps: hudState.steps.map((step) => ({
      no: step.no, type: step.type, title: step.title, locator: step.locator, status: step.status
    }))
  });

  ensureHud();
  document.addEventListener('DOMContentLoaded', ensureHud);

  // 这条点击会不会真的变成一步（文本框的点击由随后的 fill 表达、select/label 由 change 表达）。
  // 不会成步的元素不做预计数，省一次没用的扫描。判断口径要和 Python 的 click 过滤保持一致。
  const clickDropped = (el) => {
    const tag = el.tagName.toLowerCase();
    if (tag === 'input') {
      const ty = (el.type || 'text').toLowerCase();
      const textLike = !['button', 'submit', 'reset', 'checkbox', 'radio'].includes(ty);
      return textLike && !el.readOnly;
    }
    if (tag === 'textarea') return !el.readOnly;
    return tag === 'select' || tag === 'label';
  };

  // 按下鼠标时先让 Python 数一遍候选。下拉项、选中态、菜单按钮这类元素会被 click
  // 自己从页面上带走，等 click 回来再数只能数到 0（未核实）；按下这一刻它还在。
  // 只做缓存，不产生步骤；click 会用同一个 mark 取这份结果（见 snap 里的复用）。
  document.addEventListener('pointerdown', (e) => {
    if (isHudEvent(e)) return;
    if (e.button !== 0 || state.pick || state.paused) return;
    const target = e.target;
    if (!target || target.nodeType !== 1) return;
    const el = target.closest(INTERACTIVE) || target;
    if (!el || clickDropped(el)) return;
    emit({ type: 'precount', snap: snap(el) });
  }, true);

  document.addEventListener('click', (e) => {
    if (isHudEvent(e)) return;
    // 键盘触发的 click（在搜索框按 Enter 提交表单时，浏览器会给提交按钮补一次 click）
    // detail 是 0，鼠标点击至少是 1。这条如果也录下来，回放就会在 press 之后又点一次提交，
    // 表单被提交两遍、后端收到两条记录。
    // 但焦点本来就在按钮上时的那次 click 是用户真正要的操作，必须留下。
    if (e.detail === 0 && !lastKeyOnClickable && Date.now() - lastKeyDownAt < 800) return;
    const el = e.target.closest(INTERACTIVE) || e.target;
    lastPoint = { x: e.clientX, y: e.clientY };
    lastEl = el;
    if (state.pick) {
      // 默认拦截：拾取时误点「删除」不该真的把数据删掉。
      // 但滚动菜单、下拉、折叠面板里的元素必须先展开才点得到，所以按住 Alt 时只拾取、不拦截
      // （Alt 是全局修饰键，页面自己很少用；拦不拦由用户按不按决定，不做元素白名单猜测）。
      if (!e.altKey) {
        e.preventDefault();
        e.stopImmediatePropagation();
        e.stopPropagation();
      }
      emit({ type: 'pick', snap: snap(el) });
      return;
    }
    if (state.paused) return;
    emit({ type: 'click', snap: snap(el) });
  }, true);

  document.addEventListener('input', (e) => {
    if (isHudEvent(e)) return;
    if (state.paused) return;
    // Browsers fire `input` on <select> too; the `change` handler records selects.
    if (state.pick || e.target.tagName === 'SELECT') return;
    const el = e.target;
    // checkbox / radio 点一下也会触发 input（值变成 on），但那场交互已经由 click 记录；
    // 记成 fill 会生成 `fill('on')`，Playwright 在非文本输入上直接报错。range / color / file 同理。
    if (el.tagName === 'INPUT' && !FILL_INPUT_TYPES.has((el.type || 'text').toLowerCase())) return;
    if (el.tagName !== 'INPUT' && el.tagName !== 'TEXTAREA') return;
    lastPoint = { x: e.clientX, y: e.clientY };
    lastEl = el;
    emit({ type: 'fill', snap: snap(el), value: el.value });
  }, true);

  document.addEventListener('change', (e) => {
    if (isHudEvent(e)) return;
    if (state.paused) return;
    const el = e.target;
    if (state.pick || el.tagName !== 'SELECT') return;
    const opt = el.options[el.selectedIndex];
    lastPoint = { x: e.clientX, y: e.clientY };
    lastEl = el;
    emit({ type: 'select', snap: snap(el), value: el.value, label: opt ? opt.text : null });
  }, true);
})();

