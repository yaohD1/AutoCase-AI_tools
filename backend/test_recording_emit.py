import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.services.recording import emit, toolchain

TARGET = 'tests/login.robot'


def _rf_step(no=1, text='输入用户名'):
    spec = {'kind': 'rf_id', 'method': 'locator', 'args': ['css=[id="user"]'], 'kwargs': {}, 'desc': 'rf id:user'}
    candidate = {'kind': 'rf_id', 'desc': 'rf id:user', 'count': 1, 'spec': spec}
    return {
        'no': no, 'type': 'fill', 'text': text, 'value': 'alice', 'label': None, 'url': None,
        'target': {'alias': '用户名', 'status': 'unique', 'candidates': [candidate], 'primary': candidate,
                   'snap': {'tag': 'input'}},
    }


class FakeRunner(object):
    """按顺序执行每次 emit 调用的动作：动作接收工作区路径，模拟 agent 写文件。"""

    def __init__(self, *actions):
        self.actions = list(actions)
        self.prompts = []

    def emit_recording(self, workspace, model, prompt, timeout=600):
        self.prompts.append(prompt)
        if not self.actions:
            return 'no more actions'
        self.actions.pop(0)(Path(workspace))
        return 'done'


def write(rel, text):
    def action(root):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
    return action


def ROBOT(text='Input Text    id:user    alice\n'):
    return '*** Test Cases ***\nLogin\n    %s' % text


KEYWORDS = [{'name': 'Input Text', 'args': ['locator', 'text', 'clear']}]
PATCH_KW = mock.patch.object(toolchain, 'export_keywords', return_value={'keywords': KEYWORDS})


