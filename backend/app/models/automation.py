import json
import uuid
from datetime import datetime

from app.models.database import db
from app.utils.sensitive import redact_sensitive_text


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
    opencode_model = db.Column(db.String(200), default='')
    max_heal_attempts = db.Column(db.Integer, default=3)
    overwrite_policy = db.Column(db.String(50), default='reject')
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
            'base_url': redact_sensitive_text(self.base_url or ''),
            'environment_name': self.environment_name or 'test',
            'browser': self.browser or 'chromium',
            'auth_state_path': self.auth_state_path or '',
            'opencode_model': self.opencode_model or '',
            'max_heal_attempts': self.max_heal_attempts if self.max_heal_attempts is not None else 3,
            'overwrite_policy': self.overwrite_policy or 'reject',
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class AutomationGeneration(db.Model):
    __tablename__ = 'automation_generations'

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = db.Column(db.String(36), db.ForeignKey('projects.id'), nullable=False)
    status = db.Column(db.String(20), default='pending', nullable=False)
    stage = db.Column(db.String(50), default='pending')
    requirement = db.Column(db.Text)
    config_snapshot = db.Column(db.Text)
    testcase_ids = db.Column(db.Text)
    files = db.Column(db.Text)
    warnings = db.Column(db.Text)
    event_log = db.Column(db.Text)
    event_cursor = db.Column(db.Integer, default=0)
    artifact_index = db.Column(db.Text)
    test_result = db.Column(db.Text)
    heal_attempts = db.Column(db.Integer, default=0)
    commit_status = db.Column(db.String(20), default='uncommitted')
    commit_hash = db.Column(db.String(80))
    commit_message = db.Column(db.String(500))
    committed_files = db.Column(db.Text)
    error = db.Column(db.Text)
    started_at = db.Column(db.DateTime)
    process_id = db.Column(db.Integer)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime)

    def to_dict(self, include_events=True):
        def parse(value, fallback):
            if not value:
                return fallback
            try:
                return json.loads(value)
            except (TypeError, ValueError):
                return fallback

        events = parse(self.event_log, []) if include_events else []
        if not isinstance(events, list):
            events = []
        config = parse(self.config_snapshot, {})
        if isinstance(config, dict):
            config = {
                key: redact_sensitive_text(value) if isinstance(value, str) else value
                for key, value in config.items()
            }
        safe_events = []
        for event in events:
            if not isinstance(event, dict):
                continue
            safe_event = dict(event)
            safe_event['message'] = redact_sensitive_text(safe_event.get('message', ''))
            safe_events.append(safe_event)
        return {
            'id': self.id,
            'project_id': self.project_id,
            'status': self.status,
            'stage': self.stage or self.status,
            'requirement': redact_sensitive_text(self.requirement or ''),
            'config': config,
            'testcase_ids': parse(self.testcase_ids, []),
            'files': parse(self.files, []),
            'warnings': parse(self.warnings, []),
            'event_cursor': self.event_cursor or 0,
            'events': safe_events,
            'event_log': '\n'.join(str(event.get('message', '')) for event in safe_events),
            'artifacts': parse(self.artifact_index, []),
            'test_result': parse(self.test_result, {}),
            'heal_attempts': self.heal_attempts or 0,
            'commit_status': self.commit_status or 'uncommitted',
            'commit_hash': self.commit_hash,
            'commit_message': self.commit_message,
            'committed_files': parse(self.committed_files, []),
            'error': redact_sensitive_text(self.error or '') if self.error else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'process_id': self.process_id,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None
        }
