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
    const noTextName = ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName);
    const name = el.getAttribute('aria-label') || label || (noTextName ? null : text);
    return {
      tag: el.tagName.toLowerCase(),
      type: el.getAttribute('type'),
      role: el.getAttribute('role') || implicitRole(el),
      name: name ? name.slice(0, 80) : null,
      label: label,
      placeholder: el.getAttribute('placeholder'),
      testid: el.getAttribute('data-testid'),
      text: text,
      id: el.id || null,
      attr_name: el.getAttribute('name'),
      css: cssPath(el),
    };
  };
  const emit = (payload) => {
    try { window.__pwEmit(JSON.stringify(payload)); } catch (e) { /* binding not ready */ }
  };
  const INTERACTIVE = 'a,button,input,select,textarea,label,[role=button],[role=link],[role=checkbox],[role=option]';

  // ---- 录制浮层：常驻面板（只在顶层文档）+ 即时徽标（每个 frame 各自一份）----
  // 面板状态由 Python 推送（__pwHudUpdate），徽标由 Python 在算出唯一定位后推送（__pwHudBadge）。
  const HUD_ID = '__autocase_hud';
  // 悬浮面板由后端开关控制（RECORDING_HUD_PANEL，默认关）；徽标始终保留。
  const hudPanelOn = window.__AUTOCASE_HUD_PANEL !== false;
  const isTopFrame = (() => { try { return window.top === window; } catch (e) { return false; } })();
  const hudState = { status: 'recording', pick: false, total: 0, ambiguous: 0, steps: [] };
  const KIND_LABEL = { goto: '打开', click: '点击', fill: '输入', select: '选择', new_page: '标签页', assert: '断言', wait: '等待', sleep: '等待' };
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

  document.addEventListener('click', (e) => {
    if (isHudEvent(e)) return;
    const el = e.target.closest(INTERACTIVE) || e.target;
    lastPoint = { x: e.clientX, y: e.clientY };
    lastEl = el;
    if (state.pick) {
      e.preventDefault();
      e.stopImmediatePropagation();
      e.stopPropagation();
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

