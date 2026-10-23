"""轮询等待：最多等 timeout 秒，期间可以反复刷新页面。

对标的是「点了重启，每 5 秒刷新一次，直到出现运行中，最多等 6 分钟」这类场景。
它和普通断言的区别是「暂时不成立」在预期之内，只有到点还没成立才算失败，
所以这里两类用例都要有：能等到、以及等到超时后报的是「轮询超时」而不是「元素不存在」。
"""
import http.server
import threading
import time
import unittest
from pathlib import Path

from app.services.recording import intent as intent_mod
from app.services.recording import ir
from app.services.recording.emit import FRAMEWORK_RULES
from app.services.recording.replay import replay_checked

# 元素 2 秒后才出现：模拟「页面自己会更新」，靠等待就能拿到，默认超时也够。
LAZY_PAGE = (
    '<!doctype html><html><head><meta charset="utf-8"></head><body>'
    '<h1>异步页</h1>'
    '<div id="ready" style="display:none">系统运行中</div>'
    '<script>setTimeout(function () { document.getElementById("ready").style.display = "block"; }, 2000);</script>'
    '</body></html>'
)

# 6 秒才出现：故意超过 5 秒的默认断言窗口，用来证明 timeout 真的被写进了等待里，
# 而不是「碰巧默认值就够」。
SLOW_PAGE = (
    '<!doctype html><html><head><meta charset="utf-8"></head><body>'
    '<h1>慢异步页</h1>'
    '<div id="ready" style="display:none">系统运行中</div>'
    '<script>setTimeout(function () { document.getElementById("ready").style.display = "block"; }, 6000);</script>'
    '</body></html>'
)

# 前两次请求都是「重启中」，第三次开始才出现判定元素：模拟「必须刷新才知道结果」。
REBOOT_PAGE_READY = (
    '<!doctype html><html><head><meta charset="utf-8"></head><body>'
    '<h1>设备</h1><div id="ready">系统运行中</div>'
    '</body></html>'
)
REBOOT_PAGE_BUSY = (
    '<!doctype html><html><head><meta charset="utf-8"></head><body>'
    '<h1>设备</h1><div id="busy">正在重启，请稍候</div>'
    '</body></html>'
)
NEVER_PAGE = (
    '<!doctype html><html><head><meta charset="utf-8"></head><body>'
    '<h1>设备</h1><div id="busy">一直重启中</div>'
    '</body></html>'
)

STATE = {'boot': 0, 'never': 0}


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split('?')[0]
        if path == '/lazy':
            body = LAZY_PAGE
        elif path == '/slow':
            body = SLOW_PAGE
        elif path == '/device':
            STATE['boot'] += 1
            body = REBOOT_PAGE_READY if STATE['boot'] >= 3 else REBOOT_PAGE_BUSY
        elif path == '/never':
            STATE['never'] += 1
            body = NEVER_PAGE
        else:
            self.send_response(404)
            self.end_headers()
            return
        data = body.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def _start_server():
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, 'http://127.0.0.1:%d' % server.server_address[1]


def _target(css, alias='判定元素'):
    """手工造一个和录制产物同形状的 target：回放只认 candidates/primary 里的 spec。"""
    spec = {'method': 'locator', 'args': [css], 'kwargs': {}}
    cand = {'kind': 'css', 'desc': 'css=%s' % css, 'count': 1, 'index': None, 'spec': spec}
    return {'alias': alias, 'snap': {}, 'candidates': [cand], 'primary': cand, 'status': 'unique'}


def _steps(url, wait_intent, css='#ready'):
    return [
        {'no': 1, 'type': 'goto', 'url': url, 'page_index': 0, 'target': None, 'intent': None},
        {'no': 2, 'type': 'wait', 'page_index': 0, 'target': _target(css), 'intent': wait_intent},
    ]


def _wait_kind(**kwargs):
    base = {'type': 'wait_visible', 'target': '判定元素', 'value': None, 'seconds': None,
            'timeout': None, 'interval': None, 'reload': False}
    base.update(kwargs)
    return base


