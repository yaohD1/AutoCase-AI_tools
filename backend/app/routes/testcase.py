import json
from flask import Blueprint, request, jsonify, current_app
from datetime import datetime
from app.models import db, TestCase, Image, Project, AIConfig
from app.services.case_generator import CaseGenerator

testcase_bp = Blueprint('testcase', __name__)

@testcase_bp.route('/projects', methods=['GET'])
def get_projects():
    projects = Project.query.order_by(Project.created_at.desc()).all()
    return jsonify({'projects': [p.to_dict() for p in projects]}), 200

@testcase_bp.route('/projects', methods=['POST'])
def create_project():
    data = request.get_json()
    name = data.get('name')
    description = data.get('description', '')
    
    if not name:
        return jsonify({'error': 'Project name required'}), 400

    name = name.strip()
    existing = Project.query.filter_by(name=name).first()
    if existing:
        return jsonify({'error': 'Project name already exists'}), 409

    project = Project(name=name, description=description)
    db.session.add(project)
    db.session.commit()
    
    return jsonify({'success': True, 'project': project.to_dict()}), 201

@testcase_bp.route('/projects/<project_id>', methods=['GET'])
def get_project(project_id):
    project = Project.query.get(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    
    return jsonify({'project': project.to_dict()}), 200

@testcase_bp.route('/generate', methods=['POST'])
def generate_testcases():
    data = request.get_json()
    image_id = data.get('image_id')
    project_id = data.get('project_id')
    provider = data.get('provider', 'deepseek')
    case_types = data.get('case_types', ['functional', 'ui', 'boundary', 'exception'])
    case_count = data.get('case_count', 10)
    description = data.get('description', '')
    
    if not image_id or not project_id:
        return jsonify({'error': 'image_id and project_id required'}), 400
    
    image = Image.query.get(image_id)
    if not image:
        return jsonify({'error': 'Image not found'}), 404
    
    ai_config = AIConfig.query.filter_by(provider=provider, is_active=True).first()
    if not ai_config:
        return jsonify({'error': f'AI config for {provider} not found'}), 404
    
    try:
        generator = CaseGenerator(ai_config)
        testcases = generator.generate(image.file_path, case_types, case_count, description)
        
        for case_data in testcases:
            testcase = TestCase(
                project_id=project_id,
                module=case_data.get('module', ''),
                test_point=case_data.get('test_point', ''),
                title=case_data.get('title', ''),
                priority=case_data.get('priority', 'P2'),
                preconditions=case_data.get('preconditions', ''),
                steps=json.dumps(case_data.get('steps', []), ensure_ascii=False),
                expected=case_data.get('expected', ''),
                case_type=case_data.get('case_type', 'functional'),
                image_source=image.filename,
                ai_provider=provider,
                status='pending'
            )
            db.session.add(testcase)
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'count': len(testcases)
        }), 201
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@testcase_bp.route('/cases', methods=['GET'])
def get_testcases():
    project_id = request.args.get('project_id')
    case_type = request.args.get('case_type')
    status = request.args.get('status')

    query = TestCase.query

    if project_id:
        query = query.filter_by(project_id=project_id)
    if case_type:
        query = query.filter_by(case_type=case_type)
    if status:
        query = query.filter_by(status=status)

    testcases = query.order_by(TestCase.created_at.desc()).all()
    
    pending_count = 0
    if project_id:
        pending_count = TestCase.query.filter_by(
            project_id=project_id, 
            status='pending'
        ).count()
    
    return jsonify({
        'testcases': [tc.to_dict() for tc in testcases],
        'pending_count': pending_count
    }), 200

@testcase_bp.route('/cases/<case_id>', methods=['GET'])
def get_testcase(case_id):
    testcase = TestCase.query.get(case_id)
    if not testcase:
        return jsonify({'error': 'Testcase not found'}), 404
    
    return jsonify({'testcase': testcase.to_dict()}), 200

@testcase_bp.route('/cases/<case_id>', methods=['PUT'])
def update_testcase(case_id):
    testcase = TestCase.query.get(case_id)
    if not testcase:
        return jsonify({'error': 'Testcase not found'}), 404
    
    data = request.get_json()
    
    for key, value in data.items():
        if hasattr(testcase, key):
            setattr(testcase, key, value)
    
    db.session.commit()
    
    return jsonify({'success': True, 'testcase': testcase.to_dict()}), 200

@testcase_bp.route('/cases/<case_id>', methods=['DELETE'])
def delete_testcase(case_id):
    testcase = TestCase.query.get(case_id)
    if not testcase:
        return jsonify({'error': 'Testcase not found'}), 404
    
    db.session.delete(testcase)
    db.session.commit()
    
    return jsonify({'success': True}), 200

@testcase_bp.route('/cases/batch-delete', methods=['POST'])
def batch_delete_testcases():
    data = request.get_json()
    ids = data.get('ids', [])
    
    if not ids:
        return jsonify({'error': 'No testcase ids provided'}), 400
    
    try:
        count = 0
        for id in ids:
            testcase = TestCase.query.get(id)
            if testcase:
                db.session.delete(testcase)
                count += 1
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'deleted_count': count,
            'message': f'Successfully deleted {count} testcases'
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@testcase_bp.route('/cases/pending', methods=['GET'])
def get_pending_testcases():
    project_id = request.args.get('project_id')

    query = TestCase.query.filter(
        TestCase.status.in_(['pending', 'failed'])
    )

    if project_id:
        query = query.filter_by(project_id=project_id)

    testcases = query.order_by(TestCase.created_at.desc()).all()

    images = []
    if project_id and testcases:
        filenames = list(set(tc.image_source for tc in testcases if tc.image_source))
        if filenames:
            project_images = Image.query.filter(
                Image.project_id == project_id,
                Image.filename.in_(filenames)
            ).all()
            images = [img.to_dict() for img in project_images]

    pending_count = TestCase.query.filter_by(project_id=project_id, status='pending').count() if project_id else 0
    failed_count = TestCase.query.filter_by(project_id=project_id, status='failed').count() if project_id else 0
    total_gen_count = TestCase.query.filter_by(project_id=project_id).filter(TestCase.ai_provider.isnot(None)).count() if project_id else 0

    return jsonify({
        'testcases': [tc.to_dict() for tc in testcases],
        'images': images,
        'counts': {
            'pending': pending_count,
            'failed': failed_count,
            'total_generated': total_gen_count
        }
    }), 200


@testcase_bp.route('/cases/<case_id>/approve', methods=['POST'])
def approve_testcase(case_id):
    testcase = TestCase.query.get(case_id)
    if not testcase:
        return jsonify({'error': 'Testcase not found'}), 404

    data = request.get_json()

    editable_fields = ['module', 'test_point', 'title', 'priority',
                       'preconditions', 'steps', 'expected', 'case_type']
    for field in editable_fields:
        if field in data:
            setattr(testcase, field, data[field])

    testcase.status = 'approved'
    testcase.updated_at = datetime.utcnow()

    db.session.commit()

    return jsonify({'success': True, 'testcase': testcase.to_dict()}), 200


@testcase_bp.route('/cases/<case_id>/fail', methods=['POST'])
def fail_testcase(case_id):
    testcase = TestCase.query.get(case_id)
    if not testcase:
        return jsonify({'error': 'Testcase not found'}), 404

    testcase.status = 'failed'
    testcase.updated_at = datetime.utcnow()

    db.session.commit()

    return jsonify({'success': True, 'testcase': testcase.to_dict()}), 200