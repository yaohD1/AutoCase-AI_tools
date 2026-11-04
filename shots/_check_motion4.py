# -*- coding: utf-8 -*-
import time
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
    page = ctx.new_page()
    page.goto("http://localhost:5173/", wait_until="commit")
    page.wait_for_selector(".upload-page", timeout=8000)
    print(page.evaluate("""() => {
      const t = document.querySelector('.mode-tabs')
      const r = t.getBoundingClientRect()
      return {
        nav: [Math.round(r.left), Math.round(r.width), getComputedStyle(t).transform],
        rowBottom: Math.round(r.bottom),
        pseudoAnim: t.getAnimations().map(a => ({n: a.animationName, pe: a.effect.pseudoElement})),
        items: [...document.querySelectorAll('.enter-item')].map(e => e.getAnimations().map(a => a.effect.getTiming().delay))
      }
    }"""))
    for t in (150, 350, 600, 900, 1250):
        page.evaluate("(t) => document.getAnimations().forEach(a => { a.pause(); a.currentTime = t })", t)
        page.screenshot(path=f"shots/_anim3_{t}.png")
    # 冻结在 200ms 量线的实际长度
    page.evaluate("() => document.getAnimations().forEach(a => { a.pause(); a.currentTime = 200 })")
    print("200ms 线：", page.evaluate("""() => {
      const t = document.querySelector('.mode-tabs')
      const cs = getComputedStyle(t, '::after')
      return {tf: cs.transform, origin: cs.transformOrigin, opacity: cs.opacity}
    }"""))
    # 结束后量位移是否归零
    page.evaluate("() => document.getAnimations().forEach(a => a.play())")
    time.sleep(0.8)
    print("落定：", page.evaluate("""() => {
      const h = document.querySelector('.upload-head').getBoundingClientRect()
      const c = document.querySelector('.upload-card').getBoundingClientRect()
      return {head: [Math.round(h.left), Math.round(h.top)], card: [Math.round(c.left), Math.round(c.top)],
              tf: [...document.querySelectorAll('.enter-item')].map(e => getComputedStyle(e).transform)}
    }"""))
    b.close()
