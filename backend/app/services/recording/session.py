"""录制会话。

Playwright 同步 API 必须在同一个线程里使用，所以每个会话占用一个后台线程：
- 线程内持有浏览器，页面操作通过 expose_binding 回调到 _on_event；
- Flask 请求线程只通过命令队列与会话交互，不直接碰 Playwright 对象；
- 循环里调用 wait_for_timeout 让事件得以分发（同步 API 只在调用期间分发事件）。
"""
import json
import os
import queue
import threading
import time
import uuid
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

from app.services.recording import ir, verify
from app.services.recording.locators import build_specs, rank, stale_check, status_of

HERE = os.path.dirname(os.path.abspath(__file__))
RECORDER_JS = os.path.join(HERE, 'recorder.js')
# 录制时给目标元素打的临时属性，用来算出「用户点的是第几个匹配」（见 _count）。
TARGET_MARK_ATTR = 'data-autocase-target'
FIND_INDEX_JS = (
    "(els, mark) => els.findIndex((el) => el.getAttribute('%s') === mark)" % TARGET_MARK_ATTR
)
CLEAR_MARK_JS = (
    "() => document.querySelectorAll('[%s]').forEach((el) => el.removeAttribute('%s'))"
    % (TARGET_MARK_ATTR, TARGET_MARK_ATTR)
)
IDLE_SECONDS = 15 * 60
# 停止后浏览器继续开着（用户还要拾取元素、验证断言），但不能永远占着：
# 一小时没有任何命令就自己收掉。
STOPPED_IDLE_SECONDS = 60 * 60
PUMP_MS = 150
COMMAND_TIMEOUT = 60
# 关闭浏览器这类命令不能把请求线程拖住：关不掉就让线程自己退出。
CLOSE_TIMEOUT = 8
PICK_OFF_TAGS = ('select', 'label')
NON_TEXT_INPUT_TYPES = ('button', 'submit', 'reset', 'checkbox', 'radio')
# 被录制页面里的浮层：只显示最近几步，避免面板过高挡住应用。
HUD_RECENT_STEPS = 3
STEP_TITLES = {'goto': '打开', 'click': '点击', 'fill': '输入', 'select': '选择',
               'new_page': '标签页', 'assert': '断言', 'wait': '等待', 'sleep': '等待'}
STATUS_LABELS = {'unique': '唯一定位', 'fragile': '结构定位', 'ambiguous': '定位不唯一', 'stale': '定位已变化'}
STATUS_TONES = {'unique': 'ok', 'fragile': 'warn', 'ambiguous': 'bad'}
# 就地编辑允许改的字段：只改「值」，不动定位（换元素走拾取）。
EDITABLE_FIELDS = {
    'fill': ('value',),
    'select': ('value', 'label'),
    'goto': ('url',),
    'sleep': ('seconds',),
}
MAX_VALUE_LENGTH = 500


class RecordingError(Exception):
    pass


class RecordingBusy(RecordingError):
    """已有一个正在进行的录制。"""


