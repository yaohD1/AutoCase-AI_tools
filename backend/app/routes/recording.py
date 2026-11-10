"""录制生成的 HTTP 接口。

录制会话全局只有一个，不绑定项目；项目、工作区与目标路径在「生成」时才确定。
"""
import json
import logging
import os
from pathlib import Path
from urllib.parse import urlencode, urlparse

from flask import Blueprint, Response, current_app, jsonify, request

from app.services.automation_generator import AutomationScriptGenerator
from app.services.opencode_runner import OpenCodeRunner
from app.models import db
from app.models.recording_generation import RecordingGeneration
from app.services.recording import detect, embedding, emit, guard, records, replay
from app.services.recording import emit_session, intent, memory, progress, verify
from app.services.recording.ask_pool import AskUnavailable
from app.services.recording.session import RecordingBusy, RecordingError, RecordingSession


recording_bp = Blueprint('recording', __name__)
logger = logging.getLogger(__name__)

# 解析进程要用的 OpenCode 启动参数：只传存在的那几项，其余走 OpenCodeRunner 的默认值。
OPENCODE_CONFIG_KEYS = ('OPENCODE_BIN', 'OPENCODE_CONFIG', 'OPENCODE_PURE', 'OPENCODE_SERVE_HOST')


def _emit_key(session):
    """生成会话的 key：一次录制对应一条 opencode session。

    用录制会话的 id 而不是工作区：决策是「不跨录制复用」，同一条录制里
    规划、重规划、写入、修复才是同一件事。
    """
    return str(getattr(session, 'id', '') or '') or None


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
    notice = None
    if layout == 'embed':
        # 站点写了 X-Frame-Options / CSP frame-ancestors 时，iframe 只会得到一张
        # 「拒绝了我们的连接请求」错误页，而且用户还能在错误页上点出步骤来。
        # 与其让他对着错误页调试，不如直接换成独立窗口布局。
        blocked = embedding.frame_block_reason(
            base_url, shell_origin=current_app.config.get('RECORDING_PANEL_URL'))
        if blocked:
            layout = 'split'
            notice = ('被测系统禁止被 iframe 嵌入（%s），已改用两个并排窗口：'
                      '被测页面在左、编排面板在右（面板可收起，收起后屏幕右缘留一条把手）。'
                      % blocked)
    try:
        session = RecordingSession.start_new(
            base_url,
            headless=headless,
            embed_url=_embed_url(body, base_url) if layout == 'embed' else None,
            panel_url=_panel_url(body, base_url) if layout == 'split' else None,
            panel_ratio=current_app.config.get('RECORDING_PANEL_RATIO', 0.45),
            panel_float=bool(current_app.config.get('RECORDING_PANEL_FLOAT', False)),
            # 宽度跟着前端走：嵌入模式那块面板当前用多宽（localStorage 记忆值），
            # 并排的窗口就用多宽，两种布局看到的面板尺寸才一样。
            panel_width=_panel_width(body) or current_app.config.get('RECORDING_PANEL_WIDTH', 520),
            hud_panel=bool(current_app.config.get('RECORDING_HUD_PANEL', False)),
            # 断言解析器：录制一开始就在后台起并预热，开始录制时选的模型先用来热身。
            ask_model=body.get('model'),
            ask_config=_opencode_config(),
            # 动作库按项目存：同一个工作区下次录制能直接看到上次封装的动作。
            workspace=_recording_workspace(body),
            # 这次录制要按哪个框架生成：之后所有定位 / 规划 / 生成都以它为兜底，
            # 不再各自默认 playwright-ts（面板丢了参数也不会静默换写法）。
            framework=(body.get('framework') or '').strip() or None,
        )
    except RecordingBusy as exc:
        return _error(str(exc), 409)
    except RecordingError as exc:
        return _error('无法启动录制：%s' % exc, 500)
    # 新录制 = 新的生成任务：上一条的「已写入 / 失败」不该出现在这一条里。
    progress.PROGRESS.reset()
    # 上一条录制的生成会话也不能继承：规划上下文只对当次录制有效。
    emit_session.REGISTRY.close_all()
    # 项目画像：录制一开始就在后台学。用户录完点「生成」时画像已经好了，
    # 规划和写手都不用再现场读项目。学不成不影响录制，所以整段吞掉。
    _prime_project_memory(body)
    data = session.snapshot()
    if notice:
        # 布局被自动换掉必须让用户知道，否则他会奇怪为什么弹出两个窗口。
        data['notice'] = notice
    return jsonify(data), 201


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


