"""项目画像的单元测试：抠 JSON、清洗、注入切片、指纹与学习的状态机。

不调真实模型：`learn()` 的 runner 是注入的，这里塞一个只会返回固定文本的假对象，
锁住的是「学不到时不能把已有画像弄丢」「指纹没变不重学」「注入时不给规划塞代码」。
"""
import json
import tempfile
import unittest
from pathlib import Path

from flask import Flask

from app.models.database import db
from app.services.recording import emit, memory, projectmap

PROFILE = {
    'framework': 'playwright-ts',
    'runner': {'test_dir': 'tests', 'command': 'npx playwright test',
               'config': 'playwright.config.ts', 'base_url': 'http://localhost:3000'},
    'layout': '按业务模块分目录：tests/<module>/<action>.spec.ts',
    'naming': '文件名 kebab-case，用例标题中文',
    'case_shape': 'plain test from @playwright/test, beforeEach 调 loginAs',
    'helpers': [
        {'name': 'loginAs', 'file': 'tests/helpers/auth.ts',
         'signature': "loginAs(page, role = 'admin')", 'use': '免登录，覆盖每条用例'},
        {'name': 'waitTable', 'file': 'tests/helpers/ui.ts',
         'signature': 'waitTable(page)', 'use': '等表格渲染完'},
    ],
    'fixtures': [{'name': 'authedPage', 'file': 'tests/fixtures/auth.ts',
                  'signature': 'authedPage: Page'}],
    'assertions': ['toBeVisible', 'toHaveURL', 'toContainText'],
    'waits': '禁止 waitForTimeout，跳转后 expect(page).toHaveURL',
    'imports': '统一从 @playwright/test 导入',
    'snippet': {'file': 'tests/login/login-success.spec.ts',
                'code': "import { test, expect } from '@playwright/test';\n"
                        "test('登录成功', async ({ page }) => {\n"
                        "  await page.goto('/login');\n"
                        "  await expect(page).toHaveURL(/dashboard/);\n"
                        "});\n"},
    'areas': [
        {'path': 'tests/login', 'kind': 'tests', 'role': '登录页表单校验'},
        {'path': 'src/pages/LoginPage.ts', 'kind': 'pages', 'role': '登录页封装',
         'use': 'new LoginPage(page) 后 open()'},
    ],
    'read_files': ['playwright.config.ts', 'tests/login/login-success.spec.ts'],
    'confidence': 'high',
}

# 模型自造的字段：既没有人校验，又会随每轮生成变成新提示词，必须挡在入库这一步。
PROFILE['best_practices'] = ['要用 page object']
PROFILE['examples'] = [{'code': 'whatever'}]


