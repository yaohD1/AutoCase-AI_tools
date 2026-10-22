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


if __name__ == '__main__':
    unittest.main()