def _panel_width(body):
    """前端带上来的面板宽度（CSS 像素）。超出范围就当作没传，用配置兜底。"""
    try:
        value = int(body.get('panel_width') or 0)
    except (TypeError, ValueError):
        return None
    return value if 320 <= value <= 900 else None


@recording_bp.route('/automation/recording/panel/mode', methods=['POST'])
def set_panel_mode():
    """展开 / 收起面板窗口。

    收起是「把整扇窗口挪到屏幕右缘外，只留一条把手」而不是缩小：Chromium 有最小
    窗口尺寸（实测 514 CSS），缩不出胶囊。把手就是面板自己被裁剩的那一条，点它展开。
    """
    session = RecordingSession.current()
    if session is None:
        return _error('没有录制', 404)
    body = request.get_json(silent=True) or {}
    try:
        result = session.set_panel_mode(body.get('mode'))
    except RecordingError as exc:
        return _error(str(exc), 409)
    return jsonify(result), 200


@recording_bp.route('/automation/recording/panel/width', methods=['POST'])
def set_panel_width():
    """改面板宽度（CSS 像素）。

    嵌入模式改宽度是前端自己改 CSS；并排模式那扇窗口在前端够不着的地方，
    只能回过头来让后端重摆——「收起行 / 展开行」两边都要跟着变，才叫一致。
    """
    session = RecordingSession.current()
    if session is None:
        return _error('没有录制', 404)
    body = request.get_json(silent=True) or {}
    try:
        result = session.set_panel_width(body.get('width'))
    except RecordingError as exc:
        return _error(str(exc), 409)
    return jsonify(result), 200


@recording_bp.route('/automation/recording/panel/sheet', methods=['POST'])
def focus_panel_sheet():
    """任务书浮层开 / 关时调整面板窗口：开着铺宽读文档，关掉摆回并排。

    窗口调不动不算错误（嵌入模式、系统拒绝、用户已经关了窗口）：
    前端只是拿不到更宽的地方，浮层仍然在窗口里正常显示。
    """
    session = RecordingSession.current()
    if session is None:
        return _error('没有录制', 404)
    body = request.get_json(silent=True) or {}
    try:
        result = session.focus_sheet(bool(body.get('open', True)))
    except RecordingError as exc:
        return _error(str(exc), 409)
    return jsonify({'focused': bool(result.get('focused'))}), 200


@recording_bp.route('/automation/recording/goto-doc', methods=['POST'])
def goto_step_doc():
    """回到某一步录制时所在的页面，让用户重新框选定位。

    页面跑掉之后（点了按钮跳走、列表整个换掉）现场框不出来，
    但这个动作发生在哪一页是记着的，用那个地址重开就行。
    """
    session = RecordingSession.current()
    if session is None:
        return _error('没有录制', 404)
    body = request.get_json(silent=True) or {}
    try:
        result = session.goto_step_doc(body.get('no'))
    except RecordingError as exc:
        return _error(str(exc), 409)
    return jsonify(result), 200


@recording_bp.route('/automation/recording/goto-back', methods=['POST'])
def goto_back():
    """从「回去看」的页面回到离开时那一页。"""
    session = RecordingSession.current()
    if session is None:
        return _error('没有录制', 404)
    try:
        result = session.goto_back()
    except RecordingError as exc:
        return _error(str(exc), 409)
    return jsonify(result), 200


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
    # pick / paused 一起带上：这两个状态能在页面里就地切换（拾取模式下按 Esc 退出），
    # 光靠「有没有新步骤」的游标发现不了，界面会一直停在旧状态上。
    return jsonify({'events': events, 'cursor': cursor, 'status': session.status,
                    'pick': session.pick, 'paused': session.paused}), 200


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
        int(body['no']), index=index,
        framework=body.get('framework') or getattr(session, 'framework', None) or 'playwright-ts'))


@recording_bp.route('/automation/recording/highlight/clear', methods=['POST'])
def clear_recording_highlight():
    return _mutate(lambda session: session.clear_highlight())