class ParseTest(unittest.TestCase):
    def test_fenced_json_is_extracted(self):
        text = '先看一眼项目。\n```json\n' + json.dumps(PROFILE, ensure_ascii=False) + '\n```\n'
        profile, error = memory.parse(text, 'playwright-ts')
        self.assertIsNone(error)
        self.assertEqual(profile['runner']['test_dir'], 'tests')

    def test_trailing_commentary_is_ignored(self):
        """模型爱在 JSON 后面补一段说明。按「第一个 { 到最后一个 }」切会 Extra data 解析失败。"""
        text = json.dumps(PROFILE, ensure_ascii=False) + '\n\n以上就是这个项目的情况，'
        text += '如果 {target_path} 为空就按上面的目录来。\n'
        profile, error = memory.parse(text, 'playwright-ts')
        self.assertIsNone(error)
        self.assertEqual(profile['runner']['test_dir'], 'tests')

    def test_two_objects_take_the_first(self):
        text = json.dumps(PROFILE, ensure_ascii=False) + '\n' + json.dumps({'x': 1})
        profile, error = memory.parse(text, 'playwright-ts')
        self.assertIsNone(error)
        self.assertIn('runner', profile)
        self.assertEqual(len(profile['helpers']), 2)

    def test_invented_keys_are_dropped(self):
        profile, _ = memory.parse(json.dumps(PROFILE, ensure_ascii=False))
        self.assertNotIn('best_practices', profile)
        self.assertNotIn('examples', profile)
        self.assertEqual(set(profile) - {'framework'}, set(memory.PROFILE_KEYS) - {'framework'})

    def test_nothing_useful_is_rejected(self):
        """只有 framework 的「画像」等于没学到，不能覆盖上一份能用的。"""
        profile, error = memory.parse('{"framework": "playwright-ts", "confidence": "low"}')
        self.assertIsNone(profile)
        self.assertIn('没有可用内容', error)

    def test_snippet_shorter_than_threshold_is_dropped(self):
        data = dict(PROFILE, snippet={'file': 'a.ts', 'code': 'test("x", () => {});'})
        profile, _ = memory.parse(json.dumps(data, ensure_ascii=False))
        self.assertNotIn('snippet', profile)

    def test_snippet_keeps_indentation(self):
        profile, _ = memory.parse(json.dumps(PROFILE, ensure_ascii=False))
        self.assertIn('\n  await page.goto', profile['snippet']['code'])

    def test_helpers_without_name_are_dropped(self):
        data = dict(PROFILE, helpers=[{'file': 'a.ts'}, {'name': 'ok'}])
        profile, _ = memory.parse(json.dumps(data, ensure_ascii=False))
        self.assertEqual([h['name'] for h in profile['helpers']], ['ok'])

    def test_lists_are_capped(self):
        data = dict(PROFILE, areas=[{'path': 'p%d' % i, 'kind': 'tests'} for i in range(40)],
                    helpers=[{'name': 'h%d' % i} for i in range(20)])
        profile, _ = memory.parse(json.dumps(data, ensure_ascii=False))
        self.assertEqual(len(profile['areas']), projectmap.MAX_AREAS)
        self.assertEqual(len(profile['helpers']), memory.MAX_HELPERS)


class SliceTest(unittest.TestCase):
    def setUp(self):
        self.entry = {'profile': memory.parse(json.dumps(PROFILE, ensure_ascii=False))[0],
                      'stale': False}

    def test_plan_slice_has_no_code(self):
        """规划不写代码，一段 60 行样本只会白占上下文。"""
        out = memory.for_plan(self.entry)
        self.assertNotIn('snippet', out)
        self.assertNotIn('assertions', out)
        self.assertEqual(out['runner']['test_dir'], 'tests')

    def test_plan_slice_caps_helpers(self):
        profile = dict(self.entry['profile'])
        profile['helpers'] = [{'name': 'h%d' % i, 'signature': '()'} for i in range(10)]
        out = memory.for_plan({'profile': profile})
        self.assertEqual(len(out['helpers']), 6)

    def test_writer_slice_has_code(self):
        out = memory.for_writer(self.entry)
        self.assertIn('await page.goto', out['snippet']['code'])
        self.assertEqual(out['assertions'][0], 'toBeVisible')

    def test_stale_is_visible_to_the_model(self):
        entry = dict(self.entry, stale=True)
        self.assertTrue(memory.for_plan(entry)['stale'])

    def test_snippet_sample_matches_detect_shape(self):
        sample = memory.snippet_sample(self.entry)
        self.assertEqual(set(sample), {'path', 'head', 'generated'})
        self.assertEqual(sample['path'], 'tests/login/login-success.spec.ts')

    def test_missing_profile_yields_empty(self):
        self.assertEqual(memory.for_plan(None), {})
        self.assertIsNone(memory.snippet_sample(None))


class FingerprintTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'package.json').write_text('{"name":"a"}', encoding='utf-8')
        (self.root / 'playwright.config.ts').write_text('export default {}', encoding='utf-8')
        (self.root / 'tests').mkdir()
        (self.root / 'tests' / 'a.spec.ts').write_text('// a', encoding='utf-8')

    def tearDown(self):
        self.tmp.cleanup()

    def test_stable_when_nothing_changes(self):
        self.assertEqual(memory.fingerprint(self.root), memory.fingerprint(self.root))

    def test_changes_with_key_file(self):
        before = memory.fingerprint(self.root)
        (self.root / 'package.json').write_text('{"name":"a","version":"2"}', encoding='utf-8')
        self.assertNotEqual(before, memory.fingerprint(self.root))

    def test_changes_when_a_test_is_added(self):
        before = memory.fingerprint(self.root)
        (self.root / 'tests' / 'b.spec.ts').write_text('// b', encoding='utf-8')
        self.assertNotEqual(before, memory.fingerprint(self.root))


class LearnTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'package.json').write_text('{"name":"a"}', encoding='utf-8')
        (self.root / 'tests').mkdir()
        (self.root / 'tests' / 'a.spec.ts').write_text('// a', encoding='utf-8')
        self.db_tmp = tempfile.TemporaryDirectory()
        self.app = Flask(__name__)
        self.app.config.update(
            SQLALCHEMY_DATABASE_URI='sqlite:///' + str(Path(self.db_tmp.name) / 'db.sqlite'),
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        with self.app.app_context():
            db.create_all()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            # Windows 上 sqlite 文件被连接占着就删不掉，必须先把引擎的连接池放掉。
            db.engine.dispose()
        self.tmp.cleanup()
        try:
            self.db_tmp.cleanup()
        except (PermissionError, OSError):
            pass

    def _runner(self, reply=None, error=None):
        calls = []

        class Fake:
            def emit_profile(self, workspace, model, prompt, timeout=300):
                calls.append(model)
                if error:
                    raise RuntimeError(error)
                return reply
        return Fake(), calls

    def test_first_learn_stores_profile(self):
        runner, calls = self._runner(json.dumps(PROFILE, ensure_ascii=False))
        with self.app.app_context():
            result = memory.learn(runner, self.root, 'a/b')
            self.assertTrue(result['learned'])
            self.assertEqual(calls, ['a/b'])
            row = memory.row_for(self.root)
            self.assertEqual(row.status, 'ready')
            self.assertEqual(row.to_dict(True)['profile']['runner']['test_dir'], 'tests')
            entry = memory.load(self.root)
            self.assertFalse(entry['stale'])

    def test_second_learn_is_skipped_when_fingerprint_matches(self):
        runner, calls = self._runner(json.dumps(PROFILE, ensure_ascii=False))
        with self.app.app_context():
            memory.learn(runner, self.root, 'a/b')
            result = memory.learn(runner, self.root, 'a/b')
            self.assertEqual(result, {'learned': False, 'reason': 'unchanged'})
            self.assertEqual(len(calls), 1)

    def test_force_relearns(self):
        runner, calls = self._runner(json.dumps(PROFILE, ensure_ascii=False))
        with self.app.app_context():
            memory.learn(runner, self.root, 'a/b')
            memory.learn(runner, self.root, 'a/b', force=True)
            self.assertEqual(len(calls), 2)

    def test_stale_after_project_changes(self):
        runner, _ = self._runner(json.dumps(PROFILE, ensure_ascii=False))
        with self.app.app_context():
            memory.learn(runner, self.root, 'a/b')
            (self.root / 'package.json').write_text('{"name":"a","x":1}', encoding='utf-8')
            self.assertTrue(memory.load(self.root)['stale'])

    def test_failed_refresh_keeps_the_old_profile(self):
        """一次刷新失败不能把还在用的画像判死——下一轮生成还要照它写。"""
        good, _ = self._runner(json.dumps(PROFILE, ensure_ascii=False))
        bad, _ = self._runner(error='模型超时')
        with self.app.app_context():
            memory.learn(good, self.root, 'a/b')
            result = memory.learn(bad, self.root, 'a/b', force=True)
            self.assertFalse(result['learned'])
            row = memory.row_for(self.root)
            self.assertEqual(row.status, 'ready')
            self.assertIn('模型超时', row.error)
            self.assertIsNotNone(memory.load(self.root))

    def test_failed_first_learn_marks_failed(self):
        bad, _ = self._runner(error='模型超时')
        with self.app.app_context():
            result = memory.learn(bad, self.root, 'a/b')
            self.assertFalse(result['learned'])
            row = memory.row_for(self.root)
            self.assertEqual(row.status, 'failed')
            self.assertIsNone(memory.load(self.root))

    def test_empty_reply_is_not_saved(self):
        runner, _ = self._runner('我看了一下项目，挺好的。')
        with self.app.app_context():
            result = memory.learn(runner, self.root, 'a/b')
            self.assertFalse(result['learned'])
            self.assertIn('没有 JSON', result['reason'])

    def test_forget_removes_the_row(self):
        runner, _ = self._runner(json.dumps(PROFILE, ensure_ascii=False))
        with self.app.app_context():
            memory.learn(runner, self.root, 'a/b')
            self.assertTrue(memory.forget(self.root))
            self.assertIsNone(memory.load(self.root))
            self.assertFalse(memory.forget(self.root))

    def test_snapshot_summarises(self):
        runner, _ = self._runner(json.dumps(PROFILE, ensure_ascii=False))
        with self.app.app_context():
            memory.learn(runner, self.root, 'a/b')
            snap = memory.snapshot(self.root)
            self.assertEqual(snap['status'], 'ready')
            self.assertEqual(snap['summary']['helpers'], ['loginAs', 'waitTable'])
            self.assertEqual(snap['summary']['test_dir'], 'tests')


class EmitInjectionTest(unittest.TestCase):
    STEPS = [
        {'no': 1, 'type': 'goto', 'url': 'http://x/login'},
        {'no': 2, 'type': 'fill', 'value': 'admin',
         'target': {'alias': '用户名', 'candidates': [], 'snap': {'tag': 'input'}}},
    ]

    def setUp(self):
        self.entry = {'profile': memory.parse(json.dumps(PROFILE, ensure_ascii=False))[0],
                      'stale': False}

    def test_plan_payload_carries_memory(self):
        payload = emit._plan_payload('playwright-ts', self.STEPS, '', 't', memory=None)
        self.assertNotIn('project_memory', payload)
        payload = emit._plan_payload('playwright-ts', self.STEPS, '', 't',
                                     memory=memory.for_plan(self.entry))
        self.assertEqual(payload['project_memory']['runner']['test_dir'], 'tests')

    def test_plan_prompt_tells_it_to_stop_reading(self):
        payload = emit._plan_payload('playwright-ts', self.STEPS, '', 't',
                                     memory=memory.for_plan(self.entry))
        prompt = emit._plan_prompt(payload)
        self.assertIn('project_memory', prompt)
        self.assertIn('不要再去读任何项目文件', prompt)

    def test_writer_samples_come_from_profile(self):
        detected = {'samples': [{'path': 'tests/zzz.spec.ts', 'head': '// 最近改过的半截文件'}],
                    'evidence': []}
        payload = emit._payload('playwright-ts', self.STEPS, 'tests/a.spec.ts', 't',
                                detected, [], memory=memory.for_writer(self.entry))
        self.assertEqual(len(payload['samples']), 1)
        self.assertEqual(payload['samples'][0]['path'], 'tests/login/login-success.spec.ts')
        self.assertIn('await page.goto', payload['samples'][0]['head'])

    def test_writer_samples_untouched_without_memory(self):
        detected = {'samples': [{'path': 'tests/zzz.spec.ts', 'head': '// 半截'}], 'evidence': []}
        payload = emit._payload('playwright-ts', self.STEPS, 'tests/a.spec.ts', 't',
                                detected, [], memory=None)
        self.assertEqual(payload['samples'][0]['path'], 'tests/zzz.spec.ts')
        self.assertIsNone(payload['project_memory'])

    def test_writer_prompt_states_precedence(self):
        payload = emit._payload('playwright-ts', self.STEPS, 'tests/a.spec.ts', 't',
                                {'samples': [], 'evidence': []}, [],
                                memory=memory.for_writer(self.entry))
        prompt = emit._prompt(payload)
        self.assertIn('plan（本次任务书）> project_memory', prompt)
        self.assertIn('helpers', prompt)


if __name__ == '__main__':
    unittest.main()
