"""RF 定位的序号内联。

以前多匹配的步骤把「取第几个」留给写手在字符串上做手术：
`xpath=(<locator 去掉前缀>)[n+1]`。这条指令有两个必坏的出口：

1. 候选是 `css=` 前缀时，按规则拼出来是 `xpath=(css=div > button)[2]`，语法错误；
2. 写手漏改或改错时，Selenium 的 find_element 匹配多个**取第一个且不报错**——
   脚本是绿的，点的是第一行，比失败更难发现。

现在序号在 `_locator_info` 里一次算进表达式，写手拿到的 locator 已经是
`xpath=(...)[n]`，payload 用 nth_applied 标明「别再拼一次」。
"""
import unittest

from app.services.recording import emit
from app.services.recording.locators import inline_nth

TABLE = '''<!doctype html><html><head><meta charset="utf-8"></head><body>
<table>
  <tr id="r1"><td>WO-1</td><td><button>删除</button></td></tr>
  <tr id="r2"><td>WO-2</td><td><button>删除</button></td></tr>
  <tr id="r3"><td>WO-3</td><td><button>删除</button></td></tr>
</table>
</body></html>'''


def _candidate(kind, locator, count, index=None, relation='self'):
    desc = 'rf xpath:%s' % locator if kind == 'rf_xpath' else 'rf css:%s' % locator
    return {
        'kind': kind,
        'desc': desc,
        'count': count,
        'index': index,
        'relation': relation,
        'spec': {'kind': kind, 'method': 'locator', 'args': [locator], 'kwargs': {}, 'desc': desc},
    }


def _target(*candidates, **extra):
    target = {'alias': '删除', 'status': 'ambiguous', 'candidates': list(candidates),
              'primary': None, 'snap': {'tag': 'button'}}
    target.update(extra)
    return target


class InlineNthTest(unittest.TestCase):
    """纯字符串规则，不依赖浏览器。"""

    def test_xpath_gets_index(self):
        locator, applied = inline_nth("xpath=//button[normalize-space(.)='删除']", 1)
        self.assertTrue(applied)
        self.assertEqual(locator, "xpath=(//button[normalize-space(.)='删除'])[2]")

    def test_first_match_is_still_explicit(self):
        # index 0 也要写出来：显式 [1] 让「取第一个」是决定，而不是 find_element 的默认行为。
        locator, applied = inline_nth('xpath=//tr', 0)
        self.assertTrue(applied)
        self.assertEqual(locator, 'xpath=(//tr)[1]')

    def test_css_is_left_alone(self):
        # css 选择器没法嵌进 xpath，拼出来就是一句语法错误的定位。
        locator, applied = inline_nth('css=div > button', 1)
        self.assertFalse(applied)
        self.assertEqual(locator, 'css=div > button')

    def test_missing_index_is_not_inlined(self):
        for index in (None, -1):
            locator, applied = inline_nth('xpath=//button', index)
            self.assertFalse(applied, index)
            self.assertEqual(locator, 'xpath=//button')

    def test_empty_locator_is_not_inlined(self):
        locator, applied = inline_nth('xpath=', 2)
        self.assertFalse(applied)
        self.assertEqual(locator, 'xpath=')


class LocatorInfoTest(unittest.TestCase):
    """内联只发生在「RF + 多匹配 + xpath」这一种组合上。"""

    def test_rf_multi_match_xpath_is_inlined(self):
        info = emit._locator_info(
            _target(_candidate('rf_xpath', "xpath=//button[normalize-space(.)='删除']", 3, 1)),
            'robot-selenium')
        self.assertEqual(info['locator'], "xpath=(//button[normalize-space(.)='删除'])[2]")
        self.assertEqual(info['matches'], 3)
        # nth 保留：前端要靠它显示「取第 2 个」，写手靠它写注释。
        self.assertEqual(info['nth'], 1)
        self.assertTrue(info['nth_applied'])

    def test_rf_multi_match_css_is_not_inlined(self):
        info = emit._locator_info(
            _target(_candidate('rf_css', 'css=div > button', 2, 1)), 'robot-selenium')
        self.assertEqual(info['locator'], 'css=div > button')
        self.assertEqual(info['nth'], 1)
        self.assertNotIn('nth_applied', info)

    def test_unique_candidate_has_no_index(self):
        info = emit._locator_info(_target(_candidate('rf_xpath', 'xpath=//button', 1)),
                                  'robot-selenium')
        self.assertEqual(info['locator'], 'xpath=//button')
        self.assertIsNone(info['nth'])
        self.assertNotIn('nth_applied', info)

    def test_playwright_keeps_nth_for_the_writer(self):
        # Playwright 侧是 .nth(n) 方法调用，内联进 desc 反而会把界面预览搞脏。
        candidate = _candidate('role', 'xpath=//button', 3, 1)
        candidate['desc'] = "getByRole('button', { name: '删除', exact: true })"
        info = emit._locator_info(_target(candidate), 'playwright-ts')
        self.assertEqual(info['locator'], candidate['desc'])
        self.assertEqual(info['nth'], 1)
        self.assertNotIn('nth_applied', info)

    def test_old_record_without_index_is_not_inlined(self):
        # 旧录制没有 index：既不能瞎猜第几个，也不该拼出 [None]。
        info = emit._locator_info(_target(_candidate('rf_xpath', 'xpath=//button', 3)),
                                  'robot-selenium')
        self.assertEqual(info['locator'], 'xpath=//button')
        self.assertIsNone(info['nth'])
        self.assertNotIn('nth_applied', info)

    def test_chosen_candidate_is_inlined(self):
        candidate = _candidate('rf_xpath', 'xpath=//button', 3, 1)
        info = emit._locator_info(
            _target(candidate, chosen={'desc': candidate['desc'], 'index': 1}),
            'robot-selenium')
        self.assertEqual(info['mode'], 'chosen')
        self.assertEqual(info['locator'], 'xpath=(//button)[2]')
        self.assertTrue(info['nth_applied'])

    def test_false_positive_is_still_excluded(self):
        # 内联不能绕过「指向别人」的判定：那条候选本来就不该被选中。
        bad = _candidate('rf_xpath', 'xpath=//button', 1, relation='other')
        good = _candidate('rf_xpath', 'xpath=//button[@id="del"]', 3, 1)
        info = emit._locator_info(_target(bad, good), 'robot-selenium')
        self.assertEqual(info['locator'], 'xpath=(//button[@id="del"])[2]')


