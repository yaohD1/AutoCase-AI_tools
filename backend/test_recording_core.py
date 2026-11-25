import json
import queue
import threading
import unittest
from unittest.mock import MagicMock

from app.services.recording import intent, ir
from app.services.recording.locators import (
    PW_KINDS, build_specs, rank, render_pw, stale_check, status_of,
)
from app.services.recording.session import HUD_RECENT_STEPS, RecordingError, RecordingSession


class LocatorRankingTest(unittest.TestCase):
    def test_specs_follow_priority_and_skip_dynamic_ids(self):
        snap = {'tag': 'button', 'role': 'button', 'name': '提交', 'text': '提交', 'css': 'html > body > button',
                'id': 'el-id-749-6', 'testid': None, 'label': None, 'placeholder': None}
        kinds = [s['kind'] for s in build_specs(snap)]
        # Playwright 组在前，Robot Framework 组（xpath / css）随后；动态 id 两组都不产生。
        self.assertEqual(kinds, ['role', 'text', 'css', 'rf_xpath', 'rf_css'])
        self.assertNotIn('el-id-749-6', ' '.join(s['desc'] for s in build_specs(snap)))

    def test_first_unique_candidate_wins_and_ambiguous_is_reported(self):
        counted = [
            {'kind': 'role', 'desc': "getByRole('button', name='保存')", 'count': 2},
            {'kind': 'text', 'desc': "getByText('保存')", 'count': 2},
            {'kind': 'css', 'desc': "locator('html > body > button:nth-of-type(3)')", 'count': 1},
        ]
        primary, ordered = rank(counted)
        self.assertEqual(primary['kind'], 'css')
        self.assertEqual([c['kind'] for c in ordered], ['role', 'text', 'css'])
        self.assertEqual(status_of(primary), 'fragile')

    def test_semantic_unique_is_not_fragile(self):
        counted = [{'kind': 'role', 'desc': "getByRole('button', name='提交')", 'count': 1}]
        primary, _ = rank(counted)
        self.assertEqual(status_of(primary), 'unique')

    def test_widget_without_accessible_name_gets_bare_role(self):
        # 没有 label / aria-label 的 select：以前 Playwright 组只剩 css，等于「换框架」时
        # 连同一套框架内的降级都靠结构。补一条不带 name 的 role 兜住这类控件。
        snap = {'tag': 'select', 'type': None, 'role': 'combobox', 'role_attr': None, 'name': None,
                'label': None, 'aria_label': None, 'placeholder': None, 'testid': None,
                'text': None, 'id': None, 'attr_name': 'city', 'css': 'html > body > select'}
        specs = build_specs(snap)
        bare = [s for s in specs if s['kind'] == 'role_bare']
        self.assertEqual(len(bare), 1)
        self.assertEqual(bare[0]['desc'], "getByRole('combobox')")
        self.assertEqual(bare[0]['kwargs'], {}, '不带 name 的 role 候选项不能带 kwargs')
        # 一旦有了可访问名称，就不该再有裸 role：有名称的定位永远更强。
        snap['name'] = '城市'
        self.assertNotIn('role_bare', [s['kind'] for s in build_specs(snap)])

    def test_bare_role_ranks_below_named_locators(self):
        # 有 placeholder 的输入框应优先用 placeholder，而不是「页面上只有一个 textbox」这种弱依据。
        counted = [
            {'kind': 'css', 'desc': 'locator(a)', 'count': 1},
            {'kind': 'role_bare', 'desc': "getByRole('textbox')", 'count': 1},
            {'kind': 'placeholder', 'desc': "getByPlaceholder('密码')", 'count': 1},
        ]
        primary, ordered = rank(counted)
        self.assertEqual(primary['kind'], 'placeholder')
        self.assertEqual([c['kind'] for c in ordered], ['placeholder', 'role_bare', 'css'])

    def test_no_unique_candidate_is_ambiguous(self):
        counted = [{'kind': 'css', 'desc': "locator('div')", 'count': 3}]
        primary, _ = rank(counted)
        self.assertIsNone(primary)
        self.assertEqual(status_of(primary), 'ambiguous')

    def test_stale_when_primary_changes_on_revalidate(self):
        primary = {'kind': 'css', 'desc': "locator('html > body > button:nth-of-type(2)')", 'count': 1}
        self.assertEqual(stale_check("locator('html > body > button:nth-of-type(3)')", primary), 'stale')
        self.assertEqual(stale_check(primary['desc'], primary), 'fragile')
        self.assertEqual(stale_check(primary['desc'], None), 'ambiguous')


