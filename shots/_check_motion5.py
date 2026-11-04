# -*- coding: utf-8 -*-
import time
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    # 静止态：tab 蓝条与横线是否重合
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=4)
    page = ctx.new_page()
    page.goto("http://localhost:5173/", wait_until="networkidle")
    time.sleep(1.6)
    page.screenshot(path="shots/_anim3_rest_tabs.png", clip={"x":110,"y":180,"width":320,"height":34})
    # 返回首页的时长
    page.evaluate("() => document.querySelector('#app').__vue_app__.config.globalProperties.$router.push('/cases')")
    time.sleep(0.7)
    page.evaluate("() => document.querySelector('#app').__vue_app__.config.globalProperties.$router.push('/')")
    time.sleep(0.06)
    print("返回首页 60ms：", page.evaluate("""() => {
      const el = document.querySelector('.upload-page')
      const cs = getComputedStyle(el)
      return {cls: el.className, op: +(+cs.opacity).toFixed(2), tf: cs.transform, trans: cs.transitionDuration}
    }"""))
    ctx.close()

    # 减少动效
    ctx2 = b.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    p2 = ctx2.new_page()
    p2.goto("http://localhost:5173/", wait_until="networkidle")
    time.sleep(0.5)
    print("减少动效：", p2.evaluate("""() => {
      const t = document.querySelector('.mode-tabs')
      return {anims: document.getAnimations().length,
              tf: getComputedStyle(t, '::after').transform,
              op: [...document.querySelectorAll('.enter-item')].map(e => getComputedStyle(e).opacity)}
    }"""))
    ctx2.close()
    b.close()
