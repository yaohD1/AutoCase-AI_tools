# -*- coding: utf-8 -*-
"""关闭浏览器步骤 + 后置处理（Test Teardown / afterEach）。

三条硬约束，错一条这个功能就没有意义：
1. 后置的步骤必须从流程里**搬走**。留在流程里会执行两次，第二次往往已经离开那一屏。
2. 生成侧要写 Test Teardown，而且有后置时正文里**不能**再留 Close Browser：
   它在后置之前跑，浏览器一关，后置里所有步骤都落到已关闭的会话上。
3. 回放要跳过 close，并在最后执行后置——回放的浏览器是调试器自己的。
"""
import json
import tempfile
import threading
import unittest
from pathlib import Path

from app.services.recording import emit, ir, replay
from app.services.recording import actions as a
from app.services.recording.session import RecordingSession


def target(alias):
    return {
        'alias': alias,
        'snap': {'tag': 'input', 'name': alias},
        'candidates': [],
        'primary': None,
        'status': 'unique',
    }


def steps_of(*items):
    out = []
    for i, item in enumerate(items, 1):
        kind = item[0]
        alias = item[1] if len(item) > 1 else None
        value = item[2] if len(item) > 2 else None
        out.append(ir.make_step(kind, i, target=target(alias) if alias else None, value=value))
    return out


LOGIN = steps_of(
    ('fill', '用户名', 'admin'),
    ('fill', '密码', '123456'),
    ('click', '登录'),
    ('assert', '工作台'),
)


def login_params():
    return [
        {'step': 1, 'key': 'username', 'label': '用户名', 'field': 'value'},
        {'step': 2, 'key': 'password', 'label': '密码', 'field': 'value'},
    ]


def teardown_of(name='收尾', params=None, args=None, steps=None):
    """生成侧看到的形状：一个动作定义 + 这次用的实参。"""
    return {
        'action': {
            'id': 'a-td',
            'name': name,
            'desc': '',
            'params': params or [],
            'steps': steps if steps is not None else steps_of(('click', '退出登录')),
        },
        'args': args or {},
    }


def make_session(items=None):
    """不启动浏览器的最小会话：够 set/clear/update_teardown 与 snapshot 用。"""
    session = RecordingSession.__new__(RecordingSession)
    session.id = 'test-session'
    session.base_url = 'http://example.test'
    session.status = 'recording'
    session.error = None
    session.workspace = None
    session.framework = 'robot-selenium'
    session._lock = threading.RLock()
    session._steps = [dict(s) for s in (items if items is not None else LOGIN)]
    session._actions = {}
    session._actions_error = None
    session._teardown = None
    session._picks = []
    session._alias_keys = {}
    session._pick = False
    session._paused = False
    session._panel_page = None
    session._panel_hwnd = 0
    session._browser = None
    session._storage_state = None
    session._ask = None
    session._schedule_hud = lambda: None
    return session


class CloseStepTest(unittest.TestCase):
    def test_close_is_a_step_type_without_target(self):
        step = ir.make_step('close', 1, source='manual')
        self.assertEqual('close', step['type'])
        self.assertIsNone(step['target'])
        # 关浏览器不是对元素的操作，不该带定位相关的字段
        self.assertIsNone(step['value'])

    def test_close_cannot_be_edited_in_place(self):
        # 没有值可改，就地编辑要明确拒绝，而不是给一个空表单
        from app.services.recording.session import EDITABLE_FIELDS
        self.assertNotIn('close', EDITABLE_FIELDS)


class GroupMinStepsTest(unittest.TestCase):
    def test_action_still_needs_two_steps(self):
        with self.assertRaises(a.ActionError):
            a.group(LOGIN, [1], '单步')

    def test_teardown_allows_one_step(self):
        steps, action = a.group(LOGIN, [3], '退出登录', min_steps=1)
        # 只换掉第 3 步，其余三步原地保留
        self.assertEqual(4, len(steps))
        self.assertEqual('call', steps[2]['type'])
        self.assertEqual(1, len(action['steps']))


