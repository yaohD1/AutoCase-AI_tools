"""结构路径（css）必须自证唯一：录出来的选择器在页面上只许命中目标这一个元素。

洞在哪：旧 cssPath 只往上爬 6 层，而且从不验证。它只在「同一层有同名兄弟」时才补
`:nth-of-type(n)`，于是 `div > div > div > div > div > button` 这种路径——每层都没有
重名兄弟——会从文档任意位置开始匹配。实测抽样 23 条这样的路径大量不唯一，最多一条命中 13 个。
生成时它被当成「结构定位」写进脚本，能跑通纯属运气。

现在改成「最短且唯一」：每加一层就在页面上数一次，命中 1 个立刻停手；6 层不够就继续
往上爬（最多 12 层），爬到根还不唯一才返回最长的那条，交给 count 机制按 .nth(i) 降级。

这一组测试直接跑真 recorder.js：在页面里注入它、点一下目标、读它 emit 出来的快照。
不复制一份 cssPath 到测试里——那样测的是副本，不是产品代码。
"""
import json
import unittest

from app.services.recording.session import RECORDER_JS


def _wrap(inner, depth=5):
    """套 depth 层无 class 无 id 的 div：每层只有一个同类子节点，正是旧算法的盲区。"""
    for _ in range(depth):
        inner = '<div>%s</div>' % inner
    return inner


# 两个面板，内部结构完全一样、且每一层都没有同名兄弟。
# 旧的 6 层路径 = 'div > div > div > div > div > button'，两个面板各命中一次。
TWO_PANELS = ('<!doctype html><html><head><meta charset="utf-8"></head><body>'
              '<div class="panel">%s</div><div class="panel">%s</div>'
              '</body></html>') % (
    _wrap('<button data-idx="a">确定</button>'),
    _wrap('<button data-idx="b">确定</button>'),
)

# 浅页面：3 层就能唯一，不该被硬爬到 6 层（路径越长越脆）。
SHALLOW = '''<!doctype html><html><head><meta charset="utf-8"></head><body>
<div id="root"><div class="toolbar"><button data-idx="only">保存</button></div></div>
</body></html>'''

# 表格里的一行：行内按钮是录制里最常见的形态，路径也得唯一。
TABLE = '''<!doctype html><html><head><meta charset="utf-8"></head><body>
<table><tbody>
  <tr><td>WO-1</td><td><button data-idx="r1">删除</button></td></tr>
  <tr><td>WO-2</td><td><button data-idx="r2">删除</button></td></tr>
</tbody></table>
</body></html>'''


class CssPathOnRealPageTest(unittest.TestCase):
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
        self.page = self._browser.new_page()

    def tearDown(self):
        self.page.close()

    def _snap_of_click(self, html, selector):
        """注入真 recorder.js，点一下目标，取它当场算出的快照。"""
        self.page.set_content(html)
        # __pwEmit 是 recorder.js 往外送事件的唯一出口；必须在它安装前就就位。
        self.page.evaluate("""() => {
            window.__events = [];
            window.__pwEmit = (raw) => window.__events.push(raw);
        }""")
        self.page.add_script_tag(path=RECORDER_JS)
        self.page.click(selector)
        events = [json.loads(raw) for raw in self.page.evaluate('window.__events')]
        clicks = [e for e in events if e.get('type') == 'click']
        self.assertTrue(clicks, '没有录到 click 事件：%s' % [e.get('type') for e in events])
        return clicks[-1]['snap']

    def test_path_climbs_until_unique(self):
        snap = self._snap_of_click(TWO_PANELS, '[data-idx="b"]')
        path = snap['css']
        # 6 层内不可能唯一（两个面板一模一样），所以必须爬到第 7 层、把面板本身锚进来。
        parts = path.split(' > ')
        self.assertGreater(len(parts), 6, '路径没有继续上溯：%s' % path)
        self.assertIn(':nth-of-type(2)', parts[0],
                      '唯一性来自「第 2 个面板」，不是来自位置巧合：%s' % path)
        self.assertEqual(self.page.eval_on_selector_all(path, 'els => els.length'), 1, path)
        self.assertEqual(self.page.eval_on_selector_all(
            path, "els => els.map(e => e.getAttribute('data-idx'))"), ['b'])

    def test_old_six_level_path_would_have_hit_two(self):
        """把改造前的算法照抄一遍，确认它给出的路径真的不唯一——否则上面的测试毫无意义。"""
        self.page.set_content(TWO_PANELS)
        legacy = self.page.evaluate("""(sel) => {
            const el = document.querySelector(sel);
            const parts = [];
            let cur = el;
            while (cur && cur.nodeType === 1 && parts.length < 6) {
                let part = cur.tagName.toLowerCase();
                if (cur === document.documentElement) { parts.unshift(part); break; }
                const sibs = cur.parentElement
                    ? Array.from(cur.parentElement.children).filter(c => c.tagName === cur.tagName) : [];
                if (sibs.length > 1) part += ':nth-of-type(' + (sibs.indexOf(cur) + 1) + ')';
                parts.unshift(part);
                cur = cur.parentElement;
            }
            return parts.join(' > ');
        }""", '[data-idx="b"]')
        self.assertEqual(self.page.eval_on_selector_all(legacy, 'els => els.length'), 2, legacy)

    def test_short_unique_path_is_not_padded(self):
        snap = self._snap_of_click(SHALLOW, '[data-idx="only"]')
        self.assertEqual(snap['css'], 'div > button')
        self.assertEqual(self.page.eval_on_selector_all(snap['css'], 'els => els.length'), 1)

    def test_path_in_table_row_is_unique(self):
        snap = self._snap_of_click(TABLE, '[data-idx="r2"]')
        self.assertEqual(self.page.eval_on_selector_all(snap['css'], 'els => els.length'), 1,
                         snap['css'])
        self.assertEqual(self.page.eval_on_selector_all(
            snap['css'], "els => els.map(e => e.getAttribute('data-idx'))"), ['r2'])

    def test_snapshot_carries_raw_class(self):
        """class 是下一步（class 前缀候选）的唯一输入，漏采就等于这个能力不存在。"""
        snap = self._snap_of_click(SHALLOW, '[data-idx="only"]')
        self.assertIsNone(snap['class_raw'])  # 目标自己没有 class
        self.assertEqual(snap['tag'], 'button')

    def test_class_attribute_is_captured_trimmed(self):
        html = ('<!doctype html><html><head><meta charset="utf-8"></head><body>'
                '<div><button class="  _saveBtn_1a2b3_7  " data-idx="x">保存</button></div>'
                '</body></html>')
        snap = self._snap_of_click(html, '[data-idx="x"]')
        self.assertEqual(snap['class_raw'], '_saveBtn_1a2b3_7')


if __name__ == '__main__':
    unittest.main()
