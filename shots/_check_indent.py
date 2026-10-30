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
      const box = sel => { const el = document.querySelector(sel); if (!el) return null;
        const r = el.getBoundingClientRect(); return {l: Math.round(r.left), r: Math.round(r.right)}; };
      const rows = [...document.querySelectorAll('.sprint-row')].slice(0, 1).map(el => {
        const r = el.getBoundingClientRect(); return {l: Math.round(r.left), r: Math.round(r.right)};
      });
      const acts = [...document.querySelectorAll('.sprint-row .sprint-acts')].slice(0,1).map(el => {
        const r = el.getBoundingClientRect(); return {l: Math.round(r.left), r: Math.round(r.right)};
      });
      const projActs = [...document.querySelectorAll('.el-table__body tr:not(.el-table__expanded-cell-row) .row-acts')].slice(0,1).map(el => {
        const r = el.getBoundingClientRect(); return {l: Math.round(r.left), r: Math.round(r.right)};
      });
      return {
        table: box('.set-table'), cell: box('.el-table__expanded-cell'),
        cellInner: box('.el-table__expanded-cell .cell'),
        sprintList: box('.sprint-list'),
        projName: (() => { const el = document.querySelector('.cell-main'); const r = el.getBoundingClientRect(); return {l: Math.round(r.left)}; })(),
        rows, acts, projActs
      };
    }""")
    print(json.dumps(info, ensure_ascii=False, indent=1))
    b.close()
