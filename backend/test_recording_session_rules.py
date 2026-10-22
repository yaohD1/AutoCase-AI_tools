import threading
import unittest

from app.services.recording import intent, ir
from app.services.recording.session import RecordingError, RecordingSession


def _session(steps):
    s = RecordingSession.__new__(RecordingSession)
    s.id = 'test'
    s.status = 'stopped'
    s.base_url = 'http://x'
    s.error = None
    s._pick = False
    s._lock = threading.RLock()
    for i, step in enumerate(steps, 1):
        step['no'] = i
    s._steps = steps
    s._picks = []
    s._alias_keys = {}
    # 这些用例只测步骤规则，没有真实浏览器上下文；补成 None / False，
    # 让 _schedule_hud 与 snapshot 直接返回（缺字段会 AttributeError）。
    s._context = None
    s._paused = False
    s._panel_page = None
    return s


def _step(kind, text):
    step = ir.make_step(kind, no=0, value=None, source='record')
    step['text'] = text
    return step


class StepRulesTest(unittest.TestCase):
    def _flow(self):
        return [_step('click', 'open'), _step('new_page', None), _step('click', 'next'), _step('click', 'end')]

    def test_removing_the_opener_also_removes_its_new_page_marker(self):
        s = _session(self._flow())
        s.remove_step(1)
        self.assertEqual([x['type'] for x in s._steps], ['click', 'click'])
        self.assertEqual([x['no'] for x in s._steps], [1, 2])

    def test_new_page_marker_cannot_be_removed_on_its_own(self):
        s = _session(self._flow())
        with self.assertRaises(RecordingError):
            s.remove_step(2)

    def test_insert_after_opener_lands_after_the_new_page(self):
        s = _session(self._flow())
        checked = intent.validate({'type': 'sleep', 'seconds': 1}, [])
        s.add_intent_step(checked, text='wait', after_no=1)
        self.assertEqual([x['type'] for x in s._steps], ['click', 'new_page', 'sleep', 'click', 'click'])

    def test_tied_step_cannot_be_moved(self):
        s = _session(self._flow())
        with self.assertRaises(RecordingError):
            s.move_step(1, 'down')
        with self.assertRaises(RecordingError):
            s.move_step(3, 'up')

    def test_untied_steps_swap_and_renumber(self):
        s = _session(self._flow())
        s.move_step(4, 'up')
        self.assertEqual([x['text'] for x in s._steps if x['type'] == 'click'], ['open', 'end', 'next'])
        self.assertEqual([x['no'] for x in s._steps], [1, 2, 3, 4])

    def test_move_at_the_edge_is_a_no_op(self):
        s = _session(self._flow())
        s.move_step(1, 'up')
        self.assertEqual(s._steps[0]['type'], 'click')

    def test_assertion_inherits_the_tab_of_its_anchor(self):
        # 断言必须跟着锚点所在的标签页走：以前恒写 0，多标签页录制时会被塞到主标签页
        # （回放/生成的脚本就会在错误的页面上找元素）。
        s = _session([_step('click', 'open')])
        s._steps.append(ir.make_step('new_page', no=0, page_index=1))
        s._steps.append(ir.make_step('click', no=0, page_index=1))
        s._renumber()
        checked = intent.validate({'type': 'sleep', 'seconds': 1}, [])
        s.add_intent_step(checked, text='末尾')
        self.assertEqual(s._steps[-1]['page_index'], 1)
        s.add_intent_step(checked, text='中间', after_no=1)
        # 锚点那一步后面紧跟着「标签页切换」，实际插在切换之后，但页号仍按锚点算。
        middle = next(step for step in s._steps if step['text'] == '中间')
        self.assertEqual(middle['page_index'], 0)


