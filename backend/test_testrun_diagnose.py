# -*- coding: utf-8 -*-
"""执行测试的 AI 初步诊断：材料组装、结果整形、路由与「只读」保证。

重点不是「模型答得好不好」（那要靠真跑），而是三件会悄悄坏掉的事：
1. 诊断包里的材料对不对（失败链取错就会把收尾失败当主错误）；
2. 模型返回的脏数据不能把界面搞崩（置信度返回 0.94、缺字段、多给字段）；
3. 诊断全程不碰脚本文件。
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TMP = tempfile.mkdtemp(prefix='diag-')
os.environ['DATABASE_URL'] = 'sqlite:///' + os.path.join(_TMP, 'test.db').replace(chr(92), chr(47))

from app import create_app  # noqa: E402
from app.models import db  # noqa: E402
from app.models.test_run import TestRun  # noqa: E402
from app.services.recording import diagnose  # noqa: E402

SCRIPT = """# @autocase 录制自 <https://demo/#/>
*** Settings ***
Library    SeleniumLibrary    timeout=10
Suite Teardown    Close Browser

*** Test Cases ***
终端资产校验
    Go To    https://demo/#/
    登录
    Wait Until Element Is Visible    xpath=//*[@id='broken']    10s
    Click Element    xpath=//*[@id='broken']

*** Keywords ***
登录
    Input Text    xpath=//*[@id='user']    admin

收尾检查
    Wait Until Page Contains    test1@qq.com
"""

OUTPUT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<robot generator="Robot 3.1.2 (Python 3.9.13 on win32)">
  <suite id="s1" name="终端" source="tests/a.robot">
    <test id="s1-t1" name="终端资产校验">
      <kw name="Go To" library="SeleniumLibrary">
        <msg level="INFO">Opening url 'https://demo/#'</msg>
        <status status="PASS"/>
      </kw>
      <kw name="登录">
        <kw name="Input Text" library="SeleniumLibrary">
          <msg level="INFO">Typing text 'admin' into text field 'xpath=//*[@id='user']'.</msg>
          <status status="PASS"/>
        </kw>
        <status status="PASS"/>
      </kw>
      <kw name="Wait Until Element Is Visible" library="SeleniumLibrary">
        <msg level="INFO">&lt;/td&gt;&lt;/tr&gt;&lt;a href="s.png"&gt;</msg>
        <msg level="FAIL">Element with locator 'xpath=//*[@id='broken']' not found.</msg>
        <status status="FAIL">Element with locator 'xpath=//*[@id='broken']' not found.</status>
      </kw>
      <kw name="收尾检查">
        <kw name="Wait Until Page Contains" library="SeleniumLibrary">
          <msg level="FAIL">Text 'test1@qq.com' did not appear in 10 seconds.</msg>
          <status status="FAIL">Text 'test1@qq.com' did not appear in 10 seconds.</status>
        </kw>
        <status status="FAIL">Text 'test1@qq.com' did not appear in 10 seconds.</status>
      </kw>
      <status status="FAIL">Element with locator 'xpath=//*[@id='broken']' not found.</status>
    </test>
  </suite>
</robot>
"""


def _workspace():
    root = Path(tempfile.mkdtemp(prefix='diag-ws-')) / 'ws'
    (root / 'tests').mkdir(parents=True)
    (root / 'tests' / 'a.robot').write_text(SCRIPT, encoding='utf-8')
    return root


class FakeRun(object):
    """只提供诊断用得上的字段，避免为了造一条 TestRun 去碰真实库。"""

    def __init__(self, workspace, artifacts_dir=None, framework='robot-selenium', env=None):
        self.workspace = str(workspace)
        self.artifacts_dir = str(artifacts_dir) if artifacts_dir else ''
        self.framework = framework
        self._env = env or {}

    def env(self):
        return self._env


class TargetTest(unittest.TestCase):
    ROWS = [
        {'status': 'passed'},
        {'status': 'failed'},
        {'status': 'skipped'},
        {'status': 'timedOut'},
        {'status': 'error'},
        {'status': 'interrupted'},
        {'status': 'failed'},
    ]

    def test_auto_takes_only_failed_and_caps(self):
        picked = diagnose._targets(self.ROWS, None)
        self.assertEqual([1, 3, 4, 5, 6], picked)
        self.assertEqual(diagnose.MAX_CASES, len(picked))

    def test_manual_index_ignores_passed_rows(self):
        # 1 是失败，6 也是失败；2(passed) 和重复的 1 要被丢掉，顺序按传入的顺序
        self.assertEqual([6, 1], diagnose._targets(self.ROWS, [6, 2, 0, 1, 1]))

    def test_manual_index_tolerates_junk(self):
        self.assertEqual([1], diagnose._targets(self.ROWS, ['1', None, 'x', 99]))


