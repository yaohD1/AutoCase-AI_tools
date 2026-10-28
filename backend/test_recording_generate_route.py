import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TMP = tempfile.mkdtemp(prefix='generate-routes-')
_SLASH = chr(47)
os.environ['DATABASE_URL'] = 'sqlite:///' + os.path.join(_TMP, 'test.db').replace(chr(92), _SLASH)
os.environ['AUTOMATION_WORKSPACE_ROOT'] = os.path.join(_TMP, 'workspaces')
Path(os.environ['AUTOMATION_WORKSPACE_ROOT'], 'proj').mkdir(parents=True, exist_ok=True)

from app import create_app  # noqa: E402
from app.services.opencode_runner import OpenCodeRunner  # noqa: E402
from app.services.recording import emit, progress, replay  # noqa: E402
from app.services.recording.session import RecordingSession  # noqa: E402

MODEL = 'volcengine-plan/deepseek-v4-flash'
GEN = '/api/automation/recording/generate'
PLAN_API = '/api/automation/recording/plan'
REPLAY = '/api/automation/recording/replay'
# 任务书是写入的唯一入口；路由测试只关心「有没有带、对不对得上」，内容由 emit 的测试覆盖。
PLAN = {'steps': [], 'target_path': 'a.robot'}


class GenerateRouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config['TESTING'] = True
        cls.client = cls.app.test_client()

    def setUp(self):
        self.app = GenerateRouteTest.app
        RecordingSession._current = None
        progress.PROGRESS.reset()

    def _stopped_session(self, status='stopped', steps=None):
        session = RecordingSession.__new__(RecordingSession)
        session.status = status
        session.ir = lambda: list(steps or [])
        RecordingSession._current = session
        return session

    def _post(self, **extra):
        body = {'model': MODEL, 'workspace_path': 'proj', 'framework': 'robot-selenium',
                'target_path': 'a.robot', 'plan': PLAN}
        body.update(extra)
        return self.client.post(GEN, json=body)

    def _plan(self, **extra):
        body = {'model': MODEL, 'workspace_path': 'proj', 'framework': 'robot-selenium',
                'target_path': 'a.robot'}
        body.update(extra)
        return self.client.post(PLAN_API, json=body)

    def test_no_session_is_not_found(self):
        self.assertEqual(self.client.post(GEN, json={'model': MODEL}).status_code, 404)

    def test_recording_must_be_stopped_first(self):
        self._stopped_session(status='recording')
        self.assertEqual(self._post().status_code, 409)

    def test_model_is_required(self):
        self._stopped_session()
        self.assertEqual(self._post(model='').status_code, 400)

    def test_workspace_must_stay_inside_root(self):
        self._stopped_session()
        self.assertEqual(self._post(workspace_path='../escape').status_code, 400)

    def test_success_returns_written_file(self):
        self._stopped_session()
        # 自己指定临时工作区根目录，不依赖其它测试模块导入时设置的环境变量。
        root = Path(tempfile.mkdtemp(prefix='gen-root-'))
        (root / 'proj').mkdir()
        before = self.app.config['AUTOMATION_WORKSPACE_ROOT']
        self.app.config['AUTOMATION_WORKSPACE_ROOT'] = str(root)
        self.addCleanup(self.app.config.__setitem__, 'AUTOMATION_WORKSPACE_ROOT', before)
        result = {'path': 'a.robot', 'changed': ['a.robot'], 'validated': True,
                  'framework': 'robot-selenium', 'detected': None, 'reply': 'ok'}

        def fake_generate(runner, workspace, *args, **kwargs):
            # 真实的 emit.generate 成功时目标文件一定已写入，路由会读取它来建记录。
            (Path(workspace) / 'a.robot').write_text('*** Test Cases ***', encoding='utf-8')
            return result

        with mock.patch.object(emit, 'generate', side_effect=fake_generate) as gen:
            r = self._post()
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertTrue(r.get_json()['validated'])
        self.assertIsInstance(gen.call_args[0][0], OpenCodeRunner)

    def test_second_window_is_rejected_while_one_generation_runs(self):
        self._stopped_session()
        # 另一个窗口（录制面板）已经在跑：这一边不该再写一遍同一个文件。
        self.assertTrue(progress.PROGRESS.start())
        r = self._post()
        self.assertEqual(r.status_code, 409)
        self.assertIn('已有生成任务', r.get_json()['error'])

    def test_failed_generation_leaves_the_reason_on_progress(self):
        self._stopped_session()
        with mock.patch.object(emit, 'generate',
                               side_effect=emit.EmitError('校验未通过', details=['bad keyword'])):
            self._post()
        snap = progress.PROGRESS.snapshot()
        self.assertEqual(snap['status'], 'failed')
        self.assertEqual(snap['error']['message'], '校验未通过')
        self.assertEqual(snap['error']['details'], ['bad keyword'])

    def test_conflict_clears_progress_instead_of_reporting_failure(self):
        self._stopped_session()
        with mock.patch.object(emit, 'generate',
                               side_effect=emit.EmitError('目标文件已存在', details=['conflict:a.robot'])):
            self._post()
        # 覆盖还是改名由用户决定，进度不该留一条「生成失败」。
        self.assertEqual(progress.PROGRESS.snapshot()['status'], 'idle')

    def test_conflict_is_409_and_failure_is_422_with_details(self):
        self._stopped_session()
        with mock.patch.object(emit, 'generate',
                               side_effect=emit.EmitError('目标文件已存在', details=['conflict:a.robot'])):
            self.assertEqual(self._post().status_code, 409)
        with mock.patch.object(emit, 'generate',
                               side_effect=emit.EmitError('校验未通过，已还原', details=['bad keyword'])):
            r = self._post()
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.get_json()['details'], ['bad keyword'])


    def test_plan_is_required_before_writing(self):
        """写入的唯一入口是任务书：不带任务书直接拒绝，避免又回到「一键从零写」。"""
        self._stopped_session()
        r = self._post(plan=None)
        self.assertEqual(r.status_code, 400)
        self.assertIn('缺少任务书', r.get_json()['error'])

    def test_stale_plan_is_rejected(self):
        """录制改过（步骤号对不上）之后，旧任务书不能再用来写，否则会漏步骤。"""
        self._stopped_session(steps=[{'no': 1}, {'no': 2}])
        r = self._post(plan={'steps': [{'no': 1}], 'target_path': 'a.robot'})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()['details'], ['plan_stale'])

    def test_empty_target_path_takes_the_one_from_the_plan(self):
        """配置留空 = 位置由任务书定，执行时必须落到任务书那条路径上。"""
        self._stopped_session()
        root = Path(tempfile.mkdtemp(prefix='gen-planpath-'))
        (root / 'proj').mkdir()
        before = self.app.config['AUTOMATION_WORKSPACE_ROOT']
        self.app.config['AUTOMATION_WORKSPACE_ROOT'] = str(root)
        self.addCleanup(self.app.config.__setitem__, 'AUTOMATION_WORKSPACE_ROOT', before)
        seen = {}

        def fake_generate(runner, workspace, model, steps, framework, target_path, *args, **kwargs):
            seen['target'] = target_path
            path = Path(workspace) / target_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('*** Test Cases ***', encoding='utf-8')
            return {'path': target_path, 'changed': [target_path], 'validated': True,
                    'framework': framework, 'detected': None, 'reply': 'ok'}

        with mock.patch.object(emit, 'generate', side_effect=fake_generate):
            r = self._post(target_path='', plan={'steps': [], 'target_path': 'tests/from_plan.robot'})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(seen['target'], 'tests/from_plan.robot')
        self.assertEqual(r.get_json()['path'], 'tests/from_plan.robot')

    def test_plan_path_outside_the_workspace_is_rejected(self):
        """任务书是回传值，可以被人改过：写之前必须重新校验，不能照单全收。"""
        self._stopped_session()
        r = self._post(target_path='', plan={'steps': [], 'target_path': '../escape.robot'})
        self.assertEqual(r.status_code, 400)
        self.assertIn('路径', r.get_json()['error'])

    def test_plan_route_returns_task_sheet_without_writing(self):
        self._stopped_session(steps=[{'no': 1}])
        result = {'plan': {'steps': [{'no': 1, 'action': 'write'}]}, 'target_path': 'a.robot',
                  'suggestion': None, 'reason': None, 'steps': 1, 'chars': 40}
        with mock.patch.object(emit, 'plan', return_value=result) as planner:
            r = self._plan(feedback=[{'quote': 'q', 'comment': 'c'}],
                           previous_plan={'steps': [{'no': 1}]})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()['steps'], 1)
        kwargs = planner.call_args[1]
        self.assertEqual(kwargs['feedback'], [{'quote': 'q', 'comment': 'c'}])
        self.assertEqual(kwargs['previous_plan'], {'steps': [{'no': 1}]})
        self.assertEqual(progress.PROGRESS.snapshot()['status'], 'done')
        self.assertIsNone(progress.PROGRESS.snapshot()['result'], '规划结束不该伪装成一次写入结果')

    def test_plan_route_reports_failure(self):
        self._stopped_session(steps=[{'no': 1}])
        with mock.patch.object(emit, 'plan',
                               side_effect=emit.EmitError('任务书解析失败', details=['不是 JSON'])):
            r = self._plan()
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.get_json()['details'], ['不是 JSON'])
        self.assertEqual(progress.PROGRESS.snapshot()['status'], 'failed')

    def test_plan_and_write_are_mutually_exclusive(self):
        self._stopped_session()
        self.assertTrue(progress.PROGRESS.start('write'))
        r = self._plan()
        self.assertEqual(r.status_code, 409)
        self.assertIn('已有生成任务', r.get_json()['error'])


