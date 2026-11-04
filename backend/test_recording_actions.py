# -*- coding: utf-8 -*-
"""具名动作：封装、展开、解封、动作库落盘，以及生成提示词里的形状。

这一层全是纯数据改写，不碰浏览器，所以测试直接喂字典。
最要紧的两条：
1. 封装后的子步骤必须保留完整定位，解封要能原样还原；
2. 生成时 call **不能展开**——展开成平铺后，写手会照着写 4 行重复代码，封装就白做了。
"""
import json
import tempfile
import threading
import unittest
from pathlib import Path

from app.services.recording import actions as a
from app.services.recording import emit, ir
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
    """按 (类型, 别名, 值) 造一串步骤，编号从 1 开始。"""
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


class GroupTest(unittest.TestCase):
    def test_replaces_block_with_one_call(self):
        steps, action = a.group(LOGIN, [1, 2, 3, 4], '登录', params=login_params())
        self.assertEqual(1, len(steps))
        self.assertEqual('call', steps[0]['type'])
        self.assertEqual(action['id'], steps[0]['action'])
        self.assertEqual('登录', steps[0]['label'])
        self.assertEqual(4, len(action['steps']))

    def test_params_become_placeholders(self):
        _, action = a.group(LOGIN, [1, 2, 3, 4], '登录', params=login_params())
        self.assertEqual('{{username}}', action['steps'][0]['value'])
        self.assertEqual('{{password}}', action['steps'][1]['value'])
        # 没勾成参数的值保持原样。
        self.assertIsNone(action['steps'][2]['value'])

    def test_args_keep_original_values(self):
        steps, _ = a.group(LOGIN, [1, 2, 3, 4], '登录', params=login_params())
        self.assertEqual({'username': 'admin', 'password': '123456'}, steps[0]['args'])

    def test_action_params_do_not_carry_values(self):
        # 参数定义是「形状」，具体值属于每一次调用；混进定义会在多次调用间串味。
        _, action = a.group(LOGIN, [1, 2, 3, 4], '登录', params=login_params())
        for param in action['params']:
            self.assertNotIn('value', param)

    def test_without_params_keeps_literal_values(self):
        steps, action = a.group(LOGIN, [1, 2, 3, 4], '登录')
        self.assertEqual('admin', action['steps'][0]['value'])
        self.assertEqual({}, steps[0]['args'])

    def test_two_steps_are_enough(self):
        steps, action = a.group(LOGIN, [1, 2], '填账号')
        self.assertEqual(2, len(action['steps']))
        # 后面两步留着，且编号要接上。
        self.assertEqual([1, 2, 3], [s['no'] for s in steps])
        self.assertEqual('click', steps[1]['type'])

    def test_single_step_rejected(self):
        with self.assertRaises(a.ActionError):
            a.group(LOGIN, [1], '一步')

    def test_non_contiguous_rejected(self):
        with self.assertRaises(a.ActionError):
            a.group(LOGIN, [1, 3], '跳步')

    def test_bad_param_key_rejected(self):
        bad = [{'step': 1, 'key': '1abc', 'label': 'x', 'field': 'value'}]
        with self.assertRaises(a.ActionError):
            a.group(LOGIN, [1, 2], '登录', params=bad)

    def test_duplicate_param_key_rejected(self):
        bad = [
            {'step': 1, 'key': 'user', 'label': 'x', 'field': 'value'},
            {'step': 2, 'key': 'user', 'label': 'y', 'field': 'value'},
        ]
        with self.assertRaises(a.ActionError):
            a.group(LOGIN, [1, 2], '登录', params=bad)

    def test_param_source_outside_block_rejected(self):
        bad = [{'step': 4, 'key': 'x', 'label': 'x', 'field': 'value'}]
        with self.assertRaises(a.ActionError):
            a.group(LOGIN, [1, 2], '登录', params=bad)

    def test_nested_action_rejected(self):
        once, _ = a.group(LOGIN, [1, 2], '填账号')
        with self.assertRaises(a.ActionError):
            a.group(once, [1, 2], '再来一层')

    def test_new_page_is_pulled_into_block(self):
        steps = steps_of(('goto', None, 'http://x'), ('click', '打开'), ('new_page',),
                         ('click', '关闭'))
        # 终点落在 click 上、它的 new_page 在区间外：必须一起吞进来，
        # 否则展开后这个 new_page 就没有对应的点击可以跟。
        merged, action = a.group(steps, [1, 2], '打开页')
        self.assertEqual(['goto', 'click', 'new_page'], [s['type'] for s in action['steps']])
        self.assertEqual(['call', 'click'], [s['type'] for s in merged])

        # 起点落在 new_page 上：往前吞掉触发它的点击。
        merged, action = a.group(steps, [3, 4], '关闭页')
        self.assertEqual(['click', 'new_page', 'click'], [s['type'] for s in action['steps']])
        self.assertEqual(['goto', 'call'], [s['type'] for s in merged])

    def test_block_keeps_full_locators(self):
        _, action = a.group(LOGIN, [1, 2], '填账号')
        self.assertTrue(action['steps'][0]['target']['candidates'] is not None)
        self.assertEqual('用户名', action['steps'][0]['target']['alias'])

    def test_frame_reference_is_dropped(self):
        # _frame 是录制线程里的 Playwright 对象，不能进动作定义，也不能被序列化。
        with_frame = [dict(s) for s in LOGIN[:2]]
        with_frame[0]['_frame'] = object()
        _, action = a.group(with_frame, [1, 2], '填账号')
        self.assertNotIn('_frame', action['steps'][0])


