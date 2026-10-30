# -*- coding: utf-8 -*-
import time, json
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 900, "height": 800})
    page = ctx.new_page()
    page.goto("http://localhost:5173/settings", wait_until="networkidle")
    time.sleep(1.5)
    info = page.evaluate("""() => {
      const ths = [...document.querySelectorAll('.el-table__header th')].map(t => ({
        text: t.innerText.trim(), cls: t.className, disp: getComputedStyle(t).display
      }));
      const box = s => { const el = document.querySelector(s); const r = el.getBoundingClientRect();
        return {l: Math.round(r.left), w: Math.round(r.width)}; };
      return {ths, table: box('.set-table'), main: box('.set-main'), body: box('.set-body'), page: box('.settings-page')};
    }""")
    print(json.dumps(info, ensure_ascii=False, indent=1))
    b.close()
