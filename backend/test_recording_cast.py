"""录屏帧环形缓冲：取帧规则与「没有帧」的边界。

集成测试（test_recording_shot）验证真实浏览器里点击跳转后拿到的是旧页面；
这里只验证纯逻辑，跑得快，出问题能一眼定位是取帧还是链路。
"""
import base64
import io
import unittest

from PIL import Image

from app.services.recording.session import RecordingSession, _PageCast

JPEG_SIGNATURE = b'\xff\xd8\xff'


def _jpeg(color=(20, 120, 90), size=(64, 48)):
    buf = io.BytesIO()
    Image.new('RGB', size, color).save(buf, format='JPEG', quality=80)
    return buf.getvalue()


class _FakeCdp:
    def __init__(self):
        self.sent = []

    def send(self, method, params=None):
        self.sent.append((method, params))


class PageCastPickTest(unittest.TestCase):
    def _cast(self, stamps):
        cast = _PageCast(_FakeCdp())
        for i, ts in enumerate(stamps):
            cast.frames.append((ts, 'data-%d' % i, {}))
        return cast

    def test_picks_the_last_frame_at_or_before_the_click(self):
        cast = self._cast([100.0, 100.5, 101.0])
        self.assertEqual(cast.pick(100_000)[1], 'data-0')
        self.assertEqual(cast.pick(100_400)[1], 'data-0')
        self.assertEqual(cast.pick(100_500)[1], 'data-1')
        self.assertEqual(cast.pick(100_900)[1], 'data-1')
        self.assertEqual(cast.pick(101_000)[1], 'data-2')
        self.assertEqual(cast.pick(999_000)[1], 'data-2')

    def test_nothing_before_the_first_frame(self):
        cast = self._cast([100.0])
        self.assertIsNone(cast.pick(99_000))

    def test_missing_or_zero_timestamp(self):
        cast = self._cast([100.0])
        self.assertIsNone(cast.pick(None))
        self.assertIsNone(cast.pick(0))

    def test_ring_keeps_only_the_latest_frames(self):
        cast = _PageCast(_FakeCdp())
        for i in range(30):
            cast.frames.append((100.0 + i, 'd%d' % i, {}))
        self.assertLessEqual(len(cast.frames), 10)
        self.assertEqual(cast.pick(129_000)[1], 'd29')


class PageCastFramesTest(unittest.TestCase):
    def test_frame_is_queued_then_acked_in_a_batch(self):
        cdp = _FakeCdp()
        cast = _PageCast(cdp)
        cast.on_frame({'metadata': {'timestamp': 100.0}, 'data': 'abc', 'sessionId': 's1'})
        self.assertEqual(len(cast.frames), 1)
        self.assertEqual(cdp.sent, [])
        cast.flush_acks()
        self.assertEqual(cdp.sent[-1][0], 'Page.screencastFrameAck')
        self.assertEqual(cdp.sent[-1][1], {'sessionId': 's1'})
        cast.flush_acks()
        self.assertEqual(len(cdp.sent), 1)

    def test_stop_clears_the_buffer(self):
        cdp = _FakeCdp()
        cast = _PageCast(cdp)
        cast.ok = True
        cast.frames.append((1.0, 'x', {}))
        cast.stop()
        self.assertFalse(cast.ok)
        self.assertEqual(len(cast.frames), 0)
        self.assertEqual([s[0] for s in cdp.sent], ['Page.stopScreencast'])


class CastShotTest(unittest.TestCase):
    def setUp(self):
        RecordingSession._current = None
        self.session = RecordingSession('http://example.test/')
        self.page = type('Page', (), {'main_frame': object()})()

    def tearDown(self):
        RecordingSession.dispose_current()

    def _attach(self, stamps):
        cast = _PageCast(_FakeCdp())
        for ts in stamps:
            cast.frames.append((ts, base64.b64encode(_jpeg()).decode(), {}))
        self.session._casts[id(self.page)] = cast
        return cast

    def test_takes_the_frame_and_uses_css_viewport_size(self):
        self._attach([100.0])
        shot = self.session._cast_shot(self.page, self.page.main_frame,
                                       {'_t': 100_500, '_vw': 800, '_vh': 600})
        self.assertEqual(shot['mime'], 'image/jpeg')
        self.assertEqual((shot['w'], shot['h']), (800, 600))
        self.assertTrue(shot['data'].startswith(JPEG_SIGNATURE))

    def test_no_cast_or_no_earlier_frame_returns_none(self):
        self.assertIsNone(self.session._cast_shot(self.page, self.page.main_frame,
                                                  {'_t': 100_500, '_vw': 800, '_vh': 600}))
        self._attach([100.0])
        self.assertIsNone(self.session._cast_shot(self.page, self.page.main_frame,
                                                  {'_t': 99_000, '_vw': 800, '_vh': 600}))
        self.assertIsNone(self.session._cast_shot(self.page, self.page.main_frame,
                                                  {'_t': None, '_vw': 800, '_vh': 600}))

    def test_register_shot_keeps_mime(self):
        self._attach([100.0])
        shot = self.session._cast_shot(self.page, self.page.main_frame,
                                       {'_t': 100_500, '_vw': 800, '_vh': 600})
        meta = self.session._register_shot(shot)
        self.assertEqual(meta['id'], 'shot-1')
        self.assertEqual(self.session._shots['shot-1']['mime'], 'image/jpeg')

    def test_error_meta_survives_without_bytes(self):
        meta = self.session._register_shot({'error': '录屏还没出帧'})
        self.assertEqual(meta, {'error': '录屏还没出帧'})


if __name__ == '__main__':
    unittest.main()
