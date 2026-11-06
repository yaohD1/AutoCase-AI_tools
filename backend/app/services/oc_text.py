"""用 opencode 做「纯文本 / 图片」调用：不开工具、不写文件、不读项目。

首页生成用例、模块拆分、自动化脚本动作生成都走这里，和录制、脚本生成共用同一份
`opencode.models.json`——一套 provider、一套 key、一套超时。

为什么不再直连 provider：直连要自己拼 `/chat/completions`、自己处理各家
请求体差异，而模型配置已经归 opencode 管了，两套并存的结果就是「设置页改了模型，
生成还在用旧地址」。

关于常驻进程：一次调用约 9 秒，其中 5–7 秒是「这个 serve 的第一次模型调用」。
所以进程常驻、跨请求复用；空闲超过 IDLE_TTL 才回收。

关于图片：opencode 会先看模型有没有声明图片输入，没声明直接回
「当前模型不支持图像输入」。声明在 `opencode.models.json` 里由 models_config
按 `autocase.models[].vision` 自动补，见 `_sync_modalities`。
"""
import atexit
import base64
import json
import shutil
import tempfile
import threading
import time
from pathlib import Path

import requests

from app.services.opencode_runner import ASK_NO_TOOLS, OpenCodeRunner

# 一条消息最多等这么久。10 条用例实测 10–40 秒，给足余量但不至于挂死。
ASK_TIMEOUT = 300
# 空闲多久回收常驻进程。太短会反复付启动成本，太长会在没人用时白占内存。
IDLE_TTL = 1800
# 图片先压到这个尺寸内再发：原图动辄 3–8 MB，base64 之后更容易顶到请求上限。
MAX_IMAGE_EDGE = 2048


class TextError(RuntimeError):
    """模型调用失败。消息直接面向用户，调用方转成 500 的 error 字段即可。"""


def parse_json(text):
    """从模型回复里取出 JSON。

    纯文本输出比 json_schema 快 3–4 倍（实测 9s vs 44s），代价是可能带 ```json
    围栏、前后说明、甚至尾逗号。这里按「先剥围栏、再找第一个括号、最后配平」的顺序取，
    失败时把原文片段带进异常，否则用户只能看到一个语焉不详的 JSONDecodeError。
    """
    raw = (text or '').strip()
    if not raw:
        raise TextError('模型没有返回内容')
    if raw.startswith('```'):
        # ```json\n...\n```：去掉首行围栏和结尾那行。
        lines = raw.splitlines()
        if lines and lines[0].startswith('```'):
            lines = lines[1:]
        while lines and lines[-1].strip().startswith('```'):
            lines.pop()
        raw = '\n'.join(lines).strip()

    # 先找最靠前的那个括号：模型有时候先说一句「结果如下」，两个括号谁在前谁才是
    # 真正的起点。前一个解析不出来再试另一个（例如正文先提到 `[必填]` 这类字面量）。
    starts = sorted((raw.find(opener), opener, closer)
                    for opener, closer in (('[', ']'), ('{', '}')) if raw.find(opener) >= 0)
    for start, opener, closer in starts:
        depth, in_string, escape = 0, False, False
        for index in range(start, len(raw)):
            char = raw[index]
            if in_string:
                if escape:
                    escape = False
                elif char == '\\':
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == opener:
                depth += 1
            elif char == closer:
                depth -= 1
                if depth == 0:
                    chunk = raw[start:index + 1]
                    try:
                        return json.loads(chunk)
                    except ValueError:
                        # 尾逗号是最常见的一种：`[1,2,]`。只修这一种，别做通用容错。
                        try:
                            return json.loads(_strip_trailing_commas(chunk))
                        except ValueError:
                            break
    raise TextError('模型返回的内容不是有效 JSON，原文前 300 字：%s' % raw[:300])


def _strip_trailing_commas(chunk):
    out, in_string, escape = [], False, False
    for index, char in enumerate(chunk):
        if in_string:
            out.append(char)
            if escape:
                escape = False
            elif char == '\\':
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == ',':
            rest = chunk[index + 1:].lstrip()
            if rest[:1] in (']', '}'):
                continue
        out.append(char)
    return ''.join(out)


