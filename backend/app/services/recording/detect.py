"""目标工作区的框架与环境检测。

只读：读文件、读包的安装元数据，不导入项目代码，不执行项目脚本。
检测结果只作为提示和上下文，不阻止生成（见决策 12）。
"""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

SKIP_DIRS = {'.git', 'node_modules', '__pycache__', '.venv', 'venv', 'env', '.tox', 'site-packages'}
VENV_NAMES = ('.venv', 'venv', 'env')
ROBOT_DIST = 'robotframework-seleniumlibrary'
ROBOT_CORE_DIST = 'robotframework'
REQ_FILES = ('requirements.txt', 'requirements-dev.txt', 'pyproject.toml', 'Pipfile.lock',
             'poetry.lock', 'setup.cfg')
SAMPLE_LIMIT = 3
SAMPLE_LINES = 60
PROBE_TIMEOUT = 20

# 只读取安装记录，不导入库本身。
_VERSION_PROBE = (
    'import importlib.metadata as m, sys\n'
    'try:\n'
    '    print(m.version(sys.argv[1]))\n'
    'except Exception:\n'
    '    sys.exit(1)\n'
)


def _walk(root):
    # 在遍历时剪掉虚拟环境与依赖目录，避免扫描 .venv 里成千上万个文件。
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            yield Path(dirpath) / name


def find_python(workspace):
    """依次找工作区内的虚拟环境，最后退到 PATH。返回 (python 路径或 None, 依据)。"""
    root = Path(workspace)
    for name in VENV_NAMES:
        for rel in ('Scripts/python.exe', 'bin/python'):
            candidate = root / name / rel
            if candidate.is_file():
                return str(candidate), 'workspace:%s/%s' % (name, rel)
    found = shutil.which('python') or shutil.which('python3')
    if found:
        return found, 'PATH'
    return None, '未找到 Python'


def _probe(python, args, timeout=PROBE_TIMEOUT):
    try:
        result = subprocess.run([python] + args, capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, str(exc)
    if result.returncode != 0:
        return None, (result.stderr or result.stdout or '').strip()[-300:]
    return result.stdout.strip(), None


def library_version(python, dist):
    """优先读安装元数据；失败返回 None。"""
    out, _ = _probe(python, ['-c', _VERSION_PROBE, dist])
    return out or None


def robot_version(python):
    out, _ = _probe(python, ['-m', 'robot', '--version'])
    if not out:
        return None
    match = re.search(r'(\d+(?:\.\d+)+)', out)
    return match.group(1) if match else out


def requirement_hints(workspace):
    """从依赖清单里找 robot 相关的版本写法，只做提示。"""
    hints = []
    root = Path(workspace)
    pattern = re.compile(r'(robotframework[-\w]*)\s*([=<>!~]=?\s*[\w.]+)?', re.I)
    for name in REQ_FILES:
        path = root / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        for match in pattern.finditer(text):
            hints.append({'file': name, 'spec': match.group(0).strip()})
    return hints[:10]


def sample_robot_files(workspace):
    """最近修改的 .robot 文件，作为项目写法的样例。只返回前若干行。"""
    root = Path(workspace)
    files = [p for p in _walk(root) if p.suffix == '.robot']
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    samples = []
    for path in files[:SAMPLE_LIMIT]:
        try:
            lines = path.read_text(encoding='utf-8', errors='replace').splitlines()[:SAMPLE_LINES]
        except OSError:
            continue
        samples.append({'path': str(path.relative_to(root)).replace('\\', '/'), 'head': '\n'.join(lines)})
    return samples


def playwright_ts_present(workspace):
    package = Path(workspace) / 'package.json'
    if not package.is_file():
        return False
    try:
        data = json.loads(package.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    deps = {}
    deps.update(data.get('dependencies') or {})
    deps.update(data.get('devDependencies') or {})
    return '@playwright/test' in deps


def detect(workspace, python=None):
    """返回框架检测结果：framework（robot-selenium / playwright-ts / None）与可追溯的依据。

    python 为用户手动指定的解释器，给出时优先于自动查找。
    """
    root = Path(workspace)
    evidence = []
    if python:
        python_source = 'user'
    else:
        python, python_source = find_python(root)
    evidence.append({'kind': 'python', 'value': python, 'source': python_source})

    robot_files = any(p.suffix == '.robot' for p in _walk(root))
    robot_hints = requirement_hints(root)
    playwright = playwright_ts_present(root)

    version = None
    if python:
        version = library_version(python, ROBOT_DIST)
        if version:
            evidence.append({'kind': 'seleniumlibrary', 'value': version, 'source': 'importlib.metadata'})
        else:
            cli = robot_version(python)
            if cli:
                evidence.append({'kind': 'robotframework', 'value': cli, 'source': 'robot --version'})
    if not version:
        for hint in robot_hints:
            evidence.append({'kind': 'requirement', 'value': hint['spec'], 'source': hint['file']})

    if robot_files or robot_hints:
        framework = 'robot-selenium'
    elif playwright:
        framework = 'playwright-ts'
    else:
        framework = None
        evidence.append({'kind': 'framework', 'value': None, 'source': '未识别到 robot 或 @playwright/test'})

    samples = sample_robot_files(root) if framework == 'robot-selenium' else []
    if samples:
        evidence.append({'kind': 'samples', 'value': [s['path'] for s in samples], 'source': 'mtime'})

    return {
        'framework': framework,
        'python': python,
        'seleniumlibrary_version': version,
        'evidence': evidence,
        'samples': samples,
    }
