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
    time.sleep(1.5)

    # 1) 新建项目弹窗开关
    page.locator(".form-label-act").first.click()
    time.sleep(0.6)
    assert page.locator(".form-dialog").is_visible(), "新建项目弹窗没打开"
    page.locator(".form-dialog textarea").fill("临时说明（不提交）")
    page.keyboard.press("Escape")
    time.sleep(0.6)

    # 2) 选项目 / 迭代 / 两个模型，验证值能落到控件上
    for i in range(4):
        page.locator(".form-ctl").nth(i).click()
        time.sleep(0.5)
        items = page.locator(".el-select-dropdown:visible .el-select-dropdown__item")
        items.first.click()
        time.sleep(0.6)
    vals = page.evaluate("() => [...document.querySelectorAll('.form-ctl')].map(s => s.innerText.trim())")
    print("选中值:", vals)
    print("模型参数块:", page.locator(".up-model").count())

    # 3) 功能介绍弹窗：上传 → 先进标注模式 → 退出 → 完成关闭
    page.set_input_files("input[type=file]", "shots/audit-cases.png")
    time.sleep(2.0)
    assert page.locator(".desc-dialog").is_visible(), "介绍弹窗没打开"
    page.locator(".desc-dialog .el-button", has_text="标注交互").click()
    time.sleep(1.2)
    print("标注模式:", page.locator(".annotation-layout").count())
    page.locator(".crop-fullscreen-bar button", has_text="完成标注").click()
    time.sleep(0.8)
    print("退出标注:", page.locator(".annotation-layout").count())
    page.locator(".desc-dialog .el-button", has_text="完成").click()
    time.sleep(0.8)
    print("介绍弹窗已关闭:", not page.locator(".desc-dialog").is_visible())

    print("errors:", errs[:3])
    b.close()
