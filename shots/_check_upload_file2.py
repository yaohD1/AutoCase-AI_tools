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
    page.set_input_files("input[type=file]", "shots/audit-cases.png")
    time.sleep(1.5)
    # 关掉自动弹出的功能介绍框
    page.keyboard.press("Escape")
    time.sleep(0.8)
    print("dialog still open:", page.locator(".el-dialog").first.is_visible())
    page.screenshot(path="shots/upload-withfile.png")
    print("errors:", errs[:3])
    b.close()
