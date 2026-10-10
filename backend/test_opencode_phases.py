import tempfile
import unittest
from pathlib import Path

from app.routes.automation import _build_dispatch
from app.services.opencode_runner import OpenCodeRunner
from app.utils.case_markdown import parse_cases, render_cases


class PlanProblemsTest(unittest.TestCase):
    def _plan(self, text):
        handle = tempfile.NamedTemporaryFile('w', suffix='.md', delete=False, encoding='utf-8')
        handle.write(text)
        handle.close()
        return Path(handle.name)

    def test_valid_plan_has_no_problems(self):
        path = self._plan('**Seed:** tests/seed.spec.ts\n\n#### 1.1 登录成功\n')
        self.assertEqual(OpenCodeRunner._plan_problems(path), [])

    def test_missing_seed_line_is_reported(self):
        path = self._plan('#### 1.1 登录成功\n')
        self.assertIn('缺少 **Seed:** 行', OpenCodeRunner._plan_problems(path))

    def test_missing_scenario_heading_is_reported(self):
        path = self._plan('**Seed:** tests/seed.spec.ts\n\n### 1. 模块\n')
        self.assertIn('缺少 #### 用例标题', OpenCodeRunner._plan_problems(path))


class DispatchTest(unittest.TestCase):
    def test_planner_and_generator_receive_seed_and_project(self):
        dispatch = _build_dispatch('登录', 'http://x', 'autocase/runs/r/tests', 'autocase/runs/r/test-plans', 3,
                                   'tests/seed.spec.ts', 'chromium')
        self.assertIn('seed_file: tests/seed.spec.ts', dispatch['planner'])
        self.assertIn('project: chromium', dispatch['planner'])
        self.assertIn('seed_file: tests/seed.spec.ts', dispatch['generator'])
        self.assertIn('plan_file: <PLAN_FILE>', dispatch['generator'])

    def test_every_phase_gets_output_language(self):
        dispatch = _build_dispatch('登录', 'http://x', 'autocase/runs/r/tests', 'autocase/runs/r/test-plans', 3,
                                   'tests/seed.spec.ts', 'chromium')
        for phase in ('planner', 'generator', 'healer'):
            self.assertTrue(any(line.startswith('output_language:') for line in dispatch[phase]), phase)

    def test_healer_has_generated_files_placeholder(self):
        dispatch = _build_dispatch('登录', 'http://x', 'autocase/runs/r/tests', 'autocase/runs/r/test-plans', 3,
                                   'tests/seed.spec.ts', 'chromium')
        self.assertIn('generated_files: <GENERATED_FILES>', dispatch['healer'])
        self.assertNotIn('seed_file: tests/seed.spec.ts', dispatch['healer'])


class StageMappingTest(unittest.TestCase):
    def test_only_own_agents_map_to_stages(self):
        self.assertEqual(OpenCodeRunner._agent_to_stage('playwright-test-planner'), 'planner')
        self.assertEqual(OpenCodeRunner._agent_to_stage('playwright-test-generator'), 'generator')
        self.assertEqual(OpenCodeRunner._agent_to_stage('playwright-test-healer'), 'healer')
        self.assertIsNone(OpenCodeRunner._agent_to_stage('explore'))
        self.assertIsNone(OpenCodeRunner._agent_to_stage('general'))

    def test_explore_child_inherits_parent_stage(self):
        state = {'session_stage': {}, 'healer_sessions': set()}
        tool_state = {
            'metadata': {'sessionID': 'child-explore'},
            'input': {'subagent_type': 'explore', 'description': '生成 generator_write_test 映射逻辑'},
        }
        OpenCodeRunner({})._register_task_session(state, tool_state, 'generator')
        self.assertEqual(state['session_stage']['child-explore'], 'generator')

    def test_description_text_does_not_relabel_child(self):
        state = {'session_stage': {}, 'healer_sessions': set()}
        tool_state = {
            'metadata': {'sessionID': 'child-x'},
            'input': {'subagent_type': 'explore', 'description': 'planner healer generator'},
        }
        OpenCodeRunner({})._register_task_session(state, tool_state, 'generator')
        self.assertEqual(state['session_stage']['child-x'], 'generator')


