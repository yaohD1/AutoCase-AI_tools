"""验证码步骤与求解器的单元测试。

真实识别要浏览器和视觉模型，这里只覆盖「不依赖网络」的部分：
步骤结构、spec 投影、回话解析、参数边界、定位启发式、重试分支、生成注入。
浏览器 + 真实站点的链路另有用 probe 跑过的验证（见 .probe/captcha）。
"""

import io
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from app.services.recording import captcha, emit, ir, replay, session, spec_render


def _png(width=120, height=40):
    buf = io.BytesIO()
    Image.new('RGB', (width, height), (245, 245, 245)).save(buf, format='PNG')
    return buf.getvalue()


def _item(selector='.cap'):
    return {'el': mock.Mock(), 'box': {'x': 10, 'y': 20, 'width': 120, 'height': 40},
            'selector': selector}


class StepTypeTest(unittest.TestCase):
    def test_captcha_is_a_step_type(self):
        self.assertIn('captcha', ir.STEP_TYPES)

    def test_make_step_carries_captcha_config(self):
        step = ir.make_step('captcha', no=4, source='manual',
                            captcha={'selector': '#code', 'charset': 'digits', 'length': 6})
        self.assertEqual(step['type'], 'captcha')
        self.assertEqual(step['captcha']['selector'], '#code')
        self.assertEqual(step['captcha']['length'], 6)

    def test_other_steps_do_not_carry_the_field(self):
        """别的步骤多一个 null 会让前端判断和提示词都变脏。"""
        step = ir.make_step('click', no=1, target={'alias': 'x'})
        self.assertNotIn('captcha', step)

    def test_captcha_has_no_target(self):
        """它不是「点一个元素」，而是「识别 + 填入」两段流程。"""
        step = ir.make_step('captcha', no=9, source='manual', captcha={})
        self.assertIsNone(step['target'])


class SpecRenderTest(unittest.TestCase):
    def test_captcha_is_placeholder_not_a_fake_call(self):
        """投影里不能写 solveCaptcha：沙箱里没那个函数，healer 会去修一个假报错。"""
        rendered = spec_render.render([
            {'no': 1, 'type': 'goto', 'url': 'http://x/'},
            {'no': 2, 'type': 'captcha', 'captcha': {'selector': '#code'}},
        ])
        self.assertIn('验证码', rendered['code'])
        self.assertNotIn('solveCaptcha', rendered['code'])
        self.assertTrue(any('验证码' in item for item in rendered['unsupported']))


class CleanTest(unittest.TestCase):
    def test_plain_digits(self):
        self.assertEqual(captcha._clean('449158'), '449158')

    def test_digits_split_by_spaces(self):
        """模型常把 6 位写成 2+2+2，不去空白会只剩最后一段。"""
        self.assertEqual(captcha._clean('4 2 6 8 3 1'), '426831')

    def test_digits_with_trailing_count(self):
        """「449158（6 位）」不能取成 6。"""
        self.assertEqual(captcha._clean('449158（6位）'), '449158')
        self.assertEqual(captcha._clean('449158（6位）', length=6), '449158')

    def test_digits_prefers_declared_length(self):
        # 解释里的 1 位数字不该抢走答案
        self.assertEqual(captcha._clean('第1步：4231', length=4), '4231')

    def test_prose_wrapped(self):
        """charset 决定切分方式：3a9F 是字母数字码，用 digits 切只会拿到 3。"""
        self.assertEqual(captcha._clean('图中的验证码是 3a9F', 'alnum'), '3a9F')

    def test_alnum_skips_english_words(self):
        self.assertEqual(captcha._clean('The code is a3B9', 'alnum'), 'a3B9')

    def test_alnum_pure_letters(self):
        """字母码不含数字时也要能取到，不能因为「没有数字」返回空。"""
        self.assertEqual(captcha._clean('验证码为 abcd', 'alnum'), 'abcd')

    def test_alnum_with_count(self):
        self.assertEqual(captcha._clean('a3B9（4 位）', 'alnum', 4), 'a3B9')

    def test_any_keeps_chinese(self):
        self.assertEqual(captcha._clean('结果是 中文 二字', 'any'), '结果是中文二字')

    def test_alnum_letters_split_by_spaces(self):
        """模型按位拆开写字母码时要拼回去。"""
        self.assertEqual(captcha._clean('a b c d', 'alnum'), 'abcd')
        self.assertEqual(captcha._clean('a b c d', 'alnum', 4), 'abcd')

    def test_count_guard_prevents_over_join(self):
        """「4 2 6 8（6 位）」拼起来是 4268+6，长度对不上就不该拼。"""
        self.assertEqual(captcha._clean('4 2 6 8（6位）', 'digits', 6), '4268')

    def test_empty(self):
        for raw in ('', None, '看不清'):
            self.assertEqual(captcha._clean(raw), '')


