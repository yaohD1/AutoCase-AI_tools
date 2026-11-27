"""表格行断言：意图校验 / 生成载荷 / 辅助库落盘 / 现场判定。

这一档解决的场景是「同一行里多列同时成立」（操作=导入终端资产、操作对象=CPE、
操作结果=成功），单元素断言表达不了：断整行只能断一列，断整页会让别处的「成功」
混进来，分几个单格断言又证明不了它们在同一个行里。
"""
import tempfile
import threading
import unittest
import http.server
from pathlib import Path

from app.services.recording import emit, intent, ir, locator_vars, table_bundle, table_probe
from app.services.recording.session import RecordingSession


def row_intent(**over):
    raw = {
        'type': 'row_match',
        'table': {
            'rows_selector': '.el-table .el-table__body-wrapper tbody>tr',
            'header_selector': '.el-table .el-table__header-wrapper th',
            'headers': ['操作', '操作对象', '操作对象类别', '操作结果'],
            'alias': '导入终端资产',
            'route': '/terminal/asset/log',
            'page_index': 0,
        },
        'columns': [
            {'header': '操作', 'index': 0, 'value': '导入终端资产'},
            {'header': '操作结果', 'index': 3, 'value': '成功'},
        ],
        'scope': 'any',
    }
    raw.update(over)
    return raw


class RowIntentTest(unittest.TestCase):
    def test_valid(self):
        checked = intent.validate(row_intent(), [])
        self.assertEqual('row_match', checked['type'])
        self.assertEqual('any', checked['scope'])
        self.assertEqual(['操作', '操作结果'], [c['header'] for c in checked['columns']])
        self.assertEqual([0, 3], [c['index'] for c in checked['columns']])
        self.assertEqual(['contains', 'contains'], [c['op'] for c in checked['columns']])
        # 单元素断言的字段保持 null，前端按类型分支即可。
        self.assertIsNone(checked['target'])
        self.assertIsNone(checked['value'])

    def test_wildcard_value_marks_glob(self):
        checked = intent.validate(row_intent(columns=[
            {'header': '操作对象', 'index': 1, 'value': 'RF4039_REF_*'},
        ]), [])
        self.assertEqual('glob', checked['columns'][0]['op'])

    def test_scope_and_timeout_kept(self):
        checked = intent.validate(row_intent(scope='first', timeout=45, reload=True,
                                             interval=5), [])
        self.assertEqual('first', checked['scope'])
        self.assertEqual(45, checked['timeout'])
        self.assertTrue(checked['reload'])

    def test_assert_when_no_timeout_wait_when_timeout(self):
        immediate = ir.step_type_for_intent('row_match', intent.validate(row_intent(), []))
        waiting = ir.step_type_for_intent('row_match', intent.validate(
            row_intent(timeout=30), []))
        self.assertEqual('assert', immediate)
        self.assertEqual('wait', waiting)

    def test_rejects_missing_table(self):
        with self.assertRaises(intent.IntentError):
            intent.validate({'type': 'row_match', 'columns': [{'header': 'a', 'index': 0,
                                                               'value': 'b'}]}, [])

    def test_rejects_missing_rows(self):
        raw = row_intent()
        raw['table']['rows_selector'] = '   '
        with self.assertRaises(intent.IntentError) as ctx:
            intent.validate(raw, [])
        self.assertIn('行定位', str(ctx.exception))

    def test_rejects_empty_columns(self):
        with self.assertRaises(intent.IntentError) as ctx:
            intent.validate(row_intent(columns=[]), [])
        self.assertIn('至少填一列', str(ctx.exception))

    def test_rejects_empty_value(self):
        with self.assertRaises(intent.IntentError) as ctx:
            intent.validate(row_intent(columns=[{'header': '操作', 'index': 0, 'value': ' '}]), [])
        self.assertIn('期望值', str(ctx.exception))

    def test_rejects_bad_scope(self):
        with self.assertRaises(intent.IntentError) as ctx:
            intent.validate(row_intent(scope='last'), [])
        self.assertIn('行范围', str(ctx.exception))

    def test_custom_scope_normalizes_row_spec(self):
        # 面板上用户可能写「第 2 行 至 第 3 行」或「2~3」；落库统一成 2-3。
        for raw in ('2-3', '2~3', '第2行至第3行', ' 2 - 3 ', '第 2 行 到 第 3 行'):
            checked = intent.validate(row_intent(scope='custom', scope_rows=raw), [])
            self.assertEqual('custom', checked['scope'], raw)
            self.assertEqual('2-3', checked['scope_rows'], raw)
        single = intent.validate(row_intent(scope='custom', scope_rows='第2行'), [])
        self.assertEqual('2', single['scope_rows'])

    def test_custom_scope_requires_row_spec(self):
        for bad in ('', None, '   ', '最后一行', '倒数第二行', '0', '0-3', '5-2', 'abc'):
            with self.assertRaises(intent.IntentError, msg=repr(bad)):
                intent.validate(row_intent(scope='custom', scope_rows=bad), [])

    def test_non_custom_scope_drops_row_spec(self):
        # any / first 时残留的行号必须丢掉，否则生成端可能把 2-5 当秒数塞进调用。
        checked = intent.validate(row_intent(scope='any', scope_rows='2-5'), [])
        self.assertIsNone(checked['scope_rows'])

    def test_rejects_too_many_columns(self):
        cols = [{'header': 'c%d' % i, 'index': i, 'value': 'v'} for i in range(9)]
        with self.assertRaises(intent.IntentError):
            intent.validate(row_intent(columns=cols), [])

    def test_not_offered_to_natural_language_parser(self):
        # 模型看不到页面，让它产出 row_match 只会编表头名。它只由面板直接给。
        self.assertIn('row_match', ir.INTENT_TYPES)
        self.assertNotIn('row_match', ir.CONDITION_INTENT_TYPES)


