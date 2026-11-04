"""同一个 opencode session 走完规划 + 写入 + 修复的单元测试。

不碰真实 opencode：用一个假的 HTTP 层记录所有调用，断言「只建一次会话、
两轮发给同一个 session id、写轮换了收窄配置、续不上时降级」。
"""

import unittest
from unittest import mock

import requests

from app.services.opencode_runner import OpenCodeRunner
from app.services.recording import emit, emit_session


WORKSPACE = 'D:/ws/proj'
MODEL = 'p/m'


class FakeProcess(object):
    def __init__(self):
        self.pid = 4242
        self.returncode = None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 0
        return 0


class FakeResponse(object):
    def __init__(self, status_code, payload=None, text=''):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError('no json')
        return self._payload


class FakeHTTP(object):
    RequestException = requests.RequestException

    def __init__(self):
        self.calls = []
        self.session_id = 'ses_test'
        self.created = 0
        self.messages = []
        self.busy_ticks = 0
        self.creator_base = None
        self.resume_ok = True
        self.shrink_on_new_serve = False

    @staticmethod
    def _base(url):
        parts = url.split('/')
        return '/'.join(parts[:3])

    def post(self, url, json=None, timeout=None):
        self.calls.append(('POST', url, json))
        if url.endswith('/session'):
            self.created += 1
            self.creator_base = self._base(url)
            return FakeResponse(200, {'id': self.session_id})
        if '/prompt_async' in url:
            self.messages.append({'info': {'role': 'user'},
                                  'parts': [{'type': 'text', 'text': json['parts'][0]['text']}]})
            self.messages.append({'info': {'role': 'assistant'},
                                  'parts': [{'type': 'text', 'text': '第 %d 轮回复' % self.created}]})
            self.busy_ticks = 1
            return FakeResponse(204)
        if url.endswith('/abort'):
            return FakeResponse(200)
        return FakeResponse(404)

    def get(self, url, timeout=None):
        self.calls.append(('GET', url, None))
        if url.endswith('/session/status'):
            if self.busy_ticks > 0:
                self.busy_ticks -= 1
                return FakeResponse(200, {self.session_id: {'type': 'busy'}})
            return FakeResponse(200, {})
        if url.endswith('/message'):
            items = self.messages
            if self.shrink_on_new_serve and self._base(url) != self.creator_base:
                items = items[:1]
            return FakeResponse(200, list(items))
        if not self.resume_ok and self._base(url) != self.creator_base:
            return FakeResponse(404)
        return FakeResponse(200, {'id': self.session_id})


class FakeRunner(object):
    def __init__(self):
        self.serves = []
        self.configs = []
        self.port = 40000

    def _emit_config(self, writable, workspace, scope='workspace'):
        path = 'cfg-%d.json' % (len(self.configs) + 1)
        self.configs.append({'path': path, 'writable': writable, 'scope': scope})
        return path, (lambda: None)

    def _free_port(self):
        self.port += 1
        return self.port

    def _start_serve(self, workspace, config, port):
        process = FakeProcess()
        self.serves.append({'process': process, 'config': config, 'port': port})
        return process

    def _wait_serve_healthy(self, base_url, process, timeout):
        return {}

    def _serve_host(self):
        return '127.0.0.1'

    def emit_recording(self, *args, **kwargs):
        raise AssertionError('会话可用时不应该退回一次性生成')


class SessionTestBase(unittest.TestCase):
    def setUp(self):
        self.http = FakeHTTP()
        self.runner = FakeRunner()
        patcher = mock.patch.object(emit_session, 'requests', self.http)
        patcher.start()
        self.addCleanup(patcher.stop)
        closer = mock.patch.object(OpenCodeRunner, '_terminate_process',
                                   side_effect=lambda process, **kwargs: process and process.wait())
        self.terminate = closer.start()
        self.addCleanup(closer.stop)

    def make(self):
        return emit_session.EmitSession('rec-1', WORKSPACE, 'playwright-ts', MODEL, self.runner)

    def prompts(self):
        return [call for call in self.http.calls if '/prompt_async' in call[1]]

    @staticmethod
    def session_of(url):
        """从 /session/{id}/prompt_async 里取会话 id。"""
        return url.split('/session/')[1].split('/')[0]

    def created(self):
        return [call for call in self.http.calls if call[0] == 'POST' and call[1].endswith('/session')]


