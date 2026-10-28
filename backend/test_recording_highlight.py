"""定位预览：把匹配元素框出来 → 人眼确认 → 指定用第几个。

核心约束（都来自实测）：
1. 编号顺序必须等于 locator 的匹配顺序，否则用户选「2 号」和生成的 nth 对不上；
2. index 属于「某一条候选」，换一条候选就不是同一个元素，所以选择必须连同 desc 一起存；
3. 元素数量变少时不能静默改回第一个——那会点到别的元素，比报错更糟，要标出序号失效。

另一条容易被忽略的：`getByRole` 命中 3 个时，主定位会退到 css 绝对路径（count=1）。
这种「结构定位」步骤同样要记下「其实有 3 个、你点的是第 2 个」，否则用户看不到风险。
"""
import http.server
import threading
import time
import unittest

from app.services.recording import emit, locators
from app.services.recording.session import RecordingSession

TABLE_PAGE = '''<!doctype html><html><head><meta charset="utf-8"><title>设备</title></head><body>
<h1>设备列表</h1>
<table>
  <tr><td>设备A</td><td>在线</td><td><button>删除</button></td></tr>
  <tr><td>设备B</td><td>离线</td><td><button>删除</button></td></tr>
  <tr><td>设备C</td><td>在线</td><td><button>删除</button></td></tr>
</table>
<div id="out"></div>
<script>
document.querySelectorAll('button').forEach(function (b) {
  b.onclick = function () {
    document.getElementById('out').textContent = '删除了 ' + b.closest('tr').cells[0].textContent;
  };
});
</script></body></html>'''

SINGLE_PAGE = '''<!doctype html><html><head><meta charset="utf-8"></head><body>
<h1>单元素</h1><button id="only">保存</button></body></html>'''

PAGES = {'/table': TABLE_PAGE, '/single': SINGLE_PAGE}


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAGES.get(self.path.split('?')[0], TABLE_PAGE).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def _in_thread(session, fn, timeout=20):
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
        raise RuntimeError('录制线程超时')
    if 'error' in box:
        raise box['error']
    return box['value']


