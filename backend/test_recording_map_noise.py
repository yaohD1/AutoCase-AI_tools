"""扫描的抗噪声测试：噪声目录不能吃满额度，项目自己的 .gitignore 也要认。

背景（实测）：一个 Robot 项目 screenshots/ 下有 8036 张截图，而扫描有 4000 的
文件上限。目录按字典序走，screenshots 排在中文目录之前，额度被截图吃光后直接
break——四个业务目录一个都没进地图，模型只能在「libspecs + screenshots」上画地图，
产出的画像 layout/naming/helpers 全是 null。
"""
import tempfile
import unittest
from pathlib import Path

from app.services.recording import projectmap


def _code(root, rel, text='# code'):
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    return path


class NoiseQuotaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _flood(self, folder, count, suffix='.png'):
        target = self.root / folder
        target.mkdir(parents=True, exist_ok=True)
        for i in range(count):
            (target / ('shot-%04d%s' % (i, suffix))).write_text('x', encoding='utf-8')

    def test_screenshot_flood_does_not_hide_business_dirs(self):
        # 截图目录排在业务目录前面（ASCII 早于中文），旧实现会在这里断掉。
        self._flood('screenshots', 4200)
        _code(self.root, '测试用例层/登录/登录.robot')
        _code(self.root, '页面对象层/登录.resource')

        tree = projectmap.scan(self.root)
        paths = [item['path'] for item in tree['dirs']]

        self.assertIn('测试用例层', paths)
        self.assertIn('测试用例层/登录', paths)
        self.assertIn('页面对象层', paths)
        # 图片不进文件清单，也不该被算进目录的子文件数
        self.assertFalse([f for f in tree['files'] if f.endswith('.png')])
        self.assertLess(len(tree['files']), 100)

    def test_per_dir_limit_keeps_other_dirs_visible(self):
        # 单个平铺目录塞满，也不能把后面的目录挤掉
        _code(self.root, 'a/one.py')
        for i in range(projectmap.PER_DIR_FILES * 3):
            _code(self.root, 'flat/f%03d.py' % i)
        _code(self.root, 'z/last.py')

        tree = projectmap.scan(self.root)
        paths = [item['path'] for item in tree['dirs']]
        self.assertIn('z', paths)
        self.assertLessEqual(len([f for f in tree['files'] if f.startswith('flat/')]),
                             projectmap.PER_DIR_FILES)

    def test_reports_and_binaries_are_not_counted(self):
        for name in ('report.html', 'output.xml', 'log.html', 'a.zip', 'b.exe', 'c.dll'):
            _code(self.root, 'reports/' + name)
        _code(self.root, 'reports/keep.md')
        tree = projectmap.scan(self.root)
        self.assertFalse([f for f in tree['files'] if f.endswith(('.html', '.xml', '.zip', '.exe'))])


class GitignoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_gitignore_dir_and_suffix_rules(self):
        (self.root / '.gitignore').write_text(
            'screenshots/\n*.log\noutput/*\n/reports\n', encoding='utf-8')
        _code(self.root, 'shots/a.log')
        _code(self.root, 'output/run.json')
        _code(self.root, 'reports/r.txt')
        _code(self.root, 'screenshots/s.png')
        _code(self.root, 'tests/login.spec.ts')

        tree = projectmap.scan(self.root)
        paths = [item['path'] for item in tree['dirs']]
        files = tree['files']

        self.assertIn('tests', paths)
        for hidden in ('screenshots', 'output', 'reports'):
            self.assertNotIn(hidden, paths)
        self.assertFalse([f for f in files if f.endswith('.log')])
        self.assertIn('tests/login.spec.ts', files)

    def test_gitignore_negation_keeps_skipping(self):
        # `!keep.log` 这类还原规则不实现：只跳过、不还原，地图少记一条不当紧
        (self.root / '.gitignore').write_text('*.log\n!keep.log\n', encoding='utf-8')
        _code(self.root, 'logs/keep.log')
        _code(self.root, 'logs/other.log')
        tree = projectmap.scan(self.root)
        self.assertFalse([f for f in tree['files'] if f.endswith('.log')])

    def test_structure_signature_ignores_gitignored_dirs(self):
        (self.root / '.gitignore').write_text('screenshots/\n', encoding='utf-8')
        _code(self.root, 'tests/a.spec.ts')
        _code(self.root, 'screenshots/one.png')
        first = projectmap.structure_signature(self.root)
        _code(self.root, 'screenshots/two.png')
        self.assertEqual(first, projectmap.structure_signature(self.root))


class DirBudgetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_empty_deep_dirs_are_dropped_but_top_kept(self):
        (self.root / 'empty-top').mkdir()
        (self.root / 'deep' / 'no-files').mkdir(parents=True)
        _code(self.root, 'deep/has/a.py')
        tree = projectmap.scan(self.root)
        paths = [item['path'] for item in tree['dirs']]
        self.assertIn('empty-top', paths)
        self.assertIn('deep', paths)
        self.assertIn('deep/has', paths)
        self.assertNotIn('deep/no-files', paths)

    def test_dir_cap_prefers_dirs_with_more_files(self):
        for i in range(projectmap.MAX_DIRS * 2):
            _code(self.root, 'bulk/d%03d/f.py' % i)
        tree = projectmap.scan(self.root)
        self.assertLessEqual(len(tree['dirs']), projectmap.MAX_DIRS)


if __name__ == '__main__':
    unittest.main()
