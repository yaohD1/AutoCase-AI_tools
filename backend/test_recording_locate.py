# -*- coding: utf-8 -*-
"""「定位详情」：采用的定位 + 此刻页面上的存在性 / 唯一性。

这里测的是「后端算出来的答案对不对」，不测 Playwright：
真正的 count 在浏览器里，测试环境没有桌面，用假 locator 顶替。
最重要的一条是**口径一致**——弹窗里显示的必须是生成脚本会写的那一条。
"""
import unittest
from unittest.mock import patch

from app.services.recording import emit
from app.services.recording.session import RecordingSession


def role_candidate(count=1, relation='self'):
    return {
        'kind': 'role',
        'desc': "getByRole('button', { name: '登录' })",
        'spec': {'kind': 'role', 'role': 'button', 'name': '登录'},
        'count': count,
        'index': 0,
        'relation': relation,
    }


def text_candidate(count=2):
    return {
        'kind': 'text',
        'desc': "getByText('登录')",
        'spec': {'kind': 'text', 'text': '登录'},
        'count': count,
        'index': 0,
        'relation': 'self',
    }


def rf_candidate():
    return {
        'kind': 'rf_xpath',
        'desc': "xpath=//button[normalize-space()='登录']",
        # RF 组的 display 字符串就是 spec.args[0]（见 emit._fmt），这里照它的形状造。
        'spec': {'args': ["xpath=//button[normalize-space()='登录']"]},
        'count': 1,
        'index': 0,
        'relation': 'self',
    }


class FakeLocator:
    def __init__(self, count, items=None, error=None):
        self._count = count
        self._items = items or []
        self._error = error
        self.drawn_with = None

    def count(self):
        if self._error:
            raise self._error
        return self._count

    def evaluate_all(self, _js, active):
        self.drawn_with = active
        return self._items


class FakeFrame:
    url = 'http://192.168.1.10/#/login'


class LocateNowTest(unittest.TestCase):
    def _session(self, target, frame=FakeFrame()):
        session = RecordingSession.__new__(RecordingSession)
        session._find_step = lambda no: {'no': no, 'target': target, '_frame': frame}
        session._app_frame = lambda: frame
        return session

    def _run(self, target, locator, frame=FakeFrame(), framework='playwright-ts'):
        session = self._session(target, frame=frame)
        with patch('app.services.recording.session.build_locator', return_value=locator):
            return session._locate_now(1, framework)

    def test_reports_exists_and_unique(self):
        target = {'alias': '登录按钮', 'candidates': [role_candidate(1), text_candidate(2)]}
        locator = FakeLocator(1, [{'index': 0, 'visible': True, 'text': '登录'}])
        out = self._run(target, locator)
        self.assertTrue(out['ok'])
        self.assertTrue(out['exists'])
        self.assertTrue(out['unique'])
        self.assertTrue(out['visible'])
        self.assertEqual(out['count'], 1)
        self.assertEqual(out['alias'], '登录按钮')

    def test_locator_matches_what_the_generator_writes(self):
        """弹窗里的那条，必须和生成时用的完全一致（同一口径，不另写判断）。"""
        target = {'candidates': [role_candidate(1), text_candidate(2)]}
        out = self._run(target, FakeLocator(1, [{'index': 0, 'visible': True}]))
        self.assertEqual(out['code'], emit._locator_info(target, 'playwright-ts')['locator'])
        self.assertEqual(out['kind'], 'role')
        self.assertEqual(out['mode'], 'unique')

    def test_many_matches_is_not_unique(self):
        target = {'candidates': [text_candidate(2)]}
        locator = FakeLocator(2, [{'index': 0, 'visible': True}, {'index': 1, 'visible': False}])
        out = self._run(target, locator)
        self.assertTrue(out['ok'])
        self.assertTrue(out['exists'])
        self.assertFalse(out['unique'])
        self.assertEqual(out['count'], 2)
        # 当前用的是第几个，画框也要画在同一个上
        self.assertEqual(locator.drawn_with, out['index'])

    def test_missing_element_reports_not_exists(self):
        target = {'candidates': [role_candidate(1)]}
        locator = FakeLocator(0)
        out = self._run(target, locator)
        self.assertTrue(out['ok'])
        self.assertFalse(out['exists'])
        self.assertFalse(out['unique'])
        self.assertEqual(out['count'], 0)
        self.assertIsNone(locator.drawn_with)  # 0 个就不用画了

    def test_chosen_candidate_wins(self):
        """用户亲手挑过的候选，生成和弹窗都得跟着走。"""
        target = {'candidates': [role_candidate(1), text_candidate(3)],
                  'chosen': {'desc': "getByText('登录')", 'index': 1}}
        out = self._run(target, FakeLocator(3, [{'index': 0, 'visible': True},
                                                {'index': 1, 'visible': True},
                                                {'index': 2, 'visible': True}]))
        self.assertEqual(out['kind'], 'text')
        self.assertEqual(out['index'], 1)
        self.assertEqual(out['count'], 3)

    def test_no_frame_reports_reason(self):
        target = {'candidates': [role_candidate(1)]}
        out = self._run(target, FakeLocator(1), frame=None)
        self.assertFalse(out['ok'])
        self.assertEqual(out['reason'], 'no_frame')

    def test_frame_lost_maps_to_reason(self):
        target = {'candidates': [role_candidate(1)]}
        out = self._run(target, FakeLocator(1, error=RuntimeError('Target closed')))
        self.assertFalse(out['ok'])
        self.assertEqual(out['reason'], 'frame_lost')

    def test_no_target_is_not_an_error_to_hide(self):
        out = self._run({}, FakeLocator(1))
        self.assertFalse(out['ok'])
        self.assertEqual(out['reason'], 'no_target')

    def test_page_gone_is_surfaced(self):
        """录制时就翻页走了的步骤：结论要带上这个标记，别让用户以为是定位坏了。"""
        target = {'candidates': [role_candidate(0)], 'page_gone': True, 'unverified': True}
        out = self._run(target, FakeLocator(0))
        self.assertTrue(out['page_gone'])
        self.assertFalse(out['exists'])

    def test_candidates_are_filtered_by_framework(self):
        """两套候选混在一起会列十几条，用户对不上号：只给当前框架那一组。"""
        target = {'candidates': [role_candidate(1), text_candidate(2), rf_candidate()]}
        pw = self._run(target, FakeLocator(1, [{'index': 0, 'visible': True}]))
        self.assertEqual([row['kind'] for row in pw['candidates']], ['role', 'text'])
        robot = self._run(target, FakeLocator(1, [{'index': 0, 'visible': True}]),
                          framework='robot-selenium')
        self.assertEqual([row['kind'] for row in robot['candidates']], ['rf_xpath'])

    def test_adopted_candidate_is_marked_once(self):
        target = {'candidates': [role_candidate(1), text_candidate(2)]}
        out = self._run(target, FakeLocator(1, [{'index': 0, 'visible': True}]))
        adopted = [row for row in out['candidates'] if row['adopted']]
        self.assertEqual(len(adopted), 1)
        self.assertEqual(adopted[0]['kind'], 'role')


if __name__ == '__main__':
    unittest.main()
