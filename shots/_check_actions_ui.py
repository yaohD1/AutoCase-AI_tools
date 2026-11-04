# -*- coding: utf-8 -*-
"""封装 / 动作库的界面验证：用假 session 直接渲染，不启动真实录制。

只验视觉与交互骨架：多选态、封装弹窗、调用卡、动作库。
真正的封装逻辑由 test_recording_actions.py 和后端真机验证负责。
"""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = 'http://localhost:5173'
OUT = Path(__file__).resolve().parent


def target(alias, desc, name=None):
    return {
        'alias': alias,
        'snap': {'tag': 'input', 'name': name or alias, 'text': alias},
        'candidates': [{'kind': 'role', 'desc': desc, 'count': 1, 'index': 0, 'relation': 'self'}],
        'primary': {'kind': 'role', 'desc': desc, 'count': 1, 'index': 0},
        'status': 'unique',
    }


def step(no, kind, alias=None, value=None, name=None, **extra):
    item = {
        'no': no, 'type': kind, 'source': 'record',
        'target': target(alias, 'getByRole(...)', name=name) if alias else None,
        'value': value, 'label': None, 'url': None, 'page_index': 0,
        'intent': None, 'text': None, 'dialog': None,
    }
    item.update(extra)
    return item


def base_session(steps, actions=None):
    return {
        'id': 'demo-session',
        'status': 'stopped',
        'base_url': 'http://demo.local',
        'pick': False,
        'paused': False,
        'error': None,
        'steps': steps,
        'picks': [],
        'actions': actions or [],
        'actions_saved': True,
        'actions_error': None,
        'aliases': {},
        'alias_items': [],
        'panel': False,
        'browser_open': False,
        'auth_saved': False,
        'ask': {'status': 'ready', 'label': '就绪', 'detail': '', 'model': None},
        'emit': {'status': 'idle', 'stages': [], 'result': None, 'error': None,
                 'plan': None, 'started_at': None, 'finished_at': None},
    }


FLAT = [
    step(1, 'goto', None, url='http://demo.local/login'),
    step(2, 'fill', '用户名', 'admin', name='username'),
    step(3, 'fill', '密码', '123456', name='password'),
    step(4, 'click', '登录按钮'),
    step(5, 'assert', '工作台', text='进入工作台'),
    step(6, 'click', '新建'),
]

ACTION = {
    'id': 'a1',
    'name': '登录',
    'desc': '登录并进入工作台',
    'params': [
        {'key': 'username', 'label': '用户名', 'step': 2, 'field': 'value'},
        {'key': 'password', 'label': '密码', 'step': 3, 'field': 'value'},
    ],
    'steps': 4,
    'calls': 2,
}

CALL = step(2, 'call', None, action='a1', args={'username': 'admin', 'password': '123456'}, label='登录', text='登录并进入工作台')
CALL2 = step(5, 'call', None, action='a1', args={'username': 'tester', 'password': 'x'}, label='登录')
PACKED = [
    step(1, 'goto', None, url='http://demo.local/login'),
    CALL,
    step(3, 'click', '新建'),
    step(4, 'click', '新建'),
    CALL2,
]


