import uuid
from datetime import datetime
from app.models.database import db

class AIConfig(db.Model):
    __tablename__ = 'ai_configs'
    
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    provider = db.Column(db.String(50), unique=True, nullable=False)
    api_key = db.Column(db.Text)
    api_base = db.Column(db.String(500))
    model = db.Column(db.String(100))
    temperature = db.Column(db.Float, default=0.7)
    max_tokens = db.Column(db.Integer, default=2000)
    is_active = db.Column(db.Boolean, default=True)
    
    def to_dict(self):
        return {
            'id': self.id,
            'provider': self.provider,
            'api_key': self.api_key[:20] + '***' if self.api_key else None,
            'api_base': self.api_base,
            'model': self.model,
            'temperature': self.temperature,
            'max_tokens': self.max_tokens,
            'is_active': self.is_active
        }