class NormalizeTest(unittest.TestCase):
    def test_keeps_full_reply(self):
        item = diagnose._normalize({
            'category': '定位失效',
            'verdict': 'script',
            'reason': '终端管理定位不存在',
            'detail': '日志里报元素找不到',
            'evidence': ['Element with locator ... not found.'],
            'fixes': [{'label': '修正定位', 'where': '终端管理', 'before': 'a', 'after': '', 'advice': '需要重录'}],
            'confidence': 'high',
        })
        self.assertEqual('定位失效', item['category'])
        self.assertEqual('script', item['verdict'])
        self.assertEqual('high', item['confidence'])
        self.assertEqual(1, len(item['fixes']))
        self.assertEqual('需要重录', item['fixes'][0]['advice'])

    def test_unknown_enums_degrade(self):
        item = diagnose._normalize({'category': '胡说', 'verdict': 'maybe', 'confidence': '?'})
        self.assertEqual('其它', item['category'])
        self.assertEqual('unknown', item['verdict'])
        self.assertEqual('', item['confidence'])

    def test_numeric_confidence_becomes_label(self):
        # 实测模型真的会返回 0.94，直接塞进界面就是「置信度 0.94」
        self.assertEqual('high', diagnose._normalize({'confidence': 0.94})['confidence'])
        self.assertEqual('medium', diagnose._normalize({'confidence': 0.6})['confidence'])
        self.assertEqual('low', diagnose._normalize({'confidence': 0.2})['confidence'])

    def test_chinese_confidence(self):
        self.assertEqual('high', diagnose._normalize({'confidence': '高'})['confidence'])
        self.assertEqual('low', diagnose._normalize({'confidence': '较低'})['confidence'])

    def test_fixes_without_content_are_dropped(self):
        item = diagnose._normalize({'fixes': [{}, 'x', None, {'label': '只有标题'}]})
        self.assertEqual(1, len(item['fixes']))

    def test_long_text_is_clamped(self):
        item = diagnose._normalize({'reason': 'x' * 500, 'detail': 'y' * 3000,
                                    'evidence': ['e%d' % i for i in range(20)]})
        self.assertEqual(200, len(item['reason']))
        self.assertEqual(1500, len(item['detail']))
        self.assertEqual(8, len(item['evidence']))

    def test_non_dict_reply(self):
        item = diagnose._normalize(['不是对象'])
        self.assertEqual('', item['reason'])
        self.assertEqual([], item['fixes'])


class ExcerptTest(unittest.TestCase):
    def test_rf_excerpt_picks_case_and_used_keywords(self):
        chain = [['终端资产校验', 'Wait Until Element Is Visible'],
                 ['终端资产校验', '收尾检查', 'Wait Until Page Contains']]
        text = diagnose._rf_excerpt(SCRIPT, '终端资产校验', chain)
        self.assertIn('用例 终端资产校验', text)
        self.assertIn('关键字 收尾检查', text)
        # 正文里调用的用户关键字也要给：模型得看得见「登录」做了什么
        self.assertIn('关键字 登录', text)
        self.assertNotIn('*** Settings ***', text)

    def test_rf_excerpt_falls_back_when_title_missing(self):
        text = diagnose._rf_excerpt(SCRIPT, '另一个用例', [])
        self.assertIn('*** Test Cases ***', text)

    def test_rf_excerpt_handles_empty_script(self):
        self.assertEqual('', diagnose._rf_excerpt('', 'x', []))

    def test_pw_excerpt_marks_failing_line(self):
        code = '\n'.join('line %d' % i for i in range(1, 201))
        text = diagnose._pw_excerpt(code, 'a.spec.ts:100:5')
        # 行号必须标出来：模型最容易犯的错是算错偏移，说「第 N 行」却指到别处
        self.assertIn('>> 100 | line 100', text)
        self.assertIn('  99 | line 99', text)
        # 只给故障附近：spec 动辄几百行，全塞进去会淹掉真正有用的上下文
        self.assertIn('前 74 行省略', text)
        self.assertNotIn('line 1\n', text)

    def test_pw_excerpt_without_location_gives_head(self):
        code = '\n'.join('line %d' % i for i in range(1, 200))
        text = diagnose._pw_excerpt(code, '')
        self.assertIn('已截断', text)


