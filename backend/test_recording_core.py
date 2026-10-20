import unittest

from app.services.recording import intent, ir
from app.services.recording.locators import build_specs, rank, stale_check, status_of


class LocatorRankingTest(unittest.TestCase):
    def test_specs_follow_priority_and_skip_dynamic_ids(self):
        snap = {'tag': 'button', 'role': 'button', 'name': '提交', 'text': '提交', 'css': 'html > body > button',
                'id': 'el-id-749-6', 'testid': None, 'label': None, 'placeholder': None}
        kinds = [s['kind'] for s in build_specs(snap)]
        # Playwright 组在前，Robot Framework 组（xpath / css）随后；动态 id 两组都不产生。
        self.assertEqual(kinds, ['role', 'text', 'css', 'rf_xpath', 'rf_css'])
        self.assertNotIn('el-id-749-6', ' '.join(s['desc'] for s in build_specs(snap)))

    def test_first_unique_candidate_wins_and_ambiguous_is_reported(self):
        counted = [
            {'kind': 'role', 'desc': "getByRole('button', name='保存')", 'count': 2},
            {'kind': 'text', 'desc': "getByText('保存')", 'count': 2},
            {'kind': 'css', 'desc': "locator('html > body > button:nth-of-type(3)')", 'count': 1},
        ]
        primary, ordered = rank(counted)
        self.assertEqual(primary['kind'], 'css')
        self.assertEqual([c['kind'] for c in ordered], ['role', 'text', 'css'])
        self.assertEqual(status_of(primary), 'fragile')

    def test_semantic_unique_is_not_fragile(self):
        counted = [{'kind': 'role', 'desc': "getByRole('button', name='提交')", 'count': 1}]
        primary, _ = rank(counted)
        self.assertEqual(status_of(primary), 'unique')

    def test_no_unique_candidate_is_ambiguous(self):
        counted = [{'kind': 'css', 'desc': "locator('div')", 'count': 3}]
        primary, _ = rank(counted)
        self.assertIsNone(primary)
        self.assertEqual(status_of(primary), 'ambiguous')

    def test_stale_when_primary_changes_on_revalidate(self):
        primary = {'kind': 'css', 'desc': "locator('html > body > button:nth-of-type(2)')", 'count': 1}
        self.assertEqual(stale_check("locator('html > body > button:nth-of-type(3)')", primary), 'stale')
        self.assertEqual(stale_check(primary['desc'], primary), 'fragile')
        self.assertEqual(stale_check(primary['desc'], None), 'ambiguous')


class IRTest(unittest.TestCase):
    def test_unknown_step_type_rejected(self):
        with self.assertRaises(ValueError):
            ir.make_step('hover', no=1)

    def test_public_step_strips_internal_fields(self):
        step = ir.make_step('fill', no=1, value='x')
        step['_frame'] = object()
        out = ir.public_step(step)
        self.assertNotIn('_frame', out)
        self.assertEqual(out['value'], 'x')


class IntentTest(unittest.TestCase):
    ALIASES = ['提交', '用户名']

    def test_visible_with_known_alias(self):
        got = intent.validate({'type': 'visible', 'target': '提交'}, self.ALIASES)
        self.assertEqual(got['type'], 'visible')
        self.assertEqual(got['target'], '提交')

    def test_unknown_alias_rejected(self):
        with self.assertRaises(intent.IntentError):
            intent.validate({'type': 'visible', 'target': '不存在'}, self.ALIASES)

    def test_text_contains_needs_value(self):
        with self.assertRaises(intent.IntentError):
            intent.validate({'type': 'text_contains', 'target': '提交'}, self.ALIASES)

    def test_url_contains_needs_no_target(self):
        got = intent.validate({'type': 'url_contains', 'value': '/dashboard'}, [])
        self.assertIsNone(got['target'])
        self.assertEqual(got['value'], '/dashboard')

    def test_sleep_bounds(self):
        self.assertEqual(intent.validate({'type': 'sleep', 'seconds': '2'}, [])['seconds'], 2.0)
        with self.assertRaises(intent.IntentError):
            intent.validate({'type': 'sleep', 'seconds': 0}, [])
        with self.assertRaises(intent.IntentError):
            intent.validate({'type': 'sleep', 'seconds': 999}, [])

    def test_unknown_type_rejected(self):
        with self.assertRaises(intent.IntentError):
            intent.validate({'type': 'screenshot'}, self.ALIASES)

    def test_parse_accepts_fenced_json_and_validates(self):
        reply = '好的：\n```json\n{"type": "wait_visible", "target": "提交"}\n```'
        got = intent.parse_intent('等到提交按钮出现', self.ALIASES, lambda system, prompt: reply)
        self.assertEqual(got['type'], 'wait_visible')

    def test_parse_prompt_lists_aliases_only(self):
        seen = {}

        def ask(system, prompt):
            seen['system'] = system
            seen['prompt'] = prompt
            return '{"type": "visible", "target": "提交"}'

        intent.parse_intent('提交按钮可见', self.ALIASES, ask)
        self.assertEqual(seen['system'], intent.SYSTEM_PROMPT)
        self.assertIn('- 提交', seen['prompt'])
        self.assertIn('- 用户名', seen['prompt'])

    def test_parse_rejects_non_json_reply(self):
        with self.assertRaises(intent.IntentError):
            intent.parse_intent('可见', self.ALIASES, lambda system, prompt: '抱歉，我无法回答')

    def test_empty_description_rejected(self):
        with self.assertRaises(intent.IntentError):
            intent.parse_intent('   ', self.ALIASES, lambda system, prompt: '{}')


if __name__ == '__main__':
    unittest.main()
