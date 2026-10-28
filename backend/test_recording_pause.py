"""暂停中的回放：暂停必须让步骤集真的定下来，且这份 IR 能原样重演一遍。

「暂停时可回放」的前提只有一个：暂停之后不会再有步骤落库，否则回放跑的是
A 时刻的步骤、用户看到的是 B 时刻的列表，结论对不上。这里用真实浏览器验证这条，
并把暂停时的 IR 真跑一遍（不是 mock），确认真能走通。
"""
import http.server
import threading
import time
import unittest

from app.services.recording import replay as replay_mod
from app.services.recording.session import RecordingSession

PAGE = '''<!doctype html><html><head><meta charset="utf-8"></head><body style="margin:0">
<button id="a" style="position:absolute;left:40px;top:40px;width:160px;height:50px">第一步</button>
<button id="b" style="position:absolute;left:40px;top:120px;width:160px;height:50px">暂停后点的</button>
</body></html>'''


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAGE.encode('utf-8')
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


def _click(session, page, selector, hold=0.12):
    box = _in_thread(session, lambda: page.locator(selector).bounding_box())
    x = box['x'] + box['width'] / 2
    y = box['y'] + box['height'] / 2
    _in_thread(session, lambda: page.mouse.move(x, y))
    _in_thread(session, lambda: page.mouse.down())
    # 在测试线程里等，别占着录制线程：它要处理 pointerdown 的预计数。
    time.sleep(hold)
    _in_thread(session, lambda: page.mouse.up())


class PauseReplayTest(unittest.TestCase):
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

    def test_pause_freezes_steps_and_ir_can_be_replayed(self):
        session = RecordingSession.start_new(
            self.base + '/page', headless=True, ask_daemon=False, driver=_idle_scenario)
        page = session._test_page

        _click(session, page, '#a')
        # 等的是「点击步骤」落库，不是「有步骤」：goto 在会话一开始就有了，
        # 拿它当依据会在点击还在路上时就暂停，后面判断的其实是那次点击。
        self.assertTrue(_wait_for(
            lambda: any(s['type'] == 'click' for s in session.snapshot()['steps'])),
            '第一步没有落库')
        before = session.snapshot()['steps']

        # 暂停是一道命令，走的是和事件同一条流水线：它之前的点击必须已经处理完，
        # 它之后的点击一律不进 IR。否则「暂停后回放」跑的和看到的不是同一份步骤。
        session.set_pause(True)
        self.assertTrue(session.paused)
        self.assertEqual(session.status, 'recording', '暂停不该把状态改成 stopped')

        _click(session, page, '#b')
        time.sleep(0.6)
        after = session.snapshot()['steps']
        self.assertEqual(len(after), len(before), '暂停期间还有步骤落库，回放的步骤集不稳定')
        self.assertEqual([s['no'] for s in after], [s['no'] for s in before])
        self.assertNotIn('暂停后点的', [s['target']['alias'] for s in after if s.get('target')])

        ir = session.ir()
        self.assertEqual(len(ir), len(after))

        # 真跑一遍，不用 mock：暂停时的 IR 必须能原样重演。
        result = replay_mod.replay_checked(ir, framework='playwright-ts', headless=True)
        self.assertTrue(result['ok'], result.get('error'))
        self.assertEqual(result['done'], len(ir))

        # 继续录之后暂停标记要清掉，前端据此恢复「暂停中」提示和回放入口。
        session.set_pause(False)
        self.assertFalse(session.paused)


if __name__ == '__main__':
    unittest.main()
