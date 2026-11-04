# -*- coding: utf-8 -*-
import time
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
    page = ctx.new_page()
    page.goto("http://localhost:5173/", wait_until="commit")
    page.wait_for_selector(".upload-page", timeout=8000)
    info = page.evaluate("""() => {
      const root = document.querySelector('.upload-page')
      const items = [...document.querySelectorAll('.enter-item')]
      const tabs = document.querySelector('.mode-tabs')
      return {
        cls: root.className,
        items: items.map(el => el.getAnimations().map(a => ({n: a.animationName, d: a.effect.getTiming().delay, dur: a.effect.getTiming().duration}))),
        rule: tabs.getAnimations({subtree: true}).map(a => ({n: a.animationName, d: a.effect.getTiming().delay, dur: a.effect.getTiming().duration, pe: a.effect.target === tabs ? 'self' : 'pseudo'})),
        tabAnim: document.querySelectorAll('.mode-tab')[0].getAnimations().map(a => ({n: a.animationName, d: a.effect.getTiming().delay}))
      }
    }""")
    print("首帧：", info)
    for t in (120, 420, 780, 1250):
        page.evaluate("(t) => document.getAnimations().forEach(a => { a.pause(); a.currentTime = t })", t)
        page.screenshot(path=f"shots/_anim2_{t}.png")
    page.evaluate("() => document.getAnimations().forEach(a => a.play())")
    time.sleep(0.5)
    # 静止态：看 tab 蓝条与那条线是否还对得齐
    page.screenshot(path="shots/_anim2_rest.png", clip={"x":60,"y":80,"width":700,"height":90})
    print("线位置：", page.evaluate("""() => {
      const tabs = document.querySelector('.mode-tabs')
      const r = tabs.getBoundingClientRect()
      const cs = getComputedStyle(tabs, '::after')
      return {rowBottom: r.bottom, h: cs.height, bg: cs.backgroundColor, tf: cs.transform}
    }"""))
    b.close()
