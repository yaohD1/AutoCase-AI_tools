# -*- coding: utf-8 -*-
import time, json
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    page.goto("http://localhost:5173/", wait_until="networkidle")
    time.sleep(1.5)
    info = page.evaluate("""() => {
      const out = [];
      document.querySelectorAll('.config-panel .form-field').forEach(f => {
        const cs = getComputedStyle(f), r = f.getBoundingClientRect();
        out.push({h: Math.round(r.height), bb: cs.borderBottomWidth + ' ' + cs.borderBottomColor, pad: cs.padding});
      });
      const sel = document.querySelector('.config-panel .form-ctl');
      const w = sel.querySelector('.el-select__wrapper');
      const cs2 = getComputedStyle(w);
      return {fields: out, sel: {cls: sel.className, bb: cs2.borderBottomColor, shadow: cs2.boxShadow, bg: cs2.backgroundColor, h: cs2.height}};
    }""")
    print(json.dumps(info, ensure_ascii=False, indent=1))
    b.close()
