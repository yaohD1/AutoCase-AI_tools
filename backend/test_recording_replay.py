import http.server
import threading
import unittest

from app.services.recording.replay import ReplayError, replay
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

    def _record(self):
        session = RecordingSession.start_new(self.base, headless=True, driver=_scenario)
        session.add_intent_step({'type': 'visible', 'target': '提交', 'value': None, 'seconds': None},
                                text='提交按钮可见')
        session.add_intent_step({'type': 'url_contains', 'target': None, 'value': 'index.html', 'seconds': None},
                                text='地址包含 index.html')
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
        steps[-2]['intent'] = {'type': 'text_equals', 'target': '提交', 'value': '并不存在的文字',
                               'seconds': None}
        with self.assertRaises(ReplayError) as ctx:
            replay(steps, headless=True)
        self.assertIn('第 9 步', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
