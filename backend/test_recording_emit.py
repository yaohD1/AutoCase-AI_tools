import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.services.recording import emit, ir, toolchain

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
    """按顺序执行每次 emit 调用的动作：动作接收工作区路径，模拟 agent 写文件。

    规划（emit_planning）单独记一份 prompt：它和写手的提示词是两份东西，
    混在一起断言分不清是「谁看到了什么」。
    """

    def __init__(self, *actions, plan=None, plan_error=None):
        self.actions = list(actions)
        self.prompts = []
        self.writables = []
        self.plan_text = plan
        self.plan_error = plan_error
        self.plan_prompts = []

    def emit_planning(self, workspace, model, prompt, timeout=240):
        self.plan_prompts.append(prompt)
        if self.plan_error:
            raise RuntimeError(self.plan_error)
        return self.plan_text if self.plan_text is not None else '{}'

    def emit_recording(self, workspace, model, prompt, timeout=600, writable=None, **kwargs):
        self.prompts.append(prompt)
        self.writables.append(writable)
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


PLAN = {
    'project_style': {'runner': 'custom TestModule', 'assertion': 'throw new Error'},
    'reusable': [{'file': 'src/pages/LoginPage.ts', 'symbol': 'LoginPage.login',
                  'import': "import { LoginPage } from '../../src/pages/LoginPage';",
                  'usage': 'await new LoginPage(page).login(u, p);'}],
    'steps': [{'no': 1, 'action': 'reuse', 'target': 'LoginPage.login',
               'note': '直接调 LoginPage.login'}],
    'target_path': TARGET,
}