@recording_bp.route('/automation/recording/actions', methods=['POST'])
def group_recording_action():
    """把连续的几步封装成一个具名动作，原位置替换成一次调用。

    params 每项 `{step, key, label, field}`：step 是要替换的原步骤号，field 是
    value 或 label。不传 params 就是不参数化，子步骤的值原样写死。
    """
    body = request.get_json(silent=True) or {}
    nos = body.get('steps')
    if not isinstance(nos, list) or not nos:
        return _error('缺少要封装的步骤编号', 400)
    params = body.get('params')
    if params is not None and not isinstance(params, list):
        return _error('params 必须是数组', 400)
    return _mutate(lambda session: session.group_steps(
        nos, body.get('name'), desc=body.get('desc') or '', params=params or []))


@recording_bp.route('/automation/recording/actions/<action_id>', methods=['PATCH'])
def update_recording_action(action_id):
    """只改动作名和说明；子步骤要改就解封，改完重新封装。"""
    body = request.get_json(silent=True) or {}
    return _mutate(lambda session: session.update_action(action_id, body))


@recording_bp.route('/automation/recording/actions/<action_id>', methods=['DELETE'])
def delete_recording_action(action_id):
    """删除动作。还有调用点时会被拒绝，避免留下指向空气的步骤。"""
    return _mutate(lambda session: session.remove_action(action_id))


@recording_bp.route('/automation/recording/actions/<action_id>/calls', methods=['POST'])
def insert_recording_call(action_id):
    """在指定步骤之后（省略则末尾）插入一次调用。"""
    body = request.get_json(silent=True) or {}
    args = body.get('args')
    if args is not None and not isinstance(args, dict):
        return _error('args 必须是对象', 400)
    return _mutate(lambda session: session.insert_call(
        action_id, args=args, after_no=body.get('after_no')))


@recording_bp.route('/automation/recording/steps/<int:no>/ungroup', methods=['POST'])
def ungroup_recording_step(no):
    """把这一次调用还原成它包含的原始步骤。动作定义保留。"""
    return _mutate(lambda session: session.ungroup_step(no))


@recording_bp.route('/automation/recording/steps/<int:no>/args', methods=['PATCH'])
def update_recording_call_args(no):
    """改这一次调用的实参。只接受动作声明过的参数名。"""
    body = request.get_json(silent=True) or {}
    if not isinstance(body.get('args'), dict):
        return _error('缺少 args', 400)
    return _mutate(lambda session: session.update_call(no, body['args']))


@recording_bp.route('/automation/recording/steps/<int:no>/locate', methods=['POST'])
def locate_recording_step(no):
    """这一步「生成时到底用哪条定位」，以及它此刻在当前页面上还在不在、唯不唯一。

    口径是**此刻**，不是回放：页面已经走到后面几步时，这里的结论只说明现在。
    顺带把匹配到的元素在页面上框出来——只给数字不画框，等于没验证。
    """
    session = RecordingSession.current()
    if session is None:
        return _error('没有录制', 404)
    if not session.snapshot().get('browser_open'):
        return _error('浏览器已关闭，无法再看当前页面', 409)
    body = request.get_json(silent=True) or {}
    try:
        result = session.locate(no, framework=body.get('framework')
                                or getattr(session, 'framework', None) or 'playwright-ts')
    except RecordingError as exc:
        return _error(str(exc), 409)
    return jsonify(result), 200


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


@recording_bp.route('/automation/recording/steps/<int:no>/degrade', methods=['POST'])
def degrade_recording_target(no):
    """「允许降级」：这一步不再追问，生成时按最窄候选 + 录制时的序号写。

    和「选第几个」是两个出口：选是「我知道要哪个」，降级是「我不看了，按录制的来」。
    """
    return _mutate(lambda session: session.set_degraded(no))