class SessionTeardownTest(unittest.TestCase):
    def test_set_teardown_moves_selected_steps_out_of_flow(self):
        session = make_session()
        data = session.set_teardown([1, 2, 3, 4], '登录', params=login_params())
        # 四步全部搬走，流程里什么都不剩
        self.assertEqual([], data['steps'])
        self.assertEqual('登录', data['teardown']['name'])
        self.assertEqual(4, data['teardown']['steps'])
        self.assertEqual({'username': 'admin', 'password': '123456'},
                         data['teardown']['args'])
        # 子步骤只用于展示，编号是动作内部的相对序号
        self.assertEqual([1, 2, 3, 4], [s['no'] for s in data['teardown']['items']])
        # 动作库里能看出它被后置占用了，避免被误删
        self.assertEqual('teardown', data['actions'][0]['used_as'])

    def test_set_teardown_takes_remaining_steps(self):
        session = make_session()
        data = session.set_teardown([3], '退出登录')
        self.assertEqual(['fill', 'fill', 'assert'], [s['type'] for s in data['steps']])
        self.assertEqual(1, data['teardown']['steps'])

    def test_second_teardown_is_rejected(self):
        session = make_session()
        session.set_teardown([1], '收尾')
        with self.assertRaises(Exception) as ctx:
            session.set_teardown([2], '又一段')
        self.assertIn('已经设过后置', str(ctx.exception))

    def test_clear_teardown_restores_steps_at_the_end(self):
        session = make_session()
        session.set_teardown([3], '退出登录')
        data = session.clear_teardown()
        self.assertIsNone(data['teardown'])
        # 原来的 1/2/4 保持顺序，搬走的那一步回到末尾
        self.assertEqual(['fill', 'fill', 'assert', 'click'], [s['type'] for s in data['steps']])
        self.assertEqual([1, 2, 3, 4], [s['no'] for s in data['steps']])
        # 动作定义留在动作库里（和「解封」一致），还能再挂回去
        self.assertEqual(1, len(data['actions']))

    def test_clear_without_teardown_is_rejected(self):
        session = make_session()
        with self.assertRaises(Exception):
            session.clear_teardown()

    def test_update_teardown_args_rejects_unknown_keys(self):
        session = make_session()
        session.set_teardown([1, 2], '登录', params=login_params())
        with self.assertRaises(Exception) as ctx:
            session.update_teardown_args({'nope': 'x'})
        self.assertIn('没有这些参数', str(ctx.exception))
        data = session.update_teardown_args({'username': 'tester'})
        self.assertEqual('tester', data['teardown']['args']['username'])
        # 没传的键保持原值
        self.assertEqual('123456', data['teardown']['args']['password'])

    def test_delete_action_is_refused_while_used_as_teardown(self):
        session = make_session()
        data = session.set_teardown([1], '收尾')
        action_id = data['teardown']['action']
        with self.assertRaises(Exception) as ctx:
            session.remove_action(action_id)
        self.assertIn('后置处理', str(ctx.exception))

    def test_teardown_definition_carries_action_and_args(self):
        session = make_session()
        session.set_teardown([1, 2], '登录', params=login_params())
        definition = session.teardown_definition()
        self.assertEqual('登录', definition['action']['name'])
        self.assertEqual(2, len(definition['action']['steps']))
        self.assertEqual({'username': 'admin', 'password': '123456'}, definition['args'])
        # 返回的是副本：生成在另一个线程跑，不能和会话共用同一个 dict
        definition['action']['name'] = '改掉'
        self.assertEqual('登录', session.teardown_definition()['action']['name'])

    def test_snapshot_has_no_teardown_by_default(self):
        session = make_session()
        self.assertIsNone(session.snapshot()['teardown'])

    def test_restore_teardown_brings_action_back(self):
        # 换了工作区、动作库读不出来时，存档里的动作定义要能补回来
        session = make_session()
        session._restore_teardown(teardown_of('收尾', args={'k': 'v'}, params=[{'key': 'k'}]))
        data = session.snapshot()
        self.assertEqual('收尾', data['teardown']['name'])
        self.assertEqual({'k': 'v'}, data['teardown']['args'])
        self.assertEqual('收尾', session.teardown_definition()['action']['name'])

    def test_restore_teardown_keeps_library_version(self):
        # 动作库里已有同 id 的定义时不要覆盖：库里那份可能被用户改过名
        session = make_session()
        session._actions = {'a-td': {'id': 'a-td', 'name': '库里名字', 'params': [], 'steps': []}}
        session._restore_teardown(teardown_of('存档名字'))
        self.assertEqual('库里名字', session.snapshot()['teardown']['name'])