class AliasTest(unittest.TestCase):
    """别名必须唯一，且唯一的单位是「元素」而不是「名字」。"""

    def _assign(self, s, snap, primary=None, frame=None):
        target = ir.make_target(snap, [], primary, 'unique' if primary else 'ambiguous')
        with s._lock:
            target['alias'] = s._alias_for(target, s._alias_key(snap, primary, frame))
        return target

    @staticmethod
    def _snap(name, css):
        return {'tag': 'button', 'name': name, 'text': name, 'css': css}

    def test_same_name_different_elements_get_distinct_aliases(self):
        # 列表里两行各有一个「删除」：不下标的话，第二个会把第一个盖掉，
        # 断言就会静默绑到用户没选的那个元素上。
        s = _session([])
        row1 = self._assign(s, self._snap('删除', 'tbody > tr:nth-of-type(1) > td > button'))
        row2 = self._assign(s, self._snap('删除', 'tbody > tr:nth-of-type(2) > td > button'))
        self.assertEqual(row1['alias'], '删除')
        self.assertEqual(row2['alias'], '删除#2')

    def test_the_same_element_clicked_twice_keeps_one_alias(self):
        # 同一个按钮点两次不该变成两个选项——那才是「同名但不是同一个元素」的反面。
        s = _session([])
        css = 'body > form > button'
        first = self._assign(s, self._snap('提交', css))
        second = self._assign(s, self._snap('提交', css))
        self.assertEqual(first['alias'], '提交')
        self.assertEqual(second['alias'], '提交')
        s._steps.append(ir.make_step('click', no=0, target=first))
        s._steps.append(ir.make_step('click', no=0, target=second))
        s._renumber()
        self.assertEqual(s.aliases(), ['提交'])

    def test_unique_locator_decides_identity_not_the_css_path(self):
        # 重新渲染后结构路径会变，但唯一主定位没变，仍是同一个元素。
        s = _session([])
        primary = {'kind': 'role', 'desc': "getByRole('button', name='提交')", 'count': 1}
        a = self._assign(s, self._snap('提交', 'body > div:nth-of-type(1) > button'), primary)
        b = self._assign(s, self._snap('提交', 'body > div:nth-of-type(2) > button'), primary)
        self.assertEqual(a['alias'], b['alias'])

    def test_each_alias_resolves_to_its_own_element(self):
        # 修复前 _find_target 取的是「最后一个同名」：给第一行加断言会绑到第二行。
        s = _session([])
        row1 = self._assign(s, self._snap('删除', 'tbody > tr:nth-of-type(1) > td > button'))
        row2 = self._assign(s, self._snap('删除', 'tbody > tr:nth-of-type(2) > td > button'))
        for target in (row1, row2):
            s._steps.append(ir.make_step('click', no=0, target=target))
        s._renumber()
        self.assertIs(s._find_target('删除'), row1)
        self.assertIs(s._find_target('删除#2'), row2)

    def test_alias_items_explain_where_each_one_came_from(self):
        s = _session([])
        row1 = self._assign(s, self._snap('删除', 'tbody > tr:nth-of-type(1) > td > button'))
        row2 = self._assign(s, self._snap('删除', 'tbody > tr:nth-of-type(2) > td > button'))
        s._steps.append(ir.make_step('click', no=0, target=row1))
        s._steps.append(ir.make_step('click', no=0, target=row2))
        s._renumber()
        items = s.alias_items()
        self.assertEqual([i['alias'] for i in items], ['删除', '删除#2'])
        self.assertEqual([i['hint'] for i in items], ['第 1 步 · 点击', '第 2 步 · 点击'])


class ReloadStepTest(unittest.TestCase):
    """刷新是独立步骤类型：它要能插、能删、没有可编辑字段，也不能插错位置。"""

    def _flow(self):
        return [_step('click', 'open'), _step('new_page', None), _step('click', 'next')]

    def test_reload_is_inserted_after_the_anchor(self):
        s = _session(self._flow())
        s._add_reload(after_no=1)
        # 和断言一样，不能插进「点击 → 标签页切换」之间，否则那一步的绑定就断了
        self.assertEqual([x['type'] for x in s._steps], ['click', 'new_page', 'reload', 'click'])

    def test_reload_appends_to_the_end_when_no_anchor(self):
        s = _session(self._flow())
        s._add_reload()
        self.assertEqual([x['type'] for x in s._steps], ['click', 'new_page', 'click', 'reload'])
        self.assertEqual([x['no'] for x in s._steps], [1, 2, 3, 4])

    def test_insert_after_a_trailing_new_page_does_not_raise(self):
        # 旧写法在锚点就是最后一步的 new_page 时会越界（self._steps[idx + 1]）
        s = _session([_step('click', 'open'), _step('new_page', None)])
        s._add_reload(after_no=2)
        self.assertEqual([x['type'] for x in s._steps], ['click', 'new_page', 'reload'])

    def test_reload_has_nothing_to_edit(self):
        s = _session([_step('reload', None)])
        with self.assertRaises(RecordingError):
            s.update_step(1, {'url': 'http://x'})

    def test_reload_can_be_removed(self):
        s = _session([_step('goto', None), _step('reload', None)])
        s.remove_step(2)
        self.assertEqual([x['type'] for x in s._steps], ['goto'])


if __name__ == '__main__':
    unittest.main()
