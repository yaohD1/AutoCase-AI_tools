"""验证：弹窗开着时点候选，后端已「录制已结束」，弹窗要自己收掉而不是卡在那儿。"""
import copy
import json

import requests
from playwright.sync_api import sync_playwright

BASE = 'http://127.0.0.1:5000'
UI = 'http://localhost:5173/recording'

real = requests.get(BASE + '/api/automation/recording/current', timeout=15).json()
steps = real.get('steps') or []
step = copy.deepcopy(steps[1])
step['no'] = 3
step['target']['chosen'] = None
step['target']['degraded'] = None
step['target']['page_gone'] = False
step['target']['unverified'] = False
step['target']['candidates'] = []

two = copy.deepcopy(real)
two['steps'] = steps[:2]
two['status'] = 'recording'
two['browser_open'] = True
two['paused'] = False

three = copy.deepcopy(two)
three['steps'] = steps[:2] + [step]

phase = {'n': 1}


def on_current(route):
    route.fulfill(status=200, content_type='application/json',
                  body=json.dumps(two if phase['n'] == 1 else three, ensure_ascii=False))


def on_events(route):
    route.fulfill(status=200, content_type='application/json',
                  body=json.dumps({'cursor': phase['n'], 'pick': False, 'paused': False}))


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 900})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.route('**/api/automation/recording/current*', on_current)
        page.route('**/api/automation/recording/events*', on_events)
        page.goto(UI, wait_until='domcontentloaded', timeout=60000)
        page.wait_for_selector('.recording-page', timeout=20000)
        page.wait_for_timeout(1200)
        phase['n'] = 2
        page.wait_for_timeout(1500)
        print('弹窗已开：', page.locator('.loc-dialog').is_visible())

        # 真实后端这一刻是 stopped + 线程已结束：点候选会拿到 409「录制已结束」
        page.locator('.loc-item').first.click()
        page.wait_for_timeout(1200)
        print('点候选后弹窗：', page.locator('.loc-dialog').is_visible())
        page.screenshot(path='shots/_loc_4_ended.png')
        print('页面错误：%s' % (errors or '无'))
        browser.close()


if __name__ == '__main__':
    main()
