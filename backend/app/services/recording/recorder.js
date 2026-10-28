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
  window.__pwSetPick = (v) => { applyPickMode(v); };

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
  // 结构路径：从目标往上拼，**每加一层就在页面上验一次唯一**。
  // 旧版只爬 6 层、从不验证：`div:nth-of-type(2) > div > button` 这种「每层都没有同名兄弟」
  // 的路径会在文档任意位置命中一堆（实测同一条路径最多命中 13 个），却照样当成一条候选，
  // 于是「结构定位」看着唯一，实际只能靠落库时的序号碰运气。
  // 现在的规则是「最短且唯一」：当前前缀只要在整页只命中一个元素就停手，再往上加层只是
  // 更长更脆；6 层内找不到就继续往上爬，爬到 CSS_PATH_MAX 还不唯一才返回最长的那条，
  // 交给上层的 count 机制按 .nth(i) 降级（至少那时候「重数不对」是能被发现的）。
  const CSS_PATH_MAX = 12;
  const cssPath = (el) => {
    const parts = [];
    // 元素在 shadow root 里时 document.querySelectorAll 看不到它，必须在同一个根里数。
    let scope = document;
    try {
      const root = el.getRootNode ? el.getRootNode() : null;
      if (root && root.querySelectorAll) scope = root;
    } catch (e) { /* 拿不到根就按 document 数 */ }
    let cur = el;
    while (cur && cur.nodeType === 1 && parts.length < CSS_PATH_MAX) {
      let part = cur.tagName.toLowerCase();
      const isRoot = cur === document.documentElement || !cur.parentElement;
      if (!isRoot) {
        const sibs = Array.from(cur.parentElement.children).filter(c => c.tagName === cur.tagName);
        if (sibs.length > 1) part += ':nth-of-type(' + (sibs.indexOf(cur) + 1) + ')';
      }
      parts.unshift(part);
      const path = parts.join(' > ');
      // 单看一个标签名不可能唯一，白数一次；但已经爬到根节点的必须验——
      // 从 html 一路 nth-of-type 下来的路径才是「一定唯一」的那条。
      if (parts.length >= 2 || isRoot) {
        let hits = -1;
        try { hits = scope.querySelectorAll(path).length; } catch (e) { hits = -1; }
        if (hits === 1) return path;
      }
      if (isRoot) break;
      cur = cur.parentElement;
    }
    return parts.join(' > ');
  };
  // 语义可点击元素：命中最内层图形时先按这张表向上找一个「人眼中的控件」。
  // 注意它不含 div/svg——图标按钮（div > svg + click handler）不在表里，
  // 这类元素靠下面的中心点校正兜住。
  const INTERACTIVE = 'a,button,input,select,textarea,label,[role=button],[role=link],[role=checkbox],[role=option]';
  // 元素中心点被别的节点盖住时，Playwright 点它会直接拒绝（"X intercepts pointer events"）。
  // 典型是图标按钮：`div > svg > rect` + 一条画在上面的箭头 `path`，录制会选中 rect，
  // 而 rect 的中心正好压在 path 上——人点得到（点的是旁边空隙），脚本点不到。
  // 处理办法是向上找「中心点落在自己内部」的最近祖先：那个祖先才是真正能点到的容器。
  // 只在 click / precount 上用：fill / select 的目标必须是元素本身，上提会填错东西。
  const LIFT_MAX = 6;
  const liftToClickable = (el) => {
    if (!el || el.nodeType !== 1) return { el: el, liftedFrom: null };
    let cur = el;
    for (let i = 0; i < LIFT_MAX; i++) {
      if (!cur || cur === document.body || cur === document.documentElement) break;
      const r = cur.getBoundingClientRect();
      // 零尺寸（还没渲染）或整个跑到视口外：判断不了，保持原样，
      // 不能因为量不出来就把用户的点击目标换掉。
      if (r.width <= 0 || r.height <= 0) break;
      let hit = null;
      try {
        hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      } catch (e) {
        hit = null;
      }
      if (!hit) break;
      if (hit === cur || cur.contains(hit)) {
        return { el: cur, liftedFrom: cur === el ? null : el };
      }
      cur = cur.parentElement;
    }
    // 一路爬到上限 / 到达 body 仍被挡：多半是弹层或遮罩，硬换目标只会点错地方，保原样。
    return { el: el, liftedFrom: null };
  };

  // 点击目标：先按语义向上取可点击祖先，再做中心点校正。
  const clickTargetOf = (raw) => {
    if (!raw || raw.nodeType !== 1) return { el: raw, liftedFrom: null };
    const semantic = (raw.closest && raw.closest(INTERACTIVE)) || raw;
    return liftToClickable(semantic);
  };

  // 表格行 / 列表项：把「这一行是哪一行」的数据采下来，交给 Python 侧挑行锚点。
  // 这里不判断哪个字段是业务键——候选键到底能不能唯一锁定这一行，必须在页面上真数一遍
  // 才知道（见 session._row_anchor）。靠正则猜「像编号的那个」，遇到两列都像编号就会锚错。
  const ROW_SELECTOR = 'tr, [role=row], li, [role=listitem]';
  const CELL_SELECTOR = 'td, th, [role=gridcell], [role=cell]';
  const cleanText = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const rowScopeOf = (el) => {
    let row = null;
    try { row = el.closest(ROW_SELECTOR); } catch (e) { row = null; }
    // 目标自己就是这一行（点整行）：没有更内层的元素可锚。
    if (!row || row === el) return null;
    let role = row.getAttribute('role');
    if (!role) {
      if (row.tagName === 'TR') role = 'row';
      else if (row.tagName === 'LI') role = 'listitem';
    }
    if (!role) return null;
    const cells = row.querySelectorAll(CELL_SELECTOR);
    const nodes = cells.length ? Array.prototype.slice.call(cells) : [row];
    const texts = [];
    for (let i = 0; i < nodes.length && texts.length < 8; i++) {
      const text = cleanText(nodes[i].innerText);
      // 太长的一律不要：行键要么是编号要么是短标题，一整段描述拿去匹配既慢又不稳。
      if (text && text.length <= 40) texts.push(text);
    }
    // tag 是给 RF 用的：Playwright 有 getByRole('row')，XPath 没有等价物，
    // 只能写成 //tr[...] / //li[...] / //div[@role='row'][...]，所以得知道这一行是什么标签。
    return texts.length ? { role: role, tag: String(row.tagName || '').toLowerCase(), texts: texts } : null;
  };

  const snap = (el, liftedFrom) => {
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
    // 原始 class 串：很多控件既没有 role 也没有文本，唯一的身份线索就是 class 里的组件名。
    // Python 侧从里面剥出「去掉构建哈希」的那半截（_sidebarListHeaderAdd_），
    // 变成 [class*="..."] 候选——见 locators.class_stem。只留够判定的长度，别把 IR 撑大。
    let classRaw = null;
    try {
      classRaw = el.getAttribute('class');
      if (classRaw) classRaw = classRaw.trim().slice(0, 240);
    } catch (e) { /* SVG 上拿不到就当没有 */ }
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
      class_raw: classRaw,
      // 只读框（日期选择器、下拉筛选的载体）点它是要展开面板，不是聚焦输入：
      // 这类元素上的 click 和按键都要如实记录。
      readonly: !!(el.readOnly || el.getAttribute('readonly') !== null),
      css: cssPath(el),
      // 元素在表格行 / 列表项里时，记下这一行与它各单元格的文本：
      // 目标自身定位不唯一时，生成侧用「行 + 行内元素」代替「第 3 行的按钮」。
      row_scope: rowScopeOf(el),
      // 这条操作发生在哪个页面：停止录制时只在这个页面上重算定位。
      // 在页面里读，比 Python 侧事后读 frame.url / timeOrigin 更准（不会和导航抢时序）。
      _doc_url: location.href,
      _doc_stamp: (window.performance && performance.timeOrigin) || null,
      _mark: mark,
      // 中心点被遮住、向上改用可点击容器时，记下原来的标签。
      // 界面拿它提示「这条定位上提过」，排查脚本点不到时能直接看出差异。
      lifted: liftedFrom ? String(liftedFrom.tagName || '').toLowerCase() : null,
    };
  };
  const emit = (payload) => {
    try { window.__pwEmit(JSON.stringify(payload)); } catch (e) { /* binding not ready */ }
  };

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

  // ---- 拾取模式：悬停高亮 + 模式提示 ----
  // 只做「看得见」：框住的那个元素，就是点下去会登记的那一个。
  // 关键是不能照抄 F12 直接框 e.target —— 拾取会把图标提升到它所属的按钮（clickTargetOf），
  // 高亮必须走同一个函数，否则会出现「框着小图标、登记整个按钮」的错位，比没有高亮更让人困惑。
  const PICK_ID = '__autocase_pick';
  const PICK_STYLE = `
    :host { all: initial; }
    .pk-box, .pk-tag, .pk-flash, .pk-bar {
      box-sizing: border-box;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
    }
    .pk-box {
      position: fixed; pointer-events: none; border-radius: 3px;
      border: 2px solid #0a84ff; background: rgba(10,132,255,.13);
    }
    .pk-tag {
      position: fixed; pointer-events: none; max-width: 72vw;
      padding: 2px 7px; border-radius: 4px; background: #0a84ff; color: #fff;
      font: 500 11.5px/17px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }
    .pk-flash {
      position: fixed; pointer-events: none; border-radius: 4px;
      border: 2px solid #34c759; background: rgba(52,199,89,.24);
      opacity: 0; transition: opacity .18s ease-out;
    }
    .pk-bar {
      position: fixed; top: 10px; left: 50%; transform: translateX(-50%);
      pointer-events: none; display: flex; align-items: center; gap: 7px;
      max-width: 92vw; padding: 6px 12px; border-radius: 999px;
      background: rgba(24,27,34,.94); color: #e6e8ee;
      box-shadow: 0 6px 22px rgba(0,0,0,.3);
      font-size: 12px; line-height: 1.35; font-weight: 500; white-space: nowrap;
    }
    .pk-bar-dot {
      width: 7px; height: 7px; border-radius: 50%; background: #ff9f0a; flex: none;
      box-shadow: 0 0 0 3px rgba(255,159,10,.22);
    }
    .pk-bar-hint { color: #9aa3b2; font-weight: 400; }
    .pk-bar-key {
      font: 500 11px/1 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      padding: 2px 5px; border-radius: 4px; background: rgba(255,255,255,.15); color: #e6e8ee;
    }
  `;
  const PICK_CURSOR_RULE = 'html, html * { cursor: crosshair !important; }';

  let pickHost = null;
  let pickRoot = null;
  let pickNodes = null;
  let pickEl = null;
  let pickRaf = 0;
  let pickLabel = '';
  let pickTagW = 0;
  let pickTagH = 0;
  let pickFlashTimer = null;
  let pickCursor = null;
  // 模式条在顶部固定不动，位置缓存下来：标签要避开它，但没必要每帧都去量。
  let pickBarBox = null;

  // 十字准星：拾取模式和普通操作必须一眼分得开。用构造式样式表，
  // 被测站点的 CSP style-src 拦不住它；退出时按原路摘掉，页面自己的 cursor 立刻回来。
  const applyPickCursor = (on) => {
    try {
      if (!on) {
        if (!pickCursor) return;
        if (pickCursor.sheet) {
          document.adoptedStyleSheets = Array.prototype.filter.call(
            document.adoptedStyleSheets, (s) => s !== pickCursor.sheet);
        } else if (pickCursor.node) {
          pickCursor.node.remove();
        }
        pickCursor = null;
        return;
      }
      if (pickCursor) return;
      if ('adoptedStyleSheets' in document && typeof CSSStyleSheet === 'function') {
        const sheet = new CSSStyleSheet();
        sheet.replaceSync(PICK_CURSOR_RULE);
        document.adoptedStyleSheets = Array.prototype.concat.call(
          document.adoptedStyleSheets, [sheet]);
        pickCursor = { sheet: sheet };
        return;
      }
      const node = document.createElement('style');
      node.textContent = PICK_CURSOR_RULE;
      (document.head || document.documentElement).appendChild(node);
      pickCursor = { node: node };
    } catch (e) {
      pickCursor = null;
    }
  };

  const syncPickBarBox = () => {
    const bar = pickRoot && pickRoot.querySelector('.pk-bar');
    if (!bar) { pickBarBox = null; return; }
    const r = bar.getBoundingClientRect();
    pickBarBox = { left: r.left, top: r.top, right: r.right, bottom: r.bottom };
  };

  const ensurePickLayer = () => {
    if (pickHost) return;
    if (!document.documentElement) {
      setTimeout(() => { if (state.pick) ensurePickLayer(); }, 30);
      return;
    }
    try {
      const host = document.createElement('div');
      host.id = PICK_ID;
      host.setAttribute('data-autocase-pick', '1');
      host.style.cssText =
        'position:fixed;left:0;top:0;width:0;height:0;z-index:2147483646;pointer-events:none;';
      if (!host.attachShadow) return;
      const root = host.attachShadow({ mode: 'closed' });
      // 高亮框/标签/确认闪框都用 fixed + 视口坐标：滚动时重算一次即可，
      // 不用像 absolute 那样再叠加 scrollX/Y（叠加错了就是整体偏移一个滚动距离）。
      root.innerHTML =
        '<div class="pk-box" style="display:none"></div>' +
        '<div class="pk-tag" style="display:none"></div>' +
        '<div class="pk-flash" style="display:none"></div>' +
        (window.innerHeight >= 240
          ? '<div class="pk-bar">'
            + '<span class="pk-bar-dot"></span><span>拾取模式</span>'
            + '<span class="pk-bar-hint">点击即登记 · '
            + '<span class="pk-bar-key">Alt</span> 真实点击 · '
            + '<span class="pk-bar-key">Esc</span> 退出</span>'
            + '</div>'
          : '');
      let styled = false;
      if ('adoptedStyleSheets' in root && typeof CSSStyleSheet === 'function') {
        try {
          const sheet = new CSSStyleSheet();
          sheet.replaceSync(PICK_STYLE);
          root.adoptedStyleSheets = [sheet];
          styled = true;
        } catch (e) { /* 退回 <style> */ }
      }
      if (!styled) {
        const style = document.createElement('style');
        style.textContent = PICK_STYLE;
        root.appendChild(style);
      }
      document.documentElement.appendChild(host);
      pickHost = host;
      pickRoot = root;
      pickNodes = {
        box: root.querySelector('.pk-box'),
        tag: root.querySelector('.pk-tag'),
        flash: root.querySelector('.pk-flash')
      };
      syncPickBarBox();
    } catch (e) {
      // 高亮是附加能力：出错就退回「没有高亮」，不能影响拾取本身。
      pickHost = null;
      pickRoot = null;
      pickNodes = null;
    }
  };

  const teardownPickLayer = () => {
    if (pickRaf) { cancelAnimationFrame(pickRaf); pickRaf = 0; }
    if (pickFlashTimer) { clearTimeout(pickFlashTimer); pickFlashTimer = null; }
    if (pickHost) { try { pickHost.remove(); } catch (e) { /* 已经随文档走了 */ } }
    pickHost = null;
    pickRoot = null;
    pickNodes = null;
    pickEl = null;
    pickLabel = '';
    pickTagW = 0;
    pickTagH = 0;
    pickBarBox = null;
  };

  const hidePickBox = () => {
    if (!pickNodes) return;
    pickNodes.box.style.display = 'none';
    pickNodes.tag.style.display = 'none';
  };

  const pickLabelOf = (el, rect) => {
    let s = el.tagName.toLowerCase();
    if (el.id) s += '#' + el.id;
    if (el.classList && el.classList.length) {
      s += '.' + Array.prototype.slice.call(el.classList, 0, 2).join('.');
    }
    s += '  ' + Math.round(rect.width) + ' × ' + Math.round(rect.height);
    return s.length > 96 ? s.slice(0, 95) + '…' : s;
  };

  const renderPickFrame = () => {
    pickRaf = 0;
    if (!state.pick || !pickNodes || !pickEl) return;
    // 元素已经被移除或收起（下拉关闭、列表重渲染）：收掉框，别让它停在空气上。
    if (!pickEl.isConnected) { hidePickBox(); return; }
    const rect = pickEl.getBoundingClientRect();
    if (!rect.width && !rect.height) { hidePickBox(); return; }

    const box = pickNodes.box;
    box.style.left = (rect.left - 2) + 'px';
    box.style.top = (rect.top - 2) + 'px';
    box.style.width = (rect.width + 4) + 'px';
    box.style.height = (rect.height + 4) + 'px';
    box.style.display = 'block';

    const tag = pickNodes.tag;
    const text = pickLabelOf(pickEl, rect);
    // 必须先显示再量尺寸：display:none 的元素 offsetWidth/Height 恒为 0，
    // 拿 0 去算位置会把标签顶到框内（第一次悬停时确实就是这么错的）。
    tag.style.display = 'block';
    if (text !== pickLabel) {
      tag.textContent = text;
      pickLabel = text;
      // 只有文字变了才量：每次渲染都读 offsetWidth 会强制一次同步重排，
      // 而 mousemove 一秒能来六十次。
      pickTagW = tag.offsetWidth;
      pickTagH = tag.offsetHeight;
    }
    // 上方放不下就压到框内顶部；右侧要出界就贴边，别让标签跑到屏幕外。
    let top = rect.top - pickTagH - 4;
    if (top < 2) top = Math.max(2, rect.top + 2);
    let left = rect.left;
    if (left + pickTagW > window.innerWidth - 4) {
      left = Math.max(2, window.innerWidth - pickTagW - 4);
    }
    // 压在顶部模式条上的标签会被盖掉——看不见的标签等于没有，翻到框下方。
    if (pickBarBox
        && left < pickBarBox.right && left + pickTagW > pickBarBox.left
        && top < pickBarBox.bottom && top + pickTagH > pickBarBox.top) {
      top = rect.top + rect.height + 4;
    }
    // 元素贴底时下翻也没地方，兜底压回视口内。
    if (top + pickTagH > window.innerHeight - 2) {
      top = Math.max(2, window.innerHeight - pickTagH - 2);
    }
    tag.style.left = left + 'px';
    tag.style.top = top + 'px';
  };

  // 和 click 的拾取分支一字不差：语义提升到可交互祖先，但**不做中心点校正**——
  // 拾取是「登记一个元素供以后引用」，不是「点它」，没必要为了能点到而换成外层容器。
  // 两条路径必须同一口径，否则会出现「框着这个、拾到那个」。
  const pickHoverTarget = (e) => {
    const raw = e.target;
    if (!raw || raw.nodeType !== 1) return null;
    try {
      return raw.closest(INTERACTIVE) || raw;
    } catch (err) {
      return null;
    }
  };

  // 点中之后闪一下绿框：不然高亮「啪」地消失，人分不清是拾到了还是点空了。
  const flashPicked = (el) => {
    if (!pickNodes || !el || !el.getBoundingClientRect) return;
    const rect = el.getBoundingClientRect();
    const f = pickNodes.flash;
    f.style.left = (rect.left - 3) + 'px';
    f.style.top = (rect.top - 3) + 'px';
    f.style.width = (rect.width + 6) + 'px';
    f.style.height = (rect.height + 6) + 'px';
    f.style.display = 'block';
    void f.offsetWidth;   // 同一帧里从 0 到 1 不触发过渡，强制一次样式计算
    f.style.opacity = '1';
    if (pickFlashTimer) clearTimeout(pickFlashTimer);
    pickFlashTimer = setTimeout(() => { f.style.opacity = '0'; }, 480);
  };

  const applyPickMode = (on) => {
    state.pick = !!on;
    // 面板上的状态跟着拾取模式走，不用等 Python 再推一轮。
    if (typeof window.__pwHudPick === 'function') window.__pwHudPick(state.pick);
    applyPickCursor(state.pick);
    if (state.pick) {
      ensurePickLayer();
    } else {
      teardownPickLayer();
    }
  };

  document.addEventListener('mousemove', (e) => {
    if (!state.pick) return;
    if (isHudEvent(e)) { hidePickBox(); return; }
    const el = pickHoverTarget(e);
    if (!el) return;
    if (el !== pickEl) {
      pickEl = el;
      pickLabel = '';
      pickTagW = 0;
      pickTagH = 0;
    }
    if (!pickRaf) pickRaf = requestAnimationFrame(renderPickFrame);
  }, true);

  // 滚动时元素在视口里的位置变了，框要跟着走（fixed 坐标不会自己动）。
  document.addEventListener('scroll', () => {
    if (!state.pick || !pickEl) return;
    if (!pickRaf) pickRaf = requestAnimationFrame(renderPickFrame);
  }, true);

  window.addEventListener('resize', () => {
    if (!state.pick) return;
    // 模式条是按视口居中的，窗口一变宽窄它就换位置，避让用的坐标要跟着更新。
    syncPickBarBox();
    if (pickEl && !pickRaf) pickRaf = requestAnimationFrame(renderPickFrame);
  });

  document.addEventListener('mouseout', (e) => {
    if (!state.pick) return;
    // relatedTarget 为空 = 鼠标离开了整个文档（进了别的 frame 或窗口外）。
    if (!e.relatedTarget) {
      pickEl = null;
      hidePickBox();
    }
  }, true);

  // Esc 退出拾取：拾取是个模态状态，出口不该只在编排面板上。
  // 状态在 Python 那边（工具栏按钮、轮询都读它），所以先通知后端、成功了再收本地 UI——
  // 反过来先收的话，后端没收到就会「页面已退出、面板还显示退出拾取」的错位。
  document.addEventListener('keydown', (e) => {
    if (!state.pick || e.key !== 'Escape') return;
    if (typeof window.__pwHudAction !== 'function') return;
    try {
      window.__pwHudAction('pick-off');
    } catch (err) {
      return;
    }
    // 拾取模式下 Esc 属于拾取，不该顺手把页面自己的弹窗关掉。
    e.preventDefault();
    e.stopPropagation();
    applyPickMode(false);
  }, true);

  // 只给自测和排障用：closed shadow root 外部拿不到内容，这里读闭包里的渲染状态。
  window.__pwPickSnapshot = () => ({
    mounted: !!pickHost,
    cursor: !!pickCursor,
    el: pickEl ? String(pickEl.tagName || '').toLowerCase() : null,
    elId: pickEl ? (pickEl.id || null) : null,
    box: pickNodes ? {
      left: parseFloat(pickNodes.box.style.left) || 0,
      top: parseFloat(pickNodes.box.style.top) || 0,
      width: parseFloat(pickNodes.box.style.width) || 0,
      height: parseFloat(pickNodes.box.style.height) || 0,
      display: pickNodes.box.style.display
    } : null,
    tag: pickNodes ? {
      text: pickNodes.tag.textContent || '',
      display: pickNodes.tag.style.display,
      left: parseFloat(pickNodes.tag.style.left) || 0,
      top: parseFloat(pickNodes.tag.style.top) || 0,
      width: pickNodes.tag.offsetWidth || 0,
      height: pickNodes.tag.offsetHeight || 0
    } : null,
    flash: pickNodes ? {
      display: pickNodes.flash.style.display,
      opacity: pickNodes.flash.style.opacity
    } : null,
    // 小尺寸 iframe 里不放模式条，否则会挤掉大半内容。
    bar: !!(pickRoot && pickRoot.querySelector('.pk-bar')),
    barRect: (() => {
      const node = pickRoot && pickRoot.querySelector('.pk-bar');
      if (!node) return null;
      const r = node.getBoundingClientRect();
      return {left: r.left, top: r.top, width: r.width, height: r.height};
    })()
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
    const picked = clickTargetOf(target);
    const el = picked.el;
    if (!el || clickDropped(el)) return;
    emit({ type: 'precount', snap: snap(el, picked.liftedFrom) });
  }, true);

  document.addEventListener('click', (e) => {
    if (isHudEvent(e)) return;
    // 键盘触发的 click（在搜索框按 Enter 提交表单时，浏览器会给提交按钮补一次 click）
    // detail 是 0，鼠标点击至少是 1。这条如果也录下来，回放就会在 press 之后又点一次提交，
    // 表单被提交两遍、后端收到两条记录。
    // 但焦点本来就在按钮上时的那次 click 是用户真正要的操作，必须留下。
    if (e.detail === 0 && !lastKeyOnClickable && Date.now() - lastKeyDownAt < 800) return;
    if (state.pick) {
      const el = pickHoverTarget(e) || e.target;
      lastPoint = { x: e.clientX, y: e.clientY };
      lastEl = el;
      // 默认拦截：拾取时误点「删除」不该真的把数据删掉。
      // 但滚动菜单、下拉、折叠面板里的元素必须先展开才点得到，所以按住 Alt 时只拾取、不拦截
      // （Alt 是全局修饰键，页面自己很少用；拦不拦由用户按不按决定，不做元素白名单猜测）。
      if (!e.altKey) {
        e.preventDefault();
        e.stopImmediatePropagation();
        e.stopPropagation();
      }
      // 先闪一下再上报：高亮「啪」地消失，人分不清是拾到了还是点空了。
      flashPicked(el);
      emit({ type: 'pick', snap: snap(el) });
      return;
    }
    // 拾取模式不做中心点校正：用户亲手指的是哪个元素，就该是哪个。
    const picked = clickTargetOf(e.target);
    lastPoint = { x: e.clientX, y: e.clientY };
    lastEl = picked.el;
    if (state.paused) return;
    emit({ type: 'click', snap: snap(picked.el, picked.liftedFrom) });
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