class LocatorDescMatchesSpecTest(unittest.TestCase):
    """desc 就是最终写进脚本的那一行：必须和数 count 时用的参数完全一致。

    历史事故：label 用 exact=True 数出「唯一」，desc 却写成 getByLabel('密码')，
    写手照抄后子串匹配命中 input + 表单项容器，运行时 strict mode violation。
    """

    SNAP = {
        'tag': 'input', 'type': 'password', 'role': 'textbox', 'name': '密码',
        'label': '密码', 'placeholder': '请输入密码', 'text': '密码',
        'css': '#pwd', 'testid': 'pwd', 'id': None, 'aria_label': None, 'attr_name': None,
    }

    def _descs(self):
        return {s['kind']: s['desc'] for s in build_specs(self.SNAP) if s['kind'] in PW_KINDS}

    def test_desc_is_rendered_from_the_same_arguments(self):
        descs = self._descs()
        self.assertEqual(descs['testid'], "getByTestId('pwd')")
        self.assertEqual(descs['role'], "getByRole('textbox', { name: '密码', exact: true })")
        self.assertEqual(descs['label'], "getByLabel('密码', { exact: true })")
        self.assertEqual(descs['placeholder'], "getByPlaceholder('请输入密码', { exact: true })")
        self.assertEqual(descs['css'], "locator('#pwd')")
        # text 候选只对承载文本的标签生成，input 不在其中，单独验一条。
        text_snap = dict(self.SNAP, tag='span', text='联网搜索')
        self.assertEqual([s['desc'] for s in build_specs(text_snap) if s['kind'] == 'text'],
                         ["getByText('联网搜索', { exact: true })"])

    def test_desc_never_drops_an_argument(self):
        """任何参数只出现在计数、不出现在 desc 里，都会让「录制的唯一」变成「脚本的不唯一」。"""
        for spec in build_specs(self.SNAP):
            if spec['kind'] not in PW_KINDS:
                continue
            values = list(spec['args']) + list(spec['kwargs'].values())
            for value in values:
                expected = 'true' if value is True else 'false' if value is False else str(value)
                self.assertIn(expected, spec['desc'],
                              '%s 的 desc 丢了参数 %r：%s' % (spec['kind'], value, spec['desc']))

    def test_multiline_text_stays_escaped(self):
        """desc 会原样写进 TS 文件，换行必须是转义序列，否则字符串字面量会断成两行。"""
        desc = render_pw('get_by_text', ['dyh\n管理员\ntest998'], {'exact': True})
        self.assertNotIn('\n', desc)
        self.assertIn('\\n', desc)

    def test_no_python_style_keyword_arguments_left(self):
        """desc 必须是能直接粘贴的 JS：之前 role 的 name=... 要靠写手翻译，exact 就是在翻译时丢的。"""
        for desc in self._descs().values():
            self.assertNotIn('name=', desc)
            self.assertIn('(', desc)


class IRTest(unittest.TestCase):
    def test_unknown_step_type_rejected(self):
        with self.assertRaises(ValueError):
            ir.make_step('hover', no=1)

    def test_public_step_strips_internal_fields(self):
        step = ir.make_step('fill', no=1, value='x')
        step['_frame'] = object()
        out = ir.public_step(step)
        self.assertNotIn('_frame', out)
        self.assertEqual(out['value'], 'x')


