# -*- coding: utf-8 -*-
import time
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
    page = ctx.new_page()
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.goto("http://localhost:5173/recording", wait_until="networkidle")
    time.sleep(2.5)
    page.screenshot(path="shots/audit-recording.png")
    # 量一下页面根和配置卡的位置
    box = page.evaluate("""() => {
      const el = document.querySelector('.recording-page');
      const card = document.querySelector('.config-card');
      const r = el.getBoundingClientRect(), c = card.getBoundingClientRect();
      return {page: [Math.round(r.top), Math.round(r.height)], card: [Math.round(c.top), Math.round(c.height)]};
    }""")
    print("recording-page:", box["page"], "config-card:", box["card"])
    print("errors:", errs[:3])
    b.close()
