"""一条录制生成会话：一个 opencode session + 一个 serve，从规划走到写入和修复。

## 为什么是一个 serve

opencode 的 agent 权限在 **serve 启动时**加载。上一版要求写入轮「只允许写 target_path
一个文件」，而那个路径要到任务书轮结束才确定，只能换一份 config 重启 serve——也就是
「一个 session、两个 serve」。

现在写权限放宽到**整个工作区**（只排除 `.git` / `.opencode` / `node_modules`），规划轮
一开始就能用最终那份配置起 serve：规划用只读的 `recording-planner`，写入和修复用
`recording-emitter`，同一个进程、同一个 session，中途不重启。

## 越界怎么办

不靠权限硬拦，靠事后清单：`emit.generate` 用 `guard` 比对生成前后的工作区，目标文件
以外的改动进返回值的 `outside`，同时把它们的原始内容导出到 `.autocase/backups/<id>/`，
用户在界面上逐个还原。**不自动还原**——模型确实可能需要写辅助文件（helper / fixture），
自动删掉会把有用的东西一起删，而且可能误伤用户在生成期间自己的编辑。

bash / task 仍然是 deny：命令执行造成的影响没法用文件快照还原，而且实测放行
`npx playwright test` 也没用——模型看输出必然加 `2>&1 | tail`，管道会被整条拒掉。

## 降级

| 情况 | 行为 |
|---|---|
| serve 还在 | 正常，同一进程走完三轮（规划 / 写入 / 修复） |
| serve 提前退出 | `reused=False`，调用方退回一次性生成（`emit_recording`） |

## 生命周期

生成结束就关（成功或失败），另外有 1 小时空闲 TTL、4 小时最大存活、8 轮上限兜底。
不常驻、不跨录制复用——key 是 `RecordingSession.id`。
"""

import atexit
import json
import logging
import threading
import time
from pathlib import Path

import requests

from app.services.opencode_runner import OpenCodeRunner, PartialResult

logger = logging.getLogger(__name__)

PLANNER = 'recording-planner'
EMITTER = 'recording-emitter'

PLAN_TIMEOUT = 900
WRITE_TIMEOUT = 900
REPAIR_TIMEOUT = 300
HEALTH_TIMEOUT = 60
REQUEST_TIMEOUT = 60

IDLE_TTL = 3600
MAX_AGE = 4 * 3600
MAX_TURNS = 8
SWEEP_SECONDS = 60


class SessionLost(RuntimeError):
    """会话或 serve 不在了：调用方应退回一次性生成。"""


class Cancelled(RuntimeError):
    """用户主动取消。"""


def _split_model(model):
    ref = OpenCodeRunner._split_model(model)
    if ref is None:
        raise ValueError('模型格式应为 providerID/modelID')
    return ref


def _session_model(model):
    """建会话用的形状：{id, providerID}。和 prompt 的 {providerID, modelID} 不一样。"""
    ref = _split_model(model)
    return {'id': ref['id'], 'providerID': ref['providerID']}


def _prompt_model(model):
    ref = _split_model(model)
    return {'providerID': ref['providerID'], 'modelID': ref['id']}


class _Serve(object):
    """一个 opencode serve 进程 + 它用的配置。"""

    __slots__ = ('process', 'base_url', 'port', 'config', 'drop')

    def __init__(self, process, base_url, port, config, drop):
        self.process = process
        self.base_url = base_url
        self.port = port
        self.config = config
        self.drop = drop

    def alive(self):
        return self.process is not None and self.process.poll() is None

    def abort(self, session_id):
        if not session_id:
            return
        try:
            requests.post('%s/session/%s/abort' % (self.base_url, session_id), timeout=3)
        except requests.RequestException:
            pass

    def close(self):
        try:
            OpenCodeRunner._terminate_process(self.process, expected_binary=None)
        except Exception:  # noqa: BLE001 - 收尾失败没有补救手段
            pass
        try:
            if self.drop:
                self.drop()
        except Exception:  # noqa: BLE001
            pass
        self.process = None


