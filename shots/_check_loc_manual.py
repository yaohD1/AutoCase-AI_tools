"""验证：停止后点「逐条看」仍然能打开弹窗，并且不会被轮询自动收掉。"""
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
data = copy.deepcopy(real)
data['steps'] = steps[:2] + [step]
data['status'] = 'stopped'
data['browser_open'] = True
data['paused'] = False


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 900})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.route('**/api/automation/recording/current*',
                   lambda r: r.fulfill(status=200, content_type='application/json',
                                       body=json.dumps(data, ensure_ascii=False)))
        page.route('**/api/automation/recording/events*',
                   lambda r: r.fulfill(status=200, content_type='application/json',
                                       body=json.dumps({'cursor': 0, 'pick': False, 'paused': False})))
        page.goto(UI, wait_until='domcontentloaded', timeout=60000)
        page.wait_for_selector('.side-rail', timeout=20000)
        page.wait_for_timeout(1200)

        review = page.locator('.check-action')
        print('逐条看按钮：', review.count(), review.first.inner_text() if review.count() else '-')
        if review.count():
            review.first.click()
            page.wait_for_timeout(600)
            print('点开后弹窗：', page.locator('.loc-dialog').is_visible())
            # 轮询是 2 秒一次：跨过一次完整轮询还在，说明没被自动收掉
            page.wait_for_timeout(2600)
            print('2.6 秒后仍在：', page.locator('.loc-dialog').is_visible())
            page.screenshot(path='shots/_loc_3_manual.png')
        print('页面错误：%s' % (errors or '无'))
        browser.close()


if __name__ == '__main__':
    main()
