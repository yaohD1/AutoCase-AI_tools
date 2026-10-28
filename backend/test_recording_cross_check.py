"""候选交叉验证：唯一不等于命中目标。

背景：录制时每条候选都要在页面上数 count，「数到 1 个」曾被当成「定位可靠」的充分条件。
但 count 只说明匹配了几个，不说明匹配的是不是用户点的那个——
getByText('删除') 完全可能命中提示语里的「删除」，而不是按钮。

recorder.js 在目标元素上打了 data-autocase-target，数 count 的同时把「命中元素与目标的关系」
一并算出来，于是「唯一但指错」第一次变得可判：
  self / ancestor / descendant  → 落在目标上，可用
  other                         → 指的根本是别人，写进脚本必错
  gone                          → 目标已离开页面，不能算命中
这里把判定规则钉死，防止以后有人把 count == 1 又当成充分条件。
"""
import unittest

from app.services.recording.locators import (
    best_effort, false_positive, hits_target, pick_for, rank, status_of,
)

PW = 'playwright-ts'


def cand(kind, count, index=None, relation=None):
    item = {
        'kind': kind,
        'count': count,
        'desc': '%s#%s' % (kind, count),
        'spec': {'kind': kind, 'method': 'locator', 'args': [kind], 'kwargs': {}},
    }
    if index is not None:
        item['index'] = index
    if relation is not None:
        item['relation'] = relation
    return item


class RelationJudgementTest(unittest.TestCase):
    def test_missing_relation_is_allowed(self):
        """旧数据 / 没打标记：不能因为新增的校验把原本能用的步骤判死。"""
        self.assertTrue(hits_target(cand('role', 1)))
        self.assertFalse(false_positive(cand('role', 1)))

    def test_other_is_false_positive(self):
        self.assertFalse(hits_target(cand('text', 1, index=-1, relation='other')))
        self.assertTrue(false_positive(cand('text', 1, index=-1, relation='other')))

    def test_gone_is_not_a_hit(self):
        """目标已经离开页面：既不算命中，也不该被当成「指错」。"""
        self.assertFalse(hits_target(cand('role', 1, index=-1, relation='gone')))
        self.assertFalse(false_positive(cand('role', 1, index=-1, relation='gone')))

    def test_ancestor_and_descendant_count_as_hit(self):
        """命中祖先/后代时点击多数能冒泡生效，是「可用但不精确」，不该被丢弃。"""
        self.assertTrue(hits_target(cand('text', 1, index=0, relation='ancestor')))
        self.assertTrue(hits_target(cand('text', 1, index=0, relation='descendant')))

    def test_self_with_negative_index_is_not_a_hit(self):
        """关系说 self 却给不出位置：数据自相矛盾，按不命中处理。"""
        self.assertFalse(hits_target(cand('role', 1, index=-1, relation='self')))


class RankPreferenceTest(unittest.TestCase):
    def test_unique_but_wrong_element_is_skipped(self):
        """count == 1 但指向别人：不能当主定位，要让位给下一条。"""
        counted = [
            cand('text', 1, index=-1, relation='other'),
            cand('css', 1, index=0, relation='self'),
        ]
        primary, _ = rank(counted)
        self.assertIsNotNone(primary)
        self.assertEqual(primary['kind'], 'css')
        self.assertNotIn('suspect', primary)

    def test_unique_on_target_wins_by_priority(self):
        counted = [
            cand('css', 1, index=0, relation='self'),
            cand('role', 1, index=0, relation='self'),
        ]
        primary, _ = rank(counted)
        self.assertEqual(primary['kind'], 'role')

    def test_all_unique_are_wrong_falls_back_with_suspect(self):
        """唯一候选全军覆没时不判 ambiguous——那样用户只会看到「未核实」，
        却不知道为什么。保留原选择并标 suspect，界面才能说清问题。"""
        counted = [cand('role', 1, index=-1, relation='other')]
        primary, _ = rank(counted)
        self.assertIsNotNone(primary)
        self.assertTrue(primary.get('suspect'))
        self.assertEqual(status_of(primary), 'suspect')

    def test_no_unique_candidate_is_still_ambiguous(self):
        counted = [cand('role', 3, index=1, relation='self')]
        primary, _ = rank(counted)
        self.assertIsNone(primary)
        self.assertEqual(status_of(primary), 'ambiguous')


class PoolSelectionTest(unittest.TestCase):
    def test_pick_for_excludes_false_positive(self):
        counted = [cand('role', 1, index=-1, relation='other')]
        self.assertIsNone(pick_for(PW, counted))

    def test_pick_for_accepts_unverified(self):
        """没有关系信息时保持老行为：唯一就选。"""
        counted = [cand('role', 1)]
        self.assertIsNotNone(pick_for(PW, counted))

    def test_best_effort_skips_false_positive(self):
        counted = [
            cand('role', 3, index=-1, relation='other'),
            cand('css', 3, index=2, relation='self'),
        ]
        best = best_effort(PW, counted)
        self.assertIsNotNone(best)
        self.assertEqual(best['kind'], 'css')

    def test_best_effort_returns_none_when_everything_points_elsewhere(self):
        counted = [cand('role', 3, index=-1, relation='other')]
        self.assertIsNone(best_effort(PW, counted))

    def test_ancestor_still_usable_as_best_effort(self):
        counted = [cand('text', 2, index=1, relation='ancestor')]
        best = best_effort(PW, counted)
        self.assertIsNotNone(best)
        self.assertEqual(best['index'], 1)


if __name__ == '__main__':
    unittest.main()
