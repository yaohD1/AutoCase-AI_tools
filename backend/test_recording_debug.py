"""调试回放的执行器测试。

重点不是「能不能跑通一条流程」（那个靠真跑），而是几个容易写错的控制语义：
暂停要真的停住、单步只走一步、失败要停在现场、单步那一步不要多等。
"""
import threading
import time
import unittest
from unittest import mock

from app.services.recording import debug


def make(steps=None, framework='playwright-ts', delay=0.0):
    steps = steps or [{'no': 1, 'type': 'goto', 'url': 'http://example.test/'}]
    return debug.DebugSession(steps, framework, delay=delay)


class SnapshotTest(unittest.TestCase):
    def test_lists_every_step_as_pending_upfront(self):
        """右侧列表在执行之前就得有全部步骤，否则用户看不到「还剩多少」。"""
        session = make([
            {'no': 1, 'type': 'goto', 'url': 'http://a/'},
            {'no': 2, 'type': 'click', 'target': {'alias': '登录按钮'}},
            {'no': 3, 'type': 'sleep', 'intent': {'seconds': 0.2}},
        ])
        snap = session.snapshot()
        self.assertEqual(snap['total'], 3)
        self.assertEqual([s['status'] for s in snap['steps']], ['pending'] * 3)
        self.assertEqual(snap['steps'][1]['alias'], '登录按钮')
        self.assertEqual(snap['steps'][2]['label'], '固定等待 0.2s')

    def test_snapshot_is_a_copy(self):
        """快照被前端/测试改坏不能污染会话本身。"""
        session = make()
        snap = session.snapshot()
        snap['steps'][0]['status'] = 'failed'
        self.assertEqual(session.snapshot()['steps'][0]['status'], 'pending')


class GateTest(unittest.TestCase):
    def test_pause_blocks_until_resume(self):
        session = make()
        session.control('pause')
        entered = []

        def run():
            entered.append(session._gate())

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        time.sleep(0.25)
        self.assertEqual(entered, [], '暂停时闸门不该放行')
        session.control('resume')
        worker.join(1.0)
        self.assertEqual(entered, [True])

    def test_step_lets_exactly_one_through(self):
        session = make()
        session.control('pause')
        session.control('step')
        self.assertTrue(session._gate(), '单步应当放行一次')
        # 第二次就不该再放行了（还在暂停态）
        result = []
        worker = threading.Thread(target=lambda: result.append(session._gate()), daemon=True)
        worker.start()
        time.sleep(0.2)
        self.assertEqual(result, [], '单步只放行一步，之后要重新停住')

    def test_step_marks_that_one_step(self):
        session = make()
        session.control('pause')
        session.control('step')
        session._gate()
        self.assertTrue(session._stepped, '单步走的那一步要标记出来，好在 _dwell 里跳过停留')

    def test_stop_exits_the_gate(self):
        session = make()
        session.control('pause')
        session.control('stop')
        self.assertFalse(session._gate())

    def test_paused_status_is_restored_after_a_step(self):
        """单步走完回到闸门时状态要变回「已暂停」，否则界面一直显示「运行中」。"""
        session = make()
        session.control('pause')
        session.control('step')
        session._gate()                       # 放行这一步
        session._set(status='running')        # 执行期间是 running
        worker = threading.Thread(target=session._gate, daemon=True)
        worker.start()
        time.sleep(0.25)
        self.assertEqual(session.snapshot()['status'], 'paused')
        session.control('stop')


class DwellTest(unittest.TestCase):
    def test_single_step_does_not_dwell(self):
        session = make(delay=0.6)
        session._stepped = True
        started = time.monotonic()
        session._dwell()
        self.assertLess(time.monotonic() - started, 0.2, '单步那一步不该再停 0.6 秒')

    def test_pause_short_circuits_the_dwell(self):
        """按暂停后不该把剩下的停留时间耗完，否则点完按钮半秒内没反应。"""
        session = make(delay=0.8)
        session.control('pause')
        started = time.monotonic()
        session._dwell()
        self.assertLess(time.monotonic() - started, 0.2)

    def test_normal_dwell_waits(self):
        session = make(delay=0.25)
        started = time.monotonic()
        session._dwell()
        self.assertGreaterEqual(time.monotonic() - started, 0.2)


class SpeedTest(unittest.TestCase):
    def test_speed_is_clamped(self):
        session = make()
        session.set_speed(10)
        self.assertLessEqual(session.delay, 3.0)
        session.set_speed(-5)
        self.assertEqual(session.delay, 0.0)


class HighlightTest(unittest.TestCase):
    def test_highlight_does_not_create_dom_nodes(self):
        """高亮必须改元素自身的 outline。

        插一个 div 当覆盖层的话，回放自己的定位器会把那个 div 也数进去，
        「命中 1 个」莫名变「命中 2 个」，排查半天才发现是自己画的框。
        """
        for script in (debug._HL_ON, debug._HL_OFF):
            self.assertNotIn('createElement', script)
            self.assertNotIn('appendChild', script)


class SingletonTest(unittest.TestCase):
    def tearDown(self):
        if debug.current() is not None:
            try:
                debug.stop_current()
            except Exception:
                pass

    def test_busy_when_one_is_already_running(self):
        first = debug.DebugSession([], 'playwright-ts')
        first._state['status'] = 'running'
        debug._current = first
        with self.assertRaises(debug.DebugBusy):
            debug.start([], 'playwright-ts')

    def test_finished_session_can_be_replaced(self):
        first = debug.DebugSession([], 'playwright-ts')
        first._state['status'] = 'done'
        debug._current = first
        # 别真起浏览器：这一条只验「旧的结束了就能换新的」
        with mock.patch.object(debug.DebugSession, 'start', lambda self: self):
            second = debug.start([], 'playwright-ts', delay=0)
        self.assertIsNot(second, first)


if __name__ == '__main__':
    unittest.main()