class InlineXpathOnRealPageTest(unittest.TestCase):
    """内联出来的 xpath 到底指不指对地方。

    Selenium 和 Playwright 最终都把 xpath 交给浏览器原生的 document.evaluate，
    所以这里的断言就是 SeleniumLibrary 会得到的结果。
    """

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

    def row_of(self, expr):
        loc = self._page.locator('xpath=' + expr)
        self.assertEqual(loc.count(), 1, '这条定位应当唯一：%s' % expr)
        return loc.evaluate("el => el.closest('tr').id")

    def test_inlined_index_points_at_that_row(self):
        base = "//button[normalize-space(.)='删除']"
        for index, row in ((0, 'r1'), (1, 'r2'), (2, 'r3')):
            expr = inline_nth('xpath=' + base, index)[0][6:]
            self.assertEqual(self.row_of(expr), row, 'index=%d' % index)

    def test_bare_xpath_really_picks_the_first_row(self):
        """不加序号时 Selenium 取第一个且不报错——这就是「绿着点错行」的来源。"""
        bare = self._page.locator("xpath=//button[normalize-space(.)='删除']")
        self.assertEqual(bare.count(), 3)
        self.assertEqual(bare.first.evaluate("el => el.closest('tr').id"), 'r1')

    def test_row_anchor_and_inline_index_agree(self):
        """行锚定和「取第 2 个」必须落到同一行，否则录制侧两种定位在打架。"""
        anchored = "//tr[contains(normalize-space(.), 'WO-2')]//button[normalize-space(.)='删除']"
        inlined = inline_nth("xpath=//button[normalize-space(.)='删除']", 1)[0]
        self.assertEqual(self.row_of(anchored), 'r2')
        self.assertEqual(self.row_of(inlined[6:]), 'r2')


class PromptTest(unittest.TestCase):
    """提示词里必须明说「已经内联，不要再拼一次」。"""

    def _step(self, target):
        return {'no': 4, 'type': 'click', 'text': '删除第二行',
                'target': target}

    def test_nth_applied_reaches_the_prompt(self):
        step = self._step(_target(_candidate('rf_xpath', "xpath=//button[normalize-space(.)='删除']", 3, 1)))
        item = emit._steps_for_prompt([step], 'robot-selenium')[0]['target']
        self.assertTrue(item['nth_applied'])
        self.assertEqual(item['locator'], "xpath=(//button[normalize-space(.)='删除'])[2]")
        self.assertEqual(item['match_count'], 3)

    def test_prompt_explains_nth_applied(self):
        self.assertIn('nth_applied', emit.LOCATOR_RULES)
        self.assertIn('不要再包一层', emit.LOCATOR_RULES)
        # 老的「让写手自己拼 (...)[n+1]」必须彻底消失，否则两条指令会打架。
        self.assertNotIn('去掉 xpath= 前缀后的表达式', emit.LOCATOR_RULES)

    def test_candidates_are_inlined_too(self):
        # 写手从候选清单里挑备选时，拿到的也必须是能直接抄的定位。
        target = _target(_candidate('rf_xpath', 'xpath=//button', 3, 1))
        out = emit._candidates_for_prompt(target, 'robot-selenium')
        self.assertEqual(out[0]['locator'], 'xpath=(//button)[2]')
        self.assertTrue(out[0]['nth_applied'])

    def test_unique_candidate_in_prompt_has_no_nth_applied(self):
        target = _target(_candidate('rf_xpath', 'xpath=//button', 1))
        out = emit._candidates_for_prompt(target, 'robot-selenium')
        self.assertEqual(out[0]['locator'], 'xpath=//button')
        self.assertNotIn('nth_applied', out[0])


if __name__ == '__main__':
    unittest.main()
