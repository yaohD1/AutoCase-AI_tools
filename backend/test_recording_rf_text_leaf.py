# -*- coding: utf-8 -*-
"""RF 的「最内层文本」定位：div 这类容器不再只能掉到 css。

背景：div 按钮上的文字是唯一的，但直接写 `//div[normalize-space(.)='X']` 会命中
自己 + 每一层父容器（实测 4 个），所以 TEXT_TAGS 一直把 div 排除在外，div 按钮在
RF 里只能落到 `rf_css`（结构定位，页面一改版就点错）。

补的写法多一个约束：**不能再有「同标签、同样文本」的后代**，只留最内层那一层。
文字唯一时命中 1 个且正是被点的那个；真的有两处相同文本时仍然是 2 个，不会被
误判成唯一——这条对照用例比正例更重要。
"""
import http.server
import threading
import time
import unittest

from app.services.recording.locators import RF_KINDS, build_specs, pick_for
from app.services.recording.session import RecordingSession

RF = 'robot-selenium'

ROW = ('<div class="row"><div class="wrap"><div class="cell"><div class="tag">{}</div></div>'
       '</div></div>')
SPAN_ROW = ('<div class="row"><div class="wrap"><div class="cell"><div class="tag">'
            '<span>{}</span></div></div></div></div>')

PAGES = {
    '/div': '<div class="list">' + ''.join(
        ROW.format(t) for t in ('1100', 'UE1220 5/7', '1130', '980')) + '</div>',
    '/span': '<div class="list">' + ''.join(
        SPAN_ROW.format(t) if t == 'UE1220 5/7' else ROW.format(t)
        for t in ('1100', 'UE1220 5/7', '1130', '980')) + '</div>',
    '/dup': '<div class="list">' + ''.join(
        ROW.format(t) for t in ('1100', 'UE1220 5/7', 'UE1220 5/7', '980')) + '</div>',
}


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAGES.get(self.path.split('?')[0], PAGES['/div']).encode('utf-8')
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
        except Exception as exc:  # noqa: BLE001 - 交回测试线程再抛
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


def leaf_specs(**snap):
    base = {'tag': 'div'}
    base.update(snap)
    return [s for s in build_specs(base) if s['kind'] == 'rf_text_leaf']


class SpecTest(unittest.TestCase):
    """不启动浏览器：只看候选怎么生成的。"""

    def test_div_gets_leaf_text_candidate(self):
        specs = leaf_specs(text='UE1220 5/7')
        self.assertEqual(len(specs), 1)
        expr = specs[0]['args'][0]
        self.assertTrue(expr.startswith('xpath='))
        # 关键约束：不能再有「同标签、同样文本」的后代，否则祖先会被一起算进来。
        self.assertIn("not(descendant::div[normalize-space(.)='UE1220 5/7'])", expr)
        self.assertEqual(specs[0]['desc'], 'rf text:UE1220 5/7')

    def test_button_still_uses_plain_text_xpath(self):
        # button 不会嵌套 button，普通写法本来就安全：不能多出一条重复候选。
        kinds = [s['kind'] for s in build_specs({'tag': 'button', 'text': '保存'})]
        self.assertIn('rf_xpath', kinds)
        self.assertNotIn('rf_text_leaf', kinds)

    def test_long_text_is_skipped(self):
        self.assertEqual(leaf_specs(text='长' * 41), [])

    def test_leaf_text_ranks_before_structure(self):
        # 语义定位必须排在 rf_css 之前，否则生成还是会用结构路径。
        self.assertLess(RF_KINDS.index('rf_text_leaf'), RF_KINDS.index('rf_css'))
        self.assertLess(RF_KINDS.index('rf_text_leaf'), RF_KINDS.index('rf_row_scoped'))
        cands = [{'kind': 'rf_text_leaf', 'desc': "xpath=//div[.='X']", 'count': 1, 'relation': 'self'},
                 {'kind': 'rf_css', 'desc': 'css=div > div', 'count': 1, 'relation': 'self'}]
        self.assertEqual(pick_for(RF, cands)['kind'], 'rf_text_leaf')


class RecordingTest(unittest.TestCase):
    """真实浏览器：候选的命中数，以及命中的是不是被点的那个元素。"""

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
        in_thread(session, lambda: page.wait_for_selector('.tag'))
        box = in_thread(session, lambda: page.locator('.tag').nth(1).bounding_box())
        x, y = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
        in_thread(session, lambda: page.mouse.move(x, y))
        in_thread(session, lambda: page.mouse.down())
        time.sleep(0.4)          # 让 pointerdown 那轮计数跑完
        in_thread(session, lambda: page.mouse.up())
        self.assertTrue(wait_for(
            lambda: any(s['type'] == 'click' for s in session.snapshot()['steps'])))
        step = [s for s in session.snapshot()['steps'] if s['type'] == 'click'][-1]
        return session, step['target']

    @staticmethod
    def _leaf(target):
        return next((c for c in target.get('candidates') or []
                     if c.get('kind') == 'rf_text_leaf'), None)

    def test_unique_text_inside_nested_divs_is_a_real_locator(self):
        # 「1100 / UE1220 5/7 / 1130 / 980」四行，文字各不同：一条就能锁死。
        session, target = self._click_second('/div')
        leaf = self._leaf(target)
        self.assertIsNotNone(leaf, [c['kind'] for c in target['candidates']])
        self.assertEqual(leaf['count'], 1)
        self.assertEqual(leaf['relation'], 'self')
        # RF 侧真的会选它，而不是退到 rf_css
        picked = pick_for(RF, target['candidates'])
        self.assertEqual(picked['kind'], 'rf_text_leaf')

    def test_text_wrapped_in_span_still_hits_the_clicked_div(self):
        # 文字包在 <span> 里：最内层的 div 仍然是被点的那个，不能跑到 span 上去。
        session, target = self._click_second('/span')
        leaf = self._leaf(target)
        self.assertIsNotNone(leaf, [c['kind'] for c in target['candidates']])
        self.assertEqual(leaf['count'], 1)
        self.assertEqual(leaf['relation'], 'self')

    def test_same_text_twice_is_still_ambiguous(self):
        # 两行文字一样：这条写法必须老实返回 2，不许把重复内容说成唯一。
        session, target = self._click_second('/dup')
        leaf = self._leaf(target)
        self.assertIsNotNone(leaf)
        self.assertEqual(leaf['count'], 2)


if __name__ == '__main__':
    unittest.main()
