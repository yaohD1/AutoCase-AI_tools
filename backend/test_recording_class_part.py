"""class 里的「名字部分」：把 CSS Modules / BEM 的类名变成可用定位。

洞在哪：真实页面上大量控件没有 role、没有 aria、没有文本，class 是唯一的身份线索。
以前只采了结构路径（css），于是这类元素只能拿到 `div:nth-of-type(3) > div > button`——
它依赖「元素在第几个位置」，改个布局就指到别人。

现在采原始 class，剥掉构建哈希留下名字：

    _sidebarListHeaderAdd_ce5l0_53  →  [class*="_sidebarListHeaderAdd_"]
    user-card__name                 →  [class~="user-card__name"]

同时必须挡住两类噪音：Tailwind 的工具类（p-3、w-[15.368px]）和组件库的通用前缀
（ant-badge、el-input、t-icon）——前者改样式就变，后者全站同名，唯一也说明不了是谁。
"""
import unittest

from app.services.recording.locators import (
    PW_KINDS, RF_KINDS, build_specs, class_stem, class_stems, pick_for,
)
from app.services.recording.session import TARGET_MARK_ATTR, RecordingSession

PW = 'playwright-ts'
RF = 'robot-selenium'

# 下面这些 class 全部来自真实录制页面的快照（.tmp-ai-locate/hard-elements.json），
# 不是编出来的例子：Vite CSS Modules 的 `_name_hash_line`、TDesign 的 t-、Tailwind 的原子类。
REAL = [
    ('_sidebarListHeaderAdd_ce5l0_53', ('_sidebarListHeaderAdd_', False)),
    ('_itemMenu_fxrlp_33', ('_itemMenu_', False)),
    ('_itemMenuIcon_fxrlp_45', ('_itemMenuIcon_', False)),
    ('_input_xsgme_1', ('_input_', False)),
    ('_iconWrapper_dz1io_37 _deleteIcon_dz1io_48', None),
]

UTILITY = [
    'p-3 border-2 rounded-xl',
    'w-[15.368px] h-[15.368px] cursor-pointer',
    'text-white bg-[#FBE9FF]',
    'w-7 h-7 rounded-full',
    'flex-shrink-0',
    'items-center justify-between',
    'absolute bottom-0 right-0 w-24 h-24',
]

# 组件库前缀 / 构建期类名：全站同名或每次构建都变，拿到也没有定位价值。
VENDOR = ['ant-badge', 'ant-input', 'ant-input-outlined', 't-icon', 't-icon-chevron-down',
          'css-1l86wfl', 'el-input__inner', 'sc-bdVaJa', '_ngcontent-c12', 'v-enter-active']


class StemTest(unittest.TestCase):
    def test_css_modules_name_survives_hash(self):
        self.assertEqual(class_stem('_sidebarListHeaderAdd_ce5l0_53'),
                         ('_sidebarListHeaderAdd_', False))
        self.assertEqual(class_stem('_itemMenu_fxrlp_33'), ('_itemMenu_', False))

    def test_hash_without_digits_is_still_stripped(self):
        """fxrlp 里一个数字都没有，靠「短短的小写串」而不是「含数字」认出来。"""
        self.assertEqual(class_stems('_itemMenu_fxrlp_33')[0][0], '_itemMenu_')

    def test_bem_and_camel_are_kept_whole(self):
        """没有哈希可剥的就用整词精确匹配（~=），比 *= 更窄。"""
        self.assertEqual(class_stems('user-card__name'), [('user-card__name', True)])
        self.assertEqual(class_stems('sidebarListHeaderAdd'), [('sidebarListHeaderAdd', True)])
        self.assertEqual(class_stems('user-card--active'), [('user-card', True)])

    def test_utility_classes_are_rejected(self):
        for raw in UTILITY:
            self.assertEqual(class_stems(raw), [], raw)

    def test_vendor_prefixes_are_rejected(self):
        for raw in VENDOR:
            self.assertEqual(class_stems(raw), [], raw)

    def test_vendor_prefix_needs_a_boundary(self):
        """只写 `t` 会把 tab / title / tooltip 全吃掉：前缀后面必须真的跟分隔符。"""
        self.assertEqual(class_stems('title-icon'), [])
        self.assertEqual(class_stems('tab-panel'), [('tab-panel', True)])
        # el- 是组件库前缀，element- 不是（少了边界就是误伤）
        self.assertEqual(class_stems('el-header'), [])
        self.assertEqual(class_stems('element-header'), [('element-header', True)])

    def test_longest_name_wins(self):
        """父子组件同时挂 class 时，取名字长的那个——它才是真正的那个组件。"""
        stems = class_stems('_itemMenu_fxrlp_33 _itemMenuIcon_fxrlp_45')
        self.assertEqual([s[0] for s in stems], ['_itemMenuIcon_', '_itemMenu_'])

    def test_selector_characters_are_not_emitted(self):
        """带引号 / 方括号的值没法安全放进选择器，直接跳过，不要拼出一条坏字符串。"""
        self.assertEqual(class_stems('w-[15.368px]'), [])
        self.assertEqual(class_stems('a"b'), [])


def snap(**over):
    base = {'tag': 'button', 'role': 'button', 'name': None, 'text': None,
            'css': 'div:nth-of-type(3) > div > button'}
    base.update(over)
    return base


