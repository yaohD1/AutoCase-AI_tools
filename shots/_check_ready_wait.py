# -*- coding: utf-8 -*-
"""「等待加载」在前端走一遍：按钮 → 草稿卡 → 插入时提交的 intent。

接口全部拦掉，插入那一步只把请求体截下来看，不碰真实录制会话。
"""
import json
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright


def step(no, text):
    return {'no': no, 'type': 'click', 'text': text,
            'nav_to': 'http://example.com/home?tab=1',
            'target': {'primary': {'kind': 'role', 'desc': 'b', 'count': 1, 'index': None},
                       'candidates': [{'kind': 'role', 'desc': 'b', 'count': 1, 'index': None,
                                       'relation': 'self'}],
                       'matches': [text]}}


SNAPSHOT = {
    'id': 'fake', 'status': 'stopped', 'base_url': 'http://example.com', 'paused': False,
    'steps': [step(1, '登录')], 'picks': [], 'aliases': {}, 'alias_items': {},
    'panel': True, 'browser_open': False, 'auth_saved': False,
    'ask': {'status': 'ready', 'detail': ''}, 'emit': {'state': 'idle'},
}

captured = []


def handle(route):
    request = route.request
    path = urlparse(request.url).path
    if request.method == 'POST' and path.endswith('/recording/steps'):
        captured.append(json.loads(request.post_data or '{}'))
        body = dict(SNAPSHOT)
        body['steps'] = SNAPSHOT['steps'] + [{
            'no': 2, 'type': 'wait', 'text': None, 'url': None,
            'intent': captured[-1].get('intent'),
            'target': None,
        }]
        route.fulfill(status=200, content_type='application/json', body=json.dumps(body))
        return
    if path.endswith('/recording/current'):
        route.fulfill(status=200, content_type='application/json', body=json.dumps(SNAPSHOT))
        return
    route.fulfill(status=200, content_type='application/json', body='{}')


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={'width': 600, 'height': 900})
    page.on('pageerror', lambda e: print('PAGEERROR', str(e)[:200]))
    page.route(lambda url: urlparse(url).path.startswith('/api/'), handle)
    page.goto('http://localhost:5173/recording?panel=1&base_url=http://example.com',
              wait_until='domcontentloaded')
    page.wait_for_selector('.steps-card .el-card__body')
    page.evaluate("() => document.getElementById('ac-boot')?.remove()")
    page.wait_for_timeout(300)

    page.get_by_role('button', name='等待加载', exact=True).click()
    page.wait_for_selector('.draft-card')
    print('草稿标题:', page.locator('.draft-title').inner_text())
    print('分段:', page.locator('.ready-seg .cfg-seg-item').all_inner_texts())
    print('选中:', page.locator('.ready-seg .cfg-seg-item.is-on').inner_text())
    print('说明:', page.locator('.ready-note').inner_text())
    print('超时:', page.locator('.ready-body .el-input-number input').input_value())
    page.screenshot(path='shots/_ready_draft.png')

    page.locator('.ready-seg .cfg-seg-item', has_text='DOM').click()
    page.wait_for_timeout(150)
    page.locator('.ready-body .el-input-number input').fill('30')
    page.keyboard.press('Enter')
    page.get_by_role('button', name='插入等待', exact=True).click()
    page.wait_for_timeout(600)

    print('提交体:', json.dumps(captured[-1] if captured else None, ensure_ascii=False))
    print('跳转标记:', page.locator('.step-nav').first.inner_text().replace('\n', ' '))
    print('列表末行:', page.locator('.step').last.inner_text().replace('\n', ' | '))
    page.screenshot(path='shots/_ready_inserted.png')
    browser.close()