@recording_bp.route('/automation/recording/steps/<int:no>/shot', methods=['GET'])
def recording_step_shot(no):
    """这一步被按下那一刻的视口截图（只在内存里，不落盘）。

    没有图是正常情况：只有「不唯一」的点击才会截，唯一的那几百次点击不付这个成本。
    """
    session = RecordingSession.current()
    if session is None:
        return _error('没有录制', 404)
    shot = session.shot(no)
    if not shot or not shot.get('png'):
        return _error('这一步没有截图', 404)
    return Response(shot['png'], mimetype='image/png',
                    headers={'Cache-Control': 'no-store'})


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
    # 项目画像：录制开始时预热的那份。没有就这一次先不用，后台补学（见 _memory_for）。
    profile = _memory_for(ctx['workspace'], ctx['framework'])
    key = _emit_key(ctx['session'])
    session = emit_session.REGISTRY.acquire(
        key, ctx['workspace'], ctx['framework'], ctx['model'], ctx['runner'],
    ) if key else None

    def run_plan(active):
        return emit.plan(
            ctx['runner'], ctx['workspace'], ctx['model'], ctx['steps'],
            ctx['framework'], ctx['target_path'], ctx['title'],
            python=body.get('python') or None,
            feedback=body.get('feedback') or [],
            previous_plan=previous,
            progress=progress.mark,
            memory=memory.for_plan(profile) if profile else None,
            session=active,
            actions=ctx['actions'],
        )

    try:
        try:
            result = run_plan(session)
        except emit_session.SessionLost as exc:
            # 会话没了不影响出任务书：退回一次性规划，只是模型要重新读一遍项目。
            logger.warning('规划会话失效，退回一次性规划：%s', exc)
            emit_session.REGISTRY.close(key, 'lost')
            session = None
            result = run_plan(None)
        progress.PROGRESS.remember_plan(
            result, emit.steps_signature(ctx['steps']),
            session.snapshot() if session is not None else None,
        )
        progress.PROGRESS.finish()
        payload = dict(result)
        payload['session'] = session.snapshot() if session is not None else {'reused': False}
        return jsonify(payload), 200
    except emit.EmitError as exc:
        progress.PROGRESS.fail(str(exc), exc.details)
        return jsonify({'error': str(exc), 'details': exc.details}), 422
    except Exception as exc:
        progress.PROGRESS.fail('规划失败：%s' % exc)
        raise


def _recording_workspace(body):
    """开始录制时能拿到的工作区，用来加载项目动作库。

    拿不到不算错误：用户可以边录边配，此时封装的调用照常能用，只是关掉窗口就没了
    （snapshot 里 actions_saved 为 false，界面会说明）。
    """
    try:
        _, workspace = AutomationScriptGenerator(current_app.config)._resolve_workspace(
            body.get('workspace'))
    except ValueError:
        return None
    if not workspace or not Path(workspace).is_dir():
        return None
    return str(workspace)


def _prime_project_memory(body):
    """录制开始时给项目画像排一次后台学习。

    拿不到工作区、没选模型、目录不存在都算正常情况（用户可能先录再回头填配置）：
    真到生成时还会再兜一次（见 _memory_for），所以这里失败不需要告诉任何人。
    """
    model = (body.get('model') or '').strip()
    if '/' not in model:
        return
    try:
        _, workspace = AutomationScriptGenerator(current_app.config)._resolve_workspace(
            body.get('workspace'))
    except ValueError:
        return
    if not workspace or not Path(workspace).is_dir():
        return
    try:
        memory.start_background(
            current_app._get_current_object(), _opencode_config(), workspace, model,
            framework=(body.get('framework') or '').strip() or None,
        )
    except Exception:  # noqa: BLE001 - 画像是锦上添花，绝不能让「开始录制」失败
        logger.warning('项目画像预热没能启动', exc_info=True)


def _memory_for(workspace, framework):
    """取这次生成要用的画像；没有就返回 None，**不在请求里补学**。

    一次学习要读 6~8 个文件、1~2 分钟，塞进「点生成」这一段等于让用户赌运气，
    而且会和写手抢 CPU。学习只发生在两个明确时机：录制开始时的后台预热
    （见 _prime_project_memory）、用户在卡片上点「重学」。

    画像过期也照样用：旧的比没有强，注入时会带 stale 标记让模型自己留意；
    下一次开始录制时指纹对不上，预热会自动重学一份。
    """
    try:
        return memory.load(workspace, framework)
    except Exception:  # noqa: BLE001 - 画像读不出来不该影响生成
        logger.warning('项目画像读取失败', exc_info=True)
        return None


@recording_bp.route('/automation/recording/memory', methods=['GET'])
def get_recording_memory():
    """当前工作区的项目画像：学了什么、准不准、什么时候学的。"""
    try:
        _, workspace = AutomationScriptGenerator(current_app.config)._resolve_workspace(
            request.args.get('workspace_path'))
    except ValueError as exc:
        return _error(str(exc), 400)
    return jsonify(memory.snapshot(workspace, request.args.get('framework') or None)), 200


