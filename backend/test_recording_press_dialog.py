"""按键（press）与弹窗（dialog）的录制与回放。

这两类都是「录得下来、但回放跑不过去」的缺口：

- 搜索框里按 Enter 提交，脚本里只有 fill，回放不提交，下一步必崩；
- 点删除弹出 confirm，脚本没接管，页面在弹窗出现的那一刻就挂起，直到整体超时。

录制侧还要守住两条不能被误伤的规则：
1. 焦点在按钮上按 Enter，浏览器会补一次 click —— 这时该记 click，不该记 press（否则回放点两次）；
2. 输入框里按 Enter 提交表单，浏览器会给提交按钮补一次 click —— 这次 click 不该单独记（否则回放提交两遍）。
两者都靠 click 事件 detail 为 0（非鼠标）加上「键落在哪个元素上」区分。
"""
import http.server
import threading
import time
import unittest

from app.services.recording import ir
from app.services.recording.replay import replay_checked
from app.services.recording.session import RecordingSession

PW = 'playwright-ts'

SEARCH_PAGE = (
    '<!doctype html><html><head><meta charset="utf-8"></head><body>'
    '<h1>搜索</h1>'
    '<form action="/result" method="get">'
    '<input name="kw" placeholder="搜索关键词">'
    '<button type="submit">搜索</button>'
    '</form></body></html>'
)

RESULT_PAGE = (
    '<!doctype html><html><head><meta charset="utf-8"></head><body>'
    '<h1>结果</h1><div id="kw"></div>'
    '<script>document.getElementById("kw").textContent = '
    'new URLSearchParams(location.search).get("kw") || "";</script>'
    '</body></html>'
)

CONFIRM_PAGE = (
    '<!doctype html><html><head><meta charset="utf-8"></head><body>'
    '<h1>设备</h1>'
    '<button id="del" type="button">删除</button>'
    '<button id="noop" type="button">无操作</button>'
    '<div id="out"></div>'
    '<script>document.getElementById("del").onclick = function () {'
    ' document.getElementById("out").textContent ='
    ' confirm("确定删除该设备？") ? "已删除" : "已取消"; };'
    'document.getElementById("noop").onclick = function () {'
    ' document.getElementById("out").textContent = "只点了一下"; };</script>'
    '</body></html>'
)

NOTE_PAGE = (
    '<!doctype html><html><head><meta charset="utf-8"></head><body>'
    '<h1>备注</h1>'
    '<textarea placeholder="备注"></textarea>'
    '<button type="button">保存</button>'
    '</body></html>'
)

READONLY_PAGE = (
    '<!doctype html><html><head><meta charset="utf-8"></head><body>'
    '<h1>筛选</h1>'
    '<input id="ro" readonly placeholder="请选择日期">'
    '<div id="panel" hidden><button id="pick" type="button">确定</button></div>'
    '<script>document.getElementById("ro").onclick = function () {'
    ' document.getElementById("panel").hidden = false; };'
    'document.getElementById("pick").onclick = function () {'
    ' document.getElementById("ro").value = "2026-10-22"; };</script>'
    '</body></html>'
)


# 输入第一个字符之后，页面把 DOM 结构改掉（组件重渲染的常见形态），
# 用来验证「CSS 路径不能当作元素身份」。
SHIFT_PAGE = (
    '<!doctype html><html><head><meta charset="utf-8"></head><body>'
    '<h1>登录</h1>'
    '<label>账号 <input name="user"></label>'
    '<label>密码 <span id="host"><input name="pwd" type="password"></span></label>'
    '<script>'
    'let done = false;'
    'document.querySelector("input[name=pwd]").addEventListener("input", function () {'
    '  if (done) return; done = true;'
    '  const inp = this, host = document.getElementById("host");'
    '  if (MODE === "wrap") {'
    '    const d = document.createElement("div"); d.className = "el-input";'
    '    host.replaceChild(d, inp); d.appendChild(inp);'
    '  } else {'
    '    const h = document.createElement("input");'
    '    h.type = "hidden"; h.name = "otp"; host.insertBefore(h, inp);'
    '  }'
    '  inp.focus(); inp.setSelectionRange(inp.value.length, inp.value.length);'
    '});'
    '</script></body></html>'
)


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith('/result'):
            body = RESULT_PAGE
        elif self.path.startswith('/confirm'):
            body = CONFIRM_PAGE
        elif self.path.startswith('/note'):
            body = NOTE_PAGE
        elif self.path.startswith('/readonly'):
            body = READONLY_PAGE
        elif self.path.startswith('/shift'):
            body = SHIFT_PAGE.replace('MODE', '"%s"' % self.path.split('/')[1].split('-')[-1])
        else:
            body = SEARCH_PAGE
        raw = body.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


