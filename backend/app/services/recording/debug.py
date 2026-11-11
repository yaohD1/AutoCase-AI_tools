"""调试回放：把录制 IR 一步一步执行，中间能暂停、单步、看画面。

和 `replay.py` 的分工：

- `replay` 求的是**结论**——一口气跑完，出一份「第几步失效」的报告；
- 这里求的是**过程**——让用户看着它跑，想停在哪儿就停在哪儿。

所以多了三样东西：步间节奏、控制闸门、画面帧。原生速度下 6 步不到 2 秒就跑完了
（实测），人眼根本跟不上，节奏是必须的，不是锦上添花。

**为什么不用 `playwright test` / `robot` 子进程跑生成好的脚本**：那两个一启动就由不得
我们，题目要的「暂停在某一步」做不到（子进程只能 kill）。要停就得自己解释执行。
真脚本的验证另有一条路（见 `sandbox.py`），两条路解决的是不同问题。
"""
import logging
import threading
import time
import uuid
from collections import deque

from app.services.recording import replay

logger = logging.getLogger(__name__)

# 两次抓帧的最小间隔（秒）。0.09 约等于 11 fps，实测截图一次 30~40ms，留了余量。
GRAB_INTERVAL = 0.09
# 步间停留（秒）。0.55 是「人眼能看清页面变了、又不至于等得烦」的实测值。
DEFAULT_DELAY = 0.55
# 单次回放的总时长上限：卡住时得有人收尸，否则线程和浏览器进程会一直挂着。
MAX_RUNTIME = 600
FRAME_QUALITY = 55
VIEWPORT = {'width': 1440, 'height': 900}
# 高亮只等 0.8 秒：它是个「顺带让人看见」的效果，不该因为元素还没出来就卡住整条流程。
HIGHLIGHT_MS = 800
# 高亮用目标元素自己的 outline，不往 DOM 里插节点。
# 插节点会被自己的定位器数进去（`//div` 一类会连高亮框一起命中），
# 回放时「命中 1 个」突然变「命中 2 个」，排查半天才发现是自己画的框。
# 存在元素上的 __acOutline 是 JS 属性，不进 DOM，天然不污染。
_HL_ON = ("(el, c) => { if (el.__acOutline === undefined) el.__acOutline = el.style.outline || '';"
          " el.style.outline = '3px solid ' + c; el.style.outlineOffset = '2px'; }")
_HL_OFF = ("(el) => { if (el.__acOutline !== undefined) {"
           " el.style.outline = el.__acOutline; el.style.outlineOffset = '';"
           " delete el.__acOutline; } }")


class DebugError(Exception):
    pass


class DebugBusy(DebugError):
    pass


# 断言/等待步骤的 intent 类型 → 一句人话。没有这张表的话，这类步骤在右侧列表和
# 底部详情里只剩一个「assert」，用户根本不知道它在等什么。
INTENT_LABELS = {
    'visible': '元素可见',
    'wait_visible': '元素可见',
    'hidden': '元素不可见',
    'wait_hidden': '元素不可见',
    'text_contains': '元素文字包含',
    'text_equals': '元素文字等于',
    'value_equals': '输入值等于',
    'enabled': '元素可用',
    'url_contains': '地址包含',
    'page_text_contains': '页面包含文字',
    'page_ready': '页面加载完成',
    'sleep': '固定等待',
}


def _label(step):
    """给没有 alias / url 的步骤补一句人话，右侧列表不能只剩一个破折号。"""
    kind = step.get('type')
    if kind in ('assert', 'wait', 'sleep'):
        intent = step.get('intent') or {}
        what = INTENT_LABELS.get(intent.get('type'), intent.get('type') or '—')
        value = intent.get('value')
        if value:
            return '%s：%s' % (what, str(value)[:28])
        if kind == 'sleep':
            return '固定等待 %ss' % (intent.get('seconds') or '')
        return what
    if kind == 'reload':
        return '刷新当前页面'
    if kind == 'press':
        return '按键 %s' % (step.get('value') or '')
    if kind == 'dialog':
        info = step.get('dialog') or {}
        action = '确定' if (info.get('action') or step.get('value')) == 'accept' else '取消'
        return '弹窗（%s）' % action
    if kind == 'sleep':
        return '等待 %ss' % ((step.get('intent') or {}).get('seconds') or '')
    if kind == 'new_page':
        return '新标签页'
    return None


