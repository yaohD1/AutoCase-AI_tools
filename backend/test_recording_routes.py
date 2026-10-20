import http.server
import os
import tempfile
import threading
import unittest
from unittest import mock

# 用临时数据库启动应用，避免测试改动真实的 storage/app.db。必须在导入 app 之前设置。
_TMP = tempfile.mkdtemp(prefix='recording-routes-')
os.environ['DATABASE_URL'] = 'sqlite:///' + os.path.join(_TMP, 'test.db').replace('\\', '/')

from app import create_app  # noqa: E402
from app.services.opencode_runner import OpenCodeRunner  # noqa: E402
from app.services.recording.session import RecordingSession  # noqa: E402

PAGE = (
    '<!doctype html><html><head><meta charset="utf-8"><title>录制测试</title></head>'
    '<body><input placeholder="用户名"></body></html>'
).encode('utf-8')
MODEL = 'volcengine-plan/deepseek-v4-flash'
START = '/api/automation/recording/start'


class _PageHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *args):
        pass


def setUpModule():
    global _SERVER, _BASE
    _SERVER = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _PageHandler)
    threading.Thread(target=_SERVER.serve_forever, daemon=True).start()
    _BASE = 'http://127.0.0.1:%d/index.html' % _SERVER.server_address[1]


def tearDownModule():
    _SERVER.shutdown()
    _SERVER.server_close()


class RecordingRoutesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config['TESTING'] = True
        cls.app.config['RECORDING_HEADLESS'] = True
        cls.client = cls.app.test_client()

    def setUp(self):
        RecordingSession._current = None

    def tearDown(self):
        session = RecordingSession.current()
        if session is not None and session.status in ('starting', 'recording'):
            try:
                session.cancel()
            except Exception:
                pass

    def _start(self):
        r = self.client.post(START, json={'base_url': _BASE})
        self.assertEqual(r.status_code, 201, r.get_json())
        return r.get_json()

    def _stop(self):
        return self.client.post('/api/automation/recording/stop')

    def test_start_rejects_non_http_url(self):
        r = self.client.post(START, json={'base_url': 'file:///etc/passwd'})
        self.assertEqual(r.status_code, 400)

    def test_start_is_exclusive_and_stop_is_final(self):
        self._start()
        busy = self.client.post(START, json={'base_url': _BASE})
        self.assertEqual(busy.status_code, 409)

        events = self.client.get('/api/automation/recording/events?after=-1').get_json()
        self.assertEqual(events['status'], 'recording')
        self.assertEqual(events['cursor'], -1)

        stopped = self._stop().get_json()
        self.assertEqual(stopped['status'], 'stopped')
        self.assertEqual(self._stop().status_code, 409)

    def test_pick_toggles_on_active_session(self):
        self._start()
        on = self.client.post('/api/automation/recording/pick', json={'on': True})
        self.assertEqual(on.status_code, 200)
        self.assertTrue(on.get_json()['pick'])
        off = self.client.post('/api/automation/recording/pick', json={'on': False})
        self.assertFalse(off.get_json()['pick'])
        self._stop()

    def test_sleep_step_keeps_user_text_and_insert_position(self):
        self._start()
        r = self.client.post('/api/automation/recording/steps',
                             json={'intent': {'type': 'sleep', 'seconds': 1}, 'text': '等 1 秒'})
        self.assertEqual(r.status_code, 200, r.get_json())
        last = r.get_json()['steps'][-1]
        self.assertEqual((last['type'], last['text']), ('sleep', '等 1 秒'))

        inserted = self.client.post('/api/automation/recording/steps',
                                    json={'intent': {'type': 'sleep', 'seconds': 2}, 'text': '中间', 'after_no': 1})
        steps = inserted.get_json()['steps']
        self.assertEqual(steps[1]['text'], '中间')
        self.assertEqual([s['no'] for s in steps], list(range(1, len(steps) + 1)))
        self._stop()

    def test_invalid_intent_is_rejected_before_writing(self):
        self._start()
        before = len(self.client.get('/api/automation/recording/current').get_json()['steps'])
        unknown = self.client.post('/api/automation/recording/steps', json={'intent': {'type': 'screenshot'}})
        self.assertEqual(unknown.status_code, 400)
        missing_alias = self.client.post('/api/automation/recording/steps',
                                         json={'intent': {'type': 'visible', 'target': '不存在'}})
        self.assertEqual(missing_alias.status_code, 400)
        after = len(self.client.get('/api/automation/recording/current').get_json()['steps'])
        self.assertEqual(before, after)
        self._stop()

    def test_delete_missing_step_is_conflict(self):
        self._start()
        self.assertEqual(self.client.delete('/api/automation/recording/steps/99').status_code, 409)
        self._stop()

    def test_intent_parse_requires_model_choice(self):
        self._start()
        r = self.client.post('/api/automation/recording/intent', json={'text': '等 1 秒'})
        self.assertEqual(r.status_code, 400)
        self._stop()

    def test_intent_parse_returns_preview_without_writing_step(self):
        self._start()
        before = len(self.client.get('/api/automation/recording/current').get_json()['steps'])
        with mock.patch.object(OpenCodeRunner, 'ask_text',
                               return_value='{"type": "url_contains", "value": "index"}') as ask:
            r = self.client.post('/api/automation/recording/intent', json={'text': 'URL 包含 index', 'model': MODEL})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()['intent'], {'type': 'url_contains', 'target': None, 'value': 'index', 'seconds': None})
        self.assertEqual(r.get_json()['model'], MODEL)
        self.assertEqual(ask.call_args[0][0], MODEL)
        after = len(self.client.get('/api/automation/recording/current').get_json()['steps'])
        self.assertEqual(before, after)
        self._stop()

    def test_intent_parse_reports_unusable_model_reply(self):
        self._start()
        with mock.patch.object(OpenCodeRunner, 'ask_text', return_value='抱歉，我无法回答'):
            r = self.client.post('/api/automation/recording/intent', json={'text': '可见', 'model': MODEL})
        self.assertEqual(r.status_code, 422)
        self._stop()

    def test_intent_parse_reports_opencode_failure(self):
        self._start()
        with mock.patch.object(OpenCodeRunner, 'ask_text', side_effect=RuntimeError('serve 未启动')):
            r = self.client.post('/api/automation/recording/intent', json={'text': '可见', 'model': MODEL})
        self.assertEqual(r.status_code, 502)
        self.assertIn('serve 未启动', r.get_json()['error'])
        self._stop()

    def test_no_session_is_not_found(self):
        self.assertEqual(self.client.get('/api/automation/recording/current').status_code, 404)
        self.assertEqual(self.client.get('/api/automation/recording/events').status_code, 404)
        self.assertEqual(self.client.post('/api/automation/recording/steps', json={}).status_code, 404)
        self.assertEqual(self.client.post('/api/automation/recording/intent', json={}).status_code, 404)


if __name__ == '__main__':
    unittest.main()
