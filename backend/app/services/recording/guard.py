"""录制生成的范围检查：检测「目标文件以外有没有被改动」，并支持按需手动还原。

两套机制，按工作区能不能用 git 自动选择：

- **git**（工作区正好是仓库顶层、且有至少一个 commit）：开始时用 `git stash create` 把当前
  工作区状态打成一个临时 commit——不建 ref、不动 index、不动工作区文件；结束后
  `git diff --name-only <那个 commit>` 就能精确拿到修改/新增/删除，而且天然尊重工作区的
  `.gitignore`（`reports/`、`midscene_run/` 这些产物目录根本不进集合）。
  开始时未被跟踪的文件不在 stash commit 里，所以另记一份 (size, mtime_ns) 指纹来覆盖它们。
- **指纹回退**（不是 git 仓库 / 没有 commit / git 不可用）：只走 `stat` 记 (size, mtime_ns)，
  不读文件内容。几百 MB 的工作区也只是秒内完成，不再存在「工作区过大无法备份」的失败。

**只报告、不自动还原**：模型确实可能需要写辅助文件（helper / fixture），自动删掉会把
有用的东西一起删掉；而且在生成期间用户自己也可能在编辑，自动还原会误伤。

但「只报告」不等于「什么都做不了」：`capture()` 会在生成结束时把越界文件在**快照时刻**的
内容导出到 `.autocase/backups/<id>/`，`revert()` 按那份清单还原。导出是必须的——stash
create 出来的 commit 是悬空的，git gc 之后 `git show <sha>:<path>` 就取不到了。
"""
import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path

from app.services.recording.detect import SKIP_DIRS

# opencode serve 启动时自动导出的框架产物，不属于工作区内容。
# `.autocase.verify.config.ts` 是「跑一遍」用的临时配置：正常跑完就删，
# 这里兜住进程被杀的情况，免得一条残留配置被当成越界改动报给用户。
# `.opencode/` 同理：serve 在 cwd 下会写插件/会话旁路文件，不该算到 agent 头上。
FRAMEWORK_ARTIFACT_PREFIXES = ('.claude/agents/', '.github/agents/', '.playwright-mcp/',
                               '.opencode/', '.autocase/', '.autocase.verify.config.ts')

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
    """除目标文件以外的改动。"""
    target = _norm(target)
    return [p for p in (_norm(rel) for rel in paths) if p != target]


# ---- 越界快照与手动还原 ----

BACKUP_DIR = '.autocase/backups'
# 单个文件超过这个大小就不备份内容，只记一条 skip——避免把几百 MB 的产物读进内存。
MAX_BACKUP_BYTES = 8 * 1024 * 1024
SAFE_ID = re.compile(r'^[A-Za-z0-9_-]{1,64}$')


