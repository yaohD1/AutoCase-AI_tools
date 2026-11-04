# -*- coding: utf-8 -*-
import time
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
    page = ctx.new_page()
    page.goto("http://localhost:5173/", wait_until="networkidle")
    page.evaluate("() => document.querySelector('#app').__vue_app__.config.globalProperties.$router.push('/cases')")
    time.sleep(0.25)
    box = page.evaluate("""() => {
      const l = document.querySelector('.nav-line')
      const r = l.getBoundingClientRect()
      const cs = getComputedStyle(l)
      return {x:r.x, y:r.y, w:r.width, h:r.height, bg:cs.backgroundColor, tf:cs.transform, op:cs.opacity}
    }""")
    print("线：", box)
    page.evaluate("() => document.getAnimations().forEach(a => { a.pause(); a.currentTime = 300 })")
    page.screenshot(path="shots/_anim_line_crop.png", clip={"x":0,"y":40,"width":1440,"height":80})
    b.close()
