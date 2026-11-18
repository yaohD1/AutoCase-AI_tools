"""滑块步骤与求解器的单元测试。

真实拖动需要浏览器和视觉模型，这里只覆盖「不依赖网络」的部分：
步骤结构、spec 投影、坐标解析、参数边界、模型调用的错误处理。
浏览器侧的链路另有用真实站点跑过的验证（见 .probe 与提交说明）。
"""

import io
import json
import unittest
from unittest import mock

from PIL import Image

from app.services.recording import ir, slider, spec_render


class StepTypeTest(unittest.TestCase):
    def test_slider_is_a_step_type(self):
        self.assertIn('slider', ir.STEP_TYPES)

    def test_make_step_carries_slider_config(self):
        step = ir.make_step('slider', no=3, source='manual',
                            slider={'selector': '.vue-puzzle-vcode', 'attempts': 1,
                                    'timeout': 30})
        self.assertEqual(step['type'], 'slider')
        self.assertEqual(step['slider']['selector'], '.vue-puzzle-vcode')
        self.assertEqual(step['slider']['attempts'], 1)

    def test_other_steps_do_not_carry_the_field(self):
        """别的步骤多一个 null 会让前端判断和提示词都变脏。"""
        step = ir.make_step('click', no=1, target={'alias': 'x'})
        self.assertNotIn('slider', step)

    def test_slider_has_no_target(self):
        step = ir.make_step('slider', no=9, source='manual', slider={})
        self.assertIsNone(step['target'])


class SpecRenderTest(unittest.TestCase):
    def test_slider_is_placeholder_not_a_fake_call(self):
        """投影里不能写 solveSlider：沙箱里没有那个函数，healer 会去修一个假报错。"""
        rendered = spec_render.render([
            {'no': 1, 'type': 'goto', 'url': 'http://x/'},
            {'no': 2, 'type': 'slider', 'slider': {'selector': ''}},
        ])
        self.assertIn('滑块验证', rendered['code'])
        self.assertNotIn('solveSlider', rendered['code'])
        self.assertTrue(any('滑块' in item for item in rendered['unsupported']))