class IntentTest(unittest.TestCase):
    ALIASES = ['提交', '用户名']

    def test_visible_with_known_alias(self):
        got = intent.validate({'type': 'visible', 'target': '提交'}, self.ALIASES)
        self.assertEqual(got['type'], 'visible')
        self.assertEqual(got['target'], '提交')

    def test_unknown_alias_rejected(self):
        with self.assertRaises(intent.IntentError):
            intent.validate({'type': 'visible', 'target': '不存在'}, self.ALIASES)

    def test_text_contains_needs_value(self):
        with self.assertRaises(intent.IntentError):
            intent.validate({'type': 'text_contains', 'target': '提交'}, self.ALIASES)

    def test_url_contains_needs_no_target(self):
        got = intent.validate({'type': 'url_contains', 'value': '/dashboard'}, [])
        self.assertIsNone(got['target'])
        self.assertEqual(got['value'], '/dashboard')

    def test_page_text_contains_needs_value_but_no_target(self):
        got = intent.validate({'type': 'page_text_contains', 'value': '登录成功'}, [])
        self.assertIsNone(got['target'])
        self.assertEqual(got['value'], '登录成功')
        with self.assertRaises(intent.IntentError):
            intent.validate({'type': 'page_text_contains'}, [])
        # 模型可能给不需要目标的类型硬塞一个别名，直接丢掉，别让它变成假的目标元素
        got = intent.validate({'type': 'page_text_contains', 'value': 'x', 'target': '不存在'}, [])
        self.assertIsNone(got['target'])

    def test_sleep_bounds(self):
        self.assertEqual(intent.validate({'type': 'sleep', 'seconds': '2'}, [])['seconds'], 2.0)
        with self.assertRaises(intent.IntentError):
            intent.validate({'type': 'sleep', 'seconds': 0}, [])
        with self.assertRaises(intent.IntentError):
            intent.validate({'type': 'sleep', 'seconds': 999}, [])

    def test_unknown_type_rejected(self):
        with self.assertRaises(intent.IntentError):
            intent.validate({'type': 'screenshot'}, self.ALIASES)

    def test_parse_accepts_fenced_json_and_validates(self):
        reply = '好的：\n```json\n{"type": "wait_visible", "target": "提交"}\n```'
        got = intent.parse_intent('等到提交按钮出现', self.ALIASES, lambda system, prompt: reply)
        self.assertEqual(got['type'], 'wait_visible')

    def test_parse_prompt_lists_aliases_only(self):
        seen = {}

        def ask(system, prompt):
            seen['system'] = system
            seen['prompt'] = prompt
            return '{"type": "visible", "target": "提交"}'

        intent.parse_intent('提交按钮可见', self.ALIASES, ask)
        self.assertEqual(seen['system'], intent.SYSTEM_PROMPT)
        self.assertIn('- 提交', seen['prompt'])
        self.assertIn('- 用户名', seen['prompt'])
        # 类型只列名字不够，模型会猜；提示词里要写清哪些不需要目标元素。
        self.assertIn('page_text_contains：整个页面的文字', seen['prompt'])
        self.assertIn('url_contains：当前地址', seen['prompt'])

    def test_parse_rejects_non_json_reply(self):
        with self.assertRaises(intent.IntentError):
            intent.parse_intent('可见', self.ALIASES, lambda system, prompt: '抱歉，我无法回答')

    def test_empty_description_rejected(self):
        with self.assertRaises(intent.IntentError):
            intent.parse_intent('   ', self.ALIASES, lambda system, prompt: '{}')


