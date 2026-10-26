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

from app.services.recording import intent as intent_mod
from app.services.recording import ir, progress, verify
from app.services.recording.ask_pool import STOP_DELAY_SECONDS as ASK_STOP_DELAY_SECONDS
from app.services.recording.ask_pool import AskPool, disabled_snapshot
from app.services.recording.locators import (best_effort, build_specs, narrowest,
                                             pick_for, rank, stale_check, status_of)

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


def _read_js(name):
    with open(os.path.join(HERE, name), encoding='utf-8') as fp:
        return fp.read()


# 把匹配元素全部框出来并编号（人眼确认用）。见 overlay.js 里的两条约束。
OVERLAY_JS = _read_js('overlay.js')
CLEAR_OVERLAY_JS = (
    "() => { const el = document.getElementById('__autocase_boxes');"
    " if (el) el.remove(); return true; }"
)
# 录制时顺手把匹配元素的内容存下来：浏览器一关就再也查不到了，
# 而步骤栏、生成注释都要能说清「那两个元素分别是什么」。
MATCH_SUMMARY_JS = """(els) => els.slice(0, 10).map((el) => {
  const row = el.closest('tr, li, [role=row], [role=listitem], dd, article');
  const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  return {
    tag: el.tagName.toLowerCase(),
    text: clean(el.innerText || el.value || '').slice(0, 40),
    row: row ? clean(row.innerText).slice(0, 40) : null,
    visible: el.getClientRects().length > 0,
  };
})"""
MAX_MATCH_ITEMS = 10
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
# 浏览器自己知道这次导航的起因（刷新 / 前进后退 / 普通跳转），读它比拿「URL 变没变」猜准。
# 同时带回 timeOrigin：它是当前 document 的身份。SPA 在启动时会做 same-document 导航
# （pushState / hash 变化），Playwright 对每一次都会再发一个 framenavigated，但
# performance 里那条 navigation entry 始终是首次加载的，于是「刷一次」会被上报三四遍。
# 用 timeOrigin 判「是不是同一个 document」，重复上报就一眼能看出来。
NAV_INFO_JS = (
    "() => { try { const e = performance.getEntriesByType('navigation')[0];"
    ' return { type: e ? e.type : null, origin: performance.timeOrigin || null } }'
    ' catch (err) { return null } }'
)
# 点击之后这么短时间内到达的导航算「这次点击的结果」，不单独记一步 goto。
# 用一次性窗口而不是「上一步是不是 click」：后者会让点击之后的刷新、以及任何
# 「点完按钮页面自己跳」都永久丢失（刷新那条 bug 的根因）。
NAV_FROM_CLICK_SECONDS = 1.5
STEP_TITLES = {'goto': '打开', 'reload': '刷新', 'click': '点击', 'fill': '输入', 'select': '选择',
               'press': '按键', 'dialog': '弹窗',
               'new_page': '标签页', 'assert': '断言', 'wait': '等待', 'sleep': '等待'}
STATUS_LABELS = {'unique': '唯一定位', 'fragile': '结构定位', 'ambiguous': '定位不唯一', 'stale': '定位已变化'}
STATUS_TONES = {'unique': 'ok', 'fragile': 'warn', 'ambiguous': 'bad'}
# 就地编辑允许改的字段：只改「值」，不动定位（换元素走拾取）。
EDITABLE_FIELDS = {
    'fill': ('value',),
    'select': ('value', 'label'),
    'goto': ('url',),
    'sleep': ('seconds',),
    # 按键改键名；弹窗改「确定 / 取消」——录制时一律 accept，要 cancel 只能在这里改。
    'press': ('value',),
    'dialog': ('value',),
    # 断言和等待只差「最长等待」这一个参数，所以两者都能就地改等待与刷新节奏。
    # 改成 0 秒会从等待退回立即断言，step type 跟着变。
    'assert': ('timeout', 'interval', 'reload'),
    'wait': ('timeout', 'interval', 'reload'),
}
MAX_VALUE_LENGTH = 500
# 文本框里这些键只改文本或移光标，结果由紧随其后的 fill 全量表达（见 _press_only_edits_text）。
TEXT_EDIT_KEYS = frozenset([
    'Backspace', 'Delete', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight',
    'Home', 'End', 'PageUp', 'PageDown', 'Space',
])
# textarea 里回车是换行，同样被 fill 覆盖；单行输入框里的回车是提交，必须保留。
TEXTAREA_EDIT_KEYS = TEXT_EDIT_KEYS | {'Enter'}


