"""录制期常驻的断言解析服务。

一次解析要 7 秒左右，但其中 5 秒多花在「这个进程的第一次模型调用」上（建 provider 连接、
opencode 侧初始化），不在推理本身。所以录制一开始就把 serve 拉起来、顺手预热一次：
用户点「解析」时只剩 1.5–3 秒。实测（volcengine-plan/deepseek-v4-flash）：
解析时才起进程 6.5–8.1s，录制时起 + 预热 1.4–2.0s，预热后空闲 90 秒再解析 3.3s。

生命周期严格跟着录制，且任何一步失败都不影响录制本身：
- 起：录制进入 recording 后异步起（只起线程，不阻塞录制线程）；
- 杀：停止录制（延迟 STOP_DELAY_SECONDS）、关浏览器、录制线程退出、后端进程退出；
- 用：就绪就复用这条进程，拿不到就退回一次性的 OpenCodeRunner.ask_text（慢但能用）。
"""
import atexit
import shutil
import tempfile
import threading
import time
from pathlib import Path

import requests

from app.services.opencode_runner import ASK_NO_TOOLS, OpenCodeRunner

# 一条消息最多等这么久。解析正常 2 秒出结果，等超过 1 分钟不如直接报错让用户重试。
ASK_TIMEOUT = 60
# 预热只要把连接和初始化跑热，问什么不重要，越短越省 token。
WARMUP_SYSTEM = '只输出 JSON。'
WARMUP_PROMPT = '输出 {"ok":true}，不要解释。'
# 停止录制后浏览器还开着（还能插断言、验证），留一段窗口再杀进程。
STOP_DELAY_SECONDS = 90

STATUS_LABELS = {
    'off': '未启用',
    'starting': '启动中',
    'warming': '预热中',
    'ready': '就绪',
    'failed': '不可用',
    'stopped': '已关闭',
}


def disabled_snapshot():
    return {'status': 'off', 'label': STATUS_LABELS['off'], 'detail': None, 'model': None}


class AskUnavailable(RuntimeError):
    """常驻解析不可用：调用方据此退回一次性的 ask_text。"""


