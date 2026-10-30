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

    # 1) 添加服务
    page.locator(".panel-head .el-button", has_text="添加服务").click()
    time.sleep(1.0)
    page.screenshot(path="shots/set-add.png")
    print("添加弹窗:", page.locator(".el-dialog .form-block").count())

    # 2) 关闭，点第一行编辑
    page.keyboard.press("Escape")
    time.sleep(0.6)
    page.locator(".row-act", has_text="编辑").first.click()
    time.sleep(1.0)
    page.screenshot(path="shots/set-edit.png")
    print("编辑弹窗标题:", page.locator(".el-dialog__title").first.inner_text())
    print("服务商只读:", page.locator(".el-dialog .form-static").count())
    page.keyboard.press("Escape")
    time.sleep(0.6)

    # 3) 项目面板 → 新建项目
    page.locator(".set-nav-item").nth(1).click()
    time.sleep(1.2)
    page.locator(".panel-head .el-button", has_text="新建项目").click()
    time.sleep(1.0)
    page.screenshot(path="shots/set-newproject.png")
    page.keyboard.press("Escape")
    time.sleep(0.6)

    # 4) 展开 → 新建迭代
    page.locator(".set-table .el-table__expand-icon").first.click()
    time.sleep(0.8)
    page.locator(".sprint-list-head .row-act").first.click()
    time.sleep(1.0)
    page.screenshot(path="shots/set-newsprint.png")
    print("所属项目显示:", page.locator(".el-dialog .form-static").first.inner_text())

    print("errors:", errs[:3])
    b.close()
