"""录制生成：收集上下文 → 调用 recording-emitter → 范围检查 → 校验 → 失败还原。

任何一步出错都还原工作区（新文件删除，已有文件恢复原内容），并返回具体错误。
"""
import json
from pathlib import Path

from app.services.recording import detect, guard, toolchain
from app.services.recording.locators import best_effort, pick_for, pool_for

RF_FRAMEWORK = 'robot-selenium'
PW_FRAMEWORK = 'playwright-ts'
MAX_REPAIR_ROUNDS = 1
MAX_CANDIDATES_IN_PROMPT = 5
LOCATOR_RULES = (
    '要求：\n'
    '- 只写 target_path 指定的文件，不改其他文件。\n'
    '- 定位优先用 target.locator；不要自己编造选择器，需要更精确时只能从 target.candidates 里挑或组合。\n'
    '- target.match_count > 1 表示这条定位在页面上匹配了多个元素，属于「不唯一但仍可用」：\n'
    '  · 给了 target.nth，就取第 nth 个（下标从 0 开始）：\n'
    '    playwright-ts 写 locator.nth(n)；\n'
    '    robot-selenium 写 xpath=(<target.locator 去掉 xpath= 前缀后的表达式>)[n+1]，不要改写表达式本身；\n'
    '  · 没给 nth，就取第一个，并在该行上方加一行注释：匹配 N 个元素，取第 1 个，请确认。\n'
    '- target.match_count 为 null 时这一步拿不到定位，写一行 TODO 注释说明，不要猜。\n'
    '- step.page_index 大于 0 表示这一步发生在第几个标签页（按打开顺序，从 0 开始）：'
    'playwright-ts 取 context.pages[i]，robot-selenium 用 Switch Window 配合 ${windows}[i]（窗口句柄顺序与打开顺序一致）。\n'
    '- target.frame 存在表示元素在 iframe 里：playwright-ts 用 page.frameLocator(frame.url)，'
    'robot-selenium 先 Select Frame（优先 frame.name，没有 name 就用 src 的 xpath）。'
    '不要在顶层 page 上直接定位 frame 内的元素。\n'
)

# 两套框架的差异不只在定位写法，还在「有没有自动等待」——这是脚本能不能稳定跑起来的分水岭。
# SeleniumLibrary 拿到元素就操作，页面没渲染好直接失败；Playwright 自带 auto-wait。
FRAMEWORK_RULES = {
    'robot-selenium': (
        '框架写法（SeleniumLibrary，没有自动等待，每条交互前必须显式等待）：\n'
        '- 打开页面 Go To；点击 Click Element；输入 Input Text / Input Password；'
        '下拉用 Select From List By Label（或 By Value），不要用 Click Element 点原生 select。\n'
        '- 任何交互前先 Wait Until Element Is Visible（超时 10s），不要用 Sleep 代替等待。\n'
        '- 断言/等待映射：visible → Wait Until Element Is Visible；hidden → Wait Until Element Is Not Visible；'
        'text_contains → Wait Until Element Contains；text_equals → Wait Until Keyword Succeeds | 10x | 500ms | '
        'Element Text Should Be；value_equals → Wait Until Keyword Succeeds + Textfield Value Should Be；'
        'url_contains → Wait Until Location Contains；enabled → Wait Until Keyword Succeeds + Element Should Be Enabled。\n'
        '- 新标签页用 Switch Window 配合 Wait Until Keyword Succeeds；frame 内操作前先 Select Frame。\n'
        '- target.locator 已带 xpath= 前缀，原样使用，不要再包 css= 或再拼前缀。\n'
    ),
    'playwright-ts': (
        '框架写法（Playwright，自带 auto-wait）：\n'
        '- 直接 click / fill / selectOption，不要用 waitForTimeout 充当等待。\n'
        '- 断言用 expect(locator)：toBeVisible / toBeHidden / toHaveText / toHaveValue；'
        '地址断言用 expect(page).toHaveURL(...)。\n'
        '- 新标签页用 context.waitForEvent("page")；frame 内用 page.frameLocator(目标 frame 的 url)。\n'
    ),
}


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
            info = _locator_info(target, framework)
            item['target'] = {
                'alias': target.get('alias'),
                'status': target.get('status'),
                'locator': info['locator'] if info else None,
                'match_count': info['matches'] if info else None,
                'nth': info['nth'] if info else None,
                'candidates': _candidates_for_prompt(target, framework),
            }
            # 元素在 iframe 里时，模型必须知道要先切框架，否则会写成顶层 page.getByRole。
            snap = target.get('snap') or {}
            if snap.get('frame_url') or snap.get('frame_name'):
                item['target']['frame'] = {'url': snap.get('frame_url'), 'name': snap.get('frame_name')}
        # 标签页序号：0 是主标签页，非 0 说明这一步是在别的标签页里发生的。
        if step.get('page_index'):
            item['page_index'] = step['page_index']
        out.append(item)
    return out


