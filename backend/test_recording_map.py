"""项目地图的测试：扫描、渲染、以及与画像学习的接线。

不调真实模型：学习那条用假 runner 喂一段 JSON，只验「地图落库 + 落地文件」这两件事。
"""
import json
import tempfile
import unittest
from pathlib import Path

from flask import Flask

from app.models import db
from app.services.recording import memory, projectmap


def _workspace(root):
    """一个像样的小项目：有一层目录、一个测试目录、一个依赖清单、还有该跳过的目录。"""
    root = Path(root)
    (root / 'package.json').write_text('{"name":"demo"}', encoding='utf-8')
    (root / 'playwright.config.ts').write_text('export default {}', encoding='utf-8')
    (root / 'tests' / 'login').mkdir(parents=True)
    (root / 'tests' / 'login' / 'login-success.spec.ts').write_text('// a', encoding='utf-8')
    (root / 'tests' / 'api').mkdir()
    (root / 'tests' / 'api' / 'auth.spec.ts').write_text('// b', encoding='utf-8')
    (root / 'src' / 'pages' / 'router-admin').mkdir(parents=True)
    (root / 'src' / 'pages' / 'router-admin' / 'LoginPage.ts').write_text('export {}', encoding='utf-8')
    # 这些必须被忽略：依赖、产物、隐藏目录
    for junk in ('node_modules/lodash', 'reports/playwright', '.git/objects', '.autocase'):
        (root / junk).mkdir(parents=True, exist_ok=True)
        (root / junk / 'x.ts').write_text('junk', encoding='utf-8')
    return root


class ScanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = _workspace(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_scan_skips_noise_and_records_three_levels(self):
        tree = projectmap.scan(self.root)
        paths = [item['path'] for item in tree['dirs']]
        self.assertIn('tests', paths)
        self.assertIn('tests/login', paths)
        # 第三层也要在：src/pages/router-admin 里的封装正是写手要复用的东西
        self.assertIn('src/pages/router-admin', paths)
        for junk in ('node_modules', 'reports', '.git', '.autocase'):
            self.assertNotIn(junk, paths)

    def test_scan_counts_direct_files(self):
        tree = projectmap.scan(self.root)
        tests = next(item for item in tree['dirs'] if item['path'] == 'tests')
        login = next(item for item in tree['dirs'] if item['path'] == 'tests/login')
        self.assertEqual(tests['files'], 0)      # 直接子文件只有两个子目录
        self.assertEqual(login['files'], 1)

    def test_key_files_put_config_and_pages_first(self):
        names = projectmap.key_files(projectmap.scan(self.root))
        self.assertIn('package.json', names[:3])
        self.assertIn('playwright.config.ts', names[:3])
        self.assertIn('src/pages/router-admin/LoginPage.ts', names)

    def test_prompt_block_lists_tree_and_forbids_guessing(self):
        block = projectmap.prompt_block(projectmap.scan(self.root))
        self.assertIn('tests/login (1)', block)
        self.assertIn('脚本扫到的项目结构', block)
        # 路径要能照抄：提示词里明确说了 areas 的 path 得和这里一致
        self.assertIn('路径必须和上面完全一致', block)

    def test_structure_signature_notices_new_dir(self):
        before = projectmap.structure_signature(self.root)
        (self.root / 'tests' / 'smoke').mkdir()
        after = projectmap.structure_signature(self.root)
        self.assertNotEqual(before, after)


class RenderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = _workspace(self.tmp.name)
        self.tree = projectmap.scan(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def _profile(self, **extra):
        profile = {
            'runner': {'test_dir': './tests', 'command': 'npx playwright test'},
            'naming': 'kebab-case',
            'helpers': [{'name': 'LoginPage.login', 'file': 'src/pages/router-admin/LoginPage.ts',
                         'signature': 'login(user, pass)', 'use': '先 open() 再 login()'}],
            'areas': [
                {'path': 'tests/login', 'kind': 'tests', 'role': '登录页表单校验'},
                {'path': 'src/pages/router-admin/LoginPage.ts', 'kind': 'pages',
                 'role': '登录页封装', 'use': 'new LoginPage(page) → open() → login()'},
                {'path': 'package.json', 'kind': 'config', 'role': '依赖与脚本'},
            ],
        }
        profile.update(extra)
        return profile

    def test_sections_are_grouped_by_kind(self):
        text = projectmap.render(self._profile(), self.tree, name='demo')
        self.assertTrue(text.startswith('# 项目地图 · demo'))
        for title in ('## 测试', '## 页面封装', '## 配置', '## 写法', '## 现成封装'):
            self.assertIn(title, text)
        # 用法要跟在路径下面，写手才知道先 new 再 open
        self.assertIn('先 open()', text)
        self.assertIn('new LoginPage(page)', text)

    def test_invented_paths_are_dropped(self):
        profile = self._profile()
        profile['areas'].append({'path': 'src/pages/Ghost.ts', 'kind': 'pages', 'role': '编的'})
        text = projectmap.render(profile, self.tree, name='demo')
        self.assertNotIn('Ghost.ts', text)

    def test_falls_back_to_scanned_tests_when_no_areas(self):
        profile = self._profile()
        profile.pop('areas')
        text = projectmap.render(profile, self.tree, name='demo')
        self.assertIn('## 测试', text)
        self.assertIn('tests/login', text)
        self.assertNotIn('## 页面封装', text)

    def test_keeps_empty_profile_usable(self):
        text = projectmap.render({}, self.tree, name='demo')
        self.assertIn('# 项目地图', text)
        self.assertNotIn('## 现成封装', text)

    def test_size_is_capped(self):
        profile = self._profile()
        profile['naming'] = 'x' * 9000
        text = projectmap.render(profile, self.tree, name='demo')
        self.assertLessEqual(len(text), projectmap.MAX_MAP_CHARS + 20)
        self.assertIn('已截断', text)


class WriteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_writes_into_autocase_dir(self):
        rel = projectmap.write(self.root, '# 项目地图\n')
        self.assertEqual(rel, projectmap.MAP_REL)
        written = (self.root / projectmap.MAP_REL).read_text(encoding='utf-8')
        self.assertIn('项目地图', written)
        self.assertEqual(projectmap.read(self.root), written)

    def test_unwritable_root_is_not_fatal(self):
        # 根是个文件而不是目录：mkdir 必然失败，这时只能放弃写文件，不能抛。
        broken = self.root / 'not-a-dir'
        broken.write_text('x', encoding='utf-8')
        self.assertIsNone(projectmap.write(broken, '# map'))
        self.assertEqual(projectmap.read(broken), '')


PROFILE = {
    'framework': 'playwright-ts',
    'runner': {'test_dir': './tests', 'command': 'npx playwright test'},
    'layout': 'tests/<module>/*.spec.ts',
    'naming': 'kebab-case，标题中文',
    'case_shape': 'plain test',
    'helpers': [{'name': 'LoginPage.login', 'file': 'src/pages/router-admin/LoginPage.ts',
                 'signature': 'login(user, pass)', 'use': '先 open()'}],
    'assertions': ['toBeVisible'],
    'snippet': {'file': 'tests/login/login-success.spec.ts',
                'code': "test('a', async ({ page }) => { await page.goto('/login'); });"},
    'areas': [
        {'path': 'tests/login', 'kind': 'tests', 'role': '登录页表单校验'},
        {'path': 'package.json', 'kind': 'config', 'role': '依赖与脚本'},
    ],
    'read_files': ['package.json'],
    'confidence': 'high',
}


class LearnTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = _workspace(self.tmp.name)
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
            # Windows 上 sqlite 文件被连接占着就删不掉，先放掉连接池。
            db.engine.dispose()
        self.tmp.cleanup()
        try:
            self.db_tmp.cleanup()
        except (PermissionError, OSError):
            pass

    def _runner(self, reply):
        prompts = []

        class Fake:
            def emit_profile(self, workspace, model, prompt, timeout=300):
                prompts.append(prompt)
                return reply
        return Fake(), prompts

    def test_learn_stores_map_and_writes_file(self):
        runner, prompts = self._runner(json.dumps(PROFILE, ensure_ascii=False))
        with self.app.app_context():
            result = memory.learn(runner, self.root, 'volcengine/deepseek')
            self.assertTrue(result['learned'])
            snap = memory.snapshot(self.root)
            self.assertIn('# 项目地图', snap['map'])
            self.assertEqual(snap['summary']['areas'], 2)
            on_disk = (self.root / projectmap.MAP_REL).read_text(encoding='utf-8')
            self.assertEqual(on_disk, snap['map'])
        # 扫好的目录树要进提示词：模型不该再花轮次自己 ls
        self.assertIn('脚本扫到的项目结构', prompts[0])
        self.assertIn('tests/login (1)', prompts[0])

    def test_plan_gets_map_only(self):
        runner, _ = self._runner(json.dumps(PROFILE, ensure_ascii=False))
        with self.app.app_context():
            memory.learn(runner, self.root, 'volcengine/deepseek')
            entry = memory.load(self.root)
            plan = memory.for_plan(entry)
            # 有地图就不再重复给 layout / naming 那些字段：同一段话说两遍只会打架
            self.assertEqual(list(plan), ['map'])
            writer = memory.for_writer(entry)
            self.assertIn('map', writer)
            self.assertIn('snippet', writer)

    def test_without_map_plan_falls_back_to_fields(self):
        plan = memory.for_plan({'profile': {'layout': 'a', 'naming': 'b'}})
        self.assertEqual(plan, {'layout': 'a', 'naming': 'b'})


if __name__ == '__main__':
    unittest.main()
