# -*- coding: utf-8 -*-
import time
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    page.goto("http://localhost:5173/", wait_until="networkidle")
    time.sleep(1.5)
    info = page.evaluate("""() => {
      const u = document.querySelector('.upload-dragger');
      const s = getComputedStyle(u);
      return {
        dir: s.flexDirection,
        kids: [...u.children].map(c => ({cls: c.className, h: Math.round(c.getBoundingClientRect().height)})),
        upper: u.parentElement.className,
        panelKids: [...u.parentElement.children].map(c => c.className)
      };
    }""")
    print(info)
    b.close()