class HudTest(unittest.TestCase):
    """被录制页面里的浮层：数据裁剪与「撤销上一步」，不需要启动浏览器。"""

    def _session(self):
        return RecordingSession('http://example.test/')

    @staticmethod
    def _target(alias, status, desc='role=button[name="提交"]'):
        snap = {'tag': 'button', 'name': alias, 'text': alias}
        primary = {'kind': 'role', 'desc': desc, 'count': 1} if status != 'ambiguous' else None
        return ir.make_target(snap, [], primary, status)

    def test_payload_carries_recent_steps_and_ambiguous_count(self):
        session = self._session()
        session._steps = [
            ir.make_step('click', no=1, target=self._target('提交', 'unique')),
            ir.make_step('click', no=2, target=self._target('更多', 'ambiguous', desc='')),
        ]
        payload = session._hud_payload()
        self.assertEqual(payload['total'], 2)
        self.assertEqual(payload['ambiguous'], 1)
        self.assertEqual(payload['steps'][-1]['no'], 2)
        self.assertEqual(payload['steps'][-1]['locator'], '')
        self.assertEqual(payload['steps'][0]['locator'], 'role=button[name="提交"]')

    def test_payload_only_keeps_recent_steps(self):
        session = self._session()
        session._steps = [ir.make_step('click', no=i + 1, target=self._target('步骤', 'unique'))
                          for i in range(HUD_RECENT_STEPS + 2)]
        payload = session._hud_payload()
        self.assertEqual(payload['total'], HUD_RECENT_STEPS + 2)
        self.assertEqual(len(payload['steps']), HUD_RECENT_STEPS)
        self.assertEqual(payload['steps'][-1]['no'], HUD_RECENT_STEPS + 2)

    def test_payload_titles_by_step_type(self):
        session = self._session()
        session._steps = [ir.make_step('goto', no=1, url='http://example.test/login')]
        item = session._hud_payload()['steps'][0]
        self.assertEqual(item['title'], 'http://example.test/login')
        self.assertEqual(item['status'], 'none')

    def test_undo_drops_last_step(self):
        session = self._session()
        session._steps = [
            ir.make_step('click', no=1, target=self._target('提交', 'unique')),
            ir.make_step('fill', no=2, target=self._target('用户名', 'unique'), value='admin'),
        ]
        session._handle_hud_action('undo')
        self.assertEqual([step['no'] for step in session._steps], [1])

    def test_undo_drops_new_page_together_with_previous_step(self):
        session = self._session()
        session._steps = [
            ir.make_step('click', no=1, target=self._target('提交', 'unique')),
            ir.make_step('new_page', no=2, page_index=1),
        ]
        session._handle_hud_action('undo')
        self.assertEqual(session._steps, [])

    def test_unknown_action_is_ignored(self):
        session = self._session()
        session._steps = [ir.make_step('click', no=1, target=self._target('提交', 'unique'))]
        session._handle_hud_action('clear')
        self.assertEqual(len(session._steps), 1)


