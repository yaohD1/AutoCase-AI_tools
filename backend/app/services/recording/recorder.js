(() => {
  // Injected before page scripts. Emits user actions to Python via __pwEmit.
  if (window.__pwRecorderInstalled) return;
  window.__pwRecorderInstalled = true;
  const state = { pick: false };
  window.__pwSetPick = (v) => { state.pick = !!v; };

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

  document.addEventListener('click', (e) => {
    const el = e.target.closest(INTERACTIVE) || e.target;
    if (state.pick) {
      e.preventDefault();
      e.stopImmediatePropagation();
      e.stopPropagation();
      emit({ type: 'pick', snap: snap(el) });
      return;
    }
    emit({ type: 'click', snap: snap(el) });
  }, true);

  document.addEventListener('input', (e) => {
    // Browsers fire `input` on <select> too; the `change` handler records selects.
    if (state.pick || e.target.tagName === 'SELECT') return;
    const el = e.target;
    emit({ type: 'fill', snap: snap(el), value: el.value });
  }, true);

  document.addEventListener('change', (e) => {
    const el = e.target;
    if (state.pick || el.tagName !== 'SELECT') return;
    const opt = el.options[el.selectedIndex];
    emit({ type: 'select', snap: snap(el), value: el.value, label: opt ? opt.text : null });
  }, true);
})();
