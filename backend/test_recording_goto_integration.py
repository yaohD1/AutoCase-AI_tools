# -*- coding: utf-8 -*-
"""端到端：点完按钮页面跳走 → 框不出来 → 用记下的地址回去 → 框得出来。

这是「回到这一步的页面」真正要证明的事。单独测 _goto_doc_now 只能证明它跳对了，
证明不了跳回来之后 highlight 还能把那一行框出来——那才是用户要的结果。
"""
import threading
import unittest

from app.services.recording.locators import build_specs, rank
from app.services.recording.session import TARGET_MARK_ATTR, RecordingSession

LIST_PAGE = '''<!doctype html><html><head><meta charset="utf-8"></head><body>
<table>
  <tr><td>WO-1</td><td><button class="del-btn">删除</button></td></tr>
  <tr><td>WO-2</td><td><button class="del-btn">删除</button></td></tr>
  <tr><td>WO-3</td><td><button class="del-btn">删除</button></td></tr>
</table>
</body></html>'''

DETAIL_PAGE = '''<!doctype html><html><head><meta charset="utf-8"></head><body>
<h1>工单 WO-2</h1>
</body></html>'''


def _route(route):
    url = route.request.url
    body = DETAIL_PAGE if url.endswith('/detail') else LIST_PAGE
    route.fulfill(status=200, content_type='text/html; charset=utf-8', body=body)


class GotoDocIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls._pw = sync_playwright().start()
        cls._browser = cls._pw.chromium.launch()
        cls._ctx = cls._browser.new_context()
        cls._ctx.route('https://app.test/**', _route)

    @classmethod
    def tearDownClass(cls):
        cls._browser.close()
        cls._pw.stop()

    def make_session(self, step):
        session = RecordingSession.__new__(RecordingSession)
        session._context = self._ctx
        session._embed = False
        session._steps = [step]
        session._lock = threading.RLock()
        session._return_url = None
        session._nav_mute_until = 0.0
        session._doc_stamp = {}
        session._last_url = {}
        session._skip_nav = set()
        session._paused = False
        session._status = 'stopped'
        return session

    def test_page_can_be_brought_back_and_framed_again(self):
        page = self._ctx.new_page()
        page.goto('https://app.test/list', wait_until='domcontentloaded')

        snap = {
            'tag': 'button', 'role': 'button', 'text': '删除',
            'class_raw': 'del-btn',
            'css': 'table > tbody > tr:nth-of-type(2) > td:nth-of-type(2) > button',
            'row_scope': {'role': 'row', 'tag': 'tr', 'texts': ['WO-1', '删除', 'WO-2', '删除', 'WO-3', '删除']},
        }
        # 和录制时一样：给目标打标记，再让 _count 算出「点的是第几个」。
        page.evaluate(
            """([attr, m]) => {
                 const b = document.querySelectorAll('tr')[1].querySelector('button');
                 b.setAttribute(attr, m);
               }""",
            [TARGET_MARK_ATTR, 'm1'])
        counted = self._count(page, snap)
        primary, ordered = rank(counted)
        self.assertIsNotNone(primary)
        # 语义候选命中 3 个（表格里三个「删除」），录制时点的是第 2 个；
        # primary 可能是那条 count=1 的结构路径，所以 index 要去多匹配的候选里找。
        indexed = [c for c in ordered if c.get('index') is not None]
        self.assertTrue(indexed, ordered)
        self.assertEqual(indexed[0]['index'], 1)

        step = {
            'no': 1, 'type': 'click',
            'target': {'candidates': ordered, 'primary': primary, 'matches': None},
            '_doc': {'url': 'https://app.test/list'},
            '_frame': page.main_frame,
        }
        step['target']['matches'] = self._collect_matches(page, ordered)
        session = self.make_session(step)

        # 用户在列表页点了「删除」，页面跳到了详情页。
        page.goto('https://app.test/detail', wait_until='domcontentloaded')
        gone = session._highlight_now(1, None, 'playwright-ts')
        self.assertFalse(gone['ok'])
        self.assertEqual(gone['reason'], 'not_found')

        # 回到这一步的页面：地址是录制时记下的，不是浏览器后退。
        moved = session._goto_doc_now(1)
        self.assertTrue(moved['ok'])
        self.assertEqual(page.url, 'https://app.test/list')
        self.assertEqual(session._return_url, 'https://app.test/detail')

        # 回来之后必须框得出来，而且框的还是当时那一个。
        again = session._highlight_now(1, None, 'playwright-ts')
        self.assertTrue(again['ok'], again)
        self.assertEqual(again['count'], 3)
        self.assertEqual(again['active'], 1)

        # 看完能回到离开的地方。
        back = session._goto_back_now()
        self.assertTrue(back['ok'])
        self.assertEqual(page.url, 'https://app.test/detail')
        page.close()

    # 借用真实会话里的三个方法，不启动整条录制链路。
    def _count(self, page, snap):
        session = RecordingSession.__new__(RecordingSession)
        session._relation = RecordingSession._relation.__get__(session)
        session._clear_marks = RecordingSession._clear_marks.__get__(session)
        session._count = RecordingSession._count.__get__(session)
        session._row_anchors = RecordingSession._row_anchors.__get__(session)
        session._pools = {}
        counted = session._count(page.main_frame, build_specs(snap), mark='m1', snap=snap)
        return counted

    def _collect_matches(self, page, counted):
        session = RecordingSession.__new__(RecordingSession)
        session._collect_matches = RecordingSession._collect_matches.__get__(session)
        return session._collect_matches(page.main_frame, counted)


if __name__ == '__main__':
    unittest.main()