def _fmt(candidate, framework):
    """把一个候选变成目标框架里可以直接写的定位字符串。"""
    if framework == RF_FRAMEWORK:
        return candidate['spec']['args'][0]
    return candidate['desc']


def _locator_info(target, framework):
    """选定位：有唯一的就用唯一的；没有就降级到「命中最少」的那条 + 录制时的序号。

    返回 {'locator', 'matches', 'nth', 'mode'}；连一条能命中的候选都没有时返回 None。
    """
    counted = target.get('candidates') or []
    chosen = pick_for(framework, counted)
    if chosen is not None:
        return {'locator': _fmt(chosen, framework), 'matches': chosen['count'], 'nth': None, 'mode': 'unique'}
    best = best_effort(framework, counted)
    if best is None:
        return None
    return {
        'locator': _fmt(best, framework),
        'matches': best['count'],
        'nth': best.get('index'),
        'mode': 'ambiguous',
    }


def _candidates_for_prompt(target, framework):
    """给 agent 的候选清单：只留有命中的、最多 5 条，避免提示词被撑爆。"""
    out = []
    for candidate in pool_for(framework, target.get('candidates') or []):
        if candidate.get('count', -1) <= 0:
            continue
        out.append({'locator': _fmt(candidate, framework), 'count': candidate['count'], 'nth': candidate.get('index')})
        if len(out) >= MAX_CANDIDATES_IN_PROMPT:
            break
    return out


def _prompt(payload):
    return (
        '请按下面的录制结果写入一个测试文件。\n'
        '只写 target_path 指定的文件，不写其他文件。\n'
        + LOCATOR_RULES
        + FRAMEWORK_RULES.get(payload.get('framework'), '')
        + '```json\n' + json.dumps(payload, ensure_ascii=False, indent=2) + '\n```\n'
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
    # 定位不唯一不再拦整体生成：降级成「最窄候选 + 录制时的序号」，交给 agent 写 nth。
    # 只有连一条候选都命不中（元素已经不在页面上）才拒绝——那种情况写不出任何定位。
    blocked, degraded = [], []
    for step in steps:
        target = step.get('target')
        if not target:
            continue
        info = _locator_info(target, framework)
        if info is None:
            blocked.append(step['no'])
        elif info['mode'] == 'ambiguous':
            degraded.append({
                'no': step['no'],
                'alias': target.get('alias'),
                'locator': info['locator'],
                'matches': info['matches'],
                'nth': info['nth'],
            })
    if blocked:
        raise EmitError(
            '第 %s 步的目标元素在页面上已经找不到（匹配数为 0），请重新拾取后再生成'
            % '、'.join(str(n) for n in blocked),
            details=['missing:%s' % n for n in blocked],
        )

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
        # 哪些步骤是降级生成的（定位不唯一，按第 n 个匹配写入），前端据此提醒用户回放确认。
        'degraded': degraded,
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
