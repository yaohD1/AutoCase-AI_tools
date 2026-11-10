# -*- coding: utf-8 -*-
"""悬浮面板的摆位规则。

和左右分屏的区别只有一句话：**页面不吃亏**——被测窗口拿满整个工作区，
面板作为置顶窄条浮在右侧，宽度按内容定（默认 420），不按屏幕比例分。
真正的窗口挪动在 Win32 那边，测试只验「该挪到哪」和「什么时候不挪」。
"""
import unittest
from unittest.mock import MagicMock, patch

from app.services.recording.session import RecordingSession


def make_session(panel_hwnd=111, main_hwnd=222, width=420, hidden=False, headless=False):
    session = RecordingSession.__new__(RecordingSession)
    session._embed = False
    session._headless = headless
    session._context = object()
    session._panel_hwnd = panel_hwnd
    session._main_hwnd = main_hwnd
    session._panel_float = True
    session._panel_width = width
    session._panel_hidden = hidden
    session._panel_ratio = 0.45
    session._glue = None
    return session


class LayoutFloatTest(unittest.TestCase):
    def _patch(self, work=(0, 0, 2560, 1400)):
        ww = MagicMock()
        ww.available.return_value = True
        ww.work_area.return_value = work
        ww.prepare.return_value = True
        ww.show_window.return_value = True
        ww.move_visible.return_value = True
        ww.set_topmost.return_value = True
        ww.scale.return_value = 1.0
        return patch('app.services.recording.session.winwindows', ww), ww

    def test_page_keeps_full_width(self):
        ctx, ww = self._patch()
        with ctx:
            session = make_session()
            self.assertTrue(session._layout_float())
        calls = {c.args[0]: c.args for c in ww.move_visible.call_args_list}
        # 被测页面：整块工作区，一点不让
        self.assertEqual(calls[222], (222, 0, 0, 2560, 1400))
        # 面板：右侧 420 宽，上下各留 12
        self.assertEqual(calls[111], (111, 2560 - 420 - 12, 12, 420, 1400 - 24))

    def test_panel_is_topmost(self):
        ctx, ww = self._patch()
        with ctx:
            make_session()._layout_float()
        ww.set_topmost.assert_called_with(111, True)

    def test_narrow_screen_shrinks_panel_not_page(self):
        ctx, ww = self._patch(work=(0, 0, 1200, 900))
        with ctx:
            make_session()._layout_float()
        calls = {c.args[0]: c.args for c in ww.move_visible.call_args_list}
        self.assertEqual(calls[222][3], 1200)          # 页面仍是全宽
        self.assertEqual(calls[111][3], 400)           # 面板退到 1/3 屏

    def test_wide_panel_is_capped_so_page_stays_usable(self):
        ctx, ww = self._patch()
        with ctx:
            make_session(width=900)._layout_float()
        calls = {c.args[0]: c.args for c in ww.move_visible.call_args_list}
        # 2560 - 900 仍给页面留了 1660，上限就是 900
        self.assertEqual(calls[111][3], 900)

    def test_hidden_panel_is_left_to_the_pair_layout(self):
        """收起时不走悬浮：悬浮没有把手，把窗口藏起来就再也叫不回来了。"""
        ctx, ww = self._patch()
        with ctx:
            self.assertFalse(make_session(hidden=True)._layout_float())
        ww.move_visible.assert_not_called()

    def test_headless_and_embed_do_nothing(self):
        for kwargs in ({'headless': True},):
            ctx, ww = self._patch()
            with ctx:
                self.assertFalse(make_session(**kwargs)._layout_float())
            ww.move_visible.assert_not_called()
        session = make_session()
        session._embed = True
        with self._patch()[0]:
            self.assertFalse(session._layout_float())


class LayoutDispatchTest(unittest.TestCase):
    def test_float_first_then_native(self):
        session = make_session()
        with patch.object(session, '_layout_float', return_value=True) as floated, \
                patch.object(session, '_layout_native', return_value=True) as tiled:
            self.assertTrue(session.layout_windows())
        floated.assert_called_once()
        tiled.assert_not_called()

    def test_falls_back_to_tiling_when_float_fails(self):
        session = make_session()
        with patch.object(session, '_layout_float', return_value=False), \
                patch.object(session, '_layout_native', return_value=True) as tiled:
            self.assertTrue(session.layout_windows())
        tiled.assert_called_once()

    def test_embed_never_lays_out(self):
        session = make_session()
        session._embed = True
        with patch.object(session, '_layout_float') as floated:
            self.assertFalse(session.layout_windows())
        floated.assert_not_called()


class PanelModeTest(unittest.TestCase):
    def test_hidden_mode_hides_window_and_keeps_page(self):
        session = make_session()
        with patch.object(session, 'layout_windows', return_value=True), \
                patch.object(session, '_push_hud') as hud, \
                patch('app.services.recording.session.winwindows') as ww:
            ww.show_window.return_value = True
            ww.move_visible.return_value = True
            result = session._set_panel_mode_now(True)
        self.assertTrue(result['panel_hidden'])
        self.assertTrue(session._panel_hidden)
        hud.assert_called_once()

    def test_expanded_mode_clears_flag(self):
        session = make_session(hidden=True)
        with patch.object(session, 'layout_windows', return_value=True), \
                patch.object(session, '_push_hud'):
            session._set_panel_mode_now(False)
        self.assertFalse(session._panel_hidden)

    def test_bad_mode_is_rejected(self):
        from app.services.recording.session import RecordingError
        with self.assertRaises(RecordingError):
            make_session().set_panel_mode('capsule')


if __name__ == '__main__':
    unittest.main()
