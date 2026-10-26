// 把一批匹配元素全部框出来并编号，用来让用户「人眼确认」生成时到底会点到谁。
//
// 两个硬要求（都有实测依据）：
// 1. 编号顺序必须等于 locator 的匹配顺序 —— 用户选「2 号」，生成就得写 nth(1)。
//    两套顺序必须来自同一次 evaluate_all，所以这个函数只接收一个现成的 els。
// 2. 用页面坐标（rect + scrollX/Y）而不是视口坐标，否则用户一滚动框就错位。
//
// 浮层不进 DOM 流、pointer-events:none：不能挡住被测页面本身的点击。
// 页面导航后 document 换掉，浮层自然消失，不需要额外清理。
(els, active) => {
  const OLD = document.getElementById('__autocase_boxes');
  if (OLD) OLD.remove();

  const layer = document.createElement('div');
  layer.id = '__autocase_boxes';
  layer.setAttribute('data-autocase-overlay', '1');
  layer.style.cssText =
    'position:absolute;left:0;top:0;width:0;height:0;z-index:2147483647;pointer-events:none';

  const ROW_SELECTOR = 'tr, li, [role=row], [role=listitem], dd, article';
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();

  // 必须先滚动、再算坐标：滚动会改变 getBoundingClientRect 的结果，
  // 反过来做（先画后滚）框就会整体偏移一个滚动距离。
  if (els[active]) {
    try { els[active].scrollIntoView({ block: 'center', inline: 'nearest' }); } catch (e) { /* 忽略 */ }
  }

  const items = [];
  els.forEach((el, i) => {
    const rect = el.getBoundingClientRect();
    const visible = rect.width > 0 && rect.height > 0;
    const row = el.closest(ROW_SELECTOR);
    items.push({
      index: i,
      tag: el.tagName.toLowerCase(),
      text: clean(el.innerText || el.value || '').slice(0, 40),
      row: row ? clean(row.innerText).slice(0, 40) : null,
      visible: visible
    });
    if (!visible) return;

    const on = i === active;
    const box = document.createElement('div');
    box.style.cssText = [
      'position:absolute',
      'box-sizing:border-box',
      'border-radius:4px',
      'left:' + (rect.left + window.scrollX - 3) + 'px',
      'top:' + (rect.top + window.scrollY - 3) + 'px',
      'width:' + (rect.width + 6) + 'px',
      'height:' + (rect.height + 6) + 'px',
      'border:2px ' + (on ? 'solid #ff3b30' : 'dashed #8e8e93'),
      on ? 'background:rgba(255,59,48,.10)' : 'background:transparent'
    ].join(';');

    const badge = document.createElement('div');
    badge.textContent = String(i + 1);
    // 元素贴着页面顶部时，编号放上面会被裁掉，改放框内。
    const roomAbove = rect.top + window.scrollY > 25;
    badge.style.cssText = [
      'position:absolute',
      'left:' + (roomAbove ? '-2px' : '0px'),
      'top:' + (roomAbove ? '-19px' : '0px'),
      'padding:0 5px',
      'border-radius:' + (roomAbove ? '3px 3px 3px 0' : '3px 0 3px 3px'),
      'font:600 11px/16px -apple-system,"Segoe UI",sans-serif',
      'color:#fff',
      'background:' + (on ? '#ff3b30' : '#8e8e93')
    ].join(';');
    box.appendChild(badge);
    layer.appendChild(box);
  });

  document.documentElement.appendChild(layer);
  return items;
}
