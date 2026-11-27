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

    def __init__(self, *actions, plan=None, plan_error=None, plan_partial=None):
        self.actions = list(actions)
        self.prompts = []
        self.writables = []
        self.plan_text = plan
        self.plan_error = plan_error
        self.plan_partial = plan_partial
        self.plan_prompts = []

    def emit_planning(self, workspace, model, prompt, timeout=420):
        self.plan_prompts.append(prompt)
        if self.plan_error:
            error = RuntimeError(self.plan_error)
            if self.plan_partial is not None:
                error.partial = self.plan_partial
            raise error
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
             plan_feedback=None, actions=None):
        with PATCH_KW, mock.patch.object(toolchain, 'dryrun', side_effect=dryrun_results):
            return emit.generate(runner, self.ws, 'p/m', steps or [_rf_step()], 'robot-selenium',
                                 target, 'Login', python=sys.executable, overwrite=overwrite,
                                 plan=plan, plan_feedback=plan_feedback, actions=actions)

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

    def test_prompt_carries_locator_var_instead_of_raw_locator(self):
        """定位抽到资源文件后，提示词只给变量名。

        两样同时给的话写手一定挑省事的那条（它对长 xpath 有很强的改写冲动），
        资源文件就白建了。
        """
        runner = FakeRunner(write(TARGET, ROBOT()))
        self._gen(runner, [(True, 'ok')])
        prompt = runner.prompts[0]
        self.assertIn('"locator_var": "${tests用户名输入框}"', prompt)
        self.assertIn('"target_path": "tests/login.robot"', prompt)
        self.assertNotIn('css=[id=', prompt, '裸定位不该再进提示词')
        # 定位必须落在资源文件里，否则变量是空的
        body = (self.ws / '.autocase/locators/login页面元素.resource').read_text(encoding='utf-8')
        self.assertIn("css=[id='user']", body.replace('"', "'"))

    def test_script_gets_resource_line_and_reports_the_file(self):
        """资源文件路径要从用例目录往后退着写，并出现在「平台另外写入」里。

        路径写绝对路径换台机器就断；不报出来用户只会看到 .robot，目录里却多出
        .autocase/，账对不上。
        """
        runner = FakeRunner(write(TARGET, ROBOT()))
        result = self._gen(runner, [(True, 'ok')])
        text = (self.ws / TARGET).read_text(encoding='utf-8')
        self.assertIn(
            'Resource    ${CURDIR}/../.autocase/locators/login页面元素.resource', text)
        paths = [item['path'] for item in result['platform_files']]
        self.assertIn('.autocase/locators/login页面元素.resource', paths)
        self.assertEqual(result['locator']['variables'], 1)
        self.assertEqual(result['locator']['bare'], [], '正文不该留裸定位')

    def test_action_locator_gets_its_own_resource_import(self):
        """动作（登录这类）的定位在公共文件里，必须一起导入。

        只导用例那份时，Keyword 里的 ${登录用户名输入框} 在脚本里找不到定义，
        而且不报编译错——跑到那一步才 Variable not found，看着像模型写错了变量名。
        """
        action = {'id': 'act-1', 'name': '登录', 'params': [], 'steps': [_rf_step(no=1)]}
        steps = [{'no': 1, 'type': 'call', 'action': 'act-1', 'label': '登录'}]
        runner = FakeRunner(write(TARGET, ROBOT('    Input Text    ${登录用户名输入框}    alice\n')))
        self._gen(runner, [(True, 'ok')], steps=steps, actions={'act-1': action})
        text = (self.ws / TARGET).read_text(encoding='utf-8')
        self.assertIn(
            'Resource    ${CURDIR}/../.autocase/locators/公共/公共页面元素.resource', text)
        # 纯 call 用例没有可抽的步骤，用例那份文件不该凭空出现在头部
        self.assertNotIn('login页面元素.resource', text)

    def test_missing_resource_import_is_filled_in(self):
        """正文引用了资源文件里的变量，头部却没导入它 → 写盘前补上。

        这一步是兜底：清单由生成侧算，但模型可能引用了别处的变量。
        缺一行 Resource 只会在运行时炸，不如在这里补齐。
        """
        common = self.ws / '.autocase/locators/公共/公共页面元素.resource'
        common.parent.mkdir(parents=True, exist_ok=True)
        common.write_text(
            '# 由平台生成\n*** Variables ***\n${公共按钮}    xpath=//ok\n', encoding='utf-8')
        target = self.ws / TARGET
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            '*** Settings ***\n\n*** Test Cases ***\nLogin\n    Click Element    ${公共按钮}\n',
            encoding='utf-8')
        emit._normalize_rf_script(self.ws, TARGET, [], locator_libs=[])
        text = target.read_text(encoding='utf-8')
        self.assertIn(
            'Resource    ${CURDIR}/../.autocase/locators/公共/公共页面元素.resource', text)

    def test_generate_uses_the_confirmed_plan_and_does_not_plan_again(self):
        """规划已经独立成一步：写入只认传进来的任务书，不许自己再烧一次模型调用。"""
        runner = FakeRunner(write(TARGET, ROBOT()))
        result = self._gen(runner, [(True, 'ok')], plan=PLAN)

        self.assertEqual(runner.plan_prompts, [], '写入阶段不该再跑规划')
        self.assertIn('LoginPage.login', runner.prompts[0], '任务书里的步骤说明要进写手提示词')
        # project_style 不再下发：它的 auth_setup 写着「Suite Setup 登录」，而录制步骤里
        # 本来就有登录，下发它就是引导双重登录（实测踩过）；reusable / reference_files
        # 会勾着写手去调项目封装，把录制的定位白扔。
        self.assertNotIn('project_style', runner.prompts[0])
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

    def test_plan_without_a_configured_path_adopts_the_model_choice(self):
        """配置留空 = 位置交给规划。这时它写的 target_path 就是正式路径，不是建议。"""
        chosen = json.dumps({
            'steps': [{'no': 1, 'action': 'write', 'note': 'x'}],
            'target_path': 'tests/login.robot',
            'target_path_reason': 'robot 文件都放在 tests/ 下',
        }, ensure_ascii=False)
        runner = FakeRunner(plan=chosen)
        result = emit.plan(runner, self.ws, 'p/m', [_rf_step()], 'robot-selenium',
                           '', 'Login', python=sys.executable)

        self.assertEqual(result['plan']['target_path'], 'tests/login.robot')
        self.assertEqual(result['target_path'], 'tests/login.robot')
        self.assertTrue(result['chosen'], '需要告诉界面「这是 AI 选的，等用户确认」')
        self.assertIsNone(result['suggestion'], '已经采纳成正式路径，不该再挂一条建议')
        self.assertIn('tests/', result['reason'])

    def test_plan_without_a_path_rejects_a_dangerous_model_choice(self):
        """「自己定位置」不是放权：模型提名的路径同样要过校验。"""
        bad = json.dumps({'steps': [{'no': 1, 'action': 'write'}],
                          'target_path': 'node_modules/x.robot'})
        runner = FakeRunner(plan=bad)
        with self.assertRaises(emit.EmitError) as ctx:
            emit.plan(runner, self.ws, 'p/m', [_rf_step()], 'robot-selenium', '', 'Login')
        self.assertIn('node_modules', str(ctx.exception))

    def test_target_problem_rejects_paths_that_cannot_be_the_target(self):
        cases = [
            ('playwright-ts', 'tests/a.robot', '扩展名'),
            ('playwright-ts', 'playwright.config.ts', '配置文件'),
            ('playwright-ts', 'node_modules/a.spec.ts', 'node_modules'),
            ('playwright-ts', '../a.spec.ts', '..'),
            ('playwright-ts', '', '没有写入路径'),
        ]
        for framework, path, expect in cases:
            with self.subTest(path=path):
                problem = emit.target_problem(self.ws, path, framework)
                self.assertIsNotNone(problem)
                self.assertIn(expect, problem)
        self.assertIsNone(emit.target_problem(self.ws, 'tests/a.spec.ts', 'playwright-ts'))

    def test_plan_payload_carries_project_facts(self):
        """测试目录和最近用例要提前给模型，省掉它为了定路径多跑几轮 glob。"""
        (self.ws / 'playwright.config.ts').write_text(
            "export default { testDir: './tests' };", encoding='utf-8')
        (self.ws / 'tests').mkdir()
        (self.ws / 'tests' / 'a.spec.ts').write_text('test("a", () => {});', encoding='utf-8')
        # AutoCase 自己跑出来的工作区副本不能被当成「项目写法」推荐给模型。
        copy = self.ws / 'autocase' / 'runs' / '20260101-000000' / 'tests' / 'b.spec.ts'
        copy.parent.mkdir(parents=True)
        copy.write_text('test("b", () => {});', encoding='utf-8')

        payload = emit._plan_payload('playwright-ts', [_rf_step()], '', 'Login',
                                     workspace=str(self.ws))
        self.assertEqual(payload['target_path'], '')
        self.assertEqual(payload['project_facts']['test_dir'], './tests')
        recent = payload['project_facts']['recent_tests']
        self.assertIn('tests/a.spec.ts', recent)
        self.assertNotIn('autocase/runs/20260101-000000/tests/b.spec.ts', recent)
        text = emit._plan_prompt(payload)
        self.assertIn('project_facts', text)

    def test_plan_failure_is_reported_not_swallowed(self):
        """规划已经是独立一步：失败要明确报错，不能悄悄降级成「没有任务书」。"""
        runner = FakeRunner(plan_error='模型超时')
        with self.assertRaises(emit.EmitError) as ctx:
            emit.plan(runner, self.ws, 'p/m', [_rf_step()], 'robot-selenium', TARGET, 'Login')
        self.assertIn('模型超时', str(ctx.exception))

    def test_aborted_run_keeps_the_task_sheet_it_already_wrote(self):
        """超时前正文里已经写完整份任务书时，别丢掉——实测就差了最后一次输出。"""
        finished = json.dumps({
            'steps': [{'no': 1, 'action': 'write', 'note': '填入用户名'}],
            'target_path': TARGET,
        }, ensure_ascii=False)
        runner = FakeRunner(
            plan_error='录制生成超过 420 秒仍未结束',
            plan_partial={'text': finished, 'rounds': 7, 'reasoning_chars': 11444},
        )
        result = emit.plan(runner, self.ws, 'p/m', [_rf_step()], 'robot-selenium', TARGET, 'Login')

        self.assertEqual(result['steps'], 1)
        self.assertEqual(result['plan']['steps'][0]['note'], '填入用户名')

    def test_aborted_run_reports_how_far_it_got(self):
        """抢救不到 JSON 时，至少要说清它跑了几轮、推理写了多少，而不是只报超时。"""
        runner = FakeRunner(
            plan_error='录制生成超过 420 秒仍未结束',
            plan_partial={'text': '让我再看看这个项目……', 'rounds': 7, 'reasoning_chars': 11444},
        )
        with self.assertRaises(emit.EmitError) as ctx:
            emit.plan(runner, self.ws, 'p/m', [_rf_step()], 'robot-selenium', TARGET, 'Login')
        message = str(ctx.exception)
        self.assertIn('420 秒', message)
        self.assertIn('已跑 7 轮', message)
        self.assertIn('推理 11444 字符', message)

    def test_project_facts_keep_our_own_output_apart_from_project_tests(self):
        """AutoCase 生成的旧版本不能混进「项目惯例」，否则规划会在覆盖/新建之间反复权衡。"""
        (self.ws / 'tests').mkdir()
        (self.ws / 'tests' / 'project.spec.ts').write_text(
            'import { test } from "@playwright/test";\n', encoding='utf-8')
        (self.ws / 'tests' / 'old-run.spec.ts').write_text(
            '// @autocase 录制自 https://example.com —— 上次生成的\n'
            'import { test } from "@playwright/test";\n', encoding='utf-8')

        facts = emit._project_facts(str(self.ws), 'playwright-ts')
        self.assertIn('tests/project.spec.ts', facts['recent_tests'])
        self.assertNotIn('tests/old-run.spec.ts', facts['recent_tests'])
        self.assertIn('tests/old-run.spec.ts', facts['previous_outputs'])

    def test_planner_prompt_budgets_exploration(self):
        """规划只负责路径、风格、每步说明；读项目不设硬上限，但要收敛。"""
        text = emit._plan_prompt(emit._plan_payload('playwright-ts', [_rf_step()], TARGET, 'Login'))
        self.assertNotIn('最多读 4 个文件', text)
        self.assertIn('不设硬上限', text)
        self.assertIn('15 分钟', text)
        self.assertIn('previous_outputs', text)
        self.assertIn('不要写定位表达式', text)

    def test_plan_must_cover_every_recorded_step(self):
        runner = FakeRunner(plan=json.dumps({'steps': [{'no': 2, 'action': 'write'}]}))
        with self.assertRaises(emit.EmitError) as ctx:
            emit.plan(runner, self.ws, 'p/m', [_rf_step()], 'robot-selenium', TARGET, 'Login')
        self.assertIn('对不上', str(ctx.exception))
        self.assertTrue(emit.plan_matches_steps({'steps': [{'no': '1'}]}, [_rf_step()]))
        # 步骤号一样、内容换了（删了再补一个新第 1 步）也要判成过期。
        self.assertFalse(emit.plan_matches_steps(
            {'steps': [{'no': 1}], 'steps_sig': '1:click:别的元素'}, [_rf_step()]))

    def test_plan_prompt_tells_the_planner_when_it_may_pick_the_path(self):
        text = emit._plan_prompt(emit._plan_payload('playwright-ts', [_rf_step()], TARGET, 'Login',
                                                   feedback=[{'quote': 'q', 'comment': 'c'}]))
        self.assertIn('必须原样返回', text)
        self.assertIn('feedback', text)
        self.assertIn('"comment": "c"', text)
        # 配置留空时必须明确说「你来选」，否则模型还是会绕着空路径纠结。
        blank = emit._plan_prompt(emit._plan_payload('playwright-ts', [_rf_step()], '', 'Login'))
        self.assertIn('为空表示由你按项目约定选一个', blank)

    def test_writer_prompt_names_only_the_payload_target_path(self):
        """任务书里的路径不再下发：它和 payload.target_path 冲突，只会让写手写错地方。

        原来靠一句「不要拿 plan.target_path 当主文件」兜着，那句话本身就是这个冲突的代价。
        """
        prompt = emit._prompt({'framework': 'robot-selenium', 'target_path': TARGET,
                               'steps': [], 'plan': emit._plan_for_prompt(PLAN)})
        self.assertIn('只写 target_path 这一个文件', prompt)
        self.assertNotIn('plan.target_path', prompt)
        self.assertNotIn('project_style', prompt)

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
        # css 选择器带不了序号，nth 仍要告诉写手；定位本身已经是变量。
        self.assertIn('"nth": 1', runner.prompts[0])
        self.assertIn('${tests用户名输入框_2}', runner.prompts[0])
        self.assertIn('"match_count": 2', runner.prompts[0])
        self.assertTrue((self.ws / TARGET).is_file())

    def test_rf_row_scoped_locator_reaches_the_resource_file(self):
        """行锚定的 RF 定位是一条自足的 xpath，原样进资源文件，不该再被 nth 改写。"""
        step = _rf_step(no=9, text='删除 WO-2 这一行')
        locator = ("xpath=//tr[contains(normalize-space(.), 'WO-2')]"
                   "//button[normalize-space(.)='删除']")
        row = {'kind': 'rf_row_scoped', 'desc': 'rf row:' + locator, 'count': 1,
               'relation': 'self',
               'spec': {'kind': 'rf_row_scoped', 'method': 'locator',
                        'args': [locator], 'kwargs': {}, 'desc': 'rf row:' + locator}}
        step['target']['candidates'] = [row]
        step['target']['primary'] = row
        runner = FakeRunner(write(TARGET, ROBOT()))
        self._gen(runner, [(True, 'ok')], steps=[_rf_step(), step])
        body = (self.ws / '.autocase/locators/login页面元素.resource').read_text(encoding='utf-8')
        self.assertIn(locator, body)
        # 唯一命中时不再下发 nth（纯冗余），定位也没有被拼上序号
        self.assertNotIn('"nth":', runner.prompts[0])
        self.assertNotIn(locator + '[', body)

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


