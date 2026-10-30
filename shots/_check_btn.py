# -*- coding: utf-8 -*-
import time
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    page.goto("http://localhost:5173/modules", wait_until="networkidle")
    time.sleep(1.5)
    info = page.evaluate("""() => {
      const btn = [...document.querySelectorAll('.list-head-actions .el-button')].map(b => {
        const s = getComputedStyle(b);
        return {text: b.textContent.trim(), disabled: b.classList.contains('is-disabled'), border: s.borderColor, color: s.color, bg: s.backgroundColor, h: s.height};
      });
      const shell = document.querySelector('.list-shell').getBoundingClientRect();
      const pager = document.querySelector('.list-pager').getBoundingClientRect();
      return {btn, shellBottom: Math.round(shell.bottom), pagerBottom: Math.round(pager.bottom), docH: document.documentElement.scrollHeight};
    }""")
    print(info)
    b.close()
