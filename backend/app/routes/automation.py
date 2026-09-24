import json
import hashlib
import os
import subprocess
import threading
import time
from queue import Empty, Full, Queue
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from flask import Blueprint, current_app, jsonify, request, send_file

from app.models import AutomationConfig, AutomationGeneration, Project, db
from app.services.opencode_runner import OpenCodeRunner, WorkspaceBusyError
from app.utils.sensitive import redact_sensitive_text


automation_bp = Blueprint('automation', __name__)


def _append_generation_event(app, generation_id, event, artifacts):
    with app.app_context():
        for attempt in range(3):
            generation = AutomationGeneration.query.get(generation_id)
            if not generation:
                return
            try:
                events = json.loads(generation.event_log or '[]')
            except (TypeError, ValueError):
                events = []
            events.append({
                'sequence': (generation.event_cursor or 0) + 1,
                'stage': event.get('stage', 'orchestrator'),
                'event_type': event.get('event_type', 'output'),
                'message': event.get('message', '')[:4000],
                'created_at': datetime.utcnow().isoformat()
            })
            generation.event_cursor = (generation.event_cursor or 0) + 1
            if generation.status not in ('completed', 'failed', 'interrupted'):
                generation.stage = event.get('stage') or generation.stage
            generation.event_log = json.dumps(events[-500:], ensure_ascii=False)
            generation.artifact_index = json.dumps(artifacts or [], ensure_ascii=False)
            generation.updated_at = datetime.utcnow()
            try:
                db.session.commit()
                return
            except Exception:
                db.session.rollback()
                if attempt == 2:
                    app.logger.exception('Failed to persist automation event: %s', generation_id)
                else:
                    time.sleep(0.05 * (2 ** attempt))


def _wait_for_events(event_queue, timeout=5):
    deadline = time.monotonic() + timeout
    while event_queue.unfinished_tasks and time.monotonic() < deadline:
        time.sleep(0.05)


def _force_mark_generation_failed(app, generation_id, error_message, process_id=None):
    with app.app_context():
        for attempt in range(3):
            generation = AutomationGeneration.query.get(generation_id)
            if not generation:
                return
            generation.status = 'failed'
            generation.stage = generation.stage or 'orchestrator'
            generation.error = error_message
            generation.process_id = process_id
            generation.completed_at = datetime.utcnow()
            generation.updated_at = datetime.utcnow()
            try:
                db.session.commit()
                return
            except Exception:
                db.session.rollback()
                if attempt < 2:
                    time.sleep(0.05 * (2 ** attempt))
        app.logger.error('Failed to mark automation generation failed after retries: %s', generation_id)


