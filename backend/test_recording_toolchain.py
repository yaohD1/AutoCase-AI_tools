import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.services.recording import detect, toolchain

# 需要真实的 Robot Framework + SeleniumLibrary 环境。未设置时跳过这些用例。
RF_PY = os.environ.get('RECORDING_TEST_RF_PYTHON')
requires_rf = unittest.skipUnless(RF_PY, 'set RECORDING_TEST_RF_PYTHON to a python with robotframework-seleniumlibrary')

VALID_ROBOT = (
    '*** Settings ***\n'
    'Library    SeleniumLibrary\n\n'
    '*** Test Cases ***\n'
    'Valid Steps\n'
    '    Input Text    id:q    hello\n'
    "    Click Element    xpath://button[text()='提交']\n"
)
BAD_ROBOT = (
    '*** Settings ***\n'
    'Library    SeleniumLibrary\n\n'
    '*** Test Cases ***\n'
    'Unknown Keyword\n'
    '    Input Text Typo    id:q    hello\n'
)


def _workspace(files):
    root = Path(tempfile.mkdtemp(prefix='ws-'))
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
    return root


class DetectTest(unittest.TestCase):
    def tearDown(self):
        for path in getattr(self, '_dirs', []):
            shutil.rmtree(path, ignore_errors=True)

    def _ws(self, files):
        root = _workspace(files)
        self._dirs = getattr(self, '_dirs', []) + [root]
        return root

    def test_robot_files_are_detected_with_requirement_hint_and_sample(self):
        ws = self._ws({
            'tests/login.robot': '*** Test Cases ***\nLogin\n    Log    hi\n',
            'requirements.txt': 'robotframework-seleniumlibrary==6.8.0\n',
        })
        got = detect.detect(ws, python=sys.executable)
        self.assertEqual(got['framework'], 'robot-selenium')
        specs = [e['value'] for e in got['evidence'] if e['kind'] == 'requirement']
        self.assertIn('robotframework-seleniumlibrary==6.8.0', specs)
        self.assertEqual(got['samples'][0]['path'], 'tests/login.robot')

    def test_package_json_with_playwright_is_playwright_ts(self):
        ws = self._ws({'package.json': '{"devDependencies": {"@playwright/test": "1.60.0"}}'})
        self.assertEqual(detect.detect(ws, python=sys.executable)['framework'], 'playwright-ts')

    def test_unknown_project_is_reported_not_rejected(self):
        ws = self._ws({'README.md': 'hello'})
        got = detect.detect(ws, python=sys.executable)
        self.assertIsNone(got['framework'])
        self.assertTrue(any(e['kind'] == 'framework' and e['value'] is None for e in got['evidence']))

    def test_robot_version_is_reported_for_robot_projects(self):
        # 写手要按目标环境的版本约束语法：只拿到 SeleniumLibrary 版本不够。
        ws = self._ws({'tests/a.robot': '*** Test Cases ***\nA\n    Log    hi\n'})
        with mock.patch.object(detect, 'library_version', return_value='3.0.0'), \
                mock.patch.object(detect, 'robot_version', return_value='3.1.2'):
            got = detect.detect(ws, python='python')
        self.assertEqual(got['robot_version'], '3.1.2')
        self.assertTrue(any(e['kind'] == 'robotframework' and e['value'] == '3.1.2'
                            for e in got['evidence']))

    def test_workspace_venv_is_preferred_over_path(self):
        ws = self._ws({'.venv/Scripts/python.exe': ''})
        python, source = detect.find_python(ws)
        self.assertTrue(python.endswith(os.path.join('.venv', 'Scripts', 'python.exe')))
        self.assertIn('.venv', source)

    def test_samples_skip_virtualenv_directories(self):
        ws = self._ws({
            '.venv/lib/site-packages/junk.robot': 'x',
            'tests/real.robot': '*** Test Cases ***\nA\n    Log    1\n',
        })
        paths = [s['path'] for s in detect.sample_robot_files(ws)]
        self.assertEqual(paths, ['tests/real.robot'])

    def test_playwright_samples_are_collected_too(self):
        """PW 项目以前拿到的 samples 永远是空数组，提示词却让模型「照 samples 的风格写」。"""
        ws = self._ws({
            'tests/login/login.test.ts': 'test("login", async () => {});\n',
            'tests/other/x.spec.ts': 'test("x", async () => {});\n',
            'node_modules/pkg/y.spec.ts': 'test("noise", async () => {});\n',
            'src/pages/LoginPage.ts': 'export class LoginPage {}\n',
        })
        got = detect.sample_test_files(ws)
        paths = sorted(s['path'] for s in got)
        self.assertEqual(paths, ['tests/login/login.test.ts', 'tests/other/x.spec.ts'])
        self.assertIn('login', got[0]['head'] + got[1]['head'])


