# -*- coding: utf-8 -*-
"""拾取模式的交互：悬停高亮、模式提示、Esc 退出。

洞在哪：开了拾取模式，页面上什么都不会变——光标还是箭头，鼠标划过元素没有任何反应，
人不知道下一个点下去会拾到谁;点完只闪一个 1.7 秒的小徽标，回面板也没有任何「已拾取」的痕迹。

这一组测的是「看得见」那部分，全部直接跑真 recorder.js：注入、开拾取、移鼠标、读快照。
不复制一份高亮逻辑到测试里——那样测的是副本，改坏了产品代码照样绿。

最要紧的一条是「框住的 == 会拾到的」：拾取会把图标提升到所属按钮（closest(INTERACTIVE)），
高亮如果照抄 F12 直接框 e.target，就会框着小图标、登记整个按钮——比没有高亮更让人困惑。
"""
import unittest

from app.services.recording.session import RECORDER_JS

BASE_CSS = '<style>body{margin:0;font:14px sans-serif}</style>'

BUTTON = ('<!doctype html><html><head><meta charset="utf-8">%s</head><body>'
          '<div style="padding:40px">'
          '<button id="save" class="btn btn-primary" style="padding:4px 12px">保存</button>'
          '</div></body></html>') % BASE_CSS

# 图标按钮：按钮里只有一个 svg 矩形，鼠标真正落在 rect 上，可交互祖先才是 button。
ICON_BUTTON = ('<!doctype html><html><head><meta charset="utf-8">%s</head><body>'
               '<div style="padding:40px">'
               '<button id="del" style="width:80px;height:36px;padding:0">'
               '<svg width="16" height="16" style="display:block;margin:0 auto">'
               '<rect id="icon" width="16" height="16" fill="#444"/></svg>'
               '</button></div></body></html>') % BASE_CSS

# 元素贴着页面顶端：标签放上方就会被裁掉，必须翻到框内。
AT_TOP = ('<!doctype html><html><head><meta charset="utf-8">%s</head><body>'
          '<button id="top" style="position:absolute;top:0;left:0;padding:2px 8px">顶部</button>'
          '</body></html>') % BASE_CSS

# 页面顶部正中间：默认的标签位置正好压在模式条上，会被盖住。
CENTERED_TOP = ('<!doctype html><html><head><meta charset="utf-8">%s</head><body>'
                '<div style="text-align:center;padding-top:50px">'
                '<button id="cta" style="padding:6px 20px">居中按钮</button>'
                '</div></body></html>') % BASE_CSS

# 够高的页面：滚动测试需要真的能滚。
TALL = ('<!doctype html><html><head><meta charset="utf-8">%s</head><body>'
        '<div style="height:200px"></div>'
        '<button id="mid" style="padding:4px 10px">中间</button>'
        '<div style="height:2000px"></div>'
        '</body></html>') % BASE_CSS

# 元素紧贴视口底部：标签往上一格会顶到模式条，往下又没地方，只能压回视口内。
BOTTOM = ('<!doctype html><html><head><meta charset="utf-8">%s</head><body>'
          '<div style="position:absolute;bottom:0;left:40px">'
          '<button id="last" style="padding:4px 14px">末尾</button>'
          '</div></body></html>') % BASE_CSS

REMOVABLE = ('<!doctype html><html><head><meta charset="utf-8">%s</head><body>'
             '<div style="height:120px"></div>'
             '<button id="gone" style="padding:4px 10px">待删除</button>'
             '<div style="height:2000px"></div>'
             '</body></html>') % BASE_CSS


class PickHoverTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls._pw = sync_playwright().start()
        cls._browser = cls._pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls._browser.close()
        cls._pw.stop()

    def setUp(self):
        self.page = self._browser.new_page(viewport={'width': 900, 'height': 600})

    def tearDown(self):
        self.page.close()

    # ---- 夹具 ----

    def _open(self, html):
        """载入页面并装上真录制器。__pwEmit / __pwHudAction 是它往外说话的两个出口，
        必须在它安装之前就位（和 Python 侧 expose_binding 的时序一致）。"""
        self.page.set_content(html)
        self.page.evaluate("""() => {
            window.__events = [];
            window.__actions = [];
            window.__pwEmit = (raw) => window.__events.push(raw);
            window.__pwHudAction = (a) => window.__actions.push(a);
        }""")
        self.page.add_script_tag(path=RECORDER_JS)

    def _rect(self, selector):
        return self.page.eval_on_selector(selector, """el => {
            const r = el.getBoundingClientRect();
            return {x: r.left, y: r.top, w: r.width, h: r.height};
        }""")

    def _hover(self, selector):
        r = self._rect(selector)
        self.page.mouse.move(r['x'] + r['w'] / 2, r['y'] + r['h'] / 2)
        # 高亮走 requestAnimationFrame，等它跑完这一帧。
        self.page.wait_for_timeout(80)

    def _snap(self):
        return self.page.evaluate('window.__pwPickSnapshot()')

    def _pick_on(self):
        self.page.evaluate('window.__pwSetPick(true)')
        self.page.wait_for_timeout(30)

    # ---- 层的安装与拆除 ----

    def test_layer_is_absent_before_pick(self):
        self._open(BUTTON)
        snap = self._snap()
        self.assertFalse(snap['mounted'])
        self.assertFalse(snap['cursor'])

    def test_pick_on_mounts_layer_bar_and_crosshair(self):
        self._open(BUTTON)
        self._pick_on()
        snap = self._snap()
        self.assertTrue(snap['mounted'])
        self.assertTrue(snap['cursor'])
        # 模式条常驻：人不会忘了自己还在拾取态。
        self.assertTrue(snap['bar'])
        # 刚开还没移动鼠标：框是收着的，不该停在 (0,0) 那个角上。
        self.assertEqual(snap['box']['display'], 'none')

    def test_pick_off_removes_layer_and_restores_cursor(self):
        self._open(BUTTON)
        self._pick_on()
        self._hover('#save')
        self.page.evaluate('window.__pwSetPick(false)')
        self.page.wait_for_timeout(30)
        snap = self._snap()
        self.assertFalse(snap['mounted'])
        self.assertFalse(snap['cursor'])
        self.assertIsNone(snap['box'])
        # 十字准星的样式表也要摘掉，否则退出拾取后整站光标都变不回去。
        self.assertEqual(self.page.evaluate(
            "() => getComputedStyle(document.body).cursor"), 'auto')

    # ---- 悬停高亮 ----

    def test_hover_boxes_the_element_under_mouse(self):
        self._open(BUTTON)
        self._pick_on()
        rect = self._rect('#save')
        self._hover('#save')
        snap = self._snap()
        self.assertEqual(snap['box']['display'], 'block')
        self.assertEqual(snap['el'], 'button')
        self.assertEqual(snap['elId'], 'save')
        # 框比元素外扩 2px，是「描边压在元素外面」的常见做法，不挡内容。
        self.assertAlmostEqual(snap['box']['left'], rect['x'] - 2, places=1)
        self.assertAlmostEqual(snap['box']['top'], rect['y'] - 2, places=1)
        self.assertAlmostEqual(snap['box']['width'], rect['w'] + 4, places=1)
        self.assertAlmostEqual(snap['box']['height'], rect['h'] + 4, places=1)

    def test_hover_over_icon_boxes_its_button(self):
        """框住的必须就是会拾到的那个：鼠标在 rect 上，框要落在整个 button 上。

        拾取的规则是「先 closest(INTERACTIVE) 再用它」，高亮走同一个函数；
        照抄 F12 直接框 e.target 的话，这里会框住 16×16 的图标，
        而实际登记的是 80×36 的按钮——用户以为拾到了图标。
        """
        self._open(ICON_BUTTON)
        self._pick_on()
        button = self._rect('#del')
        self._hover('#icon')
        snap = self._snap()
        self.assertEqual(snap['el'], 'button')
        self.assertAlmostEqual(snap['box']['width'], button['w'] + 4, places=1)
        self.assertAlmostEqual(snap['box']['height'], button['h'] + 4, places=1)

    def test_label_shows_tag_id_class_and_size(self):
        self._open(BUTTON)
        self._pick_on()
        rect = self._rect('#save')
        self._hover('#save')
        text = self._snap()['tag']['text']
        self.assertIn('button#save', text)
        self.assertIn('.btn', text)
        self.assertIn('.btn-primary', text)
        # 尺寸要和实际一致：标签是给人核对「是不是这块」用的，数字错了不如不给。
        self.assertIn('%d × %d' % (round(rect['w']), round(rect['h'])), text)

    def test_label_moves_inside_when_no_room_above(self):
        self._open(AT_TOP)
        self._pick_on()
        self._hover('#top')
        snap = self._snap()
        # 元素贴顶，上方放不下标签：压到框内顶部，不能让它跑到视口外看不见。
        self.assertEqual(snap['tag']['display'], 'block')
        self.assertGreaterEqual(snap['tag']['top'], 0)
        self.assertLessEqual(snap['tag']['top'], snap['box']['top'] + snap['box']['height'])

    def test_label_dodges_the_mode_bar(self):
        """标签压在顶部模式条上会被盖掉——看不见的标签等于没有。

        实现在 renderPickFrame 里：标签默认位置与模式条矩形相交时，翻到框下方。
        """
        self._open(CENTERED_TOP)
        self._pick_on()
        self._hover('#cta')
        snap = self._snap()
        bar = snap['barRect']
        tag = snap['tag']
        self.assertIsNotNone(bar)
        self.assertEqual(tag['display'], 'block')

        def overlap(a, b):
            return (a['left'] < b['left'] + b['width'] and a['left'] + a['width'] > b['left']
                    and a['top'] < b['top'] + b['height'] and a['top'] + a['height'] > b['top'])

        self.assertFalse(overlap(tag, bar),
                         '标签压在模式条上了：tag=%s bar=%s' % (tag, bar))
        # 翻到了框下方，而不是被挤到别处
        self.assertGreaterEqual(tag['top'], snap['box']['top'] + snap['box']['height'])

    def test_label_stays_inside_the_viewport_at_the_bottom(self):
        self.page.set_viewport_size({'width': 900, 'height': 400})
        self._open(BOTTOM)
        self._pick_on()
        self._hover('#last')
        snap = self._snap()
        tag = snap['tag']
        self.assertEqual(tag['display'], 'block')
        self.assertLessEqual(tag['top'] + tag['height'], 400 + 1)

    def test_box_follows_scroll(self):
        self._open(TALL)
        self._pick_on()
        self._hover('#mid')
        before = self._snap()['box']['top']
        self.page.evaluate('window.scrollTo(0, 60)')
        self.page.wait_for_timeout(80)
        after = self._snap()['box']['top']
        # fixed 坐标不会自己动，必须靠 scroll 重算；不重算的话框会停在原地越飘越远。
        self.assertAlmostEqual(after, before - 60, places=0)

    def test_box_hides_when_element_is_gone(self):
        self._open(REMOVABLE)
        self._pick_on()
        self._hover('#gone')
        self.assertEqual(self._snap()['box']['display'], 'block')
        self.page.evaluate("() => document.getElementById('gone').remove()")
        # 触发一次重算（滚动是真实会发生的那种）
        self.page.evaluate('window.scrollTo(0, 10)')
        self.page.wait_for_timeout(80)
        snap = self._snap()
        self.assertEqual(snap['box']['display'], 'none')
        self.assertEqual(snap['tag']['display'], 'none')

    def test_hover_alone_never_emits_a_step(self):
        """只是划过，不能产生任何事件：拾取只认点击。"""
        self._open(BUTTON)
        self._pick_on()
        self._hover('#save')
        self.assertEqual(self.page.evaluate('window.__events'), [])

    # ---- 点击反馈 ----

    def test_click_flashes_green_frame(self):
        self._open(BUTTON)
        self._pick_on()
        self._hover('#save')
        self.page.mouse.down()
        self.page.mouse.up()
        self.page.wait_for_timeout(60)
        snap = self._snap()
        # 高亮「啪」地消失会让人分不清拾到了还是点空了，所以点中要闪一下。
        self.assertEqual(snap['flash']['display'], 'block')
        self.assertEqual(snap['flash']['opacity'], '1')
        kinds = [eval('e') for e in self.page.evaluate(
            'window.__events.map(e => JSON.parse(e).type)')]
        self.assertEqual(kinds, ['pick'])

    # ---- Esc ----

    def test_esc_leaves_pick_mode_through_the_backend(self):
        """状态存在 Python 那边：Esc 必须先通知后端，否则面板按钮还写着「退出拾取」。"""
        self._open(BUTTON)
        self._pick_on()
        self.page.keyboard.press('Escape')
        self.page.wait_for_timeout(50)
        self.assertEqual(self.page.evaluate('window.__actions'), ['pick-off'])
        self.assertFalse(self._snap()['mounted'])

    def test_esc_is_left_alone_when_not_picking(self):
        self._open(BUTTON)
        self.page.keyboard.press('Escape')
        self.page.wait_for_timeout(50)
        self.assertEqual(self.page.evaluate('window.__actions'), [])

    def test_esc_does_not_reach_the_page_while_picking(self):
        """拾取是模态状态：这一下 Esc 属于拾取，不该顺手关掉页面自己的弹窗。"""
        self._open(BUTTON)
        self.page.evaluate("""() => {
            window.__pageEsc = 0;
            document.addEventListener('keydown', (e) => {
                if (e.key === 'Escape') window.__pageEsc += 1;
            });
        }""")
        self._pick_on()
        self.page.keyboard.press('Escape')
        self.page.wait_for_timeout(50)
        self.assertEqual(self.page.evaluate('window.__pageEsc'), 0)

    # ---- 小尺寸 frame ----

    def test_small_viewport_skips_the_bar(self):
        """嵌入模式下被测页面在 iframe 里，矮 frame 放模式条会挤掉半屏内容。"""
        self.page.set_viewport_size({'width': 900, 'height': 200})
        self._open(BUTTON)
        self._pick_on()
        snap = self._snap()
        self.assertTrue(snap['mounted'])
        self.assertFalse(snap['bar'])
        # 没有条也要有框：高亮本身不能跟着一起缩水。
        self._hover('#save')
        self.assertEqual(self._snap()['box']['display'], 'block')


if __name__ == '__main__':
    unittest.main()
