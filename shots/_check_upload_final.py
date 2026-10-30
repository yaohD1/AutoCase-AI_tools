# -*- coding: utf-8 -*-
import time
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.launch()
    # 1. 桌面空态 + 三种模式
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
    page = ctx.new_page()
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.goto("http://localhost:5173/", wait_until="networkidle")
    time.sleep(1.5)
    page.screenshot(path="shots/up-empty.png")
    for label, name in [("文档", "doc"), ("知识库", "knowledge")]:
        page.locator(".mode-tab", has_text=label).first.click()
        time.sleep(1.0)
        page.screenshot(path=f"shots/up-{name}.png")
    page.locator(".mode-tab", has_text="原型图").first.click()
    time.sleep(0.8)

    # 2. 窄屏
    page.set_viewport_size({"width": 1024, "height": 900})
    time.sleep(1.0)
    page.screenshot(path="shots/up-narrow.png", full_page=True)
    print("narrow docH:", page.evaluate("document.documentElement.scrollHeight"))
    print("errors:", errs[:3])
    b.close()
