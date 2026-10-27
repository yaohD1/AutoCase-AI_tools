"""录制生成进度：一份状态，两个窗口共用。

录制页会被打开两次——主界面 `/recording` 和录制面板 `/recording?panel=1`。
生成是同步 HTTP 请求，两边各自只有本地 loading：在面板点过之后，主界面的
按钮还是能点，等于同一个文件会被写两遍。这里把任务状态放到服务端，随
`/recording/current` 一起返回，两个窗口看到同一份进度，按钮也一起变成「生成中」。

百分比只给阶段基准值：模型写脚本、编译校验的耗时都无法预测，剩下的由前端按
`stage_at` 往 cap 上插值，避免进度条卡死不动或者提前满格。
"""
import threading
import time

# 阶段 → (文案, 起始百分比, 插值上限)。上限只是前端爬升的终点，不是阶段结束值。
STAGES = {
    'prepare': ('正在检查录制步骤与定位', 5, 10),
    'detect': ('正在识别工作区框架与工具链', 10, 16),
    'snapshot': ('正在记录工作区快照', 16, 22),
    'writing': ('正在让 AI 写脚本', 22, 62),
    'check': ('正在确认目标文件已写出', 62, 68),
    'validate': ('正在校验脚本', 68, 92),
    # 修复要重写文件，进度退回去是实情：不伪装成还在前进。
    'repair': ('正在修正校验错误', 62, 78),
    'record': ('正在登记生成记录', 92, 98),
    'done': ('完成', 100, 100),
}

# 规划是独立的一次请求：它只读项目、不写文件，耗时可到一分多钟，
# 阶段比写入少，但必须自己有一份，别让它借用写入的百分比。
PLAN_STAGES = {
    'prepare': ('正在检查录制步骤与定位', 4, 8),
    'planning': ('正在读项目、整理任务书', 8, 92),
    'done': ('任务书已就绪', 100, 100),
}

# 前端只展示这些字段，reply 可能上万字，不跟着进度一起传。
RESULT_FIELDS = ('path', 'framework', 'validated', 'detected', 'degraded',
                 'outside', 'changed', 'generation_id')


class EmitProgress:
    def __init__(self):
        self._lock = threading.Lock()
        self._state = self._idle()

    @staticmethod
    def _idle():
        return {
            'status': 'idle', 'mode': None, 'stage': None, 'label': '', 'detail': '',
            'percent': 0, 'cap': 0,
            'started_at': None, 'stage_at': None, 'finished_at': None,
            'elapsed': 0.0, 'result': None, 'error': None,
        }

    def _stages(self):
        return PLAN_STAGES if self._state.get('mode') == 'plan' else STAGES

    def reset(self):
        """新录制的起点：上一个会话的进度不该出现在这一条里。"""
        with self._lock:
            self._state = self._idle()

    def start(self, mode='write'):
        """占住任务位。返回 False 表示已经有一个任务在跑（另一个窗口先点了，或规划还没结束）。

        mode 区分「出任务书」和「写脚本」：两者互斥，但它们各自的阶段表不一样。
        """
        with self._lock:
            if self._state['status'] == 'running':
                return False
            now = time.time()
            table = PLAN_STAGES if mode == 'plan' else STAGES
            label, percent, cap = table['prepare']
            self._state = self._idle()
            self._state.update({
                'status': 'running', 'mode': mode, 'stage': 'prepare', 'label': label,
                'percent': percent, 'cap': cap,
                'started_at': now, 'stage_at': now,
            })
            return True

    def mark(self, stage, detail=''):
        """推进到某个阶段。任务已结束时忽略（迟到的回调不该把进度拉回去）。"""
        with self._lock:
            if self._state['status'] != 'running':
                return
            info = self._stages().get(stage)
            if info is None:
                return
            label, percent, cap = info
            self._state.update({
                'stage': stage, 'label': label, 'detail': detail or '',
                'percent': percent, 'cap': cap, 'stage_at': time.time(),
            })

    def finish(self, result=None):
        with self._lock:
            now = time.time()
            label = self._stages()['done'][0]
            self._state.update({
                'status': 'done', 'stage': 'done', 'label': label,
                'detail': '', 'percent': 100, 'cap': 100,
                'stage_at': now, 'finished_at': now,
                'result': {k: result.get(k) for k in RESULT_FIELDS if k in result} if result else None,
                'error': None,
            })

    def fail(self, message, details=None):
        with self._lock:
            if self._state['status'] != 'running':
                return
            self._state.update({
                'status': 'failed', 'finished_at': time.time(),
                'error': {'message': str(message), 'details': list(details or [])},
            })

    def snapshot(self):
        with self._lock:
            state = dict(self._state)
            # 数组是调用方可能改的对象，复制一份免得被轮询线程之外的人动到。
            state['error'] = dict(state['error']) if state['error'] else None
            state['result'] = dict(state['result']) if state['result'] else None
        if state['started_at'] and state['status'] == 'running':
            state['elapsed'] = round(time.time() - state['started_at'], 1)
        elif state['started_at'] and state['finished_at']:
            state['elapsed'] = round(state['finished_at'] - state['started_at'], 1)
        return state


# 全局只可能有一个生成：录制会话本身就是单例。
PROGRESS = EmitProgress()


def mark(stage, detail=''):
    PROGRESS.mark(stage, detail)
