"""点击目标中心点校正：元素中心被别的节点盖住时，Playwright 点它会直接拒绝。

真实案例（2026-10-28）：发送按钮是 `div > svg > rect`，箭头 `path` 画在 rect 上面。
用户点在 rect 的边缘空隙，录制如实记下 rect；回放/生成点的是 rect 的中心，
正好压在 path 上，于是报 `path intercepts pointer events`，30s 超时。

规则：中心点命中的不是元素自己或它的后代 → 向上找到「中心点落在自己内部」的祖先。
这里用真实浏览器覆盖四种情况：该上提、不该上提、装饰层不误伤、爬不上去就保原样。
"""
import http.server
import threading
import time
import unittest

from app.services.recording.session import RecordingSession

# 图标按钮：rect 铺满，path 画在中间。点中心命中 path，点边角命中 rect。
ICON_PAGE = '''<!doctype html><html><head><meta charset="utf-8"></head>
<body style="margin:0">
<div id="send" style="position:absolute;left:40px;top:40px;width:40px;height:40px">
  <svg width="40" height="40" style="display:block">
    <rect x="0" y="0" width="40" height="40" rx="6" fill="#f0e6ff"></rect>
    <path d="M20 9 V31 M11 18 L20 9 L29 18" stroke="#7b3fe4" stroke-width="2" fill="none"></path>
  </svg>
</div>
</body></html>'''

# 普通按钮：中心就是自己，不该发生任何上提。
PLAIN_PAGE = '''<!doctype html><html><head><meta charset="utf-8"></head>
<body style="margin:0">
<button id="save" style="position:absolute;left:40px;top:40px;width:160px;height:48px">保存</button>
</body></html>'''

# 装饰层：盖在按钮上但不接收指针事件，elementFromPoint 会跳过它。
DECO_PAGE = '''<!doctype html><html><head><meta charset="utf-8"></head>
<body style="margin:0">
<button id="save" style="position:absolute;left:40px;top:40px;width:160px;height:48px">保存</button>
<div style="position:absolute;left:40px;top:40px;width:160px;height:48px;pointer-events:none;
            background:linear-gradient(rgba(0,0,0,.05),rgba(0,0,0,0))"></div>
</body></html>'''

# 中心被一块固定遮罩盖住，而遮罩不在目标的任何祖先子树里：爬到 body 也解决不了，必须保原样。
MASKED_PAGE = '''<!doctype html><html><head><meta charset="utf-8"></head>
<body style="margin:0">
<div style="position:absolute;left:40px;top:40px;width:120px;height:60px">
  <div><div><div><div>
    <div id="deep" style="width:120px;height:60px;background:#eee">内容</div>
  </div></div></div></div>
</div>
<div style="position:fixed;left:70px;top:55px;width:60px;height:30px;background:#ccc"></div>
</body></html>'''

PAGES = {'/icon': ICON_PAGE, '/plain': PLAIN_PAGE, '/deco': DECO_PAGE, '/masked': MASKED_PAGE}


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAGES.get(self.path.split('?')[0], PLAIN_PAGE).encode('utf-8')
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

    def _click_at(self, session, page, selector, dx, dy, hold=0.3):
        """按在元素内部的指定偏移上：模拟人点在边角、避开中间的图标。"""
        box = _in_thread(session, lambda: page.locator(selector).bounding_box())
        x = box['x'] + dx
        y = box['y'] + dy
        _in_thread(session, lambda: page.mouse.move(x, y))
        _in_thread(session, lambda: page.mouse.down())
        time.sleep(hold)  # 让录制线程把 precount 跑完
        _in_thread(session, lambda: page.mouse.up())

    def _click_step(self, session):
        self.assertTrue(_wait_for(
            lambda: any(s['type'] == 'click' for s in session.snapshot()['steps'])),
            '点击没有落库')
        return next(s for s in session.snapshot()['steps'] if s['type'] == 'click')


class ClickTargetLiftTest(_Base):
    def test_covered_shape_lifts_to_clickable_container(self):
        """rect 的中心被兄弟 path 盖住 → 目标改成 svg，并记下原来的 rect。"""
        session = self._start('/icon')
        page = session._test_page
        # 点在 rect 左上角：这个位置没有 path，浏览器把 click 交给 rect。
        self._click_at(session, page, '#send', 5, 5)

        target = self._click_step(session)['target']
        snap = target['snap']
        self.assertEqual(snap['tag'], 'svg', '中心被遮挡时目标没有上提到可点击容器')
        self.assertEqual(snap['lifted'], 'rect', '没有记下被替换掉的原元素')
        # 生成的定位必须落在 svg 上，不能还带着 rect。
        self.assertIn('svg', target['primary']['desc'])
        self.assertNotIn('rect', target['primary']['desc'])

    def test_normal_button_is_not_lifted(self):
        """普通按钮中心就是自己，不该被动。"""
        session = self._start('/plain')
        page = session._test_page
        self._click_at(session, page, '#save', 80, 24)

        target = self._click_step(session)['target']
        self.assertEqual(target['snap']['tag'], 'button')
        self.assertIsNone(target['snap']['lifted'])
        self.assertEqual(target['status'], 'unique')

    def test_pointer_events_none_decoration_does_not_trigger_lift(self):
        """装饰层不接收指针事件，elementFromPoint 会跳过它，不能因此上提。"""
        session = self._start('/deco')
        page = session._test_page
        self._click_at(session, page, '#save', 80, 24)

        target = self._click_step(session)['target']
        self.assertEqual(target['snap']['tag'], 'button')
        self.assertIsNone(target['snap']['lifted'], '透明装饰层把目标顶跑了')

    def test_unreachable_cover_keeps_original_target(self):
        """遮罩不在同一棵子树里：爬到上限也解决不了，保原样，别瞎猜。"""
        session = self._start('/masked')
        page = session._test_page
        # 点在 #deep 左上角，遮罩从 (30,15) 开始，这里没被盖住。
        self._click_at(session, page, '#deep', 5, 5)

        target = self._click_step(session)['target']
        self.assertEqual(target['snap']['tag'], 'div', '爬不上去时不该把目标换成别的元素')
        self.assertIsNone(target['snap']['lifted'])
        # 步骤本身不能丢：定位不准是后话，漏一步会直接改掉流程。
        self.assertIsNotNone(target['primary'])


if __name__ == '__main__':
    unittest.main()
