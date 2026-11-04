# -*- coding: utf-8 -*-
import time
from playwright.sync_api import sync_playwright
routes = ["/", "/cases", "/modules", "/scripts", "/scripts/records", "/recording", "/recording/records", "/settings"]
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    msgs = []
    page.on("console", lambda m: msgs.append((m.type, m.text[:160])) if m.type in ("error", "warning") else None)
    page.goto("http://localhost:5173/", wait_until="networkidle")
    for r in routes:
        page.evaluate("(r) => document.querySelector('#app').__vue_app__.config.globalProperties.$router.push(r)", r)
        time.sleep(0.45)
        info = page.evaluate("""() => ({
          main: document.querySelector('.el-main').firstElementChild ? document.querySelector('.el-main').firstElementChild.className : 'EMPTY',
          scroll: window.scrollY,
          heads: [...document.querySelectorAll('.el-main > *')].map(e => e.className).slice(0,3)
        })""")
        print(r, "->", info)
    # 滚动后跳转是否回顶
    page.evaluate("() => document.querySelector('#app').__vue_app__.config.globalProperties.$router.push('/cases')")
    time.sleep(0.4)
    page.evaluate("() => window.scrollTo(0, 400)")
    page.evaluate("() => document.querySelector('#app').__vue_app__.config.globalProperties.$router.push('/modules')")
    time.sleep(0.45)
    print("滚动后跳转 scrollY =", page.evaluate("() => window.scrollY"))
    print("控制台：", msgs[:8])
    b.close()
