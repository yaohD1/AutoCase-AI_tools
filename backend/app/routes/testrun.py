"""执行测试的 HTTP 接口：枚举用例、发起运行、看进度与报告、下载失败截图。

运行是异步的（一次可能几百条用例、几分钟），所以这里只负责「建记录 + 起线程」，
前端按 `GET /runs/<id>` 轮询；进度走内存，结果落库。
"""
import logging
import time
from pathlib import Path

from flask import Blueprint, Response, current_app, jsonify, request, send_file
from werkzeug.utils import secure_filename

from app.models import db
from app.models.test_run import TestRun
from app.services.recording import report_html, runner

testrun_bp = Blueprint('testrun', __name__)
logger = logging.getLogger(__name__)


def _workspace(value):
    """工作区必须落在 AUTOMATION_WORKSPACE_ROOT 内：这个接口会真的在磁盘上执行进程。"""
    configured = (current_app.config.get('AUTOMATION_WORKSPACE_ROOT') or '').strip()
    if not configured:
        raise runner.RunnerError('没有配置 AUTOMATION_WORKSPACE_ROOT，无法执行')
    root = Path(configured).expanduser().resolve()
    text = str(value or '').strip()
    if not text:
        raise runner.RunnerError('还没有选择工作区')
    candidate = Path(text).expanduser()
    target = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if target != root and root not in target.parents:
        raise runner.RunnerError('工作区必须位于 AUTOMATION_WORKSPACE_ROOT（%s）内' % root.name)
    if not target.is_dir():
        raise runner.RunnerError('工作区不存在：%s' % text)
    return str(target)


@testrun_bp.route('/automation/testrun/cases', methods=['GET'])
def list_cases():
    """列出可执行用例。refresh=1 强制重新解析（默认缓存 2 分钟）。

    files=a.robot,b.robot 时只解析这几个文件：不读缓存、也不按目录约定扫描。
    生成完脚本立刻跳过来定位就用它——刚写出的文件既不在缓存里，也可能不在
    「测试用例层 / tests」这些约定目录下。
    """
    try:
        workspace = _workspace(request.args.get('workspace'))
        files = [item.strip() for item in (request.args.get('files') or '').split(',') if item.strip()]
        if files:
            data = runner.cases_in(workspace, files)
        else:
            data = runner.discover(workspace, force=request.args.get('refresh') in ('1', 'true'))
    except runner.RunnerError as exc:
        return jsonify({'error': str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        logger.exception('枚举用例失败')
        return jsonify({'error': '枚举用例失败：%s' % exc}), 500
    return jsonify({
        'workspace': data['root'],
        'framework': data['framework'],
        'note': data.get('note'),
        # 请求了但这些文件里没解析出用例：前端据此提示「重新扫描」，不当作整页失败。
        'missing': data.get('missing') or [],
        'cases': [
            {key: value for key, value in case.items() if not key.startswith('_')}
            for case in data['cases']
        ],
        'discovered_at': data.get('discovered_at') or time.time(),
    })


@testrun_bp.route('/automation/testrun/runs', methods=['GET'])
def list_runs():
    workspace = request.args.get('workspace')
    query = TestRun.query
    if workspace:
        try:
            query = query.filter_by(workspace=_workspace(workspace))
        except runner.RunnerError as exc:
            return jsonify({'error': str(exc)}), 400
    limit = min(int(request.args.get('limit') or 20), 100)
    runs = query.order_by(TestRun.started_at.desc()).limit(limit).all()
    return jsonify({'runs': [run.to_dict(with_results=False) for run in runs]})


@testrun_bp.route('/automation/testrun/runs', methods=['POST'])
def create_run():
    body = request.get_json(silent=True) or {}
    try:
        workspace = _workspace(body.get('workspace'))
        run = runner.start(current_app._get_current_object(), workspace, body.get('cases') or [],
                           framework=(body.get('framework') or '').strip() or None)
    except runner.RunnerError as exc:
        return jsonify({'error': str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        logger.exception('发起执行失败')
        return jsonify({'error': '发起执行失败：%s' % exc}), 500
    data = run.to_dict(with_results=False)
    data['progress'] = runner.progress(run.id)
    return jsonify(data), 201


@testrun_bp.route('/automation/testrun/runs/<run_id>', methods=['GET'])
def get_run(run_id):
    run = db.session.get(TestRun, run_id)
    if run is None:
        return jsonify({'error': '运行记录不存在'}), 404
    data = run.to_dict()
    data['progress'] = runner.progress(run.id)
    return jsonify(data)


@testrun_bp.route('/automation/testrun/runs/<run_id>/stop', methods=['POST'])
def stop_run(run_id):
    run = db.session.get(TestRun, run_id)
    if run is None:
        return jsonify({'error': '运行记录不存在'}), 404
    stopped = runner.stop(run_id)
    # 进程可能刚好自己结束了：这时 stop 返回 False，但状态本来就该是「跑完了」，
    # 不该回一个 409 让用户以为操作失败。
    return jsonify({'stopped': bool(stopped), 'status': run.status})


@testrun_bp.route('/automation/testrun/runs/<run_id>/artifact', methods=['GET'])
def get_artifact(run_id):
    run = db.session.get(TestRun, run_id)
    if run is None:
        return jsonify({'error': '运行记录不存在'}), 404
    try:
        path = runner.artifact_path(run, request.args.get('path'))
    except runner.RunnerError as exc:
        return jsonify({'error': str(exc)}), 400
    # secure_filename 会把中文名清空，这里只用来挡下载头里的异常字符，实际读取仍走校验过的绝对路径。
    name = secure_filename(path.name) or 'artifact%s' % path.suffix
    return send_file(str(path), as_attachment=False, download_name=name)


@testrun_bp.route('/automation/testrun/runs/<run_id>/export', methods=['GET'])
def export_run(run_id):
    """导出自包含 HTML：截图内联，双击即可看，不依赖项目自身报告。"""
    run = db.session.get(TestRun, run_id)
    if run is None:
        return jsonify({'error': '运行记录不存在'}), 404
    if run.status == 'running':
        return jsonify({'error': '运行还没结束，先等它跑完再导出'}), 409
    try:
        text = report_html.render(run)
    except Exception as exc:  # noqa: BLE001 - 导出失败不该把 500 变成白屏
        logger.exception('导出报告失败')
        return jsonify({'error': '导出报告失败：%s' % exc}), 500
    stamp = run.started_at.strftime('%Y%m%d-%H%M%S') if run.started_at else run_id[:8]
    response = Response(text, mimetype='text/html')
    response.headers['Content-Disposition'] = 'attachment; filename="autocase-report-%s.html"' % stamp
    return response
