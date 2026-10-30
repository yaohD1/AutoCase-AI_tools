# -*- coding: utf-8 -*-
import time
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    page.goto("http://localhost:5173/", wait_until="networkidle")
    time.sleep(1.5)
    page.locator(".form-label-act").first.click()
    time.sleep(0.8)
    info = page.evaluate("""() => {
      const ta = document.querySelector('.form-dialog textarea');
      const wrap = ta.closest('.el-textarea');
      const cs = getComputedStyle(ta);
      return {taCls: ta.className, wrapCls: wrap.className, border: cs.border, bb: cs.borderBottom, shadow: cs.boxShadow, h: Math.round(ta.getBoundingClientRect().height)};
    }""")
    print(info)
    b.close()
