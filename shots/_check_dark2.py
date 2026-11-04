# -*- coding: utf-8 -*-
import time
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
    page = ctx.new_page()
    page.goto("http://localhost:5173/", wait_until="commit")
    page.evaluate("() => localStorage.setItem('ac-theme','dark')")
    page.goto("http://localhost:5173/", wait_until="commit")
    page.wait_for_selector(".upload-page", timeout=8000)
    page.evaluate("() => document.getAnimations().forEach(a => { a.pause(); a.currentTime = 380 })")
    page.screenshot(path="shots/_anim3_dark_380.png")
    b.close()
