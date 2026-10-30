# -*- coding: utf-8 -*-
import time
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
    page = ctx.new_page()
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.goto("http://localhost:5173/settings", wait_until="networkidle")
    time.sleep(1.8)
    page.screenshot(path="shots/set-ai.png")

    page.locator(".set-nav-item").nth(1).click()
    time.sleep(1.2)
    page.screenshot(path="shots/set-projects.png")
    page.locator(".set-table .el-table__expand-icon").first.click()
    time.sleep(1.0)
    page.mouse.move(700, 700)
    time.sleep(0.4)
    page.screenshot(path="shots/set-project-open.png")

    # 窄屏
    page.set_viewport_size({"width": 900, "height": 800})
    time.sleep(1.0)
    page.screenshot(path="shots/set-narrow.png", full_page=True)
    print("narrow docW:", page.evaluate("document.documentElement.scrollWidth"))
    print("errors:", errs[:3])
    b.close()
