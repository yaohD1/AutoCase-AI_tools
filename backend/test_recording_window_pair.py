# -*- coding: utf-8 -*-
"""两窗口并排的尺寸计算。

真正的「把窗口挪过去」在 Win32 那边，测试环境没有桌面，只能测到「该挪到哪」：
  1. 按显示器工作区切，不是按整块屏幕（扣掉任务栏、多屏也对）；
  2. 两个窗口之间不留缝——留了就是一条露出桌面的黑缝；
  3. 面板宽度兜底，屏幕再窄也别把面板压成一条；
  4. 无头环境 / 没有面板窗口时一律不动手，别在测试机上弹出真窗口。
"""
import time
import unittest
from unittest.mock import MagicMock, patch

from app.services.recording.session import RecordingSession
from app.services.recording.winwindows import plan_follow


def make_session(panel_hwnd=111, main_hwnd=222, ratio=0.45, headless=False,
                 width=520, hidden=False):
    session = RecordingSession.__new__(RecordingSession)
    session._context = object()
    session._embed = False
    session._headless = headless
    session._panel_hwnd = panel_hwnd
    session._main_hwnd = main_hwnd
    session._panel_ratio = ratio
    session._panel_width = width
    session._panel_hidden = hidden
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
        ww.scale.return_value = 1.0
        return patch('app.services.recording.session.winwindows', ww), ww

    def test_raises_both_windows_after_placing(self):
        """摆完必须把窗口提到最前。

        `move_visible` 用的是 SWP_NOZORDER | SWP_NOACTIVATE——只改坐标不动层级，
        桌面上只要有全屏窗口，刚启动的录制窗口就整块躺在它下面（用户实测「点开始
        后浏览器不打开」就是这么来的）。焦点给被测页面，面板只提层级。
        """
        ctx, ww = self._patch()
        with ctx:
            session = make_session()
            self.assertTrue(session._layout_native())
        ww.activate.assert_called_once_with(222)
        ww.bring_to_top.assert_called_once_with(111)
        names = [c[0] for c in ww.method_calls]
        self.assertLess(names.index('activate'), names.index('bring_to_top'))

    def test_focus_returns_to_page_after_panel_loads(self):
        """面板窗口加载完会抢焦点，隔一拍得把焦点还给被测页面。"""
        ctx, ww = self._patch()
        with ctx:
            session = make_session()
            session._raise_after_panel(delay=0.01)
            time.sleep(0.15)
        ww.activate.assert_called_with(222)

    def test_splits_work_area_with_no_gap(self):
        ctx, ww = self._patch()
        with ctx:
            session = make_session()
            self.assertTrue(session._layout_native())
        calls = ww.move_visible.call_args_list
        # 面板按 520 CSS（dpr=1）折成 520 物理，剩下 1400 给被测页面；两个 x 首尾相接
        self.assertEqual(calls[0].args, (222, 0, 0, 1400, 1040))
        self.assertEqual(calls[1].args, (111, 1400, 0, 520, 1040))

    def test_uses_work_area_not_full_screen(self):
        """副屏、任务栏：起点不一定是 0，高度也不能用整块屏幕。"""
        ctx, ww = self._patch(work=(1920, 0, 1600, 900))
        with ctx:
            session = make_session()
            session._layout_native()
        calls = ww.move_visible.call_args_list
        self.assertEqual(calls[0].args[:3], (222, 1920, 0))
        self.assertEqual(calls[1].args[1], 1920 + 1080)  # 1600 - 520

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


