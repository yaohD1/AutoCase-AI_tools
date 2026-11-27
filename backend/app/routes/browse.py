"""工作区与目标文件的目录浏览接口。

浏览器拿不到本地绝对路径（安全限制），工作区又必须落在 `AUTOMATION_WORKSPACE_ROOT` 内，
所以改由后端在根目录内列目录与文件名，前端只传相对路径：

- `GET /automation/browse`：列出根目录下的子目录，用于选择工作区；返回相对根目录的路径。
- `GET /automation/browse/files`：列出某个工作区内的子目录与测试文件，用于选择目标文件；
  这一组路径相对工作区，和 `/automation/recording/generate` 的 `target_path` 语义一致。
- `POST /automation/browse/mkdir`：在选择器里新建一个文件夹。

浏览仍然只读：不读文件内容、不越过根目录。唯一的写操作是 mkdir，而且只建目录、不建文件；
生成脚本的写入仍走 `/automation/recording/generate` 自己的校验。
"""
import re
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request


browse_bp = Blueprint('browse', __name__)

# 依赖与缓存目录不进列表：既避免噪声，也避免遍历几万个文件。
SKIP_DIRS = {
    'node_modules', 'bower_components', '__pycache__', 'site-packages', 'dist', 'build',
    'venv', 'env', '.venv', '.tox', '.idea', '.vscode', '.pytest_cache', '.mypy_cache',
    'test-results', 'playwright-report', 'blob-report',
}
MAX_DIRS = 300
MAX_FILES = 300

# 目标文件的候选扩展名：按框架给出，未知框架时取并集。
SUFFIXES = {
    'robot-selenium': ('.robot', '.resource'),
    'playwright-ts': ('.spec.ts', '.test.ts', '.ts'),
}
ALL_SUFFIXES = ('.robot', '.resource', '.spec.ts', '.test.ts', '.ts')


def _root():
    configured = current_app.config.get('AUTOMATION_WORKSPACE_ROOT') or ''
    root = Path(configured).expanduser().resolve()
    if not configured or not root.is_dir():
        raise ValueError('AUTOMATION_WORKSPACE_ROOT 不存在：%s' % (configured or '(未配置)'))
    return root


def _resolve(base, relative):
    """把相对（或绝对）路径解析成绝对路径，并把解析结果固定在 base 内。"""
    text = str(relative or '').strip().replace('\\', '/')
    candidate = Path(text) if text else base
    target = (candidate if candidate.is_absolute() else base / candidate).resolve()
    if target != base and base not in target.parents:
        raise ValueError('路径必须位于 %s 内' % base.name)
    return target


def _relative(base, path):
    return '' if path == base else path.relative_to(base).as_posix()


def _marks(path):
    """给目录打上项目类型标记，前端据此显示徽标并决定是否需要「检测」。"""
    marks = []
    try:
        if (path / '.git').exists():
            marks.append('git')
        if (path / 'package.json').is_file():
            marks.append('node')
        if any(path.glob('playwright.config.*')):
            marks.append('playwright')
        if (path / 'pyproject.toml').is_file() or (path / 'requirements.txt').is_file():
            marks.append('python')
        tests = path / 'tests'
        if tests.is_dir() and any(item.suffix in ('.robot', '.resource') for item in tests.iterdir()):
            marks.append('robot')
    except OSError:
        # 单个目录不可读不应该让整个列表失败。
        pass
    return marks


def _folder_name_error(name):
    """校验待建文件夹名；合法返回空串。

    比「系统允许什么」更严一点，因为这里建出来是给人看的目录：点开头的不显示在列表里
    （见 _sub_dirs 的过滤），SKIP_DIRS 里的名字会被当成依赖目录跳过——建了等于没建，
    所以宁可当场拒绝并说清楚。
    """
    if not name:
        return '请填写文件夹名'
    if len(name) > 80:
        return '文件夹名不能超过 80 个字符'
    if name in ('.', '..'):
        return '不能用 . 或 .. 作为文件夹名'
    if name.startswith('.'):
        return '文件夹名不能以 . 开头（这类目录不会显示在列表里）'
    if name in SKIP_DIRS:
        return '这个名称会被目录列表当成依赖目录跳过，请换一个'
    if re.search(r'[\\/:*?"<>|\x00-\x1f]', name):
        return r'文件夹名不能包含这些字符：\ / : * ? " < > |'
    if name.endswith(('.', ' ')):
        return '文件夹名不能以点或空格结尾'
    return ''