def _idle_scenario(session, page):
    # Playwright 的同步对象不能跨线程：页面的所有操作都要回到录制线程里做。
    session._test_page = page


def _in_recording_thread(session, fn, timeout=20):
    done = threading.Event()
    box = {}

    def task():
        try:
            box['value'] = fn()
        except Exception as exc:  # noqa: BLE001 - 交回测试线程再抛
            box['error'] = exc
        finally:
            done.set()

    session._inbox.put(task)
    if not done.wait(timeout):
        raise AssertionError('录制线程没有执行排入的操作')
    if 'error' in box:
        raise box['error']
    return box.get('value')


def _wait_for(predicate, timeout=10):
    waited = 0.0
    while waited < timeout:
        if predicate():
            return True
        time.sleep(0.05)
        waited += 0.05
    return predicate()


def _text_assert(no, text):
    """整页文字断言：回放结束时用它确认页面走到了预期的分支。"""
    step = ir.make_step('assert', no=no)
    step['intent'] = {'type': 'page_text_contains', 'target': None, 'value': text, 'seconds': None}
    return step


class _Base(unittest.TestCase):
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

    def _start(self, path):
        return RecordingSession.start_new(self.base + path, headless=True, ask_daemon=False, driver=_idle_scenario)

    @staticmethod
    def _types(session):
        return [step['type'] for step in session.ir()]


