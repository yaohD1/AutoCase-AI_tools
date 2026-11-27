"""任务书的改动清单与验收命令。

清单必须和实际写入同一套规则：这里断言的是「规划阶段就能预演出的文件」，
不依赖模型，也不写盘。
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.services.recording import emit


def target(alias, tag='input'):
    loc = 'css=%s[name="%s"]' % (tag, alias)
    return {
        'alias': alias,
        'snap': {'tag': tag, 'name': alias},
        'candidates': [{'kind': 'rf_css', 'spec': {'args': [loc]}, 'count': 1,
                        'index': 0, 'desc': 'rf ' + loc}],
        'primary': 'rf_css',
        'status': 'unique',
    }


def step(no, kind, **kw):
    base = {'no': no, 'type': kind, 'source': 'record', 'target': None, 'value': None,
            'label': None, 'url': None, 'page_index': 0, 'intent': None, 'text': None,
            'dialog': None}
    base.update(kw)
    return base


def row_assert(no=9):
    return step(no, 'assert', intent={
        'type': 'row_match',
        'table': {'rows_selector': 'tbody>tr', 'route': '/log'},
        'columns': [{'header': '操作', 'index': 0, 'value': '导入'}],
        'scope': 'any',
    })


def plan_of(steps):
    return {
        'target_path': '测试用例层/终端资产/导入.robot',
        'steps': [{'no': s['no'], 'action': 'write', 'note': 'x'} for s in steps],
        'reusable': [],
        'project_style': {'runner': 'robot'},
    }


class PlanFilesTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / '测试用例层/终端资产').mkdir(parents=True)
        self.target = '测试用例层/终端资产/导入.robot'

    def tearDown(self):
        self.tmp.cleanup()

    def files(self, steps, framework=emit.RF_FRAMEWORK, actions=None):
        return emit.plan_files(str(self.root), self.target, steps, framework, actions=actions)

    def test_script_without_extras(self):
        out = self.files([step(1, 'fill', target=target('用户名'), value='a')])
        self.assertEqual(['target', 'locator'], [f['role'] for f in out])
        self.assertEqual(self.target, out[0]['path'])
        self.assertEqual('.autocase/locators/终端资产/终端资产页面元素.resource', out[1]['path'])
        self.assertEqual('create', out[1]['state'])
        self.assertIn('第 1 步', out[1]['why'])

    def test_existing_files_become_overwrite_and_update(self):
        (self.root / self.target).write_text('old', encoding='utf-8')
        locator = self.root / '.autocase/locators/终端资产/终端资产页面元素.resource'
        locator.parent.mkdir(parents=True)
        locator.write_text('# old', encoding='utf-8')
        out = self.files([step(1, 'fill', target=target('用户名'), value='a')])
        self.assertEqual('overwrite', out[0]['state'])
        self.assertEqual('update', out[1]['state'])

    def test_libraries_follow_step_kinds(self):
        steps = [step(1, 'upload', target=target('上传')),
                 step(2, 'download', target=target('下载')),
                 row_assert(3)]
        paths = {f['path']: f for f in self.files(steps)}
        self.assertIn('.autocase/libraries/FilesLibrary.py', paths)
        self.assertIn('.autocase/libraries/TableLibrary.py', paths)
        self.assertNotIn('.autocase/libraries/SliderLibrary.py', paths)
        # 库路径必须是工作区相对路径，不能是 ../../.autocase（那是给 ${CURDIR}/ 拼的）
        self.assertFalse(any(p.startswith('..') for p in paths))

    def test_library_existing_says_update(self):
        lib = self.root / '.autocase/libraries/FilesLibrary.py'
        lib.parent.mkdir(parents=True)
        lib.write_text('# old', encoding='utf-8')
        paths = {f['path']: f for f in self.files([step(1, 'upload', target=target('上传'))])}
        self.assertEqual('update', paths['.autocase/libraries/FilesLibrary.py']['state'])

    def test_slider_inside_action_counts(self):
        actions = {'a1': {'id': 'a1', 'name': '登录',
                          'steps': [step(1, 'slider', target=target('滑块'))]}}
        steps = [step(1, 'call', action='a1')]
        paths = {f['path']: f for f in self.files(steps, actions=actions)}
        self.assertIn('.autocase/libraries/SliderLibrary.py', paths)

    def test_action_call_writes_common_locator(self):
        actions = {'a1': {'id': 'a1', 'name': '登录',
                          'steps': [step(1, 'fill', target=target('用户名'), value='a')]}}
        out = self.files([step(1, 'call', action='a1')], actions=actions)
        roles = {f['role']: f for f in out}
        self.assertIn('locator_common', roles)
        self.assertEqual('.autocase/locators/公共/公共页面元素.resource',
                         roles['locator_common']['path'])

    def test_uncalled_action_still_writes_shared_library(self):
        # 生成时拿的是整个动作库（不只本次调用到的）：库里任何一个动作带滑块，
        # 共享库都会被写一次。清单必须如实反映，否则「说的」和「做的」又对不上。
        actions = {'a1': {'id': 'a1', 'name': '登录',
                          'steps': [step(1, 'slider', target=target('滑块'))]}}
        paths = {f['path'] for f in self.files([step(1, 'click', target=target('登录'))],
                                              actions=actions)}
        self.assertIn('.autocase/libraries/SliderLibrary.py', paths)

    def test_playwright_uses_local_utils(self):
        steps = [step(1, 'upload', target=target('上传')), row_assert(2)]
        out = self.files(steps, framework=emit.PW_FRAMEWORK)
        paths = {f['path']: f for f in out}
        self.assertIn('测试用例层/终端资产/utils/files.ts', paths)
        self.assertIn('测试用例层/终端资产/utils/table.ts', paths)
        # PW 的辅助库放用例目录；定位变量仍是 .autocase 下的 resource（与现状一致）
        self.assertFalse(any(p.startswith('.autocase/libraries') for p in paths))

    def test_playwright_existing_utils_is_overwrite(self):
        utils = self.root / '测试用例层/终端资产/utils'
        utils.mkdir(parents=True)
        (utils / 'files.ts').write_text('old', encoding='utf-8')
        out = self.files([step(1, 'upload', target=target('上传'))], framework=emit.PW_FRAMEWORK)
        self.assertEqual('overwrite', [f for f in out if f['path'].endswith('files.ts')][0]['state'])

    def test_empty_target_returns_nothing(self):
        self.assertEqual([], emit.plan_files(str(self.root), '', [], emit.RF_FRAMEWORK))

    def test_failure_does_not_raise(self):
        # 目录不可读之类的情况不该让任务书整份失败
        with mock.patch.object(emit, '_locator_info', side_effect=RuntimeError('boom')):
            out = self.files([step(1, 'fill', target=target('用户名'), value='a')])
        self.assertEqual(['target'], [f['role'] for f in out])


class PlanCommandsTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        target = self.root / '测试用例层/终端资产/导入.robot'
        target.parent.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_rf_commands_use_project_venv(self):
        venv = self.root / '.venv/Scripts'
        venv.mkdir(parents=True)
        (venv / 'python.exe').write_text('', encoding='utf-8')
        out = emit.plan_commands(str(self.root), '测试用例层/终端资产/导入.robot',
                                 emit.RF_FRAMEWORK)
        self.assertEqual(2, len(out))
        self.assertIn('.venv/Scripts/python.exe -m robot 测试用例层/终端资产/导入.robot',
                      out[0]['command'])
        self.assertIn('--dryrun', out[1]['command'])
        # 真跑和语法检查要有区分，别让用户以为 dryrun 就够
        self.assertIn('不校验变量', out[1]['note'])

    def test_rf_without_venv_falls_back_to_python(self):
        out = emit.plan_commands(str(self.root), 'a.robot', emit.RF_FRAMEWORK)
        self.assertTrue(out[0]['command'].startswith('python -m robot'))

    def test_playwright_commands(self):
        out = emit.plan_commands(str(self.root), 'tests/a.spec.ts', emit.PW_FRAMEWORK)
        self.assertIn('npx playwright test tests/a.spec.ts', out[0]['command'])
        self.assertIn('--list', out[1]['command'])

    def test_command_quotes_path_with_space(self):
        out = emit.plan_commands(str(self.root), '测试 用例/a.robot', emit.RF_FRAMEWORK)
        self.assertIn('"测试 用例/a.robot"', out[0]['command'])

    def test_no_target_no_command(self):
        self.assertEqual([], emit.plan_commands(str(self.root), '', emit.RF_FRAMEWORK))


class NormalizePlanTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / '测试用例层/终端资产').mkdir(parents=True)
        self.steps = [step(1, 'fill', target=target('用户名'), value='a'),
                      step(2, 'click', target=target('登录'))]
        self.plan = plan_of(self.steps)

    def tearDown(self):
        self.tmp.cleanup()

    def normalize(self, plan=None):
        data = dict(self.plan if plan is None else plan)
        return emit._normalize_plan(data, '', workspace=str(self.root),
                                    framework=emit.RF_FRAMEWORK,
                                    flow_steps=self.steps, actions={})

    def test_summary_and_not_doing_are_cleaned(self):
        data = self.normalize(dict(self.plan, summary='  导入 CPE 并断言行结果  ',
                                   not_doing=['不改公共封装', '  ', '不新增依赖']))
        self.assertEqual('导入 CPE 并断言行结果', data['summary'])
        self.assertEqual(['不改公共封装', '不新增依赖'], data['not_doing'])

    def test_empty_summary_is_dropped(self):
        data = self.normalize(dict(self.plan, summary='   '))
        self.assertNotIn('summary', data)
        self.assertEqual([], data['not_doing'])

    def test_files_and_commands_injected(self):
        data = self.normalize()
        self.assertTrue(data['files'])
        self.assertEqual(2, len(data['commands']))
        self.assertEqual('create', data['files'][0]['state'])

    def test_user_path_wins_and_files_follow_it(self):
        data = emit._normalize_plan(dict(self.plan), '测试用例层/终端资产/指定.robot',
                                    workspace=str(self.root), framework=emit.RF_FRAMEWORK,
                                    flow_steps=self.steps, actions={})
        self.assertEqual('测试用例层/终端资产/指定.robot', data['target_path'])
        self.assertEqual('测试用例层/终端资产/指定.robot', data['files'][0]['path'])

    def test_list_failure_does_not_block_plan(self):
        with mock.patch.object(emit, 'plan_files', side_effect=RuntimeError('boom')):
            data = self.normalize()
        self.assertEqual([], data['files'])
        self.assertEqual([], data['commands'])
        self.assertTrue(data['steps'])  # 任务书本身仍然可用

    def test_plan_returns_files_shape(self):
        """plan() 的返回要把清单一起带出来：前端只读 result.plan.files。"""
        class FakeRunner:
            def __init__(self, payload):
                self.payload = payload

            def emit_planning(self, root, model, prompt):
                return json.dumps(self.payload)

        data = emit.plan(FakeRunner(self.plan), str(self.root), 'fake-model', self.steps,
                         emit.RF_FRAMEWORK, '', '导入终端资产')
        self.assertIn('files', data['plan'])
        self.assertEqual(2, len(data['plan']['files']))
        self.assertEqual(2, len(data['plan']['commands']))


if __name__ == '__main__':
    unittest.main()