class SameSessionTest(SessionTestBase):
    def test_two_turns_share_one_session_and_one_serve(self):
        """核心：一个会话、一个 serve，规划与写入发给同一个 id。"""
        session = self.make()
        session.plan_turn('规划提示词')
        session.begin_write('tests/a.spec.ts')
        session.emitter_turn('写入提示词')

        self.assertEqual(len(self.created()), 1)
        prompts = self.prompts()
        self.assertEqual(len(prompts), 2)
        self.assertEqual(self.session_of(prompts[0][1]), self.session_of(prompts[1][1]))
        self.assertEqual(self.session_of(prompts[1][1]), self.http.session_id)
        self.assertEqual([call[2]['agent'] for call in prompts],
                         ['recording-planner', 'recording-emitter'])
        # 一个 serve 走到底：写轮不再为了换权限重启进程，两轮打在同一台 serve 上。
        self.assertEqual(len(self.runner.serves), 1)
        self.assertEqual(prompts[0][1].split('/session/')[0],
                         prompts[1][1].split('/session/')[0])
        # 规划期就用宽范围配置起：整套流程权限不变，不需要第二份 config。
        self.assertEqual([c['scope'] for c in self.runner.configs], ['workspace'])

    def test_serve_stays_up_after_write_begins(self):
        session = self.make()
        session.plan_turn('规划')
        scope = session.begin_write('tests/a.spec.ts')
        self.assertEqual(scope, {'reused': True, 'scope': 'workspace'})
        self.assertIsNone(self.runner.serves[0]['process'].returncode)
        self.assertEqual(session.snapshot()['scope'], 'workspace')

    def test_replan_reuses_the_session(self):
        session = self.make()
        session.plan_turn('第一版')
        session.plan_turn('按意见改一版', revise=True)
        self.assertEqual(len(self.created()), 1)
        prompts = self.prompts()
        self.assertEqual(len(prompts), 2)
        self.assertEqual(prompts[0][1], prompts[1][1])
        self.assertEqual({call[2]['agent'] for call in prompts}, {'recording-planner'})

    def test_per_turn_model_shape_matches_the_api(self):
        session = self.make()
        session.plan_turn('规划')
        body = self.prompts()[0][2]
        # prompt 用 modelID；建会话用 id（见 docs/emit-session-spike.md）
        self.assertEqual(body['model'], {'providerID': 'p', 'modelID': 'm'})
        created = self.created()[0][2]
        self.assertEqual(created['model'], {'id': 'm', 'providerID': 'p'})


class DegradeTest(SessionTestBase):
    def test_serve_death_is_session_lost(self):
        session = self.make()
        session.plan_turn('规划')
        self.runner.serves[0]['process'].returncode = 1
        with self.assertRaises(emit_session.SessionLost):
            session.emitter_turn('写入')

    def test_begin_write_without_session_is_not_reused(self):
        session = self.make()
        self.assertEqual(session.begin_write('tests/a.spec.ts'),
                         {'reused': False, 'scope': 'exact'})

    def test_begin_write_after_serve_death_is_not_reused(self):
        """只有一个 serve，它没了就没有第二个可退——调用方直接走一次性生成。"""
        session = self.make()
        session.plan_turn('规划')
        self.runner.serves[0]['process'].returncode = 1
        self.assertEqual(session.begin_write('tests/a.spec.ts'),
                         {'reused': False, 'scope': 'exact'})


