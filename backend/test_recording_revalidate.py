"""停止录制时的定位复核：只在「录制时那个 document」上重算。

登录页 → 登录成功后聊天页是最常见的场景。停止时页面已经翻走，如果还拿聊天页去数
登录页的输入框，所有 count 都会变成 0，界面把「页面翻走了」误报成「不唯一」，
生成端也会把它当成元素已消失而拒绝。
"""
import http.server
import threading
import time
import unittest

from app.services.recording import emit
from app.services.recording.session import RecordingSession

LOGIN_PAGE = '''<!doctype html><html><head><meta charset="utf-8"></head><body>
<h1>登录</h1>
<input placeholder="用户名">
<button onclick="location.href='/app'">登录</button>
</body></html>'''

APP_PAGE = '''<!doctype html><html><head><meta charset="utf-8"></head><body>
<h1>聊天</h1>
<textarea placeholder="聊天输入"></textarea>
</body></html>'''

PAGES = {'/login': LOGIN_PAGE, '/app': APP_PAGE}


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAGES.get(self.path.split('?')[0], LOGIN_PAGE).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _in_thread(session, fn, timeout=20):
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
        raise RuntimeError('录制线程超时')
    if 'error' in box:
        raise box['error']
    return box['value']


def _wait_for(check, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if check():
            return True
        time.sleep(0.05)
    return check()


def _idle_scenario(session, page):
    session._test_page = page


class _Frame:
    def __init__(self, url):
        self.url = url


class SamePageTest(unittest.TestCase):
    """页面判定：URL 优先（SPA 换 URL 也算翻页），timeOrigin 只在没有 URL 时兜底。"""

    def setUp(self):
        self.session = RecordingSession.__new__(RecordingSession)
        self.frame = _Frame('https://app.test/login')
        self.session._doc_stamp = {id(self.frame): 100.0}

    def test_legacy_target_without_doc_still_revalidates(self):
        self.assertTrue(self.session._same_page(None, self.frame))
        self.assertTrue(self.session._same_page({}, self.frame))

    def test_different_url_is_page_gone_even_in_the_same_document(self):
        # SPA 的 pushState 换了 URL，timeOrigin 不变，但页面内容已经换了：
        # 不能因为 timeOrigin 相同就拿新 DOM 去数旧元素。
        doc = {'url': 'https://app.test/login', 'stamp': 100.0}
        self.frame.url = 'https://app.test/global'
        self.assertFalse(self.session._same_page(doc, self.frame))

    def test_same_url_after_reload_still_revalidates(self):
        # 刷新换了 document，但地址没变，元素大概率还在，值得重算。
        self.assertTrue(self.session._same_page(
            {'url': 'https://app.test/login', 'stamp': 99.0}, self.frame))

    def test_same_url_with_different_stamp_is_not_treated_as_page_gone(self):
        # 同地址刷新：timeOrigin 变了也要重算，不能误判成翻页。
        self.frame.url = 'https://app.test/login?step=2'
        self.assertTrue(self.session._same_page(
            {'url': 'https://app.test/login?step=2', 'stamp': 99.0}, self.frame))

    def test_no_url_falls_back_to_the_document_stamp(self):
        self.assertTrue(self.session._same_page({'stamp': 100.0}, self.frame))
        self.session._doc_stamp[id(self.frame)] = 101.0
        self.assertFalse(self.session._same_page({'stamp': 100.0}, self.frame))


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = 'http://127.0.0.1:%d' % cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        RecordingSession._current = None

    def tearDown(self):
        RecordingSession.dispose_current()


class PageGoneTest(_Base):
    def test_previous_page_steps_keep_recorded_counts_and_are_not_blocked(self):
        session = RecordingSession.start_new(
            self.base + '/login', headless=True, ask_daemon=False, driver=_idle_scenario)
        page = session._test_page

        _in_thread(session, lambda: page.get_by_placeholder('用户名').fill('alice'))
        self.assertTrue(_wait_for(
            lambda: any(s['type'] == 'fill' for s in session.snapshot()['steps'])))
        _in_thread(session, lambda: page.get_by_role('button', name='登录').click())
        _in_thread(session, lambda: page.wait_for_url('**/app'))
        _in_thread(session, lambda: page.get_by_placeholder('聊天输入').fill('hello'))

        def ready():
            types = [s['type'] for s in session.snapshot()['steps']]
            return types.count('fill') >= 2 and 'click' in types

        self.assertTrue(_wait_for(ready), '步骤没有落库')
        time.sleep(0.2)

        after = session.stop()
        login_fill = next(s for s in after['steps'] if s['type'] == 'fill' and s['value'] == 'alice')
        chat_fill = next(s for s in after['steps'] if s['type'] == 'fill' and s['value'] == 'hello')
        login_target = login_fill['target']
        chat_target = chat_fill['target']

        # 登录页的输入框：不能拿聊天页重算成 0，录制时的唯一状态和候选要保留。
        self.assertTrue(login_target.get('page_gone'))
        self.assertEqual(login_target['status'], 'unique')
        self.assertEqual(login_target['primary']['count'], 1)
        placeholder = next(c for c in login_target['candidates'] if c['kind'] == 'placeholder')
        self.assertEqual(placeholder['count'], 1)

        # 当前页（聊天页）的步骤照常复核，不能因为修了翻页逻辑就不重算了。
        self.assertFalse(chat_target.get('page_gone'))
        self.assertEqual(chat_target['primary']['count'], 1)

        # 生成端：翻页的步骤仍用录制时定位，不会被当成「元素已消失」拦下。
        ir_steps = session.ir()
        blocked = [s['no'] for s in ir_steps
                   if s.get('target') and emit._locator_info(s['target'], 'playwright-ts') is None]
        self.assertEqual(blocked, [])
        login_ir = next(s for s in ir_steps if s['type'] == 'fill' and s['value'] == 'alice')
        info = emit._locator_info(login_ir['target'], 'playwright-ts')
        self.assertIsNotNone(info)
        self.assertEqual(info['mode'], 'unique')


if __name__ == '__main__':
    unittest.main()
