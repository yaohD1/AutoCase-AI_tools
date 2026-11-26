import json
import uuid
from datetime import datetime

from app.models.database import db


class TestRun(db.Model):
    """一次「执行测试」的运行记录。

    与 recording_generations 是两件事：那边记的是「写进仓库的代码」，
    这边记的是「把用例跑了一遍」。同一条用例可以跑很多次，所以单独一张表。
    """
    __tablename__ = 'test_runs'

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    workspace = db.Column(db.String(1000), nullable=False)
    framework = db.Column(db.String(50), nullable=False)
    # running：执行中；passed / failed：跑完；stopped：手动停止；error：没跑起来（缺依赖、配置坏）
    status = db.Column(db.String(20), nullable=False, default='running')
    total = db.Column(db.Integer, nullable=False, default=0)
    passed = db.Column(db.Integer, nullable=False, default=0)
    failed = db.Column(db.Integer, nullable=False, default=0)
    skipped = db.Column(db.Integer, nullable=False, default=0)
    flaky = db.Column(db.Integer, nullable=False, default=0)
    duration_ms = db.Column(db.Integer, nullable=False, default=0)
    # 选中的用例 id 列表；结果逐条存在 results_json，够报告页用，不必再拆表
    selection_json = db.Column(db.Text)
    results_json = db.Column(db.Text)
    # 一条都没跑起来时，这里放原因；跑起来了则放输出尾部，排查用
    error = db.Column(db.Text)
    log_tail = db.Column(db.Text)
    # AI 初步诊断：{state, model, items: {用例下标: {category, reason, detail, fixes, ...}}}
    # 为什么不拆表：诊断天然跟着某一次运行走，没有跨运行查询的需求，
    # 而 test_runs 已经是「一条记录一个 JSON 结果」的形态（results_json 就是这样）。
    diagnoses_json = db.Column(db.Text)
    artifacts_dir = db.Column(db.String(1000))
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    finished_at = db.Column(db.DateTime)

    def _payload(self):
        """results_json 有两种形态：早期是纯数组，现在为了带环境信息变成 {env, rows}。"""
        try:
            data = json.loads(self.results_json or '[]')
        except ValueError:
            return {}
        if isinstance(data, list):
            return {'env': {}, 'rows': data}
        return data if isinstance(data, dict) else {}

    def results(self):
        return self._payload().get('rows') or []

    def env(self):
        """跑这次用的框架版本 / 运行时 / project，报告头部要展示。"""
        return self._payload().get('env') or {}

    def selection(self):
        try:
            return json.loads(self.selection_json or '[]')
        except ValueError:
            return []

    def diagnoses(self):
        """AI 诊断快照。没诊断过返回 idle，前端据此决定显不显示入口。"""
        try:
            data = json.loads(self.diagnoses_json or '{}')
        except ValueError:
            return {'state': 'idle', 'items': []}
        if not isinstance(data, dict) or not data:
            return {'state': 'idle', 'items': []}
        items = data.get('items') or {}
        ordered = [items[key] for key in sorted(items, key=lambda k: int(k) if str(k).isdigit() else 0)]
        return {
            'state': data.get('state') or 'idle',
            'model': data.get('model') or '',
            'started_at': data.get('started_at'),
            'finished_at': data.get('finished_at'),
            'total_failed': data.get('total_failed') or 0,
            'pending': data.get('pending') or [],
            'items': ordered,
            'error': data.get('error') or '',
        }

    def summary(self):
        return {
            'id': self.id,
            'workspace': self.workspace,
            'framework': self.framework,
            'status': self.status,
            'total': self.total,
            'passed': self.passed,
            'failed': self.failed,
            'skipped': self.skipped,
            'flaky': self.flaky,
            'duration_ms': self.duration_ms,
            'error': self.error,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'finished_at': self.finished_at.isoformat() if self.finished_at else None,
        }

    def to_dict(self, with_results=True):
        data = self.summary()
        data['selection'] = self.selection()
        data['env'] = self.env()
        data['diagnoses'] = self.diagnoses()
        if with_results:
            data['results'] = self.results()
            data['log_tail'] = self.log_tail
        return data
