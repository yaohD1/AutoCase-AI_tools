# -*- coding: utf-8 -*-
"""看写手真正收到的那份 dispatch：动作调用有没有被展开成平铺步骤。

这是封装功能唯一无法用单测保证的环节——模型是否照着写成函数取决于提示词。
能确定的是「它看到了什么」，所以这里把 payload 和提示词里相关段落打出来核对。
"""
import json

from app.services.recording import actions as a
from app.services.recording import emit, ir


def target(alias, name=None):
    return {'alias': alias, 'snap': {'tag': 'input', 'name': name or alias},
            'candidates': [], 'primary': None, 'status': 'unique'}


steps = [
    ir.make_step('goto', 1, url='http://demo.local/login'),
    ir.make_step('fill', 2, target=target('用户名', 'username'), value='admin'),
    ir.make_step('fill', 3, target=target('密码', 'password'), value='123456'),
    ir.make_step('click', 4, target=target('登录按钮')),
    ir.make_step('click', 5, target=target('新建')),
    ir.make_step('fill', 6, target=target('用户名', 'username'), value='tester'),
    ir.make_step('fill', 7, target=target('密码', 'password'), value='654321'),
    ir.make_step('click', 8, target=target('登录按钮')),
]

params = [
    {'step': 2, 'key': 'username', 'label': '用户名', 'field': 'value'},
    {'step': 3, 'key': 'password', 'label': '密码', 'field': 'value'},
]
packed, action = a.group(steps, [2, 3, 4], '登录', desc='登录并进入工作台', params=params)
# 第二次调用：同一动作，不同实参
packed, _ = a.group(packed, [4, 5, 6], '登录')  # 这里故意不参数化，仅演示第二个调用点
packed = [s for s in packed if s.get('type') == 'call' or s.get('no') <= 2]
# 上面两步构造得太绕，直接手工拼一个干净的两处调用场景
packed = [
    steps[0],
    ir.make_step('call', 2, action=action['id'], args={'username': 'admin', 'password': '123456'},
                 label='登录'),
    ir.make_step('call', 3, action=action['id'], args={'username': 'tester', 'password': '654321'},
                 label='登录'),
    ir.make_step('click', 4, target=target('新建')),
]
for i, s in enumerate(packed, 1):
    s['no'] = i

actions = {action['id']: action}

payload = emit._payload('playwright-ts', packed, 'tests/recorded.spec.ts', '登录',
                        {'evidence': [], 'samples': []}, [], write_scope='workspace',
                        actions=actions)

print('=== steps ===')
print(json.dumps(payload['steps'], ensure_ascii=False, indent=2))
print('=== actions ===')
print(json.dumps(payload['actions'], ensure_ascii=False, indent=2))

prompt = emit._prompt(payload)
print('=== 提示词里的动作段 ===')
start = prompt.find('本次步骤里有**封装动作**')
assert start >= 0, '提示词缺少动作段'
print(prompt[start:start + 700])

# 断言：调用点没有被展开成子步骤
assert len(payload['steps']) == 4, payload['steps']
assert [s['type'] for s in payload['steps']] == ['goto', 'action', 'action', 'click']
assert payload['actions'][0]['called_at'] == [2, 3]
assert payload['actions'][0]['params'] == ['username', 'password']
assert '{{username}}' in payload['actions'][0]['steps'][0]['value']
assert '绝不允许展开成多段重复代码' in prompt
print('\nOK：调用点保持 action 形态，动作定义带参数占位与被调用位置')
