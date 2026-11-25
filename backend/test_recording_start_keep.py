# -*- coding: utf-8 -*-
"""点「开始」时保留已有步骤：keep_steps 把当前会话带进新会话。

重点是取 ir() 而不是 snapshot()：snapshot 是给前端的那份，候选里的 spec 被
public_step 剥掉了，灌回去回放和生成都会缺定位。这里故意造一个「两份不一样」
的假会话，断言传出去的是带 spec 的那份。
"""
import copy
import os
import tempfile
import unittest
from unittest import mock

_TMP = tempfile.mkdtemp(prefix='recording-keep-')
_SLASH = chr(47)
os.environ['DATABASE_URL'] = 'sqlite:///' + os.path.join(_TMP, 'test.db').replace(chr(92), _SLASH)

from app import create_app  # noqa: E402
from app.models import db  # noqa: E402
from app.services.recording.session import RecordingSession  # noqa: E402
from test_support import reset_database  # noqa: E402

SPEC = {'kind': 'role', 'desc': "getByRole('button', { name: '登录' })", 'count': 1,
        'spec': {'kind': 'role', 'method': 'get_by_role', 'args': ['button'],
                 'kwargs': {'name': '登录', 'exact': True}}}

STEP = {
    'no': 1, 'type': 'click', 'source': 'record', 'page_index': 0,
    'target': {'alias': '登录', 'status': 'unique', 'snap': {'tag': 'button'},
               'primary': copy.deepcopy(SPEC), 'candidates': [copy.deepcopy(SPEC)]},
}


class FakeStarted(object):
    def snapshot(self):
        return {'id': 's1', 'status': 'recording', 'steps': []}


class FakeSession(object):
    status = 'stopped'

    def __init__(self, status='stopped'):
        self.status = status

    def ir(self):
        return [copy.deepcopy(STEP)]

    def snapshot(self):
        # 前端那份：候选只剩 kind/desc/count，没有可执行 spec
        pub = copy.deepcopy(STEP)
        pub['target']['candidates'] = [{'kind': 'role', 'desc': SPEC['desc'], 'count': 1}]
        pub['target']['primary'] = {'kind': 'role', 'desc': SPEC['desc'], 'count': 1}
        return {'steps': [pub]}

    def dispose(self, timeout=None):
        pass


class StartKeepStepsTest(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.ctx = self.app.app_context()
        self.ctx.push()
        reset_database()
        self.client = self.app.test_client()
        self._prev = RecordingSession._current

    def tearDown(self):
        RecordingSession._current = self._prev
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _start(self, payload, current):
        RecordingSession._current = current
        started = {}

        def fake_start(base_url, **kwargs):
            started.update(kwargs)
            return FakeStarted()

        with mock.patch.object(RecordingSession, 'start_new', side_effect=fake_start), \
                mock.patch('app.routes.recording._prime_project_memory'):
            res = self.client.post('/api/automation/recording/start', json=payload)
        return res, started

    def test_keep_steps_uses_full_ir_with_spec(self):
        res, started = self._start({'base_url': 'http://x/login', 'keep_steps': True},
                                   FakeSession())
        self.assertEqual(res.status_code, 201)
        steps = started.get('restore_steps')
        self.assertEqual(len(steps), 1)
        # 关键：带的是 ir() 那份，primary / candidates 都还有可执行 spec
        self.assertIn('spec', steps[0]['target']['primary'])
        self.assertIn('spec', steps[0]['target']['candidates'][0])

    def test_without_flag_starts_empty(self):
        res, started = self._start({'base_url': 'http://x/login'}, FakeSession())
        self.assertEqual(res.status_code, 201)
        self.assertIsNone(started.get('restore_steps'))

    def test_flag_ignored_while_recording(self):
        # 正在录的时候本来就会被 start_new 拒绝（409），这里防御的是「状态判断读错」：
        # 只有停止 / 出错之后才允许把步骤带过去。
        res, started = self._start({'base_url': 'http://x/login', 'keep_steps': True},
                                   FakeSession(status='recording'))
        # start_new 被 mock 掉了，所以看参数：正在录制的会话不能被当作恢复源。
        self.assertIsNone(started.get('restore_steps'))

    def test_no_current_session(self):
        res, started = self._start({'base_url': 'http://x/login', 'keep_steps': True}, None)
        self.assertEqual(res.status_code, 201)
        self.assertIsNone(started.get('restore_steps'))


if __name__ == '__main__':
    unittest.main()