def _git_bytes(root, *args):
    """取二进制的 git 输出（`git show` 不能走 text=True，那会把内容按编码替换坏）。"""
    try:
        proc = subprocess.run(
            ('git', '-C', str(root)) + args,
            capture_output=True, timeout=GIT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _blob_size(root, ref):
    proc = _git(root, 'cat-file', '-s', ref)
    if proc.returncode != 0:
        return None
    try:
        return int(_out(proc))
    except ValueError:
        return None


def _safe_rel(rel):
    """只接受工作区内的相对路径：拒绝绝对路径、盘符和 `..`。"""
    rel = _norm(rel)
    if not rel or rel.startswith('/') or ':' in rel.split('/')[0]:
        return None
    if any(part in ('..', '.') for part in rel.split('/')):
        return None
    return rel


def capture(snap, paths, backup_id=None):
    """把 paths 在**快照时刻**的内容导出到 `.autocase/backups/<id>/`，供之后手动还原。

    返回 (backup_id, entries)，entries 每项 `{path, action}`：
    - `restore`：快照时刻有内容，已备份到 `files/`；
    - `delete`：快照时刻不存在（这次是新建的），还原就是删掉；
    - `skip`：拿不到原始内容（未跟踪文件 / 文件过大 / 不是 git 仓库）。

    必须在生成刚结束时调用：`stash create` 出来的 commit 没有 ref，git gc 之后就取不到了。
    没有越界改动时不建目录，直接返回 (None, [])。
    """
    root = Path(snap.root)
    clean = [rel for rel in (_safe_rel(p) for p in paths) if rel]
    if not clean:
        return None, []
    token = backup_id if (backup_id and SAFE_ID.match(backup_id)) else (
        time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
    folder = root / BACKUP_DIR / token
    files_dir = folder / 'files'

    entries = []
    for rel in clean:
        if snap.mode != 'git':
            entries.append({'path': rel, 'action': 'skip',
                            'reason': '工作区不是 git 仓库，拿不到这次改动之前的原始内容'})
            continue
        ref = '%s:%s' % (snap.base, rel)
        size = _blob_size(root, ref)
        if size is None:
            if rel in snap.untracked:
                # 快照时就存在、但没被 git 跟踪：只有 (size, mtime)，没存内容。
                entries.append({'path': rel, 'action': 'skip',
                                'reason': '这个文件原本未被 git 跟踪，只记了指纹没存内容'})
            else:
                entries.append({'path': rel, 'action': 'delete'})
            continue
        if size > MAX_BACKUP_BYTES:
            entries.append({'path': rel, 'action': 'skip',
                            'reason': '文件超过 %d MB，没有备份内容' % (MAX_BACKUP_BYTES // 1024 // 1024)})
            continue
        blob = _git_bytes(root, 'show', ref)
        if blob is None:
            entries.append({'path': rel, 'action': 'skip', 'reason': '读取快照内容失败'})
            continue
        target = files_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
        entries.append({'path': rel, 'action': 'restore', 'size': len(blob)})

    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'manifest.json').write_text(
        json.dumps({'base': snap.base if snap.mode == 'git' else None,
                    'mode': snap.mode, 'entries': entries}, ensure_ascii=False, indent=2),
        encoding='utf-8')
    return token, entries


def read_manifest(root, backup_id):
    """读一份备份清单；id 不合法或文件不在就返回 None。"""
    if not backup_id or not SAFE_ID.match(str(backup_id)):
        return None
    path = Path(root) / BACKUP_DIR / str(backup_id) / 'manifest.json'
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def revert(root, backup_id, paths=None):
    """按清单把越界文件还原到这次生成之前的样子。

    `paths` 为空表示还原清单里的全部。还原前先把**当前**内容存到 `current/`，
    这样「还原」本身也是可逆的——模型写的东西不会被无声丢掉。
    """
    root = Path(root)
    manifest = read_manifest(root, backup_id)
    if manifest is None:
        raise GuardError('找不到这份备份清单：%s' % backup_id)
    folder = root / BACKUP_DIR / str(backup_id)
    wanted = None
    if paths:
        wanted = {rel for rel in (_safe_rel(p) for p in paths) if rel}

    reverted, skipped, failed = [], [], []
    for entry in manifest.get('entries') or []:
        rel = _safe_rel(entry.get('path'))
        if rel is None:
            continue
        if wanted is not None and rel not in wanted:
            continue
        action = entry.get('action')
        if action not in ('restore', 'delete'):
            skipped.append({'path': rel, 'reason': entry.get('reason') or '没有可还原的原始内容'})
            continue
        live = root / rel
        try:
            # 先把当前内容留一份，误还原也捞得回来。
            if live.is_file():
                keep = folder / 'current' / rel
                keep.parent.mkdir(parents=True, exist_ok=True)
                keep.write_bytes(live.read_bytes())
            if action == 'delete':
                if live.exists():
                    live.unlink()
                reverted.append({'path': rel, 'action': 'deleted'})
            else:
                source = folder / 'files' / rel
                if not source.is_file():
                    failed.append({'path': rel, 'reason': '备份文件丢了'})
                    continue
                live.parent.mkdir(parents=True, exist_ok=True)
                live.write_bytes(source.read_bytes())
                reverted.append({'path': rel, 'action': 'restored'})
        except OSError as exc:
            failed.append({'path': rel, 'reason': str(exc)})
    return {'reverted': reverted, 'skipped': skipped, 'failed': failed}
