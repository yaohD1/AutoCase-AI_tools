# -*- coding: utf-8 -*-
"""Playwright 的文本候选：白名单要和 RF 对齐（多一个 div）。

以前只对 button / a / label / span / div 生成 text 候选，漏了 li / td / p / h1~h6 / strong。
后果是可点的 li（element-ui、antd 的菜单项就是 `<li class="el-menu-item">`）文字明明唯一，
Playwright 侧却只能退到 `locator('ul > li:nth-of-type(2)')` 这种结构路径，而 Robot 侧能
按文字定位——同一个步骤，两套框架一个语义一个结构，看起来就像系统没认出文字。

PW 的文本引擎只命中「最小的那个元素」，所以白名单可以放心扩：不会像 RF 的 `//div[...]`
那样把祖先容器一起算进来。
"""
import http.server
import threading
import time
import unittest

from app.services.recording.locators import PW_TEXT_TAGS, TEXT_TAGS, build_specs, pick_for
from app.services.recording.session import RecordingSession

PW = 'playwright-ts'
T = 'UE1220 5/7'
ITEMS = ('1100', T, '1130', '1240')

PAGES = {
    '/li': '<ul>' + ''.join('<li class="hit">%s</li>' % t for t in ITEMS) + '</ul>',
    '/li-span': '<ul>' + ''.join('<li class="hit"><span>%s</span></li>' % t for t in ITEMS) + '</ul>',
    '/td': '<table><tr>' + ''.join('<td class="hit">%s</td>' % t for t in ITEMS) + '</tr></table>',
}

STYLE = '''<style>body{font:14px sans-serif}
.hit{display:block;padding:8px;border:1px solid #ddd;margin:4px 0;cursor:pointer}
table{border-collapse:collapse}td{border:1px solid #ddd;padding:8px}</style>'''


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = ('<!doctype html><html><head><meta charset="utf-8">' + STYLE + '</head><body>'
                + PAGES.get(self.path.split('?')[0], PAGES['/li']) + '</body></html>').encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def in_thread(session, fn, timeout=20):
    done, box = threading.Event(), {}

    def task():
        try:
            box['value'] = fn()
        except Exception as exc:  # noqa: BLE001
            box['error'] = exc
        finally:
            done.set()

    session._inbox.put(task)
    if not done.wait(timeout):
        raise RuntimeError('录制线程超时')
    if 'error' in box:
        raise box['error']
    return box['value']


def wait_for(check, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if check():
            return True
        time.sleep(0.05)
    return check()


def text_specs(**snap):
    base = {'tag': 'li'}
    base.update(snap)
    return [s for s in build_specs(base) if s['kind'] == 'text']


class SpecTest(unittest.TestCase):
    """不启动浏览器：只看哪些标签会生成 text 候选。"""

    def test_every_rf_text_tag_also_gets_a_playwright_candidate(self):
        # 两个框架的白名单必须对齐，否则同一元素会出现「一边语义、一边结构」。
        for tag in TEXT_TAGS:
            with self.subTest(tag=tag):
                self.assertTrue(text_specs(tag=tag, text='保存'), tag)

    def test_div_keeps_its_candidate(self):
        # div 不在 TEXT_TAGS 里（RF 侧走 rf_text_leaf），但 PW 必须保留。
        self.assertTrue(text_specs(tag='div', text=T))

    def test_whitelist_is_rf_plus_div(self):
        self.assertEqual(set(PW_TEXT_TAGS), set(TEXT_TAGS) | {'div'})

    def test_text_is_exact(self):
        spec = text_specs(tag='li', text=T)[0]
        self.assertEqual(spec['kwargs'], {'exact': True})

    def test_unknown_tag_and_long_text_are_skipped(self):
        self.assertEqual(text_specs(tag='article', text=T), [])
        self.assertEqual(text_specs(tag='li', text='长' * 41), [])


class RecordingTest(unittest.TestCase):
    """真实浏览器：li / td 现在应该按文字定唯一，不再退 css。"""

    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = 'http://127.0.0.1:%d' % cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        RecordingSession._current = None

    def tearDown(self):
        RecordingSession.dispose_current()

    def _click_second(self, path):
        session = RecordingSession.start_new(
            self.base + path, headless=True, ask_daemon=False,
            driver=lambda s, p: setattr(s, '_test_page', p))
        self.assertTrue(wait_for(lambda: getattr(session, '_test_page', None) is not None))
        page = session._test_page
        in_thread(session, lambda: page.wait_for_selector('.hit'))
        box = in_thread(session, lambda: page.locator('.hit').nth(1).bounding_box())
        x, y = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
        in_thread(session, lambda: page.mouse.move(x, y))
        in_thread(session, lambda: page.mouse.down())
        time.sleep(0.4)
        in_thread(session, lambda: page.mouse.up())
        self.assertTrue(wait_for(
            lambda: any(s['type'] == 'click' for s in session.snapshot()['steps'])))
        step = [s for s in session.snapshot()['steps'] if s['type'] == 'click'][-1]
        return step['target']

    def test_clickable_li_uses_text_not_structure(self):
        target = self._click_second('/li')
        picked = pick_for(PW, target['candidates'])
        self.assertIsNotNone(picked)
        self.assertEqual(picked['kind'], 'text')
        self.assertEqual(picked['count'], 1)
        self.assertEqual(picked['relation'], 'self')

    def test_clickable_td_uses_text_not_structure(self):
        target = self._click_second('/td')
        picked = pick_for(PW, target['candidates'])
        self.assertEqual(picked['kind'], 'text')

    def test_text_inside_span_still_wins_over_structure(self):
        # 文字在子 span 里时命中的是 span（relation=descendant），点击效果一样；
        # 关键是别退成 css——那才是「人眼能看出、系统认不出」。
        target = self._click_second('/li-span')
        picked = pick_for(PW, target['candidates'])
        self.assertEqual(picked['kind'], 'text')
        self.assertEqual(picked['count'], 1)


if __name__ == '__main__':
    unittest.main()