class RowPayloadTest(unittest.TestCase):
    def _step(self, **over):
        checked = intent.validate(row_intent(**over), [])
        return {'no': 3, 'type': 'wait' if checked.get('timeout') else 'assert',
                'intent': checked}

    def test_rf_call_uses_variable_and_tuples(self):
        payload = emit._row_assert_payload(self._step(), emit.RF_FRAMEWORK,
                                           '${终端资产log表格行}')
        self.assertEqual('${终端资产log表格行}', payload['rows'])
        self.assertEqual([['操作', 0, '导入终端资产'], ['操作结果', 3, '成功']],
                         payload['conditions'])
        self.assertEqual(
            "Assert Table Row    ${终端资产log表格行}    "
            "[('操作', 0, '导入终端资产'), ('操作结果', 3, '成功')]    scope=any",
            payload['call'])
        self.assertNotIn('wait_call', payload)

    def test_rf_wait_call_has_timeout(self):
        payload = emit._row_assert_payload(self._step(timeout=30), emit.RF_FRAMEWORK,
                                           '${表格行}')
        self.assertTrue(payload['wait_call'].endswith('scope=any    timeout=30'))
        # 立即判定那条不留 timeout，刷新轮询的 Reload And Check 里要用它。
        self.assertFalse(payload['call'].endswith('timeout=30'))

    def test_rf_falls_back_to_inline_css(self):
        payload = emit._row_assert_payload(self._step(), emit.RF_FRAMEWORK)
        self.assertTrue(payload['rows'].startswith('css=.el-table'))

    def test_pw_call(self):
        payload = emit._row_assert_payload(self._step(timeout=30), emit.PW_FRAMEWORK)
        self.assertIn('await assertTableRow(page, ".el-table .el-table__body-wrapper tbody>tr"',
                      payload['call'])
        self.assertIn('"操作", 0, "导入终端资产"', payload['call'])
        self.assertTrue(payload['call'].endswith(", 'any');"))
        self.assertTrue(payload['wait_call'].endswith(", 'any', 30);"))
        self.assertIn('tableRowMatches', payload['matches_call'])

    def test_rf_custom_scope_carries_row_range(self):
        payload = emit._row_assert_payload(
            self._step(scope='custom', scope_rows='第 2 行 至 第 3 行'), emit.RF_FRAMEWORK,
            '${表格行}')
        self.assertEqual('2-3', payload['scope_rows'])
        self.assertTrue(payload['call'].endswith('scope=custom    row_range=2-3'), payload['call'])

    def test_pw_custom_scope_pads_timeout_slot(self):
        # assertTableRow 第 5 位是 timeout：不填 0 的话 '2-3' 会落到 timeout 上。
        payload = emit._row_assert_payload(
            self._step(scope='custom', scope_rows='2-3'), emit.PW_FRAMEWORK)
        self.assertTrue(payload['call'].endswith("'custom', 0, \"2-3\");"), payload['call'])
        self.assertTrue(payload['matches_call'].endswith("'custom', \"2-3\")"),
                        payload['matches_call'])
        waited = emit._row_assert_payload(
            self._step(scope='custom', scope_rows='2-3', timeout=30), emit.PW_FRAMEWORK)
        self.assertTrue(waited['wait_call'].endswith("'custom', 30, \"2-3\");"),
                        waited['wait_call'])

    def test_rf_any_scope_has_no_row_range(self):
        payload = emit._row_assert_payload(self._step(), emit.RF_FRAMEWORK, '${x}')
        self.assertNotIn('row_range', payload['call'])

    def test_quote_and_double_space_are_safe(self):
        step = self._step(columns=[{'header': '操作', 'index': 0, 'value': "O'Brien  导入"}])
        payload = emit._row_assert_payload(step, emit.RF_FRAMEWORK, '${x}')
        # 单引号必须能过 Python 字面量；连续空格会被 Robot 当成参数分隔符。
        self.assertEqual("O'Brien 导入", payload['conditions'][0][2])
        self.assertIn('Assert Table Row', payload['call'])
        literal = payload['call'].split('    ')[2]
        import ast
        self.assertEqual([("操作", 0, "O'Brien 导入")], ast.literal_eval(literal))

    def test_plan_step_hides_selectors(self):
        described = emit._plan_step(self._step())
        self.assertEqual('row_match', described['intent']['type'])
        self.assertEqual([{'header': '操作', 'value': '导入终端资产'},
                          {'header': '操作结果', 'value': '成功'}],
                         described['intent']['columns'])
        self.assertNotIn('rows_selector', str(described))


