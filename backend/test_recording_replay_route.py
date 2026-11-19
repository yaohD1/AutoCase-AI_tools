import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TMP = tempfile.mkdtemp(prefix='replay-route-')
os.environ.setdefault('DATABASE_URL', 'sqlite:///' + os.path.join(_TMP, 'test.db').replace(chr(92), chr(47)))
Path(os.environ.get('AUTOMATION_WORKSPACE_ROOT') or os.path.join(_TMP, 'workspaces')).mkdir(
    parents=True, exist_ok=True)

from app import create_app  # noqa: E402
from app.services.recording.session import RecordingSession  # noqa: E402

REPLAY = '/api/automation/recording/replay'
STEPS = [{'no': 1, 'type': 'goto', 'url': 'http://x/'}]


class ReplayRouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config['TESTING'] = True
        cls.client = cls.app.test_client()

    def setUp(self):
        RecordingSession._current = None

    def _session(self, status='stopped', steps=None):
        session = RecordingSession.__new__(RecordingSession)
        session.status = status
        session.ir = lambda: list(STEPS if steps is None else steps)
        RecordingSession._current = session
        return session

    def _post(self, **body):
        return self.client.post(REPLAY, json=body)

    def test_no_session_is_not_found(self):
        self.assertEqual(self._post().status_code, 404)

    def test_recording_must_be_stopped_first(self):
        self._session(status='recording')
        self.assertEqual(self._post().status_code, 409)

    def test_unknown_framework_rejected(self):
        self._session()
        self.assertEqual(self._post(framework='selenium-java').status_code, 400)

    def test_empty_steps_rejected(self):
        self._session(steps=[])
        self.assertEqual(self._post().status_code, 400)

    def test_framework_reaches_the_replayer(self):
        self._session()
        report = {'ok': True, 'done': 1, 'total': 1, 'steps': [], 'error': None, 'failed_no': None}
        with mock.patch('app.routes.recording.replay.replay_checked', return_value=report) as called:
            resp = self._post(framework='robot-selenium')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(called.call_args.kwargs['framework'], 'robot-selenium')
        self.assertEqual(resp.get_json()['ok'], True)

    def test_defaults_to_playwright(self):
        self._session()
        report = {'ok': True, 'done': 1, 'total': 1, 'steps': [], 'error': None, 'failed_no': None}
        with mock.patch('app.routes.recording.replay.replay_checked', return_value=report) as called:
            self._post()
        self.assertEqual(called.call_args.kwargs['framework'], 'playwright-ts')

    def test_failed_replay_is_still_200_with_report(self):
        # 回放失败是「录制本身的问题」，不是接口错误：前端要拿到哪一步失败，所以仍返回 200。
        self._session()
        report = {'ok': False, 'done': 0, 'total': 1, 'steps': [], 'error': '第 1 步失败：boom',
                  'failed_no': 1, 'failed_type': 'goto'}
        with mock.patch('app.routes.recording.replay.replay_checked', return_value=report):
            resp = self._post()
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.get_json()['ok'])
        self.assertEqual(resp.get_json()['failed_no'], 1)

    def test_browser_failure_is_500(self):
        self._session()
        with mock.patch('app.routes.recording.replay.replay_checked',
                        side_effect=RuntimeError('browser not installed')):
            resp = self._post()
        self.assertEqual(resp.status_code, 500)
        self.assertIn('browser not installed', resp.get_json()['error'])


OPEN_WINDOW = '/api/automation/recording/replay/window'
CLOSE_WINDOW = OPEN_WINDOW + '/close'


class ReplayWindowRouteTest(unittest.TestCase):
    """独立回放窗口：录制页不动，所以入口/出口都走服务端。"""

    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config['TESTING'] = True
        cls.client = cls.app.test_client()

    def setUp(self):
        RecordingSession._current = None

    def _session(self, status='stopped', browser=True):
        session = RecordingSession.__new__(RecordingSession)
        session.status = status
        session._browser = mock.Mock() if browser else None
        session.open_replay_window = mock.Mock(return_value={'open': True})
        session.close_replay_window = mock.Mock(return_value={'open': False})
        RecordingSession._current = session
        return session

    def test_no_session_is_not_found(self):
        self.assertEqual(self.client.post(OPEN_WINDOW, json={}).status_code, 404)

    def test_browser_closed_is_conflict(self):
        self._session(browser=False)
        resp = self.client.post(OPEN_WINDOW, json={})
        self.assertEqual(resp.status_code, 409)
        self.assertIn('浏览器已关闭', resp.get_json()['error'])

    def test_open_passes_framework(self):
        session = self._session()
        resp = self.client.post(OPEN_WINDOW, json={'framework': 'robot-selenium'})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()['open'])
        session.open_replay_window.assert_called_once_with('robot-selenium')

    def test_close_without_session_is_ok(self):
        resp = self.client.post(CLOSE_WINDOW)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.get_json()['open'])

    def test_close_calls_session(self):
        session = self._session()
        resp = self.client.post(CLOSE_WINDOW)
        self.assertEqual(resp.status_code, 200)
        session.close_replay_window.assert_called_once()


class ReplayUrlTest(unittest.TestCase):
    """回放页地址必须和录制页同源，且带上 window=1 让页面知道自己是受管窗口。"""

    def _session(self, panel_url='', embed_url=None, theme=None):
        session = RecordingSession.__new__(RecordingSession)
        session._panel_url = panel_url
        session._embed_url = embed_url
        session.framework = 'robot-selenium'
        if theme:
            session._theme = theme
        return session

    def test_uses_panel_origin_and_drops_its_query(self):
        session = self._session(panel_url='http://localhost:5173/recording?panel=1')
        self.assertEqual(
            session._replay_url('playwright-ts'),
            'http://localhost:5173/recording/replay?framework=playwright-ts&window=1')

    def test_falls_back_to_embed_url(self):
        session = self._session(embed_url='http://localhost:5173/recording?embed=1&target=x')
        url = session._replay_url('')
        self.assertIn('/recording/replay?', url)
        self.assertIn('framework=robot-selenium', url)
        self.assertIn('window=1', url)

    def test_no_frontend_url(self):
        self.assertIsNone(self._session()._replay_url('playwright-ts'))

    def test_carries_theme_of_recording_window(self):
        """回放窗口也是全新 profile：主题不带上就会退回浅色。"""
        session = self._session(panel_url='http://localhost:5173/recording?panel=1', theme='dark')
        self.assertIn('theme=dark', session._replay_url('playwright-ts'))

    def test_omits_theme_when_unknown(self):
        session = self._session(panel_url='http://localhost:5173/recording?panel=1')
        self.assertNotIn('theme=', session._replay_url('playwright-ts'))


class RecordingUrlThemeTest(unittest.TestCase):
    """面板 / 嵌入外壳都由后端另起浏览器，主题只能写在地址里。"""

    @classmethod
    def setUpClass(cls):
        cls.app = create_app()

    def _url(self, builder, theme):
        from app.routes.recording import _embed_url, _panel_url
        with self.app.app_context():
            func = _panel_url if builder == 'panel' else _embed_url
            return func({'theme': theme}, 'http://x/login')

    def test_panel_carries_theme(self):
        self.assertIn('theme=dark', self._url('panel', 'dark'))

    def test_embed_carries_theme(self):
        self.assertIn('theme=light', self._url('embed', 'light'))

    def test_invalid_theme_is_dropped(self):
        self.assertNotIn('theme=', self._url('panel', 'purple'))


if __name__ == '__main__':
    unittest.main()
