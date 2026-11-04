# -*- coding: utf-8 -*-
"""两窗口并排的尺寸计算。

真正的「把窗口挪过去」在 Win32 那边，测试环境没有桌面，只能测到「该挪到哪」：
  1. 按显示器工作区切，不是按整块屏幕（扣掉任务栏、多屏也对）；
  2. 两个窗口之间不留缝——留了就是一条露出桌面的黑缝；
  3. 面板宽度兜底，屏幕再窄也别把面板压成一条；
  4. 无头环境 / 没有面板窗口时一律不动手，别在测试机上弹出真窗口。
"""
import unittest
from unittest.mock import MagicMock, patch

from app.services.recording.session import RecordingSession
from app.services.recording.winwindows import plan_follow


def make_session(panel_hwnd=111, main_hwnd=222, ratio=0.45, headless=False):
    session = RecordingSession.__new__(RecordingSession)
    session._embed = False
    session._headless = headless
    session._panel_hwnd = panel_hwnd
    session._main_hwnd = main_hwnd
    session._panel_ratio = ratio
    session._glue = None
    return session


class LayoutNativeTest(unittest.TestCase):
    def _patch(self, work=(0, 0, 1920, 1040)):
        ww = MagicMock()
        ww.available.return_value = True
        ww.work_area.return_value = work
        ww.is_usable.return_value = True
        ww.prepare.return_value = True
        # 记住摆到哪，visible_rect 照着回读——摆完之后还要校验一次有没有真贴上。
        placed = {}

        def _move(hwnd, x, y, w, h):
            placed[hwnd] = (int(x), int(y), int(w), int(h))
            return True

        ww.move_visible.side_effect = _move
        ww.visible_rect.side_effect = lambda hwnd: placed.get(hwnd)
        return patch('app.services.recording.session.winwindows', ww), ww

    def test_splits_work_area_with_no_gap(self):
        ctx, ww = self._patch()
        with ctx:
            session = make_session()
            self.assertTrue(session._layout_native())
        calls = ww.move_visible.call_args_list
        # 1920 * 0.45 = 864 给面板，剩下 1056 给被测页面；两个 x 首尾相接
        self.assertEqual(calls[0].args, (222, 0, 0, 1056, 1040))
        self.assertEqual(calls[1].args, (111, 1056, 0, 864, 1040))

    def test_uses_work_area_not_full_screen(self):
        """副屏、任务栏：起点不一定是 0，高度也不能用整块屏幕。"""
        ctx, ww = self._patch(work=(1920, 0, 1600, 900))
        with ctx:
            session = make_session()
            session._layout_native()
        calls = ww.move_visible.call_args_list
        self.assertEqual(calls[0].args[:3], (222, 1920, 0))
        self.assertEqual(calls[1].args[1], 1920 + 880)  # 1600 - 720

    def test_panel_keeps_a_floor_on_narrow_screens(self):
        ctx, ww = self._patch(work=(0, 0, 800, 600))
        with ctx:
            session = make_session()
            session._layout_native()
        page, panel = ww.move_visible.call_args_list
        self.assertGreaterEqual(panel.args[3], 320)

    def test_no_panel_window_does_nothing(self):
        ctx, ww = self._patch()
        with ctx:
            session = make_session(panel_hwnd=0)
            self.assertFalse(session._layout_native())
        ww.move_visible.assert_not_called()

    def test_headless_never_touches_windows(self):
        ctx, ww = self._patch()
        with ctx:
            session = make_session(headless=True)
            self.assertFalse(session._layout_native())
        ww.move_visible.assert_not_called()

    def test_layout_starts_glue(self):
        ctx, ww = self._patch()
        ww.Glue.return_value.start.return_value = True
        with ctx:
            session = make_session()
            session._layout_native()
        self.assertIsNotNone(session._glue)
        self.assertEqual(ww.Glue.call_args.args, (222, 111))

    def test_glue_failure_keeps_session_usable(self):
        """装不上钩子（系统拒绝、窗口已关）只是不能粘，录制照旧。"""
        ctx, ww = self._patch()
        ww.Glue.return_value.start.return_value = False
        with ctx:
            session = make_session()
            self.assertTrue(session._layout_native())
        self.assertIsNone(session._glue)

    def test_restores_maximized_windows_before_laying_out(self):
        """最大化过的窗口要先还原：否则整条原生路径会被跳过，只剩够不着面板的 CDP 兜底。"""
        ctx, ww = self._patch()
        with ctx:
            session = make_session()
            session._layout_native()
        states = [c.args for c in ww.prepare.call_args_list]
        self.assertIn((111,), states)   # 面板
        self.assertIn((222,), states)   # 被测页面


