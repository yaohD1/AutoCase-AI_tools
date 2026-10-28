# -*- coding: utf-8 -*-
"""任务书浮层的窗口形态：铺宽读文档 / 关掉摆回并排。

浮层本身在浏览器里，这里只管「面板窗口够不够宽」这一半：
- panel=1 时面板窗口只有屏幕的 45%，96vw 也大不过窗口，必须把窗口自己铺开；
- embed 模式没有独立面板窗口，一律返回 False，前端据此什么都不做；
- 窗口调不动（系统拒绝、浏览器已关）不能冒泡成错误——用户在看任务书，
  窗口宽窄是增强，不是前置条件。
"""
import unittest

from app.services.recording.session import RecordingSession


class FakePage:
    def __init__(self, size=None, error=None):
        self.size = size or {'w': 1920, 'h': 1080}
        self.error = error

    def evaluate(self, _js):
        if self.error:
            raise self.error
        return self.size


class SheetWindowsTest(unittest.TestCase):
    def make_session(self, panel_page=None, embed=False):
        session = RecordingSession.__new__(RecordingSession)
        session._context = object()
        session._embed = embed
        session._panel_page = panel_page
        session.bounds = []
        session.laid_out = 0
        session._set_window_bounds = lambda page, bounds: session.bounds.append((page, bounds))

        def fake_layout():
            session.laid_out += 1
            return True

        session.layout_windows = fake_layout
        return session

    def test_focus_centers_and_caps_width(self):
        page = FakePage()
        session = self.make_session(panel_page=page)
        self.assertTrue(session.sheet_windows(True))
        self.assertEqual(len(session.bounds), 1)
        target, bounds = session.bounds[0]
        self.assertIs(target, page)
        # 1920 宽的屏幕上不必铺满：留一圈桌面边缘，仍看得出是原来那个面板窗口。
        self.assertEqual(bounds['width'], 1280)
        self.assertEqual(bounds['height'], 1020)
        self.assertEqual(bounds['left'], (1920 - 1280) // 2)
        self.assertEqual(bounds['top'], (1080 - 1020) // 2)

    def test_small_screen_still_gets_a_readable_width(self):
        page = FakePage(size={'w': 1000, 'h': 700})
        session = self.make_session(panel_page=page)
        self.assertTrue(session.sheet_windows(True))
        bounds = session.bounds[0][1]
        # 1000 - 60 = 940：不铺到屏幕边缘，但比并排时的 45%（450）宽得多。
        self.assertEqual(bounds['width'], 940)
        self.assertEqual(bounds['height'], 640)

    def test_width_never_drops_below_floor(self):
        """极窄屏（比如投屏 / 远程桌面）也要给 720，否则两栏排不开。"""
        page = FakePage(size={'w': 700, 'h': 600})
        session = self.make_session(panel_page=page)
        session.sheet_windows(True)
        self.assertEqual(session.bounds[0][1]['width'], 720)

    def test_close_restores_side_by_side(self):
        session = self.make_session(panel_page=FakePage())
        self.assertTrue(session.sheet_windows(False))
        self.assertEqual(session.laid_out, 1)
        self.assertEqual(session.bounds, [])

    def test_no_panel_window_is_a_noop(self):
        session = self.make_session(panel_page=None)
        self.assertFalse(session.sheet_windows(True))
        self.assertFalse(session.sheet_windows(False))
        self.assertEqual(session.bounds, [])
        self.assertEqual(session.laid_out, 0)

    def test_embed_mode_is_a_noop(self):
        """嵌入模式只有一个窗口，没有「把面板铺宽」这回事。"""
        session = self.make_session(panel_page=FakePage(), embed=True)
        self.assertFalse(session.sheet_windows(True))
        self.assertEqual(session.bounds, [])

    def test_browser_error_does_not_raise(self):
        session = self.make_session(panel_page=FakePage(error=RuntimeError('窗口已关闭')))
        self.assertFalse(session.sheet_windows(True))
        self.assertEqual(session.bounds, [])

    def test_zero_size_screen_is_ignored(self):
        session = self.make_session(panel_page=FakePage(size={'w': 0, 'h': 0}))
        self.assertFalse(session.sheet_windows(True))
        self.assertEqual(session.bounds, [])


if __name__ == '__main__':
    unittest.main()
