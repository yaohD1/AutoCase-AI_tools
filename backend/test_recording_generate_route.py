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
from app.services.recording import emit, progress  # noqa: E402
from app.services.recording.session import RecordingSession  # noqa: E402

MODEL = 'volcengine-plan/deepseek-v4-flash'
GEN = '/api/automation/recording/generate'


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

    def _stopped_session(self, status='stopped'):
        session = RecordingSession.__new__(RecordingSession)
        session.status = status
        session.ir = lambda: []
        RecordingSession._current = session
        return session

    def _post(self, **extra):
        body = {'model': MODEL, 'workspace_path': 'proj', 'framework': 'robot-selenium',
                'target_path': 'a.robot'}
        body.update(extra)
        return self.client.post(GEN, json=body)

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


if __name__ == '__main__':
    unittest.main()