def _image_part(path):
    """把本地图片变成 opencode 的 file part。"""
    from PIL import Image

    source = Path(path)
    if not source.is_file():
        raise TextError('图片不存在：%s' % source.name)
    temp = None
    try:
        image = Image.open(source)
        if max(image.width, image.height) > MAX_IMAGE_EDGE:
            image.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE))
            temp = Path(tempfile.mkstemp(prefix='autocase-img-', suffix='.jpg')[1])
            image.convert('RGB').save(temp, quality=85, optimize=True)
            source = temp
        data = base64.b64encode(source.read_bytes()).decode('ascii')
        mime = 'image/jpeg' if source.suffix.lower() in ('.jpg', '.jpeg') else \
            'image/%s' % source.suffix.lstrip('.').lower()
        return {'type': 'file', 'mime': mime, 'filename': source.name,
                'url': 'data:%s;base64,%s' % (mime, data)}
    except TextError:
        raise
    except Exception as exc:  # noqa: BLE001 - 图片坏了不该让整条链路崩在奇怪的地方
        raise TextError('图片读取失败：%s' % str(exc)[:160])
    finally:
        if temp is not None:
            try:
                temp.unlink()
            except OSError:
                pass


class TextWorker(object):
    """一个常驻 serve，串行处理文本/图片请求。

    串行而不是并发：生成用例本来就是一个用户动作一次，并发只会让同一台机器上
    多个 serve 抢 CPU，反而都变慢。
    """

    _shared = None
    _shared_lock = threading.Lock()

    @classmethod
    def shared(cls, app_config=None):
        """进程级单例。app_config 只用来读 OPENCODE_* 与工作区根目录。"""
        with cls._shared_lock:
            if cls._shared is None:
                cls._shared = cls(app_config or {})
            return cls._shared

    @classmethod
    def shutdown(cls):
        with cls._shared_lock:
            worker, cls._shared = cls._shared, None
        if worker is not None:
            worker.close()

    def __init__(self, app_config):
        self._config = dict(app_config or {})
        self._runner = OpenCodeRunner(self._config)
        self._lock = threading.RLock()
        self._call_lock = threading.Lock()
        self._process = None
        self._base_url = None
        self._workdir = None
        self._last_used = 0.0
        # 有挂起会话时不允许按空闲时间回收：回收了会话就废了。
        self._hold_until = 0.0

    # ---- 进程 ----

    def _alive(self):
        with self._lock:
            return self._process is not None and self._process.poll() is None

    def _ensure_serve(self):
        with self._lock:
            alive = self._process is not None and self._process.poll() is None
            idle = (time.monotonic() - self._last_used) > IDLE_TTL
            held = time.monotonic() < self._hold_until
            stale = alive and idle and not held
            if alive and not stale:
                return self._base_url
        if alive:
            self._reap()
        return self._start()

    def _start(self):
        with self._lock:
            config_path = self._runner._config_path()
            port = self._runner._free_port()
            base_url = 'http://%s:%d' % (self._runner._serve_host(), port)
            workdir = tempfile.mkdtemp(prefix='autocase-text-')
            process = self._runner._start_serve(Path(workdir), config_path, port)
            self._process, self._base_url, self._workdir = process, base_url, workdir
        try:
            self._runner._wait_serve_healthy(base_url, process, 60)
        except Exception as exc:  # noqa: BLE001
            self._reap()
            raise TextError('opencode 启动失败：%s' % str(exc)[:200])
        return base_url

    def _reap(self):
        with self._lock:
            process, workdir = self._process, self._workdir
            self._process, self._base_url, self._workdir = None, None, None
        try:
            OpenCodeRunner._terminate_process(process)
        except Exception:  # noqa: BLE001
            pass
        try:
            if process is not None and process.stderr is not None:
                process.stderr.close()
        except Exception:  # noqa: BLE001
            pass
        if workdir:
            shutil.rmtree(workdir, ignore_errors=True)

    def close(self):
        self._reap()

    # ---- 调用 ----

    def ask(self, model, system, text, images=None, timeout=ASK_TIMEOUT):
        """发一组 parts 拿回纯文本。images 是本地图片路径列表。"""
        ref = self._ref(model)
        parts = self._parts(text, images)

        with self._call_lock:
            base_url = self._ensure_serve()
            session_id = None
            try:
                base_url, session_id = self.open_session(base_url)
                return self._post(base_url, session_id, ref, system, parts, timeout)
            except TextError:
                # 进程可能刚好挂了：重启再试一次。模型报错（如不支持图片）不该重试。
                if self._alive():
                    raise
                base_url = self._start()
                base_url, session_id = self.open_session(base_url)
                return self._post(base_url, session_id, ref, system, parts, timeout)
            finally:
                self.close_session(base_url, session_id)

    # ---- 挂起会话：拆模块和生成落在同一个会话里 ----

    def open_session(self, base_url=None, title='AutoCase'):
        """开一个会话并返回 (base_url, session_id)。调用方负责 close_session。"""
        base_url = base_url or self._ensure_serve()
        try:
            created = requests.post('%s/session' % base_url,
                                    json={'title': title}, timeout=15)
        except requests.RequestException as exc:
            raise TextError('创建 opencode 会话失败：%s' % str(exc)[:160])
        if created.status_code not in (200, 201):
            raise TextError('创建 opencode 会话失败（%s）' % created.status_code)
        session_id = (created.json() or {}).get('id')
        if not session_id:
            raise TextError('opencode 没有返回会话 id')
        return base_url, session_id

    def send(self, base_url, session_id, model, system, text, images=None,
             timeout=ASK_TIMEOUT, title=''):
        """往指定会话里发一条消息，不删会话。会话失效时抛 TextError，由调用方降级。"""
        ref = self._ref(model)
        parts = self._parts(text, images)
        with self._call_lock:
            return self._post(base_url, session_id, ref, system, parts, timeout)

    def close_session(self, base_url, session_id):
        if not base_url or not session_id:
            return
        try:
            requests.delete('%s/session/%s' % (base_url, session_id), timeout=10)
        except requests.RequestException:
            pass

    def hold(self, seconds):
        """挂起会话期间别回收 serve：回收了会话就废了（实测重启后发消息 500）。"""
        with self._lock:
            self._hold_until = max(getattr(self, '_hold_until', 0.0),
                                   time.monotonic() + max(0, seconds))

    def release(self):
        with self._lock:
            self._hold_until = 0.0

    def _ref(self, model):
        ref = OpenCodeRunner._split_model((model or '').strip())
        if ref is None:
            raise TextError('模型格式应为 provider/model，当前是「%s」' % (model or ''))
        return ref

    @staticmethod
    def _parts(text, images):
        parts = [{'type': 'text', 'text': text}]
        for path in (images or []):
            parts.append(_image_part(path))
        return parts

    def _post(self, base_url, session_id, ref, system, parts, timeout):
        body = {
            'parts': parts,
            'system': system,
            'tools': dict(ASK_NO_TOOLS),
            'format': {'type': 'text'},
            'model': {'providerID': ref['providerID'], 'modelID': ref['id']},
        }
        started = time.monotonic()
        try:
            reply = requests.post('%s/session/%s/message' % (base_url, session_id),
                                  json=body, timeout=timeout)
        except requests.RequestException as exc:
            raise TextError('模型调用超时或断开：%s' % str(exc)[:160])
        finally:
            self._last_used = time.monotonic()

        if reply.status_code != 200:
            raise TextError('模型调用失败（%s）：%s'
                            % (reply.status_code, (reply.text or '')[:200]))
        payload = reply.json() or {}
        error = (payload.get('info') or {}).get('error')
        if error:
            raise TextError(_explain_error(error))
        text = ''.join(part.get('text', '') for part in payload.get('parts', [])
                       if part.get('type') == 'text').strip()
        if not text:
            raise TextError('模型没有返回内容（耗时 %.1fs）' % (time.monotonic() - started))
        return text

    def ask_json(self, model, system, text, images=None, timeout=ASK_TIMEOUT):
        return parse_json(self.ask(model, system, text, images=images, timeout=timeout))


def _explain_error(error):
    """把 opencode 的错误翻译成用户能照做的提示。"""
    raw = str(error)
    if 'image input' in raw or '不支持图像' in raw:
        return ('当前模型没有开启图片输入。到「设置 → 模型服务」里给它勾上'
                '「支持图片」，保存后再试')
    if 'APIError' in raw or 'does not have a valid' in raw or 'subscription' in raw:
        return '模型服务返回错误：%s' % raw[:220]
    return '模型返回错误：%s' % raw[:220]


atexit.register(TextWorker.shutdown)