@recording_bp.route('/automation/recording/memory/refresh', methods=['POST'])
def refresh_recording_memory():
    """重学一遍。学习在后台跑，这里只确认「已经排上了」。"""
    body = request.get_json(silent=True) or {}
    model = (body.get('model') or '').strip()
    if '/' not in model:
        return _error('请先选择模型', 400)
    try:
        _, workspace = AutomationScriptGenerator(current_app.config)._resolve_workspace(
            body.get('workspace_path') or body.get('workspace'))
    except ValueError as exc:
        return _error(str(exc), 400)
    if not workspace or not Path(workspace).is_dir():
        return _error('工作区目录不存在：%s' % workspace, 400)
    started = memory.start_background(
        current_app._get_current_object(), _opencode_config(), workspace, model,
        framework=(body.get('framework') or '').strip() or None, force=True, delay=0,
    )
    if not started:
        return _error('这个工作区已经在学了，请稍等', 409)
    return jsonify({'started': True}), 202


@recording_bp.route('/automation/recording/memory', methods=['DELETE'])
def forget_recording_memory():
    """删掉画像。模型学歪了没有擦除通道，用户就只能整个关掉这个功能。"""
    try:
        _, workspace = AutomationScriptGenerator(current_app.config)._resolve_workspace(
            request.args.get('workspace_path'))
    except ValueError as exc:
        return _error(str(exc), 400)
    return jsonify({'removed': memory.forget(workspace)}), 200


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
    # 前端没带（面板刷新、旧书签、直接调接口）时用这次录制本身的框架：
    # 兜底成 playwright-ts 会让「录制时选 RF、生成时写 Playwright」这种情况没人发现。
    framework = ((body.get('framework') or '').strip()
                 or (getattr(session, 'framework', None) or ''))
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
        # 动作定义跟着一起走：规划和写手都要知道「这几个 call 背后是什么」。
        'actions': session.action_definitions(),
        # 登录态只在「跑一遍」时用：注入到临时配置里，不写进用户仓库、不返回给前端。
        # 浏览器已经关掉也可能有（停止时抓过一份），所以这里取的是缓存而不是活对象。
        'storage_state': session.storage_state(),
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
    profile = _memory_for(ctx['workspace'], ctx['framework'])
    key = _emit_key(ctx['session'])
    session = emit_session.REGISTRY.get(key)
    scope = {'reused': False, 'scope': 'exact'}
    if session is not None:
        # 换了工作区/框架就不能再续用：旧上下文会把写法带到另一个仓库去。
        if session.workspace != str(ctx['workspace']) or session.framework != ctx['framework']:
            emit_session.REGISTRY.close(key, 'workspace_changed')
            progress.PROGRESS.reset()
            return jsonify({'error': '工作区或框架已变化，请重新生成任务书',
                            'details': ['workspace_changed']}), 409
        scope = session.begin_write(target_path)
        if not scope.get('reused'):
            # 会话没了：退回一次性生成，界面会知道这一轮重新读了项目。
            emit_session.REGISTRY.close(key, 'lost')
            session = None
    # 'workspace'（可写整个工作区，越界由 guard 列出来）/ 'exact'（只准写目标文件）。
    write_scope = scope.get('scope') or 'exact'
    keep_session = False
    try:
        result = emit.generate(
            ctx['runner'], ctx['workspace'], ctx['model'], steps,
            ctx['framework'], target_path, ctx['title'],
            python=(body.get('python') or None), overwrite=bool(body.get('overwrite')),
            plan=plan, plan_feedback=body.get('plan_feedback') or [],
            progress=progress.mark,
            storage_state=ctx['storage_state'],
            memory=memory.for_writer(profile) if profile else None,
            session=session, write_scope=write_scope,
            actions=ctx['actions'],
        )
        progress.mark('record')
        record = records.create(
            ctx['workspace'], target_path, ctx['framework'], ctx['title'], ctx['model'], original,
            target_abs.read_bytes(), steps, result.get('reply'),
        )
        result['generation_id'] = record.id
        result['session'] = (session.snapshot() if session is not None
                             else {'reused': False, 'scope': 'exact'})
        progress.PROGRESS.finish(result)
        return jsonify(result), 200
    except emit.EmitError as exc:
        if progress.PROGRESS.cancelled():
            # 用户点了取消：这不是「生成失败」，说法要分开，否则他以为脚本有问题。
            progress.PROGRESS.fail('已取消', ['cancelled'])
            return jsonify({'error': '已取消', 'details': ['cancelled']}), 422
        status = 409 if exc.details and str(exc.details[0]).startswith('conflict:') else 422
        if status == 409:
            # 目标文件已存在不是任务失败：用户还要选覆盖或改名，进度回到 idle，
            # 别让另一个窗口看到一条没有意义的「失败」。
            # 会话也留着——用户点「覆盖」还要接着写，重新读一遍项目纯属浪费。
            keep_session = True
            progress.PROGRESS.reset(keep_plan=True)
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
    finally:
        if not keep_session and key:
            # 决策：生成结束就关。成功、失败、取消都走这里。
            emit_session.REGISTRY.close(key, 'done')