class PacketTest(unittest.TestCase):
    def setUp(self):
        self.ws = _workspace()
        self.artifacts = Path(tempfile.mkdtemp(prefix='diag-art-'))
        (self.artifacts / 'rf-0').mkdir()
        (self.artifacts / 'rf-0' / 'output.xml').write_text(OUTPUT_XML, encoding='utf-8')
        self.row = {
            'title': '终端资产校验', 'file': 'tests/a.robot', 'status': 'failed',
            'duration_ms': 3200, 'error': "Element with locator 'xpath=//*[@id='broken']' not found.",
            'where': '', 'env_error': False, 'steps': [],
        }

    def test_rf_packet_uses_first_failure_as_main(self):
        packet = diagnose._packet(FakeRun(self.ws, self.artifacts), 0, self.row, self.ws)
        # 第一条才是主错误；最后一条是收尾的连带失败
        self.assertEqual('终端资产校验 > Wait Until Element Is Visible', packet['failed_at'])
        self.assertIn('收尾检查', ' '.join(packet['failed_chain']))
        self.assertEqual('robot-selenium', packet['framework'])

    def test_rf_packet_log_skips_screenshot_html(self):
        packet = diagnose._packet(FakeRun(self.ws, self.artifacts), 0, self.row, self.ws)
        joined = '\n'.join(packet['log_tail'])
        self.assertIn("Opening url", joined)
        self.assertNotIn('</td>', joined)

    def test_rf_packet_survives_missing_artifacts(self):
        packet = diagnose._packet(FakeRun(self.ws, ''), 0, self.row, self.ws)
        self.assertEqual([], packet['log_tail'])
        self.assertEqual('', packet['failed_at'])
        self.assertIn('Click Element', packet['script'])

    def test_refuses_script_outside_workspace(self):
        row = dict(self.row, file='../../etc/passwd')
        self.assertEqual('', diagnose._read_script(self.ws, row['file']))

    def test_playwright_packet_uses_steps_as_log(self):
        row = {'title': 't', 'file': 'tests/a.spec.ts', 'status': 'failed',
               'error': 'locator.click: Timeout 30000ms', 'where': 'tests/a.spec.ts:3:5',
               'env_error': False,
               'steps': [{'title': 'page.goto', 'status': 'passed'},
                         {'title': 'click 提交', 'status': 'failed', 'error': 'element not visible'}]}
        (self.ws / 'tests' / 'a.spec.ts').write_text(
            "import { test } from '@playwright/test'\ntest('t', async ({ page }) => {\n"
            "  await page.click('#go')\n})\n", encoding='utf-8')
        packet = diagnose._packet(FakeRun(self.ws, '', framework='playwright-ts'), 0, row, self.ws)
        self.assertEqual('tests/a.spec.ts:3:5', packet['failed_at'])
        self.assertTrue(any('element not visible' in line for line in packet['log_tail']))
        self.assertIn('#go', packet['script'])


class DiagnoseOneTest(unittest.TestCase):
    def setUp(self):
        self.ws = _workspace()
        self.run = FakeRun(self.ws)
        self.row = {'title': '终端资产校验', 'file': 'tests/a.robot', 'status': 'failed',
                    'error': 'boom', 'where': '', 'env_error': False, 'steps': []}

    def test_env_error_is_skipped_without_calling_model(self):
        with mock.patch.object(diagnose, '_ask', side_effect=AssertionError('不该调模型')):
            item = diagnose._diagnose_one(self.run, 0, dict(self.row, env_error=True), self.ws, 'm')
        self.assertEqual('skipped', item['status'])
        self.assertEqual('环境不可达', item['category'])

    def test_model_failure_is_reported_per_case(self):
        with mock.patch.object(diagnose, '_ask', side_effect=RuntimeError('socket closed')):
            item = diagnose._diagnose_one(self.run, 2, self.row, self.ws, 'm')
        self.assertEqual('failed', item['status'])
        self.assertIn('socket closed', item['reason'])
        self.assertEqual(2, item['index'])

    def test_success_item_carries_index_and_model(self):
        reply = {'category': '定位失效', 'verdict': 'script', 'reason': 'r',
                 'detail': 'd', 'evidence': [], 'fixes': [], 'confidence': 'high'}
        before = (self.ws / 'tests' / 'a.robot').read_text(encoding='utf-8')
        with mock.patch.object(diagnose, '_ask', return_value=reply):
            item = diagnose._diagnose_one(self.run, 0, self.row, self.ws, 'm/x')
        self.assertEqual('done', item['status'])
        self.assertEqual(0, item['index'])
        self.assertEqual('m/x', item['model'])
        # 只读保证：诊断不该改动脚本一个字
        self.assertEqual(before, (self.ws / 'tests' / 'a.robot').read_text(encoding='utf-8'))