class AskPool(object):
    """一个录制会话对应一个常驻 serve 进程。"""

    # 后端进程退出时（IDE 重启、Ctrl+C）兜底杀掉，避免留下孤儿进程。
    _instances = set()
    _instances_lock = threading.Lock()

    def __init__(self, app_config, model=None):
        self._config = dict(app_config or {})
        self._model = (model or '').strip() or None
        self._lock = threading.RLock()
        self._status = 'starting'
        self._detail = None
        self._process = None
        self._base_url = None
        self._workdir = None
        self._thread = None
        self._timer = None
        self._closing = False
        self._ready_at = None

    # ---- 生命周期 ----

    def start(self):
        with self._lock:
            if self._closing or self._thread is not None:
                return
            self._thread = threading.Thread(
                target=self._boot, name='ask-pool-%s' % id(self), daemon=True)
            self._thread.start()
        AskPool._track(self)

    def _boot(self):
        runner = OpenCodeRunner(self._config)
        process = None
        workdir = None
        try:
            config_path = runner._config_path()
            port = runner._free_port()
            base_url = 'http://%s:%d' % (runner._serve_host(), port)
            workdir = tempfile.mkdtemp(prefix='autocase-askd-')
            process = runner._start_serve(Path(workdir), config_path, port)
            with self._lock:
                if self._closing:
                    # 起进程这几百毫秒里用户可能已经停止录制了。
                    raise AskUnavailable('已取消')
                self._process, self._base_url, self._workdir = process, base_url, workdir
            runner._wait_serve_healthy(base_url, process, 60)
            self._set_status('warming')
            if self._model:
                try:
                    self._message(WARMUP_SYSTEM, WARMUP_PROMPT)
                except AskUnavailable as exc:
                    # 进程还在就只是「没热起来」：解析仍然走它，只是第一次可能慢些。
                    if process.poll() is not None:
                        raise
                    self._set_status('ready', '预热失败：%s' % str(exc)[:160])
                    self._ready_at = time.time()
                    return
            self._set_status('ready')
            self._ready_at = time.time()
        except Exception as exc:  # noqa: BLE001 - 失败只影响解析速度，绝不能冒到录制
            self._set_status('failed', str(exc)[:200])
            self._kill(process, workdir)
            with self._lock:
                self._process, self._base_url, self._workdir = None, None, None

    def stop(self, delay=0):
        """幂等。delay > 0 时延后杀，给「停止录制后补一条断言」留窗口。"""
        if delay and delay > 0:
            with self._lock:
                if self._closing or self._timer is not None:
                    return
                timer = threading.Timer(float(delay), self.stop)
                timer.daemon = True
                self._timer = timer
            timer.start()
            return
        with self._lock:
            if self._closing:
                return
            self._closing = True
            timer, self._timer = self._timer, None
            process, workdir = self._process, self._workdir
            self._process, self._base_url, self._workdir = None, None, None
            self._status = 'stopped'
            self._detail = None
        if timer is not None:
            timer.cancel()
        self._kill(process, workdir)
        AskPool._untrack(self)

    @staticmethod
    def _kill(process, workdir):
        try:
            OpenCodeRunner._terminate_process(process)
        except Exception:  # noqa: BLE001 - 收尾失败没有补救手段，也不该打断调用方
            pass
        try:
            # serve 的 stderr 是 PIPE，进程收掉后要把句柄关掉，否则会一直挂着。
            if process is not None and process.stderr is not None:
                process.stderr.close()
        except Exception:  # noqa: BLE001
            pass
        if workdir:
            shutil.rmtree(workdir, ignore_errors=True)

    @classmethod
    def _track(cls, pool):
        with cls._instances_lock:
            cls._instances.add(pool)

    @classmethod
    def _untrack(cls, pool):
        with cls._instances_lock:
            cls._instances.discard(pool)

    @classmethod
    def shutdown_all(cls):
        with cls._instances_lock:
            pools = list(cls._instances)
        for pool in pools:
            try:
                pool.stop()
            except Exception:  # noqa: BLE001
                pass

    # ---- 使用 ----

    def is_ready(self):
        with self._lock:
            return self._status == 'ready' and not self._closing

    def note_model(self, model):
        """录制页换了模型：进程不用重启（模型在请求体里），但新 provider 首次调用可能又冷，
        后台补一次预热。"""
        model = (model or '').strip()
        if not model:
            return
        with self._lock:
            if model == self._model:
                return
            self._model = model
            ready = self._status == 'ready' and not self._closing
        if ready:
            threading.Thread(target=self._prewarm, name='ask-prewarm', daemon=True).start()

    def _prewarm(self):
        try:
            self._message(WARMUP_SYSTEM, WARMUP_PROMPT)
        except Exception:  # noqa: BLE001 - 预热是尽力而为
            pass

    def ask(self, system, prompt, timeout=ASK_TIMEOUT):
        with self._lock:
            if self._closing:
                raise AskUnavailable('解析器已关闭')
            if self._status != 'ready':
                raise AskUnavailable(self._detail or '解析器未就绪')
        return self._message(system, prompt, timeout=timeout)

    def _message(self, system, prompt, timeout=ASK_TIMEOUT):
        with self._lock:
            model, base_url, process = self._model, self._base_url, self._process
        ref = OpenCodeRunner._split_model(model or '')
        if ref is None:
            raise AskUnavailable('没有选择模型')
        if not base_url or process is None or process.poll() is not None:
            raise AskUnavailable('解析器进程已退出')
        try:
            # 每次新建会话：复用会话会把历史对话带进去，解析结果会被前文牵着走。
            created = requests.post('%s/session' % base_url,
                                    json={'title': 'AutoCase ask'}, timeout=15)
            if created.status_code not in (200, 201):
                raise AskUnavailable('创建会话失败（%s）' % created.status_code)
            session_id = (created.json() or {}).get('id')
            if not session_id:
                raise AskUnavailable('创建会话没有返回 id')
            body = {
                'parts': [{'type': 'text', 'text': prompt}],
                'system': system,
                'tools': dict(ASK_NO_TOOLS),
                'format': {'type': 'text'},
                'model': {'providerID': ref['providerID'], 'modelID': ref['id']},
            }
            reply = requests.post('%s/session/%s/message' % (base_url, session_id),
                                  json=body, timeout=timeout)
            if reply.status_code != 200:
                raise AskUnavailable('模型调用失败（%s）：%s'
                                     % (reply.status_code, (reply.text or '')[:160]))
            payload = reply.json() or {}
            error = (payload.get('info') or {}).get('error')
            if error:
                raise AskUnavailable('模型返回错误：%s' % str(error)[:160])
            return ''.join(
                part.get('text', '') for part in payload.get('parts', [])
                if part.get('type') == 'text'
            ).strip()
        except requests.RequestException as exc:
            raise AskUnavailable('请求解析器失败：%s' % exc)

    # ---- 状态 ----

    def fail(self, detail):
        """解析失败后把池子判死：下一次解析直接走一次性调用，不再重复等它。"""
        with self._lock:
            if self._closing:
                return
            self._closing = True
            timer, self._timer = self._timer, None
            process, workdir = self._process, self._workdir
            self._process, self._base_url, self._workdir = None, None, None
            self._status = 'failed'
            self._detail = str(detail)[:200]
        if timer is not None:
            timer.cancel()
        self._kill(process, workdir)

    def _set_status(self, status, detail=None):
        with self._lock:
            if self._closing:
                return
            self._status = status
            self._detail = detail

    def snapshot(self):
        with self._lock:
            return {
                'status': self._status,
                'label': STATUS_LABELS.get(self._status, self._status),
                'detail': self._detail,
                'model': self._model,
            }


atexit.register(AskPool.shutdown_all)
