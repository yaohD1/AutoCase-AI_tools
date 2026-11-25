# -*- coding: utf-8 -*-
"""执行测试：报告解析、用例回填、路由校验。

这里不启真实浏览器/robot——真实链路在开发时用两个真项目跑过（Playwright 25 条、
Robot 508 条）。单测覆盖的是那些「只有出错时才看得见」的地方：
报告字段对不上、路径越界、选中项已经不在项目里。
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TMP = tempfile.mkdtemp(prefix='recording-runner-')
_SLASH = chr(47)
os.environ['DATABASE_URL'] = 'sqlite:///' + os.path.join(_TMP, 'test.db').replace(chr(92), _SLASH)
os.environ['AUTOMATION_WORKSPACE_ROOT'] = os.path.join(_TMP, 'workspaces')
Path(os.environ['AUTOMATION_WORKSPACE_ROOT'], 'proj').mkdir(parents=True, exist_ok=True)

from app import create_app  # noqa: E402
from app.models import db  # noqa: E402
from app.models.test_run import TestRun  # noqa: E402
from app.services.recording import report_html, runner  # noqa: E402
from test_support import reset_database  # noqa: E402


def _pw_report(status='passed', attachments=None, error=None, line=6):
    result = {'status': status, 'duration': 3912, 'retry': 0, 'attachments': attachments or []}
    if error:
        result['error'] = {'message': error}
    return {
        'stats': {'expected': 1, 'unexpected': 0, 'skipped': 0, 'flaky': 0},
        'suites': [{
            'title': '', 'file': 'login-page/x.spec.ts',
            'specs': [{
                'title': '账号密码登录失败提示', 'file': 'login-page/x.spec.ts',
                'line': line, 'tags': [],
                'tests': [{'projectName': 'chromium', 'results': [result]}],
            }],
            'suites': [],
        }],
    }


class PwCollectTest(unittest.TestCase):
    def setUp(self):
        self.artifacts = Path(_TMP, 'art', 'run1')
        (self.artifacts / 'pw-0').mkdir(parents=True, exist_ok=True)

    def test_passed_case(self):
        rows = runner._pw_collect(_pw_report(), self.artifacts)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['status'], 'passed')
        self.assertEqual(rows[0]['duration_ms'], 3912)
        self.assertEqual(rows[0]['project'], 'chromium')

    def test_failure_keeps_error_and_screenshot(self):
        shot = self.artifacts / 'pw-0' / 'test-failed-1.png'
        shot.write_bytes(b'png')
        trace = self.artifacts / 'pw-0' / 'trace.zip'
        trace.write_bytes(b'zip')
        report = _pw_report('timedOut', attachments=[
            {'name': 'screenshot', 'contentType': 'image/png', 'path': str(shot)},
            {'name': 'trace', 'contentType': 'application/zip', 'path': str(trace)},
            # 产物目录外的附件不该被带出来：报告里可能出现用户自己挂的绝对路径。
            {'name': 'outside', 'contentType': 'image/png', 'path': str(Path(_TMP, 'x.png'))},
        ], error='Test timeout of 1500ms exceeded.')
        rows = runner._pw_collect(report, self.artifacts)
        self.assertEqual(rows[0]['status'], 'timedOut')
        self.assertIn('timeout', rows[0]['error'])
        self.assertEqual(rows[0]['screenshots'], ['pw-0/test-failed-1.png'])
        names = [item['name'] for item in rows[0]['artifacts']]
        self.assertIn('trace', names)
        self.assertNotIn('outside', names)

    def test_env_error_flagged(self):
        report = _pw_report('failed', error='page.goto: net::ERR_CONNECTION_REFUSED at http://x')
        rows = runner._pw_collect(report, self.artifacts)
        self.assertTrue(rows[0]['env_error'])


class LabelTest(unittest.TestCase):
    def test_playwright_matches_by_run_path_and_line(self):
        selected = [{'id': 'pw|chromium|tests/login-page/x.spec.ts|6|账号密码登录失败提示',
                     'run_path': 'login-page/x.spec.ts', 'line': 6,
                     'title': '账号密码登录失败提示', 'project': 'chromium',
                     'file': 'tests/login-page/x.spec.ts'}]
        rows = runner._pw_collect(_pw_report(), Path(_TMP, 'art'))
        rows = runner._label(rows, selected)
        self.assertEqual(rows[0]['id'], selected[0]['id'])
        # 结果里的路径要换成列表里那套（相对工作区），否则前端挂不上勾选项。
        self.assertEqual(rows[0]['file'], 'tests/login-page/x.spec.ts')

    def test_robot_matches_by_name(self):
        selected = [{'id': 'rf|a.robot|登录成功', 'title': '登录成功', 'file': 'a.robot', 'project': ''}]
        rows = [{'id': '', 'file': '', 'title': '登录成功', 'project': '', 'status': 'passed'}]
        rows = runner._label(rows, selected, by_name=True)
        self.assertEqual(rows[0]['id'], 'rf|a.robot|登录成功')
        self.assertEqual(rows[0]['file'], 'a.robot')


RF_XML = """<?xml version="1.0" encoding="UTF-8"?>
<robot generator="Robot 3.1.2 (Python 3.9.13 on win32)" generated="20261119 16:33:56.781" rpa="false">
<suite id="s1" name="环境自检" source="D:\\ws\\环境自检.robot">
<test id="s1-t1" name="能打开登录页">
<kw name="Capture Page Screenshot" library="SeleniumLibrary"><msg level="INFO">&lt;a href="smoke_login_page.png"&gt;link&lt;/a&gt;</msg>
<status status="PASS" starttime="20261119 16:34:00.000" endtime="20261119 16:34:01.500"></status></kw>
<status status="PASS" starttime="20261119 16:33:58.870" endtime="20261119 16:34:02.491"></status>
</test>
<test id="s1-t2" name="故意失败">
<tags><tag>smoke</tag></tags><doc>验证失败提示</doc>
<kw name="Should Be Equal" library="BuiltIn"><msg level="FAIL">这两个值不一样: 实际值 != 期望值</msg>
<status status="FAIL" starttime="20261119 16:34:02.500" endtime="20261119 16:34:03.000"></status></kw>
<status status="FAIL" starttime="20261119 16:34:02.500" endtime="20261119 16:34:03.000"></status>
</test>
<test id="s1-t3" name="被跳过">
<status status="SKIP" starttime="20261119 16:34:03.100" endtime="20261119 16:34:03.100"></status>
</test>
<status status="FAIL" starttime="20261119 16:33:56.781" endtime="20261119 16:34:03.100"></status>
</suite>
</robot>
"""


class RobotCollectTest(unittest.TestCase):
    def _collect(self):
        artifacts = Path(_TMP, 'art-rf')
        out = artifacts / 'rf-0'
        out.mkdir(parents=True, exist_ok=True)
        (out / 'smoke_login_page.png').write_bytes(b'png')
        (out / 'output.xml').write_text(RF_XML, encoding='utf-8')
        return runner._rf_collect(out, artifacts, 'D:\\ws')

    def test_parse_output_xml(self):
        rows, env = self._collect()
        self.assertEqual([row['title'] for row in rows], ['能打开登录页', '故意失败', '被跳过'])
        self.assertEqual([row['status'] for row in rows], ['passed', 'failed', 'skipped'])
        # 3621ms：RF 的时间戳是 `20261119 16:33:58.870`，切错一位这里就是 0。
        self.assertEqual(rows[0]['duration_ms'], 3621)
        self.assertIn('这两个值不一样', rows[1]['error'])
        self.assertEqual(rows[0]['screenshots'], ['rf-0/smoke_login_page.png'])

    def test_file_comes_from_suite_source(self):
        rows, _env = self._collect()
        # 用例名匹配不到列表时，文件路径是最后的兜底，不能是空串
        self.assertEqual(rows[0]['file'], '环境自检.robot')

    def test_env_parsed_from_generator(self):
        _rows, env = self._collect()
        self.assertEqual(env['tool'], 'Robot')
        self.assertEqual(env['version'], '3.1.2')
        self.assertEqual(env['runtime'], 'Python 3.9.13 on win32')

    def test_tags_and_doc(self):
        rows, _env = self._collect()
        self.assertEqual(rows[1]['tags'], ['smoke'])
        self.assertEqual(rows[1]['doc'], '验证失败提示')

    def test_steps_kept_for_every_case(self):
        """跑过的用例都留步骤：通过的也要能回看「走了哪几步、哪一步最慢」。"""
        rows, _env = self._collect()
        passed = rows[0]['steps']
        self.assertEqual(len(passed), 1)
        self.assertEqual(passed[0]['title'], 'Capture Page Screenshot')
        self.assertEqual(passed[0]['status'], 'passed')
        self.assertEqual(passed[0]['duration_ms'], 1500)
        failed = rows[1]['steps']
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]['title'], 'Should Be Equal')
        self.assertEqual(failed[0]['category'], 'BuiltIn')
        self.assertEqual(failed[0]['duration_ms'], 500)

    def test_missing_output_returns_empty(self):
        self.assertEqual(runner._rf_collect(Path(_TMP, 'nope'), Path(_TMP, 'art')), ([], {}))


class JsonBlobTest(unittest.TestCase):
    def test_skips_dotenvx_preamble(self):
        text = ('◇ injected env (2) from .env // tip: ⌘ multiple files { path: [".env.local"] }\n'
                '◇ more { braces }\n'
                '{"stats": {"expected": 1}, "suites": []}\n')
        data = runner._json_blob(text)
        self.assertEqual(data['stats']['expected'], 1)

    def test_none_when_no_json(self):
        self.assertIsNone(runner._json_blob('boom: config error'))


class CasesInTest(unittest.TestCase):
    """按文件枚举：生成完跳执行页靠它，坏路径只进 missing，不能整页报错。"""

    def test_missing_files_are_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            got = runner.cases_in(tmp, ['nope.robot', '..' + _SLASH + 'escape.robot'])
        self.assertEqual(got['cases'], [])
        self.assertIn('nope.robot', got['missing'])
        self.assertIn('..' + _SLASH + 'escape.robot', got['missing'])

    def test_file_unparsable_reports_missing(self):
        """文件在、但解析不出来（环境缺库、语法没被 RF 认出来）只记 missing，不掀桌子。"""
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'a.robot').write_text('*** Test Cases ***\n案例\n    Log    1\n', encoding='utf-8')
            with mock.patch.object(runner, '_framework', return_value='robot-selenium'), \
                    mock.patch.object(runner, '_rf_python', return_value='/usr/bin/python'), \
                    mock.patch.object(runner, '_rf_rows', side_effect=runner.RunnerError('boom')):
                got = runner.cases_in(tmp, ['a.robot'])
        self.assertEqual(got['cases'], [])
        self.assertIn('a.robot', got['missing'])

    def test_no_python_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'a.robot').write_text('*** Test Cases ***\n案例\n    Log    1\n', encoding='utf-8')
            with mock.patch.object(runner, '_framework', return_value='robot-selenium'), \
                    mock.patch.object(runner, '_rf_python', return_value=None):
                with self.assertRaises(runner.RunnerError):
                    runner.cases_in(tmp, ['a.robot'])


class RouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config['TESTING'] = True
        cls.app.config['TEST_RUN_FOLDER'] = os.path.join(_TMP, 'runs')
        # 全量跑时别的测试模块也会写 AUTOMATION_WORKSPACE_ROOT，谁后导入谁生效；
        # 这里显式钉一次，免得「工作区越界」的断言依赖模块导入顺序。
        cls.app.config['AUTOMATION_WORKSPACE_ROOT'] = os.environ['AUTOMATION_WORKSPACE_ROOT']
        Path(os.environ['AUTOMATION_WORKSPACE_ROOT'], 'proj').mkdir(parents=True, exist_ok=True)
        cls.client = cls.app.test_client()

    def setUp(self):
        with self.app.app_context():
            reset_database()

    def test_workspace_outside_root_rejected(self):
        r = self.client.get('/api/automation/testrun/cases', query_string={'workspace': 'C:/Windows'})
        self.assertEqual(r.status_code, 400)
        self.assertIn('AUTOMATION_WORKSPACE_ROOT', r.get_json()['error'])

    def test_unknown_workspace_rejected(self):
        r = self.client.get('/api/automation/testrun/cases', query_string={'workspace': 'nope'})
        self.assertEqual(r.status_code, 400)

    def test_cases_returns_normalized_list(self):
        fake = {'framework': 'playwright-ts', 'root': '/ws', 'note': None, 'discovered_at': 1,
                'cases': [{'id': 'pw|a', 'file': 'tests/a.spec.ts', 'title': 'x',
                           'file_total': 1, '_private': 'hidden'}]}
        with mock.patch.object(runner, 'discover', return_value=fake):
            r = self.client.get('/api/automation/testrun/cases', query_string={'workspace': 'proj'})
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertEqual(data['framework'], 'playwright-ts')
        self.assertNotIn('_private', data['cases'][0])

    def test_cases_files_param_uses_cases_in(self):
        fake = {'framework': 'robot-selenium', 'root': '/ws', 'missing': [],
                'cases': [{'id': 'rf|a.robot|x', 'file': 'a.robot', 'title': 'x',
                           'file_total': 1, '_private': 'hidden'}]}
        with mock.patch.object(runner, 'cases_in', return_value=fake) as patched:
            r = self.client.get('/api/automation/testrun/cases',
                                query_string={'workspace': 'proj', 'files': 'a.robot'})
        self.assertEqual(r.status_code, 200)
        patched.assert_called_once()
        # 指定文件时必须走按文件枚举：discover 的缓存会把刚写出的文件挡住。
        self.assertEqual(r.get_json()['cases'][0]['id'], 'rf|a.robot|x')
        self.assertNotIn('_private', r.get_json()['cases'][0])
        self.assertEqual(r.get_json()['missing'], [])

    def test_create_run_rejects_unknown_selection(self):
        fake = {'framework': 'playwright-ts', 'root': '/ws', 'note': None, 'discovered_at': 1,
                'cases': []}
        with mock.patch.object(runner, 'discover', return_value=fake):
            r = self.client.post('/api/automation/testrun/runs',
                                 json={'workspace': 'proj', 'cases': ['pw|gone']})
        self.assertEqual(r.status_code, 400)
        self.assertIn('找不到', r.get_json()['error'])

    def test_artifact_path_containment(self):
        with self.app.app_context():
            run = TestRun(workspace='/ws', framework='playwright-ts', status='passed',
                          artifacts_dir=os.path.join(_TMP, 'runs', 'r1'))
            db.session.add(run)
            db.session.commit()
            run_id = run.id
            safe_dir = Path(run.artifacts_dir, 'pw-0')
            safe_dir.mkdir(parents=True, exist_ok=True)
            (safe_dir / 'a.png').write_bytes(b'png')

            self.assertEqual(runner.artifact_path(run, 'pw-0/a.png').name, 'a.png')
            with self.assertRaises(runner.RunnerError):
                runner.artifact_path(run, '../../../app.db')
            with self.assertRaises(runner.RunnerError):
                runner.artifact_path(run, 'pw-0/missing.png')

        r = self.client.get('/api/automation/testrun/runs/%s/artifact' % run_id,
                            query_string={'path': '../../../app.db'})
        self.assertEqual(r.status_code, 400)

    def test_stop_unknown_run(self):
        r = self.client.post('/api/automation/testrun/runs/nope/stop')
        self.assertEqual(r.status_code, 404)

    def test_list_runs_filters_by_workspace(self):
        with self.app.app_context():
            db.session.add(TestRun(workspace=os.path.realpath(os.path.join(_TMP, 'workspaces', 'proj')),
                                   framework='playwright-ts', status='passed'))
            db.session.commit()
        r = self.client.get('/api/automation/testrun/runs', query_string={'workspace': 'proj'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.get_json()['runs']), 1)
        self.assertNotIn('results', r.get_json()['runs'][0])

    def _mk_run(self, status='passed', rows=None):
        with self.app.app_context():
            run = TestRun(workspace='/ws', framework='playwright-ts', status=status,
                          total=len(rows or []), passed=len(rows or []),
                          results_json=json.dumps({'env': {}, 'rows': rows or []}, ensure_ascii=False))
            db.session.add(run)
            db.session.commit()
            return run.id

    def test_export_running_rejected(self):
        r = self.client.get('/api/automation/testrun/runs/%s/export' % self._mk_run('running'))
        self.assertEqual(r.status_code, 409)
        self.assertIn('还没结束', r.get_json()['error'])

    def test_export_returns_html_attachment(self):
        run_id = self._mk_run('passed', [{'title': '登录成功', 'status': 'passed', 'duration_ms': 1200}])
        r = self.client.get('/api/automation/testrun/runs/%s/export' % run_id)
        self.assertEqual(r.status_code, 200)
        self.assertIn('attachment', r.headers['Content-Disposition'])
        body = r.get_data(as_text=True)
        self.assertIn('登录成功', body)
        self.assertIn('通过', body)

    def test_export_unknown_run(self):
        self.assertEqual(self.client.get('/api/automation/testrun/runs/nope/export').status_code, 404)


class PersistBudgetTest(unittest.TestCase):
    """步骤树改成一概保留后，结果 JSON 可能很大。兜底裁剪只准动通过用例，且要留痕。"""

    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config['TESTING'] = True

    def setUp(self):
        with self.app.app_context():
            reset_database()

    def _rows(self):
        steps = [{'title': '第 %d 步' % i, 'category': 'pw:api', 'duration_ms': 10,
                  'status': 'passed', 'depth': 0} for i in range(8)]
        return [
            {'id': '', 'file': 'a.spec.ts', 'title': '通过的那条', 'status': 'passed',
             'steps': list(steps), 'duration_ms': 80},
            {'id': '', 'file': 'a.spec.ts', 'title': '失败的那条', 'status': 'failed',
             'steps': list(steps), 'duration_ms': 80},
        ]

    def _persist(self, rows, env, budget):
        with self.app.app_context():
            run = TestRun(workspace='/ws', framework='playwright-ts', status='running')
            db.session.add(run)
            db.session.commit()
            with mock.patch.object(runner, 'RESULTS_BUDGET', budget):
                runner._persist(run, rows, 0, runner.RunHandle(run.id), env)
            return run.to_dict()

    def test_over_budget_keeps_only_failed_steps(self):
        data = self._persist(self._rows(), {}, 400)
        passed, failed = data['results']
        self.assertEqual(passed['steps'], [])
        self.assertEqual(len(failed['steps']), 8)
        # 省了多少条要写进 env，导出的报告里会说明，不能悄悄少东西
        self.assertEqual(data['env']['steps_trimmed'], 1)

    def test_within_budget_keeps_everything(self):
        data = self._persist(self._rows(), {}, 10 * 1024 * 1024)
        self.assertEqual(len(data['results'][0]['steps']), 8)
        self.assertNotIn('steps_trimmed', data['env'])


class JsonlCollectTest(unittest.TestCase):
    """自带 reporter 的 JSONL 解析。"""

    def _write(self, lines):
        path = Path(_TMP, 'pw-probe.jsonl')
        path.write_text('\n'.join(json.dumps(item, ensure_ascii=False) for item in lines) + '\n',
                        encoding='utf-8')
        return path

    def test_env_steps_and_location(self):
        path = self._write([
            {'type': 'env', 'framework': 'playwright', 'tool': 'Playwright',
             'version': '1.62.1', 'runtime': 'Node v22', 'root_dir': 'D:/ws/tests'},
            {'type': 'test', 'title': '登录', 'file': 'D:/ws/tests/a.spec.ts', 'line': 6,
             'project': 'chromium', 'status': 'failed', 'duration_ms': 1200, 'retry': 0,
             'error': 'boom', 'error_location': {'file': 'D:/ws/src/x.ts', 'line': 48, 'column': 9},
             'tags': ['smoke'], 'attachments': [],
             'steps': [{'title': 'Navigate', 'category': 'pw:api', 'duration_ms': 700,
                        'status': 'passed', 'depth': 0}]},
        ])
        rows, env = runner._pw_read_jsonl(path, Path(_TMP, 'art'))
        self.assertEqual(env['version'], '1.62.1')
        # reporter 给的是绝对路径，要换成相对 rootDir 的，才能和用例列表对上
        self.assertEqual(rows[0]['file'], 'a.spec.ts')
        self.assertEqual(rows[0]['where'], 'D:/ws/src/x.ts:48:9')
        self.assertEqual(len(rows[0]['steps']), 1)
        self.assertEqual(rows[0]['tags'], ['smoke'])

    def test_passed_case_keeps_steps(self):
        path = self._write([
            {'type': 'env', 'root_dir': ''},
            {'type': 'test', 'title': 'ok', 'file': '', 'line': 1, 'project': '',
             'status': 'passed', 'duration_ms': 10, 'retry': 0, 'error': '',
             'attachments': [], 'steps': [{'title': 'a', 'duration_ms': 1, 'depth': 0}]},
        ])
        rows, _env = runner._pw_read_jsonl(path, Path(_TMP, 'art'))
        self.assertEqual(len(rows[0]['steps']), 1)

    def test_retry_keeps_last_attempt(self):
        base = {'type': 'test', 'title': 'flaky', 'file': 'a.spec.ts', 'line': 6,
                'project': 'chromium', 'duration_ms': 10, 'error': '',
                'attachments': [], 'steps': []}
        path = self._write([{'type': 'env'},
                            dict(base, status='failed', retry=0),
                            dict(base, status='passed', retry=1)])
        rows, _env = runner._pw_read_jsonl(path, Path(_TMP, 'art'))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['status'], 'passed')
        self.assertEqual(rows[0]['retry'], 1)

    def test_missing_file_returns_empty(self):
        self.assertEqual(runner._pw_read_jsonl(Path(_TMP, 'nope.jsonl'), Path(_TMP, 'art')), ([], {}))


class ReportHtmlTest(unittest.TestCase):
    def _run(self, rows, env=None, status='failed', raw=None):
        return TestRun(workspace='D:\\ws\\proj', framework='playwright-ts', status=status,
                       total=len(rows), passed=0, failed=1, duration_ms=1200,
                       results_json=raw if raw is not None else json.dumps(
                           {'env': env or {}, 'rows': rows}, ensure_ascii=False))

    def test_escapes_case_text(self):
        rows = [{'title': '<script>x</script>', 'status': 'failed', 'duration_ms': 1200,
                 'file': 'tests/a.spec.ts', 'error': 'boom <b>', 'where': 'a.ts:1:2',
                 'steps': [{'title': 'Click', 'duration_ms': 900, 'depth': 0, 'status': 'failed'}],
                 'screenshots': []}]
        body = report_html.render(self._run(rows))
        # 用例标题/错误来自被测项目，必须转义，否则导出的文件在别人的浏览器里执行脚本
        self.assertIn('&lt;script&gt;', body)
        self.assertNotIn('<script>x</script>', body)
        self.assertIn('Click', body)
        self.assertIn('a.ts:1:2', body)

    def test_env_line_rendered(self):
        rows = [{'title': 'ok', 'status': 'passed', 'duration_ms': 10}]
        body = report_html.render(self._run(
            rows, env={'framework': 'playwright', 'tool': 'Playwright', 'version': '1.62.1',
                       'runtime': 'Node v22', 'projects': ['chromium']}, status='passed'))
        self.assertIn('Playwright 1.62.1', body)
        self.assertIn('Node v22', body)

    def test_nested_steps_fold_into_details(self):
        """RF 的 keyword 树是代码级的：顶层折叠成 details，失败的那棵默认展开。"""
        rows = [{'title': '登录', 'status': 'failed', 'duration_ms': 30000, 'screenshots': [],
                 'steps': [
                     {'title': '打开浏览器', 'duration_ms': 29000, 'depth': 0, 'status': 'passed'},
                     {'title': 'Go To', 'duration_ms': 353, 'depth': 1, 'status': 'passed'},
                     {'title': '登录', 'duration_ms': 10000, 'depth': 0, 'status': 'failed'},
                     {'title': 'Click Element', 'duration_ms': 69, 'depth': 1, 'status': 'failed'},
                 ]}]
        body = report_html.render(self._run(rows))
        self.assertEqual(body.count('<details class="fold"'), 2)
        self.assertEqual(body.count('<details class="fold" open>'), 1)
        self.assertIn('2 个顶层步骤 · 共 4 步', body)
        self.assertIn('Go To', body)

    def test_zero_ms_steps_hidden(self):
        """0ms 的胶水步骤默认不出现，但要在标题里交代藏了几条。"""
        rows = [{'title': 'ok', 'status': 'passed', 'duration_ms': 400, 'screenshots': [],
                 'steps': [
                     {'title': '登录页面操作', 'duration_ms': 400, 'depth': 0, 'status': 'passed'},
                     {'title': 'Call Method', 'duration_ms': 0, 'depth': 1, 'status': 'passed'},
                     {'title': 'Go To', 'duration_ms': 353, 'depth': 1, 'status': 'passed'},
                 ]}]
        body = report_html.render(self._run(rows, status='passed'))
        self.assertNotIn('Call Method', body)
        self.assertIn('已隐藏 1 个 0ms 步骤', body)

    def test_trim_notice_rendered(self):
        """体积兜底裁掉步骤后，报告里要写明，不能悄悄少东西。"""
        rows = [{'title': 'ok', 'status': 'passed', 'duration_ms': 10}]
        body = report_html.render(self._run(rows, env={'steps_trimmed': 3}, status='passed'))
        self.assertIn('已省略 3 条通过用例', body)

    def test_legacy_array_results_still_render(self):
        """早期记录是纯数组，老报告不能打不开。"""
        run = self._run([], raw=json.dumps([{'title': '旧结果', 'status': 'passed', 'duration_ms': 5}]))
        self.assertEqual(len(run.results()), 1)
        self.assertEqual(run.env(), {})
        self.assertIn('旧结果', report_html.render(run))

    def test_no_results_message(self):
        self.assertIn('没有产生结果', report_html.render(self._run([], status='error')))


if __name__ == '__main__':
    unittest.main()
