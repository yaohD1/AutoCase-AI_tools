"""录制生成：收集上下文 → 调用 recording-emitter → 范围检查 → 校验 → 失败还原。

任何一步出错都还原工作区（新文件删除，已有文件恢复原内容），并返回具体错误。
"""
import json
from pathlib import Path

from app.services.recording import detect, guard, toolchain
from app.services.recording.locators import pick_for

RF_FRAMEWORK = 'robot-selenium'
PW_FRAMEWORK = 'playwright-ts'
MAX_REPAIR_ROUNDS = 1


class EmitError(Exception):
    """可向用户展示的失败原因，列表形式。"""

    def __init__(self, message, details=None):
        super().__init__(message)
        self.details = details or []


def target_ok(target_path):
    """目标路径必须是相对路径、不含 ..、不指向绝对路径。"""
    path = Path(target_path.replace('\\', '/'))
    if not target_path or path.is_absolute() or '..' in path.parts or target_path.startswith('/'):
        return False
    return True


def _steps_for_prompt(steps, framework):
    """把 IR 整理成 agent 能直接用的形式：每一步只带需要的定位与意图，不带内部字段。"""
    out = []
    for step in steps:
        item = {'no': step['no'], 'type': step['type'], 'text': step.get('text')}
        if step.get('value') is not None:
            item['value'] = step['value']
        if step.get('label') is not None:
            item['label'] = step['label']
        if step.get('url'):
            item['url'] = step['url']
        if step.get('intent'):
            item['intent'] = step['intent']
        if step.get('seconds') is not None:
            item['seconds'] = step['seconds']
        target = step.get('target')
        if target:
            item['target'] = {
                'alias': target.get('alias'),
                'status': target.get('status'),
                'locator': _locator_for(target, framework),
            }
        out.append(item)
    return out


def _locator_for(target, framework):
    """按目标框架选定位。返回给 agent 的是可直接使用的写法：RF 为 SeleniumLibrary 语法，TS 为 Playwright 描述。"""
    chosen = pick_for(framework, target.get('candidates') or [])
    if chosen is None:
        return None
    if framework == RF_FRAMEWORK:
        return chosen['spec']['args'][0]
    return chosen['desc']


def _prompt(payload):
    return (
        '请按下面的录制结果写入一个测试文件。\n'
        '只写 target_path 指定的文件，不写其他文件。\n'
        '```json\n' + json.dumps(payload, ensure_ascii=False, indent=2) + '\n```\n'
    )


def _payload(framework, steps, target_path, title, detected, keywords):
    return {
        'framework': framework,
        'target_path': target_path,
        'title': title,
        'steps': _steps_for_prompt(steps, framework),
        'evidence': detected.get('evidence', []),
        'samples': detected.get('samples', []),
        'keywords': keywords,
        'output_language': 'Simplified Chinese for progress notes; keep code and identifiers unchanged.',
    }


def _validate(framework, python, workspace, target_path):
    """返回 (是否通过, 输出)。缺工具时 toolchain 会抛 ToolchainError。"""
    path = Path(workspace) / target_path
    if framework == RF_FRAMEWORK:
        return toolchain.dryrun(python, path, cwd=str(workspace))
    return toolchain.tsc_check(workspace)


def generate(runner, workspace, model, steps, framework, target_path, title, python=None, overwrite=False):
    """执行一次录制生成。成功返回 {'path', 'changed', 'validated', 'reply'}；失败抛 EmitError（已还原）。"""
    if not target_ok(target_path):
        raise EmitError('目标路径无效：必须是工作区内的相对路径，不能含 ..')
    root = Path(workspace).resolve()
    if not root.is_dir():
        raise EmitError('工作区目录不存在：%s' % workspace)
    if framework not in (RF_FRAMEWORK, PW_FRAMEWORK):
        raise EmitError('暂不支持的框架：%s' % framework)
    if not steps:
        raise EmitError('没有可生成的步骤')
    # 按目标框架检查每一步是否都有唯一定位；有一步不唯一就整体拒绝，不写任何文件。
    blocked = []
    for step in steps:
        target = step.get('target')
        if target and pick_for(framework, target.get('candidates') or []) is None:
            blocked.append(step['no'])
    if blocked:
        raise EmitError('有定位不唯一的步骤，无法生成：第 %s 步' % '、'.join(str(n) for n in blocked),
                        details=['ambiguous:%s' % n for n in blocked])

    # 检测结果只作提示（决策 12），与用户选择的框架不一致时也不阻止，由返回值中的 detected 说明。
    detected = detect.detect(root, python=python)

    keywords = []
    if framework == RF_FRAMEWORK:
        if not detected.get('python'):
            raise EmitError('找不到 Python 环境，无法导出 SeleniumLibrary 关键字')
        try:
            keywords = toolchain.export_keywords(detected['python'])['keywords']
        except toolchain.ToolchainError as exc:
            raise EmitError(str(exc))

    target_abs = root / target_path
    existed = target_abs.exists()
    if existed and not overwrite:
        raise EmitError('目标文件已存在：%s。请选择覆盖或改名。' % target_path,
                        details=['conflict:' + target_path])

    before = guard.snapshot(root)
    try:
        reply = runner.emit_recording(
            root, model, _prompt(_payload(framework, steps, target_path, title, detected, keywords))
        )
        if not target_abs.is_file():
            raise EmitError('agent 没有写出目标文件', details=[reply[:500]] if reply else [])

        outside = guard.violations(guard.changed(before, root), target_path)
        if outside:
            raise EmitError('agent 修改了目标文件以外的文件，已还原', details=outside)

        validated, output = _check_with_repair(runner, root, model, framework, target_path, detected)
        if not validated:
            raise EmitError('校验未通过，已还原', details=[output])
    except EmitError as exc:
        _restore(root, before, target_path, existed)
        raise
    except toolchain.ToolchainError as exc:
        _restore(root, before, target_path, existed)
        raise EmitError(str(exc))
    except (RuntimeError, guard.GuardError) as exc:
        _restore(root, before, target_path, existed)
        raise EmitError(str(exc))

    return {
        'path': target_path,
        'changed': guard.changed(before, root),
        'validated': True,
        'framework': framework,
        'detected': detected.get('framework'),
        'reply': reply,
    }


def _check_with_repair(runner, root, model, framework, target_path, detected):
    """校验；失败则把输出交回 agent 修一次；仍失败返回 (False, 输出)。"""
    python = detected.get('python')
    ok, output = _validate(framework, python, root, target_path)
    rounds = 0
    while not ok and rounds < MAX_REPAIR_ROUNDS:
        rounds += 1
        fix = (
            '上一次写入的 %s 校验失败。请只修改该文件，修正以下错误，不要改动其他文件。\n'
            '校验输出：\n```\n%s\n```\n' % (target_path, output)
        )
        runner.emit_recording(root, model, fix)
        ok, output = _validate(framework, python, root, target_path)
    return ok, output


def _restore(root, before, target_path, existed):
    """失败时还原：只还原 agent 动过的文件，其余保持不动。existed 仅用于说明，还原依据是备份本身。"""
    changed = guard.changed(before, root)
    if changed:
        guard.restore(root, before, changed)
