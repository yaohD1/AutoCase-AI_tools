"""录制生成的 HTTP 接口。

录制会话全局只有一个，不绑定项目；项目、工作区与目标路径在「生成」时才确定。
"""
import json
import logging
import os
from urllib.parse import urlencode, urlparse

from flask import Blueprint, current_app, jsonify, request

from app.services.automation_generator import AutomationScriptGenerator
from app.services.opencode_runner import OpenCodeRunner
from app.models import db
from app.models.recording_generation import RecordingGeneration
from app.services.recording import detect, emit, records, replay
from app.services.recording import intent, progress, verify
from app.services.recording.ask_pool import AskUnavailable
from app.services.recording.session import RecordingBusy, RecordingError, RecordingSession


recording_bp = Blueprint('recording', __name__)
logger = logging.getLogger(__name__)

# 解析进程要用的 OpenCode 启动参数：只传存在的那几项，其余走 OpenCodeRunner 的默认值。
OPENCODE_CONFIG_KEYS = ('OPENCODE_BIN', 'OPENCODE_CONFIG', 'OPENCODE_PURE', 'OPENCODE_SERVE_HOST')


def _opencode_config():
    return {key: current_app.config.get(key) for key in OPENCODE_CONFIG_KEYS
            if current_app.config.get(key) is not None}


def _ask_text(session, model, system, prompt):
    """断言解析的模型调用：优先用录制期常驻的进程，不可用就退回一次性的。

    常驻进程把 5 秒多的「进程首次调用」提前到录制开始时做掉了，这里只要 1.5–3 秒；
    退回路径慢（约 7 秒）但功能完全一样，所以常驻进程挂了也不影响用户把断言插进去。
    """
    pool = session.ask_pool() if session is not None else None
    if pool is not None and pool.is_ready():
        pool.note_model(model)
        try:
            return pool.ask(system, prompt)
        except AskUnavailable as exc:
            # 判死而不是留着重试：同一个坏进程再问一次只会让用户多等几秒。
            pool.fail(str(exc))
    return OpenCodeRunner(current_app.config).ask_text(model, system, prompt)


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
            # 断言解析器：录制一开始就在后台起并预热，开始录制时选的模型先用来热身。
            ask_model=body.get('model'),
            ask_config=_opencode_config(),
        )
    except RecordingBusy as exc:
        return _error(str(exc), 409)
    except RecordingError as exc:
        return _error('无法启动录制：%s' % exc, 500)
    # 新录制 = 新的生成任务：上一条的「已写入 / 失败」不该出现在这一条里。
    progress.PROGRESS.reset()
    return jsonify(session.snapshot()), 201


def _frontend_url(params):
    """拼前端录制页地址；地址本身可能已经带 query。"""
    base = (current_app.config.get('RECORDING_PANEL_URL') or '').strip()
    if not base:
        return None
    separator = '&' if '?' in base else '?'
    return base + separator + urlencode(params)


def _prefill(body):
    """把用户当前选的配置带给前端，独立 profile 里 localStorage 是空的。

    只带「开始录制前就可能填好」的项：目标文件、标题、Python 解释器也要带，
    否则录制窗口里这几项永远是空的——目标文件为空时生成按钮直接不可用。
    """
    values = {}
    for key in ('model', 'workspace', 'framework', 'target_path', 'title', 'python'):
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


@recording_bp.route('/automation/recording/close', methods=['POST'])
def close_recording_browser():
    """停止后关掉浏览器（录制结果保留）。停止本身不再关窗口，所以需要一个显式出口。"""
    return _mutate(lambda session: session.close_browser())