class ReplayRouteTest(unittest.TestCase):
    """回放跑的是内存里的录制 IR，不是生成的脚本：录制一停就该能跑，和有没有生成无关。"""

    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config['TESTING'] = True
        cls.client = cls.app.test_client()

    def setUp(self):
        RecordingSession._current = None

    def _stopped_session(self, status='stopped', steps=None):
        session = RecordingSession.__new__(RecordingSession)
        session.status = status
        session.ir = lambda: list(steps or [])
        RecordingSession._current = session
        return session

    def test_replay_runs_without_any_generation(self):
        self._stopped_session(steps=[{'no': 1, 'type': 'goto', 'url': 'http://example.test'}])
        fake = {'ok': True, 'done': 1, 'total': 1, 'steps': [], 'error': None, 'failed_no': None}
        with mock.patch.object(replay, 'replay_checked', return_value=fake) as called:
            r = self.client.post(REPLAY, json={'framework': 'playwright-ts'})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()['ok'])
        called.assert_called_once()
        # 传进去的就是录制 IR 本身，没有任何「读过生成产物」的痕迹。
        self.assertEqual(called.call_args[0][0][0]['type'], 'goto')

    def test_replay_needs_recording_to_be_stopped(self):
        self._stopped_session(status='recording', steps=[{'no': 1, 'type': 'goto'}])
        r = self.client.post(REPLAY, json={})
        self.assertEqual(r.status_code, 409)

    def test_replay_runs_while_paused(self):
        """暂停时步骤集不再变化，回放该和停止后一样可用——「先验一段再接着录」。"""
        session = self._stopped_session(status='recording', steps=[{'no': 1, 'type': 'goto'}])
        session._paused = True
        fake = {'ok': True, 'done': 1, 'total': 1, 'steps': [], 'error': None, 'failed_no': None}
        with mock.patch.object(replay, 'replay_checked', return_value=fake) as called:
            r = self.client.post(REPLAY, json={'framework': 'playwright-ts'})
        self.assertEqual(r.status_code, 200)
        called.assert_called_once()

    def test_paused_property_defaults_to_false(self):
        """测试里会用 __new__ 直接造会话，属性不能因为缺 _paused 就炸。"""
        session = RecordingSession.__new__(RecordingSession)
        self.assertFalse(session.paused)

    def test_replay_needs_at_least_one_step(self):
        self._stopped_session(steps=[])
        r = self.client.post(REPLAY, json={})
        self.assertEqual(r.status_code, 400)


if __name__ == '__main__':
    unittest.main()