def _press_only_edits_text(step):
    """这条 press 是否只是「改文本 / 移光标」，其结果会被紧接着的 fill 完整覆盖。

    只看按键和元素类型。单行输入框里的 Enter 是提交表单，Escape 是取消浮层，
    都不能吞；Tab 是跳焦点，更不能吞。
    """
    key = (step.get('value') or '').split('+')[-1]
    snap = ((step.get('target') or {}).get('snap') or {})
    tag = snap.get('tag')
    if tag == 'textarea':
        return key in TEXTAREA_EDIT_KEYS
    if tag == 'input':
        return key in TEXT_EDIT_KEYS
    return False


def vanished_primary(ordered):
    """点击 / 回车把页面带走了：这一步的元素已经不在当前 document 上，所有候选都数不到。

    这不是「定位写错了」，而是「计数的时候页面已经翻篇」。用快照里语义最强的那条顶上，
    count 记 0 表示没核实过，界面上会提示复核。

    不这么兜底的话，凡是会跳转的步骤（点链接、回车提交表单）都会因为没有唯一候选
    而在生成时被整体拒绝——而这恰恰是录制里最常见的一类步骤。
    """
    if not ordered or any(c.get('count', 0) > 0 for c in ordered):
        return None
    return dict(ordered[0], count=0)


class RecordingError(Exception):
    pass


class RecordingBusy(RecordingError):
    """已有一个正在进行的录制。"""


