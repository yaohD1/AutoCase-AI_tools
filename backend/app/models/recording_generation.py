import json
import uuid
from datetime import datetime

from app.models.database import db


class RecordingGeneration(db.Model):
    """录制生成的记录。与 automation_generations 完全独立：表、接口、回滚逻辑都不共用。"""
    __tablename__ = 'recording_generations'

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    workspace = db.Column(db.String(1000), nullable=False)
    target_path = db.Column(db.String(500), nullable=False)
    framework = db.Column(db.String(50), nullable=False)
    title = db.Column(db.String(200))
    model = db.Column(db.String(200))
    # generated：已写入工作区，未提交；committed：已提交；reverted：已回滚
    status = db.Column(db.String(20), nullable=False, default='generated')
    original_existed = db.Column(db.Boolean, nullable=False, default=False)
    # 生成前目标文件的内容（base64），用于未提交时回滚
    original_content = db.Column(db.Text)
    # 生成后写入的内容指纹，用于判断文件在生成后是否又被改动
    generated_sha256 = db.Column(db.String(64), nullable=False)
    commit_hash = db.Column(db.String(64))
    commit_message = db.Column(db.String(500))
    step_summary = db.Column(db.Text)
    reply = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        try:
            steps = json.loads(self.step_summary or '[]')
        except ValueError:
            steps = []
        return {
            'id': self.id,
            'workspace': self.workspace,
            'target_path': self.target_path,
            'framework': self.framework,
            'title': self.title,
            'model': self.model,
            'status': self.status,
            'commit_hash': self.commit_hash,
            'commit_message': self.commit_message,
            'steps': steps,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