class CasesModeDispatchTest(unittest.TestCase):
    def test_cases_mode_has_no_planner_and_uses_cases_file(self):
        dispatch = _build_dispatch('按用例', 'http://x', 'autocase/runs/r/tests', 'autocase/runs/r/test-plans', 3,
                                   'tests/seed.spec.ts', 'chromium', cases_file='autocase/runs/r/cases/cases.md')
        self.assertNotIn('planner', dispatch)
        self.assertIn('cases_file: autocase/runs/r/cases/cases.md', dispatch['generator'])
        self.assertFalse(any('<PLAN_FILE>' in line for line in dispatch['generator']))
        self.assertIn('generated_files: <GENERATED_FILES>', dispatch['healer'])

    def test_requirement_mode_is_unchanged_without_cases_file(self):
        dispatch = _build_dispatch('登录', 'http://x', 'autocase/runs/r/tests', 'autocase/runs/r/test-plans', 3,
                                   'tests/seed.spec.ts', 'chromium')
        self.assertIn('planner', dispatch)
        self.assertIn('plan_file: <PLAN_FILE>', dispatch['generator'])
        self.assertFalse(any(line.startswith('cases_file:') for line in dispatch['generator']))


class CaseMarkdownTest(unittest.TestCase):
    def test_labels_and_suffixes_are_stripped(self):
        seed, cases, errors = parse_cases(
            '**Seed:** tests/seed.spec.ts\n\n'
            '#### 验证码错误提示 #P0\n'
            '- 优先级：P0\n- 模块：登录页\n- 测试点：验证码\n'
            '- 前置条件：\n  1. 打开登录页\n'
            '- 步骤：\n  1. 步骤：输入用户名\n  2. 点击登录\n'
            '- 预期：\n  - 预期：提示验证码错误\n'
        )
        self.assertEqual((seed, errors), ('tests/seed.spec.ts', []))
        case = cases[0]
        self.assertEqual(case['title'], '验证码错误提示')
        self.assertEqual(case['steps'], ['输入用户名', '点击登录'])
        self.assertEqual(case['expected'], ['提示验证码错误'])
        self.assertEqual(case['preconditions'], ['打开登录页'])

    def test_plain_items_without_labels_are_accepted(self):
        _, cases, errors = parse_cases(
            '**Seed:** tests/seed.spec.ts\n\n#### 登录\n- 步骤：\n  1. 点击登录\n- 预期：\n  - 进入首页\n'
        )
        self.assertEqual(errors, [])
        self.assertEqual(cases[0]['steps'], ['点击登录'])

    def test_missing_seed_is_reported(self):
        _, _, errors = parse_cases('#### 登录\n- 步骤：\n  1. 点击\n- 预期：\n  - 成功\n')
        self.assertTrue(any('Seed' in e for e in errors))

    def test_missing_steps_is_reported(self):
        _, _, errors = parse_cases('**Seed:** tests/seed.spec.ts\n\n#### 登录\n- 预期：\n  - 成功\n')
        self.assertTrue(any('缺少步骤' in e for e in errors))

    def test_invalid_priority_is_reported(self):
        _, _, errors = parse_cases(
            '**Seed:** tests/seed.spec.ts\n\n#### 登录\n- 优先级：P9\n- 步骤：\n  1. 点击\n- 预期：\n  - 成功\n'
        )
        self.assertTrue(any('P0–P3' in e for e in errors))

    def test_no_cases_is_reported(self):
        _, _, errors = parse_cases('**Seed:** tests/seed.spec.ts\n\n只有一段说明\n')
        self.assertTrue(any('没有找到任何用例' in e for e in errors))

    def test_render_then_parse_round_trips(self):
        original = [{'title': '登录', 'priority': 'P1', 'module': 'm', 'test_point': 'p',
                     'preconditions': ['打开页面'], 'steps': ['点击'], 'expected': ['成功']}]
        _, cases, errors = parse_cases(render_cases('tests/seed.spec.ts', original))
        self.assertEqual(errors, [])
        self.assertEqual(cases[0]['steps'], ['点击'])
        self.assertEqual(cases[0]['expected'], ['成功'])


class ScopeTest(unittest.TestCase):
    def test_spec_outside_batch_dir_is_violation(self):
        files = [
            {'path': 'tests/login-page/a.spec.ts'},
            {'path': 'autocase/runs/r/tests/a.spec.ts'},
            {'path': 'autocase/runs/r/test-plans/p.md'},
        ]
        violations = OpenCodeRunner._file_scope(files, 'autocase/runs/r/tests', 'autocase/runs/r/test-plans')
        self.assertEqual(violations, ['tests/login-page/a.spec.ts'])


if __name__ == '__main__':
    unittest.main()
