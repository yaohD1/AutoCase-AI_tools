"""guard：范围检查（git 优先、指纹回退）、越界快照 / 手动还原，以及 emitter 写权限配置。

重点：产物目录不进集合、越界不自动还原但能按需还原、git 模式下能认出新文件/删除/已被跟踪文件的改动。
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

    def test_file_scope_without_writable_uses_the_original_config(self):
        path, drop = self.runner._emit_config(None, self.dir, scope='file')
        self.assertEqual(path, self.config)
        drop()

    def test_file_scope_allows_only_the_target(self):
        path, drop = self.runner._emit_config('autocase/tests/login.spec.ts', self.dir,
                                              scope='file')
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

    def test_workspace_scope_allows_helpers_but_still_blocks_git(self):
        """默认范围：可写整个工作区（脚本常要顺带写 helper），但 .git 这类必须挡。"""
        path, drop = self.runner._emit_config(None, self.dir)
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            permission = data['agent']['recording-emitter']['permission']
            for key in ('edit', 'write'):
                self.assertEqual(permission[key]['*'], 'allow')
                for denied in ('.git', '.git/**', '.opencode/**', 'node_modules/**'):
                    self.assertEqual(permission[key][denied], 'deny',
                                     '%s 必须 deny：写 git hook 等于能执行任意命令' % denied)
            # 命令执行仍然关着：文件改动能还原，命令造成的破坏不能。
            self.assertEqual(permission['bash'], {'*': 'deny'})
            self.assertEqual(permission['task'], 'deny')
        finally:
            drop()

    def test_backslash_target_is_normalised(self):
        path, drop = self.runner._emit_config('autocase\\tests\\login.spec.ts', self.dir,
                                              scope='file')
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            rules = data['agent']['recording-emitter']['permission']['edit']
            self.assertIn('autocase/tests/login.spec.ts', rules)
            self.assertNotIn('**/autocase/tests/login.spec.ts', rules,
                             'globstar 会把「只写一个文件」放大成「任意目录同名文件都能写」')
            self.assertNotIn('**/autocase\\tests\\login.spec.ts', rules)
        finally:
            drop()

    def test_missing_emitter_agent_is_an_error(self):
        self.config.write_text('{"agent": {}}', encoding='utf-8')
        with self.assertRaises(RuntimeError):
            self.runner._emit_config('a.ts', self.dir)

    def test_missing_emitter_agent_is_an_error(self):
        self.config.write_text('{"agent": {}}', encoding='utf-8')
        with self.assertRaises(RuntimeError):
            self.runner._emit_config('a.ts', self.dir)


class BackupTest(unittest.TestCase):
    """越界快照与手动还原：capture 记下生成前的内容，revert 按需写回。"""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='guard-backup-'))
        _init_repo(self.root)
        (self.root / 'src').mkdir()
        (self.root / 'src' / 'keep.ts').write_text('original keep\n', encoding='utf-8')
        (self.root / 'src' / 'gone.ts').write_text('original gone\n', encoding='utf-8')
        _git(self.root, 'add', '-A', check=True)
        _git(self.root, 'commit', '-m', 'seed', check=True)
        self.snap = guard.snapshot(self.root)
        # 模拟一次生成：改一个、删一个、新增一个
        (self.root / 'src' / 'keep.ts').write_text('model version\n', encoding='utf-8')
        (self.root / 'src' / 'gone.ts').unlink()
        (self.root / 'tests').mkdir()
        (self.root / 'tests' / 'helper.ts').write_text('export const x = 1;\n', encoding='utf-8')

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def capture(self):
        changed = guard.changed(self.snap)
        outside = guard.violations(changed, 'tests/a.spec.ts')
        return guard.capture(self.snap, outside)

    def test_capture_classifies_modified_deleted_and_new(self):
        token, entries = self.capture()
        self.assertTrue(token)
        by_path = {e['path']: e['action'] for e in entries}
        self.assertEqual(by_path['src/keep.ts'], 'restore')
        self.assertEqual(by_path['src/gone.ts'], 'restore')
        self.assertEqual(by_path['tests/helper.ts'], 'delete')

    def test_revert_restores_content_and_deletes_new_files(self):
        token, _ = self.capture()
        result = guard.revert(self.root, token)
        self.assertEqual(sorted(row['path'] for row in result['reverted']),
                         ['src/gone.ts', 'src/keep.ts', 'tests/helper.ts'])
        self.assertEqual((self.root / 'src' / 'keep.ts').read_text(encoding='utf-8'),
                         'original keep\n')
        self.assertEqual((self.root / 'src' / 'gone.ts').read_text(encoding='utf-8'),
                         'original gone\n')
        self.assertFalse((self.root / 'tests' / 'helper.ts').exists())

    def test_revert_keeps_a_copy_of_what_the_model_wrote(self):
        """还原本身也要可逆：模型写的版本留在 current/ 下，误点了能捞回来。"""
        token, _ = self.capture()
        guard.revert(self.root, token, ['src/keep.ts'])
        current = self.root / guard.BACKUP_DIR / token / 'current' / 'src' / 'keep.ts'
        self.assertEqual(current.read_text(encoding='utf-8'), 'model version\n')

    def test_revert_can_be_limited_to_one_file(self):
        token, _ = self.capture()
        result = guard.revert(self.root, token, ['tests/helper.ts'])
        self.assertEqual([row['path'] for row in result['reverted']], ['tests/helper.ts'])
        self.assertFalse((self.root / 'tests' / 'helper.ts').exists())
        # 没点的那两个保持模型写的样子
        self.assertEqual((self.root / 'src' / 'keep.ts').read_text(encoding='utf-8'),
                         'model version\n')

    def test_path_traversal_is_ignored(self):
        token, _ = self.capture()
        result = guard.revert(self.root, token, ['../outside.txt', '/etc/passwd', 'a/../b'])
        self.assertEqual(result['reverted'], [])
        self.assertEqual((self.root / 'src' / 'keep.ts').read_text(encoding='utf-8'),
                         'model version\n')

    def test_unknown_backup_id_is_an_error(self):
        with self.assertRaises(guard.GuardError):
            guard.revert(self.root, 'no-such-id')

    def test_unsafe_backup_id_is_rejected(self):
        self.assertIsNone(guard.read_manifest(self.root, '../etc'))

    def test_fingerprint_mode_cannot_restore_content(self):
        """不是 git 仓库时只有指纹，capture 必须如实标 skip，不能假装能还原。"""
        plain = Path(tempfile.mkdtemp(prefix='guard-plain-'))
        try:
            (plain / 'a.ts').write_text('a', encoding='utf-8')
            snap = guard.snapshot(plain)
            self.assertEqual(snap.mode, 'files')
            (plain / 'a.ts').write_text('b', encoding='utf-8')
            token, entries = guard.capture(snap, ['a.ts'])
            self.assertTrue(token)
            self.assertEqual(entries[0]['action'], 'skip')
            self.assertIsNotNone(guard.read_manifest(plain, token))
        finally:
            shutil.rmtree(plain, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
