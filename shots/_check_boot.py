# -*- coding: utf-8 -*-
"""收尾核对：落定后的首页、二次加载、减少动效、暗色、以及幕布是否真的挡点击。"""
import time
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.launch()

    # 1. 冷启动全程（真实速度）
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
    page = ctx.new_page()
    page.goto("http://localhost:5173/", wait_until="networkidle")
    time.sleep(2.4)
    print("落定：", page.evaluate("""() => {
      const el = document.getElementById('ac-boot')
      const root = document.querySelector('.upload-page')
      return {
        boot: !!el, attr: document.documentElement.getAttribute('data-boot'),
        cls: root.className, op: +getComputedStyle(root).opacity,
        anims: root.getAnimations().length,
        entered: sessionStorage.getItem('ac-entered'),
        booted: sessionStorage.getItem('ac-booted')
      }
    }"""))
    page.screenshot(path="shots/_b6_rest.png")

    # 2. 同会话刷新：不该再盖幕布，也不该有 boot 淡入
    page.reload(wait_until="networkidle")
    time.sleep(0.6)
    print("刷新：", page.evaluate("""() => {
      const root = document.querySelector('.upload-page')
      return {boot: !!document.getElementById('ac-boot'), cls: root.className,
              op: +getComputedStyle(root).opacity, anims: root.getAnimations().length}
    }"""))
    ctx.close()

    # 3. 暗色
    ctx2 = b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
    ctx2.add_init_script("try{localStorage.setItem('ac-theme','dark')}catch(e){}")
    p2 = ctx2.new_page()
    p2.goto("http://localhost:5173/", wait_until="commit")
    p2.wait_for_selector("#ac-boot", timeout=8000)
    time.sleep(0.25)
    print("暗色：", p2.evaluate("""() => {
      const b = document.getElementById('ac-boot')
      return {bg: getComputedStyle(b).backgroundColor,
              edge: getComputedStyle(b, '::after').backgroundColor,
              word: getComputedStyle(b.querySelector('.boot-word')).color,
              page: +getComputedStyle(document.querySelector('.upload-page')).opacity}
    }"""))
    p2.screenshot(path="shots/_b6_dark.png")
    ctx2.close()

    # 4. 减少动效
    ctx3 = b.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    p3 = ctx3.new_page()
    p3.goto("http://localhost:5173/", wait_until="networkidle")
    time.sleep(0.4)
    print("减少动效：", p3.evaluate("""() => ({
      boot: !!document.getElementById('ac-boot'),
      attr: document.documentElement.getAttribute('data-boot'),
      anims: document.getAnimations().length,
      op: +getComputedStyle(document.querySelector('.upload-page')).opacity
    })"""))
    ctx3.close()
    b.close()
