# -*- coding: utf-8 -*-
import time, json
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))

    captured = []
    def on_route(route):
        if route.request.method in ("PUT", "POST", "DELETE"):
            captured.append({"m": route.request.method, "u": route.request.url.split("/api")[-1],
                             "b": route.request.post_data})
            route.fulfill(status=200, content_type="application/json", body='{"success": true}')
        else:
            route.continue_()
    page.route("**/api/ai-configs**", on_route)
    page.route("**/api/projects**", on_route)
    page.route("**/api/sprints**", on_route)

    page.goto("http://localhost:5173/settings", wait_until="networkidle")
    time.sleep(1.8)

    # 1) 停用：应该发 PUT 且 body 只有 is_active
    page.locator(".row-act", has_text="停用").first.click()
    time.sleep(1.0)

    # 2) 编辑第一条：不填 Key 直接保存，body 里不该出现 api_key
    page.locator(".row-act", has_text="编辑").first.click()
    time.sleep(0.8)
    page.locator(".el-dialog:visible .el-button", has_text="保存").click()
    time.sleep(1.0)

    # 3) 新建项目：不填名字应被拦住（不发请求）
    page.locator(".set-nav-item").nth(1).click()
    time.sleep(1.0)
    page.locator(".panel-head .el-button", has_text="新建项目").click()
    time.sleep(0.7)
    before = len(captured)
    page.locator(".el-dialog:visible .el-button", has_text="创建项目").click()
    time.sleep(0.8)
    blocked = len(captured) == before
    page.locator(".el-dialog:visible .el-input__inner").first.fill("临时项目（不提交）")
    page.locator(".el-dialog:visible .el-button", has_text="创建项目").click()
    time.sleep(1.0)

    print("请求:", json.dumps(captured, ensure_ascii=False))
    print("空名字被拦住:", blocked)
    print("errors:", errs[:3])
    b.close()
