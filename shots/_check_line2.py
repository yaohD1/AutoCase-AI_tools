# -*- coding: utf-8 -*-
import time
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=4)
    page = ctx.new_page()
    page.goto("http://localhost:5173/", wait_until="networkidle")
    page.evaluate("() => document.querySelector('#app').__vue_app__.config.globalProperties.$router.push('/cases')")
    time.sleep(0.12)
    page.evaluate("() => document.getAnimations().forEach(a => { a.pause(); a.currentTime = 260 })")
    page.screenshot(path="shots/_anim_line_zoom.png", clip={"x":0,"y":48,"width":400,"height":20})
    b.close()
