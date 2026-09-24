import os
import json
import subprocess
from flask import Flask
from datetime import datetime
from flask_cors import CORS
from sqlalchemy import or_
from app.config import Config
from app.models.database import init_db

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    
    CORS(app)
    init_db(app)
    with app.app_context():
        from app.models import AutomationGeneration, db
        stale = AutomationGeneration.query.filter(or_(
            AutomationGeneration.status.in_(['pending', 'running']),
            AutomationGeneration.status.in_(['failed', 'interrupted']) & AutomationGeneration.process_id.isnot(None)
        )).all()
        from app.services.opencode_runner import OpenCodeRunner
        runner = OpenCodeRunner(app.config)
        for generation in stale:
            terminated = True
            process_id = generation.process_id
            if generation.process_id:
                expected_workspace = None
                try:
                    config_snapshot = json.loads(generation.config_snapshot or '{}')
                    _, expected_workspace = runner.resolve_workspace(config_snapshot.get('workspace_path'))
                except (OSError, TypeError, ValueError, json.JSONDecodeError):
                    pass
                if expected_workspace and runner._live_backend_parent(process_id):
                    continue
                try:
                    terminated = bool(expected_workspace) and runner.terminate_pid(
                        process_id, expected_workspace, app.config.get('OPENCODE_BIN')
                    )
                except (OSError, subprocess.SubprocessError):
                    app.logger.exception('Failed to terminate stale automation process: %s', process_id)
                    terminated = False
                if terminated and expected_workspace:
                    lock_path = expected_workspace / '.autocase' / 'generation.lock'
                    try:
                        owner = lock_path.read_text(encoding='ascii').strip()
                    except OSError:
                        owner = ''
                    if owner == f'opencode:{generation.process_id}':
                        runner._release_file_lock(lock_path, owner)
            else:
                expected_workspace = None
                try:
                    config_snapshot = json.loads(generation.config_snapshot or '{}')
                    _, expected_workspace = runner.resolve_workspace(config_snapshot.get('workspace_path'))
                    lock_path = expected_workspace / '.autocase' / 'generation.lock'
                    owner = lock_path.read_text(encoding='ascii').strip()
                    if owner.startswith('opencode:'):
                        process_id = int(owner.split(':', 1)[1])
                        terminated = runner.terminate_pid(process_id, expected_workspace, app.config.get('OPENCODE_BIN'))
                        if terminated:
                            runner._release_file_lock(lock_path, owner)
                    else:
                        terminated = not owner
                except (OSError, TypeError, ValueError, json.JSONDecodeError):
                    terminated = False
            generation.status = 'interrupted'
            generation.stage = 'interrupted'
            generation.error = '服务重启导致 Agent 任务中断'
            if not terminated:
                generation.error += '；残留进程未确认结束，将在下次启动时继续清理'
            generation.completed_at = datetime.utcnow()
            generation.updated_at = datetime.utcnow()
            if terminated:
                generation.process_id = None
            elif process_id:
                generation.process_id = process_id
            try:
                events = json.loads(generation.event_log or '[]')
            except (TypeError, ValueError):
                events = []
            events.append({
                'sequence': (generation.event_cursor or 0) + 1,
                'stage': 'interrupted',
                'event_type': 'interrupted',
                'message': generation.error,
                'created_at': datetime.utcnow().isoformat()
            })
            generation.event_cursor = (generation.event_cursor or 0) + 1
            generation.event_log = json.dumps(events[-500:], ensure_ascii=False)
        if stale:
            db.session.commit()
    
    from app.routes.upload import upload_bp
    from app.routes.testcase import testcase_bp
    from app.routes.export import export_bp
    from app.routes.config import config_bp
    from app.routes.sprint import sprint_bp
    from app.routes.knowledge import knowledge_bp
    from app.routes.automation import automation_bp
    
    app.register_blueprint(upload_bp, url_prefix='/api')
    app.register_blueprint(testcase_bp, url_prefix='/api')
    app.register_blueprint(export_bp, url_prefix='/api')
    app.register_blueprint(config_bp, url_prefix='/api')
    app.register_blueprint(sprint_bp, url_prefix='/api')
    app.register_blueprint(knowledge_bp, url_prefix='/api')
    app.register_blueprint(automation_bp, url_prefix='/api')
    
    return app
