"""导航与刷新的录制规则（真实浏览器）。

刷新不做自动检测，只由工具栏的「刷新页面」显式添加（自动检测分不清 SPA 启动时的
同文档导航和用户真的刷了，会多记）。这里守的是：
1. 用户按 F5 不该悄悄多一步，也不该退化成一条「打开页面」；
2. 按钮插的那一步要真的刷新页面，且只插一条；
3. 点击引起的跳转不额外记 goto，换地址的跳转照记；
4. SPA 会把一次导航上报多次，不能变成多条步骤。
"""
import http.server
import threading
import unittest

from app.services.recording.session import RecordingSession

INDEX_PAGE = (
    '<!doctype html><html><head><meta charset="utf-8"></head><body>'
    '<h1>首页</h1>'
    '<div id="marker"></div>'
    '<a href="/second.html">跳转到第二页</a>'
    '<button id="submit">提交</button>'
    '<script>document.getElementById("marker").textContent = String(Date.now()) + "-" '
    '+ Math.random().toString(36).slice(2);</script>'
    '</body></html>'
)

SECOND_PAGE = (
    '<!doctype html><html><head><meta charset="utf-8"></head><body>'
    '<h1>第二页</h1></body></html>'
)

# 模拟 SPA：启动时用 replaceState 改地址（Vue Router / React Router 都会这么做）。
# 每次 same-document 导航 Playwright 都会再发一个 framenavigated，但 performance 里
# 那条 navigation entry 还是首次加载的 —— 于是「刷一次」会被上报三遍。
SPA_PAGE = (
    '<!doctype html><html><head><meta charset="utf-8"></head><body>'
    '<h1>单页应用</h1>'
    '<script>'
    'history.replaceState({}, "", "/spa.html?a=1");'
    'setTimeout(function () { history.replaceState({}, "", "/spa.html?a=2"); }, 120);'
    '</script></body></html>'
)


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith('/spa'):
            body = SPA_PAGE
        elif self.path.startswith('/second'):
            body = SECOND_PAGE
        else:
            body = INDEX_PAGE
        body = body.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _idle_scenario(session, page):
    # 页面操作必须在录制线程里做：Playwright 的同步对象不能跨线程。
    session._test_page = page


def _in_recording_thread(session, fn, timeout=15):
    done = threading.Event()
    box = {}

    def task():
        try:
            box['value'] = fn()
        except Exception as exc:  # noqa: BLE001 - 交回测试线程再抛
            box['error'] = exc
        finally:
            done.set()

    session._inbox.put(task)
    if not done.wait(timeout):
        raise AssertionError('录制线程没有执行排入的操作')
    if 'error' in box:
        raise box['error']
    return box.get('value')


def _wait_for(predicate, timeout=10):
    import time
    waited = 0.0
    while waited < timeout:
        if predicate():
            return True
        time.sleep(0.05)
        waited += 0.05
    return predicate()


