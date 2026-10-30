# -*- coding: utf-8 -*-
"""把各页面截一遍，用于评估视觉风格是否统一。"""
import sys, time
from playwright.sync_api import sync_playwright

PAGES = [
    ("upload", "/"),
    ("cases", "/cases"),
    ("modules", "/modules"),
    ("scripts", "/scripts"),
    ("records", "/recording/records"),
    ("settings", "/settings"),
]

BASE = "http://localhost:5173"

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
    page = ctx.new_page()
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))
    for name, path in PAGES:
        page.goto(BASE + path, wait_until="networkidle")
        time.sleep(1.6)
        page.screenshot(path=f"shots/audit-{name}.png")
        print("done", name, path)
    b.close()
    if errs:
        print("PAGE ERRORS:", errs[:5], file=sys.stderr)
