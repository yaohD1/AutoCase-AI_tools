import uuid
from datetime import datetime
from app.models.database import db

class PendingModule(db.Model):
    __tablename__ = 'pending_modules'
    
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = db.Column(db.String(36), nullable=False)
    sprint_id = db.Column(db.String(36))
    image_id = db.Column(db.String(36))
    
    module = db.Column(db.String(200))
    ui_elements = db.Column(db.Text)
    function_description = db.Column(db.Text)
    interaction_flow = db.Column(db.Text)
    test_focus = db.Column(db.Text)
    case_types = db.Column(db.Text)
    case_count = db.Column(db.Integer, default=10)
    smart_mode = db.Column(db.Boolean, default=False)
    status = db.Column(db.String(20), default='pending')
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'project_id': self.project_id,
            'sprint_id': self.sprint_id,
            'image_id': self.image_id,
            'module': self.module,
            'ui_elements': self.ui_elements,
            'function_description': self.function_description,
            'interaction_flow': self.interaction_flow,
            'test_focus': self.test_focus,
            'case_types': self.case_types,
            'case_count': self.case_count,
            'smart_mode': self.smart_mode,
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }