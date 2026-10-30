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
    page.locator(".form-label-act").first.click()
    time.sleep(0.8)
    page.screenshot(path="shots/new-project.png")
    print("dialog:", page.locator(".el-dialog").first.is_visible())
    print("errors:", errs[:2])
    b.close()