class TableBundleTest(unittest.TestCase):
    def test_python_library_is_valid_and_embeds_same_js(self):
        body = table_bundle.TABLE_PY
        self.assertNotIn('__MATCH_JS__', body)
        compile(body, 'TableLibrary.py', 'exec')
        self.assertIn('class TableLibrary', body)
        self.assertIn('def assert_table_row', body)
        self.assertIn('headerSelector', body)

    def test_selenium_call_wraps_arrow_function(self):
        # Selenium 把脚本文本当函数体执行：箭头函数原样传进去只会求值、不调用，
        # 返回值是 None（不报错）。必须包成 return (...)()，这是真跑才发现的坑。
        self.assertIn("_MATCH_CALL = 'return (' + MATCH_JS + ')(arguments[0]);'",
                      table_bundle.TABLE_PY)
        self.assertIn('execute_script(_MATCH_CALL', table_bundle.TABLE_PY)

    def test_ts_library_escapes_js_as_string(self):
        body = table_bundle.TABLE_TS
        self.assertNotIn('__MATCH_JS__', body)
        self.assertIn('export async function assertTableRow', body)
        self.assertIn('export async function tableRowMatches', body)
        # JS 以 JSON 字符串形式嵌入：反斜杠被转义，不会把模板字符串撕开。
        self.assertNotIn('const MATCH_JS = `', body)
        self.assertIn('const MATCH_JS = "', body)


class EnsureTableUtilsTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='ac-table-')

    def test_robot_writes_shared_library(self):
        rel = emit._ensure_table_utils(self.root, '测试用例层/终端资产/a.robot',
                                       [{'type': 'assert', 'intent': {'type': 'row_match'}}],
                                       emit.RF_FRAMEWORK)
        self.assertEqual(rel, '../../.autocase/libraries/TableLibrary.py')
        body = (Path(self.root) / '.autocase/libraries/TableLibrary.py').read_text(encoding='utf-8')
        compile(body, rel, 'exec')
        self.assertIn('assert_table_row', body)

    def test_playwright_writes_utils(self):
        rel = emit._ensure_table_utils(self.root, 'tests/log.spec.ts',
                                       [{'type': 'wait', 'intent': {'type': 'row_match'}}],
                                       emit.PW_FRAMEWORK)
        self.assertEqual(rel, 'tests/utils/table.ts')
        self.assertIn('assertTableRow',
                      (Path(self.root) / rel).read_text(encoding='utf-8'))

    def test_skipped_without_row_match(self):
        for framework in (emit.PW_FRAMEWORK, emit.RF_FRAMEWORK):
            self.assertIsNone(emit._ensure_table_utils(
                self.root, 'tests/a.spec.ts',
                [{'type': 'assert', 'intent': {'type': 'text_contains'}}], framework))

    def test_rewrites_stale_library(self):
        stale = Path(self.root) / '.autocase/libraries/TableLibrary.py'
        stale.parent.mkdir(parents=True)
        stale.write_text('# 旧版本\n', encoding='utf-8')
        notes = []
        emit._ensure_table_utils(self.root, 'tests/a.robot',
                                 [{'type': 'assert', 'intent': {'type': 'row_match'}}],
                                 emit.RF_FRAMEWORK, notes=notes)
        self.assertEqual('updated', notes[0]['state'])
        self.assertIn('assert_table_row', stale.read_text(encoding='utf-8'))

    def test_settings_gets_library_line(self):
        gen = tempfile.mkdtemp(prefix='ac-table-gen-')
        target = '测试用例层/终端资产/用例.robot'
        path = Path(gen) / target
        path.parent.mkdir(parents=True)
        path.write_text('*** Test Cases ***\n录制流程\n    Go To    https://a/#/\n',
                        encoding='utf-8')
        emit._normalize_rf_script(
            gen, target, [], locator_libs=[],
            table_lib='../../.autocase/libraries/TableLibrary.py')
        body = path.read_text(encoding='utf-8')
        self.assertIn('Library    ${CURDIR}/../../.autocase/libraries/TableLibrary.py', body)


class FakeFrame:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.arg = None

    def evaluate(self, script, arg=None):
        self.arg = arg
        if self.error:
            raise RuntimeError(self.error)
        return self.result