class StepEditTest(unittest.TestCase):
    """就地编辑值与拖拽排序（不需要浏览器）。"""

    def _session(self):
        session = RecordingSession('http://example.test/')
        target = ir.make_target(
            {'tag': 'input', 'name': '密码'}, [],
            {'kind': 'role', 'desc': "getByRole('textbox', name='密码')", 'count': 1}, 'unique')
        session._steps = [
            ir.make_step('goto', no=1, url='http://example.test/login'),
            ir.make_step('fill', no=2, target=target, value='Du1103159086'),
            ir.make_step('click', no=3, target=target),
        ]
        return session

    def test_update_fill_value(self):
        session = self._session()
        session.update_step(2, {'value': 'Du11031'})
        self.assertEqual(session._steps[1]['value'], 'Du11031')

    def test_update_is_limited_to_editable_fields(self):
        session = self._session()
        with self.assertRaises(RecordingError):
            session.update_step(2, {'url': 'http://evil.test'})

    def test_update_rejects_step_without_editable_fields(self):
        session = self._session()
        with self.assertRaises(RecordingError):
            session.update_step(3, {'value': 'x'})

    def test_update_goto_requires_http_url(self):
        session = self._session()
        with self.assertRaises(RecordingError):
            session.update_step(1, {'url': 'javascript:alert(1)'})
        session.update_step(1, {'url': 'https://example.test/home'})
        self.assertEqual(session._steps[0]['url'], 'https://example.test/home')

    def test_update_sleep_seconds(self):
        session = RecordingSession('http://example.test/')
        session._steps = [ir.make_step('sleep', no=1)]
        session.update_step(1, {'seconds': 2.5})
        self.assertEqual(session._steps[0]['intent']['seconds'], 2.5)
        self.assertIn('2.5', session._steps[0]['text'])
        with self.assertRaises(RecordingError):
            session.update_step(1, {'seconds': 0})

    def test_update_click_settle(self):
        """点击的「执行前等待」：开、关、边界都走同一个入口。"""
        session = self._session()
        session.update_step(3, {'settle': {'timeout': 5}})
        self.assertEqual(session._steps[2]['settle'], {'timeout': 5})
        session.update_step(3, {'settle': None})
        self.assertNotIn('settle', session._steps[2])

    def test_update_click_settle_range(self):
        session = self._session()
        for bad in (0, 0.4, 361):
            with self.assertRaises(RecordingError):
                session.update_step(3, {'settle': {'timeout': bad}})
        session.update_step(3, {'settle': {'timeout': 360}})
        self.assertEqual(session._steps[2]['settle']['timeout'], 360)
        # 非 click 步骤不接受这个字段，避免「给输入框加等待」这种无意义状态
        with self.assertRaises(RecordingError):
            session.update_step(2, {'settle': {'timeout': 5}})

    def test_move_to_reorders_by_position(self):
        session = self._session()
        session.move_step_to(3, 0)
        self.assertEqual([step['type'] for step in session._steps], ['click', 'goto', 'fill'])
        self.assertEqual([step['no'] for step in session._steps], [1, 2, 3])

    def test_move_keeps_click_and_new_page_together(self):
        session = RecordingSession('http://example.test/')
        session._steps = [
            ir.make_step('click', no=1),
            ir.make_step('new_page', no=2, page_index=1),
            ir.make_step('fill', no=3, value='x'),
        ]
        session.move_step_to(1, 1)
        self.assertEqual([step['type'] for step in session._steps], ['fill', 'click', 'new_page'])

    def test_move_rejects_new_page_step(self):
        session = RecordingSession('http://example.test/')
        session._steps = [
            ir.make_step('click', no=1),
            ir.make_step('new_page', no=2, page_index=1),
        ]
        with self.assertRaises(RecordingError):
            session.move_step_to(2, 0)


class PauseTest(unittest.TestCase):
    """暂停录制：浏览器可用但不记步骤。"""

    def test_paused_defaults_to_false(self):
        session = RecordingSession('http://example.test/')
        self.assertFalse(session.snapshot()['paused'])

    def test_paused_event_is_dropped(self):
        session = RecordingSession('http://example.test/')
        session._paused = True
        # 暂停时不应走到计数/建步骤（source 是假对象，一旦继续执行就会抛异常）。
        session._handle_event(object(), json.dumps({'type': 'click', 'snap': {'tag': 'button'}}))
        self.assertEqual(session._steps, [])


class FrameworkTest(unittest.TestCase):
    """这次录制按哪个框架生成，必须记在会话上。

    以前它只活在前端 state 和面板 URL 里，后端每个接口各自兜底成 playwright-ts：
    面板丢了参数，定位和生成就静默换了写法，界面上完全看不出来。
    """

    def test_snapshot_exposes_framework(self):
        session = RecordingSession('http://example.test/', framework='robot-selenium')
        self.assertEqual(session.snapshot()['framework'], 'robot-selenium')

    def test_missing_framework_stays_none(self):
        # 没传就是没传：不给一个假装确定的默认值，兜底由调用方决定。
        self.assertIsNone(RecordingSession('http://example.test/').snapshot()['framework'])
        self.assertIsNone(RecordingSession('http://example.test/',
                                           framework='   ').snapshot()['framework'])