@recording_bp.route('/automation/recording/verify', methods=['POST'])
def verify_recording_intent():
    """插入前验证：在当前页面判断这条断言此刻是否成立。

    只读当前页面，不等待、不回放；结果要按「此刻」理解，界面上也必须这么标。
    """
    session = RecordingSession.current()
    if session is None:
        return _error('没有录制', 404)
    if not session.snapshot().get('browser_open'):
        return _error('浏览器已关闭，无法再看当前页面', 409)
    body = request.get_json(silent=True) or {}
    try:
        checked = intent.validate(body.get('intent'), session.aliases())
    except intent.IntentError as exc:
        return _error(str(exc), 400)
    try:
        result = session.verify_intent(checked)
    except RecordingError as exc:
        return _error(str(exc), 409)
    except verify.VerifyError as exc:
        return _error(str(exc), 422)
    return jsonify(result), 200


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


@recording_bp.route('/automation/recording/highlight', methods=['POST'])
def highlight_recording_step():
    """在浏览器里把这一步的匹配元素全部框出来并编号，供人眼确认。

    index 省略时用当前选中的那个（或录制时的序号）。每次调用都会重画，
    所以「换一个看看」就是带 index 再调一次。
    """
    body = request.get_json(silent=True) or {}
    if 'no' not in body:
        return _error('缺少 no', 400)
    index = body.get('index')
    if index is not None and not isinstance(index, int):
        return _error('index 必须是整数', 400)
    return _mutate(lambda session: session.highlight(
        int(body['no']), index=index, framework=body.get('framework') or 'playwright-ts'))


@recording_bp.route('/automation/recording/highlight/clear', methods=['POST'])
def clear_recording_highlight():
    return _mutate(lambda session: session.clear_highlight())


@recording_bp.route('/automation/recording/steps/<int:no>/chosen', methods=['POST'])
def choose_recording_target(no):
    """用户看过框之后指定「就是这个（第 index 个匹配）」。

    desc 必传：index 是某一条候选的匹配顺序，脱离 desc 的 index 没有意义。
    """
    body = request.get_json(silent=True) or {}
    desc = body.get('desc')
    index = body.get('index')
    if not desc or not isinstance(desc, str):
        return _error('缺少 desc', 400)
    if not isinstance(index, int) or index < 0:
        return _error('index 必须是非负整数', 400)
    return _mutate(lambda session: session.set_chosen(no, desc, index))


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


@recording_bp.route('/automation/recording/reload', methods=['POST'])
def add_recording_reload():
    """插入一步「刷新页面」。录制中会顺带真的刷新，让后续步骤落在刷新后的页面上。"""
    session = RecordingSession.current()
    if session is None:
        return _error('没有录制', 404)
    body = request.get_json(silent=True) or {}
    try:
        after_no = body.get('after_no')
        after_no = int(after_no) if after_no is not None else None
        return jsonify(session.add_reload_step(after_no)), 200
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
    try:
        parsed = intent.parse_intent(
            body.get('text') or '',
            session.aliases(),
            lambda system, prompt: _ask_text(session, model, system, prompt),
        )
    except intent.IntentError as exc:
        return _error(str(exc), 422)
    except (RuntimeError, ValueError) as exc:
        return _error('模型调用失败：%s' % exc, 502)
    return jsonify({'intent': parsed, 'model': model}), 200


@recording_bp.route('/automation/recording/plan', methods=['POST'])
def plan_recording():
    """只出任务书，不写任何文件。任务书由用户看完、改完，再走 /generate 执行。"""
    body = request.get_json(silent=True) or {}
    ctx, failed = _prepare_generation(body)
    if failed is not None:
        return failed
    previous = body.get('previous_plan')
    if previous is not None:
        if not isinstance(previous, dict):
            return _error('上一版任务书格式不对', 400)
        if len(json.dumps(previous, ensure_ascii=False)) > emit.MAX_PLAN_BYTES:
            return _error('上一版任务书过大', 400)
    if not progress.PROGRESS.start('plan'):
        return _error('已有生成任务正在进行，请等它结束', 409)
    try:
        result = emit.plan(
            ctx['runner'], ctx['workspace'], ctx['model'], ctx['steps'],
            ctx['framework'], ctx['target_path'], ctx['title'],
            python=body.get('python') or None,
            feedback=body.get('feedback') or [],
            previous_plan=previous,
            progress=progress.mark,
        )
        progress.PROGRESS.finish()
        return jsonify(result), 200
    except emit.EmitError as exc:
        progress.PROGRESS.fail(str(exc), exc.details)
        return jsonify({'error': str(exc), 'details': exc.details}), 422
    except Exception as exc:
        progress.PROGRESS.fail('规划失败：%s' % exc)
        raise