class RelayoutFallbackTest(unittest.TestCase):
    """原生路径失败时的兜底。

    无壳面板是另一个进程的窗口，CDP 摆不动它。这条兜底一旦照旧返回 True，
    用户看到的就是「点完并排，只有左边动了一下，右边空一大块」+「已重新并排」。
    """

    def _patch(self, moved):
        ww = MagicMock()
        ww.available.return_value = True
        ww.work_area.return_value = (0, 0, 1920, 1040)
        ww.move_visible.return_value = moved
        ww.is_usable.return_value = True
        ww.prepare.return_value = True
        return patch('app.services.recording.session.winwindows', ww), ww

    def test_shell_less_panel_never_reports_false_success(self):
        ctx, ww = self._patch(moved=False)
        with ctx:
            session = make_session()
            session._context = MagicMock()
            session._embed = False
            session._panel_page = None
            with patch.object(session, '_set_window_bounds') as bounds:
                self.assertFalse(session.layout_windows())
        # 兜底那条路一步都不该走：它只会挪被测窗口，面板原地不动。
        bounds.assert_not_called()


class PlanFollowTest(unittest.TestCase):
    """拖哪条边，对面怎么动。

    中间那条缝既是 A 的右边也是 B 的左边，用户会当成一根分隔条来拖；
    这时候只有「靠缝那侧伸缩、外侧边钉死」才符合直觉。整窗口挪动则是照搬位移。
    """

    LEFT = (0, 0, 1000, 800)          # 被测窗口
    RIGHT = (1000, 0, 800, 800)       # 面板

    def test_moving_whole_window_translates_the_other(self):
        target, fix = plan_follow(self.LEFT, (120, 40, 1000, 800), self.RIGHT, src_is_left=True)
        self.assertEqual(target, (1120, 40, 800, 800))
        self.assertIsNone(fix)

    def test_dragging_seam_right_keeps_outer_edge(self):
        """拖 A 的右边：A 变宽、B 从左边缩，B 的右边一点都不能动。"""
        target, fix = plan_follow(self.LEFT, (0, 0, 1120, 800), self.RIGHT, src_is_left=True)
        self.assertEqual(target, (1120, 0, 680, 800))
        self.assertEqual(target[0] + target[2], 1800)   # 外边界原地不动
        self.assertEqual(target[0], 1120)               # 贴着接缝
        self.assertIsNone(fix)

    def test_dragging_seam_left_keeps_outer_edge(self):
        target, _ = plan_follow(self.LEFT, (0, 0, 880, 800), self.RIGHT, src_is_left=True)
        self.assertEqual(target, (880, 0, 920, 800))
        self.assertEqual(target[0] + target[2], 1800)

    def test_dragging_seam_from_the_right_window(self):
        """拖 B 的左边：B 变宽、A 从右边缩，A 的左边不动。"""
        target, _ = plan_follow(self.RIGHT, (900, 0, 900, 800), self.LEFT, src_is_left=False)
        self.assertEqual(target, (0, 0, 900, 800))
        self.assertEqual(target[0], 0)                  # 外边界原地不动
        self.assertEqual(target[0] + target[2], 900)    # 贴着接缝

    def test_dragging_the_outer_edge_changes_nothing(self):
        """拖 A 的左边（外边界）：接缝没动，B 就不该动。"""
        target, _ = plan_follow(self.LEFT, (-100, 0, 1100, 800), self.RIGHT, src_is_left=True)
        self.assertEqual(target, self.RIGHT)

    def test_seam_stops_when_the_other_side_hits_the_floor(self):
        """A 拖到把 B 压死：B 卡在下限，A 的右边被推回来，接缝不能裂。"""
        target, fix = plan_follow(self.LEFT, (0, 0, 1700, 800), self.RIGHT,
                                  src_is_left=True, min_w=360)
        self.assertEqual(target, (1440, 0, 360, 800))
        self.assertEqual(fix, (0, 0, 1440, 800))        # A 的右边退到 1440
        self.assertEqual(fix[0] + fix[2], target[0])    # 依旧严丝合缝

    def test_seam_stops_from_the_right_window_too(self):
        target, fix = plan_follow(self.RIGHT, (0, 0, 1800, 800), self.LEFT,
                                  src_is_left=False, min_w=360)
        self.assertEqual(target, (0, 0, 360, 800))
        self.assertEqual(fix, (360, 0, 1440, 800))
        self.assertEqual(fix[0], target[0] + target[2])

    def test_height_mirrors_so_the_pair_stays_a_rectangle(self):
        target, _ = plan_follow(self.LEFT, (0, 0, 1000, 900), self.RIGHT, src_is_left=True)
        self.assertEqual(target, (1000, 0, 800, 900))


if __name__ == '__main__':
    unittest.main()
