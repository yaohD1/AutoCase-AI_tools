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

from app.services.recording import healer, llm_healer, replay

logger = logging.getLogger(__name__)

# 两次抓帧的最小间隔（秒）。0.09 约等于 11 fps，实测截图一次 30~40ms，留了余量。
GRAB_INTERVAL = 0.09
# 步间停留（秒）。0.55 是「人眼能看清页面变了、又不至于等得烦」的实测值。
DEFAULT_DELAY = 0.55
# 单次回放的总时长上限：卡住时得有人收尸，否则线程和浏览器进程会一直挂着。
MAX_RUNTIME = 600
FRAME_QUALITY = 55
# 1920×1200 而不是 1920×1080：前端预览容器是 16:10，16:9 的帧按宽度铺开反而更矮。
# 这一版分辨率让画面在 1440 窗口下能显示到 1150×719（原来 1067×667），
# 放大 1.2 倍以内都不会糊。代价是被测页面在 1920 宽下可能命中不同的响应式断点。
VIEWPORT = {'width': 1920, 'height': 1200}
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
    if kind == 'close':
        return '关闭浏览器'
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

    def __init__(self, steps, framework, delay=None, headless=True, patcher=None, heal=True,
                 app_config=None):
        self.id = 'dbg-%s' % uuid.uuid4().hex[:10]
        self.steps = steps
        self.framework = framework
        self.delay = DEFAULT_DELAY if delay is None else max(0.0, float(delay))
        self.headless = headless
        # AI 自愈要按 AUTOMATION_WORKSPACE_ROOT 决定沙箱放哪，这里留个口子。
        self.app_config = app_config or {}
        # 写回录制步骤的通道。用回调而不是直接拿 RecordingSession：
        # 这个模块不该知道录制会话长什么样，测试里也好换成假的。
        self._patcher = patcher
        self._heal_enabled = bool(heal) and patcher is not None

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
        # 等待用户决定的修复建议。补丁单独放在这里：它带着完整候选（含 spec），
        # 每次状态轮询都编码一遍不值得。
        self._decision = None
        self._heal_patch = None

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
            'heal': None,
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
            if self._state['status'] in ('needs_heal', 'healing'):
                # 停在这里是在等一个决定，暂停/继续/单步都没有意义——放行只会让
                # 用户点了没反应，还以为界面坏了。停止仍然有效。
                if action != 'stop':
                    raise DebugError('这一步在等修复决定，先处理上面的建议')
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

    def decide_heal(self, decision, writeback=False):
        """用户对失败的处理：ai（让官方 healer 查）/ apply（应用并重试）/ skip / stop。

        决定只在这里记下，真正的应用和重试在回放线程里做——浏览器是那个线程的，
        跨线程碰 sync API 会直接把 Playwright 弄崩。
        """
        with self._cond:
            card = self._state.get('heal')
            if not card or self._state['status'] not in ('needs_heal', 'healing'):
                raise DebugError('现在没有等待处理的问题')
            if decision not in ('ai', 'apply', 'skip', 'stop'):
                raise DebugError('不认识的处理决定：%s' % decision)
            if decision == 'ai':
                if self._state['status'] == 'healing' or card.get('status') == 'investigating':
                    raise DebugError('AI 已经在调查了，等它出结果')
            if decision == 'apply' and not card.get('can_apply'):
                raise DebugError('这一处没有可应用的改动，只能跳过或停止')
            if decision == 'apply':
                # 立刻把卡片切成「正在重试」，否则用户点完到下一次轮询之间
                # 按钮看起来没反应，很容易连点两下。
                self._state['heal'] = dict(card, status='applying')
            elif decision == 'ai':
                self._state['heal'] = dict(card, status='queued', ai_ready=False)
            self._decision = {'decision': decision, 'writeback': bool(writeback)}
            self._cond.notify_all()
        self._push()
        return self.snapshot()

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
                self._state['heal'] = None
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

            current = step
            attempt = 0
            skipped = False
            chosen = poll = ident = None
            while True:
                # 弹窗的处理器必须在上一步执行之前就交出去：等走到 dialog 那一步再注册，
                # 页面已经挂起了（和 replay 里同一条理由）。重试时也要重新装。
                probe = self._arm_upcoming(page, i, probe)
                started = time.monotonic()
                try:
                    page, chosen, poll, ident = self._exec(context, page, current)
                    break
                except Exception as exc:  # noqa: BLE001 - 失败要留在画面上给人看
                    replay._disarm_dialog(probe)
                    probe = None
                    ms = round((time.monotonic() - started) * 1000)
                    self._mark(i, status='failed', ms=ms, error=str(exc)[:400])
                    self._grab(force=True)
                    verdict = self._handle_failure(i, page, current, exc, attempt)
                    if verdict == 'stop':
                        return
                    if verdict == 'next':
                        skipped = True
                        break
                    current = verdict['step']
                    attempt += 1
                    self._mark(i, status='running', error=None)

            if skipped:
                # 跳过不代表这一步没错，只是用户决定先往下看。状态给 skipped，
                # 别混进 ok——否则一整轮跑完看起来像全绿。
                self._mark(i, status='skipped')
                with self._lock:
                    self._state['status'] = 'running'
                    self._state['heal'] = None
                    # 跳过之后继续往下跑，顶部那条红字还挂着旧的失败原因会让人
                    # 以为整轮还在出错。
                    self._state['error'] = None
                self._stepped = False
                i += 1
                continue

            ms = round((time.monotonic() - started) * 1000)
            used = self._used_of(chosen)
            suspect = self._suspect_note(current, ident)
            self._mark(i, status='ok', ms=ms, used=used,
                       suspect=bool(suspect), suspect_reason=suspect)
            with self._lock:
                self._state['url'] = self._safe_url(page)
                self._state['heal'] = None
            self._grab(force=True)

            if step.get('type') == 'dialog' and probe:
                if not probe.get('seen'):
                    # 弹窗没来就是没来，别装作成功——多半是上一步没点中。
                    self._mark(i, status='failed', error='这一步应该有弹窗，但实际没有弹出来')
                    self._fail_hard('第 %s 步应该有弹窗，但实际没有弹出来' % step.get('no'),
                                    step.get('no'))
                    return
                replay._disarm_dialog(probe)
                probe = None

            self._dwell()
            self._stepped = False
            i += 1

        with self._lock:
            self._state['status'] = 'done'
            self._state['index'] = total

    def _arm_upcoming(self, page, i, probe):
        upcoming = self.steps[i + 1] if i + 1 < len(self.steps) else None
        if upcoming and upcoming.get('type') == 'dialog' and probe is None:
            return replay._arm_dialog(page, upcoming, {'seen': [], 'no': upcoming.get('no')})
        return probe

    def _fail_hard(self, message, no):
        """不再尝试修复，直接收摊。浏览器由 _run_playwright 的 finally 关掉。"""
        with self._lock:
            self._state['status'] = 'failed'
            self._state['error'] = message
            self._state['failed_no'] = no
            self._state['heal'] = None
            # 失败自动暂停：画面停在出事那一刻，人才能看清到底发生了什么。
            self._paused = True
        return 'stop'

    def _handle_failure(self, i, page, step, exc, attempt):
        """失败 → 停下来，把决定权交给人。

        **不自动诊断。** 失败后只给一个「AI 自愈」入口，用户点了才起官方 healer。
        理由：规则诊断要判断「元素还是不是录制时那个」，判不准的时候只能停；
        而官方 healer 虽然慢，但它会真的跑、真的改、真的重跑，结论更硬。
        让用户决定什么时候花这几分钟，比替他决定更合适。

        这个方法会一直阻塞，期间浏览器保持打开——现场丢了，后面什么都查不了。
        """
        no = step.get('no')
        message = '第 %s 步（%s）失败：%s' % (no, step.get('type'), exc)
        if not self._heal_enabled:
            return self._fail_hard(message, no)

        card = self._failure_card(step, message, attempt)
        self._show_heal(card, message, no)

        patch = None
        while True:
            decision, writeback = self._wait_decision()
            if decision == 'ai':
                card = self._ai_investigate(i, page, step, card)
                patch = self._heal_patch
                continue
            break

        with self._lock:
            self._state['heal'] = None
            self._state['status'] = 'running'
        self._push()
        if decision == 'stop':
            return self._fail_hard(message, no)
        if decision == 'skip':
            self._heal_patch = None
            return 'next'

        applied = False
        note = ''
        if writeback and card.get('writeback_ok') and patch:
            try:
                self._patcher(step.get('_session_no'), patch)
                applied = True
            except Exception as err:  # noqa: BLE001 - 写回失败不该挡住本次重试
                logger.warning('修复写回失败：%s', err)
                note = '写回失败：%s' % str(err)[:80]
        self._heal_patch = None
        self._mark(i, healed=True, heal_written=applied, heal_note=note or None,
                   heal_title=card.get('title'))
        with self._lock:
            self._state['error'] = None
        wait_ms = int((patch or {}).get('wait_ms') or 0)
        if wait_ms > 0:
            self._sleep_with_frames(wait_ms)
        return {'step': healer.apply_patch(step, patch or {})}

    def _failure_card(self, step, message, attempt):
        """失败后的第一张卡片：只有事实，没有猜测。"""
        return {
            'no': step.get('no'),
            'type': step.get('type'),
            'alias': (step.get('target') or {}).get('alias'),
            'status': 'idle',
            'title': '第 %s 步失败' % (step.get('no') or ''),
            'category': 'failed',
            'reason': message,
            'probes': [],
            'changes': [],
            'can_apply': False,
            'writeback_ok': False,
            'writeback_default': False,
            'ai_ready': True,
            'attempt': attempt + 1,
            'max_attempt': 1,
        }

    def _show_heal(self, card, message, no):
        with self._lock:
            self._state['status'] = 'needs_heal'
            self._state['error'] = message
            self._state['failed_no'] = no
            self._state['heal'] = card
        self._push()
        self._grab(force=True)

    def _ai_investigate(self, i, page, step, base_card):
        """点「AI 自愈」之后这一段：投影 → 官方 healer → 回填结果。

        整段是阻塞的，但走的是回放线程（浏览器只归它）。等待期间持续出帧，
        否则画面会冻在失败那一刻，用户以为整个页面死了。
        """
        with self._lock:
            self._state['status'] = 'healing'
            self._state['heal'] = dict(
                base_card, status='investigating', title='AI 正在调查',
                reason='正在把失败现场投影成 Playwright 用例，交给官方 healer 跑一遍。',
                ai_ready=False)
        self._push()
        self._grab(force=True)

        try:
            card = llm_healer.investigate(
                page, self.steps[:i + 1], app_config=self.app_config,
                title='AutoCase 回放 · 第 %s 步' % (step.get('no') or ''),
                on_tick=self._grab)
        except Exception as exc:  # noqa: BLE001 - 自愈挂了不能把整次回放带走
            logger.exception('AI 自愈失败')
            text = str(exc)[:300] or exc.__class__.__name__
            card = dict(base_card, status='blocked', category='ai_heal',
                        title='AI 自愈', reason=text, ai_ready=True,
                        blocked_reason='AI 没能给出可用的修复，可以重试或跳过这一步。',
                        probes=['调查失败：%s' % text[:160]])

        card.setdefault('no', step.get('no'))
        card.setdefault('alias', (step.get('target') or {}).get('alias'))
        card['ai_ready'] = True
        # 补丁里带着完整 spec，不该跟着 260ms 的状态轮询反复发给前端。
        patch = card.pop('patch', None)
        if patch is not None:
            card['writeback_ok'] = bool(step.get('_session_no'))
            if not card['writeback_ok']:
                card['writeback_default'] = False
                card['writeback_note'] = '这一步是动作里的子步骤，改动只对本次回放生效。'
        self._heal_patch = patch
        with self._lock:
            self._state['heal'] = card
            self._state['status'] = 'needs_heal'
        self._push()
        self._grab(force=True)
        return card

    def _sleep_with_frames(self, ms):
        """等待期间继续出帧，否则画面上是「点了应用之后一动不动」，像卡死。"""
        deadline = time.monotonic() + ms / 1000.0
        while time.monotonic() < deadline:
            if self._stopped:
                return
            self._grab()
            time.sleep(0.08)


    def _wait_decision(self):
        """阻塞到用户做出决定。期间持续出帧——不然画面会冻住，看起来像卡死。"""
        while True:
            with self._cond:
                if self._stopped:
                    return 'stop', False
                if self._decision is not None:
                    data = self._decision
                    self._decision = None
                    return data['decision'], data['writeback']
            self._grab()
            time.sleep(0.06)

    def _safe_url(self, page):
        try:
            return page.url
        except Exception:
            return None

    def _suspect_note(self, step, ident):
        """动作执行前后对不上的元素：不判失败，但也不能装作没事。

        「结构定位漂移、点到了刷新按钮」这种不会抛错——Playwright 点成功了，
        步骤也是绿的。这里拿录制快照和实际元素的身份特征比一次，冲突就标黄，
        让人知道这一步虽然过了，但过的不是原来那个元素。
        """
        if not ident or not step.get('target'):
            return None
        try:
            cmp = healer.compare_identity(step['target'].get('snap') or {}, ident)
        except Exception:
            return None
        if cmp.get('verdict') != 'conflict':
            return None
        return cmp.get('reason') or '实际操作的元素的身份与录制时不同。'

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
        """执行一步，返回 (当前页, 用到的候选, 轮询信息, 执行前的元素身份)。

        和 `replay._run_step` 的差别只有一处：执行前把目标元素高亮一下，
        用户才知道这一步到底点在哪。其余逻辑刻意保持一致，免得「回放跑通的」
        和「生成脚本跑通的」不是同一件事。

        身份在**动作之前**读：点完按钮可能把自己带走，事后再读就什么都没了。
        回放结束后拿它和录制快照比对，能抓出「定位没报错、但点到了别人」。
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
            return page, None, None, None

        target = step.get('target')
        loc = None
        ident = None
        if target:
            try:
                loc, _ = replay._locator(page, target, self.framework)
                # 高亮只等 0.8 秒：locator.evaluate 默认会等满 30 秒（它也要等元素
                # 出现），元素没出来时等于在动作之前先白等一轮，失败总耗时直接翻倍。
                loc.evaluate(_HL_ON, '#E24B4A', timeout=HIGHLIGHT_MS)
                # 划完立刻出一帧：否则用户看到的永远是「操作之后」的页面，
                # 那个红框一闪而过，等于没画。
                self._grab(force=True)
            except Exception:
                # 高亮失败（元素还没出现、页面正在跳）不该影响这一步本身
                loc = None
            if loc is not None:
                try:
                    ident = healer.read_identity(loc)
                except Exception:
                    ident = None
        chosen, poll = replay._run_step(step, page, self.framework)
        if loc is not None:
            try:
                # 执行完再划一次：页面没跳的话，暂停时框还在，指得清是哪个元素。
                # 页面已经跳走时这里会失败——同样只等 0.8 秒，别让「点击后跳转」
                # 这种最常见的情况每步都多挂 30 秒。
                loc.evaluate(_HL_ON, '#E24B4A', timeout=HIGHLIGHT_MS)
            except Exception:
                pass
        # 新标签页可能在步骤执行中出现（点击打开了新窗口）
        if len(context.pages) > 1 and (step.get('page_index') or 0) < len(context.pages):
            page = context.pages[step.get('page_index') or 0]
        return page, chosen, poll, ident


# ---------- 单例 ----------

_current = None
_current_lock = threading.Lock()


def current():
    return _current


def start(steps, framework, delay=None, headless=True, patcher=None, heal=True,
          app_config=None):
    """起一次调试回放。上一个还活着就拒绝——两个浏览器同时在跑只会让人搞不清在看哪个。

    `patcher(no, patch)` 是写回录制步骤的通道；不传就退化成「失败即停」。
    `app_config` 决定 AI 自愈的沙箱位置（跟 AUTOMATION_WORKSPACE_ROOT 走）。
    """
    global _current
    with _current_lock:
        if _current is not None and _current.snapshot()['status'] in (
                'starting', 'running', 'paused', 'healing', 'needs_heal'):
            raise DebugBusy('上一次调试回放还在跑，先停掉它')
        _current = DebugSession(steps, framework, delay=delay, headless=headless,
                                patcher=patcher, heal=heal,
                                app_config=app_config).start()
        return _current


def stop_current():
    global _current
    with _current_lock:
        session = _current
    if session is None:
        return None
    session.control('stop')
    return session
