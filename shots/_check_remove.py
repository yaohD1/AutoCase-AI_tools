# -*- coding: utf-8 -*-
import time
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.goto("http://localhost:5173/", wait_until="networkidle")
    time.sleep(1.2)
    page.set_input_files("input[type=file]", ["shots/audit-cases.png", "shots/audit-modules.png"])
    time.sleep(1.5)
    page.keyboard.press("Escape")
    time.sleep(0.6)
    print("before:", page.locator(".file-preview-item").count())
    page.locator(".file-preview-badge").first.click()
    time.sleep(0.6)
    print("after remove:", page.locator(".file-preview-item").count())
    page.screenshot(path="shots/upload-two.png")
    print("errors:", errs[:2])
    b.close()