class ToolchainTest(unittest.TestCase):
    @staticmethod
    def _tsc_workspace():
        """造一个「装了 tsc」的假项目，把 _run 挡掉，只验判定逻辑。"""
        ws = _workspace({})
        bin_dir = ws / 'node_modules' / '.bin'
        bin_dir.mkdir(parents=True)
        (bin_dir / 'tsc.cmd').write_text('', encoding='utf-8')
        return ws

    def test_tsc_missing_is_an_error_not_a_skip(self):
        ws = _workspace({'package.json': '{}'})
        try:
            with self.assertRaises(toolchain.ToolchainError):
                toolchain.tsc_check(ws)
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_tsc_ignores_baseline_errors_outside_the_target(self):
        """全项目 tsc 会撞上项目本来就有的类型错误，那不该算到这次生成头上。

        真实踩过的坑：基线里的 seed.spec.ts 报 TS6133，每次生成都判失败，
        然后把已经写对的目标文件删了。
        """
        ws = self._tsc_workspace()
        baseline = 'src/fixtures/seed.spec.ts(3,7): error TS6133: var is declared but never read\n'
        try:
            with mock.patch.object(toolchain, '_run') as run:
                run.return_value = mock.Mock(returncode=1, stdout=baseline, stderr='')
                ok, out = toolchain.tsc_check(ws, target_path='tests/login/login.spec.ts')
            self.assertTrue(ok, '目标文件一行错都没有，就该放行')
            self.assertEqual(out, '')
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_tsc_reports_errors_that_belong_to_the_target(self):
        ws = self._tsc_workspace()
        target = 'tests/login/login.spec.ts'
        stdout = target + '(12,5): error TS2322: Type string is not assignable\n'
        try:
            with mock.patch.object(toolchain, '_run') as run:
                run.return_value = mock.Mock(returncode=1, stdout=stdout, stderr='')
                ok, out = toolchain.tsc_check(ws, target_path=target)
            self.assertFalse(ok)
            self.assertIn('TS2322', out)
            self.assertIn(target, out)
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_dryrun_without_robot_is_an_error_not_a_pass(self):
        ws = _workspace({'t.robot': VALID_ROBOT})
        try:
            if RF_PY and Path(RF_PY).resolve() == Path(sys.executable).resolve():
                self.skipTest('the test interpreter has robotframework installed')
            with self.assertRaises(toolchain.ToolchainError):
                toolchain.dryrun(sys.executable, ws / 't.robot')
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    @requires_rf
    def test_export_keywords_lists_selenium_keywords_with_args(self):
        got = toolchain.export_keywords(RF_PY)
        by_name = {k['name']: k['args'] for k in got['keywords']}
        self.assertIn('Input Text', by_name)
        self.assertIn('text', by_name['Input Text'])
        self.assertIn('Click Element', by_name)

    @requires_rf
    def test_dryrun_passes_valid_file_and_catches_unknown_keyword(self):
        ws = _workspace({'good.robot': VALID_ROBOT, 'bad.robot': BAD_ROBOT})
        try:
            ok, _ = toolchain.dryrun(RF_PY, ws / 'good.robot')
            self.assertTrue(ok)
            bad_ok, output = toolchain.dryrun(RF_PY, ws / 'bad.robot')
            self.assertFalse(bad_ok)
            self.assertIn('Input Text Typo', output)
        finally:
            shutil.rmtree(ws, ignore_errors=True)