class TextEditRecordingTest(_Base):
    """在文本框里输错再改，整个过程只该留一条「输入」。

    值的变化已经由 fill 的全量快照表达（同一元素的连续输入本来就合并成一条），
    退格 / 方向键 / 空格在文本框里没有独立语义。录成 press 不但多一步，
    还会打断合并——错一个字改一下，就变成 退格+输入、退格+输入 好几条。

    反过来的边界也要守住：单行输入框里的 Enter 是提交、Tab 是跳焦点，
    文本框以外的退格（列表页删选中项之类）仍然要有一步。
    """

    INPUT = 'input[name=kw]'

    def _type(self, session, text, selector=None):
        selector = selector or self.INPUT
        _in_recording_thread(session, lambda: session._test_page.locator(selector).click())
        _in_recording_thread(
            session, lambda: session._test_page.locator(selector).press_sequentially(text, delay=10))

    def _press(self, session, key, selector=None):
        selector = selector or self.INPUT
        _in_recording_thread(session, lambda: session._test_page.locator(selector).press(key))

    def _fill_steps(self, session):
        return [s for s in session.ir() if s['type'] == 'fill']

    def _wait_value(self, session, expected):
        def value():
            fills = self._fill_steps(session)
            return fills[-1]['value'] if fills else None

        self.assertTrue(_wait_for(lambda: value() == expected), value())

    def test_backspace_correction_leaves_one_fill(self):
        session = self._start('/search')
        self._type(session, 'admin123')
        for _ in range(3):
            self._press(session, 'Backspace')
        self._type(session, '456')
        self._wait_value(session, 'admin456')
        time.sleep(0.4)  # 给可能迟到的多余事件留个窗口
        self.assertEqual(self._types(session), ['goto', 'fill'], self._types(session))
        self.assertEqual(len(self._fill_steps(session)), 1)

    def test_arrow_and_delete_do_not_add_press_steps(self):
        session = self._start('/search')
        self._type(session, 'pass')
        self._press(session, 'ArrowLeft')
        self._press(session, 'Delete')
        self._type(session, 'X')
        self._wait_value(session, 'pasX')
        time.sleep(0.4)
        self.assertEqual(self._types(session), ['goto', 'fill'], self._types(session))

    def test_space_inside_an_input_is_part_of_the_value(self):
        session = self._start('/search')
        self._type(session, 'a')
        self._press(session, 'Space')
        self._type(session, 'b')
        self._wait_value(session, 'a b')
        time.sleep(0.4)
        self.assertEqual(self._types(session), ['goto', 'fill'], self._types(session))

    def test_clearing_a_field_is_recorded_as_an_empty_fill(self):
        """删空也是有效结果：回放时要真的把输入框清空，而不是当成没录到。"""
        session = self._start('/search')
        self._type(session, 'abc')
        self._press(session, 'Control+a')
        self._press(session, 'Backspace')
        self._wait_value(session, '')
        time.sleep(0.4)
        self.assertEqual(self._types(session), ['goto', 'fill'], self._types(session))
        self.assertEqual(self._fill_steps(session)[0]['value'], '')

    def test_enter_in_a_single_line_input_is_still_a_press(self):
        """回车提交表单必须保留：它不是文本内容，fill 表达不了。"""
        session = self._start('/search')
        self._type(session, 'abc')
        self._press(session, 'Enter')
        self.assertTrue(_wait_for(lambda: 'press' in self._types(session)), self._types(session))
        self.assertEqual([s['value'] for s in session.ir() if s['type'] == 'press'], ['Enter'])

    def test_newline_in_a_textarea_is_folded_into_the_value(self):
        """textarea 里的回车是换行，值里已经有了，press 该被那条 fill 吞掉。"""
        session = self._start('/note')
        textarea = 'textarea'
        self._type(session, 'line1', selector=textarea)
        self._press(session, 'Enter', selector=textarea)
        self._type(session, 'line2', selector=textarea)
        self._wait_value(session, 'line1\nline2')
        time.sleep(0.4)
        self.assertEqual(self._types(session), ['goto', 'fill'], self._types(session))

    def test_backspace_outside_a_text_field_is_still_recorded(self):
        """列表页/画布上的退格没有 fill 来兜底，必须保留成一步。"""
        session = self._start('/note')
        _in_recording_thread(session, lambda: session._test_page.keyboard.press('Backspace'))
        self.assertTrue(_wait_for(lambda: 'press' in self._types(session)), self._types(session))
        press = [s for s in session.ir() if s['type'] == 'press'][0]
        self.assertEqual(press['value'], 'Backspace')
        self.assertEqual(press['target']['snap']['tag'], 'body')


class FocusClickTest(_Base):
    """点文本框到底要不要记一步 click。

    可写入的框：点击只是聚焦，值由 fill 表达，多记一步没意义；
    只读的框（日期选择器、下拉筛选的载体）：点它就是展开面板，不记回放就打不开。
    """

    def test_writable_input_click_is_not_a_step(self):
        session = self._start('/search')
        _in_recording_thread(session, lambda: session._test_page.click('input[name=kw]'))
        time.sleep(0.6)
        self.assertEqual(self._types(session), ['goto'], self._types(session))

    def test_readonly_input_click_is_kept(self):
        session = self._start('/readonly')
        _in_recording_thread(session, lambda: session._test_page.click('#ro'))
        self.assertTrue(_wait_for(lambda: 'click' in self._types(session)), self._types(session))
        click = [s for s in session.ir() if s['type'] == 'click'][0]
        self.assertEqual(click['target']['alias'], '请选择日期')

    def test_readonly_input_keeps_key_steps(self):
        """只读框里的方向键可能是组件切换日期，值不会经过 fill，必须留下。"""
        session = self._start('/readonly')
        _in_recording_thread(session, lambda: session._test_page.click('#ro'))
        _in_recording_thread(session, lambda: session._test_page.locator('#ro').press('ArrowDown'))
        self.assertTrue(_wait_for(lambda: 'press' in self._types(session)), self._types(session))
        self.assertEqual([s['value'] for s in session.ir() if s['type'] == 'press'], ['ArrowDown'])


class DomShiftMergeTest(_Base):
    """组件在输入过程中改 DOM，一次输入仍然只该留一条「输入」。

    真实页面几乎都会这么干：第一次输入后包一层 el-input、插入一个隐藏 input
    （自动填充/验证码）、冒出校验错误态。结构一变 CSS 路径就变，拿它当元素身份
    必然把一次输入切成好几条。身份改用「唯一主定位的选择器」后，DOM 怎么重排都还是同一元素。
    """

    def _type_into(self, session, label, chunks, pause=0.4):
        page = session._test_page
        _in_recording_thread(session, lambda: page.get_by_label(label).click())
        for text in chunks:
            _in_recording_thread(
                session,
                lambda t=text, lb=label: session._test_page.get_by_label(lb).press_sequentially(t, delay=10))
            time.sleep(pause)

    def _fills(self, session):
        return [s for s in session.ir() if s['type'] == 'fill']

    def test_dom_wrapped_mid_typing_keeps_one_fill(self):
        session = self._start('/shift-wrap')
        self._type_into(session, '密码', ['1', '2345678', '9'])
        self.assertTrue(_wait_for(lambda: self._fills(session)
                                  and self._fills(session)[-1]['value'] == '123456789'),
                        self._fills(session))
        time.sleep(0.4)
        self.assertEqual(self._types(session), ['goto', 'fill'], self._types(session))
        self.assertEqual(self._fills(session)[0]['value'], '123456789')

    def test_hidden_input_inserted_mid_typing_keeps_one_fill(self):
        session = self._start('/shift-hidden')
        self._type_into(session, '密码', ['1', '2345'])
        self.assertTrue(_wait_for(lambda: self._fills(session)
                                  and self._fills(session)[-1]['value'] == '12345'),
                        self._fills(session))
        time.sleep(0.4)
        self.assertEqual(self._types(session), ['goto', 'fill'], self._types(session))

    def test_two_different_fields_are_not_merged(self):
        """合并只能发生在同一个元素上：两个不同的输入框各自一条。"""
        session = self._start('/shift-wrap')
        self._type_into(session, '账号', ['ad'])
        self._type_into(session, '密码', ['12'])
        self.assertTrue(_wait_for(lambda: len(self._fills(session)) == 2), self._fills(session))
        time.sleep(0.4)
        self.assertEqual([(s['target']['alias'], s['value']) for s in self._fills(session)],
                         [('账号', 'ad'), ('密码', '12')])