class SizeTest(unittest.TestCase):
    def test_reject_tiny(self):
        page = mock.Mock()
        el = mock.Mock()
        el.is_visible.return_value = True
        el.bounding_box.return_value = {'x': 0, 'y': 0, 'width': 8, 'height': 5}
        page.query_selector_all.return_value = [el]
        self.assertIsNone(captcha.find_captcha(page, '.cap'))

    def test_reject_oversized_pick_with_clear_message(self):
        """拾取到登录卡片时要报错让人重拾，而不是截一张大图去问模型。"""
        page = mock.Mock()
        el = mock.Mock()
        el.is_visible.return_value = True
        el.bounding_box.return_value = {'x': 0, 'y': 0, 'width': 1440, 'height': 900}
        el.query_selector_all.return_value = []
        page.query_selector_all.return_value = [el]
        with self.assertRaises(captcha.CaptchaError) as ctx:
            captcha.find_captcha(page, '.login-card')
        self.assertIn('比验证码大太多', str(ctx.exception))

    def test_container_resolves_to_inner_image(self):
        """拾取到容器时取内部那张图，否则会把输入框、刷新按钮一起截进去。"""
        page = mock.Mock()
        outer = mock.Mock()
        outer.is_visible.return_value = True
        outer.bounding_box.return_value = {'x': 0, 'y': 0, 'width': 400, 'height': 300}
        inner = mock.Mock()
        inner.bounding_box.return_value = {'x': 10, 'y': 10, 'width': 120, 'height': 40}
        outer.query_selector_all.side_effect = lambda sel: [inner] if sel == 'img' else []
        page.query_selector_all.return_value = [outer]
        found = captcha.find_captcha(page, '.captcha-box')
        self.assertIsNotNone(found)
        self.assertIs(found['el'], inner)

    def test_bad_selector_gives_readable_error(self):
        page = mock.Mock()
        page.query_selector_all.side_effect = RuntimeError('not a valid selector')
        with self.assertRaises(captcha.CaptchaError) as ctx:
            captcha.find_captcha(page, 'div[')
        self.assertIn('选择器不可用', str(ctx.exception))


