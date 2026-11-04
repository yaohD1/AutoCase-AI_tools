# -*- coding: utf-8 -*-
import time
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    bad = []
    page.on("response", lambda r: bad.append((r.status, r.url)) if r.status >= 400 else None)
    page.goto("http://localhost:5173/", wait_until="networkidle")
    for r in ["/scripts", "/scripts/records", "/recording", "/recording/records", "/settings", "/cases", "/modules"]:
        page.evaluate("(r) => document.querySelector('#app').__vue_app__.config.globalProperties.$router.push(r)", r)
        time.sleep(0.7)
    print(bad)
    b.close()
