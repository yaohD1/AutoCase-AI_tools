"""行锚点：把「第 3 行的删除按钮」换成「WO-2 那一行的删除按钮」。

列表里同类按钮重复是录制最常见的产物——以前只能靠 css 的 nth-child 或 .nth(i) 硬扛，
列表一增一删就整体错位，而且点错了也不报错。

现在录制时把行内每个单元格的文本都带上，在页面上挨个试：哪个文本能让这一行在全页唯一命中、
并且真的包住目标，就用它做锚点。试不出来就不加，宁可退回 nth，也不锚错行。
"""
import unittest

from app.services.recording.locators import (
    build_locator, build_specs, pick_for, rank, rf_row_scoped_specs, row_scoped_spec,
)
from app.services.recording.session import TARGET_MARK_ATTR, RecordingSession

TABLE = '''<!doctype html><html><head><meta charset="utf-8"></head><body>
<table>
  <tr id="r1"><td>WO-1</td><td>待处理</td><td><button>删除</button></td></tr>
  <tr id="r2"><td>WO-2</td><td>待处理</td><td><button>删除</button></td></tr>
</table>
</body></html>'''

PW = 'playwright-ts'
RF = 'robot-selenium'


class _Counter:
    _count = RecordingSession._count
    _relation = RecordingSession._relation
    _clear_marks = RecordingSession._clear_marks
    _row_anchors = RecordingSession._row_anchors
    _anchor_ok = RecordingSession._anchor_ok


def snap_with_row(texts, role='row', tag='tr'):
    return {'tag': 'button', 'role': 'button', 'name': '删除', 'text': '删除',
            'css': '#r2 > td:nth-child(3) > button',
            'row_scope': {'role': role, 'tag': tag, 'texts': texts}}


class RowAnchorOnRealPageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls._pw = sync_playwright().start()
        cls._browser = cls._pw.chromium.launch()
        cls._page = cls._browser.new_page()
        cls._page.set_content(TABLE)

    @classmethod
    def tearDownClass(cls):
        cls._browser.close()
        cls._pw.stop()

    def setUp(self):
        self.page = self._page
        self.counter = _Counter()
        self.mark = 'acRowTest'
        self.page.evaluate(
            """([attr, mark]) => {
                 document.querySelectorAll('[' + attr + ']').forEach(e => e.removeAttribute(attr));
                 document.querySelector('#r2 button').setAttribute(attr, mark);
               }""",
            [TARGET_MARK_ATTR, self.mark])

    def count(self, snap):
        return self.counter._count(self.page.main_frame, build_specs(snap), mark=self.mark, snap=snap)

    def test_row_anchor_uses_business_key_not_status(self):
        """'待处理' 两行都有，不能当锚点；'WO-2' 只属于目标行，应该被选中。"""
        counted = self.count(snap_with_row(['WO-1', '待处理', 'WO-2']))
        anchored = [c for c in counted if c['kind'] == 'row_scoped']
        self.assertEqual(len(anchored), 1)
        self.assertIn('WO-2', anchored[0]['desc'])
        self.assertNotIn('待处理', anchored[0]['desc'])

    def test_row_anchor_wins_over_numbered_locator(self):
        counted = self.count(snap_with_row(['WO-2']))
        primary, _ = rank(counted)
        self.assertIsNotNone(primary)
        self.assertEqual(primary['kind'], 'row_scoped')
        self.assertEqual(primary['count'], 1)

    def test_row_anchor_resolves_to_the_target_element(self):
        """写进脚本的那条链必须真的点在第 2 行的按钮上。"""
        counted = self.count(snap_with_row(['WO-2']))
        primary, _ = rank(counted)
        loc = build_locator(self.page.main_frame, primary['spec'])
        self.assertEqual(loc.count(), 1)
        # 用元素 id 反查，确认落到 #r2 那一行里
        owner = loc.evaluate("el => el.closest('tr').id")
        self.assertEqual(owner, 'r2')

    def test_no_anchor_when_every_text_appears_twice(self):
        counted = self.count(snap_with_row(['待处理', '删除']))
        self.assertEqual([c for c in counted if c['kind'] == 'row_scoped'], [])

    def test_no_anchor_without_row_scope(self):
        snap = snap_with_row(['WO-2'])
        snap['row_scope'] = None
        counted = self.count(snap)
        self.assertEqual([c for c in counted if c['kind'] == 'row_scoped'], [])

    def test_no_anchor_when_target_already_unique(self):
        """目标自己就有唯一定位时不该再套行作用域：脚本更长、依赖更多，没有收益。"""
        snap = snap_with_row(['WO-1'])
        snap['name'] = 'WO-1 删除'
        counted = self.counter._count(
            self.page.main_frame,
            [{'kind': 'css', 'desc': "locator('#r2 button')",
              'method': 'locator', 'args': ['#r2 button'], 'kwargs': {}}],
            mark=self.mark, snap=snap)
        self.assertEqual([c for c in counted if c['kind'] == 'row_scoped'], [])

    def test_rendered_chain_is_valid_playwright_syntax(self):
        spec = row_scoped_spec('row', 'WO-2', {
            'method': 'get_by_role', 'args': ['button'],
            'kwargs': {'name': '删除', 'exact': True}})
        self.assertEqual(
            spec['desc'],
            "getByRole('row').filter({ hasText: 'WO-2' })"
            ".getByRole('button', { name: '删除', exact: true })")

    # ---- Robot Framework：SeleniumLibrary 只接一个定位字符串，行作用域得写进同一条 xpath ----

    def rf_anchors(self, counted):
        return [c for c in counted if c['kind'] == 'rf_row_scoped']

    def test_rf_row_anchor_uses_business_key_not_status(self):
        counted = self.count(snap_with_row(['WO-1', '待处理', 'WO-2']))
        anchored = self.rf_anchors(counted)
        self.assertEqual(len(anchored), 1)
        self.assertIn('WO-2', anchored[0]['desc'])
        self.assertNotIn('待处理', anchored[0]['desc'])

    def test_rf_row_anchor_is_one_self_contained_xpath(self):
        counted = self.count(snap_with_row(['WO-2']))
        anchored = self.rf_anchors(counted)
        self.assertEqual(len(anchored), 1)
        # SeleniumLibrary 没有链式调用，必须一条 xpath 说完：//tr[行键]//button[...]
        self.assertEqual(
            anchored[0]['spec']['args'][0],
            "xpath=//tr[contains(normalize-space(.), 'WO-2')]"
            "//button[normalize-space(.)='删除']")

    def test_rf_row_anchor_resolves_to_the_target_element(self):
        counted = self.count(snap_with_row(['WO-2']))
        primary = pick_for(RF, counted)
        self.assertIsNotNone(primary)
        self.assertEqual(primary['kind'], 'rf_row_scoped')
        loc = build_locator(self.page.main_frame, primary['spec'])
        self.assertEqual(loc.count(), 1)
        self.assertEqual(loc.evaluate("el => el.closest('tr').id"), 'r2')

    def test_rf_row_anchor_wins_over_numbered_locator(self):
        counted = self.count(snap_with_row(['WO-2']))
        self.assertEqual(pick_for(RF, counted)['kind'], 'rf_row_scoped')
        self.assertEqual(pick_for(PW, counted)['kind'], 'row_scoped')

    def test_rf_row_anchor_falls_back_to_tr_without_tag(self):
        """更早录的快照没有 tag：按 role 猜 tr/li，猜错只会数不到，不会锚错行。"""
        snap = snap_with_row(['WO-2'])
        del snap['row_scope']['tag']
        counted = self.count(snap)
        anchored = self.rf_anchors(counted)
        self.assertEqual(len(anchored), 1)
        self.assertTrue(anchored[0]['spec']['args'][0].startswith('xpath=//tr['))

    def test_no_rf_anchor_when_every_text_appears_twice(self):
        counted = self.count(snap_with_row(['待处理', '删除']))
        self.assertEqual(self.rf_anchors(counted), [])

    def test_rf_row_anchor_skipped_when_target_already_unique(self):
        """RF 侧已经有唯一定位时不该再套行作用域。"""
        snap = snap_with_row(['WO-1'])
        snap['name'] = 'WO-1 删除'
        counted = self.counter._count(
            self.page.main_frame,
            [{'kind': 'rf_xpath', 'desc': "rf xpath://*[@id='r2']/td[3]/button",
              'method': 'locator', 'args': ["xpath=//*[@id='r2']/td[3]/button"],
              'kwargs': {}}],
            mark=self.mark, snap=snap)
        self.assertEqual(self.rf_anchors(counted), [])