class DegradeAfterStopTest(unittest.TestCase):
    """停止即关浏览器之后，`set_degraded` 还得能用。

    否则带「待确认定位」的录制会卡死：浏览器关了没法重新框选，降级又调不动，
    生成一直被拦着。这条路径是纯内存操作，不碰页面，所以线程退了也能走。
    """

    def _session(self, steps=None):
        session = RecordingSession.__new__(RecordingSession)
        session._thread = None          # 录制线程已退出（停止即结束）
        session._lock = threading.Lock()
        session._steps = steps if steps is not None else [
            {'no': 1, 'type': 'click', 'target': {'alias': '保存按钮'}}]
        session.snapshot = lambda: {'steps': session._steps, 'status': 'stopped'}
        return session

    def test_thread_gone_still_marks_degraded(self):
        session = self._session()
        session.set_degraded(1)
        self.assertTrue(session._steps[0]['target']['degraded'])

    def test_unknown_step_still_reports(self):
        session = self._session()
        with self.assertRaises(RecordingError):
            session.set_degraded(9)

    def test_step_without_target_is_refused(self):
        """没有目标元素的步骤没得降级，要说清楚而不是静默标一个空对象。"""
        session = self._session([{'no': 1, 'type': 'goto'}])
        with self.assertRaises(RecordingError):
            session.set_degraded(1)

    def test_stop_is_idempotent_after_thread_exit(self):
        """停止后浏览器已关、线程已退：再点一次停止要回当前快照，而不是报「录制已结束」。"""
        session = self._session()
        self.assertEqual(session.stop()['status'], 'stopped')
        self.assertFalse(session.close_browser()['browser_open'])


class StopKeepsPanelForSpinnerTest(unittest.TestCase):
    """停止时面板不先关：它是「正在关闭」转圈所在的窗口。

    收尾（解析进程约 5 秒 + 关浏览器）期间面板要留着，等 `_shutdown` 最后
    把它收掉——转圈随窗口一起消失，用户不会盯着一块「已停止」的界面干等。
    """

    def _session(self, embed=False):
        session = RecordingSession.__new__(RecordingSession)
        session.status = 'recording'
        session._embed = embed
        session._touch = lambda: None
        session.snapshot = lambda: {'status': 'stopped', 'steps': []}
        return session

    def test_panel_stays_until_shutdown(self):
        session = self._session()
        order = []
        session._close_panel_now = lambda: order.append('panel')
        session._mark_stopped = lambda status, revalidate: (order.append('mark'), {'status': status})[1]
        session._safe_shutdown = lambda: order.append('shutdown')
        reply = queue.Queue(maxsize=1)
        session._commands = queue.Queue()
        session._commands.put(('stop', {}, reply))

        self.assertTrue(session._drain_commands())
        # 面板交给 _shutdown 收，这里不能再提前关——提前关掉转圈就没了。
        self.assertEqual(order, ['mark', 'shutdown'])
        ok, value = reply.get(timeout=1)
        self.assertTrue(ok)
        self.assertTrue(value['closing'])
        self.assertFalse(value['close_done'])
        self.assertFalse(value['browser_open'])

    def test_embed_keeps_its_window(self):
        """嵌入模式的面板就是用户的操作界面，停止不能把人从正在看的页面上踢走。"""
        session = self._session(embed=True)
        order = []
        session._close_panel_now = lambda: order.append('panel')
        session._mark_stopped = lambda status, revalidate: (order.append('mark'), {'status': status})[1]
        session._stop_ask_pool = lambda delay=0: order.append('ask')
        session._commands = queue.Queue()
        session._commands.put(('stop', {}, queue.Queue(maxsize=1)))

        session._drain_commands()
        self.assertNotIn('panel', order)
        self.assertIn('mark', order)

    def test_close_panel_now_is_idempotent(self):
        session = RecordingSession.__new__(RecordingSession)
        order = []
        session._stop_glue = lambda: order.append('glue')
        session._cleanup_panel_app = lambda: order.append('panel')
        context = MagicMock()
        session._panel_context = context
        session._panel_page = 'page'

        session._close_panel_now()
        session._close_panel_now()
        self.assertEqual(order, ['glue', 'panel', 'glue', 'panel'])
        # 兜底形态的面板 context 关完要断引用，否则 snapshot 会报「面板还开着」。
        self.assertEqual(context.close.call_count, 1)
        self.assertIsNone(session._panel_context)
        self.assertIsNone(session._panel_page)


if __name__ == '__main__':
    unittest.main()
