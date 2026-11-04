# -*- coding: utf-8 -*-
"""「定位详情」弹窗的三档结论 + 窄面板。

后端返回什么形状、界面怎么翻译成人话，两边必须对得上：
  1) 唯一  → 绿：存在 · 唯一
  2) 多个  → 黄：存在，但匹配到 N 个，生成时取第几个
  3) 找不到 → 红：当前页面上找不到它
外加：460px 窄面板（真实面板窗口的常见宽度）下弹窗不能横向溢出。
"""
import json
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

CODE = "getByRole('button', { name: '登录' })"


def step(no, alias, desc, status='ok'):
    cand = [{'kind': 'role', 'desc': desc, 'count': 1, 'index': 0, 'relation': 'self'}]
    return {
        'no': no, 'type': 'click', 'text': '点击【%s】' % alias, 'url': None,
        'target': {'alias': alias, 'candidates': cand, 'primary': cand[0],
                   'status': status, 'matches': {'desc': desc, 'count': 1, 'items': []}},
    }


def payload(no, kind):
    base = {
        'ok': True, 'no': no, 'alias': '登录按钮', 'framework': 'playwright-ts',
        'checked_at': time.time(), 'page_url': 'http://192.168.1.10:8080/#/login',
        'page_gone': False, 'mode': 'unique', 'code': CODE, 'kind': 'role',
        'desc': CODE, 'recorded_count': 1, 'items': [],
        'candidates': [
            {'kind': 'role', 'desc': CODE, 'count': 1, 'relation': 'self', 'fragile': False, 'adopted': True},
            {'kind': 'text', 'desc': "getByText('登录')", 'count': 2, 'relation': 'self', 'fragile': False, 'adopted': False},
            {'kind': 'label', 'desc': "getByLabel('密码')", 'count': 1, 'relation': 'self', 'fragile': False, 'adopted': False},
            {'kind': 'css', 'desc': '#app > div > form > button', 'count': 1, 'relation': 'self', 'fragile': True, 'adopted': False},
        ],
    }
    if kind == 'multi':
        base.update({'mode': 'ambiguous', 'count': 3, 'exists': True, 'unique': False,
                     'visible': True, 'index': 1})
    elif kind == 'missing':
        base.update({'count': 0, 'exists': False, 'unique': False, 'visible': False, 'index': 0})
    else:
        base.update({'count': 1, 'exists': True, 'unique': True, 'visible': True, 'index': 0})
    return base


SNAP = {
    'id': 'fake', 'status': 'stopped', 'base_url': 'http://192.168.1.10:8080',
    'paused': False, 'panel': True, 'browser_open': True, 'auth_saved': False,
    'steps': [step(7, '登录按钮', CODE), step(8, '提交', CODE), step(9, '取消', CODE)],
    'pick': None, 'picks': [], 'aliases': {}, 'alias_items': {},
    'ask': {'status': 'ready', 'detail': ''}, 'emit': {'state': 'idle'},
}

KINDS = {7: 'unique', 8: 'multi', 9: 'missing'}

MEASURE = """() => {
  const dlg = document.querySelector('.loc-detail-dialog')
  const box = dlg ? dlg.getBoundingClientRect() : null
  const verdict = document.querySelector('.ld-verdict')
  const code = document.querySelector('.ld-code')
  return {
    dialogW: box ? Math.round(box.width) : 0,
    dialogH: box ? Math.round(box.height) : 0,
    overflowX: dlg ? dlg.scrollWidth > dlg.clientWidth + 1 : null,
    verdict: verdict ? verdict.textContent.replace(/\\s+/g, ' ').trim() : '',
    tone: verdict ? verdict.className : '',
    codeLines: code ? Math.round(code.getBoundingClientRect().height / 18) : 0,
  }
}"""


def handle(route):
    path = urlparse(route.request.url).path
    if '/steps/' in path and path.endswith('/locate'):
        no = int(path.split('/steps/')[1].split('/')[0])
        route.fulfill(status=200, content_type='application/json',
                      body=json.dumps(payload(no, KINDS[no])))
        return
    route.fulfill(status=200, content_type='application/json', body=json.dumps(SNAP))


def open_and_shot(page, no, path, theme='light'):
    page.evaluate("t => { localStorage.setItem('ac-theme', t) }", theme)
    page.reload(wait_until='domcontentloaded')
    page.wait_for_selector('.steps-card')
    page.evaluate("() => document.getElementById('ac-boot')?.remove()")
    page.wait_for_timeout(350)
    page.locator('li.step[data-step-no="%d"] .loc-detail-btn' % no).click()
    page.wait_for_selector('.ld-verdict', timeout=8000)
    page.wait_for_timeout(250)
    print(no, theme, json.dumps(page.evaluate(MEASURE), ensure_ascii=False))
    page.screenshot(path=path)


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={'width': 1280, 'height': 860})
    page.on('pageerror', lambda e: print('PAGEERROR', str(e)[:300]))
    page.route(lambda url: urlparse(url).path.startswith('/api/'), handle)
    page.goto('http://localhost:5173/recording', wait_until='domcontentloaded')

    open_and_shot(page, 7, 'shots/_ld_unique.png')
    open_and_shot(page, 8, 'shots/_ld_multi.png')
    page.locator('.ld-toggle').click()
    page.wait_for_timeout(250)
    page.screenshot(path='shots/_ld_multi_cands.png')
    open_and_shot(page, 9, 'shots/_ld_missing.png')
    open_and_shot(page, 7, 'shots/_ld_dark.png', theme='dark')

    narrow = browser.new_page(viewport={'width': 460, 'height': 860})
    narrow.route(lambda url: urlparse(url).path.startswith('/api/'), handle)
    narrow.goto('http://localhost:5173/recording', wait_until='domcontentloaded')
    try:
        open_and_shot(narrow, 7, 'shots/_ld_narrow.png')
    except Exception as exc:  # noqa: BLE001
        print('窄面板失败：', str(exc)[:200])
    browser.close()