DIV_TABLE = '''<!doctype html><html><head><meta charset="utf-8"></head><body>
<div role="row" id="d1"><div role="cell">WO-1</div><div role="cell"><button>删除</button></div></div>
<div role="row" id="d2"><div role="cell">WO-2</div><div role="cell"><button>删除</button></div></div>
</body></html>'''


class RfRowOnDivTableTest(unittest.TestCase):
    """组件库常见结构：没有 <tr>，行是 div[role=row]。RF 得写 //div[@role='row']。"""

    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls._pw = sync_playwright().start()
        cls._browser = cls._pw.chromium.launch()
        cls._page = cls._browser.new_page()
        cls._page.set_content(DIV_TABLE)

    @classmethod
    def tearDownClass(cls):
        cls._browser.close()
        cls._pw.stop()

    def test_div_row_anchor_resolves_to_the_second_row(self):
        page = self._page
        mark = 'acDivRow'
        page.evaluate(
            """([attr, mark]) => {
                 document.querySelectorAll('[' + attr + ']').forEach(e => e.removeAttribute(attr));
                 document.querySelector('#d2 button').setAttribute(attr, mark);
               }""",
            [TARGET_MARK_ATTR, mark])
        snap = snap_with_row(['WO-1', 'WO-2'], tag='div')
        counter = _Counter()
        counted = counter._count(page.main_frame, build_specs(snap), mark=mark, snap=snap)
        row = [c for c in counted if c['kind'] == 'rf_row_scoped']
        self.assertEqual(len(row), 1)
        self.assertTrue(row[0]['spec']['args'][0].startswith("xpath=//div[@role='row']["))
        loc = build_locator(page.main_frame, row[0]['spec'])
        self.assertEqual(loc.evaluate("el => el.closest('[role=row]').id"), 'd2')


class RfRowSpecTest(unittest.TestCase):
    """纯规则：xpath 拼接。不依赖浏览器。"""

    def spec_for(self, scope, key, locator):
        inner = {'kind': 'rf_xpath', 'spec': {'method': 'locator', 'args': [locator], 'kwargs': {}}}
        return rf_row_scoped_specs(scope, key, inner)

    def test_div_row_uses_role_attribute(self):
        specs = self.spec_for({'role': 'row', 'tag': 'div'}, 'WO-2', "xpath=//span[@title='删']")
        self.assertEqual(
            specs[0]['args'][0],
            "xpath=//div[@role='row'][contains(normalize-space(.), 'WO-2')]//span[@title='删']")

    def test_css_inner_is_rejected(self):
        """css= 没法嵌进 xpath，宁可退回「取第 N 个」，也不要写出跑不通的定位。"""
        self.assertEqual(self.spec_for({'role': 'row', 'tag': 'tr'}, 'WO-2', 'css=tr > button'), [])

    def test_quote_in_key_is_escaped(self):
        """行键里有引号时不能直接塞进 xpath 字面量：只有单引号就换双引号括起来。"""
        specs = self.spec_for({'role': 'row', 'tag': 'tr'}, "WO'2", "xpath=//button")
        self.assertIn('"WO\'2"', specs[0]['args'][0])

    def test_both_quotes_use_concat(self):
        specs = self.spec_for({'role': 'row', 'tag': 'tr'}, '他说"就这\'样"', "xpath=//button")
        self.assertIn('concat(', specs[0]['args'][0])


if __name__ == '__main__':
    unittest.main()