class NormalizeHeaderTest(unittest.TestCase):
    """头部由平台重写：证书、等待、收尾不能指望模型。

    录制侧用 ignore_https_errors 把证书警告页遮掉了，IR 里没有「继续前往」可录；
    生成的 Selenium 脚本裸跑会停在 NET::ERR_CERT_AUTHORITY_INVALID，而 Go To
    自己不报错，看起来像成功。所以头部必须由平台固定写。
    """

    TARGET = 'tests/norm.robot'
    STEPS = [{'no': 1, 'type': 'goto', 'url': 'https://x/#/'}]

    def _run(self, content):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(root), True)
        path = root / self.TARGET
        path.parent.mkdir(parents=True)
        path.write_text(content, encoding='utf-8')
        emit._normalize_rf_script(str(root), self.TARGET, self.STEPS,
                                  slider_lib='../.autocase/libraries/SliderLibrary.py')
        return path.read_text(encoding='utf-8')

    def test_rewrites_settings_and_keeps_one_boot(self):
        text = self._run(
            '*** Settings ***\nLibrary    SeleniumLibrary\nSuite Teardown    关闭浏览器\n\n'
            '*** Test Cases ***\n用例\n'
            "    ${options}=    Evaluate    sys.modules['selenium.webdriver'].ChromeOptions()    sys, selenium.webdriver\n"
            '    Call Method    ${options}    add_argument    --ignore-certificate-errors\n'
            '    Create Webdriver    Chrome    chrome_options=${options}\n'
            '    Go To    https://x/#/\n'
            '    Click Element    xpath=//a\n\n'
            '*** Keywords ***\n登录\n    Input Text    xpath=//i    1\n')
        self.assertIn('Suite Teardown    Close Browser', text)
        self.assertNotIn('关闭浏览器', text)
        self.assertEqual(text.count('Create Webdriver'), 1)
        self.assertEqual(text.count('--ignore-certificate-errors'), 1)
        self.assertIn('Set Selenium Implicit Wait', text)
        self.assertIn('Library    ${CURDIR}/../.autocase/libraries/SliderLibrary.py', text)
        self.assertIn('*** Keywords ***', text)

    def test_adds_goto_when_only_test_cases(self):
        text = self._run('*** Test Cases ***\n用例\n    Click Element    xpath=//a\n')
        self.assertIn('Go To    https://x/#/', text)
        self.assertIn('--ignore-certificate-errors', text)

    def test_drops_project_wrapper(self):
        text = self._run('*** Test Cases ***\n用例\n    打开谷歌浏览器并访问登录页面\n'
                         '    Click Element    xpath=//a\n')
        self.assertNotIn('打开谷歌浏览器并访问登录页面', text)
        self.assertIn('Go To    https://x/#/', text)

    def test_idempotent(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(root), True)
        path = root / self.TARGET
        path.parent.mkdir(parents=True)
        path.write_text('*** Test Cases ***\n用例\n    Go To    https://x/#/\n', encoding='utf-8')
        emit._normalize_rf_script(str(root), self.TARGET, self.STEPS)
        first = path.read_text(encoding='utf-8')
        emit._normalize_rf_script(str(root), self.TARGET, self.STEPS)
        self.assertEqual(path.read_text(encoding='utf-8'), first)


