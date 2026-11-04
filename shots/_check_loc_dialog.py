# -*- coding: utf-8 -*-
"""「定位待确认」弹窗：左右分栏 + 图 + 框 + 两个出口。接口全部拦掉，只看渲染与交互。"""
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

SHOT = Path('shots/_live_target.png').read_bytes()
ERROR_MODE = '--error' in sys.argv

# 真实录制里第 41 步的四个 tab（坐标是 iframe 内的 CSS 像素，截图裁到 iframe 后原点一致）
ITEMS = [
    {'tag': 'button', 'text': '智能问答', 'row': None, 'visible': True, 'x': 720, 'y': 62, 'w': 88, 'h': 32},
    {'tag': 'button', 'text': '智能摘要', 'row': None, 'visible': True, 'x': 818, 'y': 62, 'w': 88, 'h': 32},
    {'tag': 'button', 'text': '文档解析', 'row': None, 'visible': True, 'x': 916, 'y': 62, 'w': 88, 'h': 32},
    {'tag': 'button', 'text': '分段规则', 'row': None, 'visible': True, 'x': 1014, 'y': 62, 'w': 88, 'h': 32},
]

CAND = [{'kind': 'role', 'desc': "getByRole('button', { name: '智能摘要', exact: true })",
         'count': 4, 'index': 1, 'relation': 'self'}]

SNAPSHOT = {
    'id': 'fake', 'status': 'stopped', 'base_url': 'http://example.com', 'paused': False,
    'steps': [{
        'no': 41, 'type': 'click', 'text': '点击【智能摘要】', 'url': None,
        'target': {
            'alias': '智能摘要',
            'candidates': CAND,
            'primary': CAND[0],
            'status': 'ambiguous',
            'shot': ({'error': '按下之后页面立刻跳转了，没来得及截'} if ERROR_MODE
                     else {'id': 'shot-1', 'w': 1139, 'h': 738}),
            'matches': {'desc': CAND[0]['desc'], 'count': 4, 'items': ITEMS},
        },
    }],
    'picks': [], 'aliases': {}, 'alias_items': {}, 'panel': True, 'browser_open': False,
    'auth_saved': False, 'ask': {'status': 'ready', 'detail': ''}, 'emit': {'state': 'idle'},
}

STATE = {'picked': []}


def handle(route):
    request = route.request
    path = urlparse(request.url).path
    if path.endswith('/shot'):
        route.fulfill(status=200, headers={'Content-Type': 'image/png'}, body=SHOT)
        return
    if request.method == 'POST' and path.endswith('/chosen'):
        STATE['picked'].append(json.loads(request.post_data or '{}'))
        route.fulfill(status=200, content_type='application/json', body=json.dumps(SNAPSHOT))
        return
    if path.endswith('/recording/current'):
        route.fulfill(status=200, content_type='application/json', body=json.dumps(SNAPSHOT))
        return
    route.fulfill(status=200, content_type='application/json', body='{}')


MEASURE = """() => {
  const dlg = document.querySelector('.el-dialog.loc-dialog')
  const img = document.querySelector('.shot-stage img')
  const stage = document.querySelector('.shot-stage')
  const inner = document.querySelector('.shot-inner')
  const boxes = [...document.querySelectorAll('.shot-box')].map(b => {
    const r = b.getBoundingClientRect()
    return {n: b.textContent.trim(), x: Math.round(r.x), y: Math.round(r.y),
            w: Math.round(r.width), h: Math.round(r.height)}
  })
  const d = dlg.getBoundingClientRect()
  const ir = img ? img.getBoundingClientRect() : null
  const sr = stage ? stage.getBoundingClientRect() : null
  const nr = inner ? inner.getBoundingClientRect() : null
  const thumbs = [...document.querySelectorAll('.loc-thumb')].map(t => {
    const r = t.getBoundingClientRect()
    const ring = t.querySelector('.loc-thumb-ring')
    const rr = ring ? ring.getBoundingClientRect() : null
    return {w: Math.round(r.width), h: Math.round(r.height),
            ring: rr ? {w: Math.round(rr.width), h: Math.round(rr.height)} : null,
            off: t.classList.contains('is-off')}
  })
  return {
    vw: innerWidth, vh: innerHeight,
    dialog: {x: Math.round(d.x), y: Math.round(d.y), w: Math.round(d.width), h: Math.round(d.height)},
    centered: Math.abs((d.x + d.width / 2) - innerWidth / 2) < 2,
    stage: sr ? {w: Math.round(sr.width), h: Math.round(sr.height)} : null,
    inner: nr ? {x: Math.round(nr.x), y: Math.round(nr.y), w: Math.round(nr.width), h: Math.round(nr.height)} : null,
    zoom: (nr && sr) ? Math.round(nr.width / sr.width * 100) / 100 : null,
    img: ir ? {w: Math.round(ir.width), h: Math.round(ir.height)} : null,
    boxes,
    thumbs,
    rows: document.querySelectorAll('.loc-item').length,
    scrollable: (() => { const s = document.querySelector('.dlg-scroll'); return s ? s.scrollHeight > s.clientHeight + 1 : null })(),
    footer: (document.querySelector('.dlg-foot') || {}).innerText,
  }
}"""


with sync_playwright() as p:
    browser = p.chromium.launch()
    for name, w, h in (('wide', 1280, 860), ('panel', 600, 900)):
        page = browser.new_page(viewport={'width': w, 'height': h})
        page.on('pageerror', lambda e: print('PAGEERROR', str(e)[:200]))
        page.route(lambda url: urlparse(url).path.startswith('/api/'), handle)
        page.goto('http://localhost:5173/recording', wait_until='domcontentloaded')
        page.wait_for_selector('.steps-card')
        page.evaluate("() => document.getElementById('ac-boot')?.remove()")
        page.wait_for_timeout(500)
        page.get_by_role('button', name='逐条看').click()
        page.wait_for_selector('.shot-stage img' if not ERROR_MODE else '.dlg-stage-empty', timeout=10000)
        page.wait_for_timeout(500)
        print(name, json.dumps(page.evaluate(MEASURE), ensure_ascii=False))
        page.screenshot(path='shots/_loc_dialog_%s%s.png' % (name, '_error' if ERROR_MODE else ''))
        if name == 'wide' and not ERROR_MODE:
            page.locator('.shot-box').nth(1).click()
            page.wait_for_timeout(300)
            print('wide 点框后提交:', json.dumps(STATE['picked'][-1] if STATE['picked'] else None,
                                                ensure_ascii=False))
        page.close()
    browser.close()
