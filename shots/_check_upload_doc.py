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
    time.sleep(1.2)
    page.locator(".mode-tab", has_text="文档").first.click()
    time.sleep(0.8)
    page.set_input_files("input[type=file]", "测试规范知识库.md")
    time.sleep(1.5)
    page.keyboard.press("Escape")
    time.sleep(0.6)
    page.screenshot(path="shots/upload-doc-file.png")
    print("previews:", page.locator(".file-preview-item").count(), "| errors:", errs[:2])
    b.close()