class PollIntentValidateTest(unittest.TestCase):
    """字段校验不启浏览器：这些错要在插入前就拦住，不能等回放才暴露。"""

    def validate(self, obj):
        return intent_mod.validate(obj, ['判定元素'])

    def test_plain_wait_keeps_old_behavior(self):
        result = self.validate({'type': 'wait_visible', 'target': '判定元素'})
        self.assertIsNone(result['timeout'])
        self.assertIsNone(result['interval'])
        self.assertFalse(result['reload'])

    def test_timeout_accepted(self):
        result = self.validate({'type': 'wait_visible', 'target': '判定元素', 'timeout': 360})
        self.assertEqual(result['timeout'], 360)

    def test_reload_needs_timeout(self):
        # 每 5 秒刷一次却只等框架默认的 5 秒，等于没等，所以要直接拒绝。
        with self.assertRaises(intent_mod.IntentError):
            self.validate({'type': 'wait_visible', 'target': '判定元素', 'reload': True})

    def test_interval_defaults_when_reload(self):
        result = self.validate({'type': 'wait_visible', 'target': '判定元素',
                                'timeout': 360, 'reload': True})
        self.assertEqual(result['interval'], 5)

    def test_interval_cannot_exceed_timeout(self):
        with self.assertRaises(intent_mod.IntentError):
            self.validate({'type': 'wait_visible', 'target': '判定元素',
                           'timeout': 10, 'interval': 30, 'reload': True})

    def test_timeout_range(self):
        # 0 不算越界：模型常用 0 表示「不等待」，等价于没填（见下一条用例）。
        for bad in (-1, intent_mod.MAX_WAIT_SECONDS + 1):
            with self.assertRaises(intent_mod.IntentError):
                self.validate({'type': 'wait_visible', 'target': '判定元素', 'timeout': bad})
        with self.assertRaises(intent_mod.IntentError):
            self.validate({'type': 'wait_visible', 'target': '判定元素', 'timeout': 'abc'})

    def test_zero_timeout_means_no_wait(self):
        """模型返回 timeout: 0 是「别等」，不是非法值，不该弹一条看不懂的报错。"""
        result = self.validate({'type': 'visible', 'target': '判定元素', 'timeout': 0})
        self.assertIsNone(result['timeout'])
        # 但勾了刷新就必须给真超时，0 秒等于没等。
        with self.assertRaises(intent_mod.IntentError):
            self.validate({'type': 'visible', 'target': '判定元素', 'timeout': 0, 'reload': True})

    def test_interval_range(self):
        for bad in (0, intent_mod.MAX_POLL_INTERVAL + 1):
            with self.assertRaises(intent_mod.IntentError):
                self.validate({'type': 'wait_visible', 'target': '判定元素',
                               'timeout': 360, 'interval': bad, 'reload': True})

    def test_sleep_ignores_poll_fields(self):
        # 固定等待没有条件可判断，模型多给了这三个字段也不能悄悄生效。
        result = self.validate({'type': 'sleep', 'seconds': 1, 'timeout': 100, 'reload': True})
        self.assertIsNone(result['timeout'])
        self.assertFalse(result['reload'])

    def test_any_condition_can_carry_timeout(self):
        """合并后等待不再是独立类型：八种条件都能带最长等待，不再只有 wait_* 能用。"""
        cases = (
            ('visible', {}),
            ('text_contains', {'value': '运行中'}),
            ('url_contains', {'value': '/device'}),
            ('page_text_contains', {'value': '完成'}),
        )
        for kind, extra in cases:
            obj = dict({'type': kind, 'target': '判定元素', 'timeout': 100}, **extra)
            result = self.validate(obj)
            self.assertEqual(result['timeout'], 100, kind)
            self.assertEqual(result['type'], kind)

    def test_step_type_follows_timeout(self):
        """有没有 timeout 决定它是立即断言还是等待，前端不再让用户选两次。"""
        self.assertEqual(ir.step_type_for_intent('visible', {'timeout': None}), 'assert')
        self.assertEqual(ir.step_type_for_intent('visible', {'timeout': 30}), 'wait')
        self.assertEqual(ir.step_type_for_intent('visible', {}), 'assert')
        # 老类型仍然映射到 wait，历史数据不受影响。
        self.assertEqual(ir.step_type_for_intent('wait_visible'), 'wait')
        self.assertEqual(ir.step_type_for_intent('sleep'), 'sleep')

    def test_apply_poll_back_and_forth(self):
        """就地改：30 秒 → 立即断言 → 再改回 60 秒，类型跟着在 wait / assert 之间切。"""
        intent = self.validate({'type': 'visible', 'target': '判定元素', 'timeout': 30})
        self.assertEqual(ir.step_type_for_intent(intent['type'], intent), 'wait')

        off = intent_mod.apply_poll(intent, {'timeout': None, 'reload': False})
        self.assertIsNone(off['timeout'])
        self.assertEqual(ir.step_type_for_intent(off['type'], off), 'assert')

        back = intent_mod.apply_poll(off, {'timeout': 60, 'reload': True, 'interval': 5})
        self.assertEqual(back['timeout'], 60)
        self.assertTrue(back['reload'])
        self.assertEqual(ir.step_type_for_intent(back['type'], back), 'wait')

    def test_apply_poll_rejects_reload_without_timeout(self):
        intent = self.validate({'type': 'visible', 'target': '判定元素', 'timeout': 30})
        with self.assertRaises(intent_mod.IntentError):
            intent_mod.apply_poll(intent, {'timeout': None, 'reload': True})

    def test_prompt_explains_poll_fields(self):
        prompt = intent_mod.build_prompt('等重启完成，最多 360 秒，每 5 秒刷新一次', ['判定元素'])
        for word in ('timeout', 'interval', 'reload'):
            self.assertIn(word, prompt)