class SolveTest(unittest.TestCase):
    def _patch(self, code='123456', length=6, input_el=None):
        """把外部依赖都换掉，只留 solve 自己的控制流。"""
        return [
            mock.patch.object(captcha, 'find_captcha', return_value=_item()),
            mock.patch.object(captcha, 'wait_ready', return_value=True),
            mock.patch.object(captcha, 'capture', return_value=_png()),
            mock.patch.object(captcha, 'ask_model', return_value=(code, 'fake')),
            mock.patch.object(captcha, 'find_input', return_value=input_el or mock.Mock()),
            mock.patch.object(captcha, 'fill_code', side_effect=lambda el, c: c),
        ]

    def test_success_path(self):
        patches = self._patch()
        for p in patches:
            p.start()
        try:
            result = captcha.solve(mock.Mock(), {'selector': '.cap', 'length': 6})
        finally:
            for p in patches:
                p.stop()
        self.assertTrue(result['ok'])
        self.assertEqual(result['code'], '123456')
        self.assertEqual(result['attempt'], 1)
        self.assertEqual(result['size'], '120×40')
        self.assertEqual(len(result['reads']), 1)

    def test_wrong_length_retries_then_succeeds(self):
        """位数不符属于「识别失败」，要刷新重试，而不是把 3 位码填进去。"""
        page = mock.Mock()
        with mock.patch.object(captcha, 'find_captcha', return_value=_item()), \
                mock.patch.object(captcha, 'wait_ready', return_value=True), \
                mock.patch.object(captcha, 'capture', return_value=_png()), \
                mock.patch.object(captcha, 'ask_model', side_effect=[('123', 'f'), ('123456', 'f')]), \
                mock.patch.object(captcha, 'refresh', return_value=(_item(), _png())), \
                mock.patch.object(captcha, 'find_input', return_value=mock.Mock()), \
                mock.patch.object(captcha, 'fill_code', side_effect=lambda el, c: c):
            result = captcha.solve(page, {'selector': '.cap', 'length': 6, 'attempts': 2})
        self.assertEqual(result['code'], '123456')
        self.assertEqual(result['attempt'], 2)
        self.assertEqual([r['code'] for r in result['reads']], ['123', '123456'])

    def test_refresh_replaces_element(self):
        """刷新后元素可能被换掉，第二轮必须用新元素截图。"""
        fresh = _item('.cap')
        page = mock.Mock()
        with mock.patch.object(captcha, 'find_captcha', return_value=_item()), \
                mock.patch.object(captcha, 'wait_ready', return_value=True), \
                mock.patch.object(captcha, 'capture', return_value=_png()), \
                mock.patch.object(captcha, 'ask_model', side_effect=[('', 'f'), ('123456', 'f')]), \
                mock.patch.object(captcha, 'refresh', return_value=(fresh, _png())) as refresh, \
                mock.patch.object(captcha, 'find_input', return_value=mock.Mock()), \
                mock.patch.object(captcha, 'fill_code', side_effect=lambda el, c: c):
            result = captcha.solve(page, {'selector': '.cap', 'attempts': 1})
        self.assertTrue(result['ok'])
        refresh.assert_called_once()

    def test_gives_up_after_attempts(self):
        page = mock.Mock()
        with mock.patch.object(captcha, 'find_captcha', return_value=_item()), \
                mock.patch.object(captcha, 'wait_ready', return_value=True), \
                mock.patch.object(captcha, 'capture', return_value=_png()), \
                mock.patch.object(captcha, 'ask_model', return_value=('', 'f')), \
                mock.patch.object(captcha, 'refresh', return_value=(_item(), _png())):
            with self.assertRaises(captcha.CaptchaError) as ctx:
                captcha.solve(page, {'selector': '.cap', 'attempts': 1})
        self.assertIn('已尝试 2 次', str(ctx.exception))

    def test_refresh_failure_stops_early(self):
        page = mock.Mock()
        with mock.patch.object(captcha, 'find_captcha', return_value=_item()), \
                mock.patch.object(captcha, 'wait_ready', return_value=True), \
                mock.patch.object(captcha, 'capture', return_value=_png()), \
                mock.patch.object(captcha, 'ask_model', return_value=('', 'f')), \
                mock.patch.object(captcha, 'refresh', return_value=(None, None)):
            with self.assertRaises(captcha.CaptchaError) as ctx:
                captcha.solve(page, {'selector': '.cap', 'attempts': 3})
        self.assertIn('刷新验证码失败', str(ctx.exception))

    def test_missing_input_raises_with_hint(self):
        page = mock.Mock()
        with mock.patch.object(captcha, 'find_captcha', return_value=_item()), \
                mock.patch.object(captcha, 'wait_ready', return_value=True), \
                mock.patch.object(captcha, 'capture', return_value=_png()), \
                mock.patch.object(captcha, 'ask_model', return_value=('123456', 'f')), \
                mock.patch.object(captcha, 'find_input', return_value=None):
            with self.assertRaises(captcha.CaptchaError) as ctx:
                captcha.solve(page, {'selector': '.cap'})
        self.assertIn('请拾取一次输入框', str(ctx.exception))

    def test_fill_mismatch_raises(self):
        """填进去的值和读回来的不一致时要报错，不能当成功。"""
        page = mock.Mock()
        with mock.patch.object(captcha, 'find_captcha', return_value=_item()), \
                mock.patch.object(captcha, 'wait_ready', return_value=True), \
                mock.patch.object(captcha, 'capture', return_value=_png()), \
                mock.patch.object(captcha, 'ask_model', return_value=('123456', 'f')), \
                mock.patch.object(captcha, 'find_input', return_value=mock.Mock()), \
                mock.patch.object(captcha, 'fill_code', return_value='12345'):
            with self.assertRaises(captcha.CaptchaError) as ctx:
                captcha.solve(page, {'selector': '.cap'})
        self.assertIn('没能填进输入框', str(ctx.exception))

    def test_missing_selector_message_is_actionable(self):
        page = mock.Mock()
        with mock.patch.object(captcha, 'find_captcha', return_value=None):
            with self.assertRaises(captcha.CaptchaError) as ctx:
                captcha.solve(page, {})
        self.assertIn('请拾取一次验证码元素', str(ctx.exception))

    def test_on_tick_called_before_each_read(self):
        """一次识别 6～8 秒，回放要靠 on_tick 出帧，否则画面像卡死。"""
        page = mock.Mock()
        ticks = []
        with mock.patch.object(captcha, 'find_captcha', return_value=_item()), \
                mock.patch.object(captcha, 'wait_ready', return_value=True), \
                mock.patch.object(captcha, 'capture', return_value=_png()), \
                mock.patch.object(captcha, 'ask_model', side_effect=[('', 'f'), ('123456', 'f')]), \
                mock.patch.object(captcha, 'refresh', return_value=(_item(), _png())), \
                mock.patch.object(captcha, 'find_input', return_value=mock.Mock()), \
                mock.patch.object(captcha, 'fill_code', side_effect=lambda el, c: c):
            captcha.solve(page, {'selector': '.cap', 'attempts': 1},
                          on_tick=lambda: ticks.append(1))
        self.assertEqual(len(ticks), 2)


class EnlargeTest(unittest.TestCase):
    def test_triples_the_image(self):
        big, width, height = captcha.enlarge(_png(120, 40))
        self.assertEqual((width, height), (120, 40))
        img = Image.open(io.BytesIO(big))
        self.assertEqual(img.size, (360, 120))

    def test_scale_is_three(self):
        """3 倍是实测最优：原图 7/10、3 倍 10/10、4 倍 9/10。改之前先看模块注释。"""
        self.assertEqual(captcha.SCALE, 3)


class DescribeTest(unittest.TestCase):
    def test_readable(self):
        text = captcha.describe({'code': '123456', 'size': '120×40', 'attempt': 1,
                                 'elapsed': 6.4})
        self.assertIn('123456', text)
        self.assertIn('120×40', text)

    def test_long_code_truncated(self):
        text = captcha.describe({'code': '1234567890', 'size': '1×1', 'attempt': 1,
                                 'elapsed': 1})
        self.assertIn('…', text)


class SessionTest(unittest.TestCase):
    def _bare_session(self):
        s = session.RecordingSession.__new__(session.RecordingSession)
        s._lock = threading.RLock()
        return s

    def test_add_captcha_forwards_the_command(self):
        s = self._bare_session()
        with mock.patch.object(s, '_call') as call:
            s.add_captcha_step({'selector': '#c'}, after_no=3)
        call.assert_called_once_with('captcha', config={'selector': '#c'}, after_no=3)

    def test_values_are_clamped(self):
        """前端传什么都不能让一次回放问十几次模型、或把 length 设成 999。"""
        s = self._bare_session()
        captured = {}

        def fake_insert(step, after_no):
            captured['step'] = step

        with mock.patch.object(s, '_current_page', return_value=None), \
                mock.patch.object(s, '_insert_step', side_effect=fake_insert), \
                mock.patch.object(s, '_renumber'), \
                mock.patch.object(s, '_schedule_hud'), \
                mock.patch.object(s, 'snapshot', return_value={}):
            s._add_captcha({'attempts': 99, 'length': 99, 'timeout': 1}, None)
        conf = captured['step']['captcha']
        self.assertEqual(conf['attempts'], 5)
        self.assertEqual(conf['length'], 12)
        self.assertEqual(conf['timeout'], 5)

    def test_selector_length_limit(self):
        """拾取的绝对路径可能上百字符，200 字会截断成非法选择器。"""
        s = self._bare_session()
        captured = {}
        long_selector = 'div:nth-child(2) > ' * 30 + 'img'

        with mock.patch.object(s, '_current_page', return_value=None), \
                mock.patch.object(s, '_insert_step',
                                  side_effect=lambda step, after: captured.update(step=step)), \
                mock.patch.object(s, '_renumber'), \
                mock.patch.object(s, '_schedule_hud'), \
                mock.patch.object(s, 'snapshot', return_value={}):
            s._add_captcha({'selector': long_selector}, None)
        self.assertEqual(len(captured['step']['captcha']['selector']), 500)

    def test_step_title_registered(self):
        self.assertEqual(session.STEP_TITLES.get('captcha'), '验证码识别')


