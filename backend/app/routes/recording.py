"""录制生成的 HTTP 接口。

录制会话全局只有一个，不绑定项目；项目、工作区与目标路径在「生成」时才确定。
"""
import os
from urllib.parse import urlencode, urlparse

from flask import Blueprint, current_app, jsonify, request

from app.services.automation_generator import AutomationScriptGenerator
from app.services.opencode_runner import OpenCodeRunner
from app.models import db
from app.models.recording_generation import RecordingGeneration
from app.services.recording import detect, emit, records
from app.services.recording import intent
from app.services.recording.session import RecordingBusy, RecordingError, RecordingSession


recording_bp = Blueprint('recording', __name__)


def _error(message, status):
    return jsonify({'error': message}), status


def _mutate(call):
    session = RecordingSession.current()
    if session is None:
        return _error('没有录制', 404)
    try:
        return jsonify(call(session)), 200
    except RecordingError as exc:
        return _error(str(exc), 409)


@recording_bp.route('/automation/recording/start', methods=['POST'])
def start_recording():
    body = request.get_json(silent=True) or {}
    base_url = (body.get('base_url') or '').strip()
    parsed = urlparse(base_url)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        return _error('base_url 必须是 http 或 https 地址', 400)
    headless = bool(current_app.config.get('RECORDING_HEADLESS', os.environ.get('RECORDING_HEADLESS') == '1'))
    layout = (current_app.config.get('RECORDING_LAYOUT') or 'embed').strip().lower()
    try:
        session = RecordingSession.start_new(
            base_url,
            headless=headless,
            embed_url=_embed_url(body, base_url) if layout == 'embed' else None,
            panel_url=_panel_url(body, base_url) if layout == 'split' else None,
            panel_ratio=current_app.config.get('RECORDING_PANEL_RATIO', 0.45),
            hud_panel=bool(current_app.config.get('RECORDING_HUD_PANEL', False)),
        )
    except RecordingBusy as exc:
        return _error(str(exc), 409)
    except RecordingError as exc:
        return _error('无法启动录制：%s' % exc, 500)
    return jsonify(session.snapshot()), 201


def _frontend_url(params):
    """拼前端录制页地址；地址本身可能已经带 query。"""
    base = (current_app.config.get('RECORDING_PANEL_URL') or '').strip()
    if not base:
        return None
    separator = '&' if '?' in base else '?'
    return base + separator + urlencode(params)


def _prefill(body):
    """把用户当前选的模型/工作区/框架带给前端，独立 profile 里 localStorage 是空的。"""
    values = {}
    for key in ('model', 'workspace', 'framework'):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            values[key] = value.strip()
    return values


def _embed_url(body, base_url):
    """嵌入模式：外壳页把被测系统放进 iframe，左边页面、右边编排。"""
    params = {'embed': '1', 'target': base_url}
    params.update(_prefill(body))
    return _frontend_url(params)


def _panel_url(body, base_url):
    """并排模式：额外开一个面板窗口。"""
    params = {'panel': '1', 'base_url': base_url}
    params.update(_prefill(body))
    return _frontend_url(params)


@recording_bp.route('/automation/recording/panel/relayout', methods=['POST'])
def relayout_panel():
    """把被测浏览器与编排面板重新并排（用户挪动窗口后用）。"""
    session = RecordingSession.current()
    if session is None:
        return _error('没有录制', 404)
    try:
        result = session.relayout()
    except RecordingError as exc:
        return _error(str(exc), 409)
    return jsonify({'laid_out': bool(result.get('laid_out'))}), 200


@recording_bp.route('/automation/recording/current', methods=['GET'])
def current_recording():
    session = RecordingSession.current()
    if session is None:
        return _error('没有录制', 404)
    return jsonify(session.snapshot()), 200


@recording_bp.route('/automation/recording/events', methods=['GET'])
def recording_events():
    session = RecordingSession.current()
    if session is None:
        return _error('没有录制', 404)
    try:
        after = int(request.args.get('after', -1))
    except ValueError:
        after = -1
    events = session.events_after(after)
    cursor = events[-1]['seq'] if events else after
    return jsonify({'events': events, 'cursor': cursor, 'status': session.status}), 200