class ParseTest(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(slider._parse_x('{"x": 174}'), 174)

    def test_json_inside_prose(self):
        self.assertEqual(slider._parse_x('好的，结果是 {"x": 87} 。'), 87)

    def test_bare_pair(self):
        self.assertEqual(slider._parse_x('"x": 12'), 12)

    def test_nothing(self):
        self.assertIsNone(slider._parse_x('看不清'))
        self.assertIsNone(slider._parse_x(''))


class GeometryTest(unittest.TestCase):
    def test_plausible_sizes(self):
        self.assertTrue(slider._plausible({'width': 310, 'height': 160}))
        self.assertFalse(slider._plausible({'width': 50, 'height': 160}))
        # 竖图不是拼图容器：拼图一定是横的
        self.assertFalse(slider._plausible({'width': 160, 'height': 310}))

    def test_enlarge_doubles(self):
        img = Image.new('RGB', (310, 160), (10, 20, 30))
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        png, width, height = slider._enlarge(buf.getvalue())
        self.assertEqual((width, height), (310, 160))
        self.assertEqual(Image.open(io.BytesIO(png)).size, (620, 320))


class ModelCallTest(unittest.TestCase):
    def _png(self):
        buf = io.BytesIO()
        Image.new('RGB', (310, 160), (90, 110, 130)).save(buf, format='PNG')
        return buf.getvalue()

    @mock.patch('app.services.recording.slider.requests.post')
    def test_reads_x_from_content(self, post):
        post.return_value = mock.Mock(status_code=200, json=lambda: {
            'choices': [{'message': {'content': '{"x": 348}'}}]})
        x, note = slider.ask_model(self._png(), 310, 160, ref='doubao-vision/ark-code-latest')
        # 模型按放大 2 倍的图回答，换算回原图
        self.assertEqual(x, 174.0)
        self.assertIn('content', note)

    @mock.patch('app.services.recording.slider.requests.post')
    def test_falls_back_to_reasoning_content(self, post):
        """这个模型会先输出 reasoning，content 可能是空的。"""
        post.return_value = mock.Mock(status_code=200, json=lambda: {
            'choices': [{'message': {'content': '',
                                     'reasoning_content': '大概 {"x": 200} 吧'}}]})
        x, note = slider.ask_model(self._png(), 310, 160, ref='doubao-vision/ark-code-latest')
        self.assertEqual(x, 100.0)
        self.assertIn('reasoning', note)

    @mock.patch('app.services.recording.slider.requests.post')
    def test_out_of_range_is_rejected(self, post):
        """坐标超出图宽说明这次识别不可用，不能拿去拖。"""
        post.return_value = mock.Mock(status_code=200, json=lambda: {
            'choices': [{'message': {'content': '{"x": 9999}'}}]})
        with self.assertRaises(slider.SliderError):
            slider.ask_model(self._png(), 310, 160, ref='doubao-vision/ark-code-latest')

    @mock.patch('app.services.recording.slider.requests.post')
    def test_http_error_is_readable(self, post):
        post.return_value = mock.Mock(status_code=401, text='unauthorized')
        with self.assertRaises(slider.SliderError) as ctx:
            slider.ask_model(self._png(), 310, 160, ref='doubao-vision/ark-code-latest')
        self.assertIn('401', str(ctx.exception))

    def test_missing_config_says_where_to_fix(self):
        with mock.patch('app.services.recording.slider.models_config.slider_model',
                        return_value=''):
            with self.assertRaises(slider.SliderError) as ctx:
                slider.ask_model(self._png(), 310, 160)
        self.assertIn('设置', str(ctx.exception))


class SolveGuardTest(unittest.TestCase):
    def test_no_slider_raises_with_clear_message(self):
        page = mock.Mock()
        page.query_selector_all.return_value = []
        with self.assertRaises(slider.SliderError) as ctx:
            slider.solve(page, {'attempts': 0})
        self.assertIn('没有找到滑块', str(ctx.exception))

    def test_attempts_are_clamped(self):
        """前端传多少都不能让一次回放卡死：上限 5 次重试、单次 90 秒。"""
        page = mock.Mock()
        page.query_selector_all.return_value = []
        with self.assertRaises(slider.SliderError):
            slider.solve(page, {'attempts': 999, 'timeout': 100000})
        # 只要没抛参数异常就说明被夹住了；这里确认没有调用到模型
        self.assertFalse(page.screenshot.called)


class _FakeEl:
    def __init__(self, box=None, connected=True):
        self.box = box
        self.connected = connected

    def bounding_box(self):
        return self.box

    def evaluate(self, script, arg=None):
        return self.connected


def _scene(bg_x=100, bg_y=100, connected=True):
    """一组能骗过 _drag / _wait_result 的假场景。piece 留空，比例按 1.0 算。"""
    bg_box = {'x': bg_x, 'y': bg_y, 'width': 310, 'height': 160}
    handle_box = {'x': bg_x, 'y': bg_y + 180, 'width': 50, 'height': 50}
    return {
        'bg': {'el': _FakeEl(bg_box, connected=connected), 'box': bg_box},
        'piece': None,
        'handle': {'el': _FakeEl(handle_box), 'box': handle_box, 'selector': '.range-btn'},
    }


class _FakePage:
    """只实现 solve 用到的那几个方法。

    `fail_text` 模拟组件留下的失败提示：整个第一轮它一直在页面上
    （所以第一轮判 fail），复位后被清掉。
    """

    def __init__(self, png):
        self.mouse = mock.Mock()
        self._png = png
        self._fail_text = '验证失败'
        self.clips = []

    def screenshot(self, clip=None, path=None):
        self.clips.append(dict(clip or {}))
        return self._png

    def inner_text(self, selector='body'):
        return self._fail_text

    def wait_for_timeout(self, ms):
        self._fail_text = ''


class RetryTest(unittest.TestCase):
    """失败后的重试必须重新出题、重新识别。

    组件复位会换缺口位置，沿用旧坐标只是把同一段轨迹再交一次，
    这正是改动前那套「±2px 抖动」的问题。
    """

    def _png(self):
        buf = io.BytesIO()
        Image.new('RGB', (310, 160), (200, 200, 200)).save(buf, format='PNG')
        return buf.getvalue()

    def test_retry_asks_again_instead_of_nudging(self):
        page = _FakePage(self._png())
        first, second = _scene(connected=True), _scene(connected=False)
        with mock.patch.object(slider, 'find_scene', side_effect=[first, second]) as find, \
                mock.patch.object(slider, 'ask_model',
                                  side_effect=[(120.0, 'm1'), (205.0, 'm2')]) as ask, \
                mock.patch.object(slider.time, 'sleep', lambda *_: None):
            result = slider.solve(page, {'attempts': 1, 'timeout': 5})

        self.assertTrue(result['ok'])
        self.assertEqual(result['attempt'], 2)
        # 重新找了容器、重新截了图、重新问了模型
        self.assertEqual(find.call_count, 2)
        self.assertEqual(len(page.clips), 2)
        self.assertEqual(ask.call_count, 2)
        self.assertEqual([r['x'] for r in result['reads']], [120.0, 205.0])
        self.assertEqual(result['reads'][1]['via'], 'model')
        # 落点是新坐标，不是 120±2
        last_x = page.mouse.move.call_args_list[-1][0][0]
        self.assertAlmostEqual(last_x, 125 + 205, places=3)

    def test_disappeared_component_is_reported(self):
        """组件没了还拿旧坐标拖，_wait_result 会因为元素断开直接判通过——假绿。"""
        page = _FakePage(self._png())
        # 首次能找到，之后（含重试前的几次重查）一律找不到
        with mock.patch.object(slider, 'find_scene',
                               side_effect=[_scene()] + [None] * 6), \
                mock.patch.object(slider, 'ask_model', return_value=(120.0, 'm')), \
                mock.patch.object(slider.time, 'sleep', lambda *_: None):
            with self.assertRaises(slider.SliderError) as ctx:
                slider.solve(page, {'attempts': 2, 'timeout': 5})
        self.assertIn('消失', str(ctx.exception))

    def test_failed_reread_falls_back_to_last_x(self):
        page = _FakePage(self._png())
        with mock.patch.object(slider, 'find_scene',
                               side_effect=[_scene(), _scene(connected=False)]), \
                mock.patch.object(slider, 'ask_model',
                                  side_effect=[(120.0, 'm'), slider.SliderError('模型炸了')]), \
                mock.patch.object(slider.time, 'sleep', lambda *_: None):
            result = slider.solve(page, {'attempts': 1, 'timeout': 5})
        self.assertTrue(result['ok'])
        self.assertEqual(result['reads'][1]['via'], 'fallback')
        self.assertLessEqual(abs(result['ai_x'] - 120.0), 2)


class GeneratedTsTest(unittest.TestCase):
    """写进项目的 TS 是另一份实现，重试逻辑要一起改，否则验收时对不上。"""

    def test_ts_rereads_on_retry(self):
        from app.services.recording.slider_ts import SLIDER_TS
        self.assertIn('await waitReset(page)', SLIDER_TS)
        self.assertIn('const shot = await capture(page, scene)', SLIDER_TS)
        self.assertIn('target = nudge(target, scene.bg.width)', SLIDER_TS)
        # 旧实现是「等 1200ms 后 target±2」，那种写法里 target 不会在重试时被重新赋值
        self.assertNotIn('target + [-2, 0, 2]', SLIDER_TS)


class SceneFromElementTest(unittest.TestCase):
    """拾取给的是容器还是画布，都要能落成同一组「背景 + 拼图块 + 手柄」。"""

    HANDLE = {'x': 565, 'y': 425, 'width': 50, 'height': 50}
    CANVAS = {'x': 565, 'y': 245, 'width': 310, 'height': 160}

    def _page(self):
        page = mock.Mock()
        handle = mock.Mock()
        handle.bounding_box.return_value = self.HANDLE
        page.query_selector_all.return_value = [handle]
        return page

    def test_container_resolves_to_inner_canvas(self):
        page = self._page()
        canvas = mock.Mock()
        canvas.bounding_box.return_value = self.CANVAS
        root = mock.Mock()
        root.evaluate.return_value = False
        root.query_selector_all.return_value = [canvas]

        scene = slider.find_scene(page, root=root)
        self.assertIsNotNone(scene)
        self.assertEqual(scene['bg']['el'], canvas)
        self.assertEqual(scene['bg']['box']['width'], 310)
        self.assertEqual(scene['handle']['box']['y'], self.HANDLE['y'])

    def test_canvas_element_resolves_to_itself(self):
        page = self._page()
        canvas = mock.Mock()
        canvas.bounding_box.return_value = self.CANVAS
        canvas.evaluate.return_value = True
        canvas.query_selector_all.return_value = []

        scene = slider.find_scene(page, root=canvas)
        self.assertIsNotNone(scene)
        self.assertEqual(scene['bg']['el'], canvas)

    def test_bad_selector_is_readable(self):
        page = mock.Mock()
        page.query_selector_all.side_effect = Exception('Unexpected token')
        with self.assertRaises(slider.SliderError) as ctx:
            slider._scene_by_selector(page, '.a[')
        self.assertIn('无效', str(ctx.exception))

    def test_selector_is_tried_before_autodetect(self):
        """填了选择器就是「我要这一个」，不能先让自动识别挑最大的那个。"""
        page = mock.Mock()
        page.query_selector_all.return_value = [mock.Mock()]
        with mock.patch.object(slider, 'find_scene', return_value=None) as find:
            with self.assertRaises(slider.SliderError):
                slider.solve(page, {'attempts': 0, 'selector': '.vue-puzzle-vcode'})
        self.assertEqual(find.call_count, 2)
        self.assertIsNotNone(find.call_args_list[0].kwargs.get('root'))
        self.assertIsNone(find.call_args_list[1].kwargs.get('root'))


class ModelConfigTest(unittest.TestCase):
    def test_slider_model_never_falls_back_to_global(self):
        """全局默认可能是纯文本模型，回落过去只会得到「看不懂这张图」。"""
        from app.services import models_config
        with mock.patch.object(models_config, 'slider_model_explicit', return_value=''):
            with mock.patch.object(models_config, 'flat_models',
                                   return_value=[{'id': 'doubao-vision/ark-code-latest'}]):
                self.assertEqual(models_config.slider_model(),
                                 models_config.SLIDER_DEFAULT_REF)

    def test_builtin_disappears_with_the_provider(self):
        from app.services import models_config
        with mock.patch.object(models_config, 'slider_model_explicit', return_value=''):
            with mock.patch.object(models_config, 'flat_models',
                                   return_value=[{'id': 'other/model'}]):
                self.assertEqual(models_config.slider_model(), '')

    def test_explicit_wins(self):
        from app.services import models_config
        with mock.patch.object(models_config, 'slider_model_explicit',
                               return_value='my/vision'):
            self.assertEqual(models_config.slider_model(), 'my/vision')


class PromptTest(unittest.TestCase):
    def test_slider_config_reaches_the_writer(self):
        from app.services.recording import emit
        steps = [{'no': 1, 'type': 'slider', 'slider': {'selector': '', 'attempts': 2, 'timeout': 20}}]
        items = emit._steps_for_prompt(steps, 'playwright-ts')
        self.assertEqual(items[0]['type'], 'slider')
        self.assertEqual(items[0]['slider']['attempts'], 2)

    def test_prompt_mentions_solver_only_when_needed(self):
        from app.services.recording import emit
        plain = {'steps': [{'no': 1, 'type': 'click'}]}
        with_slider = {'steps': [{'no': 1, 'type': 'slider', 'slider': {}}]}
        self.assertNotIn('solveSlider', emit._prompt(plain))
        self.assertIn('solveSlider', emit._prompt(with_slider))


if __name__ == '__main__':
    unittest.main()
