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
    '- intent.type 为 url_contains / page_text_contains / sleep 时没有 target：'
    'url_contains 断言整条地址，page_text_contains 断言整个页面的文字，不要为它们编造元素定位。\n'
    '- step.type 为 reload 表示「刷新当前标签页」：这一步没有 target 也没有 url。\n'
    '- step.type 为 press 表示「在目标元素上按一个键」，step.value 是键名（如 Enter、Control+Enter）。\n'
    '- step.type 为 dialog 表示「上一步操作会弹出原生对话框」，它没有 target，也没有定位。'
    'step.value 是 accept 或 dismiss，step.dialog.message 是弹窗文字（只用于写注释，不要拿去断言）。'
    '弹窗这一步本身不产生新动作，只是把「上一步会引起弹窗、以及怎么处理」记下来。\n'
    '- target.match_count > 1 表示这条定位在页面上匹配了多个元素，属于「不唯一但仍可用」：\n'
    '  · 给了 target.nth，就取第 nth 个（下标从 0 开始）：\n'
    '    playwright-ts 写 locator.nth(n)；\n'
    '    robot-selenium 写 xpath=(<target.locator 去掉 xpath= 前缀后的表达式>)[n+1]，不要改写表达式本身；\n'
    '  · 没给 nth，就取第一个，并在该行上方加一行注释：匹配 N 个元素，取第 1 个，请确认。\n'
    '- target.match_count 为 null 时这一步拿不到定位，写一行 TODO 注释说明，不要猜。\n'
    '- target.match_count 为 0 表示「这一步会把页面带走」（回车提交、点掉一个链接），'
    '元素已经不在当前页面上，定位是按快照推出来的、没核实过：照常按 target.locator 写，'
    '不要因为它为 0 就跳过这一步，也不要改写这条定位。\n'
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
        'url_contains → Wait Until Location Contains；enabled → Wait Until Keyword Succeeds + Element Should Be Enabled；'
        'page_text_contains → Wait Until Page Contains。\n'
        '- 新标签页用 Switch Window 配合 Wait Until Keyword Succeeds；frame 内操作前先 Select Frame。\n'
        '- 刷新页面（type 为 reload）用 Reload Page，不要写成 Go To 同一地址（重新请求的语义不同）。'
        '刷新后文档是新的，后续每条交互仍要先 Wait Until Element Is Visible。\n'
        '- 按键（type 为 press）用 Press Keys    ${loc}    ${键名}，键名用 Selenium 的写法：'
        'Enter → ENTER，Escape → ESCAPE，Tab → TAB，Backspace → BACKSPACE，Delete → DELETE，'
        'ArrowDown → ARROW_DOWN，PageDown → PAGE_DOWN，Space → SPACE，修饰键 Control / Alt / Shift / Meta '
        '→ CTRL / ALT / SHIFT / META，组合键用 + 连接（Control+Enter → CTRL+ENTER）。\n'
        '- 弹窗（type 为 dialog）用 Handle Alert    ACCEPT（dismiss 时写 DISMISS），'
        '位置就放在触发它的那一步之后。Selenium 的 alert 是同步出现的，顺序写即可，不要为此改写前面的步骤。\n'
        '- 等待（type 为 wait，intent 带 timeout）：超时必须写成 intent.timeout 秒，不要用默认的 5 秒。\n'
        '  · intent.reload 为 false 时，按 intent.type 选带超时的原生关键字：\n'
        '    visible → Wait Until Element Is Visible    ${loc}    ${timeout}s\n'
        '    hidden → Wait Until Element Is Not Visible    ${loc}    ${timeout}s\n'
        '    text_contains → Wait Until Element Contains    ${loc}    ${value}    ${timeout}s\n'
        '    page_text_contains → Wait Until Page Contains    ${value}    ${timeout}s\n'
        '    url_contains → Wait Until Location Contains    ${value}    ${timeout}s\n'
        '    text_equals / value_equals / enabled 没有原生等待版，用 '
        'Wait Until Keyword Succeeds    ${timeout}s    1s    包住对应的 Should 断言。\n'
        '  · intent.reload 为 true：这类是「页面不会自己更新，要反复刷新才知道结果」，写\n'
        '    Wait Until Keyword Succeeds    ${timeout}s    ${interval}s    Reload And Check\n'
        '    并在 *** Keywords *** 里定义 Reload And Check：先 Reload Page，再用一个**小于 interval** 的超时判断'
        '（interval 为 5s 就写 3s）。内层超时若大于等于 interval，单次判断就吃满整个节奏，轮询会退化成慢速串行。\n'
        '    条件不是可见性时（文字包含、地址包含等），Reload And Check 里换成对应的 Should 断言即可。\n'
        '    不要用 Sleep 拼循环，也不要用 FOR 手写重试。\n'
        '- target.locator 已带 xpath= 前缀，原样使用，不要再包 css= 或再拼前缀。\n'
    ),
    'playwright-ts': (
        '框架写法（Playwright，自带 auto-wait）：\n'
        '- 直接 click / fill / selectOption，不要用 waitForTimeout 充当等待。\n'
        '- 断言用 expect(locator)：toBeVisible / toBeHidden / toHaveText / toHaveValue；'
        '地址断言用 expect(page).toHaveURL(...)；整页文字断言用 expect(page.locator("body")).toContainText(...)。\n'
        '- 新标签页用 context.waitForEvent("page")；frame 内用 page.frameLocator(目标 frame 的 url)。\n'
        '- 刷新页面（type 为 reload）用 page.reload()，不要写成 page.goto(page.url())：'
        '后者在 SPA 里可能根本不重载。\n'
        '- 按键（type 为 press）用 await ${loc}.press("${step.value}")，键名原样用 step.value，不要翻译。\n'
        '- 弹窗（type 为 dialog）：Playwright 必须在弹窗出现**之前**注册处理方式，等走到 dialog 这一步再注册'
        '已经晚了（页面在弹窗出现那一刻就挂起了）。所以把下面这行写在**上一步之前**：\n'
        '    page.once("dialog", async (dialog) => { await dialog.accept(); });\n'
        '  step.value 为 dismiss 时写 await dialog.dismiss()。dialog 这一步本身不产生代码，'
        '只在上一步那行前后留一句注释说明弹窗文字。\n'
        '- 等待（type 为 wait，intent 带 timeout）：超时必须用 intent.timeout 换算成毫秒，'
        '不要用默认的 5 秒。\n'
        '  · intent.reload 为 false：直接给对应的 expect matcher 加 { timeout }，例如\n'
        '    expect(loc).toBeVisible({ timeout }) / toBeHidden / toContainText(value, { timeout }) / '
        'toHaveText / toHaveValue / toBeEnabled；\n'
        '    地址用 expect(page).toHaveURL(new RegExp(...), { timeout })，'
        '整页文字用 expect(page.locator("body")).toContainText(value, { timeout })。\n'
        '  · intent.reload 为 true：Playwright 没有「刷到满足为止」的原生 API，用 expect.poll：\n'
        '    await expect.poll(async () => { await page.reload(); return <即时判断>; },'
        ' { timeout: <timeout*1000>, intervals: [<interval*1000>] }).toBe(true);\n'
        '    即时判断按 intent.type 写，不能套 matcher（matcher 自己会等，会把间隔吃掉）：\n'
        '    visible → await loc.isVisible()；hidden → !(await loc.isVisible())；\n'
        '    text_contains → ((await loc.textContent()) || "").includes(value)；\n'
        '    text_equals → (((await loc.textContent()) || "").trim()) === value；\n'
        '    value_equals → (await loc.inputValue()) === value；enabled → await loc.isEnabled()；\n'
        '    url_contains → page.url().includes(value)；'
        'page_text_contains → ((await page.locator("body").innerText()) || "").includes(value)。\n'
        '    也不要用 while(true) 手写循环。\n'
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
        # 带最长等待就是轮询等待。老数据里可能存在 type 仍是 assert 的，这里统一成 wait，
        # 免得提示词里「立即断言」和「等到成立」两套写法各自为政。
        if step['type'] in ('assert', 'wait') and (step.get('intent') or {}).get('timeout'):
            item['type'] = 'wait'
        if step.get('value') is not None:
            item['value'] = step['value']
        if step.get('label') is not None:
            item['label'] = step['label']
        if step.get('url'):
            item['url'] = step['url']
        if step.get('intent'):
            item['intent'] = step['intent']
        if step.get('dialog'):
            item['dialog'] = step['dialog']
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
        # 录制那一刻页面就跳走了，元素一条都数不到。这类步骤的定位本来就是按快照推的，
        # 放行但标 stale 让用户在回放里确认；真正失效的步骤没有 unverified 标记，仍然拦下。
        if target.get('unverified'):
            pool = pool_for(framework, counted)
            if pool:
                return {'locator': _fmt(pool[0], framework), 'matches': 0, 'nth': None, 'mode': 'stale'}
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