class NormalizePwScriptTest(unittest.TestCase):
    """Playwright 侧同一件事：自签名证书要靠 ignoreHTTPSErrors 放行。

    实测：不加这一项，page.goto 到自签名 https 直接抛
    net::ERR_CERT_AUTHORITY_INVALID；加上就正常进页面。
    """

    TARGET = 'autocase/a.spec.ts'

    def _run(self, content):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(root), True)
        path = root / self.TARGET
        path.parent.mkdir(parents=True)
        path.write_text(content, encoding='utf-8')
        changed = emit._normalize_pw_script(str(root), self.TARGET)
        return changed, path.read_text(encoding='utf-8')

    def test_inserts_test_use_after_import(self):
        changed, text = self._run(
            "import { test, expect } from '@playwright/test'\n\n"
            "test('t', async ({ page }) => { await page.goto('https://x/') })\n")
        self.assertTrue(changed)
        self.assertIn("test.use({ ignoreHTTPSErrors: true });", text)
        self.assertLess(text.index('ignoreHTTPSErrors'), text.index("test('t'"))

    def test_multiline_import(self):
        changed, text = self._run(
            "import {\n  test,\n  expect,\n} from '@playwright/test'\n\n"
            "test('t', async ({ page }) => {})\n")
        self.assertTrue(changed)
        self.assertLess(text.index('ignoreHTTPSErrors'), text.index("test('t'"))

    def test_skips_when_already_present(self):
        changed, text = self._run(
            "import { test } from '@playwright/test'\n"
            "test.use({ ignoreHTTPSErrors: true })\n")
        self.assertFalse(changed)

    def test_manual_new_context(self):
        changed, text = self._run(
            "import { chromium } from 'playwright'\n"
            'const context = await browser.newContext()\n')
        self.assertTrue(changed)
        self.assertIn('newContext({ ignoreHTTPSErrors: true })', text)

    def test_manual_new_context_with_options(self):
        changed, text = self._run(
            "import { chromium } from 'playwright'\n"
            "const context = await browser.newContext({ storageState: 's.json' })\n")
        self.assertTrue(changed)
        self.assertIn('ignoreHTTPSErrors: true', text)
        self.assertIn("storageState: 's.json'", text)

    def test_no_import_no_change(self):
        changed, _ = self._run("const x = 'ignore me'\n")
        self.assertFalse(changed)


