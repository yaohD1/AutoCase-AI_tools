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
from app.services.recording import runner  # noqa: E402


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
<robot generator="Robot 3.1.2" generated="20261119 16:33:56.781" rpa="false">
<suite id="s1" name="环境自检" source="D:\\ws\\环境自检.robot">
<test id="s1-t1" name="能打开登录页">
<kw name="Capture Page Screenshot"><msg level="INFO">&lt;a href="smoke_login_page.png"&gt;link&lt;/a&gt;</msg></kw>
<status status="PASS" starttime="20261119 16:33:58.870" endtime="20261119 16:34:02.491"></status>
</test>
<test id="s1-t2" name="故意失败">
<kw name="Should Be Equal"><msg level="FAIL">这两个值不一样: 实际值 != 期望值</msg></kw>
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
    def test_parse_output_xml(self):
        artifacts = Path(_TMP, 'art-rf')
        out = artifacts / 'rf-0'
        out.mkdir(parents=True, exist_ok=True)
        (out / 'smoke_login_page.png').write_bytes(b'png')
        (out / 'output.xml').write_text(RF_XML, encoding='utf-8')

        rows = runner._rf_collect(out, artifacts)
        self.assertEqual([row['title'] for row in rows], ['能打开登录页', '故意失败', '被跳过'])
        self.assertEqual([row['status'] for row in rows], ['passed', 'failed', 'skipped'])
        # 3621ms：RF 的时间戳是 `20261119 16:33:58.870`，切错一位这里就是 0。
        self.assertEqual(rows[0]['duration_ms'], 3621)
        self.assertIn('这两个值不一样', rows[1]['error'])
        self.assertEqual(rows[0]['screenshots'], ['rf-0/smoke_login_page.png'])

    def test_missing_output_returns_empty(self):
        self.assertEqual(runner._rf_collect(Path(_TMP, 'nope'), Path(_TMP, 'art')), [])


class JsonBlobTest(unittest.TestCase):
    def test_skips_dotenvx_preamble(self):
        text = ('◇ injected env (2) from .env // tip: ⌘ multiple files { path: [".env.local"] }\n'
                '◇ more { braces }\n'
                '{"stats": {"expected": 1}, "suites": []}\n')
        data = runner._json_blob(text)
        self.assertEqual(data['stats']['expected'], 1)

    def test_none_when_no_json(self):
        self.assertIsNone(runner._json_blob('boom: config error'))


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
            db.drop_all()
            db.create_all()

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


if __name__ == '__main__':
    unittest.main()