class TableCheckTest(unittest.TestCase):
    def _intent(self, **over):
        return intent.validate(row_intent(**over), [])

    def test_match_reports_row(self):
        frame = FakeFrame({'ok': True, 'index': 2, 'row': ['导入终端资产', 'CPE', '终端资产', '成功'],
                           'total': 10, 'headers': ['操作', '操作对象', '操作对象类别', '操作结果']})
        result = table_probe.check(frame, self._intent())
        self.assertTrue(result['ok'])
        self.assertEqual('row_match', result['kind'])
        self.assertEqual(3, result['row_index'])
        self.assertIn('导入终端资产', result['actual'])
        self.assertIn('第 3 行', result['detail'])
        # 传给页面的是「表头 + 列序号 + 期望值」，运行时按表头名找列。
        self.assertEqual('any', frame.arg['scope'])
        self.assertEqual(['操作', '操作结果'], [c['header'] for c in frame.arg['columns']])
        self.assertEqual([0, 3], [c['index'] for c in frame.arg['columns']])

    def test_miss_lists_actual_rows(self):
        frame = FakeFrame({'ok': False, 'total': 10,
                           'headers': ['操作', '操作对象', '操作对象类别', '操作结果'],
                           'sample': [['删除', 'RF4039_REF_202611', '终端初始化配置', '成功']]})
        result = table_probe.check(frame, self._intent())
        self.assertFalse(result['ok'])
        self.assertIn('没有同时满足', result['detail'])
        self.assertIn('RF4039_REF_202611', result['detail'])
        self.assertEqual(10, result['row_total'])

    def test_no_rows_hints_at_stale_locator(self):
        frame = FakeFrame({'ok': False, 'total': 0, 'sample': []})
        result = table_probe.check(frame, self._intent())
        self.assertFalse(result['ok'])
        self.assertIn('重新拾取', result['detail'])

    def test_missing_rows_selector(self):
        raw = row_intent()
        raw['table']['rows_selector'] = ''
        with self.assertRaises(intent.IntentError):
            intent.validate(raw, [])

    def test_js_error_is_readable(self):
        frame = FakeFrame(error='Execution context was destroyed')
        with self.assertRaises(table_probe.TableProbeError) as ctx:
            table_probe.check(frame, self._intent())
        self.assertIn('查表格失败', str(ctx.exception))

    def test_custom_scope_forwarded_and_explained(self):
        frame = FakeFrame({'ok': False, 'total': 10, 'from': 2, 'to': 3,
                           'headers': ['操作', '操作结果'],
                           'sample': [{'at': 2, 'cells': ['删除', '成功']}]})
        result = table_probe.check(frame, self._intent(scope='custom', scope_rows='2'))
        self.assertEqual('custom', frame.arg['scope'])
        self.assertEqual('2', frame.arg['scopeRows'])
        self.assertIn('行范围：第 2 行', result['detail'])
        # 样本行带绝对行号，不会把「第 2 行」说成「1)」
        self.assertIn('第 2 行 删除', result['detail'])

    def test_custom_scope_beyond_table(self):
        frame = FakeFrame({'ok': False, 'total': 2, 'from': 3, 'to': 2, 'sample': []})
        result = table_probe.check(frame, self._intent(scope='custom', scope_rows='9'))
        self.assertFalse(result['ok'])
        self.assertIn('只有 2 行', result['detail'])

    def test_describe_passes_snapshot_css(self):
        frame = FakeFrame({'ok': True, 'rows': 'tr', 'headers': [{'name': '操作', 'index': 0}],
                           'row_count': 3, 'has_header': True})
        info = table_probe.describe(frame, 'body>div>table>tbody>tr:nth-of-type(1)>td:nth-of-type(1)')
        # 对外字段名和 intent.table 对齐：rows → rows_selector。
        self.assertEqual('tr', info['rows_selector'])
        self.assertNotIn('rows', info)
        self.assertEqual('body>div>table>tbody>tr:nth-of-type(1)>td:nth-of-type(1)', frame.arg)

    def test_describe_reports_page_error(self):
        frame = FakeFrame({'ok': False, 'error': '这一格不在表格行里'})
        with self.assertRaises(table_probe.TableProbeError) as ctx:
            table_probe.describe(frame, 'div')
        self.assertIn('不在表格行里', str(ctx.exception))


