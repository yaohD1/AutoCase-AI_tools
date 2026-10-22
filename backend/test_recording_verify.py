"""插入前验证：「当前页面状态」与「停止后仍可拾取」的真实浏览器用例。

这两件事互相支撑：断言要能在插入前验证，就必须有一个活着的页面；
而停止录制如果直接关浏览器，页面就没了。所以这里把两者放在一起测。
"""
import http.server
import threading
import unittest

from app.services.recording.session import RecordingSession

PAGE = (
    '<!doctype html><html><head><meta charset="utf-8"></head><body>'
    '<h1>当前页</h1>'
    '<p>登录成功</p>'
    '<button>提交</button>'
    '<button>取消</button>'
    '<input placeholder="关键词">'
    '</body></html>'
)

# 两行各有一个「删除」：同名不同元素，别名不许合并（合并就会静默指错行）。
TABLE_PAGE = (
    '<!doctype html><html><head><meta charset="utf-8"></head><body>'
    '<table><tbody>'
    '<tr><td>第一行</td><td><button>删除</button></td></tr>'
    '<tr><td>第二行</td><td><button>删除</button></td></tr>'
    '</tbody></table>'
    '</body></html>'
)


# 先展开才点得到的元素：普通点击会被拾取模式拦掉，Alt+点击必须放行。
MENU_PAGE = (
    '<!doctype html><html><head><meta charset="utf-8"></head><body>'
    '<button id="more">更多</button>'
    '<div id="menu" style="display:none"><button>导出</button></div>'
    '<script>document.getElementById("more").addEventListener("click", function () {'
    ' document.getElementById("menu").style.display = "block"; });</script>'
    '</body></html>'
)


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith('/table'):
            body = TABLE_PAGE
        elif self.path.startswith('/menu'):
            body = MENU_PAGE
        else:
            body = PAGE
        body = body.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _scenario(session, page):
    # 把 page 交给测试：后面的「停止后再点击 / 再拾取」必须在录制线程里执行，
    # Playwright 的同步对象不能跨线程用。
    session._test_page = page
    page.get_by_placeholder('关键词').fill('已有内容')
    page.wait_for_timeout(150)
    session._process_inbox()
    page.get_by_role('button', name='提交').click()
    page.wait_for_timeout(200)
    session._process_inbox()


def _table_scenario(session, page):
    session._test_page = page
    page.get_by_role('button', name='删除').first.click()
    page.wait_for_timeout(150)
    session._process_inbox()


def _idle_scenario(session, page):
    session._test_page = page


def _in_recording_thread(session, fn, timeout=15):
    """把一段页面操作排进录制线程执行（inbox 由 _loop 消费）。"""
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
    deadline = threading.Event()
    step = 0.05
    waited = 0.0
    while waited < timeout:
        if predicate():
            return True
        deadline.wait(step)
        waited += step
    return predicate()


def _intent(kind, target=None, value=None):
    return {'type': kind, 'target': target, 'value': value, 'seconds': None}


class VerifyOnLivePageTest(unittest.TestCase):
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

    def _record(self):
        return RecordingSession.start_new(self.base, headless=True, driver=_scenario)

    def test_text_contains_reports_the_actual_text(self):
        session = self._record()
        hit = session.verify_intent(_intent('text_contains', '提交', '提交'))
        self.assertTrue(hit['ok'], hit)
        self.assertEqual(hit['actual'], '提交')
        miss = session.verify_intent(_intent('text_contains', '提交', '并不存在'))
        self.assertFalse(miss['ok'])
        # 实际值必须给出来，否则用户没法判断这条断言该怎么写
        self.assertEqual(miss['actual'], '提交')

    def test_visibility_and_value_are_read_from_the_page(self):
        session = self._record()
        self.assertTrue(session.verify_intent(_intent('visible', '提交'))['ok'])
        hidden = session.verify_intent(_intent('hidden', '提交'))
        self.assertFalse(hidden['ok'])
        self.assertEqual(hidden['actual'], '可见')
        value = session.verify_intent(_intent('value_equals', '关键词', '已有内容'))
        self.assertTrue(value['ok'], value)
        self.assertEqual(value['actual'], '已有内容')

    def test_page_text_assertion_needs_no_target(self):
        session = self._record()
        self.assertTrue(session.verify_intent(_intent('page_text_contains', None, '登录成功'))['ok'])
        missing = session.verify_intent(_intent('page_text_contains', None, '查无此文'))
        self.assertFalse(missing['ok'])
        self.assertIn('没有这段内容', missing['detail'])

    def test_verify_still_works_after_stop(self):
        session = self._record()
        session.stop()
        hit = session.verify_intent(_intent('text_contains', '提交', '提交'))
        self.assertTrue(hit['ok'], hit)

    def test_clicks_after_stop_are_not_recorded_but_picks_are(self):
        session = self._record()
        before = len(session.ir())
        alias_before = session.aliases()
        session.stop()
        # 停止后普通点击不该变成步骤……
        _in_recording_thread(session, lambda: session._test_page.get_by_role('button', name='取消').click())
        self.assertEqual(len(session.ir()), before)
        # ……但拾取仍然可以，因为断言要用它。
        session.set_pick(True)
        _in_recording_thread(session, lambda: session._test_page.get_by_text('登录成功').click())
        self.assertTrue(_wait_for(lambda: len(session.aliases()) > len(alias_before)), session.aliases())
        session.set_pick(False)
        new_alias = session.aliases()[-1]
        self.assertEqual(len(session.ir()), before)
        hit = session.verify_intent(_intent('text_contains', new_alias, '登录成功'))
        self.assertTrue(hit['ok'], hit)


