import uuid
from datetime import datetime
from app.models.database import db

class Sprint(db.Model):
    __tablename__ = 'sprints'
    
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = db.Column(db.String(36), db.ForeignKey('projects.id'), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    testcases = db.relationship('TestCase', backref='sprint', lazy=True, foreign_keys='TestCase.sprint_id')
    images = db.relationship('Image', backref='sprint', lazy=True, foreign_keys='Image.sprint_id')
    
    def to_dict(self):
        return {
            'id': self.id,
            'project_id': self.project_id,
            'name': self.name,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }