"""录制期常驻解析进程（AskPool）的状态机与收尾。

真起 opencode 的路径没法进单测（CI 上未必装），这里覆盖的是「不影响录制」那一半：
起不来要变成 failed 而不是抛出去、stop 要幂等、延迟停要真的停、没启用要报 off。
"""
import threading
import time
import unittest
from unittest import mock

from app.services.opencode_runner import OpenCodeRunner
from app.services.recording.ask_pool import AskPool, disabled_snapshot
from app.services.recording.session import RecordingSession


def _wait(predicate, timeout=3.0):
    waited = 0.0
    while waited < timeout:
        if predicate():
            return True
        time.sleep(0.02)
        waited += 0.02
    return predicate()


class DisabledTest(unittest.TestCase):
    def test_disabled_snapshot_says_off(self):
        snap = disabled_snapshot()
        self.assertEqual(snap['status'], 'off')
        self.assertEqual(snap['label'], '未启用')
        self.assertIsNone(snap['detail'])

    def test_session_snapshot_reports_off_when_disabled(self):
        session = RecordingSession('http://example.test/', ask_daemon=False)
        self.assertIsNone(session.ask_pool())
        self.assertEqual(session.snapshot()['ask']['status'], 'off')

    def test_session_can_report_pool_snapshot(self):
        session = RecordingSession('http://example.test/', ask_daemon=True, ask_model='p/m')
        session._ask = AskPool({}, 'p/m')
        self.assertEqual(session.snapshot()['ask']['status'], 'starting')


class LifecycleTest(unittest.TestCase):
    def test_boot_failure_marks_failed_instead_of_raising(self):
        """起不来只改状态：录制本身绝不能因为解析器挂了起不来。"""
        pool = AskPool({}, 'p/m')
        with mock.patch.object(OpenCodeRunner, '_start_serve', side_effect=RuntimeError('boom')):
            pool.start()
            self.assertTrue(_wait(lambda: pool.snapshot()['status'] == 'failed'), pool.snapshot())
        self.assertIn('boom', pool.snapshot()['detail'])
        self.assertFalse(pool.is_ready())

    def test_stop_is_idempotent(self):
        pool = AskPool({}, None)
        pool.stop()
        pool.stop()
        self.assertEqual(pool.snapshot()['status'], 'stopped')
        self.assertFalse(pool.is_ready())

    def test_stop_can_be_delayed(self):
        """停止录制后浏览器还开着，解析器要留一个窗口再收。"""
        pool = AskPool({}, None)
        pool.stop(delay=0.15)
        self.assertEqual(pool.snapshot()['status'], 'starting')  # 还没到点
        self.assertTrue(_wait(lambda: pool.snapshot()['status'] == 'stopped', timeout=2.0))

    def test_delayed_stop_is_cancelled_by_immediate_stop(self):
        pool = AskPool({}, None)
        pool.stop(delay=5)
        pool.stop()
        time.sleep(0.2)
        self.assertEqual(pool.snapshot()['status'], 'stopped')

    def test_fail_blocks_further_use(self):
        pool = AskPool({}, 'p/m')
        pool._set_status('ready')
        self.assertTrue(pool.is_ready())
        pool.fail('模型返回错误')
        self.assertFalse(pool.is_ready())
        snap = pool.snapshot()
        self.assertEqual(snap['status'], 'failed')
        self.assertIn('模型返回错误', snap['detail'])

    def test_ask_before_ready_raises(self):
        from app.services.recording.ask_pool import AskUnavailable
        pool = AskPool({}, 'p/m')
        with self.assertRaises(AskUnavailable):
            pool.ask('s', 'p')

    def test_note_model_does_not_restart_the_process(self):
        """模型在请求体里，换模型不需要重启进程，只是补一次预热。"""
        pool = AskPool({}, 'a/one')
        pool._set_status('ready')
        with mock.patch.object(pool, '_prewarm') as prewarm:
            pool.note_model('b/two')
            self.assertTrue(_wait(lambda: prewarm.called))
        self.assertEqual(pool.snapshot()['model'], 'b/two')


if __name__ == '__main__':
    unittest.main()
