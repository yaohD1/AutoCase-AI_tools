import base64
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TMP = tempfile.mkdtemp(prefix='records-')
_SLASH = chr(47)
os.environ['DATABASE_URL'] = 'sqlite:///' + os.path.join(_TMP, 'test.db').replace(chr(92), _SLASH)

from app import create_app  # noqa: E402
from app.models import AutomationGeneration, db  # noqa: E402
from app.models.recording_generation import RecordingGeneration  # noqa: E402
from app.services.recording import emit, records  # noqa: E402
from app.services.recording.session import RecordingSession  # noqa: E402

TARGET = 'tests/login.robot'
NEW = b'*** Test Cases ***\nLogin\n    Log    new\n'
OLD = b'*** Test Cases ***\nLogin\n    Log    old\n'


def _git(ws, *args):
    return subprocess.run(['git', '-C', str(ws), *args], capture_output=True, text=True,
                          encoding='utf-8', check=True).stdout


def _init_repo(root):
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    _git(root, 'config', 'user.email', 'test@example.com')
    _git(root, 'config', 'user.name', 'test')
    (root / 'README.md').write_text('seed', encoding='utf-8')
    _git(root, 'add', 'README.md')
    _git(root, 'commit', '-q', '-m', 'init')


class RecordServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()

    def setUp(self):
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.ws = Path(tempfile.mkdtemp(prefix='rec-ws-'))
        _init_repo(self.ws)

    def tearDown(self):
        self.ctx.pop()
        shutil.rmtree(self.ws, ignore_errors=True)

    def _write(self, content):
        path = self.ws / TARGET
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def _record(self, original, generated):
        self._write(generated)
        return records.create(self.ws, TARGET, 'robot-selenium', 'Login', 'p/m', original, generated, [], 'ok')

    def test_commit_then_revert_removes_the_committed_file(self):
        record = self._record(None, NEW)
        records.commit(record, '添加登录用例')
        self.assertEqual(record.status, 'committed')
        self.assertTrue(record.commit_hash)
        self.assertIn('添加登录用例', _git(self.ws, 'log', '--format=%s', '-1'))
        records.revert(record)
        self.assertEqual(record.status, 'reverted')
        self.assertFalse((self.ws / TARGET).exists())

    def test_revert_before_commit_deletes_a_new_file(self):
        record = self._record(None, NEW)
        records.revert(record)
        self.assertFalse((self.ws / TARGET).exists())
        self.assertEqual(record.status, 'reverted')

    def test_revert_before_commit_restores_the_original_content(self):
        self._write(OLD)
        record = self._record(OLD, NEW)
        records.revert(record)
        self.assertEqual((self.ws / TARGET).read_bytes(), OLD)

    def test_file_changed_after_generation_blocks_commit_and_revert(self):
        record = self._record(None, NEW)
        self._write(b'someone edited this')
        with self.assertRaises(records.RecordError):
            records.commit(record, 'msg')
        with self.assertRaises(records.RecordError):
            records.revert(record)
        self.assertEqual((self.ws / TARGET).read_bytes(), b'someone edited this')

    def test_commit_requires_message_and_git_repo(self):
        record = self._record(None, NEW)
        with self.assertRaises(records.RecordError):
            records.commit(record, '   ')
        plain = Path(tempfile.mkdtemp(prefix='plain-'))
        try:
            (plain / TARGET).parent.mkdir(parents=True, exist_ok=True)
            (plain / TARGET).write_bytes(NEW)
            other = records.create(plain, TARGET, 'robot-selenium', 'Login', 'p/m', None, NEW, [], '')
            with self.assertRaises(records.RecordError) as ctx:
                records.commit(other, 'msg')
            self.assertIn('不是 Git 仓库', str(ctx.exception))
        finally:
            shutil.rmtree(plain, ignore_errors=True)

    def test_reverting_twice_is_refused(self):
        record = self._record(None, NEW)
        records.revert(record)
        with self.assertRaises(records.RecordError):
            records.revert(record)

    def test_recording_rows_are_separate_from_automation_generations(self):
        before = AutomationGeneration.query.count()
        record = self._record(None, NEW)
        records.commit(record, 'separate')
        self.assertEqual(AutomationGeneration.query.count(), before)
        self.assertIsNotNone(db.session.get(RecordingGeneration, record.id))
        self.assertEqual(records.preview(record)['content'], NEW.decode('utf-8'))


class RecordRouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config['TESTING'] = True
        cls.app.config['AUTOMATION_WORKSPACE_ROOT'] = tempfile.mkdtemp(prefix='rec-root-')
        cls.root = Path(cls.app.config['AUTOMATION_WORKSPACE_ROOT'])
        cls.ws = cls.root / 'proj'
        cls.ws.mkdir()
        _init_repo(cls.ws)
        cls.client = cls.app.test_client()

    def setUp(self):
        RecordingSession._current = None
        session = RecordingSession.__new__(RecordingSession)
        session.status = 'stopped'
        session.ir = lambda: [{'no': 1, 'type': 'click', 'text': '点击', 'value': None, 'target': None}]
        RecordingSession._current = session
        self.ctx = self.app.app_context()
        self.ctx.push()

    def tearDown(self):
        self.ctx.pop()

    def _fake_generate(self, content):
        def generate(runner, workspace, model, steps, framework, target_path, title, python=None,
                     overwrite=False, plan=None, plan_feedback=None, progress=None):
            path = Path(workspace) / target_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            return {'path': target_path, 'changed': [target_path], 'validated': True,
                    'framework': framework, 'detected': framework, 'reply': 'ok'}
        return generate

    def _generate(self, target, content=NEW, overwrite=False):
        with mock.patch.object(emit, 'generate', side_effect=self._fake_generate(content)):
            return self.client.post('/api/automation/recording/generate', json={
                'model': 'p/m', 'workspace_path': 'proj', 'framework': 'robot-selenium',
                'target_path': target, 'title': 'Login', 'overwrite': overwrite,
                # 写入必须带任务书；这里只关心记录逻辑，任务书内容取最小可用形状。
                'plan': {'steps': [{'no': 1, 'action': 'write'}]},
            })

    def test_generate_creates_a_record_visible_only_in_recording_list(self):
        automation_before = AutomationGeneration.query.count()
        r = self._generate('tests/route_a.robot')
        self.assertEqual(r.status_code, 200, r.get_json())
        generation_id = r.get_json()['generation_id']

        listed = self.client.get('/api/automation/recording/generations').get_json()['generations']
        self.assertIn(generation_id, [g['id'] for g in listed])
        self.assertEqual(AutomationGeneration.query.count(), automation_before)

        detail = self.client.get('/api/automation/recording/generations/' + generation_id).get_json()
        self.assertEqual(detail['status'], 'generated')
        self.assertTrue(detail['changed_since_generation'] is False)
        self.assertIn('Log    new', detail['content'])

    def test_commit_and_revert_through_routes(self):
        generation_id = self._generate('tests/route_b.robot').get_json()['generation_id']
        url = '/api/automation/recording/generations/' + generation_id
        missing_msg = self.client.post(url + '/commit', json={})
        self.assertEqual(missing_msg.status_code, 409)
        committed = self.client.post(url + '/commit', json={'message': '录制 B'})
        self.assertEqual(committed.status_code, 200, committed.get_json())
        self.assertEqual(committed.get_json()['status'], 'committed')
        reverted = self.client.post(url + '/revert')
        self.assertEqual(reverted.status_code, 200, reverted.get_json())
        self.assertFalse((self.ws / 'tests' / 'route_b.robot').exists())

    def test_unknown_generation_is_not_found(self):
        self.assertEqual(self.client.get('/api/automation/recording/generations/nope').status_code, 404)
        self.assertEqual(self.client.post('/api/automation/recording/generations/nope/revert').status_code, 404)

    def test_failed_generation_without_touching_the_file_creates_no_record(self):
        before = RecordingGeneration.query.count()
        with mock.patch.object(emit, 'generate', side_effect=emit.EmitError('校验未通过，已还原', details=['x'])):
            r = self.client.post('/api/automation/recording/generate', json={
                'model': 'p/m', 'workspace_path': 'proj', 'framework': 'robot-selenium',
                'target_path': 'tests/route_c.robot',
                'plan': {'steps': [{'no': 1, 'action': 'write'}]}})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(RecordingGeneration.query.count(), before)

    def test_failed_generation_keeps_a_revertible_record_when_the_file_was_touched(self):
        """写了一半才失败：原文件已经被覆盖，必须留下能还原的记录。"""
        target = self.ws / 'tests' / 'route_fail.robot'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('*** Test Cases ***\nOLD\n', encoding='utf-8')

        def boom(runner, workspace, model, steps, framework, target_path, *args, **kwargs):
            (Path(workspace) / target_path).write_text('半截内容', encoding='utf-8')
            raise emit.EmitError('校验没过')

        with mock.patch.object(emit, 'generate', side_effect=boom):
            r = self.client.post('/api/automation/recording/generate', json={
                'model': 'p/m', 'workspace_path': 'proj', 'framework': 'robot-selenium',
                'target_path': 'tests/route_fail.robot',
                'overwrite': True,
                'plan': {'steps': [{'no': 1, 'action': 'write'}]}})
        self.assertEqual(r.status_code, 422, r.get_json())
        details = r.get_json()['details']
        record_id = next((d.split(':', 1)[1] for d in details if d.startswith('record:')), None)
        self.assertIsNotNone(record_id, '失败后要给出可回滚的记录 id')

        detail = self.client.get('/api/automation/recording/generations/' + record_id).get_json()
        self.assertEqual(detail['status'], 'failed')
        self.assertIn('半截内容', detail['content'])

        self.client.post('/api/automation/recording/generations/%s/revert' % record_id)
        self.assertIn('OLD', target.read_text(encoding='utf-8'), '回滚要把原内容写回去')

    def test_target_with_parent_segment_is_rejected_before_reading(self):
        r = self.client.post('/api/automation/recording/generate', json={
            'model': 'p/m', 'workspace_path': 'proj', 'framework': 'robot-selenium',
            'target_path': '../outside.robot'})
        self.assertEqual(r.status_code, 400)


if __name__ == '__main__':
    unittest.main()
