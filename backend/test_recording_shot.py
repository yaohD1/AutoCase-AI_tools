"""「不唯一」时按下那一刻的截图。

要守的三件事：
1. 图必须在页面跳走**之前**截到 —— 这正是「点一下就换页」的步骤最需要图的地方；
2. 同一个候选的几个元素带着视口坐标，弹窗才能在图上画框；
3. 「允许降级」落库之后，这一步不再算待确认。
"""
import http.server
import threading
import unittest
from urllib.parse import urlparse

from app.services.recording.session import RecordingSession, _shot_error


def _click_like_a_human(session, locator, hold=0.12):
    """按下、停一下、再松开。

    不能用 locator.click()：它把 down/up 挤在一起，预计数那条 binding 还没到 Python，
    页面就已经被 click 带走了——那是脚本的手速，不是人的。真实点击 down 到 up 有几十毫秒，
    预计数和截图都落在这一段里。
    """
    import time
    box = _in_recording_thread(session, lambda: locator.bounding_box())
    page = session._test_page

    def press():
        page.mouse.move(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
        page.mouse.down()

    _in_recording_thread(session, press)
    time.sleep(hold)
    _in_recording_thread(session, lambda: page.mouse.up())

INDEX_PAGE = (
    '<!doctype html><html><head><meta charset="utf-8"></head><body>'
    '<h1>列表</h1>'
    '<button class="save">保存</button>'
    '<button class="save" onclick="location.href=\'/second.html\'">保存</button>'
    '<button class="only">提交</button>'
    '</body></html>'
)

SECOND_PAGE = (
    '<!doctype html><html><head><meta charset="utf-8"></head><body>'
    '<h1>第二页</h1></body></html>'
)

PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = SECOND_PAGE if 'second' in self.path else INDEX_PAGE
        raw = body.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


def _idle_scenario(session, page):
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


def _wait_for(predicate, timeout=15):
    import time
    waited = 0.0
    while waited < timeout:
        if predicate():
            return True
        time.sleep(0.05)
        waited += 0.05
    return predicate()


class AmbiguousShotTest(unittest.TestCase):
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
        session = RecordingSession.start_new(self.base, headless=True, ask_daemon=False,
                                            driver=_idle_scenario)
        self.assertEqual([s['type'] for s in session.ir()], ['goto'])
        return session

    def test_click_that_navigates_still_has_the_page_as_it_was(self):
        """点第二个「保存」会跳走：图必须还在原来那一页，否则弹窗里没有可看的东西。"""
        session = self._record()
        _click_like_a_human(session, session._test_page.get_by_role('button', name='保存').nth(1))
        self.assertTrue(_wait_for(lambda: len(session.ir()) == 2), session.ir())

        step = session.ir()[-1]
        self.assertEqual(step['type'], 'click')
        shot = step['target'].get('shot') or {}
        self.assertTrue(shot.get('id'), step['target'])
        self.assertTrue(shot.get('w') and shot.get('h'), shot)

        shot_bytes = (session.shot(step['no']) or {}).get('png')
        self.assertTrue(shot_bytes and shot_bytes[:8] == PNG_SIGNATURE, '截图应该是合法 PNG')

    def test_items_carry_viewport_rects(self):
        """画框靠这几个字段：没有坐标，图上有几个元素也标不出来。"""
        session = self._record()
        _click_like_a_human(session, session._test_page.get_by_role('button', name='保存').nth(1))
        self.assertTrue(_wait_for(lambda: len(session.ir()) == 2), session.ir())

        items = session.ir()[-1]['target']['matches']['items']
        self.assertEqual(len(items), 2)
        for item in items:
            for key in ('x', 'y', 'w', 'h'):
                self.assertIsInstance(item[key], int, item)
            self.assertGreater(item['w'], 0)
            # 两个按钮是左右并排的，横坐标必须不同 —— 全都一样说明取错了元素。
        self.assertNotEqual(items[0]['x'], items[1]['x'])

    def test_unique_click_does_not_pay_for_a_screenshot(self):
        """唯一的那几百次点击不该截图：那是白花 50~80ms。"""
        session = self._record()
        _click_like_a_human(session, session._test_page.get_by_role('button', name='提交'))
        self.assertTrue(_wait_for(lambda: len(session.ir()) == 2), session.ir())
        self.assertNotIn('shot', session.ir()[-1]['target'])
        self.assertIsNone(session.shot(session.ir()[-1]['no']))

    def test_degrade_stops_asking(self):
        session = self._record()
        _click_like_a_human(session, session._test_page.get_by_role('button', name='保存').nth(1))
        self.assertTrue(_wait_for(lambda: len(session.ir()) == 2), session.ir())
        no = session.ir()[-1]['no']

        data = session.set_degraded(no)
        step = next(s for s in data['steps'] if s['no'] == no)
        self.assertTrue(step['target'].get('degraded'))


EMBED_TARGET = (
    '<!doctype html><html><head><meta charset="utf-8"></head><body style="margin:0">'
    '<h1>应用</h1>'
    '<button class="save" style="margin:20px 40px">保存</button>'
    '<button class="save" style="margin:20px 40px">保存</button>'
    '<div style="margin:20px 40px">'
    '<input placeholder="用户名"><input placeholder="用户名">'
    '</div>'
    '</body></html>'
)
EMBED_SHELL = (
    '<!doctype html><html><head><meta charset="utf-8"></head><body style="margin:0">'
    '<div style="display:flex">'
    '<iframe id="app" src="%s" style="width:500px;height:400px;border:0"></iframe>'
    '<aside style="flex:1;height:400px;background:#eee">编排面板</aside>'
    '</div></body></html>'
)


def _serve(pages):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            raw = pages.get(urlparse(self.path).path, '').encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class EmbedShotTest(unittest.TestCase):
    """嵌入模式：被测页面在 iframe 里，截图必须只截那一块。

    这是之前最要命的偏差——截整页会把 AutoCase 自己的编排面板也拍进去，
    而框的坐标是 iframe 内的，画在整页图上会整体偏移。
    """

    @classmethod
    def setUpClass(cls):
        cls.target = _serve({'/target.html': EMBED_TARGET})
        cls.target_url = 'http://127.0.0.1:%d/target.html' % cls.target.server_address[1]
        cls.shell = _serve({'/shell.html': EMBED_SHELL % cls.target_url})
        cls.shell_url = 'http://127.0.0.1:%d/shell.html' % cls.shell.server_address[1]

    @classmethod
    def tearDownClass(cls):
        for server in (cls.target, cls.shell):
            server.shutdown()
            server.server_close()

    def setUp(self):
        RecordingSession._current = None

    def tearDown(self):
        RecordingSession.dispose_current()

    def _record(self):
        return RecordingSession.start_new(self.target_url, headless=True, ask_daemon=False,
                                          embed_url=self.shell_url, driver=_idle_scenario)

    def _click_save(self, session):
        app = session._test_page.frame_locator('iframe#app')
        _click_like_a_human(session, app.get_by_role('button', name='保存').nth(1))
        self.assertTrue(_wait_for(lambda: any(s['type'] == 'click' for s in session.ir())),
                        session.ir())
        return next(s for s in reversed(session.ir()) if s['type'] == 'click')

    def test_shot_is_cropped_to_the_target_frame(self):
        session = self._record()
        step = self._click_save(session)
        shot = step['target'].get('shot') or {}
        self.assertTrue(shot.get('id'), step['target'])
        # iframe 是 500x400，截整页会是 1280x720
        self.assertEqual((shot['w'], shot['h']), (500, 400), shot)
        png = (session.shot(step['no']) or {}).get('png') or b''
        self.assertEqual(png[:8], PNG_SIGNATURE)
        self.assertEqual(int.from_bytes(png[16:20], 'big'), 500, 'PNG 宽度应该就是 iframe 的宽度')

    def test_fill_with_several_matches_gets_a_shot(self):
        """输入框以前永远没有图：它没有 pointerdown，只有 input 事件。"""
        session = self._record()
        app = session._test_page.frame_locator('iframe#app')
        _in_recording_thread(session, lambda: app.get_by_placeholder('用户名').nth(1).fill('abc'))
        self.assertTrue(_wait_for(lambda: any(s['type'] == 'fill' for s in session.ir())),
                        session.ir())
        step = next(s for s in reversed(session.ir()) if s['type'] == 'fill')
        self.assertIn('shot', step['target'], step['target'])
        self.assertTrue(step['target']['shot'].get('id'), step['target']['shot'])


class ShotErrorTest(unittest.TestCase):
    def setUp(self):
        RecordingSession._current = None

    def tearDown(self):
        RecordingSession.dispose_current()

    def test_failed_capture_keeps_the_reason(self):
        """失败不能只回 None：前端得说得出「为什么没截到」。"""
        # 不用 start_new：这里只验证「没有页面时怎么回」，起浏览器反而慢。
        session = RecordingSession('http://example.com/')
        shot = session._capture_shot(None, None, {'count': 2})
        self.assertIn('error', shot)
        meta = session._register_shot(shot)
        self.assertIn('error', meta)

    def test_reasons_are_human_readable(self):
        self.assertIn('跳转', _shot_error(Exception('Execution context was destroyed')))
        self.assertIn('关了', _shot_error(Exception('Target closed')))
        self.assertIn('超时', _shot_error(Exception('Timeout 1500ms exceeded')))


if __name__ == '__main__':
    unittest.main()
