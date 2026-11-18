"""回放失败的诊断与修复：判定要准，决定权要留在人手里。

两类测试分开写：

- `DiagnoseTest` 用真浏览器验证「这次失败到底该判成什么」——重点是别把
  「页面真的变了」说成「临时状态」，那会让用户点一次就没下文；
- `FlowTest` 不碰浏览器，只验决定链路：失败后要停住、要等决定、应用要真的
  写回、跳过要记成 skipped 而不是 ok。
"""
import threading
import time
import unittest
from unittest import mock

from app.services.recording import debug, healer, locators

LOGIN_OK = '<button id="login"><span>登 录</span></button>'
# 录制之后按钮里多了一个 loading 图标：可访问名从「登 录」变成「登 录 loading」，
# 精确的 role 匹配立刻归零，但按钮里那个 span 的文字还是「登 录」。
LOGIN_CHANGED = ('<button id="login"><span>登 录</span>'
                 '<img alt="loading" width="12" height="12"></button>')

LOGIN_SNAP = {
    'tag': 'button', 'role': 'button', 'name': '登 录',
    'text': '登 录', 'text_flat': '登 录', 'css': '#login',
    'class_raw': '', 'id': 'login', 'attr_name': None, 'label': None,
    'placeholder': None, 'testid': None, 'aria_label': None, 'role_attr': None,
    'frame_url': None, 'frame_name': None, 'row_scope': None,
}


def _target(page, snap, framework='playwright-ts'):
    """按录制时的页面数一遍，得到一份和真实录制同构的 target。"""
    counted = []
    for spec in locators.build_specs(snap):
        try:
            count = locators.build_locator(page, spec).count()
        except Exception:
            count = -1
        counted.append({'kind': spec['kind'], 'desc': spec['desc'],
                        'count': count, 'spec': spec})
    return {'alias': '登 录', 'snap': dict(snap), 'candidates': counted,
            'primary': locators.pick_for(framework, counted)}


def _step(kind='click', target=None, intent=None):
    step = {'no': 7, 'type': kind, 'target': target}
    if intent:
        step['intent'] = intent
    return step


class DiagnoseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._pw.stop()

    def setUp(self):
        self.page = self.browser.new_page()

    def tearDown(self):
        self.page.close()

    def _recorded(self, snap=None, html=LOGIN_OK):
        self.page.set_content(html)
        return _target(self.page, snap or LOGIN_SNAP)

    def test_locator_changed_falls_back_to_surviving_candidate(self):
        """角色名被污染、但同一个按钮里还留着原文：要给出能唯一命中的替换定位。"""
        target = self._recorded()
        self.page.set_content(LOGIN_CHANGED)
        card = healer.diagnose(self.page, _step(target=target), 'playwright-ts',
                               "Locator.click: Timeout 30000ms exceeded.")
        self.assertEqual(card['category'], 'locator_changed')
        self.assertEqual(card['status'], 'ready')
        self.assertTrue(card['can_apply'], card)
        self.assertTrue(card['verify']['ok'], card['verify'])
        self.assertNotEqual((target['primary'] or {}).get('desc'),
                            card['patch']['primary']['desc'])
        self.assertTrue(card['patch']['candidates'], '补丁要带上重数后的候选，否则 RF 那侧不会跟着改')

    def test_patch_really_hits_the_button(self):
        """换了定位之后必须真的能点中那个按钮，而不只是「count == 1」。"""
        target = self._recorded()
        self.page.set_content(LOGIN_CHANGED)
        card = healer.diagnose(self.page, _step(target=target), 'playwright-ts', 'Timeout')
        patched = healer.apply_patch(_step(target=target), card['patch'])
        locator, _ = __import__('app.services.recording.replay', fromlist=['x'])._locator(
            self.page, patched['target'])
        self.assertEqual(locator.inner_text().strip(), '登 录')
        self.assertIsNotNone(locator.bounding_box())

    def test_unchanged_page_is_transient_not_timing_guess(self):
        """定位其实还在：别编一个「换定位」的建议，直接重试才是对的。"""
        target = self._recorded()
        card = healer.diagnose(self.page, _step(target=target), 'playwright-ts', 'Timeout')
        self.assertEqual(card['category'], 'transient')
        self.assertTrue(card['patch'].get('retry_only'))
        self.assertTrue(card['can_apply'])

    def test_ambiguous_without_recorded_index_refuses_to_guess(self):
        """变成 3 个又没有录制序号：宁可不修，也不能随便挑一个。"""
        target = self._recorded()
        self.page.set_content('<button>登 录</button><button>登 录</button><button>登 录</button>')
        card = healer.diagnose(self.page, _step(target=target), 'playwright-ts', 'strict mode violation')
        self.assertEqual(card['category'], 'ambiguous')
        self.assertFalse(card['can_apply'])
        self.assertIn('3', card['reason'])

    def test_ambiguous_with_recorded_index_uses_that_index(self):
        """录制时真记过序号，就按序号修，并标成有风险。"""
        target = self._recorded()
        target['primary'] = dict(target['primary'], index=1)
        self.page.set_content('<button>登 录</button><button>登 录</button><button>登 录</button>')
        card = healer.diagnose(self.page, _step(target=target), 'playwright-ts', 'strict mode violation')
        self.assertEqual(card['category'], 'ambiguous')
        self.assertTrue(card['can_apply'])
        self.assertEqual(card['risk'], 'medium')
        patched = healer.apply_patch(_step(target=target), card['patch'])
        self.assertEqual(patched['target']['primary']['index'], 1)

    def test_stale_frame_is_dropped_but_only_when_element_is_found(self):
        """iframe 元数据失效时去掉约束，在主页面重新找——找不到就老实说找不到。"""
        snap = dict(LOGIN_SNAP, frame_url='https://old.example/frame', frame_name='old')
        target = self._recorded(snap)
        card = healer.diagnose(self.page, _step(target=target), 'playwright-ts',
                               '找不到目标 iframe：old')
        self.assertEqual(card['category'], 'frame_changed')
        self.assertTrue(card['patch']['clear_frame'])
        self.assertTrue(card['can_apply'], card)

        self.page.set_content('<div>什么都没有</div>')
        card = healer.diagnose(self.page, _step(target=target), 'playwright-ts',
                               '找不到目标 iframe：old')
        self.assertFalse(card['can_apply'])

    def test_assertion_text_mismatch_is_never_auto_fixed(self):
        """内容不一致改成「预期」等于把 bug 盖住，这条路必须堵死。"""
        target = self._recorded()
        self.page.set_content('<div id="after">登录失败</div>')
        step = _step('assert', target, {'type': 'text_contains', 'value': '登录成功'})
        card = healer.diagnose(self.page, step, 'playwright-ts', 'expect.toContainText: timeout')
        self.assertEqual(card['category'], 'assertion')
        self.assertFalse(card['can_apply'])
        self.assertNotIn('patch', card)

    def test_network_error_is_not_a_locator_problem(self):
        target = self._recorded()
        card = healer.diagnose(self.page, _step(target=target), 'playwright-ts',
                               'page.goto: net::ERR_CONNECTION_REFUSED')
        self.assertEqual(card['category'], 'env')
        self.assertFalse(card['can_apply'])

    def test_vanished_element_says_so_instead_of_inventing_a_patch(self):
        target = self._recorded()
        self.page.set_content('<div>列表空了</div>')
        card = healer.diagnose(self.page, _step(target=target), 'playwright-ts', 'Timeout')
        self.assertFalse(card['can_apply'])
        self.assertIn(card['category'], ('locator_changed', 'ambiguous'))

    def test_gives_up_after_the_attempt_budget(self):
        """同一个方向修两次还不过，就该停手，而不是无限重试。"""
        target = self._recorded()
        card = healer.diagnose(self.page, _step(target=target), 'playwright-ts', 'Timeout',
                               attempt=healer.MAX_ATTEMPT + 1)
        self.assertFalse(card['can_apply'])
        self.assertIn('已自动修复', card['reason'])

    def test_rf_mode_still_writes_a_playwright_primary(self):
        """RF 回放里修完，写回的 primary 仍必须是 PW 候选——primary 只服务 PW 那条链路。"""
        target = self._recorded()
        self.page.set_content(LOGIN_CHANGED)
        card = healer.diagnose(self.page, _step(target=target), 'robot-selenium', 'Timeout')
        self.assertTrue(card['can_apply'], card)
        self.assertIn(card['patch']['primary']['kind'], locators.PW_KINDS)
        rf_pick = locators.pick_for('robot-selenium', card['patch']['candidates'])
        self.assertIsNotNone(rf_pick, 'RF 侧应当能从刷新后的候选里挑到一条')
        self.assertIn(rf_pick['kind'], locators.RF_KINDS)

    def test_patch_does_not_touch_the_original_step(self):
        """写回是另一个决定：套补丁只能改副本。"""
        target = self._recorded()
        step = _step(target=target)
        before = (target['primary'] or {}).get('desc')
        healer.apply_patch(step, {'primary': {'kind': 'role', 'desc': 'x', 'count': 1, 'spec': {}}})
        self.assertEqual(step['target']['primary']['desc'], before)

    def test_replaced_element_is_refused_instead_of_clicked(self):
        """同 class、不同 id 和文案：这不是「定位失效」，是元素被换掉了。"""
        snap = dict(LOGIN_SNAP, name='提交', text='提交', text_flat='提交',
                    id='submit', css='#submit', class_raw='primary-action')
        target = self._recorded(snap, '<button id="submit" class="primary-action">提交</button>')
        self.page.set_content('<button id="cancel" class="primary-action">取消</button>')
        card = healer.diagnose(self.page, _step(target=target), 'playwright-ts', 'Timeout')
        self.assertEqual(card['category'], 'element_replaced')
        self.assertEqual(card['verdict'], 'conflict')
        self.assertFalse(card['can_apply'])
        self.assertNotIn('patch', card)

    def test_row_anchor_gone_never_falls_back_to_another_row(self):
        """Alice 那行没了，不能把「删除」换成 Bob 那一行的。"""
        snap = dict(LOGIN_SNAP, name='删除', text='删除', text_flat='删除',
                    id='del-alice', css='#del-alice', class_raw='row-action',
                    row_scope={'role': 'row', 'tag': 'tr', 'key': 'Alice'})
        self.page.set_content(
            '<table><tbody>'
            '<tr role="row"><td>Alice</td><td><button id="del-alice" class="row-action">删除</button></td></tr>'
            '<tr role="row"><td>Bob</td><td><button id="del-bob" class="row-action">删除</button></td></tr>'
            '</tbody></table>')
        target = _target(self.page, snap)
        inner = {'method': 'get_by_role', 'args': ['button'],
                 'kwargs': {'name': '删除', 'exact': True}}
        spec = locators.row_scoped_spec('row', 'Alice', inner)
        row_item = {'kind': 'row_scoped', 'desc': spec['desc'],
                    'count': locators.build_locator(self.page, spec).count(), 'spec': spec}
        target['candidates'].insert(0, row_item)
        target['primary'] = row_item

        self.page.set_content(
            '<table><tbody><tr role="row"><td>Bob</td><td>'
            '<button id="del-bob" class="row-action">删除</button></td></tr></tbody></table>')
        card = healer.diagnose(self.page, _step(target=target), 'playwright-ts', 'Timeout')
        self.assertEqual(card['category'], 'row_gone')
        self.assertFalse(card['can_apply'])
        self.assertNotIn('patch', card)

    def test_disabled_button_waits_instead_of_rewriting(self):
        """提交中的按钮：身份对得上，只是暂时不可点。等，不改定位。"""
        snap = dict(LOGIN_SNAP, name='提交订单', text='提交订单', text_flat='提交订单',
                    id='submit', css='#submit', class_raw='primary-action')
        target = self._recorded(snap, '<button id="submit" class="primary-action">提交订单</button>')
        self.page.set_content(
            '<button id="submit" class="primary-action" disabled>提交中…</button>')
        card = healer.diagnose(self.page, _step(target=target), 'playwright-ts', 'Timeout')
        self.assertEqual(card['category'], 'transient')
        self.assertTrue(card['can_apply'])
        self.assertTrue(card['patch'].get('retry_only'))
        self.assertEqual(card['patch'].get('wait_ms'), healer.RETRY_WAIT_MS)
        self.assertNotIn('primary', card['patch'])

    def test_visible_assertion_on_hidden_element_is_not_transient(self):
        """元素还在但藏起来了：重试不会让它变可见，不能写成「临时状态」。"""
        snap = dict(LOGIN_SNAP, tag='div', role=None, name=None, text='登录成功',
                    text_flat='登录成功', id='msg', css='#msg', class_raw='')
        target = self._recorded(snap, '<div id="msg">登录成功</div>')
        self.page.set_content('<div id="msg" style="display:none">登录成功</div>')
        step = _step('assert', target, {'type': 'visible', 'timeout': 0.5})
        card = healer.diagnose(self.page, step, 'playwright-ts', 'expect.to_be_visible: timeout')
        self.assertEqual(card['category'], 'assertion')
        self.assertFalse(card['can_apply'])

    def test_renamed_button_prefers_the_stable_id_over_class(self):
        target = self._recorded()
        self.page.set_content(
            '<button id="login" class="primary-action"><span>立即登录</span></button>')
        card = healer.diagnose(self.page, _step(target=target), 'playwright-ts', 'Timeout')
        self.assertEqual(card['verdict'], 'verified')
        self.assertEqual(card['risk'], 'low')
        self.assertIn('#login', card['patch']['primary']['desc'])

    def test_class_only_match_is_probable_and_medium_risk(self):
        """图标按钮换容器后 id 丢了，只剩 class：可以修，但要标成需留意。"""
        snap = dict(LOGIN_SNAP, name=None, text='', text_flat='',
                    id='del', css='#toolbar > button:nth-child(2)', class_raw='tool-button')
        self.page.set_content(
            '<div id="toolbar"><button id="add" class="tool-button"></button>'
            '<button id="del" class="tool-button"></button></div>')
        target = _target(self.page, snap)
        target['primary'] = next(c for c in target['candidates'] if c['kind'] == 'css')
        self.page.set_content(
            '<div class="page"><div id="toolbar">'
            '<button id="add" class="add-button"></button></div>'
            '<div class="actions"><button class="tool-button" data-name="del"></button></div></div>')
        card = healer.diagnose(self.page, _step(target=target), 'playwright-ts', 'Timeout')
        self.assertEqual(card['category'], 'locator_changed')
        self.assertTrue(card['can_apply'])
        self.assertEqual(card['verdict'], 'probable')
        self.assertEqual(card['risk'], 'medium')