class RecordingSession(object):
    _current = None
    _current_lock = threading.Lock()

    def __init__(self, base_url, headless=False, driver=None, panel_url=None,
                 panel_ratio=0.45, hud_panel=False, embed_url=None,
                 ask_daemon=None, ask_model=None, ask_config=None):
        self.id = uuid.uuid4().hex
        self.base_url = base_url
        self.status = 'starting'
        self.error = None
        self._headless = headless
        # 录制期常驻的解析进程：起停都跟着录制走，失败只影响解析速度。
        # 测试里传 False 关掉，否则每个浏览器用例都会真起一个 opencode。
        self._ask_enabled = (os.environ.get('RECORDING_ASK_DAEMON', '1').lower()
                             not in ('0', 'false', 'no')) if ask_daemon is None else bool(ask_daemon)
        self._ask_model = ask_model
        self._ask_config = ask_config or {}
        self._ask = None
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
        # 最近一次画框落在哪个 frame：清除时不必遍历所有页面。
        self._overlay_frame = None
        self._events = []
        self._steps = []
        self._picks = []
        # 元素身份 → 别名。同一个元素复用同一个别名，不同元素撞名时加后缀（见 _alias_for）。
        self._alias_keys = {}
        self._wired = set()
        self._last_url = {}
        # frame id → 当前 document 的身份（performance.timeOrigin）。同一个 document
        # 只会有一条 navigation entry，重复的 framenavigated 靠它去重。
        self._doc_stamp = {}
        # 新标签页已经由 new_page 那一步表示过的首次导航，不该再判成一次跳转。
        self._skip_nav = set()
        # frame id → 最近一次点击的时间。用来判断「这次导航是不是点击引起的」，取用即失效。
        self._pending_click = {}
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
                  panel_ratio=0.45, hud_panel=False, embed_url=None,
                  ask_daemon=None, ask_model=None, ask_config=None):
        with cls._current_lock:
            current = cls._current
            if current is not None and current.status in ('starting', 'recording'):
                raise RecordingBusy('已有正在进行的录制，请先停止或取消')
        # 上一轮停止后浏览器还开着，开新的之前必须收掉，否则会留下一个没人管的窗口。
        if current is not None:
            current.dispose()
        session = cls(base_url, headless=headless, driver=driver, panel_url=panel_url,
                      panel_ratio=panel_ratio, hud_panel=hud_panel, embed_url=embed_url,
                      ask_daemon=ask_daemon, ask_model=ask_model, ask_config=ask_config)
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

    def highlight(self, no, index=None, framework='playwright-ts'):
        """把这一步会用的定位所匹配到的元素全部框出来，index 指向的那个实心高亮。

        只在浏览器还开着时可用；关掉之后没有页面可画，返回 ok=False。
        """
        return self._call('highlight', no=no, index=index, framework=framework)

    def clear_highlight(self):
        return self._call('clear_highlight')

    def set_chosen(self, no, desc, index):
        """用户看过框之后明确指定「就是这个（第 index 个匹配）」。

        必须连 desc 一起存：index 是**某一条候选**的匹配顺序，换一条候选就不是同一个
        元素了（不同选择器的匹配顺序不一定相同）。生成时据此走用户选的那条。
        """
        return self._call('set_chosen', no=no, desc=desc, index=index, timeout=COMMAND_TIMEOUT)

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
        # 有些测试/工具会绕过 __init__ 直接造会话，取值一律走 getattr。
        pool = getattr(self, '_ask', None)
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
                # 断言解析器（录制期常驻的 opencode）状态：就绪 / 启动中 / 不可用。
                'ask': pool.snapshot() if pool is not None else disabled_snapshot(),
                # 生成进度：主界面和录制面板是两个窗口，只靠各自的本地 loading
                # 会出现「一边点完，另一边还能点」，所以状态放服务端一起返回。
                'emit': progress.PROGRESS.snapshot(),
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

    @staticmethod
    def _merge_key(snap, primary, frame):
        """「同一元素的连续输入合并成一步」用的身份。

        不能用 CSS 路径：真实页面在第一次输入之后几乎都会改 DOM——包一层 div、
        插一个隐藏 input（自动填充/验证码）、冒出校验错误态——路径一变，一次输入就
        被切成好几条 fill。主定位唯一时选择器本身就是身份，DOM 怎么重排都不变；
        没有唯一候选时才退回结构路径（那时确实只能靠结构区分同名元素）。
        """
        if primary is not None:
            return ('locator', primary['desc'], id(frame))
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
            elif kind == 'press':
                key = (changes.get('value') or '').strip()
                if not key:
                    raise RecordingError('按键不能为空')
                if len(key) > 40:
                    raise RecordingError('按键名太长')
                step['value'] = key
            elif kind == 'dialog':
                action = (changes.get('value') or '').strip()
                if action not in ('accept', 'dismiss'):
                    raise RecordingError('弹窗动作只能是 accept 或 dismiss')
                step['value'] = action
                step['dialog'] = dict(step.get('dialog') or {}, action=action)
            elif kind in ('assert', 'wait'):
                try:
                    merged = intent_mod.apply_poll(step.get('intent') or {}, changes)
                except intent_mod.IntentError as exc:
                    raise RecordingError(str(exc))
                step['intent'] = merged
                # 等待时长决定它到底是「立即断言」还是「等到成立」，类型跟着变，
                # 生成端才能挑对模板（expect 一次 vs Wait Until / expect.poll）。
                step['type'] = ir.step_type_for_intent(merged['type'], merged)
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
        step_type = ir.step_type_for_intent(intent['type'], intent)
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
            self._insert_step(step, after_no)
            self._renumber()
        self._schedule_hud()
        return self.snapshot()

    def _insert_step(self, step, after_no):
        """插到 after_no 之后（None 表示追加到末尾）。调用方需持有 self._lock。"""
        if after_no is None:
            self._steps.append(step)
            return
        idx = self._index_of(after_no)
        # 不能插进「点击 → 标签页切换」之间，否则那一步的绑定就断了。
        if idx + 1 < len(self._steps) and self._steps[idx + 1]['type'] == 'new_page':
            idx += 1
        self._steps.insert(idx + 1, step)

    def add_reload_step(self, after_no=None):
        """插入一步「刷新页面」，录制中顺带真的刷新一次。

        刷新只走这条路，不做自动检测：自动检测分不清「SPA 启动时的同文档导航」
        和「用户真的刷了」，已经证明会多记。显式添加还有个好处是能补录——
        行内「+」可以让它落在任意一步之后。
        """
        return self._call('reload', after_no=after_no)

    def _add_reload(self, after_no=None):
        page = self._current_page()
        with self._lock:
            step = ir.make_step('reload', no=0, source='manual',
                                page_index=self._page_index(page) if page is not None else 0)
            self._insert_step(step, after_no)
            self._renumber()
        # 只有录制中才真的刷：停止后插这一步只是编辑列表，没有「当前状态」需要对齐。
        if page is not None and self.status == 'recording':
            try:
                self._reload_page(page)
            except Exception as exc:
                self._schedule_hud()
                raise RecordingError('刷新页面失败：%s' % exc)
        self._schedule_hud()
        return self.snapshot()

    def _reload_page(self, page):
        """真的刷新一次。嵌入模式刷 iframe——外壳页刷新不代表被测系统刷新过。"""
        frame = None
        if self._embed:
            for item in page.frames:
                if item is not page.main_frame and self._is_app_frame(item):
                    frame = item
        if frame is not None:
            # 用 setTimeout 把刷新推到 evaluate 返回之后，避免「执行上下文已销毁」的报错。
            frame.evaluate('setTimeout(function () { location.reload(); }, 0)')
            return
        page.reload(wait_until='domcontentloaded')

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
                self._start_ask_pool()
                if self._driver is not None:
                    self._driver(self, page)
                self._process_inbox()
                self._ready.set()
                self._loop()
        except Exception as exc:
            self.error = str(exc)
            self.status = 'failed'
        finally:
            self._stop_ask_pool()
            self._ready.set()

    # ---- 解析进程（断言智能识别）----

    def _start_ask_pool(self):
        """录制一开始就把解析用的 opencode 拉起来并预热。

        只起一个线程，不阻塞录制：起进程 + 预热要 7 秒，用户这会儿正在看页面。
        """
        if not self._ask_enabled:
            return
        pool = AskPool(self._ask_config, self._ask_model)
        self._ask = pool
        pool.start()

    def _stop_ask_pool(self, delay=0):
        pool = getattr(self, '_ask', None)
        if pool is not None:
            pool.stop(delay=delay)

    def ask_pool(self):
        return getattr(self, '_ask', None)

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
                        # 停止后浏览器还开着，用户可能补一条断言再解析，所以延迟杀；
                        # 关浏览器 / 闲置超时会走 _shutdown，那里是立即杀。
                        self._stop_ask_pool(delay=ASK_STOP_DELAY_SECONDS)
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
                elif name == 'reload':
                    result = self._add_reload(kwargs.get('after_no'))
                elif name == 'highlight':
                    result = self._highlight_now(kwargs['no'], kwargs.get('index'),
                                                 kwargs.get('framework') or 'playwright-ts')
                elif name == 'clear_highlight':
                    result = self._clear_highlight_now()
                elif name == 'set_chosen':
                    result = self._set_chosen_now(kwargs['no'], kwargs['desc'], kwargs['index'])
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
        # 解析进程跟着浏览器一起收：浏览器都关了，没人会再插断言。
        self._stop_ask_pool()
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
        elif kind == 'reload':
            title = '刷新页面'
        elif kind == 'sleep':
            title = '固定等待'
        elif kind == 'press':
            title = ('%s 上按 %s' % (alias, step.get('value') or '')).strip()
        elif kind == 'dialog':
            info = step.get('dialog') or {}
            action = '确定' if (info.get('action') or step.get('value')) == 'accept' else '取消'
            title = '弹窗「%s」→ %s' % ((info.get('message') or '')[:24], action)
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
            # 「点击 → 标签页切换」和「点击 → 弹窗」都是绑定的：单独删掉后一半会留下一个
            # 无处安放的 dialog 步骤，撤销时一起删，保持结构合法。
            if self._steps[idx]['type'] in ('new_page', 'dialog') and idx > 0:
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
        # 停止后浏览器还开着，用户可能又开新标签页；那时不该再往列表里加步骤。
        if self._opened and id(page) not in self._wired and self.status == 'recording':
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
                    # 这个 frame 的第一次导航就是它自己打开的那次，已经由 new_page 表示。
                    # 显式记一笔，避免「新 document + 地址没变」被下面的兜底逻辑当成刷新。
                    self._skip_nav.add(id(page.main_frame))
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
        # 弹窗是浏览器自己的对话框，注入的 recorder.js 拿不到，只能在 Playwright 这层接。
        page.on('dialog', lambda dialog, pg=page: self._on_dialog(pg, dialog))
        if self._pick:
            self._apply_pick(page)
        if self._paused:
            self._apply_pause(page)
        self._push_hud()

    def _on_navigated(self, page, frame):
        self._inbox.put(lambda: self._handle_navigated(page, frame))

    def _on_dialog(self, page, dialog):
        """接住 alert / confirm / prompt。

        必须在这个回调里就把弹窗处理掉：Playwright 一旦有 dialog 监听，就不再自动接管，
        不 accept / dismiss 的话页面会一直挂着，用户看到的是「点了按钮就卡死」。
        默认自动接受（用户录制时本来就是在点「确定」），录制结果里再记一步 dialog。

        代价是用户没法在录制时点「取消」：Playwright 不把弹窗交给浏览器 UI，我们也就
        无从得知用户点了哪个。录到的动作固定是 accept，需要取消时在步骤上改。
        """
        info = {'kind': None, 'message': None, 'action': 'accept'}
        try:
            info['kind'] = dialog.type
            info['message'] = (dialog.message or '')[:200]
        except Exception:
            pass
        try:
            dialog.accept()
        except Exception:
            # 已经关掉了或者被别处处理了：不影响录制，只是这一步标成 failed。
            info['action'] = 'failed'
        self._inbox.put(lambda: self._handle_dialog(page, info))

    def _handle_dialog(self, page, info):
        # 停止/暂停期间弹的框不该变成步骤，但上面已经 accept 了，页面不会卡。
        if self.status != 'recording' or self._paused:
            return
        # beforeunload 是「确定离开此页」的浏览器确认框，不是用户主动的交互。
        # 记成步骤会让回放多一次确认，而且它出现与否跟时序有关，比步骤本身还脆。
        if info.get('kind') == 'beforeunload':
            return
        self._touch()
        action = info.get('action') or 'accept'
        with self._lock:
            step = ir.make_step('dialog', no=0, value=action,
                                page_index=self._page_index(page),
                                dialog={'kind': info.get('kind'),
                                        'message': info.get('message'),
                                        'action': action})
            self._steps.append(step)
            self._renumber()
        self._schedule_hud()

    def _handle_navigated(self, page, frame):
        # 嵌入模式下被测页面是外壳里的 iframe，也要按「页面跳转」记录。
        if not self._is_app_frame(frame):
            return
        url = frame.url
        if not url or url.startswith('about:'):
            return
        info = self._nav_info(frame, self._doc_stamp.get(id(frame)))
        nav_type = info.get('type') if info else None
        stamp = info.get('origin') if info else None
        key = id(frame)
        # 新标签页打开时的第一次导航已由 new_page 表示，这里只登记身份。
        if key in self._skip_nav:
            self._skip_nav.discard(key)
            self._last_url[key] = url
            if stamp is not None:
                self._doc_stamp[key] = stamp
            return
        same_url = self._last_url.get(key) == url
        # 同一个 document 的又一次上报：SPA 启动时的 pushState / hash 变化都会各自触发
        # 一次 framenavigated，但它们并不产生新的 navigation entry。真正的导航一定是
        # 新 document，所以 timeOrigin 会变。靠它把「一次导航多条上报」压成一条。
        if stamp is not None and self._doc_stamp.get(key) == stamp:
            self._last_url[key] = url
            return
        if stamp is not None:
            self._doc_stamp[key] = stamp
        self._last_url[key] = url
        # 停止 / 暂停期间浏览器还开着，用户照样会点，但这些动作不该入库。
        if self.status != 'recording' or self._paused:
            return
        with self._lock:
            prev = self._steps[-1] if self._steps else None
            # 新标签页的第一次跳转只补全 url，不单独记一步。
            if prev and prev['type'] == 'new_page' and prev.get('url') is None:
                prev['url'] = url
                self._schedule_hud()
                return
            # 刷新不自动记：用户按 F5 只影响浏览器里的当前状态，步骤由工具栏的
            # 「刷新页面」显式添加（那一步同时保证刷新后的页面与后续步骤对得上）。
            # 这里一并吞掉「按钮自己刷出来的那次」，避免它退化成一条 goto。
            if nav_type == 'reload':
                return
            if nav_type == 'back_forward':
                # Playwright 不告诉你方向；goto 在脚本语义上已经够了（测试不关心历史栈）。
                step_type = 'goto'
            elif same_url:
                # 地址没变却换了 document：刷新（已由按钮负责）或地址栏重输同一地址。
                # 两者都不该自动变成「打开页面」。
                return
            elif self._clicked_recently(frame):
                # 点击引起的跳转不单独记 goto：脚本里点完自然会跳。
                return
            else:
                step_type = 'goto'
            self._steps.append(ir.make_step(step_type, no=0, url=url,
                                            page_index=self._page_index(page)))
            self._renumber()
        # 导航后文档是新的，浮层得按当前步骤重画一次。
        self._schedule_hud()

    def _nav_info(self, frame, prev_stamp=None):
        """这次导航的起因：navigate / reload / back_forward，外加当前 document 的 timeOrigin。

        读 performance 是唯一可靠的办法——URL 变没变区分不了「谁发起的导航」，
        而刷新恰恰就是「地址没变的导航」。

        刚提交导航的那一瞬，evaluate 有可能还落在旧 document 上（读到的是上一次的
        timeOrigin）。那就等一小会儿再读一次：新 document 的身份一定不同。
        """
        info = self._read_nav_info(frame)
        if prev_stamp is None or info is None or info.get('origin') != prev_stamp:
            return info
        try:
            frame.wait_for_timeout(80)
        except Exception:
            return info
        retry = self._read_nav_info(frame)
        return retry or info

    def _read_nav_info(self, frame):
        try:
            value = frame.evaluate(NAV_INFO_JS)
        except Exception:
            return None
        return value if isinstance(value, dict) else None

    def _clicked_recently(self, frame):
        """这一步导航是不是刚发生的点击引起的。一次性窗口，不再用「上一步是不是 click」判。"""
        now = time.time()
        key = id(frame)
        at = self._pending_click.get(key)
        if at is None:
            return False
        if now - at > NAV_FROM_CLICK_SECONDS:
            self._pending_click.pop(key, None)
            return False
        return True

    def _page_index(self, page):
        try:
            return self._context.pages.index(page)
        except ValueError:
            return 0

    def _doc_identity(self, frame):
        """录制这一刻元素所在的页面/document。

        stop 时要用它判断「这个 target 还在不在当前页面上」：登录页的步骤在登录成功
        之后就不该拿聊天页重算，否则 count 全变 0，会被误报成「不唯一 / 找不到」。
        URL 是「页面是否翻走」的主判据；timeOrigin 只在旧数据没有 URL 时兜底。
        """
        try:
            url = frame.url
        except Exception:
            url = None
        stamp = self._doc_stamp.get(id(frame))
        if stamp is None:
            try:
                info = frame.evaluate(NAV_INFO_JS)
                stamp = info.get('origin') if isinstance(info, dict) else None
            except Exception:
                stamp = None
            if stamp is not None:
                self._doc_stamp[id(frame)] = stamp
        return {'url': url, 'stamp': stamp}

    def _same_page(self, doc, frame):
        """target 录制时的页面是否还是这个 frame 当前的那一个。

        只看 URL：停止时地址变了（/login → /global），就不该拿当前 DOM 去数旧元素。
        不能只靠 timeOrigin——SPA 的 pushState 换了页面内容，timeOrigin 却还是同一个。
        URL 相同（刷新 / 重新进入同一地址）才重算；旧数据没有 URL 时退回 timeOrigin。

        代价是 query / hash 变化也会被当成「翻页」，跳过复核。这是有意的保守选择：
        保留录制时的定位不会误报，拿新页面重算却会把好好的步骤洗成「不唯一」。
        """
        if not doc:
            return True
        try:
            url = frame.url
        except Exception:
            return False
        recorded = doc.get('url')
        if recorded:
            return url == recorded
        stamp = self._doc_stamp.get(id(frame))
        return doc.get('stamp') is not None and stamp is not None and doc['stamp'] == stamp

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

    def _collect_matches(self, frame, counted):
        """不唯一时，把「这批匹配到底是哪几个」存一份到 target 上。

        只取「命中数 > 1 的候选里最窄的那条」：用 getByRole 的 100 个匹配去列没有意义，
        但也不能拿 count=1 的 css 路径（它必然最窄，却只说明一个元素，没有可比性）。
        这一步发生在用户操作之后、录制线程里，实测 4–20ms。
        """
        pool = [c for c in counted if (c.get('count') or 0) > 1]
        if not pool:
            return None
        best = narrowest(pool)
        if best is None or (best.get('count') or 0) < 2:
            return None
        spec = best['spec']
        try:
            locator = getattr(frame, spec['method'])(*spec['args'], **spec['kwargs'])
            items = locator.evaluate_all(MATCH_SUMMARY_JS)
        except Exception:
            # 页面正在导航、或元素已消失：没有就没有，不能因此丢步骤。
            return None
        if not isinstance(items, list) or len(items) < 2:
            return None
        return {
            'desc': best['desc'],
            'count': best.get('count'),
            # evaluate_all 里已截到 10 条；count 会更大，界面据此提示「只列前 10」。
            'items': items[:MAX_MATCH_ITEMS],
        }

    # ---- 定位预览：把匹配元素框出来给人看 ----

    def _find_step(self, no):
        with self._lock:
            for step in self._steps:
                if step['no'] == no:
                    return step
        raise RecordingError('没有第 %s 步' % no)

    def _app_frame(self):
        """录制时没存 frame 的步骤（手工插入的）兜底用被测页面的主 frame。"""
        try:
            for page in self._context.pages:
                for frame in page.frames:
                    if self._is_app_frame(frame):
                        return frame
        except Exception:
            pass
        return None

    def _highlight_now(self, no, index, framework):
        """在页面上框出这一步的目标元素。必须在录制线程里跑（Playwright 对象不跨线程）。"""
        step = self._find_step(no)
        target = step.get('target') or {}
        frame = step.get('_frame') or self._app_frame()
        if frame is None:
            return {'ok': False, 'reason': 'no_frame', 'count': 0, 'items': [], 'active': 0}
        candidates = target.get('candidates') or []
        # 框哪一条，按「信息量最大」排：
        # 1) 用户亲手挑过的，就是他正在确认的东西；
        # 2) 录制时匹配到多个元素的那条——框出来才有「原来有 3 个」这个信息。
        #    结构定位（css 唯一）的步骤尤其需要它，否则框出来只有一个框，看不出风险；
        # 3) 否则才是生成时默认会用的那条。
        chosen = None
        picked = target.get('chosen') or {}
        if picked.get('desc'):
            chosen = next((c for c in candidates if c['desc'] == picked['desc']), None)
        if chosen is None:
            matches = target.get('matches') or {}
            if matches.get('desc'):
                chosen = next((c for c in candidates if c['desc'] == matches['desc']), None)
        if chosen is None:
            chosen = pick_for(framework, candidates) or best_effort(framework, candidates)
        if chosen is None:
            return {'ok': False, 'reason': 'no_candidate', 'count': 0, 'items': [], 'active': 0}
        try:
            locator = getattr(frame, chosen['spec']['method'])(
                *chosen['spec']['args'], **chosen['spec']['kwargs'])
            count = locator.count()
        except Exception as exc:  # noqa: BLE001 - frame 已销毁/页面已关
            return {'ok': False, 'reason': 'frame_lost', 'detail': str(exc)[:200],
                    'count': 0, 'items': [], 'active': 0}
        if count <= 0:
            # 录制中跳转过的步骤：旧页面的元素在新页面上一个都数不到。
            return {'ok': False, 'reason': 'not_found', 'desc': chosen['desc'],
                    'count': 0, 'items': [], 'active': 0}
        active = index if isinstance(index, int) else None
        if active is None:
            active = (target.get('chosen') or {}).get('index')
        if active is None:
            active = chosen.get('index')
        if not isinstance(active, int) or not 0 <= active < count:
            active = 0
        try:
            items = locator.evaluate_all(OVERLAY_JS, active)
        except Exception as exc:  # noqa: BLE001
            return {'ok': False, 'reason': 'overlay_failed', 'detail': str(exc)[:200],
                    'desc': chosen['desc'], 'count': count, 'items': [], 'active': active}
        self._overlay_frame = frame
        return {'ok': True, 'desc': chosen['desc'], 'count': count,
                'active': active, 'items': items or []}

    def _clear_highlight_now(self):
        frame = getattr(self, '_overlay_frame', None)
        if frame is not None:
            try:
                frame.evaluate(CLEAR_OVERLAY_JS)
            except Exception:
                pass
        return {'ok': True}

    def _set_chosen_now(self, no, desc, index):
        step = self._find_step(no)
        target = step.get('target') or {}
        index = int(index)
        with self._lock:
            for candidate in target.get('candidates') or []:
                if candidate['desc'] == desc:
                    candidate['index'] = index
                    candidate.pop('index_invalid', None)
            target['chosen'] = {'desc': desc, 'index': index}
        return self.snapshot()

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

        # 点可写入的文本框只是聚焦，值由随后的 fill 表达，记成 click 会多一步。
        # 只读框例外：点它是展开日期面板 / 下拉筛选，那一步是流程的一部分。
        if (kind == 'click' and snap['tag'] == 'input'
                and snap.get('type') not in NON_TEXT_INPUT_TYPES and not snap.get('readonly')):
            return
        if kind == 'click' and snap['tag'] == 'textarea' and not snap.get('readonly'):
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
        # 记下元素属于哪个 document：停止录制时只对「还在当前页面上」的 target 重算。
        # 优先用 recorder.js 在页面里读到的值，避免和导航抢时序；旧注入脚本没有这两个字段时兜底。
        doc = {'url': snap.pop('_doc_url', None), 'stamp': snap.pop('_doc_stamp', None)}
        if not doc['url'] and doc['stamp'] is None:
            doc = self._doc_identity(frame)
        counted = self._count(frame, build_specs(snap), mark=mark)
        primary, ordered = rank(counted)
        unverified = False
        if primary is None:
            primary = vanished_primary(ordered)
            unverified = primary is not None
        status = 'stale' if unverified else status_of(primary)
        target = ir.make_target(snap, ordered, primary, status)
        if unverified:
            target['unverified'] = True
        elif any((c.get('count') or 0) > 1 for c in ordered):
            # 页面上存在同名元素时就把「那几个分别是什么」记下来。
            # 不止 ambiguous 要记：更常见的是 getByRole 命中 3 个、于是退到 css 绝对路径
            # 标成「结构定位」——那种步骤用户更该看见「其实有 3 个，你点的是第 2 个」。
            # 浏览器一关这份快照就取不到了，必须在录制当时存。
            matches = self._collect_matches(frame, ordered)
            if matches:
                target['matches'] = matches
        index = self._page_index(page)

        with self._lock:
            # 别名在这里定，不在 make_target 里定：唯一性要看已录入的步骤，是会话状态。
            target['alias'] = self._alias_for(target, self._alias_key(snap, primary, frame))
            seq = len(self._events)
            self._events.append({'seq': seq, 'type': kind, 'status': status,
                                 'desc': primary['desc'] if primary else None})

            if kind == 'pick':
                self._picks.append({'seq': seq, 'target': target, '_frame': frame, '_doc': doc})
                self._schedule_badge(frame, status, 'pick', target.get('alias'))
                self._schedule_hud()
                return

            key = self._merge_key(snap, primary, frame)
            prev = self._steps[-1] if self._steps else None
            if (kind == 'fill' and prev and prev['type'] == 'press'
                    and prev.get('_key') == key and _press_only_edits_text(prev)):
                # 用户在文本框里退格改字：按键的结果已经完整落在下面这条 fill 里了，
                # 留着它不但多一步，还会把「同一元素连续输入」切成两段。
                self._steps.pop()
                self._renumber()
                prev = self._steps[-1] if self._steps else None
            if kind == 'fill' and prev and prev['type'] == 'fill' and prev.get('_key') == key:
                prev['value'] = data.get('value')
                prev['target'] = target
                prev['_frame'] = frame
                prev['_doc'] = doc
                self._schedule_hud()
                return

            step = ir.make_step(kind, no=0, target=target, value=data.get('value'),
                                label=data.get('label'), page_index=index)
            if kind == 'press':
                # 键名走 value：生成端两种框架都是「对元素发一个键」，只需要一个字符串。
                step['value'] = data.get('key')
                step['label'] = None
            step['_key'] = key
            step['_frame'] = frame
            step['_doc'] = doc
            self._steps.append(step)
            if kind in ('click', 'press'):
                # 记下时刻而不是「上一步是点击」：只有紧跟着的这次导航才算它引起的，
                # 之后用户再刷新/页面自己跳，仍然照记。
                # 按键也要记：搜索框按 Enter 提交后页面会跳转，那不该再录一条「打开 URL」。
                self._pending_click[id(frame)] = time.time()
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
                    owners.setdefault(id(target), (target, frame, step.get('_doc')))
            for pick in self._picks:
                owners.setdefault(id(pick['target']), (pick['target'], pick['_frame'], pick.get('_doc')))
        for target, frame, doc in owners.values():
            # 录制时在别的页面上（登录页 → 登录成功后）：旧元素已经随旧页面销毁，
            # 不能拿当前页面重算。否则所有 count 变 0，界面会把「页面翻走了」误报成
            # 「不唯一 / 找不到」，生成也会被拦。保留录制时的 candidates / primary /
            # status，只标 page_gone，让界面提示「未复核，生成请回放确认」。
            if not self._same_page(doc, frame):
                with self._lock:
                    target['page_gone'] = True
                continue
            previous = target['primary']['desc'] if target.get('primary') else None
            # index 是录制那一刻算的，复核时元素上的临时标记早就没了，
            # 按 desc 把旧 index 带过来，别让复核把降级定位的信息洗掉。
            kept_index = {c['desc']: c.get('index') for c in (target.get('candidates') or [])}
            counted = self._count(frame, build_specs(target['snap']))
            for item in counted:
                kept = kept_index.get(item['desc'])
                if kept is None:
                    continue
                item['index'] = kept
                n = item.get('count')
                # 元素变少了（列表被删了几行）：用户选的序号已经不存在。
                # 保留它而不是静默改回第一个——静默会点到别的元素，比报错更糟；
                # 标出来让界面提示「你选的第 3 个现在只有 2 个」。
                if isinstance(n, int) and n > 0 and kept >= n:
                    item['index_invalid'] = True
            primary, ordered = rank(counted)
            if target.get('unverified'):
                # 录制当场就没数到（页面跳走了），复核同样数不到是正常的，保留降级主定位；
                # 否则这里会把 primary 清空，生成时又变成「元素已找不到」。
                primary = vanished_primary(ordered) or target.get('primary')
                if primary is not None:
                    primary = dict(primary, count=0)
            with self._lock:
                target.pop('page_gone', None)
                target['candidates'] = ordered
                target['primary'] = primary
                # 没核实过的定位不能报成 unique：界面上要能看出「这条要回放确认」。
                target['status'] = 'stale' if target.get('unverified') else stale_check(previous, primary)
                # 用户手动指定的那个元素同样要复核：选的是第 3 个，现在只剩 2 个。
                chosen = target.get('chosen')
                if chosen:
                    match = next((c for c in ordered if c['desc'] == chosen.get('desc')), None)
                    count = match.get('count') if match else None
                    if isinstance(count, int) and count > 0 and chosen.get('index', 0) >= count:
                        chosen['index_invalid'] = True
                    else:
                        chosen.pop('index_invalid', None)
