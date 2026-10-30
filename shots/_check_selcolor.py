# -*- coding: utf-8 -*-
import time
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    page.goto("http://localhost:5173/", wait_until="networkidle")
    time.sleep(1.5)
    for i in range(3):
        page.locator(".up-ctl").nth(i).click()
        time.sleep(0.5)
        items = page.locator(".el-select-dropdown:visible .el-select-dropdown__item")
        if items.count(): items.first.click()
        time.sleep(0.5)
    info = page.evaluate("""() => {
      const out = [];
      document.querySelectorAll('.up-ctl').forEach((sel, i) => {
        const ph = sel.querySelector('.el-select__placeholder');
        const si = sel.querySelector('.el-select__selected-item');
        const inner = sel.querySelector('.el-select__selected-item span');
        const g = el => el ? {tag: el.tagName, cls: el.className, color: getComputedStyle(el).color, fs: getComputedStyle(el).fontSize} : null;
        out.push({i, ph: g(ph), si: g(si), inner: g(inner), text: sel.innerText.trim().slice(0,20)});
      });
      return out;
    }""")
    for x in info: print(x)
    b.close()