class DownloadDirHeaderTest(unittest.TestCase):
    """有下载步骤时，平台补头要把浏览器下载目录绑到 FilesLibrary 用的那个目录。

    Chrome 的 download.default_directory 只认 Windows 原生反斜杠路径：
    `${CURDIR}/downloads` 这种混合斜杠会被**静默忽略**，文件落到用户默认下载目录，
    Download File 干等 30 秒报「没有出现下载完成的文件」——实测踩过。
    所以目录值必须由 FilesLibrary 自己算（它 resolve 过），不能平台另算一遍。
    """

    TARGET = 'tests/dl.robot'
    STEPS = [{'no': 1, 'type': 'goto', 'url': 'https://x/#/'}]
    BODY = ('*** Test Cases ***\n用例\n'
            '    Go To    https://x/#/\n'
            "    ${f}=    Download File    xpath=//b    min_bytes=1\n")

    def _run(self, files_lib='../.autocase/libraries/FilesLibrary.py'):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(root), True)
        path = root / self.TARGET
        path.parent.mkdir(parents=True)
        path.write_text(self.BODY, encoding='utf-8')
        emit._normalize_rf_script(str(root), self.TARGET, self.STEPS, files_lib=files_lib)
        return path.read_text(encoding='utf-8')

    def test_binds_download_dir_when_library_present(self):
        text = self._run()
        self.assertIn('Library    ${CURDIR}/../.autocase/libraries/FilesLibrary.py    ${CURDIR}/downloads', text)
        self.assertIn('${download_dir}=    Ensure Download Dir', text)
        self.assertIn('download.default_directory=${download_dir}', text)
        self.assertIn("download.prompt_for_download=${False}", text)
        # prefs 必须在创建 driver 之前设置，否则 Chrome 已经启动，改了也没用
        self.assertLess(text.index('add_experimental_option'),
                        text.index('Create Webdriver'))

    def test_no_pref_without_download_library(self):
        text = self._run(files_lib=None)
        self.assertNotIn('Ensure Download Dir', text)
        self.assertNotIn('add_experimental_option', text)


