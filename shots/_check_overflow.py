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
    page.locator(".set-table .el-table__expand-icon").first.click()
    time.sleep(0.8)
    info = page.evaluate("""() => {
      const box = s => { const el = document.querySelector(s); if (!el) return null;
        const r = el.getBoundingClientRect(); return {l: Math.round(r.left), r: Math.round(r.right), w: Math.round(r.width)}; };
      const table = document.querySelector('.el-table');
      const wrap = document.querySelector('.el-table__body-wrapper');
      return {
        docW: document.documentElement.scrollWidth,
        body: box('.set-body'), main: box('.set-main'),
        card: box('.set-table'), table: box('.el-table'),
        innerW: table ? Math.round(table.scrollWidth) : null,
        wrapScroll: wrap ? wrap.scrollWidth : null,
        wrapClient: wrap ? wrap.clientWidth : null,
        sprintRow: box('.sprint-row')
      };
    }""")
    print(json.dumps(info, ensure_ascii=False, indent=1))
    b.close()