def _prepare_generation(body):
    """规划和写入共用的请求校验。返回 (上下文, 错误响应)，两者必有一个为 None。

    target_path 允许为空：为空表示「让规划按项目约定选一个」，规划结果里会给出
    具体路径，用户确认后再带着它走 /generate。
    """
    session = RecordingSession.current()
    if session is None:
        return None, _error('没有录制', 404)
    if session.status not in ('stopped', 'idle_timeout', 'closed'):
        return None, _error('请先停止录制', 409)
    model = (body.get('model') or '').strip()
    framework = (body.get('framework') or '').strip()
    target_path = (body.get('target_path') or '').strip()
    title = (body.get('title') or '').strip() or 'recorded flow'
    if '/' not in model:
        return None, _error('请先选择模型', 400)
    try:
        _, workspace = AutomationScriptGenerator(current_app.config)._resolve_workspace(body.get('workspace_path'))
    except ValueError as exc:
        return None, _error(str(exc), 400)
    if target_path:
        problem = emit.target_problem(workspace, target_path, framework)
        if problem:
            return None, _error(problem, 400)
    return {
        'session': session,
        'runner': OpenCodeRunner(current_app.config),
        'workspace': workspace,
        'model': model,
        'framework': framework,
        'target_path': target_path,
        'title': title,
        'steps': session.ir(),
    }, None


@recording_bp.route('/automation/recording/generate', methods=['POST'])
def generate_recording():
    """按已确认的任务书写入目标工作区。任务书是唯一入口：没带任务书直接拒绝。"""
    body = request.get_json(silent=True) or {}
    ctx, failed = _prepare_generation(body)
    if failed is not None:
        return failed
    plan = body.get('plan')
    if not isinstance(plan, dict):
        return _error('缺少任务书：请先生成任务书，再按它执行', 400)
    if len(json.dumps(plan, ensure_ascii=False)) > emit.MAX_PLAN_BYTES:
        return _error('任务书过大，请重新规划', 400)
    steps = ctx['steps']
    if not emit.plan_matches_steps(plan, steps):
        # 录制被改过之后，旧任务书里的 note 已经对不上步骤了；硬写只会漏步骤。
        return jsonify({'error': '录制步骤已变化，请重新生成任务书', 'details': ['plan_stale']}), 409

    # 路径以「用户确认过的」为准：配置里填了就用配置，没填就用任务书里那条。
    # 任务书本身可以改路径（用户在卡片上改），所以这里必须重新校验一次，
    # 不能因为它是从上一轮响应里回传的就当可信值。
    target_path = (ctx['target_path'] or str(plan.get('target_path') or '').strip()).replace('\\', '/')
    problem = emit.target_problem(ctx['workspace'], target_path, ctx['framework'])
    if problem:
        return _error('写入路径不可用：%s' % problem, 400)
    # 写手会同时看到 prompt 里的 target_path 和 plan.target_path，两处必须一致。
    plan = {**plan, 'target_path': target_path}
    target_abs = ctx['workspace'] / target_path
    # 生成前先保存原内容，未提交时回滚要用到。
    original = target_abs.read_bytes() if target_abs.is_file() else None
    # 主界面和录制面板都能点生成，服务端只放行一个：后点到的直接 409，
    # 而不是两个请求各自写一遍同一个文件。
    if not progress.PROGRESS.start('write'):
        return _error('已有生成任务正在进行，请等它结束', 409)
    try:
        result = emit.generate(
            ctx['runner'], ctx['workspace'], ctx['model'], steps,
            ctx['framework'], target_path, ctx['title'],
            python=(body.get('python') or None), overwrite=bool(body.get('overwrite')),
            plan=plan, plan_feedback=body.get('plan_feedback') or [],
            progress=progress.mark,
        )
        progress.mark('record')
        record = records.create(
            ctx['workspace'], target_path, ctx['framework'], ctx['title'], ctx['model'], original,
            target_abs.read_bytes(), steps, result.get('reply'),
        )
        result['generation_id'] = record.id
        progress.PROGRESS.finish(result)
        return jsonify(result), 200
    except emit.EmitError as exc:
        status = 409 if exc.details and str(exc.details[0]).startswith('conflict:') else 422
        if status == 409:
            # 目标文件已存在不是任务失败：用户还要选覆盖或改名，进度回到 idle，
            # 别让另一个窗口看到一条没有意义的「失败」。
            progress.PROGRESS.reset()
        else:
            # 失败也可能已经动过文件（写了一半、校验不过）。留一条可回滚的记录，
            # 否则覆盖掉的旧内容就再也拿不回来了——路径放给 AI 选之后，这一步是底线。
            record_id = _keep_failed_record(ctx, target_path, original)
            if record_id:
                exc.details = list(exc.details or []) + ['record:%s' % record_id]
            progress.PROGRESS.fail(str(exc), exc.details)
        return jsonify({'error': str(exc), 'details': exc.details}), status
    except Exception as exc:
        # 500 也要落到进度里：否则进度条会永远停在「正在写入」，用户只能刷新。
        progress.PROGRESS.fail('生成失败：%s' % exc)
        raise


