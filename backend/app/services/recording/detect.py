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
TS_TEST_SUFFIXES = ('.spec.ts', '.test.ts', '.spec.tsx', '.test.tsx',
                    '.spec.js', '.test.js', '.spec.mjs', '.test.mjs')
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


def _is_artifact_copy(path):
    """AutoCase 自己跑出来的工作区副本（autocase/runs/<时间戳>/tests/...）。

    它们是被生成或复制出来的产物，不是项目的真实写法；拿它们当样例会让模型
    以为那就是项目约定，甚至照着这个目录去定写入路径。
    """
    parts = [part.lower() for part in path.parts[:-1]]
    for index, part in enumerate(parts):
        if part == 'autocase' and index + 1 < len(parts) and parts[index + 1] == 'runs':
            return True
    return False


# AutoCase 写出来的文件带这两种痕迹：新版会在首行写 @autocase，
# 旧版（自动加标记之前）留下的注释是「spec: 录制自」。都只出现在文件头。
AUTOCASE_MARKERS = ('@autocase', 'spec: 录制自')
MARKER_SCAN_LINES = 4


def is_generated_by_autocase(path):
    """这个测试文件是不是 AutoCase 自己录出来的产物。

    它跟「项目里原本就有的用例」性质完全不同：它是这条流程上一次生成的结果。
    规划把两者混为一谈，就会读出一份「已经有一模一样的用例了」，
    然后在「覆盖它还是新建一个」上来回权衡——上次 240 秒超时就是这样烧掉的。
    """
    try:
        with path.open('r', encoding='utf-8', errors='replace') as handle:
            head = ''.join(next(handle, '') for _ in range(MARKER_SCAN_LINES))
    except OSError:
        return False
    head = head.lower()
    return any(marker in head for marker in AUTOCASE_MARKERS)


def _samples(root, files):
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    samples = []
    for path in files[:SAMPLE_LIMIT]:
        try:
            lines = path.read_text(encoding='utf-8', errors='replace').splitlines()[:SAMPLE_LINES]
        except OSError:
            continue
        samples.append({
            'path': str(path.relative_to(root)).replace('\\', '/'),
            'head': '\n'.join(lines),
            # 调用方（规划的项目事实）要把自己的产物单独归类，所以顺手标出来，
            # 免得再读一遍文件。
            'generated': is_generated_by_autocase(path),
        })
    return samples


def sample_robot_files(workspace):
    """最近修改的 .robot 文件，作为项目写法的样例。只返回前若干行。"""
    root = Path(workspace)
    files = [p for p in _walk(root) if p.suffix == '.robot' and not _is_artifact_copy(p)]
    return _samples(root, files)


def _is_ts_test(path):
    return path.name.lower().endswith(TS_TEST_SUFFIXES)


def recent_test_paths(workspace, robot=False, limit=6):
    """最近的测试文件路径，带「是不是 AutoCase 自己生成的」标记。

    样例（samples）要带正文，最多给 3 个；但规划需要看得更宽——它得同时分清
    「项目原有的用例」和「这条流程上一次生成的旧版本」。后者不能当项目惯例，
    否则规划会读出一份等价用例，然后在「覆盖还是新建」上来回权衡。
    这里只读文件头几行，成本可以忽略。
    """
    root = Path(workspace)
    if robot:
        files = [p for p in _walk(root) if p.suffix == '.robot' and not _is_artifact_copy(p)]
    else:
        files = [p for p in _walk(root) if _is_ts_test(p) and not _is_artifact_copy(p)]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for path in files[:limit]:
        try:
            rel = str(path.relative_to(root)).replace('\\', '/')
        except ValueError:
            continue
        out.append({'path': rel, 'generated': is_generated_by_autocase(path)})
    return out


def sample_test_files(workspace):
    """最近修改的 Playwright 测试文件，作为项目写法的样例。

    之前 Playwright 项目拿到的 samples 永远是空数组，提示词却说「照 samples 的风格写」——
    模型只能按通用 Playwright 风格硬写，跟项目里那套 TestModule / 断言习惯完全对不上。
    这里和 sample_robot_files 做同一件事，只是后缀不同。
    """
    root = Path(workspace)
    files = [p for p in _walk(root) if _is_ts_test(p) and not _is_artifact_copy(p)]
    return _samples(root, files)


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

    if framework == 'robot-selenium':
        samples = sample_robot_files(root)
    elif framework == 'playwright-ts':
        samples = sample_test_files(root)
    else:
        samples = []
    if samples:
        evidence.append({'kind': 'samples', 'value': [s['path'] for s in samples], 'source': 'mtime'})

    return {
        'framework': framework,
        'python': python,
        'seleniumlibrary_version': version,
        'evidence': evidence,
        'samples': samples,
    }
