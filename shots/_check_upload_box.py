# -*- coding: utf-8 -*-
import time
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    page.goto("http://localhost:5173/", wait_until="networkidle")
    time.sleep(1.5)
    info = page.evaluate("""() => {
      const pick = sel => {
        const el = document.querySelector(sel);
        if (!el) return null;
        const s = getComputedStyle(el), r = el.getBoundingClientRect();
        return {h: Math.round(r.height), flex: s.flex, display: s.display, minH: s.minHeight, pad: s.padding, just: s.justifyContent};
      };
      return {
        body: pick('.card-body-layout'),
        panel: pick('.upload-panel'),
        upload: pick('.upload-dragger'),
        dragger: pick('.el-upload-dragger'),
        tip: pick('.el-upload__tip')
      };
    }""")
    for k, v in info.items(): print(k, v)
    b.close()
