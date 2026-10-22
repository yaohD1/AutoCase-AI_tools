"""两套框架的定位投影必须指向同一个元素（不变量测试）。

背景：录制只采一份 DOM 快照，生成时才挑框架。只要 RF 组的投影写得比 Playwright 组窄，
同一个元素在 PW 下是语义定位、在 RF 下就退化成 css=div:nth-of-type(3) > div，
于是「换个框架」实际换了定位质量。这个测试把「等价」变成可执行的约束：

1. 两边都必须存在 count == 1 的候选；
2. 两边各自选中的候选，命中的元素都必须正好是录制时那一个；
3. 只要快照里有语义信号，RF 就不许掉到 rf_css；
4. RF 组任何一条「恰好唯一」的候选，都不许指向别的元素（防「唯一但指错」）。
"""
import unittest

from app.services.recording.locators import (
    PW_KINDS, RF_KINDS, build_specs, pool_for,
)

PW = 'playwright-ts'
RF = 'robot-selenium'

HTML = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<form id="basic">
  <div><label for="user">用户名</label><input id="user" name="user" type="text" data-idx="1"></div>
  <div><label>密码 <input id="pwd" name="pwd" type="password" data-idx="2"></label></div>
  <button id="login" type="submit" data-idx="3">登录</button>
  <div role="button" tabindex="0" data-idx="4">提交订单</div>
  <button id="edit" aria-label="编辑" data-idx="5"><span class="icon"></span></button>
  <input id="remember" type="checkbox" name="remember" data-idx="6">
  <textarea id="remark" name="remark" placeholder="请输入备注" data-idx="7"></textarea>
  <span data-testid="row-delete" data-idx="8">删除记录</span>
  <select id="city" aria-label="城市" data-idx="9"><option>北京</option><option>上海</option></select>
  <a id="help" href="/help" data-idx="10">帮助中心</a>
  <ul><li data-idx="11">首页</li><li data-idx="12">设置</li></ul>
</form>
<table><tbody>
  <tr><td><button data-idx="21">移除</button></td></tr>
  <tr><td><button data-idx="22">移除</button></td></tr>
  <tr><td><button data-idx="23">移除</button></td></tr>
</tbody></table>
</body></html>"""

# 与 recorder.js 的 snap() 保持一致的最小实现。改动 recorder.js 的采集字段时，
# 这个镜像也要跟着改，否则测试守的是一份不存在的快照。
SNAP_JS = r"""
(idx) => {
  const el = document.querySelector('[data-idx="' + idx + '"]');
  if (!el) return null;
  const implicitRole = (node) => {
    const t = node.tagName.toLowerCase();
    if (t === 'button') return 'button';
    if (t === 'a' && node.href) return 'link';
    if (t === 'select') return 'combobox';
    if (t === 'textarea') return 'textbox';
    if (t === 'input') {
      const ty = (node.type || 'text');
      if (ty === 'button' || ty === 'submit' || ty === 'reset') return 'button';
      if (ty === 'checkbox') return 'checkbox';
      if (ty === 'radio') return 'radio';
      return 'textbox';
    }
    return null;
  };
  const labelText = (node) => {
    if (node.id) {
      const l = document.querySelector('label[for="' + CSS.escape(node.id) + '"]');
      if (l) return l.innerText.trim();
    }
    const p = node.closest('label');
    return p ? p.innerText.trim() : null;
  };
  const cssPath = (node) => {
    const parts = [];
    let cur = node;
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
  };
  const text = el.tagName === 'SELECT' ? null : ((el.innerText || '').trim().slice(0, 80) || null);
  const label = labelText(el);
  const noTextName = ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName);
  const name = el.getAttribute('aria-label') || label || (noTextName ? null : text);
  return {
    tag: el.tagName.toLowerCase(),
    type: el.getAttribute('type'),
    role: el.getAttribute('role') || implicitRole(el),
    role_attr: el.getAttribute('role'),
    name: name ? name.slice(0, 80) : null,
    label: label,
    aria_label: el.getAttribute('aria-label'),
    placeholder: el.getAttribute('placeholder'),
    testid: el.getAttribute('data-testid'),
    text: text,
    id: el.id || null,
    attr_name: el.getAttribute('name'),
    css: cssPath(el),
  };
}
"""

IDX_JS = "els => els.map(e => e.getAttribute('data-idx'))"

# 只有一个无 label 的 select：验证「没有可访问名称的控件」也能拿到语义定位。
ONLY_SELECT = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<select data-idx="1"><option>北京</option><option>上海</option></select>
</body></html>"""

# (data-idx, 说明)。这些都是真实页面里最常见的元素形态。
CASES = [
    (1, 'input + label[for]'),
    (2, 'input 包在 label 里'),
    (3, '文字按钮'),
    (4, 'div role=button'),
    (5, '只有 aria-label 的图标按钮'),
    (6, 'checkbox（只有 name）'),
    (7, 'textarea + placeholder'),
    (8, 'span + data-testid'),
    (9, 'select + aria-label'),
    (10, '链接文字'),
    (11, '普通 li 菜单项'),
    (12, '另一个 li 菜单项'),
]


class LocatorParityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._pw.stop()

    def setUp(self):
        self.page = self.browser.new_page()
        self.page.set_content(HTML)

    def tearDown(self):
        self.page.close()

    def _measure(self, spec):
        try:
            locator = getattr(self.page, spec['method'])(*spec['args'], **spec['kwargs'])
            count = locator.count()
        except Exception as error:  # 选择器本身写错，直接暴露
            self.fail('候选无法执行：%s (%s)' % (spec['desc'], error))
        if count == 0:
            return 0, []
        return count, locator.evaluate_all(IDX_JS)

    def _resolve(self, idx):
        """返回 [(spec, count, 命中的 data-idx 列表)]。"""
        snap = self.page.evaluate(SNAP_JS, idx)
        self.assertIsNotNone(snap, '快照为空，data-idx=%s 没找到' % idx)
        return [(spec,) + self._measure(spec) for spec in build_specs(snap)]

    def _pick(self, measured, framework):
        """按目标框架的偏好顺序取第一条 count == 1 的候选，与 locators.pick_for 同规则。"""
        by_id = {id(m[0]): m for m in measured}
        for spec in pool_for(framework, [m[0] for m in measured]):
            spec, count, hit = by_id[id(spec)]
            if count == 1:
                return spec, hit
        return None, None

    def test_both_frameworks_pick_the_recorded_element(self):
        for idx, name in CASES:
            with self.subTest(element=name):
                measured = self._resolve(idx)
                pw_spec, pw_hit = self._pick(measured, PW)
                rf_spec, rf_hit = self._pick(measured, RF)
                self.assertIsNotNone(pw_spec, 'Playwright 组没有唯一候选')
                self.assertIsNotNone(rf_spec, 'RF 组没有唯一候选')
                self.assertEqual(pw_hit, [str(idx)],
                                 'Playwright 选中的定位指向了别的元素：%s' % pw_spec['desc'])
                self.assertEqual(rf_hit, [str(idx)],
                                 'RF 选中的定位指向了别的元素：%s' % rf_spec['desc'])

    def test_no_rf_candidate_points_elsewhere(self):
        """RF 组里任何一条「恰好唯一」的候选都不许指错元素——唯一但指错比不唯一更危险。"""
        for idx, name in CASES:
            with self.subTest(element=name):
                for spec, count, hit in self._resolve(idx):
                    if spec['kind'] in RF_KINDS and count == 1:
                        self.assertEqual(hit, [str(idx)], 'RF 候选唯一但指错：%s' % spec['desc'])

    def test_no_pw_candidate_points_elsewhere(self):
        """Playwright 组做同样的约束：新增的裸 role 候选靠「页面上只有一个」成立，更容易选错。"""
        for idx, name in CASES:
            with self.subTest(element=name):
                for spec, count, hit in self._resolve(idx):
                    if spec['kind'] in PW_KINDS and count == 1:
                        self.assertEqual(hit, [str(idx)], 'PW 候选唯一但指错：%s' % spec['desc'])

    def test_widget_without_accessible_name_stays_semantic(self):
        """没有 label / aria-label 的 select：Playwright 以前只能落到 css，现在应走裸 role。"""
        self.page.set_content(ONLY_SELECT)
        measured = [(spec,) + self._measure(spec) for spec in build_specs(self.page.evaluate(SNAP_JS, 1))]
        self.assertIn('role_bare', [m[0]['kind'] for m in measured])
        spec, hit = self._pick(measured, PW)
        self.assertEqual(spec['kind'], 'role_bare', 'Playwright 仍落到结构定位：%s' % spec['desc'])
        self.assertEqual(hit, ['1'])

    def test_rf_keeps_semantic_locator_for_every_widget(self):
        """只要快照里有语义信号，RF 就必须给出非 css 的唯一候选（覆盖率对齐的核心断言）。"""
        for idx, name in CASES:
            with self.subTest(element=name):
                measured = self._resolve(idx)
                rf_spec, _ = self._pick(measured, RF)
                self.assertIsNotNone(rf_spec)
                self.assertNotEqual(rf_spec['kind'], 'rf_css',
                                    'RF 掉到结构定位：%s（%s）' % (rf_spec['desc'], name))

    def test_duplicate_buttons_fall_back_consistently(self):
        """重复按钮：两边都不该硬凑语义唯一，而应一致地落到结构定位同一个元素。"""
        for idx in (21, 22, 23):
            with self.subTest(idx=idx):
                measured = self._resolve(idx)
                pw_spec, pw_hit = self._pick(measured, PW)
                rf_spec, rf_hit = self._pick(measured, RF)
                self.assertIsNotNone(pw_spec)
                self.assertIsNotNone(rf_spec)
                self.assertEqual(pw_spec['kind'], 'css')
                self.assertEqual(rf_spec['kind'], 'rf_css')
                self.assertEqual(pw_hit, [str(idx)])
                self.assertEqual(rf_hit, [str(idx)])


if __name__ == '__main__':
    unittest.main()