class RecordingSession(object):
    _current = None
    _current_lock = threading.Lock()

    def __init__(self, base_url, headless=False, driver=None, panel_url=None,
                 panel_ratio=0.45, hud_panel=False, embed_url=None):
        self.id = uuid.uuid4().hex
        self.base_url = base_url
        self.status = 'starting'
        self.error = None
        self._headless = headless
        # 仅供测试：在录制线程内、进入循环前调用 driver(session, page) 模拟用户操作。
        self._driver = driver
        # 嵌入模式：主页面是 AutoCase 外壳页，被测系统在它的 iframe 里。
        # 此时不再开第二个窗口，左侧宽度 = 窗口宽度 - 面板宽度。
        self._embed_url = embed_url
        self._embed = bool(embed_url)
        self._app_origin = '{0.scheme}://{0.netloc}'.format(urlparse(base_url))
        # 并排模式：额外开一个窗口加载前端录制页，与被测页面左右摆开。
        self._panel_url = panel_url
        self._panel_ratio = panel_ratio if 0.2 <= panel_ratio <= 0.8 else 0.45
        self._panel_context = None
        self._panel_page = None
        # 页面内悬浮面板默认关闭，只保留操作点的即时徽标。
        self._hud_panel = hud_panel
        self._lock = threading.RLock()
        self._ready = threading.Event()
        self._commands = queue.Queue()
        # Playwright 回调只入队，真正的同步调用在录制线程的循环里执行（在回调内调用会死锁）。
        self._inbox = queue.Queue()
        self._errors = []
        self._events = []
        self._steps = []
        self._picks = []
        # 元素身份 → 别名。同一个元素复用同一个别名，不同元素撞名时加后缀（见 _alias_for）。
        self._alias_keys = {}
        self._wired = set()
        self._last_url = {}
        self._pick = False
        # 暂停录制：浏览器照常可用，但操作不再记为步骤（同 Playwright codegen 的 Pause）。
        self._paused = False
        self._opened = False
        self._last_activity = time.time()
        self._browser = None
        self._context = None
        self._thread = threading.Thread(target=self._run, name='recording-%s' % self.id[:8], daemon=True)

    # ---- 会话管理（Flask 线程调用）----

    @classmethod
    def start_new(cls, base_url, headless=False, driver=None, panel_url=None,
                  panel_ratio=0.45, hud_panel=False, embed_url=None):
        with cls._current_lock:
            current = cls._current
            if current is not None and current.status in ('starting', 'recording'):
                raise RecordingBusy('已有正在进行的录制，请先停止或取消')
        # 上一轮停止后浏览器还开着，开新的之前必须收掉，否则会留下一个没人管的窗口。
        if current is not None:
            current.dispose()
        session = cls(base_url, headless=headless, driver=driver, panel_url=panel_url,
                      panel_ratio=panel_ratio, hud_panel=hud_panel, embed_url=embed_url)
        with cls._current_lock:
            cls._current = session
        session._thread.start()
        session._ready.wait(COMMAND_TIMEOUT)
        if session.error:
            raise RecordingError(session.error)
        if session.status != 'recording':
            raise RecordingError('录制未能启动')
        return session

    @classmethod
    def current(cls):
        return cls._current

    @classmethod
    def dispose_current(cls, timeout=CLOSE_TIMEOUT):
        """把当前会话的浏览器收掉（应用退出、测试 teardown 用）。"""
        current = cls._current
        if current is not None:
            current.dispose(timeout=timeout)

    def set_pick(self, on):
        return self._call('set_pick', on=bool(on))

    def set_pause(self, on):
        """暂停 / 继续录制（浏览器保持可用，暂停期间的操作不入库）。"""
        return self._call('set_pause', on=bool(on))

    def stop(self):
        """停止记录，但把浏览器留着：停止后仍可拾取元素、验证断言。"""
        return self._call('stop')

    def close_browser(self):
        """关掉浏览器与面板窗口（录制结果保留）。"""
        return self._call('close', timeout=CLOSE_TIMEOUT)

    def verify_intent(self, intent):
        """在当前页面上判断一条断言此刻是否成立。"""
        return self._call('verify', intent=intent)

    def cancel(self):
        """内部中止：关浏览器但不重校验定位。界面上没有入口（只有「停止录制」），
        主要给测试 teardown 这类「不关心最终定位状态」的场景用。"""
        return self._call('cancel')

    def dispose(self, timeout=CLOSE_TIMEOUT):
        """尽力关掉浏览器，失败也不抛：调用方只需要它「别留下窗口」。"""
        try:
            self._call('close', timeout=timeout)
        except Exception:
            pass

    def relayout(self):
        """把被测页面和面板窗口重新摆成左右并排。"""
        return self._call('relayout')

    def _call(self, name, timeout=COMMAND_TIMEOUT, **kwargs):
        # 用线程是否存活判断，而不是状态：停止后浏览器还开着，命令照样要能发进去。
        if not self._thread.is_alive():
            raise RecordingError('录制已结束')
        reply = queue.Queue(maxsize=1)
        self._commands.put((name, kwargs, reply))
        try:
            ok, value = reply.get(timeout=timeout)
        except queue.Empty:
            raise RecordingError('录制线程没有响应，请重试')
        if not ok:
            raise RecordingError(value)
        return value

    # ---- 读取（任意线程，只读内存）----

    def snapshot(self):
        with self._lock:
            steps = [ir.public_step(s) for s in self._steps]
            picks = [{'seq': p['seq'], 'target': ir.public_step({'no': 0, 'type': 'pick', 'target': p['target']})['target']}
                     for p in self._picks]
            return {
                'id': self.id,
                'status': self.status,
                'base_url': self.base_url,
                'pick': self._pick,
                # 暂停中：浏览器可用但不记录操作。
                'paused': self._paused,
                'error': self.error,
                'steps': steps,
                'picks': picks,
                'aliases': self.aliases(),
                # 下拉里要区分「删除」和「删除#2」，只给字符串分不出来（见 alias_items）。
                'alias_items': self.alias_items(),
                # 面板窗口是否开着，前端据此显示「重新并排」按钮。
                'panel': self._panel_page is not None,
                # 浏览器的生命周期比录制长：停止后仍开着，前端据此决定能否继续拾取 / 验证。
                'browser_open': self._browser_open(),
            }

    def _browser_open(self):
        return (getattr(self, '_browser', None) is not None
                and self.status in ('starting', 'recording', 'stopped', 'idle_timeout'))

    def ir(self):
        """完整 IR（含可执行的定位 spec），供回放与生成使用；不返回给前端。"""
        with self._lock:
            return [ir.replay_view(s) for s in self._steps]

    def events_after(self, after=-1):
        with self._lock:
            return [dict(e) for e in self._events if e['seq'] > after]

    def aliases(self):
        seen = []
        with self._lock:
            targets = [s['target'] for s in self._steps if s.get('target')]
            targets += [p['target'] for p in self._picks]
        for t in targets:
            alias = t.get('alias')
            if alias and alias not in seen:
                seen.append(alias)
        return seen

    def alias_items(self):
        """给界面用的别名列表：带上是第几步 / 拾取来的，用户才分得清「删除」和「删除#2」。"""
        items = []
        seen = set()
        with self._lock:
            rows = [(s['target'], s['no'], s['type']) for s in self._steps if s.get('target')]
            rows += [(p['target'], None, 'pick') for p in self._picks]
        for target, no, kind in rows:
            alias = target.get('alias')
            if not alias or alias in seen:
                continue
            seen.add(alias)
            items.append({
                'alias': alias,
                'hint': ('第 %d 步 · %s' % (no, STEP_TITLES.get(kind, kind))) if no else '拾取',
                'desc': (target.get('primary') or {}).get('desc'),
            })
        return items

    # ---- 别名分配 ----
    #
    # 别名是断言唯一能指认元素的手段（`add_intent_step` 靠它找目标），所以必须唯一。
    # 但「唯一」不能简单地对重名加后缀：同一个按钮点两次会产生两个别名，
    # 下拉里就出现两个其实指向同一元素的选项。身份按「同一个元素」判，而不是「同一个名字」。

    @staticmethod
    def _alias_key(snap, primary, frame):
        """判断两次操作是不是同一个元素。

        主定位唯一时，主定位本身就是身份——两个不同元素不可能同时命中同一个唯一选择器。
        没有唯一候选时退回结构路径：同一元素两次操作得到的路径相同，不同行里的同名按钮不同。
        """
        if primary is not None:
            return ('locator', primary['desc'])
        return ('path', snap.get('css') or '', snap.get('tag') or '', id(frame))

    def _alias_for(self, target, key):
        """返回该元素在本次录制内唯一的别名。调用方需持有 self._lock。"""
        known = self._alias_keys.get(key)
        if known:
            return known
        base = target.get('alias') or 'element'
        used = set(self._alias_keys.values())
        alias = base
        n = 2
        while alias in used:
            suffix = '#%d' % n
            alias = base[:ir.ALIAS_MAX_LENGTH - len(suffix)] + suffix
            n += 1
        self._alias_keys[key] = alias
        return alias

    def _index_of(self, no):
        idx = next((i for i, s in enumerate(self._steps) if s['no'] == no), None)
        if idx is None:
            raise RecordingError('没有编号为 %s 的步骤' % no)
        return idx

    def _tied(self, idx):
        # 点击打开新标签页后紧跟的 new_page 与前一步绑定，不能单独移动或插入到中间。
        if self._steps[idx]['type'] == 'new_page':
            return True
        return idx + 1 < len(self._steps) and self._steps[idx + 1]['type'] == 'new_page'

    def remove_step(self, no):
        with self._lock:
            idx = self._index_of(no)
            if self._steps[idx]['type'] == 'new_page':
                raise RecordingError('标签页切换随前一步一起删除')
            # 删除前一步时，它打开的标签页切换一并删除。
            drop = {idx}
            if idx + 1 < len(self._steps) and self._steps[idx + 1]['type'] == 'new_page':
                drop.add(idx + 1)
            self._steps = [s for i, s in enumerate(self._steps) if i not in drop]
            self._renumber()
        self._schedule_hud()
        return self.snapshot()

    def move_step(self, no, direction):
        with self._lock:
            idx = self._index_of(no)
            target = idx - 1 if direction == 'up' else idx + 1
            if target < 0 or target >= len(self._steps):
                return self.snapshot()
            if self._tied(idx) or self._tied(target):
                raise RecordingError('这一步与标签页切换绑定，不能移动')
            self._steps[idx], self._steps[target] = self._steps[target], self._steps[idx]
            self._renumber()
        self._schedule_hud()
        return self.snapshot()

    def move_step_to(self, no, position):
        """拖拽排序：把步骤（含绑定的标签页切换）移动到 position（0 基，含自身的列表）。

        position 用「移除自身之后的列表下标」，与前端拖拽库的 newIndex 语义一致。
        """
        try:
            to = int(position)
        except (TypeError, ValueError):
            raise RecordingError('位置必须是整数')
        with self._lock:
            idx = self._index_of(no)
            if self._steps[idx]['type'] == 'new_page':
                raise RecordingError('标签页切换随前一步一起移动')
            block = [idx]
            if idx + 1 < len(self._steps) and self._steps[idx + 1]['type'] == 'new_page':
                block.append(idx + 1)
            moving = [self._steps[i] for i in block]
            rest = [step for i, step in enumerate(self._steps) if i not in block]
            pos = max(0, min(len(rest), to))
            # 不能插到「点击 → 标签页切换」之间，否则绑定就断了。
            if 0 < pos < len(rest) and rest[pos]['type'] == 'new_page':
                pos -= 1
            self._steps = rest[:pos] + moving + rest[pos:]
            self._renumber()
        self._schedule_hud()
        return self.snapshot()

    def update_step(self, no, changes):
        """就地修改一步的可编辑字段，不动定位（换元素请用拾取）。"""
        if not isinstance(changes, dict):
            raise RecordingError('请求体必须是对象')
        with self._lock:
            idx = self._index_of(no)
            step = self._steps[idx]
            kind = step['type']
            allowed = EDITABLE_FIELDS.get(kind)
            if not allowed:
                raise RecordingError('这一步没有可编辑的内容')
            unexpected = sorted(set(changes) - set(allowed))
            if unexpected:
                raise RecordingError('这一步不支持修改：%s' % '、'.join(unexpected))
            if kind == 'goto':
                url = (changes.get('url') or '').strip()
                parsed = urlparse(url)
                if parsed.scheme not in ('http', 'https') or not parsed.netloc:
                    raise RecordingError('地址必须是 http 或 https')
                step['url'] = url
            elif kind == 'sleep':
                try:
                    seconds = float(changes.get('seconds'))
                except (TypeError, ValueError):
                    raise RecordingError('等待时间必须是数字')
                if not 0.1 <= seconds <= 600:
                    raise RecordingError('等待时间需在 0.1 ~ 600 秒之间')
                step['intent'] = dict(step.get('intent') or {'type': 'sleep'}, seconds=seconds)
                step['text'] = '固定等待 %s 秒' % seconds
            else:  # fill / select
                value = changes.get('value')
                if not isinstance(value, str):
                    raise RecordingError('值必须是字符串')
                if len(value) > MAX_VALUE_LENGTH:
                    raise RecordingError('值最长 %d 个字符' % MAX_VALUE_LENGTH)
                step['value'] = value
                if 'label' in changes:
                    label = changes.get('label')
                    step['label'] = (label or '').strip() or None
        self._schedule_hud()
        return self.snapshot()

    def add_intent_step(self, intent, text=None, after_no=None):
        """插入断言 / 等待 / 睡眠。intent 必须已由 intent.validate 校验过。

        text 保存用户原文；after_no 为空则追加到末尾，否则插入到该步骤之后。
        断言的标签页跟着锚点走：以前固定写 0，多标签页录制时断言会被写到主标签页上。
        """
        step_type = ir.step_type_for_intent(intent['type'])
        with self._lock:
            target = None
            if intent.get('target'):
                target = self._find_target(intent['target'])
                if target is None:
                    raise RecordingError('没有名为 %r 的元素' % intent['target'])
            page_index = self._page_index_near(after_no)
            step = ir.make_step(step_type, no=0, target=target, value=intent.get('value'),
                                page_index=page_index, source='manual')
            step['intent'] = intent
            step['text'] = text
            if after_no is None:
                self._steps.append(step)
            else:
                idx = self._index_of(after_no)
                # 不能插进「点击 → 标签页切换」之间。
                pos = idx + 2 if self._tied(idx) and self._steps[idx + 1]['type'] == 'new_page' else idx + 1
                self._steps.insert(pos, step)
            self._renumber()
        self._schedule_hud()
        return self.snapshot()

    def _page_index_near(self, after_no=None):
        """断言落在哪个标签页：插在某步之后就用那一步的页，追加就用最后一步的页。"""
        if after_no is not None:
            try:
                return self._steps[self._index_of(after_no)].get('page_index') or 0
            except RecordingError:
                return 0
        for step in reversed(self._steps):
            if step.get('page_index'):
                return step['page_index']
        return 0

    def _find_target(self, alias):
        return self._find_target_with_frame(alias)[0]

    def _find_target_with_frame(self, alias):
        """按别名找目标，并把「它在哪个 frame 里」一起带出来（验证要在原来的 frame 上做）。"""
        if not alias:
            return None, None
        for s in reversed(self._steps):
            if s.get('target') and s['target'].get('alias') == alias:
                return s['target'], s.get('_frame')
        for p in self._picks:
            if p['target'].get('alias') == alias:
                return p['target'], p.get('_frame')
        return None, None

    def _renumber(self):
        for i, s in enumerate(self._steps, 1):
            s['no'] = i

    # ---- 线程主循环（只在录制线程内执行 Playwright 调用）----

    def _run(self):
        try:
            with sync_playwright() as p:
                self._browser = p.chromium.launch(headless=self._headless)
                # no_viewport：页面跟随真实窗口大小。Playwright 默认会给 1280×720 的
                # viewport（用 CDP 覆盖渲染尺寸），窗口最大化后页面仍按 1280 渲染，
                # 右边就会空一大块，录制到的也不是用户看到的尺寸。
                self._context = self._browser.new_context(no_viewport=True)
                # 悬浮面板开关要在 recorder.js 之前写进页面。
                self._context.add_init_script(
                    script='window.__AUTOCASE_HUD_PANEL = %s;' % ('true' if self._hud_panel else 'false')
                )
                # 嵌入模式下外壳页（AutoCase 自己）不参与录制，按 origin 告诉注入脚本跳过它。
                if self._embed:
                    shell_origin = '{0.scheme}://{0.netloc}'.format(urlparse(self._embed_url))
                    self._context.add_init_script(
                        script='window.__AUTOCASE_SKIP_ORIGIN = %s;' % json.dumps(shell_origin)
                    )
                self._context.add_init_script(path=RECORDER_JS)
                self._context.on('page', self._on_new_page)
                page = self._context.new_page()
                self._wire(page)
                self._opened = True
                if self._embed:
                    # 先最大化再加载：被测页面按最终宽度完成首次布局，
                    # 否则它会按浏览器默认窗口（约 1280）渲染，全屏后右侧空一大块。
                    self._maximize(page)
                page.goto(self._embed_url or self.base_url, wait_until='domcontentloaded')
                if self._embed:
                    # 外壳页很快，真正要等的是 iframe 里的被测系统。
                    self._wait_app_frame(page)
                self._open_panel()
                self.status = 'recording'
                if self._driver is not None:
                    self._driver(self, page)
                self._process_inbox()
                self._ready.set()
                self._loop()
        except Exception as exc:
            self.error = str(exc)
            self.status = 'failed'
        finally:
            self._ready.set()

    def _process_inbox(self):
        while True:
            try:
                task = self._inbox.get_nowait()
            except queue.Empty:
                return
            try:
                task()
            except Exception as exc:
                self._errors.append(str(exc))

    def _loop(self):
        while True:
            self._process_inbox()
            if self._drain_commands():
                return
            if self.status == 'recording' and not self._paused and time.time() - self._last_activity > IDLE_SECONDS:
                # 暂停期间不判超时：用户可能正在页面上查资料或读提示。
                self._mark_stopped('idle_timeout', revalidate=False)
                self._shutdown()
                return
            if self.status in ('stopped', 'idle_timeout') and time.time() - self._last_activity > STOPPED_IDLE_SECONDS:
                # 停止后浏览器一直开着，但没人用了就收掉，别在机器上留窗口。
                self._shutdown()
                return
            try:
                self._pump()
            except Exception as exc:
                self.error = '浏览器已关闭：%s' % exc
                self._mark_stopped('closed', revalidate=False)
                self._shutdown()
                return

    def _pump(self):
        pages = self._context.pages
        if not pages:
            raise RecordingError('没有打开的页面')
        pages[0].wait_for_timeout(PUMP_MS)

    def _drain_commands(self):
        while True:
            try:
                name, kwargs, reply = self._commands.get_nowait()
            except queue.Empty:
                return False
            self._touch()
            try:
                if name == 'stop':
                    # 停止是幂等的：浏览器还开着，再点一次不该报错。
                    if self.status == 'recording':
                        result = self._mark_stopped('stopped', revalidate=True)
                    else:
                        result = self.snapshot()
                elif name == 'close':
                    # 先把结果回给请求线程，再关窗口——面板可能就活在这个浏览器里。
                    # 状态要按「关掉之后」写，不能等 _shutdown 之后再取（那时已经回不了话）。
                    result = dict(self.snapshot(), browser_open=False)
                    reply.put((True, result))
                    self._shutdown()
                    return True
                elif name == 'cancel':
                    result = self._mark_stopped('cancelled', revalidate=False)
                elif name == 'set_pick':
                    self._set_pick(kwargs['on'])
                    result = {'pick': self._pick}
                elif name == 'set_pause':
                    self._set_paused(kwargs['on'])
                    result = {'paused': self._paused}
                elif name == 'relayout':
                    result = {'laid_out': self.layout_windows()}
                elif name == 'verify':
                    result = self._verify_now(kwargs['intent'])
                else:
                    raise RecordingError('未知命令：%s' % name)
                reply.put((True, result))
            except Exception as exc:
                reply.put((False, str(exc)))
            if name == 'cancel':
                self._shutdown()
                return True

    def _mark_stopped(self, status, revalidate):
        """结束记录：不再入库新步骤，但浏览器和面板都留着。"""
        self._process_inbox()
        with self._lock:
            self.status = 'stopping'
        if revalidate:
            self._revalidate_all()
        with self._lock:
            self.status = status
        self._push_hud()
        return self.snapshot()

    def _shutdown(self):
        """关面板窗口与浏览器；状态保持不变（录到的结果仍然有效）。"""
        try:
            for page in list(self._context.pages) if self._context is not None else []:
                try:
                    page.evaluate('window.__pwSetPick(false)')
                except Exception:
                    pass
        except Exception:
            pass
        try:
            # 面板窗口是独立 context，先关它再关浏览器。
            if self._panel_context is not None:
                self._panel_context.close()
        except Exception:
            pass
        self._panel_page = None
        self._panel_context = None
        try:
            if self._browser is not None:
                self._browser.close()
        except Exception:
            pass
        self._browser = None

    def _touch(self):
        self._last_activity = time.time()

    def _set_pick(self, on):
        self._pick = on
        for page in list(self._context.pages):
            self._apply_pick(page)
        self._push_hud()

    def _apply_pick(self, page):
        # 嵌入模式下被测页面在 iframe 里，拾取开关要发给每个 frame。
        for frame in page.frames:
            try:
                frame.evaluate('window.__pwSetPick(%s)' % ('true' if self._pick else 'false'))
            except Exception:
                pass

    # ---- 暂停录制 ----

    def _set_paused(self, on):
        self._paused = bool(on)
        self._touch()
        for page in list(self._context.pages):
            self._apply_pause(page)
        self._push_hud()
        message = '已暂停录制' if self._paused else '继续录制'
        for page in list(self._context.pages):
            for frame in page.frames:
                if self._is_app_frame(frame):
                    self._push_badge_frame(frame, 'warn' if self._paused else 'ok', message)
                    break

    def _apply_pause(self, page):
        value = 'true' if self._paused else 'false'
        for frame in page.frames:
            try:
                frame.evaluate('window.__pwSetPaused(%s)' % value)
            except Exception:
                pass

    # ---- 编排面板窗口（独立 context，与被测页面左右并排）----

    def _open_panel(self):
        """额外开一个窗口加载前端录制页。打不开就退回单窗口录制，不影响录制本身。"""
        if not self._panel_url or self._browser is None:
            return
        try:
            # no_viewport：让页面跟随窗口大小，而不是被固定 viewport 框住。
            self._panel_context = self._browser.new_context(no_viewport=True)
            page = self._panel_context.new_page()
            page.goto(self._panel_url, wait_until='domcontentloaded', timeout=15000)
            self._panel_page = page
        except Exception:
            self._panel_page = None
            try:
                if self._panel_context is not None:
                    self._panel_context.close()
            except Exception:
                pass
            self._panel_context = None
            return
        self.layout_windows()

    def layout_windows(self):
        """把被测页面与面板窗口左右并排；用户挪过窗口后可再次调用。"""
        if self._context is None or self._embed:
            # 嵌入模式只有一个窗口，不需要并排。
            return False
        try:
            main = self._context.pages[0]
            size = main.evaluate('() => ({ w: screen.availWidth, h: screen.availHeight })')
            total_w = int(size.get('w') or 0)
            total_h = int(size.get('h') or 0)
            if total_w <= 0 or total_h <= 0:
                return False
            gap = 8
            panel_w = max(360, int(total_w * self._panel_ratio))
            main_w = max(480, total_w - panel_w - gap)
            panel_w = total_w - main_w - gap
            self._set_window_bounds(main, {'left': 0, 'top': 0, 'width': main_w, 'height': total_h})
            if self._panel_page is not None:
                self._set_window_bounds(
                    self._panel_page,
                    {'left': main_w + gap, 'top': 0, 'width': max(320, panel_w), 'height': total_h}
                )
            return True
        except Exception:
            return False

    @staticmethod
    def _set_window_bounds(page, bounds):
        try:
            cdp = page.context.new_cdp_session(page)
            info = cdp.send('Browser.getWindowForTarget')
            cdp.send('Browser.setWindowBounds', {
                'windowId': info['windowId'],
                'bounds': dict(bounds, windowState='normal')
            })
            cdp.detach()
        except Exception:
            pass

    @staticmethod
    def _maximize(page):
        """嵌入模式是单窗口，直接最大化，让被测页面拿到整屏宽度。"""
        try:
            cdp = page.context.new_cdp_session(page)
            info = cdp.send('Browser.getWindowForTarget')
            cdp.send('Browser.setWindowBounds', {
                'windowId': info['windowId'],
                'bounds': {'windowState': 'maximized'}
            })
            cdp.detach()
        except Exception:
            pass

    # ---- 被录制页面里的浮层（面板 + 即时徽标）----

    def _schedule_hud(self):
        """把「推浮层」排进录制线程；Flask 线程（增删步骤）也可以安全调用。"""
        if self._context is None:
            return
        try:
            self._inbox.put(self._push_hud)
        except Exception:
            pass

    def _push_hud(self):
        """只在录制线程里执行：把最近几步与整体状态刷到被录制页面。"""
        if self._context is None or not self._hud_panel:
            return
        payload = json.dumps(self._hud_payload(), ensure_ascii=False)
        for page in list(self._context.pages):
            try:
                page.evaluate('window.__pwHudUpdate(%s)' % payload)
            except Exception:
                # 页面正在导航或已关闭，下一次推送会补上。
                pass

    def _hud_payload(self):
        with self._lock:
            steps = list(self._steps)
            recent = [self._hud_item(step) for step in steps[-HUD_RECENT_STEPS:]]
            ambiguous = sum(1 for step in steps if (step.get('target') or {}).get('status') == 'ambiguous')
            return {
                'status': self.status,
                'pick': self._pick,
                'total': len(steps),
                'ambiguous': ambiguous,
                'steps': recent,
            }

    @staticmethod
    def _hud_item(step):
        """给浮层用的精简视图：只带编号、类型、一句标题和主定位。"""
        target = step.get('target') or {}
        primary = target.get('primary') or {}
        alias = (target.get('alias') or '').strip()
        kind = step.get('type')
        if kind in ('goto', 'new_page'):
            title = step.get('url') or ('新标签页' if kind == 'new_page' else '')
        elif kind == 'sleep':
            title = '固定等待'
        elif kind in ('assert', 'wait'):
            title = alias or (step.get('text') or '')
        else:
            title = alias or (step.get('value') or '')
        return {
            'no': step.get('no'),
            'type': kind,
            'title': str(title)[:60],
            'locator': primary.get('desc') or '',
            'status': target.get('status') or 'none',
        }

    @staticmethod
    def _push_badge_frame(frame, tone, text):
        payload = json.dumps({'tone': tone, 'text': text}, ensure_ascii=False)
        try:
            frame.evaluate('window.__pwHudBadge(%s)' % payload)
        except Exception:
            pass

    def _schedule_badge(self, frame, status, kind, alias=None, no=None):
        try:
            self._inbox.put(lambda: self._push_badge(frame, status, kind, alias, no))
        except Exception:
            pass

    def _push_badge(self, frame, status, kind, alias=None, no=None):
        """在某一步被记录后，于操作点弹一个 1.7 秒的小徽标说明定位结果。"""
        tone = STATUS_TONES.get(status, 'warn')
        label = STATUS_LABELS.get(status) or ''
        name = ('「%s」' % alias[:12]) if alias else ''
        if kind == 'pick':
            text = '已拾取%s · %s' % (name or '元素', label)
        else:
            text = '#%s %s%s · %s' % (no, STEP_TITLES.get(kind, kind), name, label)
        self._push_badge_frame(frame, tone, text)

    def _on_hud_action(self, source, payload):
        # binding 回调：只入队。
        self._inbox.put(lambda: self._handle_hud_action(payload))

    def _handle_hud_action(self, payload):
        action = payload if isinstance(payload, str) else (payload or {}).get('action')
        if action != 'undo':
            return
        with self._lock:
            if not self._steps:
                return
            idx = len(self._steps) - 1
            drop = {idx}
            # 「点击 → 标签页切换」是绑定的，撤销时一起删，保持结构合法。
            if self._steps[idx]['type'] == 'new_page' and idx > 0:
                drop.add(idx - 1)
            self._steps = [step for i, step in enumerate(self._steps) if i not in drop]
            self._renumber()
        self._touch()
        self._push_hud()
        if self._context is not None:
            for page in list(self._context.pages):
                self._push_badge_frame(page.main_frame, 'warn', '已撤销上一步')

    def _is_app_frame(self, frame):
        """哪个 frame 算「被测页面」：普通模式是顶层页面；嵌入模式按 origin 判断——
        外壳页（AutoCase 自己）不算，iframe 里的应用、以及它 target=_blank 打开的新标签页都算。"""
        try:
            if self._embed:
                return frame.url.startswith(self._app_origin)
            return frame.parent_frame is None
        except Exception:
            return False

    def _wait_app_frame(self, page, timeout=25):
        """嵌入模式启动时等 iframe 里的被测系统就绪，避免第一屏操作还没加载完。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for frame in page.frames:
                if frame is page.main_frame or not self._is_app_frame(frame):
                    continue
                try:
                    frame.wait_for_load_state('domcontentloaded', timeout=2000)
                    return True
                except Exception:
                    continue
            try:
                page.wait_for_timeout(200)
            except Exception:
                return False
        return False

    # ---- 页面与事件 ----

    def _on_new_page(self, page):
        self._inbox.put(lambda: self._handle_new_page(page))

    def _handle_new_page(self, page):
        if self._opened and id(page) not in self._wired:
            with self._lock:
                index = self._page_index(page)
                self._steps.append(ir.make_step('new_page', no=0, page_index=index))
                self._renumber()
        self._wire(page)
        # 绑定前页面可能已经跳转完成，framenavigated 不会再触发，这里补上当前 url。
        url = page.url
        if not url.startswith('about:'):
            with self._lock:
                last = self._steps[-1] if self._steps else None
                if last and last['type'] == 'new_page' and last.get('url') is None:
                    last['url'] = url
                    self._last_url[id(page.main_frame)] = url
        # 新标签页的文档是新的，浮层需要按当前步骤重画一次。
        self._schedule_hud()

    def _wire(self, page):
        if id(page) in self._wired:
            return
        self._wired.add(id(page))
        try:
            page.expose_binding('__pwEmit', self._on_event)
        except Exception:
            pass
        try:
            # 浮层上的「撤销上一步」走独立 binding，和录制事件分开。
            page.expose_binding('__pwHudAction', self._on_hud_action)
        except Exception:
            pass
        page.on('framenavigated', lambda frame, pg=page: self._on_navigated(pg, frame))
        if self._pick:
            self._apply_pick(page)
        if self._paused:
            self._apply_pause(page)
        self._push_hud()

    def _on_navigated(self, page, frame):
        self._inbox.put(lambda: self._handle_navigated(page, frame))

    def _handle_navigated(self, page, frame):
        # 嵌入模式下被测页面是外壳里的 iframe，也要按「页面跳转」记录。
        if not self._is_app_frame(frame):
            return
        url = frame.url
        if not url or url.startswith('about:'):
            return
        if self._last_url.get(id(frame)) == url:
            return
        self._last_url[id(frame)] = url
        with self._lock:
            prev = self._steps[-1] if self._steps else None
            # 点击触发的跳转不单独记为 goto；新标签页的第一次跳转只补全 url。
            if prev and prev['type'] == 'new_page' and prev.get('url') is None:
                prev['url'] = url
            elif not (prev and prev['type'] == 'click'):
                self._steps.append(ir.make_step('goto', no=0, url=url, page_index=self._page_index(page)))
                self._renumber()
        # 导航后文档是新的，浮层得按当前步骤重画一次。
        self._schedule_hud()

    def _page_index(self, page):
        try:
            return self._context.pages.index(page)
        except ValueError:
            return 0

    def _count(self, frame, specs, mark=None):
        """逐条在页面上数匹配数；mark 存在时顺带算出「用户点的是第几个匹配」。

        Playwright 的语义选择器（get_by_role 等）内部怎么排序我们拿不到，
        所以录制那一刻由 recorder.js 给目标元素打一个临时属性，再在页面里 findIndex，
        这样不唯一的步骤也能生成 .nth(i)，指向用户真正操作的那个元素。
        """
        out = []
        for spec in specs:
            index = None
            try:
                locator = getattr(frame, spec['method'])(*spec['args'], **spec['kwargs'])
                n = locator.count()
                if mark and n > 1:
                    found = locator.evaluate_all(FIND_INDEX_JS, mark)
                    index = found if isinstance(found, int) and found >= 0 else None
            except Exception:
                n = -1
            item = {'kind': spec['kind'], 'desc': spec['desc'], 'count': n, 'spec': spec}
            if index is not None:
                item['index'] = index
            out.append(item)
        if mark:
            # 计数完立刻摘掉临时属性，不把痕迹留在被测页面上。
            try:
                frame.evaluate(CLEAR_MARK_JS)
            except Exception:
                pass
        return out

    def _on_event(self, source, raw):
        # binding 回调：只入队。
        self._inbox.put(lambda: self._handle_event(source, raw))

    def _handle_event(self, source, raw):
        data = json.loads(raw)
        kind = data['type']
        # 停止后浏览器还开着：拾取仍要做（断言要用），但不能再记操作步骤。
        # 暂停期间同理，拾取是显式动作，仍然允许。
        if kind != 'pick' and (self._paused or self.status != 'recording'):
            return
        snap = data['snap']
        frame = source['frame']
        page = source['page']
        self._touch()

        if kind == 'click' and snap['tag'] == 'input' and snap.get('type') not in NON_TEXT_INPUT_TYPES:
            return
        if kind == 'click' and snap['tag'] in PICK_OFF_TAGS:
            return

        # 记录元素所在的 iframe（主框架为 None），回放时据此找到同一个框架。
        # frame_name 是给 SeleniumLibrary 的 Select Frame 用的：它认 name/id，不认 url。
        if frame.parent_frame is not None:
            try:
                frame_name = frame.name or None
            except Exception:
                frame_name = None
            snap = dict(snap, frame_url=frame.url, frame_name=frame_name)
        # _mark 只用于这一轮求 index，不留在 target 里（它只是个临时 DOM 属性名）。
        mark = snap.pop('_mark', None)
        counted = self._count(frame, build_specs(snap), mark=mark)
        primary, ordered = rank(counted)
        status = status_of(primary)
        target = ir.make_target(snap, ordered, primary, status)
        index = self._page_index(page)

        with self._lock:
            # 别名在这里定，不在 make_target 里定：唯一性要看已录入的步骤，是会话状态。
            target['alias'] = self._alias_for(target, self._alias_key(snap, primary, frame))
            seq = len(self._events)
            self._events.append({'seq': seq, 'type': kind, 'status': status,
                                 'desc': primary['desc'] if primary else None})

            if kind == 'pick':
                self._picks.append({'seq': seq, 'target': target, '_frame': frame})
                self._schedule_badge(frame, status, 'pick', target.get('alias'))
                self._schedule_hud()
                return

            key = (snap.get('css'), id(frame))
            prev = self._steps[-1] if self._steps else None
            if kind == 'fill' and prev and prev['type'] == 'fill' and prev.get('_key') == key:
                prev['value'] = data.get('value')
                prev['target'] = target
                prev['_frame'] = frame
                self._schedule_hud()
                return

            step = ir.make_step(kind, no=0, target=target, value=data.get('value'),
                                label=data.get('label'), page_index=index)
            step['_key'] = key
            step['_frame'] = frame
            self._steps.append(step)
            self._renumber()
            no = step['no']
        # 徽标要读计数结果，`_count` 已经算完；这里排进队列，避免在锁里做 Playwright 调用。
        self._schedule_badge(frame, status, kind, target.get('alias'), no)
        self._schedule_hud()

    # ---- 插入前验证（只在录制线程里跑，直接读当前页面）----

    def _verify_now(self, intent):
        """在当前页面上判断这条断言此刻是否成立。

        只回答「现在」：不等待、不重跑流程。所以 false 不代表脚本一定失败——
        断言插在点击之后时，当前页面可能还没走到那一步。
        """
        kind = intent.get('type')
        target, frame = self._find_target_with_frame(intent.get('target'))
        page = self._page_of(frame) or self._current_page()
        if page is None:
            raise RecordingError('浏览器里没有可用的页面')
        # 这两类断言没有目标元素：一个看整页文字，一个看地址。
        if kind in ('page_text_contains', 'url_contains'):
            return verify.check(self._text_frame(page), None, intent)
        if target is None:
            raise RecordingError('没有名为 %r 的元素' % intent.get('target'))
        # frame 已经随着页面导航失效时退回当前页，至少给个「找不到」而不是抛异常。
        frame = frame if self._page_of(frame) is not None else None
        return verify.check(frame or self._text_frame(page), target, intent)

    @staticmethod
    def _page_of(frame):
        if frame is None:
            return None
        try:
            return None if frame.is_detached() else frame.page
        except Exception:
            return None

    def _current_page(self):
        """用户此刻在看的页面：最近打开的那个（面板是独立 context，不在这里面）。"""
        try:
            pages = [p for p in self._context.pages if not p.is_closed()]
        except Exception:
            return None
        return pages[-1] if pages else None

    def _text_frame(self, page):
        """「整个页面的文字」落在哪个 frame：普通模式是顶层文档，嵌入模式是被测 iframe。"""
        try:
            for frame in page.frames:
                if frame is not page.main_frame and self._is_app_frame(frame):
                    return frame
            return page.main_frame
        except Exception:
            return page.main_frame

    def _revalidate_all(self):
        # 同一个目标可能被多个步骤引用（例如点击后又被断言引用），只能按目标去重；
        # 并且必须用记录它的那个框架重新计数，否则会把主定位误清空。
        with self._lock:
            owners = {}
            for step in self._steps:
                target, frame = step.get('target'), step.get('_frame')
                if target is not None and frame is not None:
                    owners.setdefault(id(target), (target, frame))
            for pick in self._picks:
                owners.setdefault(id(pick['target']), (pick['target'], pick['_frame']))
        for target, frame in owners.values():
            previous = target['primary']['desc'] if target.get('primary') else None
            # index 是录制那一刻算的，复核时元素上的临时标记早就没了，
            # 按 desc 把旧 index 带过来，别让复核把降级定位的信息洗掉。
            kept_index = {c['desc']: c.get('index') for c in (target.get('candidates') or [])}
            counted = self._count(frame, build_specs(target['snap']))
            for item in counted:
                if item.get('index') is None and kept_index.get(item['desc']) is not None:
                    item['index'] = kept_index[item['desc']]
            primary, ordered = rank(counted)
            with self._lock:
                target['candidates'] = ordered
                target['primary'] = primary
                target['status'] = stale_check(previous, primary)
