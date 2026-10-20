"""录制生成的范围检查与还原（方案 A：只允许用户指定的目标文件改动）。

运行 agent 前备份工作区文件内容；运行后比对。只要有目标文件以外的改动，或校验失败，
就把工作区恢复到备份时的状态。不依赖 git，工作区是不是仓库都可以。
"""
import os
from pathlib import Path

from app.services.recording.detect import SKIP_DIRS

# opencode serve 启动时自动导出的框架产物，不算越界（与 opencode_runner._file_scope 保持一致）。
FRAMEWORK_ARTIFACT_PREFIXES = ('.claude/agents/', '.github/agents/', '.playwright-mcp/', '.autocase/')
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024


class GuardError(Exception):
    pass


def _rel(root, path):
    return path.relative_to(root).as_posix()


def _is_artifact(rel):
    return rel.startswith(FRAMEWORK_ARTIFACT_PREFIXES)


def snapshot(workspace):
    """备份工作区文件：{相对路径: bytes}。超过上限时拒绝执行（不静默跳过大文件）。"""
    root = Path(workspace).resolve()
    contents = {}
    total = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            path = Path(dirpath) / name
            try:
                size = path.stat().st_size
            except OSError:
                continue
            rel = _rel(root, path)
            if _is_artifact(rel):
                continue
            if size > MAX_FILE_BYTES:
                raise GuardError('工作区文件过大，无法安全备份：%s（%d 字节）' % (rel, size))
            total += size
            if total > MAX_TOTAL_BYTES:
                raise GuardError('工作区体积超过备份上限，请把录制输出指向较小的项目目录')
            try:
                contents[rel] = path.read_bytes()
            except OSError:
                raise GuardError('无法读取工作区文件以备份：%s' % rel)
    return contents


def changed(before, workspace):
    """与备份相比发生变化的相对路径（新增、修改、删除），排除框架产物。"""
    root = Path(workspace).resolve()
    now = snapshot(root)
    out = set()
    for rel in set(before) | set(now):
        if _is_artifact(rel):
            continue
        if before.get(rel) != now.get(rel):
            out.add(rel)
    return sorted(out)


def violations(changed_paths, target):
    """除目标文件以外的改动都是越界。"""
    return [p for p in changed_paths if p != target]


def restore(workspace, before, paths):
    """把 paths 恢复到备份状态：备份里有就写回原内容，备份里没有（新建的）就删除。"""
    root = Path(workspace).resolve()
    for rel in paths:
        path = (root / rel).resolve()
        if root not in path.parents and path != root:
            raise GuardError('拒绝还原工作区外的路径：%s' % rel)
        if rel in before:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(before[rel])
        elif path.is_file():
            path.unlink()