@recording_bp.route('/automation/recording/pick', methods=['POST'])
def set_pick_mode():
    body = request.get_json(silent=True) or {}
    return _mutate(lambda session: session.set_pick(bool(body.get('on'))))


@recording_bp.route('/automation/recording/stop', methods=['POST'])
def stop_recording():
    return _mutate(lambda session: session.stop())


@recording_bp.route('/automation/recording/steps/<int:no>', methods=['DELETE'])
def delete_recording_step(no):
    return _mutate(lambda session: session.remove_step(no))


@recording_bp.route('/automation/recording/steps/<int:no>', methods=['PATCH'])
def update_recording_step(no):
    """就地修改一步的值（输入值 / 选项 / 地址 / 等待秒数），不动定位。"""
    body = request.get_json(silent=True) or {}
    return _mutate(lambda session: session.update_step(no, body))


@recording_bp.route('/automation/recording/steps/<int:no>/order', methods=['POST'])
def reorder_recording_step(no):
    """拖拽排序：position 是「移除自身之后」的 0 基下标（与前端拖拽库一致）。"""
    body = request.get_json(silent=True) or {}
    if 'position' not in body:
        return _error('缺少 position', 400)
    return _mutate(lambda session: session.move_step_to(no, body.get('position')))


@recording_bp.route('/automation/recording/pause', methods=['POST'])
def pause_recording():
    """暂停 / 继续录制：浏览器保持可用，暂停期间的操作不记为步骤。"""
    session = RecordingSession.current()
    if session is None:
        return _error('没有录制', 404)
    body = request.get_json(silent=True) or {}
    if 'paused' not in body:
        return _error('缺少 paused', 400)
    return _mutate(lambda s: s.set_pause(bool(body.get('paused'))))


@recording_bp.route('/automation/recording/steps', methods=['POST'])
def add_recording_step():
    """断言 / 等待 / 睡眠。意图可以来自 LLM 解析，也可以来自结构化表单，两者用同一套校验。"""
    session = RecordingSession.current()
    if session is None:
        return _error('没有录制', 404)
    body = request.get_json(silent=True) or {}
    try:
        checked = intent.validate(body.get('intent'), session.aliases())
        after_no = body.get('after_no')
        after_no = int(after_no) if after_no is not None else None
        return jsonify(session.add_intent_step(checked, text=body.get('text'), after_no=after_no)), 200
    except intent.IntentError as exc:
        return _error(str(exc), 400)
    except (RecordingError, TypeError, ValueError) as exc:
        return _error(str(exc), 400)


@recording_bp.route('/automation/recording/intent', methods=['POST'])
def parse_recording_intent():
    """用所选模型把自然语言描述解析为意图。只返回预览，不写入录制。"""
    session = RecordingSession.current()
    if session is None:
        return _error('没有录制', 404)
    body = request.get_json(silent=True) or {}
    model = (body.get('model') or '').strip()
    if '/' not in model:
        return _error('请先选择模型；不选模型时请使用结构化表单', 400)
    runner = OpenCodeRunner(current_app.config)
    try:
        parsed = intent.parse_intent(
            body.get('text') or '',
            session.aliases(),
            lambda system, prompt: runner.ask_text(model, system, prompt),
        )
    except intent.IntentError as exc:
        return _error(str(exc), 422)
    except (RuntimeError, ValueError) as exc:
        return _error('模型调用失败：%s' % exc, 502)
    return jsonify({'intent': parsed, 'model': model}), 200