def _describe(step):
    """一步的展示信息：列表里那一行要的全部字段，执行前就能算好。"""
    target = step.get('target') or {}
    intent = step.get('intent') or {}
    return {
        'no': step.get('no'),
        'type': step.get('type'),
        'alias': target.get('alias'),
        'url': step.get('url'),
        'label': _label(step),
        'intent': intent.get('type') if intent else None,
        'value': step.get('value'),
    }


class DebugSession:
    """一次调试回放。一个进程里同时只允许一个（浏览器很重，而且界面只有一个入口）。"""

    def __init__(self, steps, framework, delay=None, headless=True):
        self.id = 'dbg-%s' % uuid.uuid4().hex[:10]
        self.steps = steps
        self.framework = framework
        self.delay = DEFAULT_DELAY if delay is None else max(0.0, float(delay))
        self.headless = headless

        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._frame = None
        self._frame_seq = 0
        self._last_grab = 0.0
        self._grabber = None

        self._paused = False
        self._allow_one = False
        self._stepped = False
        self._stopped = False
        self._thread = None
        self._started = time.monotonic()

        self._state = {
            'id': self.id,
            'status': 'starting',
            'framework': framework,
            'engine': 'playwright',
            'total': len(steps),
            'index': 0,
            'elapsed': 0.0,
            'error': None,
            'failed_no': None,
            'url': None,
            'steps': [dict(_describe(s), status='pending', ms=None, used=None, error=None)
                      for s in steps],
        }

    # ---------- 对外 ----------

    def start(self):
        self._thread = threading.Thread(target=self._run, name='debug-replay', daemon=True)
        self._thread.start()
        return self

    def snapshot(self):
        with self._lock:
            data = dict(self._state)
            data['steps'] = [dict(s) for s in self._state['steps']]
            data['paused'] = self._paused
            data['elapsed'] = round(time.monotonic() - self._started, 1)
            data['delay'] = self.delay
            data['frame_seq'] = self._frame_seq
            return data

    def latest_frame(self):
        """返回 (jpeg bytes, seq)。seq 用来让推流端判断要不要发新帧。"""
        with self._lock:
            return self._frame, self._frame_seq

    def control(self, action):
        with self._cond:
            if action == 'pause':
                if self._state['status'] in ('done', 'failed', 'stopped'):
                    return
                self._paused = True
                self._state['status'] = 'paused'
            elif action == 'resume':
                self._paused = False
                self._allow_one = False
                if self._state['status'] in ('paused', 'running'):
                    self._state['status'] = 'running'
            elif action == 'step':
                # 单步 = 保持暂停，但放行一步。闸门看到 _allow_one 就开一次。
                self._paused = True
                self._allow_one = True
                if self._state['status'] == 'paused':
                    self._state['status'] = 'running'
            elif action == 'stop':
                self._stopped = True
                self._paused = False
                self._allow_one = True
            elif action == 'speed':
                pass
            else:
                raise DebugError('不认识的控制命令：%s' % action)
            self._cond.notify_all()
        if action == 'speed':
            return
        self._push()

    def set_speed(self, delay):
        self.delay = max(0.0, min(3.0, float(delay)))

    def wait_done(self, timeout=MAX_RUNTIME):
        if self._thread:
            self._thread.join(timeout)

    # ---------- 状态维护 ----------

    def _push(self):
        """把状态变化通知出去。轮询接口直接读 _state，这里只是留个钩子给将来的 SSE。"""
        return

    def _set(self, **fields):
        with self._lock:
            self._state.update(fields)

    def _mark(self, index, **fields):
        with self._lock:
            if 0 <= index < len(self._state['steps']):
                self._state['steps'][index].update(fields)

    # ---------- 画面 ----------

    def _grab(self, force=False):
        """抓一帧塞进缓冲区。抓不到就跳过——画面断了不该影响回放本身。"""
        now = time.monotonic()
        if not force and now - self._last_grab < GRAB_INTERVAL:
            return
        grabber = self._grabber
        if grabber is None:
            return
        try:
            data = grabber()
        except Exception:
            return
        if not data:
            return
        self._last_grab = now
        with self._lock:
            # 同一个画面反复压进来只是浪费带宽：长度一样就先当没变（JPEG 里长度变了
            # 基本就是画面变了，误判的代价只是少发一帧，下一轮就补上）。
            if self._frame is not None and len(data) == len(self._frame) and not force:
                return
            self._frame = data
            self._frame_seq += 1

    # ---------- 执行 ----------

    def _gate(self):
        """每步执行前的闸门。返回 False 表示该收摊了。

        暂停期间不能干等：手一直在 `wait()` 里挂着，画面就冻在上一帧了。所以这里
        自己带抓帧轮询——用户暂停后看到的仍然是「此刻」的页面。
        """
        while True:
            with self._cond:
                if self._stopped:
                    return False
                if self._allow_one:
                    self._allow_one = False
                    self._stepped = True
                    return True
                if not self._paused:
                    return True
                # 单步走完一步回到这里时要重新挂上「已暂停」，否则界面一直显示
                # 「运行中」——用户以为它还在跑，其实已经停住了。
                if self._state['status'] == 'running':
                    self._state['status'] = 'paused'
            self._grab()
            time.sleep(0.06)

    def _dwell(self):
        """步间停留：期间持续出帧，否则「页面在加载」这一段用户只能看静态图。

        单步走完的那一步不留 —— 用户点「单步」的意思就是「走一步然后停」，
        再停 0.55 秒只会让他以为按钮没生效。
        中途按了暂停也立刻退出来，不把这个停留耗完。
        """
        if self._stepped:
            return
        deadline = time.monotonic() + self.delay
        while time.monotonic() < deadline:
            with self._cond:
                if self._paused or self._stopped:
                    return
            self._grab()
            time.sleep(0.06)

    def _run(self):
        try:
            self._run_playwright()
        except Exception as exc:  # noqa: BLE001 - 线程里不能把异常抛给任何人
            logger.exception('调试回放异常退出')
            with self._lock:
                if self._state['status'] not in ('stopped', 'failed'):
                    self._state['status'] = 'failed'
                    self._state['error'] = '回放异常退出：%s' % exc
        finally:
            self._grabber = None

    def _run_playwright(self):
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=self.headless,
                args=['--disable-blink-features=AutomationControlled'],
            )
            context = browser.new_context(
                ignore_https_errors=True,          # 内网系统多为自签名证书
                viewport=VIEWPORT,
                device_scale_factor=1,
            )
            page = context.new_page()
            self._grabber = lambda: page.screenshot(type='jpeg', quality=FRAME_QUALITY)
            self._set(status='running')
            timer = threading.Timer(MAX_RUNTIME, self._hard_stop)
            timer.daemon = True
            timer.start()
            try:
                self._walk(context, page)
            finally:
                timer.cancel()
                self._grab(force=True)
                try:
                    browser.close()
                except Exception:
                    pass

    def _hard_stop(self):
        logger.warning('调试回放超过 %s 秒，强制停止', MAX_RUNTIME)
        with self._cond:
            self._stopped = True
            self._paused = False
            self._allow_one = True
            self._cond.notify_all()

    def _walk(self, context, first_page):
        page = first_page
        probe = None
        i = 0
        total = len(self.steps)
        while i < total:
            step = self.steps[i]
            # 先把「当前步」指到即将执行的这一步再等闸门。反过来的话，暂停时高亮的是
            # 刚刚做完的那一步，看着像卡在上一行不动。
            with self._lock:
                self._state['index'] = i + 1
            if not self._gate():
                self._set(status='stopped')
                return
            with self._lock:
                self._state['status'] = 'running'
            self._mark(i, status='running')
            started = time.monotonic()

            upcoming = self.steps[i + 1] if i + 1 < total else None
            # 弹窗的处理器必须在上一步执行之前就交出去：等走到 dialog 那一步再注册，
            # 页面已经挂起了（和 replay 里同一条理由）。
            if upcoming and upcoming.get('type') == 'dialog' and probe is None:
                probe = replay._arm_dialog(page, upcoming, {'seen': [], 'no': upcoming.get('no')})

            try:
                page, chosen, poll = self._exec(context, page, step)
            except Exception as exc:  # noqa: BLE001 - 失败要留在画面上给人看
                replay._disarm_dialog(probe)
                probe = None
                ms = round((time.monotonic() - started) * 1000)
                self._mark(i, status='failed', ms=ms, error=str(exc)[:400])
                with self._lock:
                    self._state['status'] = 'failed'
                    self._state['error'] = '第 %s 步（%s）失败：%s' % (
                        step.get('no'), step.get('type'), exc)
                    self._state['failed_no'] = step.get('no')
                    # 失败自动暂停：画面停在出事那一刻，人才能看清到底发生了什么。
                    self._paused = True
                self._grab(force=True)
                return

            ms = round((time.monotonic() - started) * 1000)
            used = self._used_of(chosen)
            self._mark(i, status='ok', ms=ms, used=used)
            with self._lock:
                self._state['url'] = self._safe_url(page)
            self._grab(force=True)

            if step.get('type') == 'dialog' and probe:
                if not probe.get('seen'):
                    # 弹窗没来就是没来，别装作成功——多半是上一步没点中。
                    self._mark(i, status='failed', error='这一步应该有弹窗，但实际没有弹出来')
                    with self._lock:
                        self._state['status'] = 'failed'
                        self._state['error'] = '第 %s 步应该有弹窗，但实际没有弹出来' % step.get('no')
                        self._state['failed_no'] = step.get('no')
                        self._paused = True
                    return
                replay._disarm_dialog(probe)
                probe = None

            self._dwell()
            self._stepped = False
            i += 1

        with self._lock:
            self._state['status'] = 'done'
            self._state['index'] = total

    def _safe_url(self, page):
        try:
            return page.url
        except Exception:
            return None

    def _used_of(self, chosen):
        if not chosen:
            return None
        spec = chosen.get('spec') or {}
        syntax = spec['args'][0] if spec.get('method') == 'locator' and spec.get('args') else chosen.get('desc')
        matches = chosen.get('count')
        return {
            'kind': chosen.get('kind'),
            'desc': chosen.get('desc'),
            'syntax': syntax,
            'matches': matches,
            'nth': (chosen.get('index') + 1) if (matches or 0) > 1 and chosen.get('index') is not None else None,
        }

    def _exec(self, context, page, step):
        """执行一步，返回 (当前页, 用到的候选, 轮询信息)。

        和 `replay._run_step` 的差别只有一处：执行前把目标元素高亮一下，
        用户才知道这一步到底点在哪。其余逻辑刻意保持一致，免得「回放跑通的」
        和「生成脚本跑通的」不是同一件事。
        """
        kind = step.get('type')
        if kind == 'new_page':
            before = len(context.pages)
            deadline = time.monotonic() + 10
            while len(context.pages) <= before and time.monotonic() < deadline:
                time.sleep(0.05)
            page = context.pages[-1] if context.pages else page
            try:
                page.wait_for_load_state('domcontentloaded', timeout=15000)
            except Exception:
                pass
            return page, None, None

        target = step.get('target')
        loc = None
        if target:
            try:
                loc, _ = replay._locator(page, target, self.framework)
                loc.evaluate(_HL_ON, '#E24B4A')
                # 划完立刻出一帧：否则用户看到的永远是「操作之后」的页面，
                # 那个红框一闪而过，等于没画。
                self._grab(force=True)
            except Exception:
                # 高亮失败（元素还没出现、页面正在跳）不该影响这一步本身
                loc = None
        chosen, poll = replay._run_step(step, page, self.framework)
        if loc is not None:
            try:
                # 执行完再划一次：页面没跳的话，暂停时框还在，指得清是哪个元素。
                # 页面已经跳走时这里会失败，正常。
                loc.evaluate(_HL_ON, '#E24B4A')
            except Exception:
                pass
        # 新标签页可能在步骤执行中出现（点击打开了新窗口）
        if len(context.pages) > 1 and (step.get('page_index') or 0) < len(context.pages):
            page = context.pages[step.get('page_index') or 0]
        return page, chosen, poll


# ---------- 单例 ----------

_current = None
_current_lock = threading.Lock()


def current():
    return _current


def start(steps, framework, delay=None, headless=True):
    """起一次调试回放。上一个还活着就拒绝——两个浏览器同时在跑只会让人搞不清在看哪个。"""
    global _current
    with _current_lock:
        if _current is not None and _current.snapshot()['status'] in ('starting', 'running', 'paused'):
            raise DebugBusy('上一次调试回放还在跑，先停掉它')
        _current = DebugSession(steps, framework, delay=delay, headless=headless).start()
        return _current


def stop_current():
    global _current
    with _current_lock:
        session = _current
    if session is None:
        return None
    session.control('stop')
    return session