class RegistryTest(SessionTestBase):
    def tearDown(self):
        emit_session.REGISTRY.close_all()

    def test_acquire_does_not_start_a_serve(self):
        emit_session.REGISTRY.acquire('rec-x', WORKSPACE, 'playwright-ts', MODEL, self.runner)
        self.assertEqual(self.runner.serves, [])

    def test_close_removes_the_entry_and_kills_serves(self):
        session = emit_session.REGISTRY.acquire('rec-y', WORKSPACE, 'playwright-ts', MODEL,
                                                self.runner)
        session.plan_turn('规划')
        emit_session.REGISTRY.close('rec-y', 'done')
        self.assertIsNone(emit_session.REGISTRY.get('rec-y'))
        self.assertEqual(self.runner.serves[0]['process'].returncode, 0)

    def test_sweep_closes_idle_sessions(self):
        session = emit_session.REGISTRY.acquire('rec-z', WORKSPACE, 'playwright-ts', MODEL,
                                                self.runner)
        session.plan_turn('规划')
        session._last_used -= emit_session.IDLE_TTL + 1
        emit_session.REGISTRY.sweep()
        self.assertIsNone(emit_session.REGISTRY.get('rec-z'))
        self.assertEqual(self.runner.serves[0]['process'].returncode, 0)

    def test_cancel_aborts_then_closes(self):
        session = emit_session.REGISTRY.acquire('rec-c', WORKSPACE, 'playwright-ts', MODEL,
                                                self.runner)
        session.plan_turn('规划')
        self.assertTrue(emit_session.REGISTRY.cancel('rec-c'))
        aborts = [call for call in self.http.calls if call[1].endswith('/abort')]
        self.assertEqual(len(aborts), 1)
        self.assertEqual(self.runner.serves[0]['process'].returncode, 0)

    def test_empty_key_returns_none(self):
        self.assertIsNone(emit_session.REGISTRY.acquire('', WORKSPACE, 'playwright-ts', MODEL,
                                                       self.runner))


class PromptShapeTest(unittest.TestCase):
    def test_resumed_prompt_forbids_rescanning(self):
        text = emit._prompt({'target_path': 'tests/a.spec.ts', 'write_scope': 'exact'},
                            resumed=True)
        self.assertIn('同一个会话的第二轮', text)
        self.assertIn('不要重新扫项目', text)
        self.assertIn('不要再输出任务书', text)

    def test_unscoped_prompt_says_so(self):
        text = emit._prompt({'target_path': 'tests/a.spec.ts',
                             'write_scope': 'instruction_only'}, resumed=True)
        self.assertIn('没有运行时兜底', text)

    def test_workspace_scope_allows_helper_files_with_a_report(self):
        """默认范围放开了辅助文件，但必须要求说明用途、并且不许碰配置。"""
        text = emit._prompt({'target_path': 'tests/a.spec.ts',
                             'write_scope': 'workspace'}, resumed=False)
        self.assertIn('辅助文件', text)
        self.assertIn('写完在回复里逐个说明额外文件', text)
        self.assertIn('不许改 package.json', text)
        # 定位规则里那条「只写一个文件、写别处会被拒绝」必须删掉，否则和新规则打架。
        self.assertNotIn('只写 target_path 指定的那一个文件', text)

    def test_exact_scope_keeps_the_single_file_wording(self):
        text = emit._prompt({'target_path': 'tests/a.spec.ts',
                             'write_scope': 'exact'}, resumed=False)
        self.assertIn('主文件必须是 target_path', text)
        self.assertNotIn('写完在回复里逐个说明额外文件', text)

    def test_payload_carries_context_flags(self):
        payload = emit._payload('playwright-ts', [], 'tests/a.spec.ts', 't', {}, [],
                                context_reused=True, write_scope='exact')
        self.assertTrue(payload['context_reused'])
        self.assertEqual(payload['write_scope'], 'exact')

    def test_revise_prompt_skips_rescan(self):
        text = emit._plan_prompt({'framework': 'playwright-ts', 'steps': []}, revise=True)
        self.assertIn('同一个会话里对任务书的修订', text)
        self.assertIn('只输出 JSON', text)


if __name__ == '__main__':
    unittest.main()
