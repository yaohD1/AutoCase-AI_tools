# -*- coding: utf-8 -*-
import time
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
    page = ctx.new_page()
    page.goto("http://localhost:5173/", wait_until="networkidle")
    page.evaluate("() => document.querySelector('#app').__vue_app__.config.globalProperties.$router.push('/recording')")
    time.sleep(1.0)
    print(page.evaluate("""() => {
      const r = document.querySelector('.recording-page')
      const cs = r ? getComputedStyle(r) : null
      return {cls: r && r.className, h: r && r.getBoundingClientRect().height,
              docH: document.documentElement.scrollHeight, scrollY: window.scrollY,
              transform: cs && cs.transform}
    }"""))
    page.screenshot(path="shots/_anim_ws.png")
    b.close()
