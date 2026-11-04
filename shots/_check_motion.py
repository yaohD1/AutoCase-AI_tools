# -*- coding: utf-8 -*-
"""验证首帧阶梯入场、路由淡入与导航线。"""
import time
from playwright.sync_api import sync_playwright

URL = "http://localhost:5173/"

with sync_playwright() as p:
    b = p.chromium.launch()

    ctx = b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
    page = ctx.new_page()
    page.goto(URL, wait_until="commit")
    page.wait_for_selector(".upload-page", timeout=8000)

    info = page.evaluate("""() => {
      const root = document.querySelector('.upload-page')
      const items = [...document.querySelectorAll('.enter-item')]
      return {
        rootClass: root.className,
        count: items.length,
        anims: items.map(el => el.getAnimations().map(a => ({
          name: a.animationName,
          delay: a.effect.getTiming().delay,
          dur: a.effect.getTiming().duration
        }))),
        navLine: !!document.querySelector('.nav-line')
      }
    }""")
    print("首帧：", info)

    # 冻结在 120ms / 620ms，看两帧的实际状态
    for t in (120, 620):
        page.evaluate("""(t) => {
          document.getAnimations().forEach(a => { a.pause(); a.currentTime = t })
        }""", t)
        page.screenshot(path=f"shots/_anim_entry_{t}.png")

    page.evaluate("() => document.getAnimations().forEach(a => a.play())")
    time.sleep(0.4)
    end = page.evaluate("""() => ({
      cls: document.querySelector('.upload-page').className,
      op: [...document.querySelectorAll('.enter-item')].map(e => getComputedStyle(e).opacity)
    })""")
    print("演完：", end)

    # 刷新同一标签页：不该重播
    page.reload(wait_until="commit")
    page.wait_for_selector(".upload-page", timeout=8000)
    again = page.evaluate("""() => ({
      cls: document.querySelector('.upload-page').className,
      anims: document.getAnimations().filter(a => a.animationName === 'enter-rise').length
    })""")
    print("刷新后：", again)

    # 换页：淡入 + 导航线
    page.evaluate("""() => document.querySelector('#app').__vue_app__.config.globalProperties.$router.push('/cases')""")
    time.sleep(0.05)
    trans = page.evaluate("""() => {
      const el = document.querySelector('.list-page')
      const line = document.querySelector('.nav-line')
      return {
        enterActive: !!document.querySelector('.page-enter-active'),
        line: !!line,
        anim: line ? line.getAnimations().map(a => ({n: a.animationName, d: a.effect.getTiming().duration})) : [],
        opacity: el ? getComputedStyle(el).opacity : null
      }
    }""")
    print("换页中：", trans)
    page.evaluate("() => document.getAnimations().forEach(a => { a.pause(); a.currentTime = 90 })")
    page.screenshot(path="shots/_anim_route_90.png")
    time.sleep(0.6)
    page.screenshot(path="shots/_anim_cases.png")
    ctx.close()

    # 减少动态效果：一律不动
    ctx2 = b.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    p2 = ctx2.new_page()
    p2.goto(URL, wait_until="networkidle")
    time.sleep(0.4)
    rm = p2.evaluate("""() => ({
      cls: document.querySelector('.upload-page').className,
      anims: document.getAnimations().length,
      op: [...document.querySelectorAll('.enter-item')].map(e => getComputedStyle(e).opacity)
    })""")
    print("减少动效：", rm)
    p2.evaluate("""() => document.querySelector('#app').__vue_app__.config.globalProperties.$router.push('/cases')""")
    time.sleep(0.3)
    line = p2.evaluate("""() => {
      const l = document.querySelector('.nav-line')
      return l ? getComputedStyle(l).display : 'none(元素不存在)'
    }""")
    print("减少动效下的导航线：", line)
    ctx2.close()
    b.close()
