# -*- coding: utf-8 -*-
"""「回到这一步的页面」：用录制时记下的地址重开那一页，并且这次跳转不能被记成步骤。

为什么不用浏览器后退：实测在一个 SPA 上 go_back 会落到 /login，
go_forward 也回不去（登录跳转把历史栈占了）。用地址重开是干净的。
"""
import threading
import time
import unittest

from app.services.recording.session import RecordingSession


class FakeFrame:
    def __init__(self, url='https://app/detail', error=None):
        self.url = url
        self.parent_frame = None
        self.error = error
        self.gotos = []
        self.waits = []

    def goto(self, url, **kwargs):
        if self.error:
            raise self.error
        self.gotos.append((url, kwargs))
        self.url = url

    def wait_for_timeout(self, ms):
        self.waits.append(ms)


class GotoDocTest(unittest.TestCase):
    def make_session(self, steps=None, frame=None):
        session = RecordingSession.__new__(RecordingSession)
        session._context = object()
        session._embed = False
        session._steps = steps or []
        session._lock = threading.RLock()
        session._return_url = None
        session._nav_mute_until = 0.0
        session._doc_stamp = {}
        session._last_url = {}
        session._skip_nav = set()
        session._pending_click = {}
        session._paused = False
        session._status = 'stopped'
        session._frame = frame
        # 真实实现要 evaluate 页面读 performance；这里只关心跳转与静音。
        session._nav_info = lambda frame, prev=None: None
        session._app_frame = lambda: frame
        return session

    def step(self, url, frame):
        return {'no': 1, 'type': 'click', 'target': {}, '_doc': {'url': url}, '_frame': frame}

    def test_no_recorded_url_is_reported_not_raised(self):
        frame = FakeFrame()
        session = self.make_session(steps=[self.step(None, frame)], frame=frame)
        self.assertEqual(session._goto_doc_now(1)['reason'], 'no_url')
        self.assertEqual(frame.gotos, [])

    def test_goto_uses_recorded_url_and_remembers_where_to_come_back(self):
        frame = FakeFrame('https://app/detail')
        session = self.make_session(steps=[self.step('https://app/list', frame)], frame=frame)
        result = session._goto_doc_now(1)
        self.assertTrue(result['ok'])
        self.assertEqual(frame.gotos[0][0], 'https://app/list')
        # 离开时在详情页：选完要能回到这里，而不是回到列表
        self.assertEqual(session._return_url, 'https://app/detail')
        self.assertEqual(result['return_url'], 'https://app/detail')

    def test_second_look_does_not_overwrite_the_return_point(self):
        frame = FakeFrame('https://app/detail')
        session = self.make_session(steps=[self.step('https://app/list', frame)], frame=frame)
        session._goto_doc_now(1)
        session._goto_doc_now(1)
        # 第二次时 frame.url 已经是列表页了，但还是得回到最初的详情页
        self.assertEqual(session._return_url, 'https://app/detail')

    def test_goto_back_returns_and_clears(self):
        frame = FakeFrame('https://app/detail')
        session = self.make_session(steps=[self.step('https://app/list', frame)], frame=frame)
        session._goto_doc_now(1)
        result = session._goto_back_now()
        self.assertTrue(result['ok'])
        self.assertEqual(frame.gotos[-1][0], 'https://app/detail')
        self.assertIsNone(session._return_url)

    def test_goto_back_without_a_return_point(self):
        frame = FakeFrame()
        session = self.make_session(frame=frame)
        self.assertEqual(session._goto_back_now()['reason'], 'no_return')
        self.assertEqual(frame.gotos, [])

    def test_navigation_failure_is_reported(self):
        frame = FakeFrame(error=RuntimeError('net::ERR_TIMED_OUT'))
        session = self.make_session(steps=[self.step('https://app/list', frame)], frame=frame)
        result = session._goto_doc_now(1)
        self.assertFalse(result['ok'])
        self.assertEqual(result['reason'], 'goto_failed')

    def test_settles_after_goto(self):
        """goto 到 domcontentloaded 时列表还是空壳，必须等一拍再去框。"""
        frame = FakeFrame()
        session = self.make_session(steps=[self.step('https://app/list', frame)], frame=frame)
        session._goto_doc_now(1)
        self.assertEqual(len(frame.waits), 1)
        self.assertGreaterEqual(frame.waits[0], 500)

    def test_muted_navigation_updates_identity_but_adds_no_step(self):
        """「回来看」引起的跳转要静音：否则每看一次就多一条 goto 步骤。"""
        frame = FakeFrame('https://app/list')
        session = self.make_session(frame=frame)
        session._status = 'recording'
        session._nav_mute_until = time.monotonic() + 5
        before = list(session._steps)
        session._handle_navigated(object(), frame)
        self.assertEqual(session._steps, before)
        # 身份还是要更新，否则后续 same_url / stamp 判断会错位
        self.assertEqual(session._last_url[id(frame)], 'https://app/list')


if __name__ == '__main__':
    unittest.main()
