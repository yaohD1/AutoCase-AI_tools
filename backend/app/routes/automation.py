import json
import os
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from flask import Blueprint, current_app, jsonify, request, send_file

from app.models import AIConfig, AutomationConfig, AutomationGeneration, Project, TestCase, db
from app.services.automation_generator import AutomationScriptGenerator


automation_bp = Blueprint('automation', __name__)


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
        'locator_strategy': 'role',
        'action_timeout': 10000,
        'navigation_timeout': 30000,
        'expect_timeout': 5000,
        'naming_strategy': 'case',
        'overwrite_policy': 'reject',
        'ai_config_id': ''
    }


def _apply_config(config, data):
    allowed = {
        'framework', 'language', 'workspace_path', 'specs_path', 'base_url',
        'environment_name', 'browser', 'auth_state_path', 'locator_strategy',
        'action_timeout', 'navigation_timeout', 'expect_timeout',
        'naming_strategy', 'overwrite_policy', 'ai_config_id'
    }
    for key in allowed:
        if key in data:
            setattr(config, key, data[key])


def _validate_config(config, require_generation=False):
    if config.framework != 'playwright' or config.language != 'typescript':
        return 'Only Playwright + TypeScript is supported in this version'
    if config.browser not in ('chromium', 'firefox', 'webkit'):
        return 'browser must be chromium, firefox or webkit'
    if config.overwrite_policy not in ('reject', 'overwrite'):
        return 'overwrite_policy must be reject or overwrite'
    if not isinstance(config.workspace_path or '', str) or len(config.workspace_path or '') > 500:
        return 'workspace_path must be a string shorter than 500 characters'
    if not isinstance(config.specs_path or '', str) or len(config.specs_path or '') > 255:
        return 'specs_path must be a string shorter than 255 characters'
    if require_generation and not (config.workspace_path or '').strip():
        return 'workspace_path is required for generation'
    if require_generation:
        parsed = urlparse(config.base_url or '')
        if parsed.scheme not in ('http', 'https') or not parsed.netloc:
            return 'base_url must be a valid http or https URL'
    for field, minimum, maximum in (
        ('action_timeout', 1000, 120000),
        ('navigation_timeout', 1000, 180000),
        ('expect_timeout', 1000, 120000)
    ):
        value = getattr(config, field, None)
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            return f'{field} must be an integer between {minimum} and {maximum}'
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
    validation_error = _validate_config(config)
    if validation_error:
        return jsonify({'error': validation_error}), 400
    db.session.commit()
    return jsonify({'success': True, 'config': config.to_dict()}), 200


@automation_bp.route('/automation/generate', methods=['POST'])
def generate_automation_scripts():
    data = request.get_json() or {}
    project_id = data.get('project_id')
    testcase_ids = data.get('testcase_ids') or []
    if not project_id or not testcase_ids:
        return jsonify({'error': 'project_id and testcase_ids are required'}), 400
    if not isinstance(testcase_ids, list) or len(testcase_ids) > 100 or not all(isinstance(item, str) for item in testcase_ids):
        return jsonify({'error': 'testcase_ids must be a list of at most 100 string IDs'}), 400
    if len(testcase_ids) != len(set(testcase_ids)):
        return jsonify({'error': 'testcase_ids must not contain duplicates'}), 400

    project = Project.query.get(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    testcases = TestCase.query.filter(
        TestCase.project_id == project_id,
        TestCase.id.in_(testcase_ids),
        TestCase.status == 'approved'
    ).all()
    if len(testcases) != len(set(testcase_ids)):
        return jsonify({'error': 'Only approved testcases from the selected project can be generated'}), 400

    config = AutomationConfig.query.filter_by(project_id=project_id).first()
    if not config:
        config = AutomationConfig(project_id=project_id)
        db.session.add(config)
    if isinstance(data.get('config'), dict):
        _apply_config(config, data['config'])
    validation_error = _validate_config(config, require_generation=True)
    if validation_error:
        return jsonify({'error': validation_error}), 400

    ai_config = None
    if config.ai_config_id:
        ai_config = AIConfig.query.get(config.ai_config_id)
        if not ai_config:
            return jsonify({'error': 'Selected AI config was not found'}), 400
    if not ai_config:
        ai_config = AIConfig.query.filter_by(is_active=True).first()

    generation = AutomationGeneration(
        project_id=project_id,
        status='running',
        config_snapshot=json.dumps(config.to_dict(), ensure_ascii=False),
        testcase_ids=json.dumps([tc.id for tc in testcases], ensure_ascii=False)
    )
    db.session.add(generation)
    db.session.commit()

    try:
        files, warnings, zip_path = AutomationScriptGenerator(current_app.config).generate(
            project, testcases, config.to_dict(), ai_config, generation.id
        )
        generation.status = 'completed'
        generation.files = json.dumps(files, ensure_ascii=False)
        generation.warnings = json.dumps(warnings, ensure_ascii=False)
        generation.completed_at = datetime.utcnow()
        db.session.commit()
        result = generation.to_dict()
        result['download_url'] = f'/api/automation/generations/{generation.id}/download'
        result['zip_filename'] = os.path.basename(zip_path)
        return jsonify({'success': True, 'generation': result}), 201
    except FileExistsError as exc:
        generation.status = 'failed'
        generation.error = str(exc)
        db.session.commit()
        return jsonify({'error': str(exc), 'generation': generation.to_dict()}), 409
    except Exception as exc:
        current_app.logger.exception('Automation generation failed: %s', exc)
        db.session.rollback()
        generation.status = 'failed'
        generation.error = '脚本生成失败，请查看服务端日志'
        db.session.add(generation)
        db.session.commit()
        return jsonify({'error': generation.error, 'generation': generation.to_dict()}), 500


@automation_bp.route('/automation/generations', methods=['GET'])
def list_generations():
    project_id = request.args.get('project_id')
    if not project_id:
        return jsonify({'error': 'project_id required'}), 400
    generations = AutomationGeneration.query.filter_by(project_id=project_id).order_by(
        AutomationGeneration.created_at.desc()
    ).limit(20).all()
    return jsonify({'generations': [generation.to_dict() for generation in generations]}), 200


@automation_bp.route('/automation/generations/<generation_id>', methods=['GET'])
def get_generation(generation_id):
    generation = AutomationGeneration.query.get(generation_id)
    if not generation:
        return jsonify({'error': 'Generation not found'}), 404
    return jsonify({'generation': generation.to_dict()}), 200


@automation_bp.route('/automation/generations/<generation_id>/download', methods=['GET'])
def download_generation(generation_id):
    generation = AutomationGeneration.query.get(generation_id)
    if not generation or generation.status != 'completed':
        return jsonify({'error': 'Completed generation not found'}), 404
    path = Path(current_app.config['EXPORT_FOLDER']) / f'automation_{generation.id}.zip'
    if not path.exists():
        return jsonify({'error': 'Generated archive not found'}), 404
    return send_file(path, as_attachment=True, download_name=path.name)