class ExpandTest(unittest.TestCase):
    def test_expand_replaces_placeholders(self):
        steps, action = a.group(LOGIN, [1, 2, 3, 4], '填账号', params=login_params())
        out = a.expand(steps, {action['id']: action})
        self.assertEqual(['fill', 'fill', 'click', 'assert'], [s['type'] for s in out])
        self.assertEqual('admin', out[0]['value'])
        self.assertEqual('123456', out[1]['value'])
        self.assertEqual('填账号', out[0]['_action']['name'])

    def test_expand_renumbers_in_flow_order(self):
        steps, action = a.group(LOGIN, [1, 2], '填账号', params=login_params())
        steps.insert(1, ir.make_step('click', 0, target=target('中间'), value=None))
        out = a.expand(a.clean(steps), {action['id']: action})
        # call(2 子步) + 中间那步 + 剩下 2 步 = 5
        self.assertEqual([1, 2, 3, 4, 5], [s['no'] for s in out])

    def test_expand_keeps_unknown_call(self):
        steps = [ir.make_step('call', 1, action='missing', args={})]
        out = a.expand(steps, {})
        self.assertEqual('call', out[0]['type'])

    def test_expand_two_calls_use_their_own_args(self):
        steps, action = a.group(LOGIN, [1, 2], '填账号', params=login_params())
        second = ir.make_step('call', 0, action=action['id'],
                              args={'username': 'tester', 'password': 'x'})
        out = a.expand(a.clean(steps) + [second], {action['id']: action})
        # 第一次调用（2 步）+ 剩下 2 步 + 第二次调用（2 步），各次用自己的实参。
        self.assertEqual(['admin', '123456', None, None, 'tester', 'x'],
                         [s['value'] for s in out])

    def test_missing_arg_leaves_placeholder(self):
        # 旧动作库里少了一个键时，宁可让占位符露在界面上，也不要静默填空串。
        action = {'id': 'a1', 'name': 'x', 'params': [{'key': 'u'}],
                  'steps': [ir.make_step('fill', 1, target=target('u'), value='{{u}}')]}
        out = a.expand([ir.make_step('call', 1, action='a1', args={})], {'a1': action})
        self.assertEqual('{{u}}', out[0]['value'])


class UngroupTest(unittest.TestCase):
    def test_restores_sub_steps_with_arg_values(self):
        steps, action = a.group(LOGIN, [1, 2, 3, 4], '填账号', params=login_params())
        out = a.ungroup(steps, {action['id']: action}, 1)
        self.assertEqual(['fill', 'fill', 'click', 'assert'], [s['type'] for s in out])
        self.assertEqual('admin', out[0]['value'])
        self.assertNotIn('_action', out[0])

    def test_rejects_non_call_step(self):
        with self.assertRaises(a.ActionError):
            a.ungroup(LOGIN, {}, 1)


