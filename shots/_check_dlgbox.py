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
      const dlg = document.querySelector('.form-dialog');
      const body = dlg.querySelector('.el-dialog__body');
      const form = dlg.querySelector('.form-block');
      const fields = [...dlg.querySelectorAll('.form-field')].map(f => {
        const r = f.getBoundingClientRect(), cs = getComputedStyle(f);
        return {top: Math.round(r.top), h: Math.round(r.height), bb: cs.borderBottomWidth + ' ' + cs.borderBottomColor, last: f === f.parentElement.lastElementChild};
      });
      const g = el => { const r = el.getBoundingClientRect(), cs = getComputedStyle(el); return {t: Math.round(r.top), b: Math.round(r.bottom), h: Math.round(r.height), pad: cs.padding, minH: cs.minHeight}; };
      return {dlg: g(dlg), body: g(body), form: g(form), fields};
    }""")
    import json; print(json.dumps(info, ensure_ascii=False, indent=1))
    b.close()