class NavigationRecordingTest(unittest.TestCase):
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
        RecordingSession.dispose_current()

    def _types(self, session):
        return [step['type'] for step in session.ir()]

    def _record(self):
        session = RecordingSession.start_new(self.base, headless=True, ask_daemon=False, driver=_idle_scenario)
        self.assertEqual(self._types(session), ['goto'])
        return session

    def test_click_does_not_add_a_goto(self):
        session = self._record()
        _in_recording_thread(session, lambda: session._test_page.get_by_role('link').click())
        self.assertTrue(_wait_for(lambda: len(session.ir()) == 2), self._types(session))
        # 点击引起的跳转由脚本里的点击自然完成，不该再补一条 goto
        self.assertEqual(self._types(session), ['goto', 'click'])

    def test_reload_is_not_recorded_automatically(self):
        """刷新只由工具栏按钮显式添加：用户按 F5 只改变浏览器状态，不该悄悄多一步。"""
        session = self._record()
        _in_recording_thread(session, lambda: session._test_page.reload())
        import time
        time.sleep(1.0)
        self.assertEqual(self._types(session), ['goto'])

    def test_reload_after_a_click_is_not_recorded_either(self):
        session = self._record()
        _in_recording_thread(session, lambda: session._test_page.click('#submit'))
        self.assertTrue(_wait_for(lambda: self._types(session) == ['goto', 'click']), self._types(session))
        _in_recording_thread(session, lambda: session._test_page.reload())
        import time
        time.sleep(1.0)
        self.assertEqual(self._types(session), ['goto', 'click'])

    def test_goto_to_another_url_is_still_recorded(self):
        session = self._record()
        second = self.base.replace('index.html', 'second.html')
        _in_recording_thread(session, lambda: session._test_page.goto(second))
        self.assertTrue(
            _wait_for(lambda: self._types(session) == ['goto', 'goto']), self._types(session))
        self.assertTrue(session.ir()[-1]['url'].endswith('second.html'))

    def test_manual_reload_reloads_the_page_and_records_one_step(self):
        session = self._record()
        before = _in_recording_thread(session, lambda: session._test_page.inner_text('#marker'))
        session.add_reload_step()
        after = _in_recording_thread(session, lambda: session._test_page.inner_text('#marker'))
        self.assertNotEqual(before, after, '按钮应当真的刷新一次，让后续步骤落在刷新后的页面上')
        # 按钮自己刷出来的那次导航不能退化成第二条步骤
        self.assertTrue(_wait_for(lambda: self._types(session) == ['goto', 'reload']), self._types(session))
        import time
        time.sleep(0.3)
        self.assertEqual(self._types(session), ['goto', 'reload'])

    def test_manual_reload_after_a_step_lands_right_after_it(self):
        session = self._record()
        _in_recording_thread(session, lambda: session._test_page.click('#submit'))
        self.assertTrue(_wait_for(lambda: self._types(session) == ['goto', 'click']), self._types(session))
        session.add_reload_step(after_no=1)
        self.assertEqual(self._types(session), ['goto', 'reload', 'click'])
        self.assertEqual([step['no'] for step in session.ir()], [1, 2, 3])

    def test_reload_after_stop_is_not_recorded_but_the_step_can_be_added(self):
        session = self._record()
        session.stop()
        _in_recording_thread(session, lambda: session._test_page.reload())
        self.assertTrue(_wait_for(lambda: len(session.ir()) == 1, timeout=2), self._types(session))
        # 停止后插这一步只是编辑列表，不该再去动浏览器
        session.add_reload_step()
        self.assertEqual(self._types(session), ['goto', 'reload'])


class SpaNavigationTest(unittest.TestCase):
    """SPA 会把一次导航上报成多次：同一个 document 的 pushState 各触发一次事件。"""

    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = 'http://127.0.0.1:%d/spa.html' % cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        RecordingSession._current = None

    def tearDown(self):
        RecordingSession.dispose_current()

    def _types(self, session):
        return [step['type'] for step in session.ir()]

    def _record(self):
        """打开 SPA 页，并记录期间收到过几次导航事件（用来确认测试真的踩到了重复上报）。"""
        session = RecordingSession.start_new(self.base, headless=True, ask_daemon=False, driver=_idle_scenario)
        seen = []
        original = session._handle_navigated
        session._handle_navigated = lambda page, frame: (seen.append(frame.url), original(page, frame))[1]
        # 页面启动时的 replaceState 是异步的，等它们都到齐
        import time
        time.sleep(0.8)
        return session, seen

    def test_startup_same_document_navigations_are_one_step(self):
        session, seen = self._record()
        self.assertEqual(self._types(session), ['goto'])

    def test_one_reload_is_recorded_once(self):
        """刷新已改成只由按钮添加，所以这里一条都不该多：重复上报更不能变成多条。"""
        session, seen = self._record()
        seen.clear()
        _in_recording_thread(session, lambda: session._test_page.reload())
        import time
        time.sleep(1.2)
        # 刷新后页面又把 replaceState 跑了一遍：事件不止一条，但步骤不能多
        self.assertGreaterEqual(len(seen), 2, '刷新应当触发多次导航事件：%s' % seen)
        self.assertEqual(self._types(session), ['goto'])

    def test_manual_reload_button_adds_exactly_one_step(self):
        session, _ = self._record()
        session.add_reload_step()
        self.assertEqual(self._types(session), ['goto', 'reload'])
        import time
        time.sleep(1.0)
        self.assertEqual(self._types(session), ['goto', 'reload'])


if __name__ == '__main__':
    unittest.main()