def _wait_for(check, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if check():
            return True
        time.sleep(0.05)
    return check()


def _idle_scenario(session, page):
    # Playwright 的同步对象不能跨线程：页面操作都要回到录制线程里做。
    session._test_page = page


class NarrowestTest(unittest.TestCase):
    """候选挑选：只在「命中数 > 1」的候选里找最窄的。"""

    def test_skips_unique_candidates(self):
        counted = [
            {'kind': 'css', 'desc': 'css', 'count': 1},
            {'kind': 'role', 'desc': 'role', 'count': 3},
        ]
        self.assertEqual(locators.narrowest([c for c in counted if c['count'] > 1])['desc'], 'role')

    def test_prefers_fewest_matches_then_priority(self):
        counted = [
            {'kind': 'css', 'desc': 'css', 'count': 9},
            {'kind': 'text', 'desc': 'text', 'count': 2},
            {'kind': 'role', 'desc': 'role', 'count': 2},
        ]
        # 命中数相同时按 PRIORITY：role 在 text 之前
        self.assertEqual(locators.narrowest(counted)['desc'], 'role')

    def test_no_matches_returns_none(self):
        self.assertIsNone(locators.narrowest([{'kind': 'css', 'desc': 'a', 'count': 0}]))
        self.assertIsNone(locators.narrowest([]))


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = 'http://127.0.0.1:%d' % cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        RecordingSession._current = None

    def tearDown(self):
        RecordingSession.dispose_current()

    def _start(self, path='/table'):
        return RecordingSession.start_new(self.base + path, headless=True,
                                          ask_daemon=False, driver=_idle_scenario)

    def _click_nth(self, session, nth, name='删除'):
        page = session._test_page
        _in_thread(session, lambda: page.get_by_role('button', name=name, exact=True).nth(nth).click())

    def _wait_click_step(self, session):
        def found():
            return any(s['type'] == 'click' for s in session.snapshot()['steps'])

        self.assertTrue(_wait_for(found), '点击事件没有落库')
        time.sleep(0.15)
        return next(s for s in session.snapshot()['steps'] if s['type'] == 'click')


class MatchSummaryTest(_Base):
    """录制时就要把「那几个元素是什么」记下来：浏览器一关就再也查不到了。"""

    def test_multi_match_is_recorded_with_row_text(self):
        session = self._start()
        self._click_nth(session, 1)  # 第 2 行
        step = self._wait_click_step(session)
        target = step['target']
        matches = target.get('matches') or {}
        self.assertEqual(matches.get('count'), 3)
        self.assertEqual(len(matches.get('items') or []), 3)
        rows = [item['row'] for item in matches['items']]
        self.assertIn('设备B 离线 删除', rows)
        # 三个按钮自身的文本是一样的，能区分它们全靠行文本
        self.assertEqual({item['text'] for item in matches['items']}, {'删除'})
        self.assertEqual({item['visible'] for item in matches['items']}, {True})

    def test_matches_are_kept_even_when_primary_is_unique(self):
        """getByRole 命中 3 个 → 现在用「行 + 行内按钮」锚定，不再退到 css 绝对路径。

        以前这里只能选 css（div:nth-of-type(2) > ...），列表一增一删就错位；
        行锚点把「第 2 行的删除」变成「设备A 那一行的删除」，多匹配摘要也还得留着。
        """
        session = self._start()
        self._click_nth(session, 0)
        step = self._wait_click_step(session)
        target = step['target']
        primary = target.get('primary') or {}
        self.assertEqual(primary.get('kind'), 'row_scoped',
                         '列表行内元素应当按行锚定，而不是退回 css 绝对路径')
        self.assertIn('设备A', primary.get('desc') or '')
        self.assertIsNotNone(target.get('matches'), '行锚定的步骤同样要记下多个匹配')
        self.assertEqual(target['matches']['desc'],
                         "getByRole('button', { name: '删除', exact: true })")

    def test_single_element_records_nothing(self):
        session = self._start('/single')
        self._click_nth(session, 0, name='保存')
        step = self._wait_click_step(session)
        self.assertIsNone(step['target'].get('matches'))


class HighlightTest(_Base):
    def test_frames_all_matches_and_marks_the_recorded_one(self):
        session = self._start()
        self._click_nth(session, 1)
        step = self._wait_click_step(session)
        result = session.highlight(step['no'], framework='playwright-ts')
        self.assertTrue(result['ok'])
        self.assertEqual(result['count'], 3)
        self.assertEqual(result['active'], 1, '默认要指向录制时点的那一个')
        self.assertEqual(len(result['items']), 3)
        # 编号顺序必须等于 locator 的匹配顺序
        self.assertEqual([item['index'] for item in result['items']], [0, 1, 2])
        self.assertIn('设备A', result['items'][0]['row'])

    def test_overlay_is_drawn_and_replaced(self):
        session = self._start()
        self._click_nth(session, 0)
        step = self._wait_click_step(session)
        no = step['no']
        session.highlight(no, framework='playwright-ts')
        drawn = _in_thread(session, lambda: session._test_page.evaluate(
            "() => { const l = document.getElementById('__autocase_boxes');"
            " return l ? l.children.length : -1; }"))
        self.assertEqual(drawn, 3, '三个匹配要画三个框')
        # 换一个：不能叠加出 6 个框
        session.highlight(no, index=2, framework='playwright-ts')
        again = _in_thread(session, lambda: session._test_page.evaluate(
            "() => { const l = document.getElementById('__autocase_boxes');"
            " return l ? l.children.length : -1; }"))
        self.assertEqual(again, 3)

    def test_active_box_is_the_solid_red_one(self):
        session = self._start()
        self._click_nth(session, 2)
        step = self._wait_click_step(session)
        session.highlight(step['no'], framework='playwright-ts')
        borders = _in_thread(session, lambda: session._test_page.evaluate(
            "() => Array.from(document.getElementById('__autocase_boxes').children)"
            ".map(b => b.style.border)"))
        self.assertEqual(borders[2], '2px solid rgb(255, 59, 48)')
        self.assertTrue(all('dashed' in b for b in borders[:2]))

    def test_clear_removes_the_overlay(self):
        session = self._start()
        self._click_nth(session, 0)
        step = self._wait_click_step(session)
        session.highlight(step['no'], framework='playwright-ts')
        session.clear_highlight()
        left = _in_thread(session, lambda: session._test_page.evaluate(
            "() => document.getElementById('__autocase_boxes') ? 'still' : 'cleared'"))
        self.assertEqual(left, 'cleared')

    def test_index_out_of_range_falls_back_to_first(self):
        session = self._start()
        self._click_nth(session, 0)
        step = self._wait_click_step(session)
        result = session.highlight(step['no'], index=99, framework='playwright-ts')
        self.assertTrue(result['ok'])
        self.assertEqual(result['active'], 0)

    def test_unknown_step_is_reported(self):
        session = self._start()
        with self.assertRaises(Exception):
            session.highlight(999, framework='playwright-ts')


class ChosenTest(_Base):
    def _prepare(self, nth=1):
        session = self._start()
        self._click_nth(session, nth)
        step = self._wait_click_step(session)
        desc = step['target']['matches']['desc']
        return session, step['no'], desc

    def test_choice_is_stored_with_its_candidate(self):
        session, no, desc = self._prepare()
        snap = session.set_chosen(no, desc, 2)
        target = snap['steps'][no - 1]['target']
        self.assertEqual(target['chosen'], {'desc': desc, 'index': 2})
        hit = next(c for c in target['candidates'] if c['desc'] == desc)
        self.assertEqual(hit['index'], 2)

    def test_choice_survives_stop(self):
        session, no, desc = self._prepare()
        session.set_chosen(no, desc, 2)
        after = session.stop()
        target = after['steps'][no - 1]['target']
        self.assertEqual(target['chosen'], {'desc': desc, 'index': 2})
        self.assertFalse(target['chosen'].get('index_invalid'))

    def test_choice_becomes_invalid_when_rows_disappear(self):
        """录制时选了第 3 个，停止前列表少了一行 → 序号失效，不能静默改回第一个。"""
        session, no, desc = self._prepare()
        session.set_chosen(no, desc, 2)
        _in_thread(session, lambda: session._test_page.evaluate(
            "() => { const rows = document.querySelectorAll('tr'); rows[rows.length - 1].remove(); }"))
        after = session.stop()
        target = after['steps'][no - 1]['target']
        self.assertEqual(target['chosen']['index'], 2, '用户选的序号不能被改掉')
        self.assertTrue(target['chosen'].get('index_invalid'))

    def test_highlight_prefers_the_users_choice(self):
        session, no, desc = self._prepare()
        session.set_chosen(no, desc, 2)
        result = session.highlight(no, framework='playwright-ts')
        self.assertEqual(result['desc'], desc)
        self.assertEqual(result['active'], 2)

    def test_highlight_after_stop_still_works(self):
        session, no, desc = self._prepare()
        session.stop()
        result = session.highlight(no, framework='playwright-ts')
        self.assertTrue(result['ok'])
        self.assertEqual(result['count'], 3)


class EmitUsesChoiceTest(unittest.TestCase):
    """生成端：用户的选择优先于任何自动选择，且选的那条必须能在目标框架里用。"""

    def _target(self, **extra):
        candidates = [
            {'kind': 'role', 'desc': "getByRole('button', name='删除')", 'count': 3, 'index': 1,
             'spec': {'method': 'get_by_role', 'args': ['button'],
                      'kwargs': {'name': '删除', 'exact': True}}},
            {'kind': 'css', 'desc': "locator('body > table > tr > td > button')", 'count': 1,
             'spec': {'method': 'locator', 'args': ['body > table > tr > td > button'], 'kwargs': {}}},
        ]
        target = {'candidates': candidates,
                  'matches': {'desc': candidates[0]['desc'], 'count': 3,
                              'items': [{'text': '删除', 'row': '设备B 离线 删除'}]}}
        target.update(extra)
        return target, candidates

    def test_without_choice_keeps_the_old_behaviour(self):
        target, _ = self._target()
        info = emit._locator_info(target, 'playwright-ts')
        self.assertEqual(info['mode'], 'unique')
        self.assertTrue(info['locator'].startswith('locator('))

    def test_choice_overrides_automatic_pick(self):
        target, candidates = self._target()
        target['chosen'] = {'desc': candidates[0]['desc'], 'index': 2}
        info = emit._locator_info(target, 'playwright-ts')
        self.assertEqual(info['mode'], 'chosen')
        self.assertEqual(info['locator'], candidates[0]['desc'])
        self.assertEqual(info['nth'], 2)

    def test_choice_is_ignored_when_the_candidate_drops_to_one(self):
        """页面只剩一个匹配时，nth(2) 已经没有意义，退回正常的唯一选择。"""
        target, candidates = self._target()
        candidates[0]['count'] = 1
        target['chosen'] = {'desc': candidates[0]['desc'], 'index': 2}
        info = emit._locator_info(target, 'playwright-ts')
        self.assertNotEqual(info['mode'], 'chosen')

    def test_choice_is_ignored_for_a_framework_that_cannot_use_it(self):
        target, candidates = self._target()
        target['chosen'] = {'desc': candidates[0]['desc'], 'index': 2}
        obj = {'kind': 'rf_xpath', 'desc': 'rf xpath:btn', 'count': 1,
               'spec': {'method': 'locator', 'args': ['xpath=//button'], 'kwargs': {}}}
        target['candidates'] = [candidates[0], obj]
        info = emit._locator_info(target, 'robot-selenium')
        self.assertEqual(info['locator'], 'xpath=//button')

    def test_match_texts_prefer_row_over_element_text(self):
        target, _ = self._target()
        texts = emit._match_texts(target)
        self.assertEqual(texts['of'], 3)
        self.assertEqual(texts['each'], ['设备B 离线 删除'])

    def test_match_texts_absent_when_no_summary(self):
        target, _ = self._target()
        target.pop('matches')
        self.assertIsNone(emit._match_texts(target))

    def test_prompt_carries_the_match_summary(self):
        target, _ = self._target()
        target['chosen'] = {'desc': target['candidates'][0]['desc'], 'index': 2}
        steps = [{'no': 1, 'type': 'click', 'target': target, 'value': None,
                  'label': None, 'url': None, 'page_index': 0, 'intent': None, 'dialog': None}]
        payload = emit._steps_for_prompt(steps, 'playwright-ts')
        self.assertEqual(payload[0]['target']['nth'], 2)
        self.assertEqual(payload[0]['target']['matches']['of'], 3)


if __name__ == '__main__':
    unittest.main()