def _run_generation(app, generation_id, workspace, manifest, model, timeout):
    runner = OpenCodeRunner(app.config)
    event_write_lock = threading.Lock()
    event_queue = Queue(maxsize=1000)

    def callback(event, artifacts):
        try:
            event_queue.put_nowait((event, artifacts))
        except Full:
            app.logger.warning('Dropping OpenCode log event because the event queue is full: %s', generation_id)

    def write_events():
        while True:
            item = event_queue.get()
            try:
                if item is None:
                    return
                with event_write_lock:
                    _append_generation_event(app, generation_id, item[0], item[1])
            finally:
                event_queue.task_done()

    writer = threading.Thread(target=write_events, daemon=True)
    writer.start()

    def process_callback(pid):
        with event_write_lock, app.app_context():
            generation = AutomationGeneration.query.get(generation_id)
            if generation:
                generation.process_id = pid
                if generation.status in ('pending', 'queued'):
                    generation.status = 'running'
                    generation.stage = generation.stage or 'orchestrator'
                generation.updated_at = datetime.utcnow()
                db.session.commit()
    try:
        result = runner.run(
            workspace,
            manifest,
            generation_id,
            model=model,
            timeout=timeout,
            event_callback=callback,
            process_callback=process_callback
        )
        _wait_for_events(event_queue)
        with event_write_lock, app.app_context():
            generation = AutomationGeneration.query.get(generation_id)
            if not generation:
                return
            cancellation_won = generation.status == 'interrupted' and not result.get('interrupted')
            generation.status = 'interrupted' if result.get('interrupted') or cancellation_won else ('completed' if result['success'] else 'failed')
            generation.stage = 'interrupted' if result.get('interrupted') or cancellation_won else ('completed' if result['success'] else (result.get('stage') or 'orchestrator'))
            generation.files = json.dumps(result['files'], ensure_ascii=False)
            generation.artifact_index = json.dumps(result.get('artifacts', []), ensure_ascii=False)
            generation.test_result = json.dumps({
                'success': result['success'],
                'return_code': result['return_code'],
                'return_code_hex': result.get('return_code_hex'),
                'return_code_name': result.get('return_code_name'),
                'elapsed_seconds': result.get('elapsed_seconds'),
                'timed_out': result.get('timed_out', False),
                'output': result.get('output', '')[-20000:]
            }, ensure_ascii=False)
            generation.heal_attempts = result.get('heal_attempts', 0)
            generation.process_id = result.get('process_id') if result.get('cleanup_failed') else None
            if result.get('interrupted') or cancellation_won:
                generation.error = 'OpenCode workflow was interrupted by the user.'
                final_message = result.get('output') or generation.error
            elif result.get('stalled'):
                generation.error = f"OpenCode produced no workflow activity for {result.get('idle_timeout_seconds')} seconds."
                if result.get('cleanup_failed'):
                    generation.error += ' The process did not exit cleanly; the workspace lock was retained.'
                final_message = f"{generation.error}\n{result.get('output') or ''}"
            elif result['success']:
                generation.error = None
                final_message = result.get('output') or 'OpenCode workflow completed.'
            elif result.get('timed_out'):
                generation.error = f"OpenCode process timed out after {result.get('timeout_seconds')} seconds."
                if result.get('cleanup_failed'):
                    generation.error += ' The process did not exit cleanly; the workspace lock was retained.'
                final_message = f"{generation.error}\n{result.get('output') or ''}"
            elif result.get('cleanup_failed'):
                generation.error = 'OpenCode process did not exit cleanly; the workspace lock was retained.'
                final_message = f"{generation.error}\n{result.get('output') or ''}"
            elif any(marker in (result.get('output') or '').lower() for marker in ('ai_apicallerror', 'stream error')):
                generation.error = '模型 API 流请求失败，OpenCode 未能完成本次工作流。'
                final_message = f"{generation.error}\n{result.get('output') or ''}"
            elif result.get('secret_violations'):
                generation.error = 'Generated artifacts contain sensitive data and were withheld from publication.'
                final_message = f"{generation.error}\nFiles: {', '.join(result['secret_violations'])}"
            elif result.get('scope_violations'):
                generation.error = 'Agent attempted to modify files outside the automation test scope.'
                final_message = f"{generation.error}\nFiles: {', '.join(result['scope_violations'])}"
            elif result.get('return_code') not in (0, None):
                code = result.get('return_code')
                code_hex = result.get('return_code_hex') or 'unknown hex code'
                code_name = result.get('return_code_name') or 'unknown status'
                generation.error = f"OpenCode exited with code {code} ({code_hex}, {code_name})."
                final_message = f"{generation.error}\n{result.get('output') or ''}"
            elif not result.get('files'):
                generation.error = 'OpenCode completed without generating workspace files.'
                final_message = f"{generation.error}\n{result.get('output') or ''}"
            else:
                generation.error = 'OpenCode workflow failed; inspect the generation log.'
                final_message = f"{generation.error}\n{result.get('output') or ''}"
            generation.completed_at = datetime.utcnow()
            generation.updated_at = datetime.utcnow()
            try:
                events = json.loads(generation.event_log or '[]')
            except (TypeError, ValueError):
                events = []
            events.append({
                'sequence': (generation.event_cursor or 0) + 1,
                'stage': generation.stage,
                'event_type': 'interrupted' if result.get('interrupted') or cancellation_won else ('completed' if result['success'] else 'failed'),
                'message': final_message[-4000:],
                'created_at': datetime.utcnow().isoformat()
            })
            generation.event_cursor = (generation.event_cursor or 0) + 1
            generation.event_log = json.dumps(events[-500:], ensure_ascii=False)
            db.session.commit()
    except Exception as exc:
        app.logger.exception('Automation generation failed: %s', exc)
        _wait_for_events(event_queue)
        residual_process_id = None
        try:
            lock_path = workspace / '.autocase' / 'generation.lock'
            lock_owner = lock_path.read_text(encoding='ascii').strip()
            if lock_owner.startswith('opencode:'):
                residual_process_id = int(lock_owner.split(':', 1)[1])
        except (OSError, ValueError):
            pass
        with app.app_context():
            generation = AutomationGeneration.query.get(generation_id)
            if generation:
                if generation.status == 'interrupted':
                    if residual_process_id and not generation.process_id:
                        generation.process_id = residual_process_id
                        generation.updated_at = datetime.utcnow()
                        db.session.commit()
                    return
                generation.status = 'failed'
                generation.stage = 'orchestrator'
                error_message = OpenCodeRunner._redact_output(str(exc))[:2000] or 'OpenCode workflow failed.'
                generation.error = error_message
                generation.test_result = json.dumps({
                    'success': False,
                    'return_code': -1,
                    'output': error_message
                }, ensure_ascii=False)
                generation.process_id = residual_process_id
                generation.completed_at = datetime.utcnow()
                generation.updated_at = datetime.utcnow()
                try:
                    events = json.loads(generation.event_log or '[]')
                except (TypeError, ValueError):
                    events = []
                events.append({
                    'sequence': (generation.event_cursor or 0) + 1,
                    'stage': 'failed',
                    'event_type': 'error',
                    'message': error_message,
                    'created_at': datetime.utcnow().isoformat()
                })
                generation.event_cursor = (generation.event_cursor or 0) + 1
                generation.event_log = json.dumps(events[-500:], ensure_ascii=False)
                try:
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                    _force_mark_generation_failed(app, generation_id, error_message, residual_process_id)
    finally:
        _wait_for_events(event_queue)
        try:
            event_queue.put(None, timeout=5)
        except Full:
            pass
        writer.join(timeout=5)


