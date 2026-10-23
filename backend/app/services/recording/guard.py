"""录制生成的范围检查：只检测「目标文件以外有没有被改动」，不做还原。

两套机制，按工作区能不能用 git 自动选择：

- **git**（工作区正好是仓库顶层、且有至少一个 commit）：开始时用 `git stash create` 把当前
  工作区状态打成一个临时 commit——不建 ref、不动 index、不动工作区文件；结束后
  `git diff --name-only <那个 commit>` 就能精确拿到修改/新增/删除，而且天然尊重工作区的
  `.gitignore`（`reports/`、`midscene_run/` 这些产物目录根本不进集合）。
  开始时未被跟踪的文件不在 stash commit 里，所以另记一份 (size, mtime_ns) 指纹来覆盖它们。
- **指纹回退**（不是 git 仓库 / 没有 commit / git 不可用）：只走 `stat` 记 (size, mtime_ns)，
  不读文件内容。几百 MB 的工作区也只是秒内完成，不再存在「工作区过大无法备份」的失败。

**只报告、不回滚**：回滚会把用户在这期间自己的编辑一起抹掉。真要撤销，git 本来就是更好的工具。
"""
import os
import subprocess
from pathlib import Path

from app.services.recording.detect import SKIP_DIRS

# opencode serve 启动时自动导出的框架产物，不属于工作区内容。
FRAMEWORK_ARTIFACT_PREFIXES = ('.claude/agents/', '.github/agents/', '.playwright-mcp/', '.autocase/')

# 工具链（tsc / playwright / midscene / allure）自己写的产物目录。校验是我们自己跑的，
# 这些目录里的东西不该算到 agent 头上。
ARTIFACT_DIRS = frozenset({
    'reports', 'allure-results', 'allure-report', 'midscene_run', 'midscene_run_cache',
    'test-results', 'blob-report', 'playwright-report', 'playwright-report-legacy',
    'coverage', 'dist', 'build', 'out', '.next', '.nuxt', '.svelte-kit',
    '.cache', '.turbo', '.parcel-cache', '.pytest_cache', '.mypy_cache',
})

GIT_TIMEOUT = 20
MAX_SCAN_FILES = 200000


class GuardError(Exception):
    """检查本身没法进行（git 出错、工作区不可读等），不是「检测到越界」。"""


class Snapshot:
    """开始时刻的工作区状态。mode 为 'git' 时用 base，为 'files' 时用 files。"""

    __slots__ = ('root', 'mode', 'base', 'untracked', 'files')

    def __init__(self, root, mode, base=None, untracked=None, files=None):
        self.root = root
        self.mode = mode
        self.base = base
        self.untracked = untracked or {}
        self.files = files or {}


def _git(root, *args):
    try:
        return subprocess.run(
            ('git', '-C', str(root)) + args,
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=GIT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise GuardError('git 调用失败：%s' % exc)


def _out(proc):
    return (proc.stdout or '').strip()


def _repo_root(root):
    """工作区是不是仓库顶层。放在大仓库的子目录里不算——那样 diff 出来的路径基准会是仓库根。"""
    proc = _git(root, 'rev-parse', '--show-toplevel')
    if proc.returncode != 0:
        return None
    top = _out(proc)
    if not top:
        return None
    try:
        return Path(top).resolve() == root
    except OSError:
        return False


def _head(root):
    proc = _git(root, 'rev-parse', '--verify', 'HEAD')
    if proc.returncode != 0:
        return None
    return _out(proc) or None


def _is_artifact(rel):
    if rel.startswith(FRAMEWORK_ARTIFACT_PREFIXES):
        return True
    if rel.split('/', 1)[0] in ARTIFACT_DIRS:
        return True
    return rel.endswith('.tsbuildinfo')


def _norm(rel):
    """统一成 posix 相对路径。注意只剥前导 './'，不能 lstrip——那会把 '.env' 变成 'env'。"""
    rel = str(rel).replace('\\', '/')
    while rel.startswith('./'):
        rel = rel[2:]
    return rel


def _sig(path):
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_size, st.st_mtime_ns)


def _gitignore_dirs(root):
    """非 git 时也能把工作区自己声明的产物目录排除掉（只取顶层目录名，不追求完整语义）。"""
    try:
        text = (root / '.gitignore').read_text(encoding='utf-8', errors='replace')
    except OSError:
        return set()
    names = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#') or line.startswith('!'):
            continue
        line = line.lstrip('/')
        if line.endswith('/'):
            line = line[:-1]
        first = line.split('/', 1)[0]
        if not first or first in ('.', '..') or any(ch in first for ch in '*?['):
            continue
        names.add(first)
    return names


def _fingerprints(root):
    """{相对路径: (size, mtime_ns)}，只 stat 不读内容。"""
    skip = set(SKIP_DIRS) | set(ARTIFACT_DIRS) | _gitignore_dirs(root)
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip]
        for name in filenames:
            path = Path(dirpath) / name
            rel = path.relative_to(root).as_posix()
            if _is_artifact(rel) or path.is_symlink():
                continue
            sig = _sig(path)
            if sig is None:
                continue
            out[rel] = sig
            if len(out) > MAX_SCAN_FILES:
                raise GuardError('工作区文件数超过 %d，无法检查改动范围' % MAX_SCAN_FILES)
    return out


def _untracked(root):
    """工作区里未被跟踪、也没被 .gitignore 忽略的文件及其指纹。"""
    proc = _git(root, 'ls-files', '--others', '--exclude-standard', '-z')
    if proc.returncode != 0:
        raise GuardError('git ls-files 失败：%s' % (_out(proc) or '未知原因'))
    out = {}
    for rel in (proc.stdout or '').split('\0'):
        if not rel:
            continue
        rel = _norm(rel)
        if _is_artifact(rel):
            continue
        sig = _sig(root / rel)
        if sig is not None:
            out[rel] = sig
    return out


def snapshot(workspace):
    """记下开始时刻的工作区状态。只做只读操作和一次 `stash create`（不改工作区内容）。"""
    root = Path(workspace).resolve()
    if not root.is_dir():
        raise GuardError('工作区目录不存在：%s' % root)
    if _repo_root(root):
        head = _head(root)
        if head:
            stash = _git(root, 'stash', 'create')
            # 工作区本来就干净时 stash create 输出为空，那种情况 HEAD 就是基准。
            if stash.returncode == 0:
                base = _out(stash) or head
                return Snapshot(root, 'git', base=base, untracked=_untracked(root))
    return Snapshot(root, 'files', files=_fingerprints(root))


def _diff_names(root, base):
    proc = _git(root, 'diff', '--name-only', '-z', base)
    if proc.returncode != 0:
        raise GuardError('git diff 失败：%s' % (_out(proc) or '未知原因'))
    return {_norm(rel) for rel in (proc.stdout or '').split('\0') if rel}


def changed(snap):
    """和开始时刻相比，被新增/修改/删除的相对路径（已排除产物目录）。"""
    if snap.mode == 'git':
        out = set(_diff_names(snap.root, snap.base))
        now = _untracked(snap.root)
        out |= {rel for rel in now if rel not in snap.untracked}
        out |= {rel for rel, sig in now.items() if rel in snap.untracked and snap.untracked[rel] != sig}
    else:
        now = _fingerprints(snap.root)
        out = {rel for rel in set(snap.files) | set(now) if snap.files.get(rel) != now.get(rel)}
    return sorted(rel for rel in out if not _is_artifact(rel))


def violations(paths, target):
    """除目标文件以外的改动。只用于报告，不回滚。"""
    target = _norm(target)
    return [p for p in (_norm(rel) for rel in paths) if p != target]
