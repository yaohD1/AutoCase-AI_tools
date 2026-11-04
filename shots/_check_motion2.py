# -*- coding: utf-8 -*-
import time
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
    page = ctx.new_page()
    # 1. 未知路径还能不能挂载
    page.goto("http://localhost:5173/foo", wait_until="networkidle")
    time.sleep(0.6)
    print("未知路径：", page.evaluate("() => ({html: document.querySelector('#app').innerHTML.length, url: location.pathname})"))
    # 2. 暗色下的入场
    page.goto("http://localhost:5173/", wait_until="commit")
    page.evaluate("() => localStorage.setItem('ac-theme','dark')")
    page.goto("http://localhost:5173/", wait_until="commit")
    page.wait_for_selector(".upload-page", timeout=8000)
    page.evaluate("() => document.getAnimations().forEach(a => { a.pause(); a.currentTime = 300 })")
    page.screenshot(path="shots/_anim_dark_300.png")
    page.evaluate("() => document.getAnimations().forEach(a => a.play())")
    time.sleep(0.8)
    page.evaluate("() => document.querySelector('#app').__vue_app__.config.globalProperties.$router.push('/cases')")
    time.sleep(0.15)
    page.evaluate("() => document.getAnimations().forEach(a => { a.pause(); a.currentTime = 300 })")
    page.screenshot(path="shots/_anim_dark_route.png")
    b.close()