# libdoc 的 XML spec 有两代写法，解析要都能吃：
#   RF 3.x —— <arg>path=None</arg>
#   RF 4+  —— <arg><name>path</name><default>None</default></arg>
RF3_SPEC = '''<?xml version="1.0" encoding="UTF-8"?>
<keywordspec name="SeleniumLibrary" type="library">
<version>3.0.0</version>
<keywords>
<kw name="Click Element">
<arguments>
<arg>locator</arg>
</arguments>
</kw>
<kw name="Add Cookie">
<arguments>
<arg>name</arg>
<arg>value</arg>
<arg>path=None</arg>
</arguments>
</kw>
</keywords>
</keywordspec>
'''

RF7_SPEC = '''<?xml version="1.0" encoding="UTF-8"?>
<keywordspec name="Collections" type="LIBRARY" format="MARKDOWN" specversion="7">
<version>7.5.1</version>
<keywords>
<kw name="Append To List">
<arguments repr="list_, *values">
<arg kind="POSITIONAL_OR_NAMED" required="true">
<name>list_</name>
<doc>The list to modify.</doc>
</arg>
<arg kind="VAR_POSITIONAL" required="false">
<name>values</name>
</arg>
</arguments>
</kw>
</keywords>
</keywordspec>
'''


class SpecParseTest(unittest.TestCase):
    """libdoc 的 XML 结构跨版本不一样，这里锁住两种写法。"""

    def _spec(self, text):
        root = Path(tempfile.mkdtemp(prefix='specread-'))
        path = root / 'lib.xml'
        path.write_text(text, encoding='utf-8')
        self.addCleanup(shutil.rmtree, str(root), True)
        return path

    def test_rf3_spec_strips_default_values(self):
        got = toolchain._read_spec(self._spec(RF3_SPEC))
        self.assertEqual(got['library'], 'SeleniumLibrary')
        self.assertEqual(got['version'], '3.0.0')
        by_name = {kw['name']: kw['args'] for kw in got['keywords']}
        self.assertEqual(by_name['Click Element'], ['locator'])
        self.assertEqual(by_name['Add Cookie'], ['name', 'value', 'path'])

    def test_rf7_spec_reads_name_child(self):
        got = toolchain._read_spec(self._spec(RF7_SPEC))
        by_name = {kw['name']: kw['args'] for kw in got['keywords']}
        self.assertEqual(by_name['Append To List'], ['list_', 'values'])

    def test_export_asks_libdoc_for_xml_explicitly(self):
        # 靠后缀猜格式在 RF 3.x 会直接失败，7.x 也不再推断：必须显式 --format XML。
        seen = {}

        def fake_run(cmd, timeout, cwd=None):
            seen['cmd'] = cmd
            Path(cmd[-1]).write_text(RF7_SPEC, encoding='utf-8')
            return mock.Mock(returncode=0, stdout='', stderr='')

        with mock.patch.object(toolchain, '_run', side_effect=fake_run):
            got = toolchain.export_keywords('python')
        self.assertIn('--format', seen['cmd'])
        self.assertEqual(seen['cmd'][seen['cmd'].index('--format') + 1], 'XML')
        self.assertEqual(got['library'], 'Collections')
        self.assertEqual(got['version'], '7.5.1')

    def test_old_robot_format_error_says_version_too_old(self):
        err = "Format must be 'HTML' or 'XML', got 'JSON'.  Try --help for usage information."
        with mock.patch.object(toolchain, '_run',
                               return_value=mock.Mock(returncode=2, stdout='', stderr=err)):
            with self.assertRaises(toolchain.ToolchainError) as ctx:
                toolchain.export_keywords('python')
        self.assertIn('版本太老', str(ctx.exception))

    def test_missing_robot_is_a_readable_error(self):
        err = "No module named robot"
        with mock.patch.object(toolchain, '_run',
                               return_value=mock.Mock(returncode=1, stdout='', stderr=err)):
            with self.assertRaises(toolchain.ToolchainError) as ctx:
                toolchain.export_keywords('python')
        self.assertIn('没有 robotframework', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()