class EmitTest(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix='emit-ws-'))
        (self.ws / 'README.md').write_text('seed', encoding='utf-8')

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    def _gen(self, runner, dryrun_results, target=TARGET, overwrite=False, steps=None):
        with PATCH_KW, mock.patch.object(toolchain, 'dryrun', side_effect=dryrun_results):
            return emit.generate(runner, self.ws, 'p/m', steps or [_rf_step()], 'robot-selenium',
                                 target, 'Login', python=sys.executable, overwrite=overwrite)

    def test_success_writes_only_the_target_file(self):
        runner = FakeRunner(write(TARGET, ROBOT()))
        result = self._gen(runner, [(True, 'ok')])
        self.assertTrue(result['validated'])
        self.assertEqual(result['changed'], [TARGET])
        self.assertTrue((self.ws / TARGET).is_file())
        self.assertIn('Input Text', runner.prompts[0])

    def test_prompt_carries_locator_keywords_and_target_path(self):
        runner = FakeRunner(write(TARGET, ROBOT()))
        self._gen(runner, [(True, 'ok')])
        # prompt 内是 JSON，引号会被转义成 \"，用 chr(92) 显式拼出反斜杠。
        bs = chr(92)
        self.assertIn('"locator": "css=[id=' + bs + '"user' + bs + '"]"', runner.prompts[0])
        self.assertIn('"target_path": "tests/login.robot"', runner.prompts[0])

    def test_out_of_scope_write_is_reverted_and_reported(self):
        existing = self.ws / 'other.robot'
        existing.write_text('original', encoding='utf-8')
        runner = FakeRunner(lambda root: (write(TARGET, ROBOT())(root), write('other.robot', 'changed')(root)))
        with self.assertRaises(emit.EmitError) as ctx:
            self._gen(runner, [(True, 'ok')])
        self.assertIn('other.robot', ctx.exception.details)
        self.assertFalse((self.ws / TARGET).exists(), 'new target must be removed')
        self.assertEqual(existing.read_text(encoding='utf-8'), 'original')

    def test_validation_failure_is_repaired_once(self):
        runner = FakeRunner(write(TARGET, ROBOT('Input Text Typo    id:user\n')),
                            write(TARGET, ROBOT()))
        result = self._gen(runner, [(False, 'No keyword with name Input Text Typo'), (True, 'ok')])
        self.assertTrue(result['validated'])
        self.assertEqual(len(runner.prompts), 2)
        self.assertIn('Input Text Typo', runner.prompts[1])

    def test_validation_failure_after_repair_restores_original(self):
        existing = self.ws / TARGET
        existing.parent.mkdir(parents=True, exist_ok=True)
        existing.write_text('before', encoding='utf-8')
        runner = FakeRunner(write(TARGET, 'bad one'), write(TARGET, 'still bad'))
        with self.assertRaises(emit.EmitError) as ctx:
            self._gen(runner, [(False, 'err1'), (False, 'err2')], overwrite=True)
        self.assertIn('err2', ctx.exception.details[0])
        self.assertEqual(existing.read_text(encoding='utf-8'), 'before')

    def test_no_target_file_is_an_error(self):
        runner = FakeRunner(lambda root: None)
        with self.assertRaises(emit.EmitError) as ctx:
            self._gen(runner, [])
        self.assertIn('没有写出目标文件', str(ctx.exception))

    def test_existing_target_is_a_conflict_and_agent_is_not_called(self):
        (self.ws / TARGET).parent.mkdir(parents=True, exist_ok=True)
        (self.ws / TARGET).write_text('keep me', encoding='utf-8')
        runner = FakeRunner()
        with self.assertRaises(emit.EmitError) as ctx:
            self._gen(runner, [])
        self.assertTrue(ctx.exception.details[0].startswith('conflict:'))
        self.assertEqual(runner.prompts, [])
        self.assertEqual((self.ws / TARGET).read_text(encoding='utf-8'), 'keep me')

    def test_path_escape_is_rejected_before_any_call(self):
        runner = FakeRunner()
        for bad in ('../evil.robot', '/etc/x.robot', 'C:/x.robot', ''):
            with self.assertRaises(emit.EmitError):
                self._gen(runner, [], target=bad)
        self.assertEqual(runner.prompts, [])

    def test_missing_keyword_export_is_reported_not_skipped(self):
        runner = FakeRunner()
        with mock.patch.object(toolchain, 'export_keywords',
                               side_effect=toolchain.ToolchainError('未安装 SeleniumLibrary')):
            with self.assertRaises(emit.EmitError) as ctx:
                emit.generate(runner, self.ws, 'p/m', [_rf_step()], 'robot-selenium', TARGET, 'Login',
                              python=sys.executable)
        self.assertIn('未安装 SeleniumLibrary', str(ctx.exception))
        self.assertEqual(runner.prompts, [])

    def test_ambiguous_step_is_degraded_not_blocked(self):
        """定位不唯一不再整体拒绝：降级成「最窄候选 + 录制序号」，并在提示词里给出 nth。"""
        bad = _rf_step()
        bad['no'] = 7
        dup = {'kind': 'rf_css', 'desc': 'rf css:a', 'count': 2, 'index': 1,
               'spec': {'kind': 'rf_css', 'method': 'locator', 'args': ['css=a'], 'kwargs': {}, 'desc': 'x'}}
        bad['target']['candidates'] = [dup]
        bad['target']['primary'] = None
        runner = FakeRunner(write(TARGET, ROBOT()))
        result = self._gen(runner, [(True, 'ok')], steps=[_rf_step(), bad])
        self.assertTrue(result['validated'])
        self.assertEqual(len(result['degraded']), 1)
        self.assertEqual(result['degraded'][0]['no'], 7)
        self.assertEqual(result['degraded'][0]['matches'], 2)
        self.assertEqual(result['degraded'][0]['nth'], 1)
        self.assertIn('"nth": 1', runner.prompts[0])
        self.assertIn('"match_count": 2', runner.prompts[0])
        self.assertTrue((self.ws / TARGET).is_file())

    def test_target_that_matches_nothing_still_blocks(self):
        """连一条候选都命不中（元素已不在页面上）才拒绝——那种情况写不出任何定位。"""
        gone = _rf_step()
        gone['no'] = 7
        gone['target']['candidates'] = []
        gone['target']['primary'] = None
        runner = FakeRunner()
        with self.assertRaises(emit.EmitError) as ctx:
            self._gen(runner, [], steps=[_rf_step(), gone])
        self.assertIn('第 7 步', str(ctx.exception))
        self.assertEqual(runner.prompts, [])
        self.assertFalse((self.ws / TARGET).exists())

    def test_empty_steps_rejected(self):
        with self.assertRaises(emit.EmitError):
            emit.generate(FakeRunner(), self.ws, 'p/m', [], 'robot-selenium', TARGET, 'Login')

    def test_prompt_carries_framework_waits_and_frame_context(self):
        """两套框架的提示词必须是「各自的写法」：RF 要有显式等待，PW 要有 auto-wait 语义。"""
        step = _rf_step()
        step['page_index'] = 1
        step['target']['snap'] = {'tag': 'input', 'frame_url': 'http://a/frame.html', 'frame_name': 'inner'}
        rf_steps = emit._steps_for_prompt([step], 'robot-selenium')
        self.assertEqual(rf_steps[0]['page_index'], 1)
        self.assertEqual(rf_steps[0]['target']['frame'],
                         {'url': 'http://a/frame.html', 'name': 'inner'})

        rf_prompt = emit._prompt({'framework': 'robot-selenium', 'steps': rf_steps})
        self.assertIn('Wait Until Element Is Visible', rf_prompt)
        self.assertIn('Select Frame', rf_prompt)
        self.assertNotIn('自带 auto-wait', rf_prompt)

        pw_prompt = emit._prompt({'framework': 'playwright-ts', 'steps': []})
        self.assertIn('frameLocator', pw_prompt)
        self.assertNotIn('Wait Until Element Is Visible', pw_prompt)
        # 主框架的步骤不该带 frame 字段，否则模型会多写一层切框架。
        plain = emit._steps_for_prompt([_rf_step()], 'robot-selenium')[0]
        self.assertNotIn('frame', plain['target'])
        self.assertNotIn('page_index', plain)


if __name__ == '__main__':
    unittest.main()
