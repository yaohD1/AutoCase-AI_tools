"""生成进度与写权限白名单的契约。

两件事都在「看不见的地方」出错，只能靠断言守住：
- 进度是跨窗口的唯一事实，start/mark/finish/fail 的时序错了，按钮会在两个窗口里各说各话；
- 写权限的 pattern 必须用**相对工作区路径**（opencode 内部 `path.relative(worktree, filepath)`），
  写成 `**/x` 或绝对路径对根目录文件一条都不命中，最后只剩 `*: deny`，表现是「agent 写不出文件」。
"""
import json
import tempfile
import threading
import unittest
from pathlib import Path

from app.services.opencode_runner import OpenCodeRunner
from app.services.recording import progress


class EmitProgressTest(unittest.TestCase):
    def setUp(self):
        self.progress = progress.EmitProgress()

    def test_start_puts_it_in_running_at_prepare(self):
        self.assertTrue(self.progress.start())
        snap = self.progress.snapshot()
        self.assertEqual(snap['status'], 'running')
        self.assertEqual(snap['stage'], 'prepare')
        self.assertEqual(snap['percent'], 5)
        self.assertTrue(snap['started_at'])
        self.assertEqual(snap['result'], None)
        self.assertEqual(snap['error'], None)

    def test_second_start_is_rejected_while_running(self):
        self.assertTrue(self.progress.start())
        self.assertFalse(self.progress.start(), '两个窗口同时点，只能有一个真的开始写')

    def test_mark_moves_stage_and_percent(self):
        self.progress.start()
        self.progress.mark('writing')
        snap = self.progress.snapshot()
        self.assertEqual(snap['stage'], 'writing')
        self.assertEqual(snap['percent'], 26)
        self.assertTrue(snap['cap'] > snap['percent'], 'cap 要留出前端爬升空间')
        self.assertTrue(snap['stage_at'])

    def test_unknown_stage_is_ignored(self):
        self.progress.start()
        self.progress.mark('nonsense')
        self.assertEqual(self.progress.snapshot()['stage'], 'prepare')

    def test_mark_after_finish_does_not_rewind(self):
        self.progress.start()
        self.progress.finish({'path': 'a.robot'})
        self.progress.mark('writing')
        snap = self.progress.snapshot()
        self.assertEqual(snap['status'], 'done')
        self.assertEqual(snap['percent'], 100)

    def test_finish_keeps_display_fields_and_drops_reply(self):
        self.progress.start()
        self.progress.finish({'path': 'a.robot', 'validated': True, 'generation_id': 'g1',
                              'reply': 'x' * 1000, 'changed': ['a.robot']})
        snap = self.progress.snapshot()
        self.assertEqual(snap['status'], 'done')
        self.assertEqual(snap['result']['generation_id'], 'g1')
        self.assertNotIn('reply', snap['result'], 'agent 回复可能上万字，不跟着轮询一起传')
        self.assertTrue(snap['finished_at'])

    def test_fail_keeps_message_and_details(self):
        self.progress.start()
        self.progress.fail('校验未通过', ['bad keyword'])
        snap = self.progress.snapshot()
        self.assertEqual(snap['status'], 'failed')
        self.assertEqual(snap['error']['message'], '校验未通过')
        self.assertEqual(snap['error']['details'], ['bad keyword'])

    def test_fail_after_done_is_ignored(self):
        self.progress.start()
        self.progress.finish({'path': 'a.robot'})
        self.progress.fail('迟到的错误')
        self.assertEqual(self.progress.snapshot()['status'], 'done')

    def test_reset_clears_everything(self):
        self.progress.start()
        self.progress.finish({'path': 'a.robot'})
        self.progress.reset()
        snap = self.progress.snapshot()
        self.assertEqual(snap['status'], 'idle')
        self.assertEqual(snap['percent'], 0)
        self.assertIsNone(snap['result'])

    def test_start_again_after_finish(self):
        self.progress.start()
        self.progress.finish({'path': 'a.robot'})
        self.assertTrue(self.progress.start(), '上一次做完后应当可以再生成一次')
        self.assertEqual(self.progress.snapshot()['status'], 'running')

    def test_concurrent_start_only_one_wins(self):
        results = []
        barrier = threading.Barrier(4)

        def hit():
            barrier.wait()
            results.append(self.progress.start())

        threads = [threading.Thread(target=hit) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(results.count(True), 1)


class EmitConfigPermissionTest(unittest.TestCase):
    """recording-emitter 的写权限白名单。"""

    def _config_in(self, root):
        config = Path(root) / 'opencode.json'
        config.write_text(json.dumps({
            'agent': {'recording-emitter': {'permission': {'edit': {'*': 'allow'}}}},
        }), encoding='utf-8')
        return config

    def _rules(self, writable):
        root = Path(tempfile.mkdtemp(prefix='emit-config-'))
        workspace = root / 'ws'
        workspace.mkdir()
        runner = OpenCodeRunner({'OPENCODE_CONFIG': str(self._config_in(root))})
        path, drop = runner._emit_config(writable, workspace)
        try:
            data = json.loads(Path(path).read_text(encoding='utf-8'))
            return data['agent']['recording-emitter']['permission']
        finally:
            drop()

    def test_relative_path_is_the_primary_allow_rule(self):
        permission = self._rules('test-corekg.spec.ts')
        edit = permission['edit']
        self.assertEqual(list(edit)[0], '*', 'deny 必须排在最前，规则是最后命中者生效')
        self.assertEqual(edit['*'], 'deny')
        # 这条是真正的命中项：opencode 拿 path.relative(worktree, filepath) 来比对。
        self.assertEqual(edit['test-corekg.spec.ts'], 'allow')

    def test_root_file_does_not_rely_on_globstar(self):
        edit = self._rules('a.spec.ts')['edit']
        allows = [p for p, action in edit.items() if action == 'allow']
        self.assertIn('a.spec.ts', allows)
        # `**/a.spec.ts` 留着兜底，但不能是唯一的相对形式。
        self.assertIn('./a.spec.ts', allows)
        self.assertIn('**/a.spec.ts', allows)

    def test_subdirectory_path_keeps_its_prefix(self):
        edit = self._rules('tests/login.spec.ts')['edit']
        self.assertEqual(edit['tests/login.spec.ts'], 'allow')

    def test_bash_and_task_stay_closed(self):
        permission = self._rules('a.spec.ts')
        self.assertEqual(permission['bash'], {'*': 'deny'}, 'bash 能 echo > file，必须关掉')
        self.assertEqual(permission['task'], 'deny')

    def test_no_writable_returns_original_config(self):
        root = Path(tempfile.mkdtemp(prefix='emit-config-'))
        config = self._config_in(root)
        runner = OpenCodeRunner({'OPENCODE_CONFIG': str(config)})
        path, drop = runner._emit_config(None, root)
        self.assertEqual(Path(path), config)
        drop()


if __name__ == '__main__':
    unittest.main()
