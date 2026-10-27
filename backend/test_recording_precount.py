"""pointerdown 预计数：点击自己把元素带走时，click 回来再数只能数到 0。

下拉项、菜单按钮、选中态都是这一类：点完就没了。按下那一刻元素还在，
这时候数出来的候选才是用户点的时候页面的真实情况。这里用真实浏览器验证：
按住 400ms（足够录制线程跑完预计数）再松开，步骤不能落到「未核实」。
"""
import http.server
import threading
import time
import unittest

from app.services.recording.session import RecordingSession

GONE_PAGE = '''<!doctype html><html><head><meta charset="utf-8"></head><body style="margin:0">
<button id="gone" style="position:absolute;left:40px;top:40px;width:180px;height:60px"
        onclick="this.remove()">个人资料</button>
</body></html>'''

TWINS_PAGE = '''<!doctype html><html><head><meta charset="utf-8"></head><body style="margin:0">
<div style="position:absolute;left:40px;top:40px">
  <button id="a" style="display:block;width:180px;height:50px"
          onclick="this.remove()">删除</button>
  <button id="b" style="display:block;width:180px;height:50px">删除</button>
</div>
</body></html>'''

PAGES = {'/gone': GONE_PAGE, '/twins': TWINS_PAGE}


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAGES.get(self.path.split('?')[0], GONE_PAGE).encode('utf-8')
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

    def _start(self, path):
        return RecordingSession.start_new(
            self.base + path, headless=True, ask_daemon=False, driver=_idle_scenario)

    def _press_release(self, session, page, selector, hold=0.4):
        """像人一样按下、停一会、松开：停的这段时间录制线程要把预计数跑完。"""
        box = _in_thread(session, lambda: page.locator(selector).bounding_box())
        x = box['x'] + box['width'] / 2
        y = box['y'] + box['height'] / 2
        _in_thread(session, lambda: page.mouse.move(x, y))
        _in_thread(session, lambda: page.mouse.down())
        # 在测试线程里等，别占着录制线程：它要在这个窗口里处理 precount。
        time.sleep(hold)
        _in_thread(session, lambda: page.mouse.up())


class PrecountTest(_Base):
    def test_click_that_removes_its_own_element_is_not_unverified(self):
        session = self._start('/gone')
        page = session._test_page
        self._press_release(session, page, '#gone')

        self.assertTrue(_wait_for(
            lambda: any(s['type'] == 'click' for s in session.snapshot()['steps'])),
            '点击没有落库')
        # 元素确实是被这次点击带走的：现在页面上已经没有了。
        self.assertEqual(_in_thread(session, lambda: page.locator('#gone').count()), 0)

        step = next(s for s in session.snapshot()['steps'] if s['type'] == 'click')
        target = step['target']
        self.assertFalse(target.get('unverified'), '按下时的计数没有被用上')
        self.assertEqual(target['primary']['count'], 1)
        self.assertEqual(target['status'], 'unique')
        # 元素确实没了：界面据此不再弹「当场选第几个」（页面上只剩别的同名元素）。
        self.assertTrue(target.get('vanished_after'))

    def test_matches_snapshot_is_taken_before_the_click_wipes_it(self):
        session = self._start('/twins')
        page = session._test_page
        self._press_release(session, page, '#a')

        self.assertTrue(_wait_for(
            lambda: any(s['type'] == 'click' for s in session.snapshot()['steps'])),
            '点击没有落库')
        target = next(s for s in session.snapshot()['steps'] if s['type'] == 'click')['target']

        # 点完只剩一个「删除」，但快照是按下时取的：两条都在。
        matches = target.get('matches') or {}
        self.assertEqual(matches.get('count'), 2)
        self.assertEqual(len(matches.get('items') or []), 2)
        self.assertTrue(target.get('vanished_after'))


class PrecountCacheTest(unittest.TestCase):
    def setUp(self):
        self.session = RecordingSession.__new__(RecordingSession)
        self.session._lock = threading.RLock()
        self.session._precounts = {}

    def test_entry_is_consumed_once(self):
        counted = [{'kind': 'css', 'desc': 'locator("#x")', 'count': 1}]
        self.session._precounts['m1'] = (time.monotonic(), counted, None)
        got = self.session._take_precount('m1')
        self.assertIsNotNone(got)
        self.assertEqual(got[0], counted)
        self.assertIsNone(self.session._take_precount('m1'))

    def test_expired_entry_is_ignored(self):
        self.session._precounts['m2'] = (time.monotonic() - 60, [], None)
        self.assertIsNone(self.session._take_precount('m2'))

    def test_missing_mark_is_harmless(self):
        self.assertIsNone(self.session._take_precount(None))


if __name__ == '__main__':
    unittest.main()