class AliasOnLivePageTest(unittest.TestCase):
    """真实浏览器上同名元素的别名：录制一个「删除」、再拾取另一个「删除」。"""

    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = 'http://127.0.0.1:%d/table.html' % cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        RecordingSession._current = None

    def tearDown(self):
        RecordingSession.dispose_current()

    def _record(self):
        return RecordingSession.start_new(self.base, headless=True, driver=_table_scenario)

    def _pick_second(self, session):
        session.set_pick(True)
        try:
            _in_recording_thread(
                session, lambda: session._test_page.get_by_role('button', name='删除').nth(1).click())
            self.assertTrue(_wait_for(lambda: len(session.aliases()) == 2), session.aliases())
        finally:
            session.set_pick(False)

    def test_two_rows_named_delete_get_two_aliases(self):
        session = self._record()
        self.assertEqual(session.aliases(), ['删除'])
        self._pick_second(session)
        self.assertEqual(session.aliases(), ['删除', '删除#2'])

    def test_each_alias_points_at_its_own_row(self):
        session = self._record()
        self._pick_second(session)
        first = session._find_target('删除')
        second = session._find_target('删除#2')
        self.assertIn('tr:nth-of-type(1)', first['snap']['css'])
        self.assertIn('tr:nth-of-type(2)', second['snap']['css'])
        # 隐藏第一行的按钮：如果两个别名指的是同一个元素，这里会一起变
        _in_recording_thread(
            session,
            lambda: session._test_page.evaluate("document.querySelectorAll('button')[0].style.display='none'"))
        self.assertTrue(session.verify_intent(_intent('visible', '删除#2'))['ok'])
        self.assertFalse(session.verify_intent(_intent('visible', '删除'))['ok'])


class PickPassthroughTest(unittest.TestCase):
    """拾取模式默认拦掉点击（防止误点「删除」），Alt+点击放行（用于展开菜单）。"""

    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = 'http://127.0.0.1:%d/menu.html' % cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        RecordingSession._current = None

    def tearDown(self):
        RecordingSession.dispose_current()

    def test_plain_click_is_blocked_and_alt_click_passes_through(self):
        session = RecordingSession.start_new(self.base, headless=True, driver=_idle_scenario)
        session.set_pick(True)
        try:
            _in_recording_thread(session, lambda: session._test_page.click('#more'))
            self.assertTrue(_wait_for(lambda: len(session.aliases()) == 1), session.aliases())
            self.assertFalse(
                _in_recording_thread(session, lambda: session._test_page.is_visible('#menu')),
                '默认点击应当被拦掉，菜单不该展开')
            # Alt+点击：照样拾取，同时让页面真的把菜单展开——否则菜单里的元素根本拾取不到
            _in_recording_thread(session, lambda: session._test_page.click('#more', modifiers=['Alt']))
            self.assertTrue(
                _in_recording_thread(session, lambda: session._test_page.is_visible('#menu')),
                'Alt+点击应当放行默认行为')
        finally:
            session.set_pick(False)


if __name__ == '__main__':
    unittest.main()