class SpecTest(unittest.TestCase):
    def _kinds(self, **over):
        return [(s['kind'], s['desc']) for s in build_specs(snap(**over))]

    def test_both_frameworks_get_a_candidate(self):
        kinds = dict(self._kinds(class_raw='_sidebarListHeaderAdd_ce5l0_53'))
        self.assertEqual(kinds['class_part'], "locator('[class*=\"_sidebarListHeaderAdd_\"]')")
        self.assertEqual(kinds['rf_class_part'], 'rf class:_sidebarListHeaderAdd_')
        # RF 的 desc 只是标签，真正写进脚本的是 args[0]（和其它 RF 候选一致）。
        spec = next(s for s in build_specs(snap(class_raw='_sidebarListHeaderAdd_ce5l0_53'))
                    if s['kind'] == 'rf_class_part')
        self.assertEqual(spec['args'][0],
                         "xpath=//*[contains(@class, '_sidebarListHeaderAdd_')]")

    def test_exact_token_uses_double_quoted_rf_xpath(self):
        spec = next(s for s in build_specs(snap(class_raw='user-card__name'))
                    if s['kind'] == 'rf_class_part')
        self.assertEqual(
            spec['args'][0],
            "xpath=//*[contains(concat(' ', normalize-space(@class), ' '), ' user-card__name ')]")

    def test_no_class_no_candidate(self):
        """老数据没有 class_raw：不能凭空多出候选，也不能因此报错。"""
        kinds = [k for k, _ in self._kinds()]
        self.assertNotIn('class_part', kinds)
        self.assertNotIn('rf_class_part', kinds)

    def test_priority_is_between_text_and_css(self):
        self.assertLess(PW_KINDS.index('text'), PW_KINDS.index('class_part'))
        self.assertLess(PW_KINDS.index('class_part'), PW_KINDS.index('css'))
        self.assertLess(RF_KINDS.index('rf_row_scoped'), RF_KINDS.index('rf_class_part'))
        self.assertLess(RF_KINDS.index('rf_class_part'), RF_KINDS.index('rf_css'))


PAGE = '''<!doctype html><html><head><meta charset="utf-8"></head><body>
<div class="app-shell">
  <aside class="_sidebar_1rfyg_12">
    <button class="_sidebarListHeaderAdd_ce5l0_53" data-idx="add">+</button>
    <ul>
      <li class="_itemMenu_fxrlp_33" data-idx="i1">会话一</li>
      <li class="_itemMenu_fxrlp_33" data-idx="i2">会话二</li>
      <li class="_itemMenu_fxrlp_33" data-idx="i3">会话三</li>
    </ul>
  </aside>
  <main class="p-3 border-2 rounded-xl">
    <button class="ant-btn ant-btn-primary" data-idx="ant">确定</button>
  </main>
</div>
</body></html>'''


IDX_JS = "els => els.map(e => e.getAttribute('data-idx'))"


class _Counter:
    _count = RecordingSession._count
    _relation = RecordingSession._relation
    _clear_marks = RecordingSession._clear_marks


class ClassLocatorOnRealPageTest(unittest.TestCase):
    """真正跑一遍：Playwright 的 css 引擎认不认 ~= / *=，RF 的 xpath 是不是同一个集合。"""

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
        self.counter = _Counter()

    def _mark(self, selector, mark='cm1'):
        self.page.evaluate(
            """([sel, attr, m]) => {
                 document.querySelectorAll('[' + attr + ']').forEach(e => e.removeAttribute(attr));
                 document.querySelector(sel).setAttribute(attr, m);
               }""",
            [selector, TARGET_MARK_ATTR, mark])
        return mark

    def _count(self, raw, mark):
        out = self.counter._count(self.page.main_frame, build_specs(snap(class_raw=raw)),
                                  mark=mark)
        return {c['kind']: c for c in out}

    def test_hash_prefix_matches_exactly_one_element(self):
        mark = self._mark('[data-idx="add"]')
        got = self._count('_sidebarListHeaderAdd_ce5l0_53 ant-btn', mark)
        self.assertEqual(got['class_part']['count'], 1)
        self.assertEqual(got['class_part']['relation'], 'self')

    def test_hash_prefix_is_not_confused_by_longer_sibling_class(self):
        """_itemMenu_ 不能吃掉 _itemMenuIcon_：名字后面的下划线就是边界。"""
        mark = self._mark('[data-idx="i2"]')
        got = self._count('_itemMenu_fxrlp_33 _itemMenuIcon_fxrlp_45', mark)
        # 页面上的两个类都只命中 1 个（i2 同时挂了两个 class 时才是 1）；
        # 这里 i2 只挂 _itemMenu_，所以命中 3 个、index 指向第 2 个。
        self.assertEqual(got['class_part']['count'], 3)
        self.assertEqual(got['class_part']['index'], 1)
        self.assertEqual(got['rf_class_part']['count'], 3)

    def test_exact_token_matches_only_that_token(self):
        mark = self._mark('[data-idx="i1"]')
        got = self._count('_itemMenu_fxrlp_33', mark)
        # 剥掉哈希后是 *=，三个 li 都命中；换成整词（user-card__name 那种）才是精确的。
        self.assertEqual(got['class_part']['count'], 3)
        self.assertEqual(got['rf_class_part']['count'], 3)

    def test_pick_prefers_class_over_css_when_nothing_else_is_unique(self):
        """没有 role / 文本可用时，class 候选必须赢过 css 结构路径。"""
        mark = self._mark('[data-idx="add"]')
        counted = self.counter._count(self.page.main_frame,
                                      build_specs(snap(class_raw='_sidebarListHeaderAdd_ce5l0_53')),
                                      mark=mark)
        for framework in (PW, RF):
            picked = pick_for(framework, counted)
            self.assertIsNotNone(picked, framework)
            self.assertEqual(picked['kind'],
                             'class_part' if framework == PW else 'rf_class_part')


if __name__ == '__main__':
    unittest.main()