class FlowTest(unittest.TestCase):
    """决定链路：失败要停住、等决定、应用要真的写回、跳过要记成 skipped。"""

    def _session(self, patcher=None, steps=None):
        steps = steps or [{'no': 1, 'type': 'click', 'target': {'alias': '登 录'},
                           '_session_no': 3}]
        return debug.DebugSession(steps, 'playwright-ts', delay=0.0,
                                  patcher=patcher, heal=patcher is not None)

    def _card(self, can_apply=True, writeback_ok=True):
        return {'no': 1, 'type': 'click', 'alias': '登 录', 'status': 'ready',
                'category': 'locator_changed', 'title': '定位失效', 'reason': 'r',
                'confidence': 0.9, 'changes': [], 'verify': {'ok': True, 'text': 'ok'},
                'risk': 'low', 'can_apply': can_apply, 'blocked_reason': None,
                'attempt': 1, 'max_attempt': 2, 'probes': [],
                'writeback_ok': writeback_ok,
                'patch': {'primary': {'kind': 'text', 'desc': '新', 'count': 1, 'spec': {}}}}

    def _run(self, session, decided, results, card=None):
        calls = {'n': 0}

        def fake_exec(context, page, step):
            calls['n'] += 1
            if calls['n'] == 1:
                raise RuntimeError('Locator.click: Timeout 30000ms exceeded.')
            results.append(dict(step))
            return page, None, None, None

        session._exec = fake_exec
        # 失败后不再自动诊断：真正的诊断发生在用户点「AI 自愈」之后。
        # 这里把官方 healer 换成一个立即返回的卡片，测的是「决定链路」而不是模型。
        with mock.patch.object(debug.llm_healer, 'investigate',
                               return_value=dict(card or self._card())):
            session._walk(None, None)
        return calls

    def _decide_later(self, session, decision, writeback, results):
        def worker():
            for _ in range(600):
                snap = session.snapshot()
                if snap['status'] == 'needs_heal':
                    card = snap.get('heal') or {}
                    state = card.get('status')
                    if state == 'idle':
                        # 第一张卡片只有事实，先让它去调查
                        session.decide_heal('ai')
                    elif state in ('ready', 'blocked'):
                        session.decide_heal(decision, writeback=writeback)
                        return
                    # queued / investigating / applying：正在调查，继续等
                time.sleep(0.02)
            results.append({'never': 'needs_heal'})

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        return thread

    def test_failure_waits_for_a_decision_instead_of_closing(self):
        """没人的时候要一直挂着——浏览器一关，现场就没了。"""
        session = self._session(patcher=lambda no, patch: None)
        thread = threading.Thread(target=self._run, args=(session, None, []), daemon=True)
        thread.start()
        for _ in range(100):
            if session.snapshot()['status'] == 'needs_heal':
                break
            time.sleep(0.02)
        self.assertEqual(session.snapshot()['status'], 'needs_heal')
        self.assertIsNotNone(session.snapshot()['heal'])
        # 补丁里带着完整候选（含 spec），不该跟着 260ms 的状态轮询反复发给前端。
        self.assertNotIn('patch', session.snapshot()['heal'])
        self.assertTrue(thread.is_alive(), '浏览器不该已经被关掉')
        session.control('stop')
        thread.join(2.0)

    def test_pause_is_refused_while_waiting(self):
        session = self._session(patcher=lambda no, patch: None)
        thread = threading.Thread(target=self._run, args=(session, None, []), daemon=True)
        thread.start()
        for _ in range(100):
            if session.snapshot()['status'] == 'needs_heal':
                break
            time.sleep(0.02)
        with self.assertRaises(debug.DebugError):
            session.control('pause')
        session.control('stop')
        thread.join(2.0)

    def test_apply_writes_back_and_retries(self):
        patch_calls = []
        results = []
        session = self._session(patcher=lambda no, patch: patch_calls.append((no, patch)))
        worker = self._decide_later(session, 'apply', True, results)
        self._run(session, None, results)
        worker.join(1.0)
        self.assertEqual(patch_calls and patch_calls[0][0], 3, '写回要用录制的步骤编号')
        self.assertEqual(len(results), 1, '重试应当真的再执行一次')
        self.assertEqual(results[0]['target']['primary']['desc'], '新')
        self.assertEqual(session.snapshot()['status'], 'done')
        self.assertTrue(session.snapshot()['steps'][0]['healed'])

    def test_apply_without_writeback_keeps_recording_untouched(self):
        patch_calls = []
        results = []
        session = self._session(patcher=lambda no, patch: patch_calls.append(no))
        worker = self._decide_later(session, 'apply', False, results)
        self._run(session, None, results)
        worker.join(1.0)
        self.assertEqual(patch_calls, [], '没勾写回就不该动录制数据')
        self.assertEqual(results[0]['target']['primary']['desc'], '新')

    def test_skip_marks_the_step_as_skipped(self):
        results = []
        session = self._session(patcher=lambda no, patch: None)
        worker = self._decide_later(session, 'skip', False, results)
        self._run(session, None, results)
        worker.join(1.0)
        snap = session.snapshot()
        self.assertEqual(snap['steps'][0]['status'], 'skipped')
        self.assertEqual(snap['status'], 'done')
        self.assertIsNone(snap['heal'])

    def test_stop_ends_the_run_as_failed(self):
        results = []
        session = self._session(patcher=lambda no, patch: None)
        worker = self._decide_later(session, 'stop', False, results)
        self._run(session, None, results)
        worker.join(1.0)
        snap = session.snapshot()
        self.assertEqual(snap['status'], 'failed')
        self.assertEqual(snap['failed_no'], 1)

    def test_blocked_card_cannot_be_applied(self):
        session = self._session(patcher=lambda no, patch: None)
        session._state['status'] = 'needs_heal'
        session._state['heal'] = dict(self._card(can_apply=False), patch=None)
        with self.assertRaises(debug.DebugError):
            session.decide_heal('apply')

    def test_wrong_element_is_flagged_even_when_the_step_passes(self):
        """1b：结构定位漂移，点击照样成功——步骤是绿的，但点到了别的按钮。

        这种情况 healer 没机会介入（没有失败），只能靠执行前的身份核对把它标出来。
        """
        other = {'tag': 'button', 'id': 'refresh', 'testid': '', 'aria': '',
                 'name_attr': '', 'text': '刷新', 'row_text': '', 'class_raw': '',
                 'owner': None, 'visible': True, 'enabled': True}
        results = []

        def fake_exec(context, page, step):
            results.append(dict(step))
            return page, None, None, other

        session = self._session(steps=[{
            'no': 1, 'type': 'click',
            'target': {'alias': '删除', 'snap': {'tag': 'button', 'id': 'del', 'text': '删除'}},
        }])
        session._exec = fake_exec
        session._walk(None, None)
        snap = session.snapshot()
        self.assertEqual(snap['status'], 'done')
        self.assertTrue(snap['steps'][0]['suspect'])
        self.assertIn('refresh', snap['steps'][0]['suspect_reason'])

    def test_no_healer_keeps_the_old_behaviour(self):
        """不接 patcher 时行为必须和接入前一致：失败即停。"""
        session = debug.DebugSession(
            [{'no': 1, 'type': 'click', 'target': {'alias': 'x'}}],
            'playwright-ts', delay=0.0)

        def boom(context, page, step):
            raise RuntimeError('点不中')

        session._exec = boom
        session._walk(None, None)
        snap = session.snapshot()
        self.assertEqual(snap['status'], 'failed')
        self.assertIsNone(snap['heal'])