def _default_config(project_id):
    return {
        'project_id': project_id,
        'framework': 'playwright',
        'language': 'typescript',
        'workspace_path': '',
        'specs_path': 'tests',
        'base_url': '',
        'environment_name': 'test',
        'browser': 'chromium',
        'auth_state_path': '',
        'opencode_model': '',
        'max_heal_attempts': 3,
        'overwrite_policy': 'reject',
    }


def _apply_config(config, data):
    allowed = {
        'framework', 'language', 'workspace_path', 'specs_path', 'base_url',
        'environment_name', 'browser', 'auth_state_path', 'opencode_model',
        'max_heal_attempts', 'overwrite_policy'
    }
    for key in allowed:
        if key in data:
            setattr(config, key, data[key])


def _normalise_config(config):
    if not config.framework:
        config.framework = 'playwright'
    if not config.language:
        config.language = 'typescript'
    if not config.specs_path:
        config.specs_path = 'tests'
    if not config.environment_name:
        config.environment_name = 'test'
    if not config.browser:
        config.browser = 'chromium'
    if config.max_heal_attempts is None:
        config.max_heal_attempts = 3
    if not config.overwrite_policy:
        config.overwrite_policy = 'reject'


def _validate_config(config, require_generation=False):
    for field, maximum in (
        ('workspace_path', 500), ('specs_path', 255), ('base_url', 500),
        ('environment_name', 100), ('auth_state_path', 500), ('opencode_model', 200)
    ):
        value = getattr(config, field, '') or ''
        if not isinstance(value, str) or len(value) > maximum:
            return f'{field} must be a string shorter than {maximum} characters'
    if config.framework != 'playwright' or config.language != 'typescript':
        return 'Only Playwright + TypeScript is supported in this version'
    if config.browser not in ('chromium', 'firefox', 'webkit'):
        return 'browser must be chromium, firefox or webkit'
    if config.overwrite_policy not in ('reject', 'overwrite'):
        return 'overwrite_policy must be reject or overwrite'
    specs_path = Path(config.specs_path or 'tests')
    if specs_path.is_absolute() or '..' in specs_path.parts:
        return 'specs_path must be a relative directory without ..'
    if require_generation and not (config.workspace_path or '').strip():
        return 'workspace_path is required for generation'
    if config.base_url:
        parsed = urlparse(config.base_url or '')
        if require_generation and (parsed.scheme not in ('http', 'https') or not parsed.netloc):
            return 'base_url must be a valid http or https URL'
        if parsed.username or parsed.password:
            return 'base_url must not contain embedded credentials; use storage_state instead'
        if parsed.query or parsed.fragment:
            return 'base_url must not contain query parameters or fragments; use storage_state instead'
    attempts = config.max_heal_attempts
    if isinstance(attempts, bool) or not isinstance(attempts, int) or not 0 <= attempts <= 3:
        return 'max_heal_attempts must be an integer between 0 and 3'
    return None