class TableLocatorTest(unittest.TestCase):
    def test_name_from_route(self):
        self.assertEqual('log表格行', locator_vars.table_row_name('/terminal/asset/log'))
        self.assertEqual('detail表格行', locator_vars.table_row_name('/terminal/asset/detail?id=3'))
        self.assertEqual('表格行', locator_vars.table_row_name('/:id'))
        self.assertEqual('表格行', locator_vars.table_row_name(''))

    def _build(self, root, target_path):
        def target(alias):
            return {'alias': alias, 'snap': {'tag': 'input', 'name': alias},
                    'candidates': [], 'primary': None, 'status': 'unique'}
        steps = [ir.make_step('fill', 1, target=target('用户名'), value='admin')]
        return locator_vars.build(
            steps, {}, target_path, root, lambda t: 'xpath=//input[@name="%s"]' % t['alias'],
            extras=[{'key': '1', 'name': locator_vars.table_row_name('/terminal/asset/log'),
                     'locator': 'css=.el-table tbody>tr', 'route': '/terminal/asset/log'}])

    def test_extra_lands_in_resource_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            built = self._build(tmp, '测试用例层/终端资产/导入.robot')
            self.assertEqual('${终端资产log表格行}', built['extra_vars']['1'])
            body = Path(tmp, built['files'][0]['path']).read_text(encoding='utf-8')
            self.assertIn('${终端资产log表格行}    css=.el-table tbody>tr', body)
            # 行定位不属于任何一步，不能把步骤变量覆盖成表格行。
            self.assertEqual('${终端资产用户名输入框}', built['vars']['1'])

    def test_second_build_reuses_same_variable(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = self._build(tmp, '测试用例层/终端资产/导入.robot')
            second = self._build(tmp, '测试用例层/终端资产/导入.robot')
            self.assertEqual(first['extra_vars'], second['extra_vars'])
            self.assertEqual('reused', second['files'][0]['state'])
            body = Path(tmp, second['files'][0]['path']).read_text(encoding='utf-8')
            self.assertEqual(1, body.count('${终端资产log表格行}'))


TABLE_PAGE = (
    '<!doctype html><html><head><meta charset="utf-8"></head><body>'
    '<div class="el-table" style="width:760px">'
    '<div class="el-table__header-wrapper"><table class="el-table__header"><thead><tr>'
    '<th style="width:160px"><div class="cell">操作</div></th>'
    '<th style="width:240px"><div class="cell">操作对象</div></th>'
    '<th style="width:160px"><div class="cell">操作对象类别</div></th>'
    '<th style="width:120px"><div class="cell">操作结果</div></th>'
    '</tr></thead></table></div>'
    '<div class="el-table__body-wrapper"><table class="el-table__body"><tbody>'
    '<tr class="el-table__row">'
    '<td><div class="cell">删除</div></td>'
    '<td><div class="cell">RF4039_REF_20261127_093000</div></td>'
    '<td><div class="cell">终端初始化配置</div></td>'
    '<td><div class="cell">成功</div></td></tr>'
    '<tr class="el-table__row">'
    '<td><div class="cell">导入终端资产</div></td>'
    '<td><div class="cell">CPE</div></td>'
    '<td><div class="cell">终端资产</div></td>'
    '<td><div class="cell">成功</div></td></tr>'
    '</tbody></table></div></div>'
    '</body></html>'
)


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = TABLE_PAGE.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _idle(session, page):
    # Playwright 的同步对象不能跨线程用：页面交给测试，后续点击排进录制线程。
    session._test_page = page


def _in_recording_thread(session, fn, timeout=15):
    done = threading.Event()
    box = {}

    def task():
        try:
            box['value'] = fn()
        except Exception as exc:  # noqa: BLE001 - 交回测试线程再抛
            box['error'] = exc
        finally:
            done.set()

    session._inbox.put(task)
    if not done.wait(timeout):
        raise AssertionError('录制线程没有执行排入的操作')
    if 'error' in box:
        raise box['error']
    return box.get('value')


def _wait_for(predicate, timeout=10):
    import time
    waited = 0.0
    while waited < timeout:
        if predicate():
            return True
        time.sleep(0.05)
        waited += 0.05
    return predicate()


class RowMatchOnLivePageTest(unittest.TestCase):
    """真实浏览器：拾取表格 → 读表头 → 落成步骤 → 现场判定。

    纯函数单测只能证明「数据形状对」，这一段证明的是整条链：css 拾取、表头读取、
    行/列位置、首行与任意行的差别，以及意图能真的插进流程里。
    """

    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = 'http://127.0.0.1:%d/index.html' % cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        RecordingSession._current = None

    def tearDown(self):
        RecordingSession.dispose_current()

    def _pick_object_cell(self, session):
        session.set_pick(True)
        try:
            _in_recording_thread(
                session,
                lambda: session._test_page.locator(
                    '.el-table__body-wrapper tbody tr').first.locator('td').nth(1).click())
            self.assertTrue(_wait_for(lambda: len(session.aliases()) == 1), session.aliases())
        finally:
            session.set_pick(False)
        return session.aliases()[0]

    def test_describe_and_verify_on_live_table(self):
        session = RecordingSession.start_new(self.base, headless=True, ask_daemon=False,
                                             driver=_idle)
        alias = self._pick_object_cell(session)

        info = session.describe_table(alias)
        self.assertTrue(info['rows_selector'])
        self.assertEqual(['操作', '操作对象', '操作对象类别', '操作结果'],
                         [h['name'] for h in info['headers']])
        self.assertEqual(2, info['row_count'])
        self.assertTrue(info['has_header'])
        # 行定位必须选得中数据行，而不是表头或固定列
        self.assertIn('tbody>tr', info['rows_selector'])

        index = {h['name']: h['index'] for h in info['headers']}
        raw = {
            'type': 'row_match',
            'table': {
                'rows_selector': info['rows_selector'],
                'header_selector': info['header_selector'],
                'headers': [h['name'] for h in info['headers']],
                'alias': alias,
                'route': info.get('route') or '',
                'page_index': 0,
            },
            'columns': [
                {'header': '操作', 'index': index['操作'], 'value': '导入终端资产'},
                {'header': '操作结果', 'index': index['操作结果'], 'value': '成功'},
            ],
            'scope': 'first',
        }
        # 首行是「删除」，严格要求首行时不该成立——顺带证明 scope 真的生效
        first = session.verify_intent(intent.validate(raw, session.aliases()))
        self.assertFalse(first['ok'], first)
        self.assertIn('没有同时满足', first['detail'])
        self.assertEqual(2, first['row_total'])

        raw['scope'] = 'any'
        checked = intent.validate(raw, session.aliases())
        hit = session.verify_intent(checked)
        self.assertTrue(hit['ok'], hit)
        self.assertEqual(2, hit['row_index'])
        self.assertEqual(['导入终端资产', 'CPE', '终端资产', '成功'], hit['row'])

        # 值改错必须变红，否则「通过」没有意义
        raw['columns'][1]['value'] = '失败'
        miss = session.verify_intent(intent.validate(raw, session.aliases()))
        self.assertFalse(miss['ok'], miss)

        # 意图能落成步骤，且快照里带着表格与列条件（刷新后还能继续生成）
        session.add_intent_step(checked)
        step = session.snapshot()['steps'][-1]
        self.assertEqual('row_match', step['intent']['type'])
        self.assertEqual(2, len(step['intent']['columns']))
        self.assertTrue(step['intent']['table']['rows_selector'])

    def test_custom_row_scope_on_live_table(self):
        """自定义行范围要真的只看那几行：同一组条件在第 1 行不成立、第 2 行成立。"""
        session = RecordingSession.start_new(self.base, headless=True, ask_daemon=False,
                                             driver=_idle)
        alias = self._pick_object_cell(session)
        info = session.describe_table(alias)
        index = {h['name']: h['index'] for h in info['headers']}

        def raw(rows):
            return {
                'type': 'row_match',
                'table': {
                    'rows_selector': info['rows_selector'],
                    'header_selector': info['header_selector'],
                    'headers': [h['name'] for h in info['headers']],
                    'alias': alias,
                    'route': info.get('route') or '',
                    'page_index': 0,
                },
                # 只有第 2 行是「导入终端资产 / CPE」那一条。
                'columns': [
                    {'header': '操作', 'index': index['操作'], 'value': '导入终端资产'},
                    {'header': '操作对象', 'index': index['操作对象'], 'value': 'CPE'},
                ],
                'scope': 'custom',
                'scope_rows': rows,
            }

        hit = session.verify_intent(intent.validate(raw('第 2 行'), session.aliases()))
        self.assertTrue(hit['ok'], hit)
        self.assertEqual(2, hit['row_index'])

        first_only = session.verify_intent(intent.validate(raw('1'), session.aliases()))
        self.assertFalse(first_only['ok'], first_only)
        self.assertIn('行范围：第 1 行', first_only['detail'])

        window = session.verify_intent(intent.validate(raw('1-2'), session.aliases()))
        self.assertTrue(window['ok'], window)

        # 行号超出现有行数：要说清楚「表里只有 2 行」，不能含糊地报「没有满足条件的行」。
        beyond = session.verify_intent(intent.validate(raw('9'), session.aliases()))
        self.assertFalse(beyond['ok'], beyond)
        self.assertIn('只有 2 行', beyond['detail'])


if __name__ == '__main__':
    unittest.main()