"""验证：定位确认弹窗在录制结束后必须消失；关闭浏览器一次点击就消失。

不碰真实录制会话：/current 与 /events 全部用假快照喂给前端。
"""
import copy
import json
import sys
import time

import requests
from playwright.sync_api import sync_playwright

BASE = 'http://127.0.0.1:5000'
UI = 'http://localhost:5173/recording'

real = requests.get(BASE + '/api/automation/recording/current', timeout=15).json()
steps = real.get('steps') or []


def make_session(step_list, status='recording', browser_open=True):
    data = copy.deepcopy(real)
    data['steps'] = copy.deepcopy(step_list)
    data['status'] = status
    data['browser_open'] = browser_open
    data['paused'] = False
    return data


def ambiguous_step():
    step = copy.deepcopy(steps[1])
    step['no'] = 3
    step['target']['chosen'] = None
    step['target']['degraded'] = None
    step['target']['status'] = 'ambiguous'
    # effectiveStatus 不看 status，而是按 page_gone / unverified / candidates 推：
    # 页面已翻走的会被判成 page_gone（不弹），这里改成「当场不唯一」。
    step['target']['page_gone'] = False
    step['target']['unverified'] = False
    step['target']['candidates'] = []
    return step


two = steps[:2]
three = steps[:2] + [ambiguous_step()]
phase = {'n': 1}
payloads = {
    1: make_session(two, 'recording', True),
    2: make_session(three, 'recording', True),
    3: make_session(three, 'stopped', True),
    4: make_session(three, 'stopped', True),
    5: make_session(three, 'stopped', False),
}


def on_current(route):
    route.fulfill(status=200, content_type='application/json',
                  body=json.dumps(payloads[phase['n']], ensure_ascii=False))


def on_events(route):
    route.fulfill(status=200, content_type='application/json',
                  body=json.dumps({'cursor': phase['n'], 'pick': False, 'paused': False}))


def on_close(route):
    phase['n'] = 5
    route.fulfill(status=200, content_type='application/json',
                  body=json.dumps(payloads[5], ensure_ascii=False))


def main():
    log = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 900})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.route('**/api/automation/recording/current*', on_current)
        page.route('**/api/automation/recording/events*', on_events)
        page.route('**/api/automation/recording/close', on_close)
        page.goto(UI, wait_until='domcontentloaded', timeout=60000)
        page.wait_for_selector('.recording-page', timeout=20000)
        page.wait_for_timeout(1500)
        log.append('首帧：弹窗 %s' % page.locator('.loc-dialog').is_visible())

        phase['n'] = 2
        page.wait_for_timeout(1500)
        opened = page.locator('.loc-dialog').is_visible()
        log.append('新步骤落地：弹窗 %s' % opened)
        page.screenshot(path='shots/_loc_1_open.png')

        if opened:
            phase['n'] = 3
            started = time.time()
            gone_at = None
            for _ in range(60):
                page.wait_for_timeout(100)
                if not page.locator('.loc-dialog').is_visible():
                    gone_at = time.time() - started
                    break
            log.append('切到 stopped（浏览器还开着）：弹窗消失 %s（%.1fs）'
                       % (bool(gone_at), gone_at or -1))
            page.screenshot(path='shots/_loc_2_after_close.png')
            # 页面必须能操作：点页头「配置」应能打开抽屉
            page.locator('.page-actions button:has-text("配置")').click()
            page.wait_for_timeout(500)
            log.append('页面可操作（抽屉打开）：%s'
                       % page.evaluate("document.querySelector('.cfg-shell').classList.contains('is-open')"))
            page.keyboard.press('Escape')
            page.wait_for_timeout(300)

        # 场景 B：stopped 但浏览器还开着，点一次关闭应立刻让按钮消失
        phase['n'] = 4
        page.wait_for_timeout(1600)
        btn = page.locator('button:has-text("关闭浏览器")')
        log.append('关闭按钮可见：%s' % btn.is_visible())
        if btn.is_visible():
            btn.click()
            page.wait_for_timeout(400)
            page.locator('.el-message-box__btns button:has-text("关闭")').click()
            started = time.time()
            hid_at = None
            for _ in range(25):
                page.wait_for_timeout(100)
                if not btn.is_visible():
                    hid_at = time.time() - started
                    break
            log.append('确认关闭后按钮消失 %s（%.1fs）' % (bool(hid_at), hid_at or -1))

        print('\n'.join(log))
        print('页面错误：%s' % (errors or '无'))
        browser.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
