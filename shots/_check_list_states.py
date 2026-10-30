# -*- coding: utf-8 -*-
"""验证列表页的两个交互态：勾选后的批量条、行 hover。"""
import time
from playwright.sync_api import sync_playwright

BASE = "http://localhost:5173"

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
    page = ctx.new_page()
    page.goto(BASE + "/cases", wait_until="networkidle")
    time.sleep(1.5)

    boxes = page.locator(".list-shell .el-table__body .el-checkbox")
    boxes.nth(0).click()
    boxes.nth(1).click()
    time.sleep(0.5)
    page.screenshot(path="shots/list-bulk.png")
    print("bulk bar text:", page.locator(".list-bulk").inner_text().replace("\n", " | "))

    # 取消选择
    page.locator(".list-bulk .list-act").first.click()
    time.sleep(0.4)
    print("bulk bar count after clear:", page.locator(".list-bulk").count())

    # hover 第 3 行
    page.locator(".list-shell .el-table__body tr").nth(2).hover()
    time.sleep(0.3)
    page.screenshot(path="shots/list-hover.png")

    b.close()
