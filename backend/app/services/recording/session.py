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

from playwright.sync_api import sync_playwright

from app.services.recording import ir
from app.services.recording.locators import build_specs, rank, stale_check, status_of

HERE = os.path.dirname(os.path.abspath(__file__))
RECORDER_JS = os.path.join(HERE, 'recorder.js')
IDLE_SECONDS = 15 * 60
PUMP_MS = 150
COMMAND_TIMEOUT = 60
PICK_OFF_TAGS = ('select', 'label')
NON_TEXT_INPUT_TYPES = ('button', 'submit', 'reset', 'checkbox', 'radio')


class RecordingError(Exception):
    pass


class RecordingBusy(RecordingError):
    """已有一个正在进行的录制。"""


class RecordingSession(object):
    _current = None
    _current_lock = threading.Lock()

    def __init__(self, base_url, headless=False, driver=None):
        self.id = uuid.uuid4().hex
        self.base_url = base_url
        self.status = 'starting'
        self.error = None
        self._headless = headless
        # 仅供测试：在录制线程内、进入循环前调用 driver(session, page) 模拟用户操作。
        self._driver = driver
        self._lock = threading.RLock()
        self._ready = threading.Event()
        self._commands = queue.Queue()
        # Playwright 回调只入队，真正的同步调用在录制线程的循环里执行（在回调内调用会死锁）。
        self._inbox = queue.Queue()
        self._errors = []
        self._events = []
        self._steps = []
        self._picks = []
        self._wired = set()
        self._last_url = {}
        self._pick = False
        self._opened = False
        self._last_activity = time.time()
        self._browser = None
        self._context = None
        self._thread = threading.Thread(target=self._run, name='recording-%s' % self.id[:8], daemon=True)

    # ---- 会话管理（Flask 线程调用）----

    @classmethod
    def start_new(cls, base_url, headless=False, driver=None):
        with cls._current_lock:
            current = cls._current
            if current is not None and current.status in ('starting', 'recording'):
                raise RecordingBusy('已有正在进行的录制，请先停止或取消')
            session = cls(base_url, headless=headless, driver=driver)
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

    def set_pick(self, on):
        return self._call('set_pick', on=bool(on))

    def stop(self):
        return self._call('stop')

    def cancel(self):
        return self._call('cancel')

    def _call(self, name, **kwargs):
        # 先看状态而不是线程：停止后线程还要一会儿才退出，命令放进队列也没人取，会白等超时。
        if self.status not in ('starting', 'recording') or not self._thread.is_alive():
            raise RecordingError('录制已结束')
        reply = queue.Queue(maxsize=1)
        self._commands.put((name, kwargs, reply))
        ok, value = reply.get(timeout=COMMAND_TIMEOUT)
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
                'error': self.error,
                'steps': steps,
                'picks': picks,
                'aliases': self.aliases(),
            }

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
        return self.snapshot()

    def add_intent_step(self, intent, text=None, after_no=None):
        """插入断言 / 等待 / 睡眠。intent 必须已由 intent.validate 校验过。

        text 保存用户原文；after_no 为空则追加到末尾，否则插入到该步骤之后。
        """
        step_type = ir.step_type_for_intent(intent['type'])
        with self._lock:
            target = None
            if intent.get('target'):
                target = self._find_target(intent['target'])
                if target is None:
                    raise RecordingError('没有名为 %r 的元素' % intent['target'])
            step = ir.make_step(step_type, no=0, target=target, value=intent.get('value'), source='manual')
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
        return self.snapshot()

    def _find_target(self, alias):
        for s in reversed(self._steps):
            if s.get('target') and s['target'].get('alias') == alias:
                return s['target']
        for p in self._picks:
            if p['target'].get('alias') == alias:
                return p['target']
        return None

    def _renumber(self):
        for i, s in enumerate(self._steps, 1):
            s['no'] = i

    # ---- 线程主循环（只在录制线程内执行 Playwright 调用）----

    def _run(self):
        try:
            with sync_playwright() as p:
                self._browser = p.chromium.launch(headless=self._headless)
                self._context = self._browser.new_context()
                self._context.add_init_script(path=RECORDER_JS)
                self._context.on('page', self._on_new_page)
                page = self._context.new_page()
                self._wire(page)
                self._opened = True
                page.goto(self.base_url, wait_until='domcontentloaded')
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
            if time.time() - self._last_activity > IDLE_SECONDS:
                self._finish('idle_timeout', revalidate=False)
                return
            try:
                self._pump()
            except Exception as exc:
                self.error = '浏览器已关闭：%s' % exc
                self._finish('closed', revalidate=False)
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
                    result = self._finish('stopped', revalidate=True)
                elif name == 'cancel':
                    result = self._finish('cancelled', revalidate=False)
                elif name == 'set_pick':
                    self._set_pick(kwargs['on'])
                    result = {'pick': self._pick}
                else:
                    raise RecordingError('未知命令：%s' % name)
                reply.put((True, result))
            except Exception as exc:
                reply.put((False, str(exc)))
            if name in ('stop', 'cancel'):
                return True

    def _finish(self, status, revalidate):
        self._process_inbox()
        with self._lock:
            self.status = 'stopping'
        if revalidate:
            self._revalidate_all()
        try:
            if self._browser is not None:
                self._browser.close()
        except Exception:
            pass
        with self._lock:
            self.status = status
        return self.snapshot()

    def _touch(self):
        self._last_activity = time.time()

    def _set_pick(self, on):
        self._pick = on
        for page in list(self._context.pages):
            self._apply_pick(page)

    def _apply_pick(self, page):
        try:
            page.evaluate('window.__pwSetPick(%s)' % ('true' if self._pick else 'false'))
        except Exception:
            pass

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
                    self._last_url[id(page)] = url

    def _wire(self, page):
        if id(page) in self._wired:
            return
        self._wired.add(id(page))
        try:
            page.expose_binding('__pwEmit', self._on_event)
        except Exception:
            pass
        page.on('framenavigated', lambda frame, pg=page: self._on_navigated(pg, frame))
        if self._pick:
            self._apply_pick(page)

    def _on_navigated(self, page, frame):
        self._inbox.put(lambda: self._handle_navigated(page, frame))

    def _handle_navigated(self, page, frame):
        if frame.parent_frame is not None:
            return
        url = frame.url
        if not url or url.startswith('about:'):
            return
        if self._last_url.get(id(page)) == url:
            return
        self._last_url[id(page)] = url
        with self._lock:
            prev = self._steps[-1] if self._steps else None
            # 点击触发的跳转不单独记为 goto；新标签页的第一次跳转只补全 url。
            if prev and prev['type'] == 'new_page' and prev.get('url') is None:
                prev['url'] = url
                return
            if prev and prev['type'] == 'click':
                return
            self._steps.append(ir.make_step('goto', no=0, url=url, page_index=self._page_index(page)))
            self._renumber()

    def _page_index(self, page):
        try:
            return self._context.pages.index(page)
        except ValueError:
            return 0

    def _count(self, frame, specs):
        out = []
        for spec in specs:
            try:
                n = getattr(frame, spec['method'])(*spec['args'], **spec['kwargs']).count()
            except Exception:
                n = -1
            out.append({'kind': spec['kind'], 'desc': spec['desc'], 'count': n, 'spec': spec})
        return out

    def _on_event(self, source, raw):
        # binding 回调：只入队。
        self._inbox.put(lambda: self._handle_event(source, raw))

    def _handle_event(self, source, raw):
        data = json.loads(raw)
        snap = data['snap']
        kind = data['type']
        frame = source['frame']
        page = source['page']
        self._touch()

        if kind == 'click' and snap['tag'] == 'input' and snap.get('type') not in NON_TEXT_INPUT_TYPES:
            return
        if kind == 'click' and snap['tag'] in PICK_OFF_TAGS:
            return

        # 记录元素所在的 iframe（主框架为 None），回放时据此找到同一个框架。
        snap = dict(snap, frame_url=frame.url if frame.parent_frame is not None else None)
        counted = self._count(frame, build_specs(snap))
        primary, ordered = rank(counted)
        status = status_of(primary)
        target = ir.make_target(snap, ordered, primary, status)
        index = self._page_index(page)

        with self._lock:
            seq = len(self._events)
            self._events.append({'seq': seq, 'type': kind, 'status': status,
                                 'desc': primary['desc'] if primary else None})

            if kind == 'pick':
                self._picks.append({'seq': seq, 'target': target, '_frame': frame})
                return

            key = (snap.get('css'), id(frame))
            prev = self._steps[-1] if self._steps else None
            if kind == 'fill' and prev and prev['type'] == 'fill' and prev.get('_key') == key:
                prev['value'] = data.get('value')
                prev['target'] = target
                prev['_frame'] = frame
                return

            step = ir.make_step(kind, no=0, target=target, value=data.get('value'),
                                label=data.get('label'), page_index=index)
            step['_key'] = key
            step['_frame'] = frame
            self._steps.append(step)
            self._renumber()

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
            counted = self._count(frame, build_specs(target['snap']))
            primary, ordered = rank(counted)
            with self._lock:
                target['candidates'] = ordered
                target['primary'] = primary
                target['status'] = stale_check(previous, primary)
