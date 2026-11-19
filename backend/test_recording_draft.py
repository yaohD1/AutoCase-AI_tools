# -*- coding: utf-8 -*-
"""录制存档：保存 / 列表 / 详情 / 删除 / 续录恢复。

存档的意义是「系统关了也能接着录」，所以这里重点验三件事：
  1. 存进去的步骤能原样读回来，且剥掉了只服务录制当场的大字段（shot / matches）；
  2. 同一条会话重复保存是覆盖，不在列表里堆副本；
  3. 续录时 start_new 拿得到 restore_steps，步骤灌回会话后编号与别名都还在。
"""
import copy
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

_TMP = tempfile.mkdtemp(prefix='recording-drafts-')
_SLASH = chr(47)
os.environ['DATABASE_URL'] = 'sqlite:///' + os.path.join(_TMP, 'test.db').replace(chr(92), _SLASH)
os.environ['AUTOMATION_WORKSPACE_ROOT'] = os.path.join(_TMP, 'workspaces')
Path(os.environ['AUTOMATION_WORKSPACE_ROOT'], 'proj').mkdir(parents=True, exist_ok=True)

from app import create_app  # noqa: E402
from app.models import db  # noqa: E402
from app.models.recording_draft import RecordingDraft  # noqa: E402
from app.services.recording import drafts  # noqa: E402
from app.services.recording.session import RecordingSession  # noqa: E402

STEP = {
    'no': 1, 'type': 'click', 'text': None, 'value': None, 'page_index': 0,
    'source': 'record', 'label': None, 'url': None, 'intent': None, 'dialog': None,
    'target': {
        'snap': {'tag': 'button', 'role': 'button', 'name': '登录', 'css': '#ok'},
        'candidates': [{'kind': 'role', 'desc': "getByRole('button', { name: '登录' })",
                        'count': 1, 'index': None, 'relation': 'self'}],
        'primary': {'kind': 'role', 'desc': "getByRole('button', { name: '登录' })", 'count': 1},
        'status': 'unique', 'alias': '登录按钮', 'count': 1,
        # 只服务录制当场的大字段：存档必须剥掉，否则一份草稿能胀到几十兆。
        'shot': 'data:image/jpeg;base64,AAAA',
        'matches': [{'text': '登录', 'tag': 'button'}],
    },
}


class FakeSession(object):
    base_url = 'http://x/login'
    workspace = 'proj'
    framework = 'playwright-ts'
    status = 'recording'

    def __init__(self, steps=None):
        self._steps = copy.deepcopy(steps if steps is not None else [STEP])
        self._draft_id = None

    def snapshot(self):
        return {'steps': copy.deepcopy(self._steps), 'picks': []}


class DraftServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config['TESTING'] = True
        cls.client = cls.app.test_client()

    def setUp(self):
        with self.app.app_context():
            db.drop_all()
            db.create_all()

    def test_save_strips_heavy_fields(self):
        with self.app.app_context():
            draft = drafts.save(FakeSession(), {'title': '登录流程', 'model': 'm1'})
            stored = draft.steps()
            self.assertEqual(len(stored), 1)
            target = stored[0]['target']
            self.assertNotIn('shot', target)
            self.assertNotIn('matches', target)
            # 定位靠的是 snap，这个必须留着，否则续录后生成不出候选定位。
            self.assertEqual(target['snap']['name'], '登录')
            self.assertEqual(target['alias'], '登录按钮')

    def test_save_twice_updates_same_row(self):
        with self.app.app_context():
            session = FakeSession()
            first = drafts.save(session, {'title': 'a'})
            session._steps.append(copy.deepcopy(STEP))
            second = drafts.save(session, {'title': 'b'})
            self.assertEqual(first.id, second.id)
            self.assertEqual(RecordingDraft.query.count(), 1)
            self.assertEqual(second.step_count, 2)
            self.assertEqual(second.title, 'b')

    def test_list_omits_steps_and_detail_has_them(self):
        with self.app.app_context():
            drafts.save(FakeSession(), {'title': '登录流程'})
        listed = self.client.get('/api/automation/recording/drafts').get_json()
        self.assertEqual(len(listed['drafts']), 1)
        self.assertNotIn('steps', listed['drafts'][0])
        detail = self.client.get('/api/automation/recording/drafts/%s' % listed['drafts'][0]['id'])
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(len(detail.get_json()['steps']), 1)

    def test_delete(self):
        with self.app.app_context():
            draft = drafts.save(FakeSession(), {'title': 'x'})
            draft_id = draft.id
        self.assertEqual(self.client.delete('/api/automation/recording/drafts/%s' % draft_id).status_code, 200)
        with self.app.app_context():
            self.assertIsNone(drafts.get(draft_id))

    def test_restore_preloads_steps_and_aliases(self):
        """续录：步骤灌回会话后编号连续、别名预占，新元素不会撞名。"""
        session = RecordingSession.__new__(RecordingSession)
        session._steps = []
        session._alias_keys = {}
        session._restore([copy.deepcopy(STEP), dict(copy.deepcopy(STEP), no=99)])
        self.assertEqual([s['no'] for s in session._steps], [1, 2])
        self.assertEqual(session._alias_keys, {'restored:登录按钮': '登录按钮'})
        # 恢复后浏览器打开起始地址的那次导航，要当成存档里的「打开页面」，
        # 否则步骤列表开头会多出一条重复的 goto。
        self.assertTrue(session._restored)

    def test_restore_ignores_empty(self):
        session = RecordingSession.__new__(RecordingSession)
        session._steps = []
        session._alias_keys = {}
        session._restore([])
        self.assertEqual(session._steps, [])


class DraftRouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config['TESTING'] = True
        cls.client = cls.app.test_client()

    def setUp(self):
        with self.app.app_context():
            db.drop_all()
            db.create_all()
        RecordingSession._current = None

    def test_save_requires_a_session(self):
        res = self.client.post('/api/automation/recording/drafts', json={'title': 'x'})
        self.assertEqual(res.status_code, 404)

    def test_save_from_current_session(self):
        RecordingSession._current = FakeSession()
        res = self.client.post('/api/automation/recording/drafts',
                               json={'title': '登录流程', 'workspace': 'proj',
                                     'framework': 'playwright-ts', 'model': 'm1'})
        self.assertEqual(res.status_code, 200)
        data = res.get_json()['draft']
        self.assertEqual(data['step_count'], 1)
        self.assertEqual(data['base_url'], 'http://x/login')
        self.assertEqual(data['title'], '登录流程')

    def test_resume_passes_steps_and_draft_id(self):
        with self.app.app_context():
            draft = drafts.save(FakeSession(), {'title': '登录流程', 'workspace': 'proj',
                                                'framework': 'playwright-ts'})
            draft_id = draft.id

        started = {}

        class FakeStarted(object):
            def snapshot(self):
                return {'id': 's1', 'status': 'recording', 'steps': draft.steps()}

            def dispose(self):
                pass

        def fake_start(base_url, **kwargs):
            started['base_url'] = base_url
            started.update(kwargs)
            return FakeStarted()

        with mock.patch.object(RecordingSession, 'start_new', side_effect=fake_start), \
                mock.patch('app.routes.recording._prime_project_memory'):
            res = self.client.post('/api/automation/recording/drafts/%s/resume' % draft_id)
        self.assertEqual(res.status_code, 201)
        self.assertEqual(started['base_url'], 'http://x/login')
        self.assertEqual(started['draft_id'], draft_id)
        self.assertEqual(len(started['restore_steps']), 1)
        self.assertEqual(started['restore_steps'][0]['target']['alias'], '登录按钮')

    def test_resume_rejects_missing_draft(self):
        res = self.client.post('/api/automation/recording/drafts/nope/resume')
        self.assertEqual(res.status_code, 404)


if __name__ == '__main__':
    unittest.main()
