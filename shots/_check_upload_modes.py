# -*- coding: utf-8 -*-
import time
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
    page = ctx.new_page()
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.goto("http://localhost:5173/", wait_until="networkidle")
    time.sleep(1.5)
    for label, name in [("知识库", "knowledge"), ("文档", "doc"), ("原型图", "image")]:
        page.locator(".mode-tab", has_text=label).first.click()
        time.sleep(1.2)
        page.screenshot(path=f"shots/upload-{name}.png")
        title = page.locator(".upload-title").inner_text()
        print(name, "->", title)
    print("errors:", errs[:3])
    b.close()
