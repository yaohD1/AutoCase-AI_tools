# -*- coding: utf-8 -*-
"""面板窗口（split 布局，被测系统禁止 iframe 时）滚不滚得动。

用 600×900 打开 /recording?panel=1，接口全部拦掉喂一份 16 步的假会话。
先量修复后的样式并真滚一次，再把 page-col 还原成修复前的规则复现「滚不动」。
注意：冷启动幕布 #ac-boot 会盖住整页（z-index 4000），不等它揭完，滚轮和
elementFromPoint 全都打在幕布上，量出来的结论是假的。
"""
import json
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright


def step(no, text):
    return {
        'no': no,
        'type': 'click',
        'text': text,
        'target': {
            'primary': {'kind': 'role', 'desc': 'button "%s"' % text, 'count': 1, 'index': None},
            'candidates': [{'kind': 'role', 'desc': 'button "%s"' % text, 'count': 1,
                            'index': None, 'relation': 'self'}],
            'matches': [text],
        },
    }


SNAPSHOT = {
    'id': 'fake-session',
    'status': 'recording',
    'base_url': 'http://example.com/login',
    'pick': False,
    'paused': False,
    'error': None,
    'steps': [step(i + 1, '操作 %d' % (i + 1)) for i in range(16)],
    'picks': [],
    'aliases': {},
    'alias_items': {},
    'panel': True,
    'browser_open': True,
    'auth_saved': False,
    'ask': {'status': 'ready', 'detail': ''},
    'emit': {'state': 'idle'},
}

BODY = "document.querySelector('.steps-card .el-card__body')"

MEASURE = """() => {
  const body = %s
  const main = document.querySelector('#app .el-main')
  const page = document.querySelector('.recording-page')
  const col = document.querySelector('.page-col')
  const r = (el) => el ? Math.round(el.getBoundingClientRect().height) : null
  return {
    viewport: innerWidth + 'x' + innerHeight,
    mainH: main ? Math.round(main.clientHeight) : null,
    mainOverflow: main ? getComputedStyle(main).overflow : '',
    pageH: r(page), colH: r(col),
    colDisplay: col ? getComputedStyle(col).display : '',
    bodyClient: body ? body.clientHeight : null,
    bodyScroll: body ? body.scrollHeight : null,
    canScroll: !!body && body.scrollHeight > body.clientHeight + 1,
    docScroll: document.documentElement.scrollHeight,
  }
}""" % BODY


def wheel(page, x, y, dy=500):
    page.mouse.move(x, y)
    page.mouse.wheel(0, dy)
    page.wait_for_timeout(400)
    return page.evaluate("() => %s.scrollTop" % BODY)


with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={'width': 600, 'height': 900})
    page = ctx.new_page()

    def handle(route):
        path = urlparse(route.request.url).path
        if path.endswith('/automation/recording/current'):
            route.fulfill(status=200, content_type='application/json', body=json.dumps(SNAPSHOT))
        elif 'events' in path:
            route.fulfill(status=200, content_type='application/json',
                          body=json.dumps({'events': [], 'offset': 0, 'reset': False}))
        elif 'memory' in path:
            route.fulfill(status=200, content_type='application/json',
                          body=json.dumps({'status': 'idle'}))
        else:
            route.fulfill(status=200, content_type='application/json', body=json.dumps({}))

    # 只拦真接口。用 '**/api/**' 会把 /src/api/index.js 这类开发期模块也拦下来，
    # Vite 的模块请求拿到 JSON 就直接白屏。
    page.route(lambda url: urlparse(url).path.startswith('/api/'), handle)
    # 页面每 2 秒轮一次接口，networkidle 永远等不到。
    page.goto('http://localhost:5173/recording?panel=1&base_url=http://example.com/login',
              wait_until='domcontentloaded')
    page.wait_for_selector('.steps-card .el-card__body', timeout=15000)
    page.evaluate("() => document.getElementById('ac-boot')?.remove()")
    page.wait_for_timeout(400)

    print('修复后：', json.dumps(page.evaluate(MEASURE), ensure_ascii=False))
    page.screenshot(path='shots/_panel_scroll_fixed.png')

    # 真滚一下：先程序化设一次（容器能不能滚），再用滚轮（用户实际怎么滚）
    print('设 scrollTop=400 后读回：', page.evaluate(
        "() => { const b = %s; b.scrollTop = 400; return b.scrollTop }" % BODY))
    page.evaluate("() => { %s.scrollTop = 0 }" % BODY)
    box = page.locator('.steps-card .el-card__body').bounding_box()
    x = box['x'] + box['width'] / 2
    for label, dy in (('工具栏上', 60), ('步骤行上', 300), ('时间线空白处', 600)):
        page.evaluate("() => { %s.scrollTop = 0 }" % BODY)
        print('滚轮（%s）→ scrollTop %s' % (label, wheel(page, x, box['y'] + dy)))
    page.evaluate("() => { %s.scrollTop = 400 }" % BODY)
    page.screenshot(path='shots/_panel_scroll_after_wheel.png')

    # 还原成修复前的规则（page-col 保持块级、不吃剩余高度），应当复现「滚不动」。
    # 组件样式带 scoped 的 [data-v-x]，所以要 !important 才压得住。
    page.add_style_tag(content='.recording-page.is-panel .page-col'
                               '{flex:none !important;min-height:auto !important;display:block !important}')
    page.wait_for_timeout(300)
    print('修复前：', json.dumps(page.evaluate(MEASURE), ensure_ascii=False))
    page.evaluate("() => { %s.scrollTop = 0 }" % BODY)
    print('修复前滚轮（时间线空白处）→ scrollTop %s' % wheel(page, x, 700))
    page.screenshot(path='shots/_panel_scroll_before.png')

    ctx.close()
    browser.close()