def _sub_dirs(root, current):
    entries = []
    truncated = False
    try:
        children = sorted(current.iterdir(), key=lambda item: item.name.lower())
    except OSError as exc:
        raise ValueError('目录不可读：%s' % exc)
    for child in children:
        name = child.name
        if name.startswith('.') or name in SKIP_DIRS:
            continue
        try:
            if not child.is_dir():
                continue
        except OSError:
            continue
        # junction / symlink 指向根目录外时直接跳过，避免顺带暴露根目录外的结构。
        try:
            if child.resolve() != root and root not in child.resolve().parents:
                continue
        except OSError:
            continue
        if len(entries) >= MAX_DIRS:
            truncated = True
            break
        entries.append({
            'name': name,
            'path': _relative(root, child),
            'marks': _marks(child),
        })
    return entries, truncated


@browse_bp.route('/automation/browse', methods=['GET'])
def browse_workspaces():
    """列出根目录（或指定子目录）下的候选工作区目录。"""
    try:
        root = _root()
        current = _resolve(root, request.args.get('path'))
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    if not current.is_dir():
        return jsonify({'error': '目录不存在：%s' % _relative(root, current)}), 404
    dirs, truncated = _sub_dirs(root, current)
    return jsonify({
        'root': str(root),
        'path': _relative(root, current),
        'parent': None if current == root else _relative(root, current.parent),
        'dirs': dirs,
        'truncated': truncated,
    }), 200


@browse_bp.route('/automation/browse/files', methods=['GET'])
def browse_target_files():
    """列出某个工作区内的子目录与测试文件；路径一律相对工作区。"""
    try:
        root = _root()
        workspace = _resolve(root, request.args.get('workspace'))
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    if not workspace.is_dir():
        return jsonify({'error': '工作区目录不存在：%s' % _relative(root, workspace)}), 404
    try:
        current = _resolve(workspace, request.args.get('path'))
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    if not current.is_dir():
        return jsonify({'error': '目录不存在：%s' % _relative(workspace, current)}), 404

    framework = (request.args.get('framework') or '').strip()
    suffixes = SUFFIXES.get(framework, ALL_SUFFIXES)
    dirs = []
    files = []
    truncated = False
    try:
        children = sorted(current.iterdir(), key=lambda item: item.name.lower())
    except OSError as exc:
        return jsonify({'error': '目录不可读：%s' % exc}), 400
    for child in children:
        name = child.name
        if name.startswith('.') or name in SKIP_DIRS:
            continue
        try:
            if child.is_dir():
                if len(dirs) < MAX_DIRS:
                    dirs.append({'name': name, 'path': _relative(workspace, child)})
                else:
                    truncated = True
                continue
            if not child.is_file() or not name.endswith(suffixes):
                continue
            if len(files) >= MAX_FILES:
                truncated = True
                break
            files.append({
                'name': name,
                'path': _relative(workspace, child),
                'size': child.stat().st_size,
            })
        except OSError:
            continue

    return jsonify({
        'workspace': _relative(root, workspace),
        'path': _relative(workspace, current),
        'parent': None if current == workspace else _relative(workspace, current.parent),
        'dirs': dirs,
        'files': files,
        'truncated': truncated,
    }), 200


@browse_bp.route('/automation/browse/mkdir', methods=['POST'])
def make_folder():
    """在当前浏览位置下新建一个文件夹。

    两种模式共用：给了 workspace 就以工作区为基准（选目标文件的场景），
    不给就以根目录为基准（选工作区的场景）——和两个 GET 接口的路径语义保持一致。
    只建一层（`mkdir` 不带 parents），上层目录必须已经存在，避免一次请求建出一串。
    """
    payload = request.get_json(silent=True) or {}
    name = str(payload.get('name') or '').strip()
    try:
        root = _root()
        workspace = str(payload.get('workspace') or '').strip()
        base = root
        if workspace:
            base = _resolve(root, workspace)
            if not base.is_dir():
                return jsonify({'error': '工作区目录不存在：%s' % _relative(root, base)}), 404
        current = _resolve(base, payload.get('path'))
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    problem = _folder_name_error(name)
    if problem:
        return jsonify({'error': problem}), 400
    if not current.is_dir():
        return jsonify({'error': '目录不存在：%s' % _relative(base, current)}), 404

    target = current / name
    try:
        target.mkdir()
    except FileExistsError:
        return jsonify({'error': '当前目录下已有同名文件夹：%s' % name}), 409
    except OSError as exc:
        return jsonify({'error': '创建文件夹失败：%s' % exc}), 400

    return jsonify({
        'name': name,
        'path': _relative(base, target),
        'workspace': '' if base == root else _relative(root, base),
    }), 201
