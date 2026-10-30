# -*- coding: utf-8 -*-
import time
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    page.goto("http://localhost:5173/recording", wait_until="networkidle")
    time.sleep(2)
    info = page.evaluate("""() => {
      const out = [];
      document.querySelectorAll('.card-title').forEach(el => {
        const b = getComputedStyle(el, '::before');
        out.push({
          text: el.textContent.trim().slice(0, 12),
          card: el.closest('.el-card')?.className || '',
          content: b.content, display: b.display, width: b.width, bg: b.backgroundColor
        });
      });
      return out;
    }""")
    for i in info: print(i)
    b.close()
