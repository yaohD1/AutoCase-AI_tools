"""定位变量抽取与落盘。

重点是撞名：同一个用例目录下的两个页面都有「取消」时，变量名不能重复——
后写的把先写的覆盖掉，先录的那批用例就会点到错的元素，而且完全不报错。
"""
import tempfile
import unittest
from pathlib import Path

from app.services.recording import locator_vars as lv


def _step(no, alias, locator, kind='click', route=None, role='button', tag='button'):
    target = {
        'alias': alias,
        'snap': {'role': role, 'tag': tag},
        '_locator': locator,
    }
    if route:
        target['page'] = {'route': route, 'title': None}
    return {'no': no, 'type': kind, 'target': target}


def _locator_of(target):
    return target.get('_locator')


class PathTest(unittest.TestCase):
    def test_file_follows_case_dir(self):
        self.assertEqual(
            lv.file_rel('测试用例层/终端管理/终端资产/终端资产菜单中功能.robot'),
            '.autocase/locators/终端管理/终端资产/终端资产页面元素.resource')

    def test_root_level_and_flat_file(self):
        self.assertEqual(lv.file_rel('tests/login.robot'),
                         '.autocase/locators/login页面元素.resource')
        self.assertEqual(lv.file_rel('a.robot'), '.autocase/locators/a页面元素.resource')

    def test_robot_treats_space_and_case_as_same(self):
        self.assertEqual(lv.normalize_name('终端资产 导出'), lv.normalize_name('终端资产_导出'))
        self.assertEqual(lv.normalize_name('ABC'), lv.normalize_name('abc'))


class NameTest(unittest.TestCase):
    def test_prefix_alias_and_suffix(self):
        steps = [_step(3, '导出', "xpath=//button[text()='导出']", route='/asset')]
        self.assertEqual(steps[0]['target']['alias'] + '按钮', '导出按钮')

        root = tempfile.mkdtemp()
        out = lv.build(steps, {}, '测试用例层/终端管理/终端资产/a.robot', root, _locator_of)
        self.assertEqual(out['vars']['3'], '${终端资产导出按钮}')

    def test_suffix_not_duplicated(self):
        root = tempfile.mkdtemp()
        steps = [_step(1, '输入框', 'xpath=//input', kind='fill', tag='input')]
        out = lv.build(steps, {}, '测试用例层/a/b.robot', root, _locator_of)
        self.assertEqual(out['vars']['1'], '${a输入框}')

    def test_unsafe_chars_dropped(self):
        root = tempfile.mkdtemp()
        steps = [_step(1, '告警ID：', 'xpath=//div', route='/x')]
        out = lv.build(steps, {}, '测试用例层/a/b.robot', root, _locator_of)
        # 全角冒号、点、空格都不进变量名：Robot 里 `.` 还会被当成属性访问。
        self.assertEqual(out['vars']['1'], '${a告警ID按钮}')

    def test_upload_picked_on_wrapper_div(self):
        root = tempfile.mkdtemp()
        steps = [_step(1, 'div', 'css=div:nth-of-type(3)', kind='upload', tag='div', role='')]
        out = lv.build(steps, {}, '测试用例层/a/b.robot', root, _locator_of)
        self.assertEqual(out['vars']['1'], '${a上传框}')


