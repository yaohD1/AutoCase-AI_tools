# -*- coding: utf-8 -*-
import time
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    page.goto("http://localhost:5173/foo/bar", wait_until="networkidle")
    time.sleep(0.5)
    print(page.evaluate("() => ({url: location.pathname, has: !!document.querySelector('.upload-page'), cls: document.querySelector('.upload-page') && document.querySelector('.upload-page').className})"))
    b.close()