class EmitPayloadTest(unittest.TestCase):
    def test_payload_carries_teardown_in_writer_shape(self):
        payload = emit._payload('robot-selenium', LOGIN, 'x.robot', 't', {}, [],
                                actions=None, teardown=teardown_of(
                                    '收尾', params=[{'key': 'who'}], args={'who': 'admin'}))
        shaped = payload['teardown']
        self.assertEqual('收尾', shaped['name'])
        self.assertEqual(['who'], shaped['params'])
        self.assertEqual({'who': 'admin'}, shaped['args'])
        self.assertEqual(['click'], [s['type'] for s in shaped['steps']])

    def test_payload_has_no_teardown_key_when_not_set(self):
        payload = emit._payload('robot-selenium', LOGIN, 'x.robot', 't', {}, [])
        self.assertNotIn('teardown', payload)

    def test_prompt_writes_rf_teardown_rules(self):
        payload = emit._payload('robot-selenium', LOGIN, 'x.robot', 't', {}, [],
                                teardown=teardown_of('收尾'))
        text = emit._prompt(payload)
        self.assertIn('后置处理', text)
        self.assertIn('Test Teardown', text)
        # 后置的关浏览器交给平台，模型不该自己加
        self.assertIn('不要在这个 Keyword 里加 Close Browser', text)

    def test_prompt_writes_pw_teardown_rules(self):
        payload = emit._payload('playwright-ts', LOGIN, 'tests/a.spec.ts', 't', {}, [],
                                teardown=teardown_of('收尾'))
        text = emit._prompt(payload)
        self.assertIn('test.afterEach', text)
        self.assertIn('finally', text)

    def test_prompt_translates_close_step(self):
        steps = steps_of(('goto', None), ('close',))
        steps[0]['url'] = 'http://x'
        rf = emit._prompt(emit._payload('robot-selenium', steps, 'x.robot', 't', {}, []))
        self.assertIn('type 为 close', rf)
        self.assertIn('Close Browser', rf)
        pw = emit._prompt(emit._payload('playwright-ts', steps, 'tests/a.spec.ts', 't', {}, []))
        self.assertIn('page.close()', pw)


