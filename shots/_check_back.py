# -*- coding: utf-8 -*-
import time
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    page.goto("http://localhost:5173/", wait_until="networkidle")
    time.sleep(1.0)
    page.evaluate("() => document.querySelector('#app').__vue_app__.config.globalProperties.$router.push('/cases')")
    time.sleep(0.8)
    page.evaluate("() => document.querySelector('#app').__vue_app__.config.globalProperties.$router.push('/')")
    for d in (0.02, 0.1, 0.25):
        time.sleep(d)
        print(d, page.evaluate("""() => {
          const el = document.querySelector('.upload-page')
          return el ? {cls: el.className, op: getComputedStyle(el).opacity, tf: getComputedStyle(el).transform} : null
        }"""))
    b.close()
