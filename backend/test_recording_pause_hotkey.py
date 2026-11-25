# -*- coding: utf-8 -*-
"""Ctrl+` 暂停快捷键的状态机。

快捷键在页面里（recorder.js），但状态只有 Python 这一份：页面只发 `pause-toggle`，
真正翻状态的是 `_set_paused`。这里只测状态机那一半；按键能不能收到、会不会往输入框
打字，在 .tmp-promptlab/pause_hotkey_probe.py 里跑真录制器验证。

最要紧的一条是去重：一次按键会发给页面上每一个 frame，不去重的话两个 frame
各翻一次，按一下等于没按。
"""
import threading
import unittest

from app.services.recording.session import RecordingSession


class PauseHotkeyTest(unittest.TestCase):
    def make_session(self, status='recording', paused=False):
        session = RecordingSession.__new__(RecordingSession)
        session.status = status
        session._paused = paused
        session._steps = []
        session._lock = threading.RLock()
        session._context = None
        session.calls = []

        def fake_set_paused(on):
            session._paused = bool(on)
            session.calls.append(bool(on))

        session._set_paused = fake_set_paused
        session._push_hud = lambda: None
        session._touch = lambda: None
        return session

    def test_toggle_starts_pausing(self):
        session = self.make_session()
        session._handle_hud_action('pause-toggle')
        self.assertEqual(session.calls, [True])
        self.assertTrue(session.paused)

    def test_toggle_resumes(self):
        session = self.make_session(paused=True)
        session._handle_hud_action('pause-toggle')
        self.assertEqual(session.calls, [False])
        self.assertFalse(session.paused)

    def test_same_keypress_from_many_frames_counts_once(self):
        session = self.make_session()
        # 主文档 + 两个 iframe，一次按键三条回调
        session._handle_hud_action('pause-toggle')
        session._handle_hud_action('pause-toggle')
        session._handle_hud_action('pause-toggle')
        self.assertEqual(session.calls, [True], '多 frame 不能互相抵消')

    def test_next_press_after_window_works(self):
        session = self.make_session()
        session._handle_hud_action('pause-toggle')
        # 把去重时间戳往前挪，模拟「隔了一会儿又按了一次」
        session._pause_toggle_at -= 0.3
        session._handle_hud_action('pause-toggle')
        self.assertEqual(session.calls, [True, False])

    def test_ignored_after_stop(self):
        session = self.make_session(status='stopped')
        session._handle_hud_action('pause-toggle')
        self.assertEqual(session.calls, [])
        self.assertFalse(session.paused)

    def test_ignored_while_failed(self):
        session = self.make_session(status='failed')
        session._handle_hud_action('pause-toggle')
        self.assertEqual(session.calls, [])

    def test_steps_are_untouched(self):
        session = self.make_session()
        session._steps = [{'no': 1, 'type': 'click'}]
        session._handle_hud_action('pause-toggle')
        # 快捷键不是撤销：掉进后面的 undo 分支会把用户最后一步删掉。
        self.assertEqual(len(session._steps), 1)

    def test_unknown_action_still_ignored(self):
        session = self.make_session()
        session._handle_hud_action('pause')
        self.assertEqual(session.calls, [])


if __name__ == '__main__':
    unittest.main()
