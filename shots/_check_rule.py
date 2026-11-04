# -*- coding: utf-8 -*-
import time
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    page.goto("http://localhost:5173/", wait_until="commit")
    page.wait_for_selector(".upload-page", timeout=8000)
    print(page.evaluate("""() => {
      const t = document.querySelector('.mode-tabs')
      const cs = getComputedStyle(t, '::after')
      const r = t.getBoundingClientRect()
      const anims = t.getAnimations()
      return {
        row: [r.left, r.width, r.bottom],
        origin: cs.transformOrigin,
        tf: cs.transform,
        animsOnPseudo: anims.map(a => ({n: a.animationName, t: a.effect.target && a.effect.target.tagName, pe: a.effect.pseudoElement})),
        docAnims: document.getAnimations().map(a => a.animationName)
      }
    }"""))
    page.evaluate("() => document.getAnimations().forEach(a => { a.pause(); a.currentTime = 200 })")
    print("200ms：", page.evaluate("""() => {
      const t = document.querySelector('.mode-tabs')
      const cs = getComputedStyle(t, '::after')
      return {tf: cs.transform, origin: cs.transformOrigin}
    }"""))
    b.close()
