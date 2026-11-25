import json
import uuid
from datetime import datetime

from app.models.database import db


class RecordingDraft(db.Model):
    """录制存档：把一次还没录完的步骤存下来，下次接着录。

    与 recording_generations 分开存：那张表是「已写入脚本文件」的记录，
    带 original_content / generated_sha256 / commit_hash，草稿没有文件可写。

    steps_json 存完整 IR（target.candidates / primary 带定位 spec，见 session.ir()），
    恢复时原样灌回会话即可继续录制、生成任务书、回放。
    老存档存的是 snapshot 的公开步骤（没有 spec），续录时由 locators.hydrate_target
    按 target.snap 重建补回。
    """
    __tablename__ = 'recording_drafts'

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title = db.Column(db.String(200))
    base_url = db.Column(db.String(2000), nullable=False)
    workspace = db.Column(db.String(1000))
    framework = db.Column(db.String(50))
    target_path = db.Column(db.String(500))
    model = db.Column(db.String(200))
    python = db.Column(db.String(1000))
    step_count = db.Column(db.Integer, nullable=False, default=0)
    steps_json = db.Column(db.Text)
    picks_json = db.Column(db.Text)
    # 后置处理：{'action': {完整动作定义}, 'args': {...}}。动作定义随存档带一份，
    # 因为后置是项目级动作库里的引用，换了工作区 / 库被清过就找不到名字了。
    teardown_json = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def steps(self):
        try:
            return json.loads(self.steps_json or '[]')
        except ValueError:
            return []

    def picks(self):
        try:
            return json.loads(self.picks_json or '[]')
        except ValueError:
            return []

    def teardown(self):
        try:
            return json.loads(self.teardown_json or 'null')
        except ValueError:
            return None

    def to_dict(self, with_steps=False):
        data = {
            'id': self.id,
            'kind': 'draft',
            'title': self.title or '',
            'base_url': self.base_url,
            'workspace': self.workspace,
            'framework': self.framework,
            'target_path': self.target_path,
            'model': self.model,
            'python': self.python,
            'step_count': self.step_count or 0,
            # 后置的名字给列表用：存档列表里能看到「带后置」比只报步数有用。
            'teardown': ((self.teardown() or {}).get('action') or {}).get('name') or None,
            # 时间在库里是 naive UTC，带上 Z 前端才会按本地时区显示；
            # 不加的话列表里会比实际早 8 小时。
            'created_at': _iso(self.created_at),
            'updated_at': _iso(self.updated_at),
        }
        if with_steps:
            data['steps'] = self.steps()
            data['picks'] = self.picks()
        return data


def _iso(value):
    return value.isoformat() + 'Z' if value else None