class CollisionTest(unittest.TestCase):
    """撞名必须另起名字，不能覆盖。"""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.target = '测试用例层/终端管理/终端资产/a.robot'

    def _body(self):
        rel = lv.file_rel(self.target)
        return (Path(self.root) / rel).read_text(encoding='utf-8')

    def test_same_name_different_page_gets_suffix(self):
        steps = [
            _step(1, '取消', 'xpath=//topo-cancel', route='/topo'),
            _step(2, '取消', 'xpath=//nelist-cancel', route='/neList'),
        ]
        out = lv.build(steps, {}, self.target, self.root, _locator_of)
        self.assertEqual(out['vars']['1'], '${终端资产取消按钮}')
        self.assertEqual(out['vars']['2'], '${终端资产取消按钮_2}')
        body = self._body()
        self.assertIn('//topo-cancel', body)
        self.assertIn('//nelist-cancel', body)

    def test_same_name_same_page_gets_suffix(self):
        # 同一页有两个都没标签的输入框：名字一样、页面一样，但定位不同。
        # 按覆盖处理会把第一个的定位抹掉，生成出来的脚本就点错框。
        steps = [
            _step(1, '输入框', "xpath=//*[@id='input-v-440']", kind='fill', tag='input', route='/neList'),
            _step(2, '输入框', "xpath=//*[@id='input-v-443']", kind='fill', tag='input', route='/neList'),
        ]
        out = lv.build(steps, {}, self.target, self.root, _locator_of)
        self.assertEqual(out['vars']['1'], '${终端资产输入框}')
        self.assertEqual(out['vars']['2'], '${终端资产输入框_2}')
        body = self._body()
        self.assertIn('input-v-440', body)
        self.assertIn('input-v-443', body)

    def test_same_element_twice_shares_one_variable(self):
        # 同一个元素点两次（定位相同）：两个步骤共用一个变量。
        steps = [
            _step(1, '刷新', 'xpath=//*[@data-testid="refresh"]', route='/neList'),
            _step(2, '刷新', 'xpath=//*[@data-testid="refresh"]', route='/neList'),
        ]
        out = lv.build(steps, {}, self.target, self.root, _locator_of)
        self.assertEqual(out['vars']['1'], out['vars']['2'])

    def test_same_name_same_page_overwrites(self):
        # 页面改版：同名同页面，新定位覆盖旧值，不新增变量。
        old = lv.render(lv.merge({}, [{
            'name': '终端资产取消按钮', 'locator': 'xpath=//old', 'route': '/topo',
            'no': 1, 'scope': None}]), None)
        rel = lv.file_rel(self.target)
        path = Path(self.root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(old, encoding='utf-8')

        steps = [_step(9, '取消', 'xpath=//new', route='/topo')]
        out = lv.build(steps, {}, self.target, self.root, _locator_of)
        self.assertEqual(out['vars']['9'], '${终端资产取消按钮}')
        body = self._body()
        self.assertIn('//new', body)
        self.assertNotIn('//old', body)

    def test_existing_vars_survive_when_not_in_this_recording(self):
        # 同目录下另一个用例的变量，不能因为这次没录到就被删掉。
        old = lv.render(lv.merge({}, [{
            'name': '终端资产导出按钮', 'locator': 'xpath=//export', 'route': '/asset',
            'no': 1, 'scope': None}]), None)
        rel = lv.file_rel(self.target)
        path = Path(self.root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(old, encoding='utf-8')

        steps = [_step(5, '导入', 'xpath=//import', route='/asset')]
        lv.build(steps, {}, self.target, self.root, _locator_of)
        body = self._body()
        self.assertIn('//export', body)
        self.assertIn('//import', body)


class RenderTest(unittest.TestCase):
    def test_parse_round_trip_with_hash_in_locator(self):
        # CSS 定位里带 #id，不能被当成行尾注释切掉。
        locator = 'css=#app > div:nth-of-type(2)'
        merged = lv.merge({}, [{
            'name': 'X', 'locator': locator, 'route': '/a', 'no': 1, 'scope': None}])
        text = lv.render(merged, None)
        again = lv.parse(text)
        self.assertEqual(again[lv.normalize_name('X')]['locator'], locator)
        self.assertEqual(again[lv.normalize_name('X')]['route'], '/a')

    def test_page_group_is_only_comment(self):
        merged = lv.merge({}, [
            {'name': 'A', 'locator': 'xpath=//a', 'route': '/topo', 'no': 1, 'scope': None},
            {'name': 'B', 'locator': 'xpath=//b', 'route': '/neList', 'no': 2, 'scope': None},
        ])
        text = lv.render(merged, {'/topo': '拓扑管理'})
        self.assertIn('# ---- /topo（拓扑管理） ----', text)
        self.assertIn('# ---- /neList ----', text)
        self.assertIn('${A}    xpath=//a    # /topo', text)

    def test_write_states(self):
        root = Path(tempfile.mkdtemp())
        path = root / 'a.resource'
        self.assertEqual(lv.write(path, 'hello\n'), 'created')
        self.assertEqual(lv.write(path, 'hello\n'), 'reused')
        self.assertEqual(lv.write(path, 'changed\n'), 'updated')


class ActionTest(unittest.TestCase):
    def test_action_vars_go_to_common_file(self):
        root = tempfile.mkdtemp()
        actions = {'act1': {'name': '登录', 'steps': [
            _step(1, '账号', 'xpath=//input[@id="u"]', kind='fill', tag='input'),
        ]}}
        out = lv.build([], actions, '测试用例层/a/b.robot', root, _locator_of)
        self.assertEqual(out['action_vars']['act1']['1'], '${登录账号输入框}')
        self.assertEqual(out['files'][0]['path'],
                         '.autocase/locators/公共/公共页面元素.resource')

    def test_two_actions_with_same_element_dont_collide(self):
        # 两个动作里都有「确定」，各带动作名前缀，不该互相覆盖。
        root = tempfile.mkdtemp()
        actions = {
            'a1': {'name': '新增', 'steps': [_step(1, '确定', 'xpath=//ok1')]},
            'a2': {'name': '删除', 'steps': [_step(1, '确定', 'xpath=//ok2')]},
        }
        out = lv.build([], actions, '测试用例层/a/b.robot', root, _locator_of)
        self.assertEqual(out['action_vars']['a1']['1'], '${新增确定按钮}')
        self.assertEqual(out['action_vars']['a2']['1'], '${删除确定按钮}')


if __name__ == '__main__':
    unittest.main()