class LibraryTest(unittest.TestCase):
    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, action = a.group(LOGIN, [1, 2], '填账号', params=login_params())
            path = a.save(tmp, {action['id']: action})
            self.assertTrue(Path(path).is_file())
            loaded = a.load(tmp)
            self.assertEqual(['填账号'], [x['name'] for x in loaded.values()])
            self.assertEqual('{{username}}', loaded[action['id']]['steps'][0]['value'])
            self.assertEqual(['username', 'password'],
                             [p['key'] for p in loaded[action['id']]['params']])

    def test_load_missing_file_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual({}, a.load(tmp))

    def test_load_broken_file_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            library = Path(tmp) / a.LIBRARY_DIR / a.LIBRARY_FILE
            library.parent.mkdir(parents=True)
            library.write_text('{not json', encoding='utf-8')
            self.assertEqual({}, a.load(tmp))

    def test_save_without_workspace_is_noop(self):
        self.assertIsNone(a.save(None, {}))

    def test_saved_file_has_no_private_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, action = a.group(LOGIN, [1, 2], '填账号')
            a.save(tmp, {action['id']: action})
            raw = (Path(tmp) / a.LIBRARY_DIR / a.LIBRARY_FILE).read_text(encoding='utf-8')
            self.assertNotIn('"_frame"', raw)
            json.loads(raw)


class CandidatesTest(unittest.TestCase):
    def test_lists_input_values_in_order(self):
        found = a.candidates(LOGIN)
        self.assertEqual([1, 2], [c['step'] for c in found])
        # 中文别名猜不出标识符，退回 arg / arg2；用户在封装弹窗里改。
        self.assertEqual(['arg', 'arg2'], [c['key'] for c in found])
        self.assertEqual('admin', found[0]['value'])

    def test_chinese_alias_falls_back_to_arg(self):
        found = a.candidates(steps_of(('fill', '用户名', 'x')))
        self.assertEqual('arg', found[0]['key'])

    def test_english_snapshot_name_wins(self):
        # 真实录制里别名是中文，但快照的 name / placeholder 往往就是代码里的标识符。
        step = ir.make_step('fill', 1, target={
            'alias': '用户名', 'snap': {'tag': 'input', 'name': 'username'}}, value='admin')
        self.assertEqual('username', a.candidates([step])[0]['key'])

    def test_placeholder_is_used_when_no_name(self):
        step = ir.make_step('fill', 1, target={
            'alias': '密码', 'snap': {'tag': 'input', 'placeholder': 'inputPassword'}}, value='x')
        self.assertEqual('inputPassword', a.candidates([step])[0]['key'])

    def test_duplicate_aliases_get_suffix(self):
        found = a.candidates(steps_of(('fill', 'name', 'a'), ('fill', 'name', 'b')))
        self.assertEqual(['name', 'name2'], [c['key'] for c in found])

    def test_empty_value_is_skipped(self):
        self.assertEqual([], a.candidates(steps_of(('fill', 'name', ''))))


class PromptShapeTest(unittest.TestCase):
    def test_call_is_not_expanded_for_writer(self):
        steps, action = a.group(LOGIN, [1, 2, 3, 4], '填账号', params=login_params())
        out = emit._steps_for_prompt(steps, 'playwright-ts', {action['id']: action})
        self.assertEqual(1, len(out))
        self.assertEqual('action', out[0]['type'])
        self.assertEqual('填账号', out[0]['name'])
        self.assertEqual({'username': 'admin', 'password': '123456'}, out[0]['args'])

    def test_actions_carry_called_at(self):
        steps, action = a.group(LOGIN, [1, 2], '填账号', params=login_params())
        again = ir.make_step('call', 2, action=action['id'], args={})
        steps = steps + [again]
        out = emit._actions_for_prompt({action['id']: action}, 'playwright-ts', steps)
        self.assertEqual([1, 2], out[0]['called_at'])
        self.assertEqual(['username', 'password'], out[0]['params'])
        self.assertEqual('{{username}}', out[0]['steps'][0]['value'])

    def test_plan_step_describes_action(self):
        item = emit._plan_step(ir.make_step('call', 3, action='a1', label='登录', args={'u': 'x'}))
        self.assertEqual('action', item['type'])
        self.assertEqual('登录', item['name'])
        self.assertEqual({'u': 'x'}, item['args'])

    def test_payload_has_no_actions_key_without_actions(self):
        payload = emit._payload('playwright-ts', LOGIN, 'tests/a.spec.ts', 't',
                                {}, [], write_scope='exact')
        self.assertNotIn('actions', payload)


