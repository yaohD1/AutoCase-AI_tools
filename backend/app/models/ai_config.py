import uuid
from datetime import datetime
from app.models.database import db


def mask_key(key):
    """列表里只展示前 6 位和后 4 位。

    原来写的是 key[:20] + '***'，看着像脱敏，其实 20 位已经覆盖了大部分
    真实 key（常见 key 也就 30~40 位），短 key 甚至会整段暴露。
    """
    if not key:
        return None
    if len(key) <= 12:
        return '•' * len(key)
    return f'{key[:6]}••••{key[-4:]}'


class AIConfig(db.Model):
    __tablename__ = 'ai_configs'
    
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    provider = db.Column(db.String(50), nullable=False)
    api_key = db.Column(db.Text)
    api_base = db.Column(db.String(500))
    model = db.Column(db.String(100))
    temperature = db.Column(db.Float, default=0.7)
    max_tokens = db.Column(db.Integer, default=2000)
    is_active = db.Column(db.Boolean, default=True)
    supports_vision = db.Column(db.Boolean, default=True)
    
    def to_dict(self):
        return {
            'id': self.id,
            'provider': self.provider,
            'api_key': mask_key(self.api_key),
            'has_key': bool(self.api_key),
            'api_base': self.api_base,
            'model': self.model,
            'temperature': self.temperature,
            'max_tokens': self.max_tokens,
            'is_active': self.is_active,
            'supports_vision': self.supports_vision if self.supports_vision is not None else True
        }