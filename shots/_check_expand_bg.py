# -*- coding: utf-8 -*-
import time, json
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    page.goto("http://localhost:5173/settings", wait_until="networkidle")
    time.sleep(1.5)
    page.locator(".set-nav-item").nth(1).click()
    time.sleep(1.2)
    page.locator(".set-table .el-table__expand-icon").first.click()
    time.sleep(1.0)
    info = page.evaluate("""() => {
      const cell = document.querySelector('.el-table__expanded-cell');
      const chain = [];
      let el = document.querySelector('.sprint-list');
      while (el && el !== document.body) {
        const cs = getComputedStyle(el);
        chain.push({tag: el.tagName, cls: (el.className || '').toString().slice(0, 60), bg: cs.backgroundColor, pad: cs.padding});
        el = el.parentElement;
      }
      return chain;
    }""")
    print(json.dumps(info, ensure_ascii=False, indent=1))
    b.close()
