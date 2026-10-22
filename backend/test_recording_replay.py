import http.server
import threading
import unittest

from app.services.recording.emit import _locator_info
from app.services.recording.replay import ReplayError, replay, replay_checked
from app.services.recording.session import RecordingSession

PAGES = {
    '/index.html': (
        '<!doctype html><html><head><meta charset="utf-8"></head><body>'
        '<h1>录制验证页</h1>'
        '<input placeholder="请输入关键词">'
        '<select name="city"><option value="bj">北京</option><option value="sh">上海</option></select>'
        '<button onclick="window.__submitted=true">提交</button>'
        '<button onclick="window.__picked=true">保存</button>'
        '<button onclick="window.__picked=true">保存</button>'
        '<a href="/newtab.html" target="_blank">打开新页</a>'
        '<iframe src="/frame.html" width="400" height="120"></iframe>'
        '</body></html>'
    ),
    '/frame.html': (
        '<!doctype html><html><head><meta charset="utf-8"></head><body>'
        '<input placeholder="帧内输入"><button>帧内按钮</button>'
        '</body></html>'
    ),
    '/newtab.html': (
        '<!doctype html><html><head><meta charset="utf-8"></head><body>'
        '<button>新页按钮</button>'
        '</body></html>'
    ),
}


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAGES.get(self.path.split('?')[0])
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(body.encode('utf-8'))

    def log_message(self, *args):
        pass


def _scenario(session, page):
    # 与 spike 相同的一段操作：输入、下拉、点击、iframe、拾取（不记录）、新标签页。
    # 测试驱动和真实循环一样需要定期处理收件箱，否则弹窗的 binding 来不及绑定。
    def pump(ms=200):
        page.wait_for_timeout(ms)
        session._process_inbox()

    page.get_by_placeholder('请输入关键词').fill('hello')
    pump()
    page.select_option('select[name=city]', label='上海')
    pump()
    page.get_by_role('button', name='提交').click()
    pump()
    page.frame_locator('iframe').get_by_role('button', name='帧内按钮').click()
    pump()
    session._set_pick(True)
    page.locator('button', has_text='保存').nth(1).click()
    pump()
    session._set_pick(False)
    with page.expect_popup() as info:
        page.get_by_text('打开新页', exact=True).click()
    popup = info.value
    session._process_inbox()
    popup.wait_for_load_state()
    session._process_inbox()
    popup.get_by_role('button', name='新页按钮').click()
    pump(500)


class ReplayScenarioTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = 'http://127.0.0.1:%d/index.html' % cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        RecordingSession._current = None

    def tearDown(self):
        # 停止后浏览器仍开着（为了停止后还能拾取、验证），每个用例结束要收掉。
        RecordingSession.dispose_current()

    def _record(self):
        session = RecordingSession.start_new(self.base, headless=True, driver=_scenario)
        # 追加在末尾的断言会落在「最后一步所在的标签页」——这里流程最后停在新标签页，
        # 所以断言的目标必须是新页面上的元素（以前固定写 page 0，等于在主页面里找）。
        session.add_intent_step({'type': 'visible', 'target': '新页按钮', 'value': None, 'seconds': None},
                                text='新页按钮可见')
        session.add_intent_step({'type': 'url_contains', 'target': None, 'value': 'newtab.html', 'seconds': None},
                                text='地址包含 newtab.html')
        session.stop()
        return session

    def test_recorded_steps_match_the_scenario(self):
        session = self._record()
        types = [s['type'] for s in session.ir()]
        self.assertEqual(types, ['goto', 'fill', 'select', 'click', 'click', 'click', 'new_page', 'click',
                                 'assert', 'assert'])
        aliases = [s['target']['alias'] for s in session.ir() if s.get('target')]
        self.assertNotIn('保存', aliases, 'pick clicks must not become steps')
        iframe_clicks = [s for s in session.ir() if s.get('target') and s['target']['snap'].get('frame_url')]
        self.assertEqual(len(iframe_clicks), 1)

    def test_replay_of_recorded_ir_passes(self):
        session = self._record()
        self.assertEqual(replay(session.ir(), headless=True), 10)

    def test_replay_fails_when_an_assertion_is_wrong(self):
        session = self._record()
        steps = session.ir()
        steps[-2]['intent'] = {'type': 'text_equals', 'target': '新页按钮', 'value': '并不存在的文字',
                               'seconds': None}
        with self.assertRaises(ReplayError) as ctx:
            replay(steps, headless=True)
        self.assertIn('第 9 步', str(ctx.exception))

    def test_replay_report_uses_rf_locators_for_robot_framework(self):
        # RF 的候选本来就是用 Playwright 的 xpath= / css= 计的，所以不需要 Selenium 也能校验。
        # 这条守的是「RF 回放必须真的换成 rf_* 候选」，而不是偷用 PW 主定位假装通过。
        session = self._record()
        report = replay_checked(session.ir(), framework='robot-selenium', headless=True)
        self.assertTrue(report['ok'], report['error'])
        self.assertEqual(report['done'], 10)
        used = [s['used'] for s in report['steps'] if s['used']]
        self.assertTrue(used, '报告里应该有带定位的步骤')
        self.assertTrue(all(u['kind'].startswith('rf_') for u in used),
                        'RF 回放用了非 RF 候选：%s' % [u['kind'] for u in used])
        # 提示词里给的定位和回放实际用的必须一致，否则「校验通过」不等于「生成的脚本能跑」。
        for step, entry in zip(session.ir(), report['steps']):
            info = step.get('target') and _locator_info(step['target'], 'robot-selenium')
            if info and entry['used']:
                self.assertEqual(entry['used']['syntax'], info['locator'])

    def test_replay_report_keeps_successful_steps_before_failure(self):
        session = self._record()
        steps = session.ir()
        steps[-1]['intent'] = {'type': 'text_equals', 'target': '新页按钮', 'value': '不存在的文字',
                               'seconds': None}
        report = replay_checked(steps, headless=True)
        self.assertFalse(report['ok'])
        self.assertEqual(report['failed_no'], 10)
        self.assertEqual(report['total'], 10)
        # 失败之前的步骤要保留，前端才能显示「前 9 步走通、第 10 步失效」
        self.assertEqual(len([s for s in report['steps'] if s['ok']]), 9)
        self.assertEqual(report['steps'][-1]['ok'], False)


    def test_replay_supports_page_text_assertion(self):
        # 整页文字断言没有目标元素，回放里也要能跑（body 文字包含即可）。
        session = self._record()
        session.add_intent_step({'type': 'page_text_contains', 'target': None, 'value': '新页按钮',
                                 'seconds': None}, text='新页面包含按钮文字')
        steps = session.ir()
        # 断言落在最后一步所在的标签页（新标签页），不是主页面。
        self.assertEqual(steps[-1]['page_index'], 1)
        self.assertEqual(replay(steps, headless=True), 11)
        steps[-1]['intent'] = {'type': 'page_text_contains', 'target': None, 'value': '并不存在的文字',
                               'seconds': None}
        with self.assertRaises(ReplayError):
            replay(steps, headless=True)


if __name__ == '__main__':
    unittest.main()
