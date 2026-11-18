# -*- coding: utf-8 -*-
"""AI 自愈的两个纯函数：步骤投影成 spec、spec 改动解析回定位。

这两块是整条链路里最容易悄悄出错的地方——投影歪了，healer 修的就是另一个问题；
解析歪了，写回去的定位点的是另一个元素。所以单独立一组测试，不掺浏览器。
"""

import unittest

from app.services.recording import spec_render
from app.services.recording.llm_healer import _pair_changes, parse_locator


def target(spec, alias='目标'):
    return {'alias': alias, 'primary': {'kind': 'role', 'desc': 'd', 'spec': spec}}


class SpecRenderTest(unittest.TestCase):
    def test_click_and_assert_render_into_runnable_code(self):
        steps = [
            {'no': 1, 'type': 'goto', 'url': 'http://x/a.html'},
            {'no': 2, 'type': 'click',
             'target': target({'method': 'get_by_role', 'args': ['button'],
                               'kwargs': {'name': '登录', 'exact': True}})},
            {'no': 3, 'type': 'assert', 'intent': {'type': 'visible'},
             'target': target({'method': 'get_by_text', 'args': ['欢迎'], 'kwargs': {}})},
        ]
        code = spec_render.render(steps)['code']
        self.assertIn("await page.goto(\"http://x/a.html\"", code)
        self.assertIn("getByRole('button', { name: '登录', exact: true }).click()", code)
        self.assertIn("await expect(page.getByText('欢迎')).toBeVisible()", code)

    def test_upto_stops_before_the_steps_that_depend_on_the_failure(self):
        steps = [
            {'no': 1, 'type': 'goto', 'url': 'http://x/a.html'},
            {'no': 2, 'type': 'click', 'target': target(
                {'method': 'get_by_text', 'args': ['A'], 'kwargs': {}})},
            {'no': 3, 'type': 'click', 'target': target(
                {'method': 'get_by_text', 'args': ['B'], 'kwargs': {}})},
        ]
        code = spec_render.render(steps, upto=1)['code']
        self.assertIn("getByText('A')", code)
        self.assertNotIn("getByText('B')", code)

    def test_missing_locator_is_reported_instead_of_invented(self):
        steps = [{'no': 4, 'type': 'click', 'target': {'alias': 'x'}}]
        result = spec_render.render(steps)
        self.assertTrue(result['unsupported'])
        self.assertIn('没有可用定位', result['unsupported'][0])

    def test_fill_value_is_escaped_not_interpolated(self):
        steps = [{'no': 1, 'type': 'fill', 'value': '带"引号"\n换行',
                  'target': target({'method': 'locator', 'args': ['#q'], 'kwargs': {}})}]
        code = spec_render.render(steps)['code']
        self.assertIn('\\"引号\\"', code)
        self.assertIn('\\n', code)


class ParseLocatorTest(unittest.TestCase):
    def test_role_with_options(self):
        parsed = parse_locator("page.getByRole('button', { name: '立即登录', exact: true })")
        self.assertEqual(parsed['spec']['method'], 'get_by_role')
        self.assertEqual(parsed['spec']['args'], ['button'])
        self.assertEqual(parsed['spec']['kwargs'], {'name': '立即登录', 'exact': True})

    def test_css_and_text(self):
        self.assertEqual(parse_locator("page.locator('#login')")['spec']['args'], ['#login'])
        self.assertEqual(parse_locator("page.getByText('登 录')")['spec']['method'], 'get_by_text')

    def test_chain_keeps_row_scope(self):
        parsed = parse_locator(
            "page.getByRole('row').filter({ hasText: 'Alice' }).getByRole('button')")
        chain = parsed['spec']['chain']
        self.assertEqual(chain[0]['method'], 'get_by_role')
        self.assertEqual(chain[1]['kwargs'], {'has_text': 'Alice'})
        self.assertEqual(chain[2]['args'], ['button'])

    def test_unparseable_returns_none_instead_of_guessing(self):
        self.assertIsNone(parse_locator('page.somethingWeird(1)'))
        self.assertIsNone(parse_locator(''))


class DiffPairTest(unittest.TestCase):
    def test_pairs_removed_and_added_lines(self):
        diff = '\n'.join([
            '--- a/autocase/probe.spec.ts',
            '+++ b/autocase/probe.spec.ts',
            '@@ -5,1 +5,1 @@',
            "-  await page.getByText('登 录').click()",
            "+  await page.getByText('立即登录').click()",
        ])
        pairs = _pair_changes(diff)
        self.assertEqual(len(pairs), 1)
        self.assertIn('登 录', pairs[0][0][0])
        self.assertIn('立即登录', pairs[0][1][0])


if __name__ == '__main__':
    unittest.main()
