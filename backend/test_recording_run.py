"""执行闭环的单元测试：报告解析、环境错误识别、修复轮数与早停。

不启动真实浏览器：真实执行由人工在临时工作区验过（见 .workbuddy/memory），
这里只锁住「解析得对、该修才修、修不动就停」这三条逻辑。
"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app.services.recording import emit, toolchain


def _report(failures=None, expected=0, unexpected=0, duration=1200, errors=None):
    specs = []
    for title, message, line in failures or []:
        specs.append({
            'title': title,
            'tests': [{'results': [{'status': 'failed', 'error': {'message': message,
                                                                 'location': {'line': line}}}]}],
        })
    return {
        'stats': {'expected': expected, 'unexpected': unexpected, 'flaky': 0, 'skipped': 0,
                  'duration': duration},
        'errors': errors or [],
        'suites': [{'specs': specs, 'suites': []}],
    }


def _workspace(root):
    """最小可用工作区：有 playwright 可执行文件，不需要真的跑起来。"""
    (Path(root) / 'node_modules' / '.bin').mkdir(parents=True)
    for name in ('playwright.cmd', 'playwright'):
        (Path(root) / 'node_modules' / '.bin' / name).write_text('', encoding='utf-8')
    (Path(root) / 'playwright.config.ts').write_text('export default {}', encoding='utf-8')


class RunParseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        _workspace(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, report, returncode=0):
        proc = SimpleNamespace(returncode=returncode, stdout=json.dumps(report), stderr='')
        with mock.patch.object(toolchain.subprocess, 'run', return_value=proc):
            return toolchain.run_playwright(self.root, 'tests/a.spec.ts', None)

    def test_passing_run(self):
        result = self._run(_report(expected=1))
        self.assertTrue(result['ran'])
        self.assertTrue(result['ok'])
        self.assertEqual((result['total'], result['passed']), (1, 1))
        self.assertEqual(result['duration_ms'], 1200)
        # 临时配置不能留在用户仓库里
        self.assertFalse((self.root / '.autocase.verify.config.ts').exists())

    def test_failure_carries_line_and_message(self):
        result = self._run(_report([('登录', 'Expected: "a"\nReceived: "b"', 12)], unexpected=1),
                           returncode=1)
        self.assertFalse(result['ok'])
        self.assertIn('登录（第 12 行）', result['errors'][0])
        self.assertIn('Received', result['errors'][0])

    def test_env_error_is_marked(self):
        result = self._run(_report([('首页', 'page.goto: net::ERR_CONNECTION_REFUSED', 3)],
                                   unexpected=1), returncode=1)
        self.assertTrue(result['env_error'])

    def test_no_tests_found_is_not_a_run(self):
        # 一个用例都没收集到 ≠ 跑了没过：配置错时 stats 全 0，只看 stats 会误判成「跑通了」；
        # 但把它当「失败」又会让修复循环去改一个从未被执行过的文件。所以是「没跑起来」。
        spec = self.root / 'tests' / 'a.spec.ts'
        spec.parent.mkdir(parents=True, exist_ok=True)
        spec.write_text("test('x', async () => {});\n", encoding='utf-8')
        result = self._run(_report(errors=[{'message': 'No tests found'}]))
        self.assertFalse(result['ran'])
        self.assertFalse(result.get('ok'))
        self.assertIn('没收集到', result['reason'])
        self.assertIn('No tests found', result['tail'])

    def test_no_test_declaration_is_named_in_reason(self):
        # 文件在、路径对，但里面没有 test()：原因要点明，而不是笼统甩一句「配置问题」。
        spec = self.root / 'tests' / 'a.spec.ts'
        spec.parent.mkdir(parents=True, exist_ok=True)
        spec.write_text('export const helper = 1;\n', encoding='utf-8')
        result = self._run(_report(errors=[{'message': 'No tests found'}]))
        self.assertFalse(result['ran'])
        self.assertIn('没有 test()', result['reason'])

    def test_load_error_still_counts_as_failure(self):
        # 与上面区分开：文件加载失败（语法/import 错）是真失败，必须能进修复循环。
        result = self._run(_report(errors=[{'message': "Error: a.spec.ts:3:1 - Unexpected token"}],
                                   expected=0), returncode=1)
        self.assertTrue(result['ran'])
        self.assertFalse(result['ok'])
        self.assertIn('Unexpected token', result['errors'][0])

    def test_missing_playwright_bin(self):
        empty = tempfile.TemporaryDirectory()
        try:
            result = toolchain.run_playwright(Path(empty.name), 'tests/a.spec.ts', None)
            self.assertFalse(result['ran'])
            self.assertIn('@playwright/test', result['reason'])
        finally:
            empty.cleanup()


class RunRepairTest(unittest.TestCase):
    def _run(self, results):
        with mock.patch.object(emit.toolchain, 'run_playwright', side_effect=results) as run:
            runner = mock.Mock()
            out = emit._run_with_repair(runner, Path('.'), 'p/m', 'tests/a.spec.ts', None)
        return out, runner, run

    def test_repairs_then_passes(self):
        out, runner, run = self._run([
            {'ran': True, 'ok': False, 'errors': ['strict mode violation: 2 elements'], 'env_error': False},
            {'ran': True, 'ok': True, 'total': 1, 'passed': 1},
        ])
        self.assertTrue(out['ok'])
        self.assertEqual(runner.emit_recording.call_count, 1)
        prompt = runner.emit_recording.call_args[0][2]
        self.assertIn('登录态', prompt)
        self.assertIn('不允许删步骤', prompt)

    def test_same_error_stops_after_one_round(self):
        same = {'ran': True, 'ok': False, 'errors': ['Timeout 5000ms waiting for locator'], 'env_error': False}
        out, runner, run = self._run([same, dict(same), dict(same)])
        self.assertFalse(out['ok'])
        self.assertEqual(runner.emit_recording.call_count, 1)

    def test_env_error_not_repaired(self):
        out, runner, run = self._run([
            {'ran': True, 'ok': False, 'errors': ['net::ERR_CONNECTION_REFUSED'], 'env_error': True},
        ])
        self.assertFalse(out['ok'])
        self.assertEqual(runner.emit_recording.call_count, 0)
        self.assertIn('不可达', out['note'])
        self.assertFalse(out['auth'])

    def test_missing_auth_is_explained(self):
        same = {'ran': True, 'ok': False, 'errors': ['locator not found'], 'env_error': False}
        out, runner, run = self._run([same, dict(same)])
        self.assertFalse(out['auth'])
        self.assertIn('登录态', out['note'])

    def test_not_ran_is_reported_as_is(self):
        out, runner, run = self._run([{'ran': False, 'reason': 'Playwright 没能启动'}])
        self.assertFalse(out['ran'])
        self.assertEqual(runner.emit_recording.call_count, 0)


class RunSignatureTest(unittest.TestCase):
    def test_numbers_are_ignored(self):
        a = {'errors': ['14 × locator resolved to <h1>']}
        b = {'errors': ['3 × locator resolved to <h1>']}
        self.assertEqual(emit._run_signature(a), emit._run_signature(b))

    def test_different_errors_differ(self):
        self.assertNotEqual(emit._run_signature({'errors': ['a']}),
                            emit._run_signature({'errors': ['b']}))


if __name__ == '__main__':
    unittest.main()
