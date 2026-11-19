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
    artifacts_dir = db.Column(db.String(1000))
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    finished_at = db.Column(db.DateTime)

    def results(self):
        try:
            return json.loads(self.results_json or '[]')
        except ValueError:
            return []

    def selection(self):
        try:
            return json.loads(self.selection_json or '[]')
        except ValueError:
            return []

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
        if with_results:
            data['results'] = self.results()
            data['log_tail'] = self.log_tail
        return data
