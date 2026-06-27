import json
from flask import Blueprint, request, jsonify, current_app
from datetime import datetime
from app.models import db, TestCase, Image, Project, AIConfig, Sprint, PendingModule
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

@testcase_bp.route('/projects/<project_id>', methods=['PUT'])
def update_project(project_id):
    project = Project.query.get(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    data = request.get_json()
    name = data.get('name', '').strip()
    if name and name != project.name:
        existing = Project.query.filter_by(name=name).first()
        if existing:
            return jsonify({'error': 'Project name already exists'}), 409
        project.name = name
    if 'description' in data:
        project.description = data.get('description', '')
    db.session.commit()
    return jsonify({'project': project.to_dict()}), 200

@testcase_bp.route('/projects/<project_id>', methods=['DELETE'])
def delete_project(project_id):
    project = Project.query.get(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    try:
        sprints = Sprint.query.filter_by(project_id=project_id).all()
        for sprint in sprints:
            TestCase.query.filter_by(sprint_id=sprint.id).delete()
            Image.query.filter_by(sprint_id=sprint.id).delete()
            db.session.delete(sprint)
        TestCase.query.filter_by(project_id=project_id).delete()
        Image.query.filter_by(project_id=project_id).delete()
        PendingModule.query.filter_by(project_id=project_id).delete()
        db.session.delete(project)
        db.session.commit()
        return jsonify({'success': True}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@testcase_bp.route('/analyze-image', methods=['POST'])
def analyze_image():
    data = request.get_json()
    image_id = data.get('image_id')
    image_ids = data.get('image_ids')
    config_id = data.get('config_id') or data.get('provider')
    provider = data.get('provider', 'kimi')
    description = data.get('description', '')
    
    if not image_id and not image_ids:
        return jsonify({'error': 'image_id or image_ids required'}), 400
    
    ai_config = None
    if config_id:
        ai_config = AIConfig.query.get(config_id)
    if not ai_config:
        ai_config = AIConfig.query.filter_by(provider=provider, is_active=True).first()
    if not ai_config:
        return jsonify({'error': 'AI config not found'}), 404
    
    try:
        generator = CaseGenerator(ai_config)
        if image_ids and len(image_ids) > 1:
            paths = []
            for iid in image_ids:
                img = Image.query.get(iid)
                if not img:
                    return jsonify({'error': f'Image {iid} not found'}), 404
                paths.append(img.file_path)
            modules = generator.analyze_images(paths, description)
        else:
            iid = image_id or (image_ids[0] if image_ids else None)
            if not iid:
                return jsonify({'error': 'No image provided'}), 400
            image = Image.query.get(iid)
            if not image:
                return jsonify({'error': 'Image not found'}), 404
            modules = generator.analyze_image(image.file_path, description)
        return jsonify({'success': True, 'modules': modules}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@testcase_bp.route('/generate', methods=['POST'])
def generate_testcases():
    data = request.get_json()
    image_id = data.get('image_id')
    image_ids = data.get('image_ids')
    project_id = data.get('project_id')
    provider = data.get('provider', 'deepseek')
    config_id = data.get('config_id')
    case_types = data.get('case_types', ['functional', 'ui', 'boundary', 'exception'])
    case_count = data.get('case_count', 10)
    description = data.get('description', '')
    modules = data.get('modules', None)
    smart_mode = data.get('smart_mode', False)
    sprint_id = data.get('sprint_id', '')
    
    if not project_id:
        return jsonify({'error': 'project_id required'}), 400
    if not image_id and not modules and not image_ids:
        return jsonify({'error': 'image_id, image_ids or modules required'}), 400
    
    image_paths = []
    first_image = None
    if image_id:
        first_image = Image.query.get(image_id)
        if first_image:
            image_paths.append(first_image.file_path)
    if image_ids:
        for iid in image_ids:
            img = Image.query.get(iid)
            if img:
                image_paths.append(img.file_path)
                if not first_image:
                    first_image = img
    
    ai_config = None
    if config_id:
        ai_config = AIConfig.query.get(config_id)
    if not ai_config:
        ai_config = AIConfig.query.filter_by(provider=provider, is_active=True).first()
    if not ai_config:
        return jsonify({'error': 'AI config not found'}), 404
    
    try:
        generator = CaseGenerator(ai_config)
        is_smart = smart_mode or not case_types or len(case_types) == 0
        if modules:
            testcases = generator.generate_from_modules(modules, case_types, case_count, description, is_smart, image_paths if image_paths else None)
        else:
            if not image_paths:
                return jsonify({'error': 'No valid image found'}), 400
            testcases = generator.generate(image_paths[0], case_types, case_count, description)
        
        image_source_val = ''
        if modules and len(modules) > 0:
            mid = modules[0].get('image_id')
            if mid:
                img = Image.query.get(mid)
                if img:
                    image_source_val = img.filename
        if not image_source_val and image_paths:
            img = Image.query.filter_by(file_path=image_paths[0]).first()
            if img:
                image_source_val = img.filename
        
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
                image_source=image_source_val,
                ai_provider=provider,
                status='pending',
                sprint_id=sprint_id
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
    sprint_id = request.args.get('sprint_id')

    query = TestCase.query

    if project_id:
        query = query.filter_by(project_id=project_id)
    if case_type:
        query = query.filter_by(case_type=case_type)
    if status:
        query = query.filter_by(status=status)
    if sprint_id:
        query = query.filter_by(sprint_id=sprint_id)

    testcases = query.order_by(TestCase.created_at.desc()).all()
    
    pending_count = 0
    if project_id:
        pq = TestCase.query.filter_by(project_id=project_id, status='pending')
        if sprint_id:
            pq = pq.filter_by(sprint_id=sprint_id)
        pending_count = pq.count()
    
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


@testcase_bp.route('/pending-modules/approved', methods=['GET'])
def get_approved_modules():
    project_id = request.args.get('project_id')
    sprint_id = request.args.get('sprint_id')
    
    query = PendingModule.query.filter_by(status='approved')
    if project_id:
        query = query.filter_by(project_id=project_id)
    if sprint_id:
        query = query.filter_by(sprint_id=sprint_id)
    
    modules = query.order_by(PendingModule.created_at.desc()).all()
    result = []
    for m in modules:
        d = m.to_dict()
        if m.image_id:
            img = Image.query.get(m.image_id)
            d['image_filename'] = img.filename if img else None
        else:
            d['image_filename'] = None
        result.append(d)
    return jsonify({'modules': result}), 200


@testcase_bp.route('/pending-modules', methods=['GET'])
def get_pending_modules():
    project_id = request.args.get('project_id')
    sprint_id = request.args.get('sprint_id')
    
    query = PendingModule.query.filter_by(status='pending')
    if project_id:
        query = query.filter_by(project_id=project_id)
    if sprint_id:
        query = query.filter_by(sprint_id=sprint_id)
    
    modules = query.order_by(PendingModule.created_at.asc()).all()
    result = []
    for m in modules:
        d = m.to_dict()
        if m.image_id:
            img = Image.query.get(m.image_id)
            d['image_filename'] = img.filename if img else None
        else:
            d['image_filename'] = None
        result.append(d)
    return jsonify({'modules': result}), 200


@testcase_bp.route('/pending-modules/<module_id>/approve', methods=['POST'])
def approve_pending_module(module_id):
    mod = PendingModule.query.get(module_id)
    if not mod:
        return jsonify({'error': 'Module not found'}), 404
    
    data = request.get_json()
    if data:
        for key in ['module', 'ui_elements', 'function_description', 'interaction_flow', 'test_focus', 'case_types', 'case_count', 'smart_mode']:
            if key in data:
                setattr(mod, key, data[key])
    
    mod.status = 'approved'
    mod.updated_at = datetime.utcnow()
    db.session.commit()
    
    return jsonify({'success': True, 'module': mod.to_dict()}), 200


@testcase_bp.route('/pending-modules/<module_id>/fail', methods=['POST'])
def fail_pending_module(module_id):
    mod = PendingModule.query.get(module_id)
    if not mod:
        return jsonify({'error': 'Module not found'}), 404
    
    mod.status = 'failed'
    mod.updated_at = datetime.utcnow()
    db.session.commit()
    
    return jsonify({'success': True, 'module': mod.to_dict()}), 200


@testcase_bp.route('/pending-modules', methods=['POST'])
def save_pending_modules():
    data_list = request.get_json()
    if not data_list or not isinstance(data_list, list):
        return jsonify({'error': 'Array of modules required'}), 400
    
    saved = []
    for d in data_list:
        mod = PendingModule(
            project_id=d.get('project_id', ''),
            sprint_id=d.get('sprint_id', ''),
            image_id=d.get('image_id', ''),
            module=d.get('module', ''),
            ui_elements=d.get('ui_elements', ''),
            function_description=d.get('function_description', ''),
            interaction_flow=d.get('interaction_flow', ''),
            test_focus=d.get('test_focus', ''),
            case_types=d.get('case_types', ''),
            case_count=d.get('case_count', 10),
            smart_mode=d.get('smart_mode', False),
            status='pending'
        )
        db.session.add(mod)
        saved.append(mod)
    
    db.session.commit()
    return jsonify({'success': True, 'count': len(saved)}), 201

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


@testcase_bp.route('/pending-modules/batch-delete', methods=['POST'])
def batch_delete_pending_modules():
    data = request.get_json()
    ids = data.get('ids', [])
    if not ids:
        return jsonify({'error': 'No module ids provided'}), 400
    try:
        count = 0
        for mid in ids:
            mod = PendingModule.query.get(mid)
            if mod:
                db.session.delete(mod)
                count += 1
        db.session.commit()
        return jsonify({'success': True, 'deleted_count': count}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@testcase_bp.route('/cases/pending', methods=['GET'])
def get_pending_testcases():
    project_id = request.args.get('project_id')
    sprint_id = request.args.get('sprint_id')

    query = TestCase.query.filter_by(status='pending')

    if project_id:
        query = query.filter_by(project_id=project_id)
    if sprint_id:
        query = query.filter_by(sprint_id=sprint_id)

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
        if not images:
            images = [img.to_dict() for img in Image.query.filter_by(project_id=project_id).all()]

    stats_query = TestCase.query
    if project_id:
        stats_query = stats_query.filter_by(project_id=project_id)
    if sprint_id:
        stats_query = stats_query.filter_by(sprint_id=sprint_id)

    pending_count = stats_query.filter_by(status='pending').count() if project_id else 0
    failed_count = stats_query.filter_by(status='failed').count() if project_id else 0
    total_gen_count = stats_query.filter(TestCase.ai_provider.isnot(None)).count() if project_id else 0

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