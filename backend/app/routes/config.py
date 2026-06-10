from flask import Blueprint, request, jsonify
from app.models import db, AIConfig

config_bp = Blueprint('config', __name__)

@config_bp.route('/ai-configs', methods=['GET'])
def get_ai_configs():
    configs = AIConfig.query.all()
    return jsonify({'configs': [c.to_dict() for c in configs]}), 200

@config_bp.route('/ai-configs', methods=['POST'])
def create_ai_config():
    data = request.get_json()
    provider = data.get('provider')
    api_key = data.get('api_key')
    api_base = data.get('api_base')
    model = data.get('model')
    temperature = data.get('temperature', 0.7)
    max_tokens = data.get('max_tokens', 2000)
    
    if not provider or not api_key:
        return jsonify({'error': 'provider and api_key required'}), 400
    
    existing = AIConfig.query.filter_by(provider=provider).first()
    if existing:
        return jsonify({'error': 'Config for this provider already exists'}), 400
    
    config = AIConfig(
        provider=provider,
        api_key=api_key,
        api_base=api_base,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens
    )
    db.session.add(config)
    db.session.commit()
    
    return jsonify({'success': True, 'config': config.to_dict()}), 201

@config_bp.route('/ai-configs/<config_id>', methods=['PUT'])
def update_ai_config(config_id):
    config = AIConfig.query.get(config_id)
    if not config:
        return jsonify({'error': 'Config not found'}), 404
    
    data = request.get_json()
    
    if 'api_key' in data:
        config.api_key = data['api_key']
    if 'api_base' in data:
        config.api_base = data['api_base']
    if 'model' in data:
        config.model = data['model']
    if 'temperature' in data:
        config.temperature = data['temperature']
    if 'max_tokens' in data:
        config.max_tokens = data['max_tokens']
    if 'is_active' in data:
        config.is_active = data['is_active']
    
    db.session.commit()
    
    return jsonify({'success': True, 'config': config.to_dict()}), 200

@config_bp.route('/ai-configs/<config_id>', methods=['DELETE'])
def delete_ai_config(config_id):
    config = AIConfig.query.get(config_id)
    if not config:
        return jsonify({'error': 'Config not found'}), 404
    
    db.session.delete(config)
    db.session.commit()
    
    return jsonify({'success': True}), 200