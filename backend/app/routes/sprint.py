from flask import Blueprint, request, jsonify
from app.models import Sprint, TestCase, Image, PendingModule, db

sprint_bp = Blueprint('sprint', __name__)

@sprint_bp.route('/sprints', methods=['GET'])
def get_sprints():
    project_id = request.args.get('project_id')
    
    query = Sprint.query
    if project_id:
        query = query.filter_by(project_id=project_id)
    
    sprints = query.order_by(Sprint.created_at.desc()).all()
    return jsonify({'sprints': [s.to_dict() for s in sprints]}), 200

@sprint_bp.route('/sprints', methods=['POST'])
def create_sprint():
    data = request.get_json()
    project_id = data.get('project_id')
    name = data.get('name', '').strip()
    
    if not project_id or not name:
        return jsonify({'error': 'project_id and name required'}), 400
    
    existing = Sprint.query.filter_by(project_id=project_id, name=name).first()
    if existing:
        return jsonify({'sprint': existing.to_dict()}), 200
    
    sprint = Sprint(project_id=project_id, name=name)
    db.session.add(sprint)
    db.session.commit()
    
    return jsonify({'sprint': sprint.to_dict()}), 201

@sprint_bp.route('/sprints/<sprint_id>', methods=['PUT'])
def update_sprint(sprint_id):
    sprint = Sprint.query.get(sprint_id)
    if not sprint:
        return jsonify({'error': 'Sprint not found'}), 404
    data = request.get_json()
    name = data.get('name', '').strip()
    if name:
        existing = Sprint.query.filter_by(project_id=sprint.project_id, name=name).first()
        if existing and existing.id != sprint.id:
            return jsonify({'error': 'Sprint name already exists'}), 409
        sprint.name = name
        db.session.commit()
    return jsonify({'sprint': sprint.to_dict()}), 200

@sprint_bp.route('/sprints/<sprint_id>', methods=['DELETE'])
def delete_sprint(sprint_id):
    sprint = Sprint.query.get(sprint_id)
    if not sprint:
        return jsonify({'error': 'Sprint not found'}), 404
    try:
        TestCase.query.filter_by(sprint_id=sprint_id).delete()
        Image.query.filter_by(sprint_id=sprint_id).delete()
        PendingModule.query.filter_by(sprint_id=sprint_id).delete()
        db.session.delete(sprint)
        db.session.commit()
        return jsonify({'success': True}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500