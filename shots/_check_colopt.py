# -*- coding: utf-8 -*-
import time, json
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 900, "height": 800})
    page = ctx.new_page()
    page.goto("http://localhost:5173/settings", wait_until="networkidle")
    time.sleep(1.5)
    page.locator(".set-nav-item").nth(1).click()
    time.sleep(1.2)
    info = page.evaluate("""() => {
      const ths = [...document.querySelectorAll('.el-table__header th')].map(t => ({
        text: t.innerText.trim(), cls: t.className, disp: getComputedStyle(t).display
      }));
      const tds = [...document.querySelectorAll('.el-table__body tr:first-child td')].map(t => ({
        cls: t.className.slice(0, 70)
      }));
      const table = document.querySelector('.set-table').getBoundingClientRect();
      const inner = document.querySelector('.el-table').getBoundingClientRect();
      return {ths, tds, tableW: Math.round(table.width), innerW: Math.round(inner.width)};
    }""")
    print(json.dumps(info, ensure_ascii=False, indent=1))
    b.close()
