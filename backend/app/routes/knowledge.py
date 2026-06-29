import os
import uuid
from flask import Blueprint, request, jsonify, current_app
from werkzeug.utils import secure_filename
from app.models import db, KnowledgeFile, Project
from app.utils.file_utils import read_document_content

knowledge_bp = Blueprint('knowledge', __name__)

ALLOWED_KNOWLEDGE_EXT = {'md', 'txt', 'docx', 'doc'}

@knowledge_bp.route('/knowledge/upload', methods=['POST'])
def upload_knowledge():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    
    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in ALLOWED_KNOWLEDGE_EXT:
        return jsonify({'error': 'Invalid file type, only md/txt/docx/doc allowed'}), 400
    
    project_id = request.form.get('project_id')
    if not project_id:
        return jsonify({'error': 'Project ID required'}), 400
    
    project = Project.query.get(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    
    original_name = file.filename
    safe_name = secure_filename(file.filename)
    unique_filename = f"{uuid.uuid4()}_{safe_name}"
    upload_folder = current_app.config['UPLOAD_FOLDER']
    os.makedirs(upload_folder, exist_ok=True)
    file_path = os.path.join(upload_folder, unique_filename)
    file.save(file_path)
    file_size = os.path.getsize(file_path)
    
    content = ''
    if ext in ('docx', 'doc'):
        try:
            content = read_document_content(file_path)
        except Exception:
            content = ''
    else:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception:
            content = ''
    
    kf = KnowledgeFile(
        project_id=project_id,
        filename=unique_filename,
        original_name=original_name,
        file_path=file_path,
        content=content,
        file_size=file_size
    )
    db.session.add(kf)
    db.session.commit()
    
    return jsonify({'success': True, 'file': kf.to_dict()}), 201


@knowledge_bp.route('/knowledge/files', methods=['GET'])
def list_knowledge_files():
    project_id = request.args.get('project_id')
    if not project_id:
        return jsonify({'error': 'project_id required'}), 400
    
    files = KnowledgeFile.query.filter_by(project_id=project_id).order_by(KnowledgeFile.created_at.desc()).all()
    return jsonify({'files': [f.to_dict() for f in files]}), 200


@knowledge_bp.route('/knowledge/files/<file_id>', methods=['GET'])
def get_knowledge_file(file_id):
    kf = KnowledgeFile.query.get(file_id)
    if not kf:
        return jsonify({'error': 'File not found'}), 404
    return jsonify({'file': kf.to_dict(include_content=True)}), 200


@knowledge_bp.route('/knowledge/files/<file_id>', methods=['DELETE'])
def delete_knowledge_file(file_id):
    kf = KnowledgeFile.query.get(file_id)
    if not kf:
        return jsonify({'error': 'File not found'}), 404
    
    if os.path.exists(kf.file_path):
        try:
            os.remove(kf.file_path)
        except Exception:
            pass
    
    db.session.delete(kf)
    db.session.commit()
    return jsonify({'success': True}), 200