class PatchTargetTest(unittest.TestCase):
    """写回录制步骤：改完两个框架都要跟着改口，而且必须能撤。"""

    def _session(self):
        from app.services.recording import ir
        from app.services.recording.session import RecordingSession
        session = RecordingSession('http://example.test/')
        snap = {'tag': 'button', 'name': '登 录', 'text': '登 录', 'role': 'button',
                'css': '#login', 'frame_url': 'https://old/frame', 'frame_name': 'old'}
        candidates = [
            {'kind': 'role', 'desc': "getByRole('button')", 'count': 1,
             'spec': {'method': 'get_by_role', 'args': ['button'], 'kwargs': {}}},
            {'kind': 'rf_css', 'desc': 'css=#login', 'count': 1,
             'spec': {'method': 'locator', 'args': ['#login'], 'kwargs': {}}},
        ]
        primary = dict(candidates[0])
        session._steps = [ir.make_step('click', no=3,
                                       target=ir.make_target(snap, candidates, primary, 'unique'))]
        return session

    @staticmethod
    def _patch():
        return {
            'primary': {'kind': 'text', 'desc': "getByText('登 录', { exact: true })", 'count': 1,
                        'spec': {'method': 'get_by_text', 'args': ['登 录'], 'kwargs': {'exact': True}}},
            'candidates': [
                {'kind': 'text', 'desc': "getByText('登 录', { exact: true })", 'count': 1,
                 'spec': {'method': 'get_by_text', 'args': ['登 录'], 'kwargs': {'exact': True}}},
                {'kind': 'rf_xpath', 'desc': "rf xpath://button[normalize-space(.)='登 录']",
                 'count': 1,
                 'spec': {'method': 'locator',
                          'args': ["xpath=//button[normalize-space(.)='登 录']"], 'kwargs': {}}},
            ],
            'clear_frame': True,
        }

    def test_both_frameworks_switch_to_the_patched_locator(self):
        from app.services.recording import replay
        session = self._session()
        session.patch_target(3, self._patch())
        target = session.ir()[0]['target']
        self.assertIn("getByText('登 录'", replay.choose_locator(target, 'playwright-ts')['desc'])
        self.assertEqual(replay.choose_locator(target, 'robot-selenium')['desc'],
                         "rf xpath://button[normalize-space(.)='登 录']")

    def test_frame_metadata_is_cleared(self):
        session = self._session()
        session.patch_target(3, self._patch())
        snap = session.ir()[0]['target']['snap']
        self.assertIsNone(snap['frame_url'])
        self.assertIsNone(snap['frame_name'])

    def test_undo_restores_the_previous_target(self):
        session = self._session()
        before = session.ir()[0]['target']['primary']['desc']
        session.patch_target(3, self._patch())
        self.assertNotEqual(session.ir()[0]['target']['primary']['desc'], before)
        session.undo_heal(3)
        self.assertEqual(session.ir()[0]['target']['primary']['desc'], before)
        self.assertEqual(session.ir()[0]['target']['snap']['frame_url'], 'https://old/frame')

    def test_unknown_patch_keys_are_rejected(self):
        """这个入口只该被 healer 用，不能让别的字段偷偷进来。"""
        from app.services.recording.session import RecordingError
        session = self._session()
        with self.assertRaises(RecordingError):
            session.patch_target(3, {'value': '删掉这条断言'})

    def test_step_without_target_is_rejected(self):
        from app.services.recording import ir
        from app.services.recording.session import RecordingError
        session = self._session()
        session._steps = [ir.make_step('goto', no=1, url='http://example.test/')]
        with self.assertRaises(RecordingError):
            session.patch_target(1, self._patch())


if __name__ == '__main__':
    unittest.main()