@recording_bp.route('/automation/recording/generate', methods=['POST'])
def generate_recording():
    """把已停止的录制写入目标工作区。成功返回写入的文件；失败已还原工作区并返回具体错误。"""
    session = RecordingSession.current()
    if session is None:
        return _error('没有录制', 404)
    if session.status not in ('stopped', 'idle_timeout', 'closed'):
        return _error('请先停止录制再生成', 409)
    body = request.get_json(silent=True) or {}
    model = (body.get('model') or '').strip()
    framework = (body.get('framework') or '').strip()
    target_path = (body.get('target_path') or '').strip()
    title = (body.get('title') or '').strip() or 'recorded flow'
    if '/' not in model:
        return _error('请先选择模型', 400)
    try:
        _, workspace = AutomationScriptGenerator(current_app.config)._resolve_workspace(body.get('workspace_path'))
    except ValueError as exc:
        return _error(str(exc), 400)
    if not emit.target_ok(target_path):
        return _error('目标路径无效：必须是工作区内的相对路径，不能含 ..', 400)
    target_abs = workspace / target_path
    # 生成前先保存原内容，未提交时回滚要用到。
    original = target_abs.read_bytes() if target_abs.is_file() else None
    steps = session.ir()
    runner = OpenCodeRunner(current_app.config)
    try:
        result = emit.generate(
            runner, workspace, model, steps, framework, target_path, title,
            python=(body.get('python') or None), overwrite=bool(body.get('overwrite')),
        )
    except emit.EmitError as exc:
        status = 409 if exc.details and str(exc.details[0]).startswith('conflict:') else 422
        return jsonify({'error': str(exc), 'details': exc.details}), status
    record = records.create(
        workspace, target_path, framework, title, model, original,
        target_abs.read_bytes(), steps, result.get('reply'),
    )
    result['generation_id'] = record.id
    return jsonify(result), 200


def _record_or_404(generation_id):
    return db.session.get(RecordingGeneration, generation_id)


@recording_bp.route('/automation/recording/generations', methods=['GET'])
def list_recording_generations():
    rows = RecordingGeneration.query.order_by(RecordingGeneration.created_at.desc()).limit(50).all()
    return jsonify({'generations': [row.to_dict() for row in rows]}), 200


@recording_bp.route('/automation/recording/generations/<generation_id>', methods=['GET'])
def get_recording_generation(generation_id):
    record = _record_or_404(generation_id)
    if record is None:
        return _error('没有这条录制生成记录', 404)
    return jsonify({**record.to_dict(), **records.preview(record)}), 200


@recording_bp.route('/automation/recording/generations/<generation_id>/commit', methods=['POST'])
def commit_recording_generation(generation_id):
    record = _record_or_404(generation_id)
    if record is None:
        return _error('没有这条录制生成记录', 404)
    body = request.get_json(silent=True) or {}
    try:
        records.commit(record, body.get('message'))
    except records.RecordError as exc:
        return _error(str(exc), 409)
    return jsonify(record.to_dict()), 200


@recording_bp.route('/automation/recording/generations/<generation_id>/revert', methods=['POST'])
def revert_recording_generation(generation_id):
    record = _record_or_404(generation_id)
    if record is None:
        return _error('没有这条录制生成记录', 404)
    try:
        records.revert(record)
    except records.RecordError as exc:
        return _error(str(exc), 409)
    return jsonify(record.to_dict()), 200


@recording_bp.route('/automation/recording/detect', methods=['GET'])
def detect_workspace():
    """读取目标工作区的框架、Python 环境与库版本，只读，供页面预填。"""
    try:
        _, workspace = AutomationScriptGenerator(current_app.config)._resolve_workspace(
            request.args.get('workspace_path'))
    except ValueError as exc:
        return _error(str(exc), 400)
    if not workspace.is_dir():
        return _error('工作区目录不存在：%s' % workspace, 404)
    return jsonify(detect.detect(workspace, python=request.args.get('python') or None)), 200


@recording_bp.route('/automation/recording/steps/<int:no>/move', methods=['POST'])
def move_recording_step(no):
    body = request.get_json(silent=True) or {}
    direction = body.get('direction')
    if direction not in ('up', 'down'):
        return _error('direction 只能是 up 或 down', 400)
    return _mutate(lambda session: session.move_step(no, direction))