def _keep_failed_record(ctx, target_path, original):
    """写入失败时也留一条记录，让用户能把原文件拿回来。

    只记「文件确实被动过」的情况：agent 一个字没写、原文件也没被覆盖时建记录，
    只会给记录页添一条没有内容的废数据。
    """
    target_abs = ctx['workspace'] / target_path
    current = target_abs.read_bytes() if target_abs.is_file() else None
    if current == original:
        return None
    try:
        record = records.create(
            ctx['workspace'], target_path, ctx['framework'], ctx['title'], ctx['model'],
            original, None, ctx['steps'], '', status='failed',
        )
        return record.id
    except Exception:
        logger.exception('录制生成失败后未能留下回滚记录：%s', target_path)
        return None


def _record_or_404(generation_id):
    return db.session.get(RecordingGeneration, generation_id)


@recording_bp.route('/automation/recording/replay', methods=['POST'])
def replay_recording():
    """在浏览器里把录制重跑一遍，验证定位是否仍然有效。

    校验的是「录制本身还能不能走通」，不是「生成的脚本对不对」，所以两个框架都能校验：
    RF 的 rf_* 候选本来就是用 Playwright 的 xpath= / css= 计数的，不需要 Selenium 环境。
    不生成代码、不写文件。
    """
    session = RecordingSession.current()
    if session is None:
        return _error('没有录制', 404)
    if session.status not in ('stopped', 'idle_timeout', 'closed'):
        return _error('请先停止录制再回放校验', 409)
    body = request.get_json(silent=True) or {}
    framework = (body.get('framework') or '').strip() or replay.PW_FRAMEWORK
    if framework not in (replay.PW_FRAMEWORK, replay.RF_FRAMEWORK):
        return _error('不支持的框架：%s' % framework, 400)
    steps = session.ir()
    if not steps:
        return _error('还没有录制任何步骤', 400)
    try:
        result = replay.replay_checked(steps, framework=framework,
                                       headless=bool(body.get('headless', True)))
    except Exception as exc:  # 浏览器起不来、页面打不开等环境问题
        return _error('回放无法执行：%s' % exc, 500)
    return jsonify(result), 200


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
