# -*- coding: utf-8 -*-
import time
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    page.goto("http://localhost:5173/", wait_until="networkidle")
    page.evaluate("() => document.querySelector('#app').__vue_app__.config.globalProperties.$router.push('/recording/records')")
    for t in (0.05, 0.3, 0.8, 1.6):
        time.sleep(t if t == 0.05 else 0.25)
        print(t, page.evaluate("() => [...document.querySelectorAll('.el-main > *')].map(e => e.className)"))
    # 逐个路由看残留
    for r in ["/cases", "/modules", "/settings", "/scripts"]:
        page.evaluate("(r) => document.querySelector('#app').__vue_app__.config.globalProperties.$router.push(r)", r)
        time.sleep(1.2)
        print(r, page.evaluate("() => [...document.querySelectorAll('.el-main > *')].map(e => e.className)"))
    b.close()
