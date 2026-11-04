"""「等待加载」与「点击导致的跳转」这两件事的测试。

两件事针对的是同一个故障：点击后页面要跳转，站点慢，下一步直接找不到元素。
- 等待加载：用户可以在任意位置插一步「等页面到某个状态」（load / domcontentloaded / networkidle）。
- nav_to：录制时把「这一下点击真的跳走了、跳到哪」记在点击那一步上，
  生成时写手据此写 waitForURL，用户什么都不用做。
"""
import unittest

from app.services.recording import emit, intent, ir, replay
from app.services.recording.session import RecordingSession


def _click(no, alias='登录'):
    return {'no': no, 'type': 'click', 'value': None, 'url': None, 'intent': None,
            'text': None, 'target': {'alias': alias, 'candidates': [], 'primary': None, 'status': 'ok'}}


class ReadyIntentTest(unittest.TestCase):
    def test_default_is_load_with_15_seconds(self):
        got = intent.validate({'type': 'page_ready'}, [])
        # 用户嘴里说的「页面加载完」就是 onload 那一档，默认值和它对齐。
        self.assertEqual(got['state'], 'load')
        self.assertEqual(got['timeout'], ir.DEFAULT_READY_SECONDS)
        # 等整页加载不需要目标元素，也不该被塞进轮询刷新那套字段。
        self.assertIsNone(got['target'])
        self.assertFalse(got['reload'])

    def test_explicit_state_and_timeout_kept(self):
        got = intent.validate({'type': 'page_ready', 'state': 'networkidle', 'timeout': 40}, [])
        self.assertEqual(got['state'], 'networkidle')
        self.assertEqual(got['timeout'], 40)

    def test_unknown_state_rejected(self):
        with self.assertRaises(intent.IntentError):
            intent.validate({'type': 'page_ready', 'state': 'idle'}, [])

    def test_zero_timeout_falls_back_to_default(self):
        # 0 在别处表示「不等待」，这里不能变成一个 0 秒的超时（等于必然失败）。
        got = intent.validate({'type': 'page_ready', 'timeout': 0}, [])
        self.assertEqual(got['timeout'], ir.DEFAULT_READY_SECONDS)

    def test_step_type_is_wait(self):
        # 语义是「等到」，不是「此刻就要成立」；归到 wait，前端和生成端才走等待那一支。
        self.assertEqual(ir.step_type_for_intent('page_ready', {'timeout': 15}), 'wait')
        self.assertEqual(ir.step_type_for_intent('page_ready', {}), 'wait')


class ClickNavTest(unittest.TestCase):
    def test_nav_marks_the_click_step(self):
        session = RecordingSession.__new__(RecordingSession)
        session._steps = [_click(1), _click(2)]
        session._mark_click_nav('http://example.com/home')
        self.assertEqual(session._steps[-1]['nav_to'], 'http://example.com/home')
        self.assertNotIn('nav_to', session._steps[0])

    def test_nav_after_new_page_step_still_finds_the_click(self):
        # 新标签页那一步夹在中间时，落点仍属于触发它的那次点击。
        session = RecordingSession.__new__(RecordingSession)
        session._steps = [_click(1), {'no': 2, 'type': 'new_page', 'url': None}]
        session._mark_click_nav('http://example.com/tab')
        self.assertEqual(session._steps[0]['nav_to'], 'http://example.com/tab')

    def test_nav_without_recent_click_is_ignored(self):
        session = RecordingSession.__new__(RecordingSession)
        session._steps = [{'no': 1, 'type': 'goto', 'url': 'http://a'}, _click(2)]
        session._steps[-1] = {'no': 2, 'type': 'fill', 'value': 'x'}
        session._mark_click_nav('http://example.com/x')
        self.assertNotIn('nav_to', session._steps[1])


class PromptTest(unittest.TestCase):
    def test_step_payload_carries_nav_to_and_page_ready(self):
        click = _click(1)
        click['nav_to'] = 'http://example.com/home'
        ready = {'no': 2, 'type': 'wait', 'text': None, 'url': None,
                 'intent': {'type': 'page_ready', 'state': 'load', 'timeout': 15,
                            'target': None, 'value': None, 'seconds': None,
                            'interval': None, 'reload': False}}
        items = emit._steps_for_prompt([click, ready], 'playwright-ts')
        self.assertEqual(items[0]['nav_to'], 'http://example.com/home')
        self.assertEqual(items[1]['intent']['state'], 'load')

    def test_both_frameworks_know_how_to_wait(self):
        pw = emit.FRAMEWORK_RULES['playwright-ts']
        rf = emit.FRAMEWORK_RULES['robot-selenium']
        self.assertIn('waitForLoadState', pw)
        self.assertIn('page_ready', pw)
        self.assertIn('nav_to', pw)
        self.assertIn('readyState', rf)
        self.assertIn('Wait Until Location Contains', rf)


class ReplayReadyTest(unittest.TestCase):
    class Page:
        def __init__(self):
            self.calls = []

        def wait_for_load_state(self, state=None, timeout=None):
            self.calls.append((state, timeout))

    def test_replay_waits_with_the_state_and_timeout_from_the_step(self):
        page = self.Page()
        replay._assert_once({'type': 'page_ready', 'state': 'domcontentloaded', 'timeout': 20},
                            None, page, 'playwright-ts', 20000)
        self.assertEqual(page.calls, [('domcontentloaded', 20000)])

    def test_replay_defaults_to_load(self):
        page = self.Page()
        replay._assert_once({'type': 'page_ready', 'timeout': 15}, None, page, 'playwright-ts', 15000)
        self.assertEqual(page.calls, [('load', 15000)])


if __name__ == '__main__':
    unittest.main()
