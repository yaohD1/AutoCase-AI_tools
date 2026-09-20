import json
import uuid
from datetime import datetime

from app.models.database import db


class AutomationConfig(db.Model):
    __tablename__ = 'automation_configs'

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = db.Column(db.String(36), db.ForeignKey('projects.id'), nullable=False, unique=True)
    framework = db.Column(db.String(50), default='playwright', nullable=False)
    language = db.Column(db.String(50), default='typescript', nullable=False)
    workspace_path = db.Column(db.String(500), default='')
    specs_path = db.Column(db.String(255), default='tests')
    base_url = db.Column(db.String(500), default='')
    environment_name = db.Column(db.String(100), default='test')
    browser = db.Column(db.String(50), default='chromium')
    auth_state_path = db.Column(db.String(500), default='')
    locator_strategy = db.Column(db.String(50), default='role')
    action_timeout = db.Column(db.Integer, default=10000)
    navigation_timeout = db.Column(db.Integer, default=30000)
    expect_timeout = db.Column(db.Integer, default=5000)
    naming_strategy = db.Column(db.String(50), default='case')
    overwrite_policy = db.Column(db.String(50), default='reject')
    ai_config_id = db.Column(db.String(36), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'project_id': self.project_id,
            'framework': self.framework,
            'language': self.language,
            'workspace_path': self.workspace_path or '',
            'specs_path': self.specs_path or 'tests',
            'base_url': self.base_url or '',
            'environment_name': self.environment_name or 'test',
            'browser': self.browser or 'chromium',
            'auth_state_path': self.auth_state_path or '',
            'locator_strategy': self.locator_strategy or 'role',
            'action_timeout': self.action_timeout or 10000,
            'navigation_timeout': self.navigation_timeout or 30000,
            'expect_timeout': self.expect_timeout or 5000,
            'naming_strategy': self.naming_strategy or 'case',
            'overwrite_policy': self.overwrite_policy or 'reject',
            'ai_config_id': self.ai_config_id or '',
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class AutomationGeneration(db.Model):
    __tablename__ = 'automation_generations'

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = db.Column(db.String(36), db.ForeignKey('projects.id'), nullable=False)
    status = db.Column(db.String(20), default='pending', nullable=False)
    config_snapshot = db.Column(db.Text)
    testcase_ids = db.Column(db.Text)
    files = db.Column(db.Text)
    warnings = db.Column(db.Text)
    error = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime)

    def to_dict(self):
        def parse(value, fallback):
            if not value:
                return fallback
            try:
                return json.loads(value)
            except (TypeError, ValueError):
                return fallback

        return {
            'id': self.id,
            'project_id': self.project_id,
            'status': self.status,
            'config': parse(self.config_snapshot, {}),
            'testcase_ids': parse(self.testcase_ids, []),
            'files': parse(self.files, []),
            'warnings': parse(self.warnings, []),
            'error': self.error,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None
        }