class PollReplayTest(unittest.TestCase):
    """真实浏览器：等待、刷新轮询、超时三种走向。"""

    @classmethod
    def setUpClass(cls):
        cls.server, cls.base = _start_server()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        STATE['boot'] = 0
        STATE['never'] = 0

    def test_wait_with_timeout_outlasts_default(self):
        # 元素 6 秒后才出现。默认窗口（5 秒）不够，只有 timeout 真的生效才会通过，
        # 所以这条用例不是「碰巧能过」，而是直接证明 timeout 被写进了等待。
        report = replay_checked(_steps(self.base + '/slow', _wait_kind(timeout=10)))
        self.assertTrue(report['ok'], report.get('error'))

    def test_wait_without_timeout_fails_on_slow_page(self):
        # 同一张慢页面不给 timeout：必须失败，否则说明超时参数根本没被用上。
        report = replay_checked(_steps(self.base + '/slow', _wait_kind()))
        self.assertFalse(report['ok'])

    def test_poll_with_reload_until_visible(self):
        report = replay_checked(_steps(self.base + '/device', _wait_kind(
            timeout=20, interval=1, reload=True)))
        self.assertTrue(report['ok'], report.get('error'))
        # 前两次是「重启中」，必须真的刷到第三次才可能看见。
        self.assertGreaterEqual(STATE['boot'], 3)

    def test_poll_reports_attempts(self):
        report = replay_checked(_steps(self.base + '/device', _wait_kind(
            timeout=20, interval=1, reload=True)))
        step = next(s for s in report['steps'] if s['no'] == 2)
        self.assertIsNotNone(step['poll'])
        self.assertGreaterEqual(step['poll']['attempts'], 2)
        self.assertTrue(step['poll']['reload'])
        self.assertGreaterEqual(step['poll']['elapsed'], 0)

    def test_poll_timeout_says_polling_not_missing(self):
        # 元素从头到尾没出现：报错必须指向「轮询超时」，而不是「元素不存在」，
        # 否则用户会去改定位，实际问题在设备没起来。
        started = time.monotonic()
        report = replay_checked(_steps(self.base + '/never', _wait_kind(
            timeout=3, interval=1, reload=True)))
        elapsed = time.monotonic() - started
        self.assertFalse(report['ok'])
        self.assertIn('轮询等待', report['error'])
        self.assertIn('3', report['error'])
        self.assertGreaterEqual(elapsed, 2.5)
        self.assertLess(elapsed, 12)

    def test_plain_wait_without_timeout_still_works(self):
        report = replay_checked(_steps(self.base + '/lazy', _wait_kind()))
        self.assertTrue(report['ok'], report.get('error'))


class PollPromptTest(unittest.TestCase):
    """生成侧：两套框架的写法都要落到提示词里。

    这两处以前踩过坑——只改了 emit.py 的规则，真正生效的 .opencode 提示词没改，
    结果生成出来的脚本完全没有轮询。所以两边一起断言。
    """

    def test_framework_rules_cover_poll(self):
        self.assertIn('expect.poll', FRAMEWORK_RULES['playwright-ts'])
        self.assertIn('Wait Until Keyword Succeeds', FRAMEWORK_RULES['robot-selenium'])
        self.assertIn('interval', FRAMEWORK_RULES['robot-selenium'])

    def test_opencode_prompt_cover_poll(self):
        prompt = Path(__file__).resolve().parent.parent / '.opencode' / 'prompts' / 'recording-emitter.md'
        text = prompt.read_text(encoding='utf-8')
        self.assertIn('expect.poll', text)
        self.assertIn('Wait Until Keyword Succeeds', text)


if __name__ == '__main__':
    unittest.main()
