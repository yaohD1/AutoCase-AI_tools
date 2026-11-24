# -*- coding: utf-8 -*-
"""定位确认弹窗的「临时放大面板窗口」。

面板窗口默认 420~900 CSS 宽，弹窗 append-to-body 到它身上就只能拿到 568，
单列下 1600 宽的帧缩成 0.33 倍，图上的字只剩 4px。这组测试盯的是换算：
CSS 宽 × DPI = 物理宽、居中到工作区、屏幕放不下就干脆别动。
"""
import unittest
from unittest import mock

from app.services.recording import session as session_mod
from app.services.recording.session import RecordingSession


def _bare(embed=False, headless=False, hwnd=1234):
    """只造方法需要的那几个属性：这里测的是窗口算式，不需要 Playwright。"""
    obj = object.__new__(RecordingSession)
    obj._embed = embed
    obj._headless = headless
    obj._panel_hwnd = hwnd
    obj._panel_expanded = False
    obj._panel_width = 600
    obj._glue = None
    obj._stop_glue = lambda: None
    return obj


class ExpandPanelTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(session_mod, 'winwindows')
        self.win = patcher.start()
        self.addCleanup(patcher.stop)
        self.win.available.return_value = True
        self.win.prepare.return_value = True
        self.win.scale.return_value = 1.75
        self.win.work_area.return_value = (0, 0, 2560, 1370)
        self.win.move_visible.return_value = True

    def test_width_is_css_times_dpr_and_centered(self):
        obj = _bare()
        result = obj._expand_panel_now()
        self.assertTrue(result['expanded'])
        self.assertEqual(result['css_width'], 1180)
        # 840 CSS 会超过 783 的可用高度，按工作区封顶
        self.assertEqual(result['css_height'], 743)
        args = self.win.move_visible.call_args[0]
        self.assertEqual(args[0], 1234)
        self.assertEqual(args[1], (2560 - 2065) // 2)
        self.assertEqual(args[3], 2065)
        self.assertEqual(args[4], 1300)

    def test_short_screen_still_fits_below_screen_height(self):
        self.win.work_area.return_value = (0, 0, 2560, 1100)
        result = _bare()._expand_panel_now()
        self.assertTrue(result['expanded'])
        # 1100 - 20*1.75*2 = 1030 物理 = 589 CSS
        self.assertEqual(result['css_height'], 589)

    def test_screen_too_narrow_does_not_move_anything(self):
        """拉不到 980 CSS 就别跳这一下：单列拉大一点图还是读不出来。"""
        self.win.work_area.return_value = (0, 0, 1400, 900)
        result = _bare()._expand_panel_now()
        self.assertFalse(result['expanded'])
        self.assertEqual(result['reason'], 'screen-too-small')
        self.win.move_visible.assert_not_called()

    def test_embed_mode_never_touches_windows(self):
        result = _bare(embed=True)._expand_panel_now()
        self.assertFalse(result['expanded'])
        self.assertEqual(result['reason'], 'not-native')

    def test_no_panel_window_falls_back(self):
        result = _bare(hwnd=0)._expand_panel_now()
        self.assertFalse(result['expanded'])
        self.assertEqual(result['reason'], 'no-panel')

    def test_glue_stops_before_resize(self):
        """不断开粘合的话，这次 SetWindowPos 会被当成用户拖动，把被测窗口也拽走。"""
        obj = _bare()
        stopped = []
        obj._stop_glue = lambda: stopped.append(1)
        obj._expand_panel_now()
        self.assertEqual(stopped, [1])

    def test_failed_move_rolls_back_the_flag(self):
        self.win.move_visible.return_value = False
        obj = _bare()
        result = obj._expand_panel_now()
        self.assertFalse(result['expanded'])
        self.assertFalse(obj._panel_expanded)

    def test_expand_is_idempotent(self):
        obj = _bare()
        obj._expand_panel_now()
        first = self.win.move_visible.call_args[0]
        obj._expand_panel_now()
        second = self.win.move_visible.call_args[0]
        self.assertEqual(first, second)


class RestorePanelTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(session_mod, 'winwindows')
        self.win = patcher.start()
        self.addCleanup(patcher.stop)
        self.win.prepare.return_value = True

    def test_nothing_to_restore_when_never_expanded(self):
        obj = _bare()
        obj.layout_windows = mock.Mock(return_value=True)
        self.assertFalse(obj._restore_panel_now()['restored'])
        obj.layout_windows.assert_not_called()

    def test_restore_goes_through_layout_windows(self):
        """不记矩形回放：收起态在屏幕外、悬浮有自己算法，重摆一次才不会留错状态。"""
        obj = _bare()
        obj._panel_expanded = True
        obj.layout_windows = mock.Mock(return_value=True)
        result = obj._restore_panel_now()
        self.assertTrue(result['restored'])
        self.assertEqual(result['panel_width'], 600)
        obj.layout_windows.assert_called_once_with()
        self.assertFalse(obj._panel_expanded)

    def test_restore_twice_is_a_noop(self):
        obj = _bare()
        obj._panel_expanded = True
        obj.layout_windows = mock.Mock(return_value=True)
        obj._restore_panel_now()
        self.assertFalse(obj._restore_panel_now()['restored'])
        self.assertEqual(obj.layout_windows.call_count, 1)


if __name__ == '__main__':
    unittest.main()