def make_session(workspace=None):
    """不启动浏览器的最小会话：够 group_steps / insert_call / snapshot 用。"""
    session = RecordingSession.__new__(RecordingSession)
    session.id = 'test-session'
    session.base_url = 'http://example.test'
    session.status = 'recording'
    session.error = None
    session.workspace = workspace
    session._lock = threading.RLock()
    session._steps = [dict(s) for s in LOGIN]
    session._actions = {}
    session._actions_error = None
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


class SessionActionsTest(unittest.TestCase):
    def test_group_then_snapshot_lists_action(self):
        session = make_session()
        data = session.group_steps([1, 2, 3, 4], '登录', params=login_params())
        self.assertEqual(1, len(data['steps']))
        self.assertEqual('call', data['steps'][0]['type'])
        self.assertEqual(1, len(data['actions']))
        self.assertEqual(1, data['actions'][0]['calls'])
        self.assertEqual(4, data['actions'][0]['steps'])
        # 没选工作区：动作库不落盘，界面要能看出这一点。
        self.assertFalse(data['actions_saved'])
        self.assertIn('工作区', data['actions_error'])

    def test_group_writes_library_when_workspace_given(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = make_session(workspace=tmp)
            data = session.group_steps([1, 2], '填账号')
            self.assertTrue(data['actions_saved'])
            self.assertTrue((Path(tmp) / a.LIBRARY_DIR / a.LIBRARY_FILE).is_file())

    def test_insert_call_twice_reuses_action(self):
        session = make_session()
        session.group_steps([1, 2, 3, 4], '填账号', params=login_params())
        data = session.insert_call(session.actions_snapshot()[0]['id'])
        self.assertEqual(2, len(data['steps']))
        self.assertEqual(2, data['actions'][0]['calls'])
        # 第二次调用沿用第一次的实参。
        self.assertEqual('admin', data['steps'][1]['args']['username'])

    def test_insert_call_with_new_args(self):
        session = make_session()
        session.group_steps([1, 2, 3, 4], '填账号', params=login_params())
        action_id = session.actions_snapshot()[0]['id']
        data = session.insert_call(action_id, args={'username': 'tester'})
        self.assertEqual('tester', data['steps'][1]['args']['username'])
        self.assertEqual('123456', data['steps'][1]['args']['password'])

    def test_update_call_rejects_unknown_key(self):
        session = make_session()
        session.group_steps([1, 2], '填账号', params=login_params())
        with self.assertRaises(Exception):
            session.update_call(1, {'nope': 'x'})

    def test_update_call_merges(self):
        session = make_session()
        session.group_steps([1, 2], '填账号', params=login_params())
        data = session.update_call(1, {'username': 'root'})
        self.assertEqual({'username': 'root', 'password': '123456'}, data['steps'][0]['args'])

    def test_remove_action_with_calls_rejected(self):
        session = make_session()
        session.group_steps([1, 2], '填账号', params=login_params())
        action_id = session.actions_snapshot()[0]['id']
        with self.assertRaises(Exception) as ctx:
            session.remove_action(action_id)
        self.assertIn('第 1 步', str(ctx.exception))

    def test_remove_action_after_ungroup(self):
        session = make_session()
        session.group_steps([1, 2], '填账号', params=login_params())
        action_id = session.actions_snapshot()[0]['id']
        data = session.ungroup_step(1)
        self.assertEqual(4, len(data['steps']))
        self.assertEqual([1, 2, 3, 4], [s['no'] for s in data['steps']])
        self.assertEqual('admin', data['steps'][0]['value'])
        session.remove_action(action_id)
        self.assertEqual([], session.actions_snapshot())

    def test_rename_action_updates_calls(self):
        session = make_session()
        session.group_steps([1, 2], '填账号', params=login_params())
        action_id = session.actions_snapshot()[0]['id']
        data = session.update_action(action_id, {'name': '输入账号'})
        self.assertEqual('输入账号', data['steps'][0]['label'])

    def test_replay_keeps_order_after_expand(self):
        session = make_session()
        session.group_steps([1, 2], '填账号', params=login_params())
        expanded = a.expand(session.ir(), session.action_definitions())
        self.assertEqual(['fill', 'fill', 'click', 'assert'], [s['type'] for s in expanded])
        self.assertEqual('admin', expanded[0]['value'])


if __name__ == '__main__':
    unittest.main()
