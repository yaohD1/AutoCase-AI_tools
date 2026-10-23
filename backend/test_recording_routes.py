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
    '<body><input placeholder="用户名"><p>登录成功</p></body></html>'
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
    global _SERVER, _BASE, _ASK_ENV
    # 这些用例验证的是接口契约，不需要真的起一个 opencode 解析进程。
    _ASK_ENV = os.environ.get('RECORDING_ASK_DAEMON')
    os.environ['RECORDING_ASK_DAEMON'] = '0'
    _SERVER = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _PageHandler)
    threading.Thread(target=_SERVER.serve_forever, daemon=True).start()
    _BASE = 'http://127.0.0.1:%d/index.html' % _SERVER.server_address[1]


def tearDownModule():
    _SERVER.shutdown()
    _SERVER.server_close()
    if _ASK_ENV is None:
        os.environ.pop('RECORDING_ASK_DAEMON', None)
    else:
        os.environ['RECORDING_ASK_DAEMON'] = _ASK_ENV


class RecordingRoutesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config['TESTING'] = True
        cls.app.config['RECORDING_HEADLESS'] = True
        cls.client = cls.app.test_client()

    def setUp(self):
        # 上一轮可能是「停止但浏览器还开着」的会话，先收掉再开始。
        RecordingSession.dispose_current()
        RecordingSession._current = None

    def tearDown(self):
        session = RecordingSession.current()
        if session is not None:
            session.dispose()

    def _start(self):
        r = self.client.post(START, json={'base_url': _BASE})
        self.assertEqual(r.status_code, 201, r.get_json())
        return r.get_json()

    def _stop(self):
        return self.client.post('/api/automation/recording/stop')

    def _verify(self, intent):
        return self.client.post('/api/automation/recording/verify', json={'intent': intent})

    def test_start_rejects_non_http_url(self):
        r = self.client.post(START, json={'base_url': 'file:///etc/passwd'})
        self.assertEqual(r.status_code, 400)

    def test_start_is_exclusive_and_stop_is_idempotent(self):
        self._start()
        busy = self.client.post(START, json={'base_url': _BASE})
        self.assertEqual(busy.status_code, 409)

        events = self.client.get('/api/automation/recording/events?after=-1').get_json()
        self.assertEqual(events['status'], 'recording')
        self.assertEqual(events['cursor'], -1)

        stopped = self._stop().get_json()
        self.assertEqual(stopped['status'], 'stopped')
        # 停止只结束记录，浏览器留着：后面还要拾取元素、验证断言。
        self.assertTrue(stopped['browser_open'])
        self.assertEqual(self._stop().status_code, 200)

    def test_browser_lives_through_stop_until_closed(self):
        self._start()
        self._stop()
        picked = self.client.post('/api/automation/recording/pick', json={'on': True})
        self.assertEqual(picked.status_code, 200, picked.get_json())
        self.assertTrue(picked.get_json()['pick'])

        closed = self.client.post('/api/automation/recording/close')
        self.assertEqual(closed.status_code, 200, closed.get_json())
        self.assertEqual(closed.get_json()['status'], 'stopped')
        self.assertFalse(closed.get_json()['browser_open'])
        # 关掉之后不能再操作，也不能再验证
        self.assertEqual(self.client.post('/api/automation/recording/pick', json={'on': True}).status_code, 409)
        self.assertEqual(
            self._verify({'type': 'page_text_contains', 'value': '登录成功'}).status_code, 409)

    def test_verify_reads_the_current_page(self):
        self._start()
        hit = self._verify({'type': 'page_text_contains', 'value': '登录成功'})
        self.assertEqual(hit.status_code, 200, hit.get_json())
        self.assertTrue(hit.get_json()['ok'])
        self.assertIn('登录成功', hit.get_json()['actual'])

        miss = self._verify({'type': 'page_text_contains', 'value': '查无此文'})
        self.assertEqual(miss.status_code, 200, miss.get_json())
        self.assertFalse(miss.get_json()['ok'])
        self.assertIn('没有这段内容', miss.get_json()['detail'])

        url = self._verify({'type': 'url_contains', 'value': 'index.html'})
        self.assertTrue(url.get_json()['ok'])
        self._stop()

    def test_verify_still_works_after_stop(self):
        self._start()
        self._stop()
        r = self._verify({'type': 'page_text_contains', 'value': '登录成功'})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertTrue(r.get_json()['ok'])

    def test_verify_rejects_unknown_target(self):
        self._start()
        r = self._verify({'type': 'visible', 'target': '不存在'})
        self.assertEqual(r.status_code, 400)
        self._stop()

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

    def test_assert_with_timeout_becomes_wait_and_can_be_edited(self):
        """断言和等待是同一个东西：加了最长等待就是 wait，去掉就退回 assert，不用删了重插。"""
        self._start()
        r = self.client.post('/api/automation/recording/steps',
                             json={'intent': {'type': 'page_text_contains', 'value': '登录成功',
                                              'timeout': 30, 'reload': True, 'interval': 5}})
        self.assertEqual(r.status_code, 200, r.get_json())
        step = r.get_json()['steps'][-1]
        self.assertEqual(step['type'], 'wait')
        self.assertEqual(step['intent']['timeout'], 30)
        self.assertTrue(step['intent']['reload'])

        # 改成立即断言：类型跟着退回 assert，等待参数清空
        patched = self.client.patch('/api/automation/recording/steps/%d' % step['no'],
                                    json={'timeout': None, 'reload': False})
        self.assertEqual(patched.status_code, 200, patched.get_json())
        step = patched.get_json()['steps'][-1]
        self.assertEqual(step['type'], 'assert')
        self.assertIsNone(step['intent']['timeout'])
        self.assertFalse(step['intent']['reload'])

        # 只给 timeout 时，没提到的字段保持原值（刷新仍然是关的）
        back = self.client.patch('/api/automation/recording/steps/%d' % step['no'],
                                 json={'timeout': 60})
        step = back.get_json()['steps'][-1]
        self.assertEqual(step['type'], 'wait')
        self.assertFalse(step['intent']['reload'])
        self._stop()

    def test_edit_rejects_reload_without_timeout(self):
        """只勾刷新不给最长等待，等于每刷一次就放弃，必须拦下来。"""
        self._start()
        created = self.client.post('/api/automation/recording/steps',
                                   json={'intent': {'type': 'page_text_contains', 'value': '登录成功'}})
        no = created.get_json()['steps'][-1]['no']
        r = self.client.patch('/api/automation/recording/steps/%d' % no,
                              json={'timeout': None, 'reload': True})
        self.assertEqual(r.status_code, 409, r.get_json())
        self.assertIn('最长等待', r.get_json()['error'])
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
        intent = r.get_json()['intent']
        self.assertEqual(intent['type'], 'url_contains')
        self.assertEqual(intent['value'], 'index')
        # 模型没提等待时，三个等待字段必须是空值——否则生成端会以为这一步要等 N 秒或者要刷新。
        self.assertIsNone(intent['timeout'])
        self.assertIsNone(intent['interval'])
        self.assertFalse(intent['reload'])
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

    def test_reload_step_is_inserted_and_does_not_double_record(self):
        """浏览器里的刷新会被自动检测到，工具栏按钮又自己刷一次：只该产生一条步骤。"""
        self._start()
        r = self.client.post('/api/automation/recording/reload', json={})
        self.assertEqual(r.status_code, 200, r.get_json())
        types = [s['type'] for s in r.get_json()['steps']]
        self.assertEqual(types, ['goto', 'reload'])
        # 刷新后的导航事件由录制线程处理，等它过去再确认没有多记一条
        import time
        time.sleep(1.0)
        again = self.client.get('/api/automation/recording/current').get_json()
        self.assertEqual([s['type'] for s in again['steps']], ['goto', 'reload'])
        self._stop()

    def test_reload_requires_a_session(self):
        self.assertEqual(self.client.post('/api/automation/recording/reload', json={}).status_code, 404)

    def test_no_session_is_not_found(self):
        self.assertEqual(self.client.get('/api/automation/recording/current').status_code, 404)
        self.assertEqual(self.client.get('/api/automation/recording/events').status_code, 404)
        self.assertEqual(self.client.post('/api/automation/recording/steps', json={}).status_code, 404)
        self.assertEqual(self.client.post('/api/automation/recording/intent', json={}).status_code, 404)


if __name__ == '__main__':
    unittest.main()
