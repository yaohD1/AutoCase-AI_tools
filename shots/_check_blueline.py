# -*- coding: utf-8 -*-
import time
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
    page = ctx.new_page()
    page.goto("http://localhost:5173/", wait_until="networkidle")
    time.sleep(1.5)
    for i in range(4):
        page.locator(".form-ctl").nth(i).click()
        time.sleep(0.5)
        items = page.locator(".el-select-dropdown:visible .el-select-dropdown__item")
        if items.count(): items.first.click()
        time.sleep(0.5)
    page.mouse.move(700, 700)
    time.sleep(0.5)
    info = page.evaluate("""() => [...document.querySelectorAll('.form-ctl')].map((s, i) => {
      const w = s.querySelector('.el-select__wrapper');
      const cs = getComputedStyle(w);
      return {i, cls: w.className, bb: cs.borderBottomColor, bw: cs.borderBottomWidth, shadow: cs.boxShadow};
    })""")
    for x in info: print(x)
    page.screenshot(path="shots/upload-model2.png", full_page=True)
    b.close()
