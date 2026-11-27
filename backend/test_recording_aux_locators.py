"""滑块 / 验证码定位进资源文件。

它们没有 target 元素，定位挂在 step.slider / step.captcha 上，很容易被
「只扫 target」的老逻辑漏掉——漏掉的后果是脚本里硬编码裸选择器，页面改版要逐条改。
"""
import tempfile
import unittest
from pathlib import Path

from app.services.recording import emit, locator_vars, slider_bundle


def target(alias):
    loc = 'css=input[name="%s"]' % alias
    return {
        'alias': alias,
        'snap': {'tag': 'input', 'name': alias},
        'candidates': [{'kind': 'rf_css', 'spec': {'args': [loc]}, 'count': 1,
                        'index': 0, 'desc': 'rf ' + loc}],
        'primary': 'rf_css',
        'status': 'unique',
    }


def step(no, kind, **kw):
    base = {'no': no, 'type': kind, 'source': 'record', 'target': None, 'value': None,
            'label': None, 'url': None, 'page_index': 0, 'intent': None, 'text': None,
            'dialog': None}
    base.update(kw)
    return base


def slider(no, selector='div#nc_1_n1z'):
    return step(no, 'slider', slider={'selector': selector, 'attempts': 2, 'timeout': 20})


def captcha(no, selector='img.captcha', input_selector='input#code'):
    return step(no, 'captcha', captcha={
        'selector': selector, 'input_selector': input_selector,
        'charset': 'digits', 'length': 4, 'attempts': 2, 'timeout': 20,
    })


def locator_of(target_or_none):
    if not target_or_none:
        return None
    return (target_or_none.get('candidates') or [{}])[0]['spec']['args'][0]


class AuxLocatorTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, steps, actions=None):
        return locator_vars.build(steps, actions or {}, '测试用例层/登录/登录.robot',
                                  self.root, locator_of)

    def test_slider_and_captcha_go_into_resource(self):
        built = self.build([step(1, 'fill', target=target('用户名'), value='a'),
                            slider(2), captcha(3)])
        self.assertEqual('${登录滑块}', built['aux_vars']['2']['slider'])
        self.assertEqual('${登录验证码}', built['aux_vars']['3']['captcha'])
        self.assertEqual('${登录验证码输入框}', built['aux_vars']['3']['captcha_input'])
        text = (self.root / built['files'][0]['path']).read_text(encoding='utf-8')
        self.assertIn('${登录滑块}    css=div#nc_1_n1z', text)
        self.assertIn('${登录验证码}    css=img.captcha', text)
        self.assertIn('${登录验证码输入框}    css=input#code', text)
        # 普通元素的变量没有被挤掉
        self.assertIn('${登录用户名输入框}', text)

    def test_second_build_reuses_names(self):
        steps = [slider(1), captcha(2)]
        first = self.build(steps)
        second = self.build(steps)
        self.assertEqual(first['aux_vars'], second['aux_vars'])
        self.assertEqual(first['files'][0]['count'], second['files'][0]['count'])
        self.assertEqual('reused', second['files'][0]['state'])

    def test_two_different_sliders_get_distinct_names(self):
        built = self.build([slider(1, 'div#a'), slider(2, 'div#b')])
        self.assertEqual('${登录滑块}', built['aux_vars']['1']['slider'])
        self.assertEqual('${登录滑块_2}', built['aux_vars']['2']['slider'])

    def test_same_slider_twice_shares_one_variable(self):
        built = self.build([slider(1), slider(2)])
        self.assertEqual(built['aux_vars']['1']['slider'], built['aux_vars']['2']['slider'])

    def test_empty_selectors_create_nothing(self):
        built = self.build([slider(1, ''), captcha(2, '', '')])
        self.assertEqual({}, built.get('aux_vars') or {})
        self.assertEqual([], built['files'])

    def test_captcha_without_input_selector(self):
        built = self.build([captcha(1, 'img.captcha', '')])
        self.assertNotIn('captcha_input', built['aux_vars']['1'])
        text = (self.root / built['files'][0]['path']).read_text(encoding='utf-8')
        self.assertNotIn('验证码输入框', text)

    def test_action_slider_goes_to_common_file(self):
        actions = {'a1': {'id': 'a1', 'name': '登录',
                          'steps': [step(1, 'fill', target=target('用户名'), value='a'),
                                     slider(2)]}}
        steps = [step(1, 'call', action='a1')]
        built = self.build(steps, actions)
        self.assertEqual('${登录滑块}', built['action_aux_vars']['a1']['2']['slider'])
        paths = [f['path'] for f in built['files']]
        self.assertIn('.autocase/locators/公共/公共页面元素.resource', paths)
        text = (self.root / '.autocase/locators/公共/公共页面元素.resource').read_text(encoding='utf-8')
        self.assertIn('${登录滑块}    css=div#nc_1_n1z', text)


class AuxPromptTest(unittest.TestCase):

    def build(self, steps):
        with tempfile.TemporaryDirectory() as tmp:
            return locator_vars.build(steps, {}, '测试用例层/登录/登录.robot', tmp, locator_of)

    def items(self, framework):
        steps = [slider(2), captcha(3)]
        built = self.build(steps)
        return emit._steps_for_prompt(steps, framework, locator_vars=built['vars'],
                                      aux_vars=built['aux_vars'])

    def test_rf_gets_variable_not_raw_selector(self):
        items = self.items(emit.RF_FRAMEWORK)
        self.assertEqual('${登录滑块}', items[0]['slider']['selector_var'])
        self.assertNotIn('selector', items[0]['slider'])
        self.assertEqual('${登录验证码}', items[1]['captcha']['selector_var'])
        self.assertEqual('${登录验证码输入框}', items[1]['captcha']['input_selector_var'])
        self.assertNotIn('selector', items[1]['captcha'])
        self.assertNotIn('input_selector', items[1]['captcha'])

    def test_playwright_keeps_raw_selector(self):
        items = self.items(emit.PW_FRAMEWORK)
        self.assertEqual('div#nc_1_n1z', items[0]['slider']['selector'])
        self.assertEqual('img.captcha', items[1]['captcha']['selector'])
        self.assertNotIn('selector_var', items[0]['slider'])

    def test_slider_rule_mentions_variable(self):
        payload = {'framework': emit.RF_FRAMEWORK, 'steps': [slider(1)]}
        rule = emit._slider_rules(payload)
        self.assertIn('selector_var', rule)
        self.assertIn('不要', rule)


class SliderSelectorTest(unittest.TestCase):

    def test_library_strips_css_prefix(self):
        # slider_bundle 整个是「生成到目标项目的源码」模板，不能在模块里直接调函数。
        source = slider_bundle.SLIDER_PY
        self.assertIn("startswith('css=')", source)
        self.assertIn('selector = _css_selector(selector)', source)
        # 语法必须成立：这段会原样写成 SliderLibrary.py
        compile(source, 'SliderLibrary.py', 'exec')


class PlanFilesAuxTest(unittest.TestCase):

    def test_aux_locator_makes_locator_file_show_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / '测试用例层/登录').mkdir(parents=True)
            out = emit.plan_files(tmp, '测试用例层/登录/登录.robot',
                                  [slider(1), captcha(2)], emit.RF_FRAMEWORK)
        roles = {f['role']: f for f in out}
        self.assertIn('locator', roles)
        self.assertIn('第 1、2 步', roles['locator']['why'])


if __name__ == '__main__':
    unittest.main()
