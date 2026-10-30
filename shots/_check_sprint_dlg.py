# -*- coding: utf-8 -*-
import time
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
    page = ctx.new_page()
    page.goto("http://localhost:5173/", wait_until="networkidle")
    time.sleep(1.5)
    page.locator(".form-label-act").nth(1).click()
    time.sleep(0.8)
    page.mouse.move(200, 700)
    time.sleep(0.4)
    page.screenshot(path="shots/new-sprint.png")
    print("visible:", page.locator(".form-dialog").is_visible())
    b.close()