class PanelWidthByCssTest(unittest.TestCase):
    """面板宽度按 CSS 折成物理像素，和嵌入模式那块面板同一口径。

    嵌入模式里面板就是 420~600 CSS 宽；并排时如果直接拿物理像素当 CSS 用，
    在 175% 缩放的机器上面板会窄掉将近一半，两边看着完全不是一回事。
    """

    def _patch(self, work=(0, 0, 2560, 1370), dpr=1.75):
        ww = MagicMock()
        ww.available.return_value = True
        ww.work_area.return_value = work
        ww.is_usable.return_value = True
        ww.prepare.return_value = True
        ww.move_visible.return_value = True
        ww.visible_rect.return_value = None
        ww.scale.return_value = dpr
        return patch('app.services.recording.session.winwindows', ww), ww

    def test_css_width_is_scaled_to_physical_pixels(self):
        ctx, ww = self._patch()
        with ctx:
            session = make_session(width=520)
            session._layout_native()
        panel = ww.move_visible.call_args_list[1].args
        self.assertEqual(panel[3], 910)          # 520 CSS × 1.75
        self.assertEqual(panel[1], 2560 - 910)   # 贴着页面右边界

    def test_page_keeps_a_minimum_css_width(self):
        """屏窄到放不下两栏就压面板：页面至少留 520 CSS，否则被测系统直接变移动版。"""
        ctx, ww = self._patch(work=(0, 0, 1400, 900), dpr=1.75)
        with ctx:
            session = make_session(width=520)
            session._layout_native()
        page, panel = ww.move_visible.call_args_list[:2]
        self.assertGreaterEqual(page.args[3], int(520 * 1.75))
        self.assertEqual(page.args[3] + panel.args[3], 1400)

    def test_glue_replaces_the_old_pair(self):
        """重新摆位要先把旧钩子拆掉，否则两套钩子互相打架。"""
        ctx, ww = self._patch()
        ww.Glue.return_value.start.return_value = True
        with ctx:
            session = make_session()
            session._layout_native()
            session._layout_native()
        self.assertEqual(ww.Glue.call_count, 2)

    def test_width_change_relayouts_the_pair(self):
        """「收起行 / 展开行」改宽度走的就是这条路：重摆后两窗仍然首尾相接。

        嵌入模式那边改宽度就是改 CSS，一瞬间的事；并排这边是一扇真窗口，
        只能重摆一次——两边行为要一样，用户才不会觉得切了布局就换了套东西。
        """
        ctx, ww = self._patch()
        with ctx:
            session = make_session(width=600)
            with patch.object(session, '_push_hud'):
                session._set_panel_width_now(420)
        page, panel = ww.move_visible.call_args_list[-2:]
        self.assertEqual(page.args[3] + panel.args[3], 2560)
        self.assertEqual(panel.args[1], page.args[3])
        self.assertEqual(panel.args[3], int(round(420 * 1.75)))


class PanelHandleTest(unittest.TestCase):
    """收起 = 整扇窗口滑到屏幕右缘外，只留一条把手。

    Chromium 有最小窗口宽（实测 514 CSS），窗口本身缩不到 44px，所以只能「挪出去」。
    页面要把这条让出来，否则把手会压在页面最右边那 44px 上，点什么都点不着。
    """

    def _patch(self, work=(0, 0, 2560, 1370)):
        ww = MagicMock()
        ww.available.return_value = True
        ww.work_area.return_value = work
        ww.is_usable.return_value = True
        ww.prepare.return_value = True
        ww.move_visible.return_value = True
        ww.visible_rect.return_value = None
        ww.scale.return_value = 1.0
        return patch('app.services.recording.session.winwindows', ww), ww

    def test_collapsed_panel_leaves_a_peek_on_the_right_edge(self):
        ctx, ww = self._patch()
        with ctx:
            session = make_session(hidden=True)
            self.assertTrue(session._layout_native())
        page, panel = ww.move_visible.call_args_list[:2]
        self.assertEqual(page.args, (222, 0, 0, 2560 - 36, 1370))
        self.assertEqual(panel.args[1], 2560 - 36)   # 窗口左边界就是把手
        self.assertEqual(panel.args[0], 111)
        # 整扇窗口再上移一个标题栏：不然那一条顶端会露半个标题栏
        self.assertEqual(panel.args[2], -34)
        self.assertEqual(panel.args[4], 1370 + 34)

    def test_collapsed_does_not_glue(self):
        """收起后把手钉在屏幕边上，拖页面窗口不该把它带走。"""
        ctx, ww = self._patch()
        ww.Glue.return_value.start.return_value = True
        with ctx:
            session = make_session(hidden=True)
            session._layout_native()
        ww.Glue.assert_not_called()

    def test_expanding_restores_the_pair(self):
        ctx, ww = self._patch()
        with ctx:
            session = make_session(hidden=True)
            session._layout_native()
            session._panel_hidden = False
            ww.move_visible.reset_mock()
            session._layout_native()
        page, panel = ww.move_visible.call_args_list[:2]
        self.assertEqual(panel.args[1], page.args[3])   # 首尾重新贴住
        self.assertEqual(page.args[3] + panel.args[3], 2560)

    def test_peek_never_eats_the_whole_screen(self):
        """极窄屏（工作区 400）也得给页面留位置，把手按屏幕宽度的 5% 收。"""
        ctx, ww = self._patch(work=(0, 0, 400, 600))
        with ctx:
            session = make_session(hidden=True)
            session._layout_native()
        page = ww.move_visible.call_args_list[0].args
        self.assertGreaterEqual(page[3], 400 - 24)


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
        ww.scale.return_value = 1.0
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
