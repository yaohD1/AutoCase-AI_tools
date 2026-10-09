import tempfile
import unittest
from pathlib import Path

from app.routes.automation import _build_dispatch
from app.services.opencode_runner import OpenCodeRunner


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
