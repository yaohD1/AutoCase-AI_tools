from flask import Blueprint, request, jsonify, send_file, current_app
import os
import uuid
from app.models import db, TestCase, Project
from app.services.xmind_builder import XMindBuilder

export_bp = Blueprint('export', __name__)

@export_bp.route('/export', methods=['POST'])
def export_xmind():
    data = request.get_json()
    project_id = data.get('project_id')
    case_ids = data.get('case_ids', [])
    
    if not project_id:
        return jsonify({'error': 'project_id required'}), 400
    
    project = Project.query.get(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    
    if case_ids:
        testcases = TestCase.query.filter(TestCase.id.in_(case_ids)).all()
    else:
        testcases = TestCase.query.filter_by(project_id=project_id).all()
    
    if not testcases:
        return jsonify({'error': 'No testcases to export'}), 400
    
    export_folder = current_app.config['EXPORT_FOLDER']
    if not os.path.exists(export_folder):
        os.makedirs(export_folder)
    
    filename = f"{project.name}_{uuid.uuid4().hex[:8]}.xmind"
    filepath = os.path.join(export_folder, filename)
    
    builder = XMindBuilder(project.name)
    builder.build(testcases)
    builder.save(filepath)
    
    return jsonify({
        'success': True,
        'download_url': f'/api/download/{filename}',
        'filename': filename
    }), 200

@export_bp.route('/download/<filename>', methods=['GET'])
def download_file(filename):
    export_folder = current_app.config['EXPORT_FOLDER']
    filepath = os.path.join(export_folder, filename)
    
    if not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404
    
    return send_file(filepath, as_attachment=True, download_name=filename)