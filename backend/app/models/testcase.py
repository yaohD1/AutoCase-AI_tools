import uuid
from datetime import datetime
from app.models.database import db

class TestCase(db.Model):
    __tablename__ = 'testcases'
    
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = db.Column(db.String(36), db.ForeignKey('projects.id'), nullable=False)
    
    module = db.Column(db.String(200), nullable=False)
    test_point = db.Column(db.String(200), nullable=False)
    title = db.Column(db.String(500), nullable=False)
    priority = db.Column(db.String(10), default='P2')
    
    preconditions = db.Column(db.Text)
    steps = db.Column(db.Text)
    expected = db.Column(db.Text)
    
    case_type = db.Column(db.String(50), default='functional')
    image_source = db.Column(db.String(500))
    ai_provider = db.Column(db.String(50))
    status = db.Column(db.String(20), default='pending', nullable=False)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'project_id': self.project_id,
            'module': self.module,
            'test_point': self.test_point,
            'title': self.title,
            'priority': self.priority,
            'preconditions': self.preconditions,
            'steps': self.steps,
            'expected': self.expected,
            'case_type': self.case_type,
            'image_source': self.image_source,
            'ai_provider': self.ai_provider,
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }