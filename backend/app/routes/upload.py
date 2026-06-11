import os
import uuid
from flask import Blueprint, request, jsonify, current_app, send_file
from werkzeug.utils import secure_filename
from app.models import db, Image, Project

upload_bp = Blueprint('upload', __name__)

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in current_app.config['ALLOWED_EXTENSIONS']

@upload_bp.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    
    if not allowed_file(file.filename):
        return jsonify({'error': 'Invalid file type'}), 400
    
    project_id = request.form.get('project_id')
    if not project_id:
        return jsonify({'error': 'Project ID required'}), 400
    
    project = Project.query.get(project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    
    filename = secure_filename(file.filename)
    name, ext = os.path.splitext(filename)
    if ext and ext.lower() in ('.jpg', '.jpeg', '.png', '.webp'):
        unique_filename = f"{uuid.uuid4()}{ext}"
    else:
        unique_filename = f"{uuid.uuid4()}_{filename}"
    upload_folder = current_app.config['UPLOAD_FOLDER']
    
    if not os.path.exists(upload_folder):
        os.makedirs(upload_folder)
    
    file_path = os.path.join(upload_folder, unique_filename)
    file.save(file_path)
    
    file_size = os.path.getsize(file_path)
    
    image = Image(
        project_id=project_id,
        filename=unique_filename,
        file_path=file_path,
        file_size=file_size
    )
    db.session.add(image)
    db.session.commit()
    
    return jsonify({
        'success': True,
        'image': image.to_dict()
    }), 201

@upload_bp.route('/upload/<image_id>', methods=['DELETE'])
def delete_file(image_id):
    image = Image.query.get(image_id)
    if not image:
        return jsonify({'error': 'Image not found'}), 404
    
    if os.path.exists(image.file_path):
        os.remove(image.file_path)
    
    db.session.delete(image)
    db.session.commit()
    
    return jsonify({'success': True}), 200

@upload_bp.route('/upload/<image_id>', methods=['GET'])
def get_file(image_id):
    image = Image.query.get(image_id)
    if not image:
        return jsonify({'error': 'Image not found'}), 404
    
    return jsonify({'image': image.to_dict()}), 200


@upload_bp.route('/images/<filename>', methods=['GET'])
def serve_image(filename):
    image = Image.query.filter_by(filename=filename).first()
    if not image or not os.path.exists(image.file_path):
        return jsonify({'error': 'Image not found'}), 404
    return send_file(image.file_path, mimetype='image/jpeg')


@upload_bp.route('/documents/<filename>/content', methods=['GET'])
def serve_document_content(filename):
    from app.utils.file_utils import is_document, read_document_content
    image = Image.query.filter_by(filename=filename).first()
    if not image or not os.path.exists(image.file_path):
        return jsonify({'error': 'File not found'}), 404
    if not is_document(image.filename):
        return jsonify({'error': 'Not a document file'}), 400
    content = read_document_content(image.file_path)
    return jsonify({'content': content, 'filename': image.filename})