class SettlePromptTest(unittest.TestCase):
    """点击的「执行前等待」（新录步骤默认 5 秒）要原样进提示词。

    写错的地方只有两种：等错元素（写成下一步的），或给没有开关的步骤也补等待。
    所以数据和规则都要说清楚「等这一步自己的元素」「只对带 settle 的步骤」。
    """

    def test_prompt_step_keeps_settle(self):
        items = emit._steps_for_prompt(
            [{'no': 1, 'type': 'click', 'settle': {'timeout': 5}, 'target': None}],
            emit.RF_FRAMEWORK)
        self.assertEqual(items[0]['settle'], {'timeout': 5})

    def test_rules_tell_writer_to_wait_before_click(self):
        text = emit._settle_rules({
            'framework': emit.RF_FRAMEWORK,
            'steps': [{'no': 1, 'type': 'click', 'settle': {'timeout': 5}}],
        })
        self.assertIn('Wait Until Element Is Visible', text)
        self.assertIn('<settle.timeout>s', text)
        self.assertIn('没有 settle 的步骤一律不要加', text)

    def test_no_rules_without_settle(self):
        # 老草稿里的点击没有 settle，提示词里就不该出现等待规则。
        for framework in (emit.RF_FRAMEWORK, emit.PW_FRAMEWORK):
            self.assertEqual(emit._settle_rules({
                'framework': framework,
                'steps': [{'no': 1, 'type': 'click'}],
            }), '')


if __name__ == '__main__':
    unittest.main()
