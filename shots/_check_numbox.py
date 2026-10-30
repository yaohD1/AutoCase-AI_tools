# -*- coding: utf-8 -*-
import time, json
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    page.goto("http://localhost:5173/settings", wait_until="networkidle")
    time.sleep(1.5)
    page.locator(".panel-head .el-button", has_text="添加服务").click()
    time.sleep(1.0)
    info = page.evaluate("""() => {
      const out = [];
      document.querySelectorAll('.form-num').forEach(n => {
        const r = n.getBoundingClientRect(), cs = getComputedStyle(n);
        const w = n.querySelector('.el-input__wrapper').getBoundingClientRect();
        const inc = n.querySelector('.el-input-number__increase')?.getBoundingClientRect();
        const dec = n.querySelector('.el-input-number__decrease')?.getBoundingClientRect();
        out.push({
          cls: n.className, w: Math.round(r.width), h: Math.round(r.height),
          cssW: cs.width, minW: cs.minWidth, disp: cs.display,
          wrapper: {l: Math.round(w.left), r: Math.round(w.right)},
          inc: inc ? {l: Math.round(inc.left), t: Math.round(inc.top), w: Math.round(inc.width), h: Math.round(inc.height)} : null,
          dec: dec ? {l: Math.round(dec.left), t: Math.round(dec.top), w: Math.round(dec.width)} : null
        });
      });
      return out;
    }""")
    print(json.dumps(info, ensure_ascii=False, indent=1))
    b.close()
