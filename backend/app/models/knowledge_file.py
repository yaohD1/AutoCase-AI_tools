import uuid
from datetime import datetime
from app.models.database import db

class KnowledgeFile(db.Model):
    __tablename__ = 'knowledge_files'
    
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = db.Column(db.String(36), db.ForeignKey('projects.id'), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    original_name = db.Column(db.String(255))
    file_path = db.Column(db.String(500), nullable=False)
    content = db.Column(db.Text)
    file_size = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self, include_content=False):
        d = {
            'id': self.id,
            'project_id': self.project_id,
            'filename': self.filename,
            'original_name': self.original_name,
            'file_path': self.file_path,
            'file_size': self.file_size,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }
        if include_content:
            d['content'] = self.content
        return d
