# -*- coding: utf-8 -*-
import time
from playwright.sync_api import sync_playwright

def pick(page, idx):
    page.locator(".form-ctl").nth(idx).click()
    time.sleep(0.6)
    items = page.locator(".el-select-dropdown:visible .el-select-dropdown__item")
    n = items.count()
    print("  dropdown", idx, "items:", n, "->", items.first.inner_text() if n else "-")
    if n:
        items.first.click()
        time.sleep(0.8)

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
    page = ctx.new_page()
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.goto("http://localhost:5173/", wait_until="networkidle")
    time.sleep(1.5)
    for i in range(4):
        pick(page, i)
    time.sleep(1.0)
    page.screenshot(path="shots/upload-model.png")
    print("models shown:", page.locator(".up-model").count())
    print("errors:", errs[:3])
    b.close()
