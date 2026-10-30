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
    time.sleep(2.0)
    page.screenshot(path="shots/set-ai.png")

    # 表格有没有数据、key 是否打码
    info = page.evaluate("""() => ({
      rows: document.querySelectorAll('.set-table .el-table__body tr').length,
      keyTexts: [...document.querySelectorAll('.key-line')].map(e => e.innerText.trim()),
      navs: [...document.querySelectorAll('.set-nav-item')].map(e => e.innerText.split(String.fromCharCode(10)).join(' ')),
      docH: document.documentElement.scrollHeight
    })""")
    print("AI 页:", info)

    # 切到项目与迭代
    page.locator(".set-nav-item").nth(1).click()
    time.sleep(1.5)
    page.screenshot(path="shots/set-projects.png")
    print("项目行数:", page.locator(".set-table .el-table__body tr").count())

    # 展开第一行
    page.locator(".set-table .el-table__expand-icon").first.click()
    time.sleep(1.0)
    page.screenshot(path="shots/set-project-open.png")
    print("迭代行:", page.locator(".sprint-row").count(), "| 空态:", page.locator(".sprint-empty").count())

    print("errors:", errs[:3])
    b.close()
