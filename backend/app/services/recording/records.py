"""录制生成的记录：预览、提交、回滚。

只服务于录制生成，数据在 recording_generations 表里，不读写 automation_generations。
提交与回滚只针对记录里的那一个目标文件；文件在生成后被改动时拒绝操作，不静默覆盖。
"""
import base64
import hashlib
import json
import subprocess
from pathlib import Path

from app.models import db
from app.models.recording_generation import RecordingGeneration

GIT_TIMEOUT = 60
PREVIEW_LIMIT = 20000
MAX_MESSAGE = 500


class RecordError(Exception):
    pass


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _target(record):
    return Path(record.workspace) / record.target_path


def _summary(steps):
    return [{'no': s.get('no'), 'type': s.get('type'), 'text': s.get('text'), 'value': s.get('value')}
            for s in steps]


def create(workspace, target_path, framework, title, model, original, generated, steps, reply):
    """original / generated 为字节内容；original 为 None 表示生成前文件不存在。"""
    record = RecordingGeneration(
        workspace=str(Path(workspace).resolve()),
        target_path=target_path,
        framework=framework,
        title=title,
        model=model,
        status='generated',
        original_existed=original is not None,
        original_content=base64.b64encode(original).decode('ascii') if original is not None else None,
        generated_sha256=_sha(generated),
        step_summary=json.dumps(_summary(steps), ensure_ascii=False),
        reply=reply or '',
    )
    db.session.add(record)
    db.session.commit()
    return record


def _current_bytes(record):
    path = _target(record)
    return path.read_bytes() if path.is_file() else None


def _ensure_unchanged(record):
    current = _current_bytes(record)
    if current is None or _sha(current) != record.generated_sha256:
        raise RecordError('目标文件在生成后已被改动或删除，请先确认后再提交或回滚')


def _git(workspace, args, timeout=GIT_TIMEOUT, check=True):
    result = subprocess.run(
        ['git', '-C', str(workspace), *args],
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or '').strip()[-400:]
        raise RecordError('git %s 失败：%s' % (args[0], detail))
    return result


def preview(record):
    current = _current_bytes(record)
    text = current.decode('utf-8', errors='replace') if current is not None else ''
    return {
        'content': text[:PREVIEW_LIMIT],
        'truncated': len(text) > PREVIEW_LIMIT,
        'file_exists': current is not None,
        'changed_since_generation': current is None or _sha(current) != record.generated_sha256,
        'reply': record.reply or '',
    }


def commit(record, message):
    if record.status != 'generated':
        raise RecordError('只有未提交的记录可以提交')
    message = (message or '').strip()
    if not message:
        raise RecordError('提交说明不能为空')
    if len(message) > MAX_MESSAGE:
        raise RecordError('提交说明过长')
    _ensure_unchanged(record)
    workspace = record.workspace
    if _git(workspace, ['rev-parse', '--is-inside-work-tree'], check=False).stdout.strip() != 'true':
        raise RecordError('工作区不是 Git 仓库，无法提交')
    if not _git(workspace, ['status', '--porcelain', '--', record.target_path]).stdout.strip():
        raise RecordError('目标文件没有可提交的变化')
    _git(workspace, ['add', '--', record.target_path])
    _git(workspace, ['commit', '--only', '-m', message, '--', record.target_path], timeout=120)
    head = _git(workspace, ['rev-parse', 'HEAD']).stdout.strip()
    record.status = 'committed'
    record.commit_hash = head
    record.commit_message = message
    db.session.commit()
    return record


def revert(record):
    if record.status == 'generated':
        _ensure_unchanged(record)
        target = _target(record)
        if record.original_existed:
            target.write_bytes(base64.b64decode(record.original_content))
        elif target.is_file():
            target.unlink()
    elif record.status == 'committed':
        if not record.commit_hash:
            raise RecordError('记录缺少提交哈希，无法回滚')
        result = _git(record.workspace, ['revert', '--no-edit', record.commit_hash], check=False, timeout=120)
        if result.returncode != 0:
            _git(record.workspace, ['revert', '--abort'], check=False)
            detail = (result.stderr or result.stdout or '').strip()[-400:]
            raise RecordError('git revert 失败，已取消：%s' % detail)
    else:
        raise RecordError('该记录已回滚')
    record.status = 'reverted'
    db.session.commit()
    return record