class EmitSession(object):
    def __init__(self, key, workspace, framework, model, runner):
        self.key = str(key)
        self.workspace = str(workspace)
        self.framework = framework
        self.model = model
        self.runner = runner
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._planner = None
        self._active = None
        self._session_id = None
        self._turns = []
        self._created_at = time.time()
        self._last_used = time.time()
        self._phase = 'new'
        self._scope = 'workspace'
        self._write_target = None

    # ---- 对外 ----

    def plan_turn(self, prompt, timeout=PLAN_TIMEOUT, revise=False):
        with self._lock:
            self._ensure_planner()
            return self._send(self._active, prompt, PLANNER, timeout,
                              'replan' if revise else 'plan')

    def begin_write(self, target_path):
        """写轮直接用规划期那个 serve。返回 {reused, scope}。

        权限一开始就是宽范围，不需要为了收窄再起第二个进程——这正是「一个 session
        一个 serve」成立的原因。serve 没了就只能退回一次性生成，没有备用的第二个可退。
        """
        with self._lock:
            if not self._session_id or self._active is None or not self._active.alive():
                return {'reused': False, 'scope': 'exact'}
            self._phase = 'writing'
            self._write_target = str(target_path)
            logger.info('写入轮复用规划期 serve（可写整个工作区），会话 %s', self._session_id)
            return {'reused': True, 'scope': 'workspace'}

    def emitter_turn(self, prompt, timeout=WRITE_TIMEOUT):
        with self._lock:
            if self._active is None or not self._session_id:
                raise SessionLost('会话已结束')
            return self._send(self._active, prompt, EMITTER, timeout, 'write')

    def repair_turn(self, prompt, timeout=REPAIR_TIMEOUT):
        with self._lock:
            if self._active is None or not self._session_id:
                raise SessionLost('会话已结束')
            return self._send(self._active, prompt, EMITTER, timeout, 'repair')

    def cancel(self, reason='cancelled'):
        self._cancel.set()
        with self._lock:
            seen = set()
            for serve in (self._active, self._planner):
                if serve is not None and id(serve) not in seen:
                    seen.add(id(serve))
                    serve.abort(self._session_id)
            self._close_all(reason)

    def close(self, reason='done'):
        with self._lock:
            self._close_all(reason)

    def snapshot(self):
        with self._lock:
            return {
                'reused': bool(self._session_id),
                'scope': self._scope,
                'phase': self._phase,
                'turns': len(self._turns),
                'expires_at': self._created_at + MAX_AGE,
                'idle_left': max(0, int(self._last_used + IDLE_TTL - time.time())),
            }

    def expired(self):
        now = time.time()
        if now - self._created_at > MAX_AGE:
            return 'max_age'
        if now - self._last_used > IDLE_TTL:
            return 'idle'
        return None

    # ---- 内部 ----

    def _ensure_planner(self):
        if self._planner is not None and self._planner.alive() and self._session_id:
            return
        self._close_all('restart')
        self._phase = 'planning'
        # 一开始就用最终那份宽配置起 serve：规划轮走只读的 recording-planner，
        # 写入轮换 recording-emitter 即可，不用为了改权限重启进程。
        config, drop = self.runner._emit_config(None, self.workspace, scope='workspace')
        serve = self._start_serve(config, drop)
        body = {'title': 'AutoCase recording', 'agent': PLANNER,
                'model': _session_model(self.model)}
        try:
            created = requests.post(serve.base_url + '/session', json=body, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            serve.close()
            raise SessionLost('创建 opencode 会话失败：%s' % exc)
        if created.status_code not in (200, 201):
            serve.close()
            raise SessionLost('创建 opencode 会话失败（%s）：%s'
                              % (created.status_code, (created.text or '')[:200]))
        session_id = (created.json() or {}).get('id')
        if not session_id:
            serve.close()
            raise SessionLost('opencode 没有返回会话 id')
        self._planner = serve
        self._active = serve
        self._session_id = session_id
        self._scope = 'workspace'
        logger.info('录制生成会话已建立：%s（workspace=%s）', session_id, self.workspace)

    def _start_serve(self, config, drop):
        port = self.runner._free_port()
        process = self.runner._start_serve(Path(self.workspace), config, port)
        base_url = 'http://%s:%d' % (self.runner._serve_host(), port)
        try:
            self.runner._wait_serve_healthy(base_url, process, HEALTH_TIMEOUT)
        except Exception:
            OpenCodeRunner._terminate_process(process)
            if drop:
                drop()
            raise
        return _Serve(process, base_url, port, config, drop)

    def _send(self, serve, prompt, agent, timeout, kind):
        if serve is None or not serve.alive():
            raise SessionLost('opencode serve 已退出')
        if self._cancel.is_set():
            raise Cancelled('已取消')
        if len(self._turns) >= MAX_TURNS:
            raise SessionLost('本次生成轮数超过上限（%d）' % MAX_TURNS)
        body = {'parts': [{'type': 'text', 'text': prompt}],
                'agent': agent, 'model': _prompt_model(self.model)}
        try:
            sent = requests.post('%s/session/%s/prompt_async' % (serve.base_url, self._session_id),
                                 json=body, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            raise SessionLost('派发失败：%s' % exc)
        if sent.status_code not in (200, 202, 204):
            raise SessionLost('派发失败（%s）：%s' % (sent.status_code, (sent.text or '')[:200]))
        started = time.monotonic()
        self._wait_turn(serve, timeout)
        text = self._last_assistant_text(serve)
        self._turns.append({'agent': agent, 'kind': kind,
                            'seconds': round(time.monotonic() - started, 1),
                            'chars': len(text)})
        self._last_used = time.time()
        if not text:
            raise SessionLost('opencode 这一轮没有返回内容')
        return text

    def _wait_turn(self, serve, timeout):
        deadline = time.monotonic() + timeout
        busy_deadline = time.monotonic() + 60
        seen_busy = False
        while True:
            if self._cancel.is_set():
                serve.abort(self._session_id)
                raise Cancelled('已取消')
            if not serve.alive():
                raise SessionLost('opencode serve 提前退出（%s）' % serve.process.returncode)
            try:
                status = requests.get(serve.base_url + '/session/status', timeout=10).json() or {}
            except (requests.RequestException, ValueError):
                status = {}
            if self._session_id in status:
                seen_busy = True
            elif seen_busy:
                return True
            elif time.monotonic() > busy_deadline:
                raise SessionLost('派发后 60 秒都没有开始处理')
            if time.monotonic() > deadline:
                partial = {'text': self._last_assistant_text(serve) or '',
                           'reason': '超过 %d 秒仍未结束' % timeout,
                           'turns': len(self._turns), 'reasoning_chars': 0}
                raise PartialResult('超过 %d 秒仍未结束' % timeout, partial)
            time.sleep(1)

    def _messages(self, serve):
        try:
            response = requests.get('%s/session/%s/message' % (serve.base_url, self._session_id),
                                    timeout=REQUEST_TIMEOUT)
        except requests.RequestException:
            return None
        if response.status_code != 200:
            return None
        try:
            return response.json() or []
        except ValueError:
            return None

    def _last_assistant_text(self, serve):
        items = self._messages(serve)
        for message in reversed(items or []):
            if (message.get('info') or {}).get('role') != 'assistant':
                continue
            texts = [part.get('text') or '' for part in (message.get('parts') or [])
                     if part.get('type') == 'text']
            joined = '\n'.join(text for text in texts if text).strip()
            if joined:
                return joined
        return ''

    def _close_all(self, reason):
        seen = set()
        for serve in (self._active, self._planner):
            if serve is not None and id(serve) not in seen:
                seen.add(id(serve))
                serve.close()
        self._active = None
        self._planner = None
        self._phase = 'done' if reason == 'done' else 'dead'
        logger.info('录制生成会话关闭（%s）：%s', reason, self._session_id)
        self._session_id = None


class SessionRegistry(object):
    """按录制会话 id 保存 EmitSession。只建对象不起进程（单测友好）。"""

    def __init__(self):
        self._items = {}
        self._lock = threading.Lock()
        self._sweeper = None

    def acquire(self, key, workspace, framework, model, runner):
        if not key:
            return None
        with self._lock:
            item = self._items.get(str(key))
            if item is None:
                item = EmitSession(key, workspace, framework, model, runner)
                self._items[str(key)] = item
            elif item.expired():
                item.close('expired')
                item = EmitSession(key, workspace, framework, model, runner)
                self._items[str(key)] = item
            self._ensure_sweeper()
            return item

    def get(self, key):
        if not key:
            return None
        with self._lock:
            return self._items.get(str(key))

    def close(self, key, reason='done'):
        if not key:
            return
        with self._lock:
            item = self._items.pop(str(key), None)
        if item is not None:
            item.close(reason)

    def cancel(self, key):
        with self._lock:
            item = self._items.get(str(key))
        if item is None:
            return False
        item.cancel('cancelled')
        return True

    def sweep(self):
        with self._lock:
            expired = [(key, item) for key, item in self._items.items() if item.expired()]
        for key, item in expired:
            logger.info('回收过期生成会话：%s', key)
            item.close('expired')
            with self._lock:
                self._items.pop(key, None)

    def snapshot(self, key):
        item = self.get(key)
        return item.snapshot() if item is not None else None

    def _ensure_sweeper(self):
        if self._sweeper is not None:
            return
        thread = threading.Thread(target=self._sweep_loop, name='ac-emit-sweeper', daemon=True)
        thread.start()
        self._sweeper = thread

    def _sweep_loop(self):
        while True:
            time.sleep(SWEEP_SECONDS)
            try:
                self.sweep()
            except Exception:  # noqa: BLE001 - 后台线程不能把进程带崩
                logger.warning('生成会话回收异常', exc_info=True)

    def close_all(self):
        with self._lock:
            items = list(self._items.items())
            self._items.clear()
        for _, item in items:
            try:
                item.close('shutdown')
            except Exception:  # noqa: BLE001
                pass


REGISTRY = SessionRegistry()
atexit.register(REGISTRY.close_all)