def run(page):
    report = []

    def shot(name):
        path = OUT / f'_act_{name}.png'
        page.screenshot(path=str(path), full_page=True)
        report.append(str(path))

    # 1. 平铺列表：工具栏出现「封装 / 动作库」
    page.wait_for_selector('.timeline .step')
    shot('1_list')
    assert page.locator('button:has-text("封装")').count() >= 1
    assert page.locator('button:has-text("动作库")').count() >= 1

    # 2. 进入多选态，勾选前三步
    page.locator('.toolbar button:has-text("封装")').first.click()
    page.wait_for_timeout(200)
    rows = page.locator('.timeline .step')
    rows.nth(1).click()
    rows.nth(2).click()
    page.wait_for_timeout(200)
    assert page.locator('.step.is-picked').count() == 2
    shot('2_selecting')

    # 3. 勾第 4 步会跨过第 3 步 → 提示「断开了」且封装按钮禁用
    rows.nth(4).click()
    page.wait_for_timeout(200)
    hint = page.locator('.select-hint').inner_text()
    report.append('hint=' + hint)
    assert '断开' in hint, hint
    assert page.locator('.select-actions button:has-text("封装为动作")').is_disabled()
    shot('3_gap')

    # 4. 取消第 5 步，补上「点击登录按钮」，让动作名能从句中的点击推导出来
    rows.nth(4).click()
    rows.nth(3).click()
    page.wait_for_timeout(150)
    assert page.locator('.step.is-picked').count() == 3
    page.locator('.select-actions button:has-text("封装为动作")').click()
    page.wait_for_selector('.group-dialog')
    page.wait_for_timeout(300)
    shot('4_dialog')
    # 选中的是两个 fill，各有一个输入值 → 两行参数
    assert page.locator('.gd-param:not(.is-head)').count() == 2

    # 5. 参数名优先取快照里的英文 name，而不是中文别名
    names = page.locator('.gd-param:not(.is-head) .el-input input').evaluate_all(
        'els => els.map(e => e.value)')
    report.append('param_keys=%s' % names)
    assert names == ['username', 'password'], names
    action_name = page.locator('.group-dialog .el-input input').first.input_value()
    report.append('action_name=%s' % action_name)
    assert action_name == '登录', action_name
    page.keyboard.press('Escape')
    page.wait_for_timeout(200)

    # 6. 退出多选，换成「已封装」的会话看调用卡与动作库
    page.unroute('**/api/automation/recording/current')
    page.route('**/api/automation/recording/current', lambda route: route.fulfill(
        status=200, content_type='application/json',
        body=json.dumps(base_session(PACKED, [ACTION]), ensure_ascii=False)))
    page.reload()
    page.wait_for_selector('.timeline .step')
    page.wait_for_timeout(600)
    assert page.locator('.step.is-call').count() == 2
    shot('5_calls')

    # 7. 动作库
    page.locator('button:has-text("动作库")').first.click()
    page.wait_for_timeout(400)
    shot('6_library')
    assert page.locator('.lib-item').count() == 1

    return report, names


def main():
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 1080})
        page.on('console', lambda m: errors.append(m.text) if m.type == 'error' else None)
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.route('**/api/automation/recording/current', lambda route: route.fulfill(
            status=200, content_type='application/json',
            body=json.dumps(base_session(FLAT), ensure_ascii=False)))
        page.goto(BASE + '/recording', wait_until='networkidle')
        report, names = run(page)
        # 窄面板：封装弹窗不能溢出。先换回「平铺」会话，不然选中的是调用卡，封装会被拒。
        page.unroute('**/api/automation/recording/current')
        page.route('**/api/automation/recording/current', lambda route: route.fulfill(
            status=200, content_type='application/json',
            body=json.dumps(base_session(FLAT), ensure_ascii=False)))
        page.set_viewport_size({'width': 460, 'height': 900})
        page.reload()
        page.wait_for_selector('.timeline .step')
        page.locator('.toolbar button:has-text("封装")').first.click()
        page.locator('.timeline .step').nth(1).click()
        page.locator('.timeline .step').nth(2).click()
        page.locator('.select-actions button:has-text("封装为动作")').click()
        page.wait_for_timeout(400)
        page.screenshot(path=str(OUT / '_act_7_narrow.png'), full_page=True)
        report.append(str(OUT / '_act_7_narrow.png'))
        box = page.locator('.group-dialog').bounding_box()
        report.append('narrow dialog width=%s' % (box and round(box['width'])))
        assert box and box['width'] <= 460, box
        browser.close()
    print('\n'.join(report))
    real = [e for e in errors if 'Failed to load resource' not in e and '404' not in e]
    if real:
        print('CONSOLE ERRORS:', real, file=sys.stderr)
        return 1
    print('OK no console errors')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
