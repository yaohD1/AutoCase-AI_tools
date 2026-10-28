# -*- coding: utf-8 -*-
"""拾取开关的状态归属。

拾取模式能在页面里按 Esc 退出，但状态存在 Python 这边（工具栏按钮、轮询都读它）。
页内 JS 自己关掉不通知后端的话，面板会一直显示「退出拾取」，点了才同步——状态只有一个出处，
所以 Esc 走的是 binding → `_handle_hud_action('pick-off')` → `_set_pick(False)`。

这里只测状态机这一半；页面里的高亮/光标/横幅在 test_recording_pick_hover 里跑真录制器验证。
"""
import threading
import unittest

from app.services.recording.session import RecordingSession


class PickStateTest(unittest.TestCase):
    def make_session(self, pick=False, steps=2):
        session = RecordingSession.__new__(RecordingSession)
        session._pick = pick
        session._steps = [{'no': i + 1, 'type': 'click'} for i in range(steps)]
        session._lock = threading.RLock()
        session._context = None
        session._skip_nav = set()
        session.calls = []
        session._set_pick = lambda on: session.calls.append(on)
        session._touch = lambda: None
        session._push_hud = lambda: None
        session._renumber = lambda: None
        return session

    def test_pick_property_defaults_false(self):
        self.assertFalse(self.make_session().pick)

    def test_pick_property_reads_the_flag(self):
        self.assertTrue(self.make_session(pick=True).pick)

    def test_pick_off_only_toggles_pick(self):
        session = self.make_session(pick=True)
        session._handle_hud_action('pick-off')
        self.assertEqual(session.calls, [False])
        # 必须提前 return：掉进下面的 undo 分支会把上一个步骤删掉。
        # 用户按的是 Esc，不是撤销。
        self.assertEqual(len(session._steps), 2)

    def test_unknown_action_changes_nothing(self):
        session = self.make_session()
        session._handle_hud_action('whatever')
        self.assertEqual(session.calls, [])
        self.assertEqual(len(session._steps), 2)

    def test_undo_still_removes_last_step(self):
        session = self.make_session()
        session._handle_hud_action('undo')
        self.assertEqual(session.calls, [])
        self.assertEqual(len(session._steps), 1)


if __name__ == '__main__':
    unittest.main()