class NormalizeRobotTest(unittest.TestCase):
    """平台补头的两条分支：有后置 / 没有后置。"""

    SOURCE = '\n'.join([
        '# @autocase 录制自 http://x',
        '*** Settings ***',
        'Library    SeleniumLibrary',
        '',
        '*** Test Cases ***',
        '用例',
        '    Go To    http://x',
        '    Click Element    id=a',
        '    Close Browser',
        '',
        '*** Keywords ***',
        '收尾',
        '    Click Element    id=logout',
        '',
    ])

    def _normalize(self, steps, teardown=None):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'a.robot'
            path.write_text(self.SOURCE, encoding='utf-8')
            emit._normalize_rf_script(tmp, 'a.robot', steps, teardown=teardown)
            return path.read_text(encoding='utf-8')

    def test_writes_test_teardown_and_drops_body_close(self):
        steps = steps_of(('goto', None), ('click', 'a'), ('close',))
        steps[0]['url'] = 'http://x'
        text = self._normalize(steps, teardown=teardown_of('收尾'))
        self.assertIn('Test Teardown    收尾', text)
        # 有后置时正文里的 Close Browser 必须摘掉：它跑在后置之前
        body = text.split('*** Test Cases ***')[1].split('*** Keywords ***')[0]
        self.assertNotIn('Close Browser', body)
        self.assertIn('Suite Teardown    Close Browser', text)
        # 模型写的关键字定义要保留
        self.assertIn('id=logout', text)

    def test_keeps_body_close_without_teardown(self):
        steps = steps_of(('goto', None), ('click', 'a'), ('close',))
        steps[0]['url'] = 'http://x'
        text = self._normalize(steps)
        body = text.split('*** Test Cases ***')[1].split('*** Keywords ***')[0]
        self.assertIn('Close Browser', body)
        self.assertNotIn('Test Teardown', text)

    def test_teardown_args_are_written_in_param_order(self):
        steps = steps_of(('goto', None), ('click', 'a'))
        steps[0]['url'] = 'http://x'
        params = [{'key': 'user'}, {'key': 'note'}]
        text = self._normalize(steps, teardown=teardown_of(
            '收尾', params=params, args={'note': 'done', 'user': 'admin'}))
        self.assertIn('Test Teardown    收尾    admin    done', text)

    def test_empty_arg_is_omitted_to_keep_columns_aligned(self):
        steps = steps_of(('goto', None), ('click', 'a'))
        steps[0]['url'] = 'http://x'
        params = [{'key': 'user'}, {'key': 'note'}]
        text = self._normalize(steps, teardown=teardown_of(
            '收尾', params=params, args={'user': 'admin', 'note': ''}))
        self.assertIn('Test Teardown    收尾    admin\n', text)

    def test_normalize_is_idempotent_with_teardown(self):
        steps = steps_of(('goto', None), ('click', 'a'), ('close',))
        steps[0]['url'] = 'http://x'
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'a.robot'
            path.write_text(self.SOURCE, encoding='utf-8')
            emit._normalize_rf_script(tmp, 'a.robot', steps, teardown=teardown_of('收尾'))
            first = path.read_text(encoding='utf-8')
            emit._normalize_rf_script(tmp, 'a.robot', steps, teardown=teardown_of('收尾'))
            self.assertEqual(first, path.read_text(encoding='utf-8'))
            self.assertEqual(1, first.count('Test Teardown'))


class ReplayTest(unittest.TestCase):
    def test_close_is_a_noop_in_replay(self):
        # 回放的浏览器是调试器自己的：真关掉之后，后置会跑在已关闭的会话上
        self.assertEqual((None, None), replay._run_step({'type': 'close', 'no': 1}, object()))

    def test_teardown_steps_continue_numbering(self):
        extra = replay.teardown_steps(teardown_of('收尾', steps=steps_of(
            ('click', '退出登录'), ('assert', '工作台'))), after_no=7)
        self.assertEqual([8, 9], [s['no'] for s in extra])
        self.assertTrue(all(s.get('_teardown') for s in extra))

    def test_teardown_steps_empty_without_teardown(self):
        self.assertEqual([], replay.teardown_steps(None))

    def test_teardown_label_is_readable(self):
        self.assertEqual('关闭浏览器', replay._step_label({'type': 'close'}))


class TeardownJsonTest(unittest.TestCase):
    def test_definition_is_json_serialisable(self):
        # 存档要把它写进 teardown_json，不能带 _frame 这类页面对象
        definition = teardown_of('收尾', steps=steps_of(('click', '退出登录')))
        text = json.dumps(definition, ensure_ascii=False)
        self.assertEqual('收尾', json.loads(text)['action']['name'])


if __name__ == '__main__':
    unittest.main()
