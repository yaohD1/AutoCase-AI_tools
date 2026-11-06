# -*- coding: utf-8 -*-
"""模型服务配置接口。

前端只跟这里打交道，不直接读写 JSON。所有写操作都走 models_config 的原子写，
成功后统一返回最新的合并视图，省得前端再请求一次。
"""
from flask import Blueprint, jsonify, request

from app.services import models_config
from app.services.models_config import ModelConfigError

models_bp = Blueprint('models', __name__)


def _fail(exc):
    return jsonify({'error': str(exc)}), 400


def _view():
    view = models_config.merged_view()
    view['models'] = models_config.flat_models()
    view['refs'] = models_config.model_refs()
    return jsonify(view), 200


@models_bp.route('/models', methods=['GET'])
def list_models():
    """合并视图：全局 + 本地的 provider，带 source 和掩码后的 key。"""
    try:
        return _view()
    except ModelConfigError as exc:
        return _fail(exc)


@models_bp.route('/models/providers', methods=['POST'])
def create_provider():
    data = request.get_json(silent=True) or {}
    provider = (data.get('provider') or '').strip()
    if not provider:
        return jsonify({'error': '请填写服务名'}), 400
    try:
        models_config.upsert_provider(
            provider,
            npm=(data.get('npm') or '').strip(),
            base_url=(data.get('base_url') or '').strip(),
            api_key=(data.get('api_key') or '').strip() or None,
            name=(data.get('name') or '').strip() or None,
        )
        return _view()
    except ModelConfigError as exc:
        return _fail(exc)


@models_bp.route('/models/providers/<provider>', methods=['PUT'])
def update_provider(provider):
    data = request.get_json(silent=True) or {}
    try:
        models_config.upsert_provider(
            provider,
            npm=(data.get('npm') or '').strip(),
            base_url=(data.get('base_url') or '').strip(),
            # 留空表示不改，和旧页面的习惯一致。
            api_key=(data.get('api_key') or '').strip() or None,
            name=(data.get('name') or '').strip() or None,
        )
        return _view()
    except ModelConfigError as exc:
        return _fail(exc)


@models_bp.route('/models/providers/<provider>', methods=['DELETE'])
def drop_provider(provider):
    """删本地定义。全局带的 provider 会「还原」成全局原样，不会真的消失。"""
    try:
        models_config.delete_provider(provider)
        return _view()
    except ModelConfigError as exc:
        return _fail(exc)


@models_bp.route('/models/providers/<provider>/hidden', methods=['POST'])
def hide_provider(provider):
    data = request.get_json(silent=True) or {}
    try:
        models_config.set_provider_hidden(provider, bool(data.get('hidden')))
        return _view()
    except ModelConfigError as exc:
        return _fail(exc)


@models_bp.route('/models/providers/<provider>/models', methods=['POST'])
def create_model(provider):
    data = request.get_json(silent=True) or {}
    try:
        models_config.add_model(
            provider,
            data.get('model') or '',
            name=data.get('name'),
            temperature=data.get('temperature'),
            max_tokens=data.get('max_tokens'),
            vision=bool(data.get('vision', True)),
        )
        return _view()
    except ModelConfigError as exc:
        return _fail(exc)


@models_bp.route('/models/providers/<provider>/models/<path:model>', methods=['PATCH'])
def update_model(provider, model):
    data = request.get_json(silent=True) or {}
    try:
        models_config.update_model(
            provider, model,
            name=data.get('name'),
            temperature=data.get('temperature'),
            max_tokens=data.get('max_tokens'),
            vision=data.get('vision'),
        )
        return _view()
    except ModelConfigError as exc:
        return _fail(exc)


@models_bp.route('/models/providers/<provider>/models/<path:model>', methods=['DELETE'])
def drop_model(provider, model):
    try:
        models_config.remove_model(provider, model)
        return _view()
    except ModelConfigError as exc:
        return _fail(exc)


@models_bp.route('/models/default', methods=['PUT'])
def set_default_model():
    data = request.get_json(silent=True) or {}
    try:
        models_config.set_default((data.get('model') or '').strip())
        return _view()
    except ModelConfigError as exc:
        return _fail(exc)


@models_bp.route('/models/test', methods=['POST'])
def test_model():
    data = request.get_json(silent=True) or {}
    provider = (data.get('provider') or '').strip()
    try:
        result = models_config.test_connection(
            provider,
            model_id=(data.get('model') or '').strip() or None,
            api_key=(data.get('api_key') or '').strip() or None,
            base_url=(data.get('base_url') or '').strip() or None,
        )
        return jsonify(result), 200
    except ModelConfigError as exc:
        return _fail(exc)


@models_bp.route('/models/apply', methods=['POST'])
def apply_models():
    """重建运行时配置。

    正在跑的 opencode serve 不会热加载，这里只负责把合并文件重新写好并清掉旧的，
    serve 的重启交给下一次生成（每次生成都会重新起）。前端据此提示用户。
    """
    try:
        path = models_config.runtime_config()
        return jsonify({'success': True, 'runtime': str(path),
                        'hint': '正在运行的会话需要重新开始才会用新模型'}), 200
    except ModelConfigError as exc:
        return _fail(exc)
