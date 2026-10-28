# -*- coding: utf-8 -*-
"""找出页面上「真正难以定位」的元素：没有 text / aria / label / placeholder / testid。

这类才是「触发不唯一」的来源，也是前面 5 个目标测不出来的部分。
"""
import json

from playwright.sync_api import sync_playwright

import harness

OUT = harness.OUT

SCAN_JS = harness.HELPERS + """
() => {
  const out = [];
  const seen = new Set();
  // 所有「看起来可点」的元素：有 cursor:pointer、或是交互标签、或带 click 语义的 role
  const clickable = [...document.querySelectorAll('*')].filter((el) => {
    const st = getComputedStyle(el);
    const t = el.tagName.toLowerCase();
    const role = (el.getAttribute('role') || '').toLowerCase();
    return st.cursor === 'pointer' || ['a', 'button', 'input', 'textarea'].includes(t)
      || ['button', 'link', 'tab', 'menuitem', 'option'].includes(role);
  });
  for (const el of clickable) {
    const r = el.getBoundingClientRect();
    if (r.width < 6 || r.height < 6) continue;
    if (seen.has(el)) continue;
    seen.add(el);
    const snap = snapshot(el);
    // 语义信号：有任何一个就不算「难定位」
    const semantic = snap.text || snap.aria_label || snap.label || snap.placeholder
      || snap.testid || snap.attr_name;
    if (semantic) continue;
    out.push({
      tag: snap.tag, cls: snap.cls.slice(0, 70), role: snap.role,
      box: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
      css: snap.css,
      parentText: norm(el.parentElement ? el.parentElement.innerText : '').slice(0, 40),
      parentCls: el.parentElement ? (el.parentElement.getAttribute('class') || '').slice(0, 50) : '',
    });
  }
  return out;
}"""

COUNT_DUP_JS = """
() => {
  const counts = {};
  document.querySelectorAll('svg').forEach((el) => {
    const cls = el.getAttribute('class') || '(none)';
    counts[cls] = (counts[cls] || 0) + 1;
  });
  return Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 8);
}"""


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(ignore_https_errors=True, viewport={'width': 1440, 'height': 900})
        page = ctx.new_page()
        print('登录...')
        print(harness.open_main(page))
        page.screenshot(path=OUT + '/main2.png')

        hard = page.evaluate(SCAN_JS)
        print('\n无语义可点元素数:', len(hard))
        for item in hard[:25]:
            print(json.dumps(item, ensure_ascii=False))

        print('\nsvg class 重复度 Top:')
        for cls, n in page.evaluate(COUNT_DUP_JS):
            print('  %-40s %d' % (cls, n))

        json.dump(hard, open(OUT + '/hard-elements.json', 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=1)
        browser.close()


if __name__ == '__main__':
    main()