class ReplayRunStepTest(unittest.TestCase):
    def test_run_step_calls_solver(self):
        """回放到 captcha 步骤时必须调求解器，并把结果带回去。"""
        step = {'no': 3, 'type': 'captcha', 'captcha': {'selector': '.cap', 'length': 6}}
        outcome = {'ok': True, 'code': '123456', 'attempt': 1, 'size': '120×40',
                   'elapsed': 6.0, 'reads': []}
        with mock.patch.object(replay.captcha, 'solve', return_value=outcome) as solve:
            chosen, poll = replay._run_step(step, mock.Mock())
        self.assertIsNone(chosen)
        self.assertEqual(poll['captcha']['code'], '123456')
        solve.assert_called_once()

    def test_run_step_wraps_error_with_step_context(self):
        step = {'no': 3, 'type': 'captcha', 'captcha': {}}
        with mock.patch.object(replay.captcha, 'solve',
                               side_effect=captcha.CaptchaError('模型没配')):
            with self.assertRaises(replay.ReplayError) as ctx:
                replay._run_step(step, mock.Mock())
        self.assertIn('模型没配', str(ctx.exception))


class ReplayTest(unittest.TestCase):
    def test_entry_carries_captcha_detail(self):
        step = {'no': 4, 'type': 'captcha', 'captcha': {'charset': 'digits', 'length': 6}}
        entry = replay._entry(step, None, True, poll={'captcha': {'code': '123456'}})
        self.assertEqual(entry['captcha']['code'], '123456')
        # 不能塞进 poll：前端把 poll 当「等了多久 / 刷新几次」渲染
        self.assertIsNone(entry['poll'])

    def test_label_mentions_length(self):
        step = {'no': 4, 'type': 'captcha',
                'captcha': {'charset': 'digits', 'length': 6, 'selector': '#c'}}
        label = replay._step_label(step)
        self.assertIn('数字', label)
        self.assertIn('6 位', label)

    def test_label_mentions_auto_find(self):
        step = {'no': 4, 'type': 'captcha', 'captcha': {'selector': ''}}
        self.assertIn('自动找图', replay._step_label(step))


class EmitTest(unittest.TestCase):
    def test_utils_written_when_needed(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'tests').mkdir()
            rel = emit._ensure_captcha_utils(tmp, 'tests/login.spec.ts',
                                             [{'type': 'captcha'}], 'playwright-ts')
            self.assertEqual(rel, 'tests/utils/captcha.ts')
            written = (Path(tmp) / 'tests' / 'utils' / 'captcha.ts').read_text(encoding='utf-8')
            self.assertIn('solveCaptcha', written)
            # 放大必须在浏览器里做：目标项目没有 PIL，也没有 sharp
            self.assertIn('canvas', written)

    def test_no_file_without_captcha_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'tests').mkdir()
            rel = emit._ensure_captcha_utils(tmp, 'tests/login.spec.ts',
                                             [{'type': 'click'}], 'playwright-ts')
            self.assertIsNone(rel)
            self.assertFalse((Path(tmp) / 'tests' / 'utils' / 'captcha.ts').exists())

    def test_robot_framework_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            rel = emit._ensure_captcha_utils(tmp, 'tests/login.robot',
                                             [{'type': 'captcha'}], 'robot-selenium')
            self.assertIsNone(rel)

    def test_payload_carries_config(self):
        step = {'no': 2, 'type': 'captcha', 'captcha': {'selector': '#c', 'length': 6}}
        payload = emit._payload('playwright-ts', [step], 'tests/x.spec.ts', 't', {},
                                [], {}, {})
        item = [s for s in payload['steps'] if s['type'] == 'captcha'][0]
        self.assertEqual(item['captcha']['selector'], '#c')


if __name__ == '__main__':
    unittest.main()
