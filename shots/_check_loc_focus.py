# -*- coding: utf-8 -*-
"""聚焦逻辑的边界形状：挨在一起的图标、隔着整页的两个、上下两行、滚动出视口的一条。

四种形状各截一张，顺便量一下缩放倍数和框的位置，确认：
1) 小元素能放大到看得见；2) 散在整页四角时退回整页；3) 竖着的两行不会被压成细缝；
4) 屏幕外的元素老实标「屏外」，不硬画框。
"""
import json
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

SHOT = Path('shots/_live_target.png').read_bytes()
DESC = "getByRole('button', { name: '智能摘要', exact: true })"

SCENARIOS = {
    # 两个 18×18 的小图标挨在一起：最需要放大的情形
    'icons': [
        {'tag': 'button', 'text': '', 'visible': True, 'x': 700, 'y': 300, 'w': 18, 'h': 18},
        {'tag': 'button', 'text': '', 'visible': True, 'x': 724, 'y': 300, 'w': 18, 'h': 18},
    ],
    # 一个在左上、一个在右下：应该退回整页，不放大
    'far': [
        {'tag': 'button', 'text': '顶部', 'visible': True, 'x': 60, 'y': 60, 'w': 88, 'h': 32},
        {'tag': 'button', 'text': '底部', 'visible': True, 'x': 1000, 'y': 690, 'w': 88, 'h': 32},
    ],
    # 表格里上下两行同名按钮：竖着的一组
    'rows': [
        {'tag': 'button', 'text': '删除', 'row': '张三 2026-01-01 已启用', 'visible': True,
         'x': 320, 'y': 200, 'w': 60, 'h': 30},
        {'tag': 'button', 'text': '删除', 'row': '李四 2026-01-02 已停用', 'visible': True,
         'x': 320, 'y': 330, 'w': 60, 'h': 30},
    ],
    # 一条滚出视口：y 是负数，截图里根本没有它
    'offscreen': [
        {'tag': 'button', 'text': '可见的', 'visible': True, 'x': 500, 'y': 300, 'w': 88, 'h': 32},
        {'tag': 'button', 'text': '滚上去的', 'visible': False, 'x': 500, 'y': -260, 'w': 88, 'h': 32},
    ],
}


def snapshot(items):
    cand = [{'kind': 'role', 'desc': DESC, 'count': len(items), 'index': 0, 'relation': 'self'}]
    return {
        'id': 'fake', 'status': 'stopped', 'base_url': 'http://example.com', 'paused': False,
        'steps': [{
            'no': 7, 'type': 'click', 'text': '点击【智能摘要】', 'url': None,
            'target': {
                'alias': '智能摘要', 'candidates': cand, 'primary': cand[0], 'status': 'ambiguous',
                'shot': {'id': 'shot-1', 'w': 1139, 'h': 738},
                'matches': {'desc': DESC, 'count': len(items), 'items': items},
            },
        }],
        'picks': [], 'aliases': {}, 'alias_items': {}, 'panel': True, 'browser_open': False,
        'auth_saved': False, 'ask': {'status': 'ready', 'detail': ''}, 'emit': {'state': 'idle'},
    }


MEASURE = """() => {
  const q = (s) => document.querySelector(s)
  const stage = q('.shot-stage'), inner = q('.shot-inner')
  const sr = stage.getBoundingClientRect(), nr = inner.getBoundingClientRect()
  const boxes = [...document.querySelectorAll('.shot-box')].map(b => {
    const r = b.getBoundingClientRect()
    return {n: b.textContent.trim(), w: Math.round(r.width), h: Math.round(r.height),
            inside: r.left >= sr.left - 1 && r.right <= sr.right + 1}
  })
  return {
    stage: {w: Math.round(sr.width), h: Math.round(sr.height)},
    zoom: Math.round(nr.width / sr.width * 100) / 100,
    boxes,
    thumbs: [...document.querySelectorAll('.loc-thumb')].map(t => ({
      off: t.classList.contains('is-off'),
      ring: (t.querySelector('.loc-thumb-ring') || {getBoundingClientRect: () => ({width: 0, height: 0})})
        .getBoundingClientRect().width,
    })),
  }
}"""


def handle_factory(snap):
    def handle(route):
        request = route.request
        path = urlparse(request.url).path
        if path.endswith('/shot'):
            route.fulfill(status=200, headers={'Content-Type': 'image/png'}, body=SHOT)
            return
        if path.endswith('/recording/current'):
            route.fulfill(status=200, content_type='application/json', body=json.dumps(snap))
            return
        route.fulfill(status=200, content_type='application/json', body=json.dumps(snap))
    return handle


with sync_playwright() as p:
    browser = p.chromium.launch()
    for name, items in SCENARIOS.items():
        page = browser.new_page(viewport={'width': 1280, 'height': 860})
        page.on('pageerror', lambda e: print('PAGEERROR', str(e)[:200]))
        page.route(lambda url: urlparse(url).path.startswith('/api/'), handle_factory(snapshot(items)))
        page.goto('http://localhost:5173/recording', wait_until='domcontentloaded')
        page.wait_for_selector('.steps-card')
        page.evaluate("() => document.getElementById('ac-boot')?.remove()")
        page.wait_for_timeout(400)
        page.get_by_role('button', name='逐条看').click()
        page.wait_for_selector('.shot-stage img', timeout=10000)
        page.wait_for_timeout(400)
        print(name, json.dumps(page.evaluate(MEASURE), ensure_ascii=False))
        page.screenshot(path='shots/_focus_%s.png' % name)
        if name == 'rows':
            page.locator('.shot-box').nth(1).hover()
            page.wait_for_timeout(300)
            page.screenshot(path='shots/_focus_hover.png')
        page.close()
    browser.close()