@recording_bp.route('/automation/recording/emit/cancel', methods=['POST'])
def cancel_recording_emit():
    """中止正在进行的规划 / 生成：先 abort 会话，再收掉 serve 进程树。

    规划轮现在最长 15 分钟，没有这个入口用户只能等或者杀后端。
    """
    session = RecordingSession.current()
    key = _emit_key(session) if session is not None else None
    hit = emit_session.REGISTRY.cancel(key) if key else False
    # 即使没有会话也要立标志：一次性生成路径虽然收不到，但进度条要能立刻停。
    progress.PROGRESS.request_cancel()
    if not hit:
        return jsonify({'cancelled': False, 'reason': '没有正在进行的生成会话'}), 200
    return jsonify({'cancelled': True}), 200


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
    # 回放读的是内存里的录制 IR，只要步骤集是稳定的就能跑：
    # 停止（含超时、关浏览器）之后，或者用户主动暂停时。暂停时浏览器仍开着，
    # 但不再收新步骤，正是「先验一段再接着录」的时候；录制中则不放开，
    # 否则步骤一边录一边变，回放结果和界面上的步骤对不上。
    replayable = session.status in ('stopped', 'idle_timeout', 'closed') or (
        session.status == 'recording' and session.paused)
    if not replayable:
        return _error('先暂停或停止录制再回放校验', 409)
    body = request.get_json(silent=True) or {}
    framework = (body.get('framework') or '').strip() or replay.PW_FRAMEWORK
    if framework not in (replay.PW_FRAMEWORK, replay.RF_FRAMEWORK):
        return _error('不支持的框架：%s' % framework, 400)
    steps = session.ir()
    if not steps:
        return _error('还没有录制任何步骤', 400)
    try:
        result = replay.replay_checked(steps, framework=framework,
                                       headless=bool(body.get('headless', True)),
                                       actions=session.action_definitions())
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


@recording_bp.route('/automation/recording/backups/<backup_id>/revert', methods=['POST'])
def revert_recording_backup(backup_id):
    """把这次生成里越界改动过的文件还原到生成之前的样子。

    只还原清单里有的文件，`paths` 为空表示全部。模型写的辅助文件不会被自动删——
    保留还是还原由用户决定。还原前会把当前内容存到同一目录的 `current/` 下，
    点错了也捞得回来。
    """
    body = request.get_json(silent=True) or {}
    session = RecordingSession.current()
    workspace = Path(session.workspace) if session is not None else None
    if workspace is None or not workspace.is_dir():
        try:
            _, workspace = AutomationScriptGenerator(current_app.config)._resolve_workspace(
                body.get('workspace_path'))
        except ValueError as exc:
            return _error(str(exc), 400)
    if not workspace or not Path(workspace).is_dir():
        return _error('工作区目录不存在', 404)
    try:
        result = guard.revert(workspace, backup_id, body.get('paths') or None)
    except guard.GuardError as exc:
        return _error(str(exc), 404)
    except OSError as exc:
        return _error('还原失败：%s' % exc, 500)
    return jsonify(result), 200


@recording_bp.route('/automation/recording/steps/<int:no>/move', methods=['POST'])
def move_recording_step(no):
    body = request.get_json(silent=True) or {}
    direction = body.get('direction')
    if direction not in ('up', 'down'):
        return _error('direction 只能是 up 或 down', 400)
    return _mutate(lambda session: session.move_step(no, direction))
