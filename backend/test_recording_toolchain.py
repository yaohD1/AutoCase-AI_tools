import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

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


class ToolchainTest(unittest.TestCase):
    def test_tsc_missing_is_an_error_not_a_skip(self):
        ws = _workspace({'package.json': '{}'})
        try:
            with self.assertRaises(toolchain.ToolchainError):
                toolchain.tsc_check(ws)
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


if __name__ == '__main__':
    unittest.main()