@automation_bp.route('/automation/config', methods=['GET'])
def get_automation_config():
    project_id = request.args.get('project_id')
    if not project_id:
        return jsonify({'error': 'project_id required'}), 400
    if not Project.query.get(project_id):
        return jsonify({'error': 'Project not found'}), 404
    config = AutomationConfig.query.filter_by(project_id=project_id).first()
    return jsonify({'config': config.to_dict() if config else _default_config(project_id)}), 200


@automation_bp.route('/automation/config', methods=['PUT'])
def save_automation_config():
    data = request.get_json() or {}
    project_id = data.get('project_id')
    if not project_id:
        return jsonify({'error': 'project_id required'}), 400
    if not Project.query.get(project_id):
        return jsonify({'error': 'Project not found'}), 404
    config = AutomationConfig.query.filter_by(project_id=project_id).first()
    if not config:
        config = AutomationConfig(project_id=project_id)
        db.session.add(config)
    _apply_config(config, data)
    _normalise_config(config)
    validation_error = _validate_config(config)
    if validation_error:
        return jsonify({'error': validation_error}), 400
    db.session.commit()
    return jsonify({'success': True, 'config': config.to_dict()}), 200


@automation_bp.route('/automation/generate', methods=['POST'])
def generate_automation_scripts():
    data = request.get_json() or {}
    project_id = data.get('project_id')
    requirement = data.get('requirement')
    if not project_id or not isinstance(requirement, str) or not requirement.strip():
        return jsonify({'error': 'project_id and requirement are required'}), 400
    if len(requirement) > 30000:
        return jsonify({'error': 'requirement must be shorter than 30000 characters'}), 400

    project = Project.query.get(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    config = AutomationConfig.query.filter_by(project_id=project_id).first()
    if not config:
        config = AutomationConfig(project_id=project_id)
        db.session.add(config)
    if isinstance(data.get('config'), dict):
        _apply_config(config, data['config'])
    _normalise_config(config)
    validation_error = _validate_config(config, require_generation=True)
    if validation_error:
        return jsonify({'error': validation_error}), 400

    runner = OpenCodeRunner(current_app.config)
    safe_requirement = runner.redact_sensitive_text(requirement.strip())
    try:
        _, workspace = runner.resolve_workspace(config.workspace_path)
        runner.validate_workspace(workspace)
        storage_state = runner.resolve_storage_state(workspace, config.auth_state_path)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    generation = AutomationGeneration(
        project_id=project_id,
        status='pending',
        stage='starting',
        requirement=safe_requirement,
        config_snapshot=json.dumps(config.to_dict(), ensure_ascii=False),
        testcase_ids=json.dumps([], ensure_ascii=False),
        event_log=json.dumps([], ensure_ascii=False),
        artifact_index=json.dumps([], ensure_ascii=False),
        heal_attempts=0,
        commit_status='uncommitted',
        started_at=datetime.utcnow(),
        process_id=None,
        updated_at=datetime.utcnow()
    )
    db.session.add(generation)
    db.session.commit()

    manifest = {
        'version': 1,
        'project_id': project.id,
        'project_name': project.name,
        'requirement': safe_requirement,
        'base_url': config.base_url,
        'environment_name': config.environment_name or 'test',
        'browser': config.browser or 'chromium',
        'specs_path': config.specs_path or 'tests',
        'storage_state': str(storage_state.relative_to(workspace)).replace('\\', '/') if storage_state else '',
        'max_heal_attempts': config.max_heal_attempts if config.max_heal_attempts is not None else 3,
        'overwrite_policy': config.overwrite_policy or 'reject',
        'rules': [
            'Use the provided Base URL and storage state when exploring the application.',
            'Do not write credentials, API keys, or secrets into prompts, logs, or test files.',
            'Credentials from the requirement are redacted; use storage_state for authenticated flows.',
            'Write generated tests only below specs_path and keep the workspace uncommitted.'
        ]
    }
    generation.stage = 'queued'
    db.session.commit()
    app = current_app._get_current_object()
    runner.register_run(generation.id)
    thread = threading.Thread(
        target=_run_generation,
        args=(app, generation.id, workspace, manifest, config.opencode_model or None, app.config.get('OPENCODE_TIMEOUT', 3600)),
        daemon=True
    )
    thread.start()
    response = generation.to_dict()
    response['location'] = f'/scripts/generations/{generation.id}'
    return jsonify({'success': True, 'generation': response}), 202


@automation_bp.route('/automation/generations', methods=['GET'])
def list_generations():
    project_id = request.args.get('project_id')
    if not project_id:
        return jsonify({'error': 'project_id required'}), 400
    generations = AutomationGeneration.query.filter_by(project_id=project_id).order_by(
        AutomationGeneration.created_at.desc()
    ).limit(20).all()
    return jsonify({'generations': [generation.to_dict(include_events=False) for generation in generations]}), 200


@automation_bp.route('/automation/generations/<generation_id>', methods=['GET'])
def get_generation(generation_id):
    generation = AutomationGeneration.query.get(generation_id)
    project_id = request.args.get('project_id')
    if not project_id or not generation or generation.project_id != project_id:
        return jsonify({'error': 'Generation not found'}), 404
    return jsonify({'generation': generation.to_dict(include_events=False)}), 200


def _get_project_generation(generation_id):
    project_id = request.args.get('project_id')
    generation = AutomationGeneration.query.get(generation_id)
    if not project_id or not generation or generation.project_id != project_id:
        return None
    return generation


def _mark_generation_interrupted(generation, message):
    generation.status = 'interrupted'
    generation.stage = 'interrupted'
    generation.error = message
    generation.completed_at = datetime.utcnow()
    generation.updated_at = datetime.utcnow()
    try:
        events = json.loads(generation.event_log or '[]')
    except (TypeError, ValueError):
        events = []
    events.append({
        'sequence': (generation.event_cursor or 0) + 1,
        'stage': 'interrupted',
        'event_type': 'interrupted',
        'message': message,
        'created_at': datetime.utcnow().isoformat()
    })
    generation.event_cursor = (generation.event_cursor or 0) + 1
    generation.event_log = json.dumps(events[-500:], ensure_ascii=False)


def _cleanup_residual_generation(app, generation_id, process_id, workspace):
    runner = OpenCodeRunner(app.config)
    terminated = runner.terminate_pid(process_id, workspace, app.config.get('OPENCODE_BIN'))
    with app.app_context():
        generation = AutomationGeneration.query.get(generation_id)
        if not generation:
            return
        if terminated:
            generation.process_id = None
            lock_path = workspace / '.autocase' / 'generation.lock'
            runner._release_file_lock(lock_path, f'opencode:{process_id}')
        else:
            generation.error = 'OpenCode cancellation was requested, but the process could not be terminated safely; the workspace lock was retained.'
        generation.updated_at = datetime.utcnow()
        db.session.commit()


@automation_bp.route('/automation/generations/<generation_id>/cancel', methods=['POST'])
def cancel_generation(generation_id):
    generation = _get_project_generation(generation_id)
    if not generation:
        return jsonify({'error': 'Generation not found'}), 404
    if generation.status not in ('pending', 'running', 'interrupted', 'failed'):
        return jsonify({'error': 'Only pending or running generations can be cancelled'}), 409

    runner = OpenCodeRunner(current_app.config)
    if generation.status == 'failed' and not generation.process_id:
        return jsonify({'success': True, 'generation': generation.to_dict(include_events=False)}), 200
    if generation.status == 'interrupted':
        if not generation.process_id:
            return jsonify({'success': True, 'generation': generation.to_dict(include_events=False)}), 200
        process_id = generation.process_id
        try:
            config = json.loads(generation.config_snapshot or '{}')
            _, workspace = runner.resolve_workspace(config.get('workspace_path'))
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return jsonify({'error': str(exc)}), 409
        app = current_app._get_current_object()
        threading.Thread(
            target=_cleanup_residual_generation,
            args=(app, generation.id, process_id, workspace),
            daemon=True
        ).start()
        return jsonify({'success': True, 'cancellation_requested': True, 'generation': generation.to_dict(include_events=False)}), 202

    process_id = generation.process_id
    workspace = None
    try:
        config = json.loads(generation.config_snapshot or '{}')
        _, workspace = runner.resolve_workspace(config.get('workspace_path'))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        pass
    if not workspace:
        return jsonify({'error': 'The generation workspace could not be resolved safely'}), 409
    if workspace and not process_id:
        try:
            owner = (workspace / '.autocase' / 'generation.lock').read_text(encoding='ascii').strip()
            if owner.startswith('opencode:'):
                process_id = int(owner.split(':', 1)[1])
        except (OSError, ValueError):
            pass
    if process_id:
        if not workspace:
            return jsonify({'error': 'The OpenCode process could not be terminated safely'}), 409
    try:
        runner = OpenCodeRunner(current_app.config)
        cancel_path = runner._cancel_path(generation.id)
        cancel_path.write_text('requested', encoding='ascii')
    except OSError as exc:
        return jsonify({'error': f'Could not request cancellation: {exc}'}), 409

    runner.request_cancel(generation.id)

    _mark_generation_interrupted(generation, 'OpenCode workflow cancellation requested by the user.')
    db.session.commit()
    return jsonify({
        'success': True,
        'cancellation_requested': True,
        'generation': generation.to_dict(include_events=False)
    }), 202


@automation_bp.route('/automation/generations/<generation_id>/events', methods=['GET'])
def get_generation_events(generation_id):
    generation = _get_project_generation(generation_id)
    if not generation:
        return jsonify({'error': 'Generation not found'}), 404
    try:
        after = max(0, int(request.args.get('after', 0)))
    except ValueError:
        after = 0
    try:
        events = json.loads(generation.event_log or '[]')
    except (TypeError, ValueError):
        events = []
    return jsonify({
        'events': [event for event in events if event.get('sequence', 0) > after],
        'cursor': generation.event_cursor or 0,
        'status': generation.status,
        'stage': generation.stage
    }), 200


@automation_bp.route('/automation/generations/<generation_id>/artifacts', methods=['GET'])
def get_generation_artifacts(generation_id):
    generation = _get_project_generation(generation_id)
    if not generation:
        return jsonify({'error': 'Generation not found'}), 404
    try:
        artifacts = json.loads(generation.artifact_index or '[]')
    except (TypeError, ValueError):
        artifacts = []
    kind = request.args.get('kind')
    if kind:
        artifacts = [artifact for artifact in artifacts if artifact.get('kind') == kind]
    return jsonify({'artifacts': artifacts}), 200


@automation_bp.route('/automation/generations/<generation_id>/artifact', methods=['GET'])
def get_generation_artifact(generation_id):
    generation = _get_project_generation(generation_id)
    if not generation:
        return jsonify({'error': 'Generation not found'}), 404
    relative_path = request.args.get('path', '').replace('\\', '/')
    path = Path(relative_path)
    if not relative_path or path.is_absolute() or '..' in path.parts:
        return jsonify({'error': 'Invalid artifact path'}), 400
    try:
        artifacts = json.loads(generation.artifact_index or '[]')
    except (TypeError, ValueError):
        artifacts = []
    matching = next((artifact for artifact in artifacts if artifact.get('path') == relative_path), None)
    if not matching or matching.get('kind') not in ('plan', 'script', 'config'):
        return jsonify({'error': 'Artifact not found'}), 404
    try:
        config = json.loads(generation.config_snapshot or '{}')
    except (TypeError, ValueError):
        return jsonify({'error': 'Generation configuration is invalid'}), 409
    runner = OpenCodeRunner(current_app.config)
    try:
        _, workspace = runner.resolve_workspace(config.get('workspace_path'))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        return jsonify({'error': str(exc)}), 400
    try:
        target = (workspace / path).resolve()
    except OSError:
        return jsonify({'error': 'Artifact path is unavailable'}), 404
    if target != workspace and workspace not in target.parents:
        return jsonify({'error': 'Artifact path escaped workspace'}), 400
    if target.name in ('.env', '.auth') or '.auth' in target.parts or (target.suffix in ('.json', '.key', '.pem') and 'test-plans' not in target.parts):
        return jsonify({'error': 'Artifact is not readable'}), 403
    try:
        if not target.is_file() or target.stat().st_size > 2 * 1024 * 1024:
            return jsonify({'error': 'Artifact is unavailable'}), 404
        content = target.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return jsonify({'error': 'Artifact is unavailable'}), 404
    safe_content = redact_sensitive_text(content)
    if safe_content != content:
        return jsonify({'error': 'Artifact contains sensitive data and is not readable'}), 403
    return jsonify({
        'path': relative_path,
        'kind': matching.get('kind'),
        'content': safe_content
    }), 200


@automation_bp.route('/automation/generations/<generation_id>/commit', methods=['POST'])
def commit_generation(generation_id):
    generation = _get_project_generation(generation_id)
    if not generation:
        return jsonify({'error': 'Generation not found'}), 404
    if generation.status != 'completed':
        return jsonify({'error': 'Only completed generations can be committed'}), 409
    if generation.commit_status == 'committed':
        return jsonify({'generation': generation.to_dict()}), 200
    data = request.get_json() or {}
    message = str(data.get('message') or 'test: generate Playwright tests').strip()
    if not message or len(message) > 200:
        return jsonify({'error': 'Commit message must be between 1 and 200 characters'}), 400
    config = json.loads(generation.config_snapshot or '{}')
    runner = OpenCodeRunner(current_app.config)
    lock = None
    file_lock = None
    try:
        _, workspace = runner.resolve_workspace(config.get('workspace_path'))
        lock = runner._workspace_lock(workspace)
        file_lock = runner._acquire_file_lock(workspace)
        files = json.loads(generation.files or '[]')
        paths = [item.get('path') for item in files if item.get('path')]
        violations = runner._file_scope(files, config.get('specs_path'))
        if violations:
            return jsonify({'error': 'Generation contains files outside the commit scope', 'files': violations}), 409
        allowed = [path for path in paths if path.startswith('test-plans/') or path.startswith(f"{Path(config.get('specs_path') or 'tests').as_posix().rstrip('/')}/") or path.startswith('playwright.config.')]
        if not allowed:
            return jsonify({'error': 'No committable generated files found'}), 409
        expected_hashes = {item.get('path'): item.get('sha256') for item in files}
        changed_since_generation = []
        for path in allowed:
            target = (workspace / path).resolve()
            if not target.is_file():
                changed_since_generation.append(path)
                continue
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            if expected_hashes.get(path) and digest != expected_hashes[path]:
                changed_since_generation.append(path)
        if changed_since_generation:
            return jsonify({'error': 'Generated files changed after the task completed; review and regenerate before committing', 'files': changed_since_generation}), 409
        commit_hash = runner.commit_files(workspace, allowed, message)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        return jsonify({'error': str(exc)}), 409
    finally:
        if file_lock:
            runner._release_file_lock(file_lock, f'launcher:{os.getpid()}')
        if lock:
            lock.release()
    generation.commit_status = 'committed'
    generation.commit_hash = commit_hash
    generation.commit_message = message
    generation.committed_files = json.dumps(allowed, ensure_ascii=False)
    generation.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'success': True, 'generation': generation.to_dict()}), 200


@automation_bp.route('/automation/generations/<generation_id>/download', methods=['GET'])
def download_generation(generation_id):
    generation = AutomationGeneration.query.get(generation_id)
    project_id = request.args.get('project_id')
    if not generation or generation.status != 'completed' or generation.project_id != project_id:
        return jsonify({'error': 'Completed generation not found'}), 404
    path = Path(current_app.config['EXPORT_FOLDER']) / f'automation_{generation.id}.zip'
    if not path.exists():
        return jsonify({'error': 'Generated archive not found'}), 404
    return send_file(path, as_attachment=True, download_name=path.name)
