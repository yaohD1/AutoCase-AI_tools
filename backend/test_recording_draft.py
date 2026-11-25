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
from app.services.recording import drafts, emit, ir, locators  # noqa: E402
from app.services.recording.session import RecordingSession  # noqa: E402
from test_support import reset_database  # noqa: E402

# 录制时的完整目标：candidates / primary 都带 spec，才是能拿去查询页面的定位。
_SNAP = {
    'tag': 'button', 'role': 'button', 'role_attr': 'button', 'name': '登录',
    'text': '登录', 'css': '#ok', 'id': 'login-btn',
}


def _recorded_step(snap=None, no=1):
    """按真实录制链路造一步：候选由 build_specs 生成，每条都带 spec。"""
    snap = dict(snap or _SNAP)
    candidates = [{'kind': spec['kind'], 'desc': spec['desc'], 'count': 1,
                   'index': None, 'relation': 'self', 'spec': spec}
                  for spec in locators.build_specs(snap)]
    return {
        'no': no, 'type': 'click', 'text': None, 'value': None, 'page_index': 0,
        'source': 'record', 'label': None, 'url': None, 'intent': None, 'dialog': None,
        'target': {
            'snap': snap,
            'candidates': candidates,
            'primary': dict(candidates[0]),
            'status': 'unique', 'alias': '登录按钮', 'count': 1,
            # 只服务录制当场的大字段：存档必须剥掉，否则一份草稿能胀到几十兆。
            'shot': 'data:image/jpeg;base64,AAAA',
            'matches': [{'text': '登录', 'tag': 'button'}],
        },
    }


STEP = _recorded_step()


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

    def ir(self):
        return [ir.replay_view(s) for s in self._steps]


class DraftServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config['TESTING'] = True
        cls.client = cls.app.test_client()

    def setUp(self):
        with self.app.app_context():
            reset_database()

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

    def test_save_keeps_locator_spec(self):
        """存档必须保留定位 spec：续录后生成任务书、回放都要拿它去查页面。"""
        with self.app.app_context():
            draft = drafts.save(FakeSession(), {'title': '登录流程'})
            target = draft.steps()[0]['target']
            self.assertEqual(target['candidates'][0]['spec']['method'], 'get_by_role')
            self.assertEqual(target['primary']['spec']['args'], ['button'])

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

    def test_restore_hydrates_spec_for_old_draft(self):
        """老存档只剩前端视图（candidates 没有 spec），续录要按快照把定位补回来。

        线上症状：RF 框架续录后点「生成任务书」，emit._fmt 取 candidate['spec']
        直接 KeyError('spec') → 500「服务器内部错误：'spec'」。
        """
        public = ir.public_step(copy.deepcopy(STEP))
        self.assertNotIn('spec', public['target']['candidates'][0])
        session = RecordingSession.__new__(RecordingSession)
        session._steps = []
        session._alias_keys = {}
        session._restore([public])
        target = session._steps[0]['target']
        self.assertTrue(all(c.get('spec') for c in target['candidates']))
        info = emit._locator_info(target, 'robot-selenium')
        self.assertIsNotNone(info)
        self.assertFalse(info.get('missing_spec'))
        self.assertTrue(info['locator'].startswith('xpath='))

    def test_prepare_reports_unlocated_step(self):
        """确实补不出定位时，报「第 N 步请重新拾取」，而不是让 KeyError 冒到全局 500。"""
        public = ir.public_step(copy.deepcopy(STEP))
        root = os.path.join(os.environ['AUTOMATION_WORKSPACE_ROOT'], 'proj')
        with self.assertRaises(emit.EmitError) as ctx:
            emit._prepare(root, [public], 'robot-selenium', '', {})
        self.assertIn('第 1 步', str(ctx.exception))
        self.assertIn('缺少定位信息', str(ctx.exception))


class DraftRouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config['TESTING'] = True
        cls.client = cls.app.test_client()

    def setUp(self):
        with self.app.app_context():
            reset_database()
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
