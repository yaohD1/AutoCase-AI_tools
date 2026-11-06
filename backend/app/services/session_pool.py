"""挂起中的 opencode 会话。

首页生成用例是一条「AI 拆模块 → 用户改 → AI 接着生成」的链路。分析那一轮开的会话
不能立刻删：用户要在弹窗里改名、删模块、设条数，几分钟后再点生成，这一轮才继续。
会话留着，图就还在上下文里，第二轮不用重传图片（实测答得出第一轮没提过的占位文字）。

存储是**进程内字典**：本项目单进程跑（Flask dev server），够用；换成多 worker 部署时
令牌可能落到别的进程，`take()` 拿不到，调用方按「新开会话 + 重发图」降级即可，
不会报错，只是慢一点。

失效的两种情况都靠降级兜住：
- 超过 `TTL` 没生成 → 惰性清理掉。
- opencode serve 被回收/重启 → 会话记录还在（查询返回 200），但发消息 500，
  因为它的临时工作目录已经删了。这时同样降级。
"""
import threading
import time
import uuid

# 用户改模块的耐心上限。超过这个时间还没点生成，会话就该收了。
TTL = 2 * 3600
# 同时挂起的会话数上限。每个会话在 opencode 里都带着图，不设上限会一路涨。
MAX_SESSIONS = 20


class SessionPool(object):
    def __init__(self, ttl=TTL, limit=MAX_SESSIONS):
        self._items = {}
        self._lock = threading.RLock()
        self._ttl = ttl
        self._limit = limit

    def put(self, session_id, base_url, model, images, project_id=''):
        """登记一个挂起会话，返回给前端的令牌。满了就挤掉最老的一个。"""
        token = uuid.uuid4().hex
        item = {
            'token': token,
            'session_id': session_id,
            'base_url': base_url,
            'model': model,
            'images': list(images or []),
            'project_id': project_id or '',
            'created_at': time.time(),
        }
        with self._lock:
            self._sweep()
            while len(self._items) >= self._limit:
                oldest = min(self._items.values(), key=lambda x: x['created_at'])
                self._items.pop(oldest['token'], None)
            self._items[token] = item
        return token

    def take(self, token):
        """按令牌取会话元数据；取不到或过期返回 None（调用方走降级）。"""
        if not token:
            return None
        with self._lock:
            self._sweep()
            item = self._items.get(token)
            if not item:
                return None
            if time.time() - item['created_at'] > self._ttl:
                self._items.pop(token, None)
                return None
            return dict(item)

    def drop(self, token):
        with self._lock:
            self._items.pop(token or '', None)

    def active(self):
        with self._lock:
            self._sweep()
            return len(self._items)

    def clear(self):
        with self._lock:
            items = list(self._items.values())
            self._items.clear()
        return [item['session_id'] for item in items]

    def _sweep(self):
        deadline = time.time() - self._ttl
        for token, item in list(self._items.items()):
            if item['created_at'] < deadline:
                self._items.pop(token, None)


pool = SessionPool()