class SnapshotTest(unittest.TestCase):
    def test_idle_when_never_diagnosed(self):
        self.assertEqual({'state': 'idle', 'items': []}, TestRun().diagnoses())

    def test_items_are_ordered_by_index(self):
        run = TestRun()
        run.diagnoses_json = json.dumps({
            'state': 'done', 'items': {'2': {'index': 2}, '0': {'index': 0}}})
        data = run.diagnoses()
        self.assertEqual([0, 2], [item['index'] for item in data['items']])

    def test_broken_json_does_not_raise(self):
        run = TestRun()
        run.diagnoses_json = 'not json'
        self.assertEqual('idle', run.diagnoses()['state'])


class RouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()

    def setUp(self):
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        self.ctx.pop()

    def _run(self, status='failed', rows=None):
        run = TestRun(workspace=_TMP, framework='robot-selenium', status=status,
                      results_json=json.dumps({'env': {}, 'rows': rows or []}))
        db.session.add(run)
        db.session.commit()
        return run.id

    def test_unknown_run_is_404(self):
        r = self.client.post('/api/automation/testrun/runs/nope/diagnose', json={})
        self.assertEqual(404, r.status_code)

    def test_running_run_is_rejected(self):
        run_id = self._run(status='running', rows=[{'status': 'failed', 'title': 'x'}])
        r = self.client.post('/api/automation/testrun/runs/%s/diagnose' % run_id, json={})
        self.assertEqual(400, r.status_code)
        self.assertIn('还没结束', r.get_json()['error'])

    def test_all_passed_run_is_rejected(self):
        run_id = self._run(rows=[{'status': 'passed', 'title': 'x'}])
        r = self.client.post('/api/automation/testrun/runs/%s/diagnose' % run_id, json={})
        self.assertEqual(400, r.status_code)
        self.assertIn('没有需要诊断', r.get_json()['error'])

    def test_indexes_must_be_a_list(self):
        run_id = self._run(rows=[{'status': 'failed', 'title': 'x'}])
        r = self.client.post('/api/automation/testrun/runs/%s/diagnose' % run_id,
                             json={'indexes': 1})
        self.assertEqual(400, r.status_code)

    def test_start_is_called_with_given_indexes(self):
        run_id = self._run(rows=[{'status': 'failed', 'title': 'x'},
                                 {'status': 'failed', 'title': 'y'}])
        with mock.patch.object(diagnose, 'start', return_value={'state': 'running', 'items': []}) as fake:
            r = self.client.post('/api/automation/testrun/runs/%s/diagnose' % run_id,
                                 json={'indexes': [1]})
        self.assertEqual(202, r.status_code)
        self.assertEqual([1], fake.call_args[0][2])

    def test_run_detail_carries_diagnoses(self):
        run_id = self._run(rows=[{'status': 'failed', 'title': 'x'}])
        r = self.client.get('/api/automation/testrun/runs/%s' % run_id)
        self.assertEqual(200, r.status_code)
        self.assertEqual('idle', r.get_json()['diagnoses']['state'])


class NoAutoTriggerTest(unittest.TestCase):
    """跑完不自动诊断：一次运行十几条失败会白烧掉几十分钟和一批 token。

    用户要的是「先正常看失败，想深究再点一下」。这条测试守住这个默认值，
    免得以后谁又把它挂回 _execute 里。
    """

    def test_runner_does_not_import_diagnose_on_run(self):
        from app.services.recording import runner

        source = Path(runner.__file__).read_text(encoding='utf-8')
        self.assertNotIn('_auto_diagnose', source)
        self.assertNotIn('diagnose.start', source)


if __name__ == '__main__':
    unittest.main()