class EmitTest(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix='emit-ws-'))
        (self.ws / 'README.md').write_text('seed', encoding='utf-8')

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    def _gen(self, runner, dryrun_results, target=TARGET, overwrite=False, steps=None, plan=None,
             plan_feedback=None):
        with PATCH_KW, mock.patch.object(toolchain, 'dryrun', side_effect=dryrun_results):
            return emit.generate(runner, self.ws, 'p/m', steps or [_rf_step()], 'robot-selenium',
                                 target, 'Login', python=sys.executable, overwrite=overwrite,
                                 plan=plan, plan_feedback=plan_feedback)

    def test_success_writes_only_the_target_file(self):
        runner = FakeRunner(write(TARGET, ROBOT()))
        result = self._gen(runner, [(True, 'ok')])
        self.assertTrue(result['validated'])
        self.assertEqual(result['changed'], [TARGET])
        self.assertEqual(result['outside'], [])
        # 写权限必须按目标文件下发，越界靠 opencode 在工具调用前拒绝。
        self.assertEqual(runner.writables, [TARGET])
        self.assertTrue((self.ws / TARGET).is_file())
        self.assertIn('Input Text', runner.prompts[0])

    def test_prompt_carries_locator_keywords_and_target_path(self):
        runner = FakeRunner(write(TARGET, ROBOT()))
        self._gen(runner, [(True, 'ok')])
        # prompt 内是 JSON，引号会被转义成 \"，用 chr(92) 显式拼出反斜杠。
        bs = chr(92)
        self.assertIn('"locator": "css=[id=' + bs + '"user' + bs + '"]"', runner.prompts[0])
        self.assertIn('"target_path": "tests/login.robot"', runner.prompts[0])

    def test_generate_uses_the_confirmed_plan_and_does_not_plan_again(self):
        """规划已经独立成一步：写入只认传进来的任务书，不许自己再烧一次模型调用。"""
        runner = FakeRunner(write(TARGET, ROBOT()))
        result = self._gen(runner, [(True, 'ok')], plan=PLAN)

        self.assertEqual(runner.plan_prompts, [], '写入阶段不该再跑规划')
        self.assertIn('LoginPage.login', runner.prompts[0], '复用项要进写手的提示词')
        self.assertIn('custom TestModule', runner.prompts[0], '项目风格要进写手的提示词')
        self.assertEqual(result['plan']['steps'][0]['action'], 'reuse')
        # 写权限只在写入阶段申请一次。
        self.assertEqual(runner.writables, [TARGET])

    def test_user_feedback_on_the_plan_reaches_the_writer(self):
        """用户在任务书上选中片段写的意见，必须原样进写手提示词，否则界面上的批注只是装饰。"""
        runner = FakeRunner(write(TARGET, ROBOT()))
        self._gen(runner, [(True, 'ok')], plan=PLAN, plan_feedback=[
            {'quote': 'page.getByText("登 录")', 'comment': '用 getByRole("button")，文本里有空格'},
            {'quote': '', 'comment': '账号密码走环境变量'},
            {'quote': '空的', 'comment': '   '},
        ])
        prompt = runner.prompts[0]
        self.assertIn('用 getByRole', prompt)
        self.assertIn('账号密码走环境变量', prompt)
        self.assertNotIn('空的', prompt, '空意见不该占提示词')

    def test_plan_forces_target_path_and_keeps_the_model_suggestion(self):
        """模型改过的路径只能当建议：写入路径、权限与验收都只认请求里的 target_path。"""
        changed = json.dumps({
            'steps': [{'no': 1, 'action': 'write', 'note': 'x'}],
            'target_path': 'tests/ai-chat/login.robot',
            'target_path_reason': 'playwright.config.ts 的 testDir 是 ./tests',
        }, ensure_ascii=False)
        runner = FakeRunner(plan=changed)
        result = emit.plan(runner, self.ws, 'p/m', [_rf_step()], 'robot-selenium',
                           TARGET, 'Login', python=sys.executable)

        self.assertEqual(result['plan']['target_path'], TARGET)
        self.assertEqual(result['suggestion'], 'tests/ai-chat/login.robot')
        self.assertIn('testDir', result['reason'])
        self.assertEqual(result['steps'], 1)
        self.assertEqual(runner.prompts, [], '规划不该写文件')

    def test_plan_failure_is_reported_not_swallowed(self):
        """规划已经是独立一步：失败要明确报错，不能悄悄降级成「没有任务书」。"""
        runner = FakeRunner(plan_error='模型超时')
        with self.assertRaises(emit.EmitError) as ctx:
            emit.plan(runner, self.ws, 'p/m', [_rf_step()], 'robot-selenium', TARGET, 'Login')
        self.assertIn('模型超时', str(ctx.exception))

    def test_plan_must_cover_every_recorded_step(self):
        runner = FakeRunner(plan=json.dumps({'steps': [{'no': 2, 'action': 'write'}]}))
        with self.assertRaises(emit.EmitError) as ctx:
            emit.plan(runner, self.ws, 'p/m', [_rf_step()], 'robot-selenium', TARGET, 'Login')
        self.assertIn('对不上', str(ctx.exception))
        self.assertTrue(emit.plan_matches_steps({'steps': [{'no': '1'}]}, [_rf_step()]))
        # 步骤号一样、内容换了（删了再补一个新第 1 步）也要判成过期。
        self.assertFalse(emit.plan_matches_steps(
            {'steps': [{'no': 1}], 'steps_sig': '1:click:别的元素'}, [_rf_step()]))

    def test_plan_prompt_forbids_rewriting_target_path(self):
        text = emit._plan_prompt(emit._plan_payload('playwright-ts', [_rf_step()], TARGET, 'Login',
                                                   feedback=[{'quote': 'q', 'comment': 'c'}]))
        self.assertIn('必须原样返回', text)
        self.assertIn('feedback', text)
        self.assertIn('"comment": "c"', text)

    def test_writer_prompt_forbids_using_the_plan_path(self):
        prompt = emit._prompt({'framework': 'robot-selenium', 'target_path': TARGET,
                               'steps': [], 'plan': PLAN})
        self.assertIn('唯一允许写入的路径', prompt)
        self.assertIn('plan.target_path', prompt)

    def test_plan_json_survives_code_fence_and_chatter(self):
        """模型常把 JSON 包进 ```json 或前面加一句废话，都要能抠出来。"""
        plan, error = emit._extract_plan('好的，任务书如下：\n```json\n{"steps": [{"no": 1}]}\n```\n')
        self.assertIsNone(error)
        self.assertEqual(plan['steps'], [{'no': 1}])

        plan, error = emit._extract_plan('{"project_style": {"runner": "x"}}')
        self.assertIsNone(error)
        self.assertEqual(plan['steps'], [], '缺 steps 要补成空数组，否则后面按步对账会炸')

        plan, error = emit._extract_plan('我不知道')
        self.assertIsNone(plan)
        self.assertIn('没有 JSON', error)

    def test_plan_prompt_does_not_carry_locator_candidates(self):
        """规划只判断「项目里有没有现成封装」，喂候选定位串纯属浪费 token。"""
        payload = emit._plan_payload('robot-selenium', [_rf_step()], TARGET, 'Login')
        text = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn('candidates', text)
        self.assertNotIn('css=[id=', text)
        self.assertIn('"no": 1', text)
        self.assertIn('"tag": "input"', text)

    def test_out_of_scope_write_is_reported_but_not_reverted(self):
        existing = self.ws / 'other.robot'
        existing.write_text('original', encoding='utf-8')
        runner = FakeRunner(lambda root: (write(TARGET, ROBOT())(root), write('other.robot', 'changed')(root)))
        result = self._gen(runner, [(True, 'ok')])
        # 越界只报告不回滚：文件保留 agent 写的内容，由用户决定是否 git checkout。
        self.assertEqual(existing.read_text(encoding='utf-8'), 'changed')
        self.assertEqual(result['outside'], ['other.robot'])
        self.assertIn(TARGET, result['changed'])
        self.assertTrue((self.ws / TARGET).is_file(), 'target must be kept on success')

    def test_validation_failure_is_repaired_once(self):
        runner = FakeRunner(write(TARGET, ROBOT('Input Text Typo    id:user\n')),
                            write(TARGET, ROBOT()))
        result = self._gen(runner, [(False, 'No keyword with name Input Text Typo'), (True, 'ok')])
        self.assertTrue(result['validated'])
        self.assertEqual(len(runner.prompts), 2)
        self.assertIn('Input Text Typo', runner.prompts[1])

    def test_validation_failure_after_repair_keeps_overwritten_target(self):
        existing = self.ws / TARGET
        existing.parent.mkdir(parents=True, exist_ok=True)
        existing.write_text('before', encoding='utf-8')
        runner = FakeRunner(write(TARGET, 'bad one'), write(TARGET, 'still bad'))
        with self.assertRaises(emit.EmitError) as ctx:
            self._gen(runner, [(False, 'err1'), (False, 'err2')], overwrite=True)
        self.assertIn('err2', ctx.exception.details[0])
        # 原本存在的目标文件不再自动回滚，只把恢复办法写在错误里。
        self.assertEqual(existing.read_text(encoding='utf-8'), 'still bad')
        self.assertTrue(any('git checkout' in str(d) for d in ctx.exception.details))

    def test_validation_failure_keeps_a_newly_written_file(self):
        """原来「失败就删」，结果一次无关的校验错误会把已经写对的内容一起抹掉。改成保留。"""
        runner = FakeRunner(write(TARGET, ROBOT()), write(TARGET, ROBOT()))
        with self.assertRaises(emit.EmitError) as ctx:
            self._gen(runner, [(False, 'err1'), (False, 'err2')])
        self.assertTrue((self.ws / TARGET).is_file(), '校验没过不等于内容没用，不该替用户删')
        self.assertTrue(any('已保留' in str(d) for d in ctx.exception.details))

    def test_no_target_file_is_an_error(self):
        runner = FakeRunner(lambda root: None)
        with self.assertRaises(emit.EmitError) as ctx:
            self._gen(runner, [])
        self.assertIn('没有写出目标文件', str(ctx.exception))

    def test_missing_target_reports_where_the_agent_actually_wrote(self):
        """模型把文件写到别的目录时，错误里要直接说出写到了哪，而不是只报「没写出」。"""
        runner = FakeRunner(write('tests/ai-chat/login.robot', ROBOT()))
        with self.assertRaises(emit.EmitError) as ctx:
            self._gen(runner, [])
        details = ' '.join(str(d) for d in ctx.exception.details)
        self.assertIn('tests/ai-chat/login.robot', details)
        self.assertIn('目标路径是', details)

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

    def test_prompt_maps_page_text_assertion_for_both_frameworks(self):
        """整页文字断言没有目标元素，提示词必须给出各自的写法，否则模型会自己编一个 locator。"""
        step = ir.make_step('assert', no=1)
        step['intent'] = {'type': 'page_text_contains', 'target': None, 'value': '登录成功', 'seconds': None}
        for framework, keyword in (('robot-selenium', 'Wait Until Page Contains'),
                                   ('playwright-ts', 'expect(page.locator("body")).toContainText')):
            prompt = emit._prompt({'framework': framework,
                                   'steps': emit._steps_for_prompt([step], framework)})
            self.assertIn(keyword, prompt, framework)
            self.assertIn('page_text_contains', prompt, framework)


    def test_prompt_maps_reload_for_both_frameworks(self):
        """刷新在脚本里不等于「打开同一个地址」，提示词必须给出各自的关键字，
        否则模型会写成 Go To / page.goto(page.url())，SPA 里可能根本不重载。"""
        step = ir.make_step('reload', no=1)
        rf = emit._prompt({'framework': 'robot-selenium',
                           'steps': emit._steps_for_prompt([step], 'robot-selenium')})
        self.assertIn('Reload Page', rf)
        pw = emit._prompt({'framework': 'playwright-ts',
                           'steps': emit._steps_for_prompt([step], 'playwright-ts')})
        self.assertIn('page.reload()', pw)
        # reload 没有目标元素也没有地址，别让模型去编一个
        self.assertNotIn('target', emit._steps_for_prompt([step], 'playwright-ts')[0])
        self.assertNotIn('url', emit._steps_for_prompt([step], 'playwright-ts')[0])


if __name__ == '__main__':
    unittest.main()
