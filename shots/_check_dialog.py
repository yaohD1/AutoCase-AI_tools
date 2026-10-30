# -*- coding: utf-8 -*-
import time
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
    page = ctx.new_page()
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.goto("http://localhost:5173/cases", wait_until="networkidle")
    time.sleep(1.5)
    page.locator(".list-shell .el-table__body tr").first.locator(".list-act").first.click()
    time.sleep(1.2)
    dlg = page.locator(".el-dialog").first
    print("dialog visible:", dlg.is_visible(), "| title:", page.locator(".el-dialog__title").first.inner_text())
    page.screenshot(path="shots/case-dialog.png")
    print("errors:", errs[:3])
    b.close()
