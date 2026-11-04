import json
import uuid
from datetime import datetime

from app.models.database import db


class ProjectMemory(db.Model):
    """一个工作区一条项目画像。

    key 用 workspace 绝对路径：AutoCase 里没有独立的「项目」实体，工作区路径就是它的身份，
    同一台机器上换一个目录就是另一套写法，混着用比没有更糟。

    画像本身是模型读项目后产出的 JSON（schema 见 services/recording/memory.py），
    这张表只负责存、按指纹判断要不要重学，不解释内容。
    """
    __tablename__ = 'project_memories'

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    workspace = db.Column(db.String(1000), nullable=False, unique=True, index=True)
    framework = db.Column(db.String(50))
    # learning：正在学（后台线程还没回来）；ready：可用；failed：上次学失败，error 里有原因
    status = db.Column(db.String(20), nullable=False, default='learning')
    # 画像 JSON 文本，体积上限由 memory.MAX_PROFILE_BYTES 控制
    profile = db.Column(db.Text)
    # 项目地图 markdown（目录树 + 每块职责 + 写法 + 封装 + 坑）。同一份也写进
    # <workspace>/.autocase/project-map.md；这里存一份是为了卡片和换机器后仍能看到。
    map_md = db.Column(db.Text)
    # 学画像那一刻项目的指纹：git HEAD + 关键文件大小 + 用例规模。指纹没变就不重学。
    fingerprint = db.Column(db.String(64))
    model = db.Column(db.String(200))
    error = db.Column(db.Text)
    learned_at = db.Column(db.DateTime)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def parsed(self):
        """画像 JSON；坏数据当没有，不要让一条脏记录把生成打挂。"""
        try:
            data = json.loads(self.profile or '{}')
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def to_dict(self, with_profile=False):
        data = {
            'id': self.id,
            'workspace': self.workspace,
            'framework': self.framework,
            'status': self.status,
            'model': self.model,
            'error': self.error,
            'learned_at': self.learned_at.isoformat() if self.learned_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'map': self.map_md or '',
        }
        if with_profile:
            data['profile'] = self.parsed()
        return data
