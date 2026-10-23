"""guard：范围检查（git 优先、指纹回退）与 emitter 写权限配置。

重点：产物目录不进集合、越界只报告不回滚、git 模式下能认出新文件/删除/已被跟踪文件的改动。
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.services.opencode_runner import OpenCodeRunner
from app.services.recording import guard


def _git(cwd, *args, check=False):
    result = subprocess.run(
        ['git', '-C', str(cwd)] + list(args),
        capture_output=True, text=True, encoding='utf-8', errors='replace',
    )
    if check and result.returncode != 0:
        raise AssertionError('git %s failed: %s' % (args[0], result.stderr))
    return result


def _init_repo(root):
    _git(root, 'init', check=True)
    _git(root, 'config', 'user.email', 'test@example.com', check=True)
    _git(root, 'config', 'user.name', 'Test', check=True)
    _git(root, 'config', 'commit.gpgsign', 'false', check=True)
    (root / 'README.md').write_text('seed', encoding='utf-8')
    _git(root, 'add', 'README.md', check=True)
    _git(root, 'commit', '-m', 'init', check=True)


class GuardGitTest(unittest.TestCase):
    """工作区是 git 仓库顶层时走 git。"""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='guard-git-'))
        _init_repo(self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_uses_git_mode_when_workspace_is_repo_root(self):
        self.assertEqual(guard.snapshot(self.root).mode, 'git')

    def test_detects_new_modified_and_deleted_files(self):
        (self.root / 'src').mkdir()
        (self.root / 'src' / 'keep.ts').write_text('a', encoding='utf-8')
        _git(self.root, 'add', '.', check=True)
        _git(self.root, 'commit', '-m', 'src', check=True)

        before = guard.snapshot(self.root)
        (self.root / 'src' / 'new.ts').write_text('b', encoding='utf-8')
        (self.root / 'src' / 'keep.ts').write_text('aa', encoding='utf-8')
        (self.root / 'README.md').unlink()
        self.assertEqual(guard.changed(before), ['README.md', 'src/keep.ts', 'src/new.ts'])

    def test_artifact_dirs_are_ignored(self):
        (self.root / '.gitignore').write_text('reports/\nmidscene_run/\n', encoding='utf-8')
        _git(self.root, 'add', '.gitignore', check=True)
        _git(self.root, 'commit', '-m', 'ignore', check=True)

        before = guard.snapshot(self.root)
        (self.root / 'reports').mkdir()
        (self.root / 'reports' / 'big.html').write_text('x' * 100, encoding='utf-8')
        (self.root / 'midscene_run').mkdir()
        (self.root / 'midscene_run' / 'run.zip').write_bytes(b'x')
        self.assertEqual(guard.changed(before), [])

    def test_untracked_file_modified_during_run_is_detected(self):
        loose = self.root / 'notes.txt'
        loose.write_text('v1', encoding='utf-8')
        before = guard.snapshot(self.root)
        loose.write_text('v2', encoding='utf-8')
        self.assertEqual(guard.changed(before), ['notes.txt'])

    def test_pre_existing_dirty_file_is_not_blamed(self):
        # 开始时就已经改过、生成期间没再动：不该算越界。
        (self.root / 'README.md').write_text('dirty before run', encoding='utf-8')
        before = guard.snapshot(self.root)
        self.assertEqual(guard.changed(before), [])

    def test_subdirectory_of_a_repo_falls_back_to_fingerprints(self):
        sub = self.root / 'packages' / 'app'
        sub.mkdir(parents=True)
        (sub / 'a.ts').write_text('a', encoding='utf-8')
        self.assertEqual(guard.snapshot(sub).mode, 'files')


class GuardFilesTest(unittest.TestCase):
    """不是 git 仓库时退回指纹。"""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='guard-files-'))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_detects_change_without_reading_content(self):
        (self.root / 'a.ts').write_text('a', encoding='utf-8')
        before = guard.snapshot(self.root)
        self.assertEqual(before.mode, 'files')
        (self.root / 'a.ts').write_text('bb', encoding='utf-8')
        (self.root / 'b.ts').write_text('b', encoding='utf-8')
        self.assertEqual(guard.changed(before), ['a.ts', 'b.ts'])

    def test_gitignore_dirs_are_excluded_without_git(self):
        (self.root / '.gitignore').write_text('# 产物\nreports/\n*.log\n', encoding='utf-8')
        before = guard.snapshot(self.root)
        (self.root / 'reports').mkdir()
        (self.root / 'reports' / 'big.html').write_text('x' * 10, encoding='utf-8')
        (self.root / 'test-results').mkdir()
        (self.root / 'test-results' / 'r.json').write_text('{}', encoding='utf-8')
        (self.root / 'tsconfig.tsbuildinfo').write_text('{}', encoding='utf-8')
        (self.root / 'real.ts').write_text('a', encoding='utf-8')
        self.assertEqual(guard.changed(before), ['real.ts'])

    def test_huge_workspace_does_not_fail_on_size(self):
        # 老实现会因为「单文件超过 2MB，无法安全备份」直接报错，现在只 stat，不读内容。
        big = self.root / 'midscene_run' / 'report'
        big.mkdir(parents=True)
        (big / 'merged.html').write_bytes(b'x' * (5 * 1024 * 1024))
        (self.root / 'a.ts').write_text('a', encoding='utf-8')
        before = guard.snapshot(self.root)
        (self.root / 'a.ts').write_text('b', encoding='utf-8')
        self.assertEqual(guard.changed(before), ['a.ts'])

    def test_violations_excludes_the_target(self):
        self.assertEqual(guard.violations(['a/b.ts', 'c.ts'], 'a/b.ts'), ['c.ts'])
        self.assertEqual(guard.violations(['a\\b.ts'], 'a/b.ts'), [])


class EmitConfigTest(unittest.TestCase):
    """emitter 的写权限配置：只放行目标文件。"""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix='emit-cfg-'))
        self.config = self.dir / 'opencode.json'
        self.config.write_text(json.dumps({
            'agent': {'recording-emitter': {'mode': 'subagent', 'prompt': 'x'}},
            'tools': {'playwright*': False},
        }), encoding='utf-8')
        self.runner = OpenCodeRunner({'OPENCODE_CONFIG': str(self.config)})

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_without_writable_uses_the_original_config(self):
        path, drop = self.runner._emit_config(None, self.dir)
        self.assertEqual(path, self.config)
        drop()

    def test_permission_allows_only_the_target(self):
        path, drop = self.runner._emit_config('autocase/tests/login.spec.ts', self.dir)
        try:
            self.assertEqual(path.parent, self.dir, '临时配置必须和原配置同目录，否则 {file:} 提示词会失效')
            data = json.loads(path.read_text(encoding='utf-8'))
            permission = data['agent']['recording-emitter']['permission']
            for key in ('edit', 'write'):
                self.assertEqual(permission[key]['*'], 'deny')
                allowed = [pat for pat, action in permission[key].items() if action == 'allow']
                # 权限是按绝对路径匹配的，所以放行规则里必须有能覆盖目标的那一条。
                self.assertTrue(any(pat.endswith('autocase/tests/login.spec.ts') for pat in allowed), allowed)
                self.assertTrue(all(pat.endswith('login.spec.ts') or pat.endswith('tests/login.spec.ts')
                                    for pat in allowed), allowed)
            self.assertEqual(permission['bash'], {'*': 'deny'})
            self.assertEqual(permission['task'], 'deny')
            # 原配置的其余部分必须原样保留，否则 serve 起来就少东西。
            self.assertEqual(data['tools'], {'playwright*': False})
        finally:
            drop()
        self.assertFalse(path.exists(), '临时配置用完必须删掉')

    def test_backslash_target_is_normalised(self):
        path, drop = self.runner._emit_config('autocase\\tests\\login.spec.ts', self.dir)
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            rules = data['agent']['recording-emitter']['permission']['edit']
            self.assertIn('**/autocase/tests/login.spec.ts', rules)
            self.assertNotIn('**/autocase\\tests\\login.spec.ts', rules)
        finally:
            drop()

    def test_missing_emitter_agent_is_an_error(self):
        self.config.write_text('{"agent": {}}', encoding='utf-8')
        with self.assertRaises(RuntimeError):
            self.runner._emit_config('a.ts', self.dir)


if __name__ == '__main__':
    unittest.main()
