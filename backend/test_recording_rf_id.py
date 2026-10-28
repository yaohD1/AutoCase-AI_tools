"""RF 的 id 判定：分清「业务键」和「框架自动生成」。

旧判据是 `^el-id-|\\d{3,}`（search），等于「数字多就是动态的」，恰好判反了两个方向：

  该留的被丢：order-20261028-001、user-100 —— 退到 css 结构路径，比 id 差得多；
  该丢的留着：:r1:（React 18 useId）、react-select-3-input、rc-tabs-0-tab-2 ——
              这些每次挂载都重新算，回放必然找不到元素。

现在按「有没有框架生成的特征」判：明确的丢弃，含数字但分不清的降级成 rf_id_loose，
让位给 testid / 干净 id / name / aria / label，但仍排在文本 xpath 之前。
"""
import unittest

from app.services.recording.locators import (
    RF_KINDS, RF_SEMANTIC_KINDS, build_specs, pick_for,
)

RF = 'robot-selenium'


def id_kinds(element_id, **snap):
    base = {'tag': 'input', 'id': element_id}
    base.update(snap)
    return [(s['kind'], s['desc']) for s in build_specs(base) if s['kind'].startswith('rf_id')]


def counted(element_id, **snap):
    """把 build_specs 的产物补上 count，模拟录制时数过的候选。"""
    out = []
    for spec in build_specs(dict({'tag': 'input', 'id': element_id}, **snap)):
        if not spec['kind'].startswith('rf'):
            continue
        out.append({'kind': spec['kind'], 'desc': spec['desc'], 'count': 1,
                    'relation': 'self', 'spec': spec})
    return out


class DynamicIdTest(unittest.TestCase):
    """框架自动生成的 id 必须丢掉——回放时它一定变了，没有任何补救空间。"""

    def test_framework_ids_are_dropped(self):
        for element_id in (
            ':r1:', ':R2:', ':r1a:',            # React 18 useId
            'el-id-749-6', 'el-id-749-7',       # Element Plus
            'react-select-3-input',             # React Select
            'rc-tabs-0-tab-2', 'rc-select-1',   # rc-*（antd 底层）
            'mui-12345',                        # MUI
            'radix-:r5:',                       # Radix
            'headlessui-menu-button-3',         # Headless UI
            'react-aria-7',
            'downshift-1-input',
            'ext-gen1234',                      # ExtJS
            'gwt-uid-42',                       # GWT
            'j_id_5', 'j_idt12',                # JSF
            'ember1234',                        # Ember
        ):
            self.assertEqual(id_kinds(element_id), [], element_id)


class BusinessIdTest(unittest.TestCase):
    """业务键是页面上最稳的定位，绝不能因为「带数字」就被扔掉。"""

    def test_business_ids_are_kept(self):
        for element_id in ('order-20261028-001', 'user-100', 'order-2026', 'WO-2026-0001'):
            kinds = [k for k, _ in id_kinds(element_id)]
            self.assertEqual(kinds, ['rf_id_loose'], element_id)

    def test_clean_ids_stay_strong(self):
        for element_id in ('user', 'userId', 'btn-submit', 'login-form', 'searchInput',
                           'user-1', 'a1b2c3d4'):
            kinds = [k for k, _ in id_kinds(element_id)]
            self.assertEqual(kinds, ['rf_id'], element_id)

    def test_control_name_with_sequence_is_loose(self):
        """input-0 / tab-2 这种「控件名+序号」没人拿来当业务 id，都是渲染时自增的。"""
        for element_id in ('input-0', 'input-1', 'tab-2', 'item-3', 'field-1'):
            kinds = [k for k, _ in id_kinds(element_id)]
            self.assertEqual(kinds, ['rf_id_loose'], element_id)


class OrderingTest(unittest.TestCase):
    """降级的实际含义：有更强的属性时让位，没有时仍然比文本 xpath 优先。"""

    def test_loose_id_yields_to_name(self):
        pool = counted('user-100', attr_name='username')
        self.assertIn('rf_name', [c['kind'] for c in pool])
        self.assertEqual(pick_for(RF, pool)['kind'], 'rf_name')

    def test_loose_id_beats_text_xpath(self):
        # 只有 id 和文本可用时，id 仍然更接近「元素本身」。
        pool = counted('btn-1234', text='提交', role_attr='button')
        self.assertIn('rf_xpath', [c['kind'] for c in pool])
        self.assertEqual(pick_for(RF, pool)['kind'], 'rf_id_loose')

    def test_loose_id_yields_to_aria_and_label(self):
        self.assertEqual(pick_for(RF, counted('user-100', aria_label='用户名'))['kind'], 'rf_aria')
        self.assertEqual(pick_for(RF, counted('user-100', label='用户名'))['kind'], 'rf_label')

    def test_order_constant_matches_pick_for(self):
        # 前端 RF_ORDER 镜像的就是这个元组，顺序错了界面预览和生成会不一致。
        self.assertLess(RF_KINDS.index('rf_id'), RF_KINDS.index('rf_id_loose'))
        self.assertLess(RF_KINDS.index('rf_label'), RF_KINDS.index('rf_id_loose'))
        self.assertLess(RF_KINDS.index('rf_id_loose'), RF_KINDS.index('rf_xpath'))
        self.assertLess(RF_KINDS.index('rf_id_loose'), RF_KINDS.index('rf_css'))

    def test_loose_id_can_anchor_a_row(self):
        # 行锚定挑内层候选时也要认它，否则行内的 id 定位会被排除在外。
        self.assertIn('rf_id_loose', RF_SEMANTIC_KINDS)


if __name__ == '__main__':
    unittest.main()
