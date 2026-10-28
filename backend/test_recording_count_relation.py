"""在真实页面上验证「候选与目标的关系」判定。

_count 以前只数 count，count == 1 就当成唯一命中。但 count 回答的是「匹配了几个」，
不是「匹配的是不是用户点的那个」——所以这里用真实 DOM 把五种关系钉死：
self / ancestor / descendant / other / gone。

打标记的方式和录制时一致：在目标元素上写 data-autocase-target，再让 _count 去认。
"""
import unittest

from app.services.recording.locators import build_specs, rank
from app.services.recording.session import FIND_INDEX_JS, TARGET_MARK_ATTR, RecordingSession

PAGE = '''<!doctype html><html><head><meta charset="utf-8"></head><body>
<div id="box" class="row">
  <span id="label">标签</span>
  <button id="btn">删除</button>
</div>
<div id="elsewhere">别处</div>
</body></html>'''


class _Counter:
    """只借用 _count / _relation / _clear_marks 三个方法，不启动整条录制链路。"""

    _count = RecordingSession._count
    _relation = RecordingSession._relation
    _clear_marks = RecordingSession._clear_marks


def css(selector):
    return {'kind': 'css', 'desc': 'locator(%r)' % selector, 'method': 'locator',
            'args': [selector], 'kwargs': {}}


class RelationOnRealPageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls._pw = sync_playwright().start()
        cls._browser = cls._pw.chromium.launch()
        cls._page = cls._browser.new_page()
        cls._page.set_content(PAGE)

    @classmethod
    def tearDownClass(cls):
        cls._browser.close()
        cls._pw.stop()

    def setUp(self):
        self.page = self._page
        self.page.evaluate("() => document.querySelectorAll('[%s]').forEach(e => e.removeAttribute('%s'))"
                           % (TARGET_MARK_ATTR, TARGET_MARK_ATTR))
        self.counter = _Counter()

    def mark(self, selector, mark='m1'):
        self.page.evaluate(
            """([sel, attr, m]) => {
                 const el = document.querySelector(sel);
                 if (el) el.setAttribute(attr, m);
               }""",
            [selector, TARGET_MARK_ATTR, mark])
        return mark

    def count_one(self, selector, mark):
        """返回唯一那条候选的 {count, index, relation}。"""
        out = self.counter._count(self.page.main_frame, [css(selector)], mark=mark)
        return out[0]

    def test_self_when_locator_points_at_target(self):
        mark = self.mark('#btn')
        item = self.count_one('#btn', mark)
        self.assertEqual(item['count'], 1)
        # count == 1 时不写 index：它只在多匹配时才有意义，写 0 会被前端拼成 .nth(0)
        self.assertNotIn('index', item)
        self.assertEqual(item['relation'], 'self')

    def test_ancestor_when_locator_matches_container(self):
        """命中祖先：点击容器通常能把事件冒泡到按钮，可用但不精确。"""
        mark = self.mark('#btn')
        item = self.count_one('#box', mark)
        self.assertEqual(item['count'], 1)
        self.assertNotIn('index', item)
        self.assertEqual(item['relation'], 'ancestor')

    def test_descendant_when_locator_matches_child(self):
        mark = self.mark('#box')
        item = self.count_one('#label', mark)
        self.assertEqual(item['count'], 1)
        self.assertNotIn('index', item)
        self.assertEqual(item['relation'], 'descendant')

    def test_other_when_locator_matches_sibling(self):
        """命中兄弟节点：唯一、但根本不是目标。这正是以前会被静默采用的候选。"""
        mark = self.mark('#btn')
        item = self.count_one('#elsewhere', mark)
        self.assertEqual(item['count'], 1)
        self.assertEqual(item['relation'], 'other')
        # count == 1 不写 index（多匹配时才有意义），关系本身就足以判定它不是目标
        self.assertNotIn('index', item)

    def test_gone_when_target_left_the_page(self):
        """目标不在页面上（被点击带走 / 页面跳转）：不能算命中，也不能算指错。"""
        item = self.count_one('#btn', 'never-existed')
        self.assertEqual(item['relation'], 'gone')

    def test_index_points_at_target_among_many(self):
        """多个匹配时，index 必须是目标那一个——nth 定位的正确性全靠它。"""
        self.page.evaluate("""() => {
            const box = document.querySelector('#box');
            for (let i = 0; i < 3; i++) {
              const b = document.createElement('button');
              b.textContent = '删除';
              box.appendChild(b);
            }
        }""")
        try:
            mark = self.mark('#box button:nth-of-type(3)')
            item = self.count_one('#box button', mark)
            self.assertEqual(item['count'], 4)
            self.assertEqual(item['index'], 2)
            self.assertEqual(item['relation'], 'self')
        finally:
            self.page.evaluate("() => document.querySelectorAll('#box button').forEach((b, i) => { if (i > 0) b.remove() })")

    def test_false_positive_is_ranked_out(self):
        """端到端：唯一但指错的那条不能当主定位。"""
        mark = self.mark('#btn')
        counted = self.counter._count(
            self.page.main_frame,
            [css('#elsewhere'), css('#btn')], mark=mark)
        primary, _ = rank(counted)
        self.assertIsNotNone(primary)
        self.assertEqual(primary['desc'], 'locator(\'#btn\')')

    def test_build_specs_output_is_verified(self):
        """真实快照走一遍 build_specs：所有指向目标的候选都要么 self 要么不进结果。"""
        snap = {'tag': 'button', 'role': 'button', 'name': '删除', 'text': '删除',
                'css': '#btn'}
        mark = self.mark('#btn')
        counted = self.counter._count(self.page.main_frame, build_specs(snap), mark=mark)
        self.assertTrue(counted)
        for item in counted:
            relation = item.get('relation')
            self.assertIn(relation, ('self', 'ancestor', 'descendant', 'other', 'gone', None))
            if relation == 'self' and item['count'] > 1:
                # 多匹配时必须给出目标的位置，.nth(i) 的正确性全靠它
                self.assertGreaterEqual(item['index'], 0)
            if item['count'] == 1:
                # 唯一命中不写序号，否则会被渲染成 .nth(0)
                self.assertNotIn('index', item)


if __name__ == '__main__':
    unittest.main()