class PressRecordingTest(_Base):
    def _fill_and_enter(self, session):
        page = session._test_page
        _in_recording_thread(session, lambda: page.get_by_placeholder('搜索关键词').fill('abc'))
        _in_recording_thread(session, lambda: page.get_by_placeholder('搜索关键词').press('Enter'))
        self.assertTrue(_wait_for(lambda: 'press' in self._types(session)), self._types(session))
        return [s for s in session.ir() if s['type'] == 'press'][0]

    def test_enter_in_an_input_becomes_a_press_step(self):
        session = self._start('/search')
        press = self._fill_and_enter(session)
        self.assertEqual(press['value'], 'Enter')
        # 键要落在输入框上，不是 body：否则生成的 press 打在一个不可见的元素上。
        self.assertEqual(press['target']['snap']['tag'], 'input')
        # 回车把页面带走了，计数已经在新 document 上：定位按快照推、标「未核实」，
        # 但必须留下——否则所有会跳转的步骤都会因为「找不到元素」而被整体拒绝生成。
        self.assertTrue(press['target']['primary'], press['target'])
        self.assertTrue(press['target']['unverified'], press['target'])
        self.assertEqual(press['target']['status'], 'stale')

    def test_enter_submit_does_not_add_the_synthesized_click(self):
        """回车提交表单时浏览器会给提交按钮补一次 click，记下来回放就会提交两遍。"""
        session = self._start('/search')
        self._fill_and_enter(session)
        self.assertNotIn('click', self._types(session), self._types(session))

    def test_navigation_caused_by_enter_is_not_an_extra_goto(self):
        """按 Enter 提交后页面会跳转，但那是 press 的结果，不该再记一条「打开页面」。"""
        session = self._start('/search')
        self._fill_and_enter(session)
        self.assertTrue(_wait_for(lambda: '/result' in (session._test_page.url or '')))
        time.sleep(0.6)  # 给导航判定留一个窗口，确认它不会事后补一条
        self.assertEqual(self._types(session), ['goto', 'fill', 'press'], self._types(session))

    def test_enter_on_a_button_is_a_click_not_a_press(self):
        """焦点本来就在按钮上：这就是一次点击，记 press 会让回放点两次。"""
        session = self._start('/search')
        _in_recording_thread(
            session, lambda: session._test_page.get_by_role('button', name='搜索').press('Enter'))
        self.assertTrue(_wait_for(lambda: 'click' in self._types(session)), self._types(session))
        self.assertNotIn('press', self._types(session), self._types(session))


class DialogRecordingTest(_Base):
    def test_confirm_becomes_a_dialog_step(self):
        session = self._start('/confirm')
        _in_recording_thread(session, lambda: session._test_page.click('#del'))
        self.assertTrue(_wait_for(lambda: 'dialog' in self._types(session)), self._types(session))
        dialog = [s for s in session.ir() if s['type'] == 'dialog'][0]
        self.assertEqual(dialog['value'], 'accept')
        self.assertEqual(dialog['dialog']['kind'], 'confirm')
        self.assertIn('确定删除', dialog['dialog']['message'])
        self.assertIsNone(dialog['target'])

    def test_dialog_step_sits_right_after_the_click(self):
        """顺序决定回放能不能提前注册处理方式，不能插到别处去。"""
        session = self._start('/confirm')
        _in_recording_thread(session, lambda: session._test_page.click('#del'))
        self.assertTrue(_wait_for(lambda: len(self._types(session)) == 3), self._types(session))
        self.assertEqual(self._types(session), ['goto', 'click', 'dialog'])

    def test_page_is_not_stuck_after_the_dialog(self):
        """录制时弹窗必须被自动处理掉，否则用户看到的就是「点了删除就卡死」。"""
        session = self._start('/confirm')
        _in_recording_thread(session, lambda: session._test_page.click('#del'))

        def out_text():
            # Playwright 对象不能跨线程读，取值也要回到录制线程里。
            return _in_recording_thread(session, lambda: session._test_page.inner_text('#out'))

        self.assertTrue(_wait_for(lambda: out_text() == '已删除'), out_text())


class ReplayTest(_Base):
    def _record_steps(self, path, action, edit=None, until=None):
        """录完就把浏览器收掉：回放要自己开一个 Playwright 实例，两个实例同时挂着
        容易在同一个线程里抢事件循环。

        until 是必须等的条件：事件是入了队列之后由录制线程再处理的，
        操作返回的那一刻步骤还没落地，直接取 ir() 会拿到不完整的录制。
        """
        session = self._start(path)
        _in_recording_thread(session, lambda: action(session._test_page))
        if until is not None:
            self.assertTrue(_wait_for(lambda: until(self._types(session))), self._types(session))
        if edit:
            edit(session)
        try:
            return session.ir()
        finally:
            RecordingSession.dispose_current()

    def test_press_replays_and_submits_the_form(self):
        def search(page):
            page.get_by_placeholder('搜索关键词').fill('abc')
            page.get_by_placeholder('搜索关键词').press('Enter')

        steps = self._record_steps('/search', search, until=lambda t: 'press' in t)
        report = replay_checked(steps + [_text_assert(99, 'abc')], framework=PW, headless=True)
        self.assertTrue(report['ok'], report.get('error'))

    def test_corrected_input_replays_with_the_final_value(self):
        """改过的值要真的提交出去：回放后结果页上应该是 admin456。"""
        def search(page):
            field = page.get_by_placeholder('搜索关键词')
            field.click()
            field.press_sequentially('admin123', delay=5)
            for _ in range(3):
                field.press('Backspace')
            field.press_sequentially('456', delay=5)
            page.get_by_role('button', name='搜索').click()

        steps = self._record_steps('/search', search, until=lambda t: 'click' in t)
        report = replay_checked(steps + [_text_assert(99, 'admin456')], framework=PW, headless=True)
        self.assertTrue(report['ok'], report.get('error'))

    def test_dialog_is_accepted_during_replay(self):
        steps = self._record_steps('/confirm', lambda page: page.click('#del'),
                                   until=lambda t: 'dialog' in t)
        report = replay_checked(steps + [_text_assert(99, '已删除')], framework=PW, headless=True)
        self.assertTrue(report['ok'], report.get('error'))

    def test_dialog_can_be_switched_to_dismiss(self):
        """录制时一律 accept（Playwright 不把弹窗交给浏览器 UI，问不到用户点了哪个），
        需要取消时在步骤上改。"""
        def dismiss(session):
            dialog_no = [s['no'] for s in session.ir() if s['type'] == 'dialog'][0]
            session.update_step(dialog_no, {'value': 'dismiss'})

        steps = self._record_steps('/confirm', lambda page: page.click('#del'),
                                   edit=dismiss, until=lambda t: 'dialog' in t)
        report = replay_checked(steps + [_text_assert(99, '已取消')], framework=PW, headless=True)
        self.assertTrue(report['ok'], report.get('error'))

    def test_missing_dialog_fails_instead_of_passing_silently(self):
        """录制里有弹窗、回放时没弹（多半上一步没点中），要明确失败而不是装作走通。"""
        steps = self._record_steps('/confirm', lambda page: page.click('#noop'),
                                   until=lambda t: 'click' in t)
        steps.append(ir.make_step('dialog', no=99, value='accept',
                                  dialog={'kind': 'confirm', 'message': '确定删除该设备？',
                                          'action': 'accept'}))
        report = replay_checked(steps, framework=PW, headless=True)
        self.assertFalse(report['ok'])
        self.assertIn('没有弹出来', report['error'])


class GeneratorFallbackTest(unittest.TestCase):
    """会跳转的步骤（回车提交、点链接）在计数时元素已经不在页面上了。

    这种步骤必须放行生成，否则「录下来的第一件事就是提交表单」这种最常见的流程
    会被整体拒绝。但放行只针对录制当场就没数到的（unverified），
    真正失效的步骤仍然要拦下来让用户重新拾取。
    """

    @staticmethod
    def _candidate(kind, desc, count):
        return {'kind': kind, 'desc': desc, 'count': count,
                'spec': {'kind': kind, 'method': 'locator', 'args': [desc],
                         'kwargs': {}, 'desc': desc}}

    def test_unverified_step_still_generates(self):
        from app.services.recording import emit
        target = {
            'unverified': True,
            'candidates': [self._candidate('placeholder', "getByPlaceholder('x')", 0)],
        }
        info = emit._locator_info(target, 'playwright-ts')
        self.assertIsNotNone(info)
        self.assertEqual(info['mode'], 'stale')
        self.assertEqual(info['locator'], "getByPlaceholder('x')")

    def test_truly_missing_element_is_still_blocked(self):
        from app.services.recording import emit
        target = {
            'candidates': [self._candidate('placeholder', "getByPlaceholder('x')", 0)],
        }
        self.assertIsNone(emit._locator_info(target, 'playwright-ts'))


if __name__ == '__main__':
    unittest.main()
