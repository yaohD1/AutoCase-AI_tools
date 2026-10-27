"""录制生成：出任务书（只读）→ 按任务书写脚本 → 范围检查 → 校验。

两步是分开的：`plan()` 只读项目、产出任务书，不写文件；`generate()` 拿着用户确认过的
任务书写入。任务书里的 target_path 只当建议，真正的写入路径与权限永远以请求为准。

越界靠**预防**而不是事后回滚：emitter 运行时只对目标文件开放写权限（见
`OpenCodeRunner.emit_config`），其余文件写入会被 opencode 直接拒绝；这里的范围检查是
事后确认，只报告、不回滚——回滚会把用户在这期间自己的编辑一起抹掉。
失败时不删文件，只把「已经写了什么、写在哪」告诉用户。
"""
import json
import logging
import re
from pathlib import Path

from app.services.recording import detect, guard, toolchain
from app.services.recording.locators import best_effort, pick_for, pool_for

logger = logging.getLogger(__name__)

RF_FRAMEWORK = 'robot-selenium'
PW_FRAMEWORK = 'playwright-ts'
MAX_REPAIR_ROUNDS = 1
MAX_CANDIDATES_IN_PROMPT = 5
# 任务书会原样进写手提示词、也会经前端回传，体积必须有上限，
# 否则模型偶尔吐一份巨型 JSON 就能把一次生成撑爆。
MAX_PLAN_BYTES = 64 * 1024
MAX_PLAN_FEEDBACK = 20
LOCATOR_RULES = (
    '要求：\n'
    '- 只写 target_path 指定的那一个文件。运行环境只给它开了写权限，'
    '写其他任何文件都会被拒绝，并导致本次生成失败；不要尝试创建辅助文件、临时文件或改配置。\n'
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
    '  · 给了 target.matches 时，把这几个匹配分别是什么写进同一行注释里，例如：\n'
    '    // 页面上有 3 个「删除」：设备A 在线 / 设备B 离线 / 设备C 在线，这里取第 2 个\n'
    '    matches.each 就是被匹配到的元素内容（不唯一时才有，取值优先用 each，不要自己编）。\n'
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
    """目标路径必须是相对路径、不含 ..；也不能带 glob 元字符。

    写权限是按路径做模式匹配给的，`*`、`?`、`[`、`]` 在 glob 里有含义，
    真出现会被当成通配符，等于把写权限放大到别的文件上。
    """
    path = Path(target_path.replace('\\', '/'))
    if not target_path or path.is_absolute() or '..' in path.parts or target_path.startswith('/'):
        return False
    return not any(ch in target_path for ch in '*?[]')


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
            # 用户在界面上看过框、亲手挑的那个，要让它出现在提示词里，
            # 否则模型只看到「取第 2 个」，却不知道这 2 个分别是什么，也就写不出有意义的注释。
            matched = _match_texts(target)
            if matched:
                item['target']['matches'] = matched
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


def _match_texts(target):
    """不唯一时，把「这批匹配分别是什么」压成一句能给模型看的文字。

    优先用行文本（所在 tr/li 的内容）：三个「删除」按钮的自身文本完全相同，
    只有行文本能把它们区分开。取不到行文本才退回元素自身的文本。
    """
    matches = target.get('matches') or {}
    items = matches.get('items') or []
    if not items:
        return None
    each = []
    for item in items[:6]:
        text = (item.get('text') or '').strip()
        row = (item.get('row') or '').strip()
        each.append(row or text or item.get('tag') or '元素')
    return {'of': matches.get('count') or len(items), 'each': each}


def _locator_info(target, framework):
    """选定位：有唯一的就用唯一的；没有就降级到「命中最少」的那条 + 录制时的序号。

    返回 {'locator', 'matches', 'nth', 'mode'}；连一条能命中的候选都没有时返回 None。
    """
    counted = target.get('candidates') or []
    # 用户看过框、亲手挑过的，优先于任何自动选择：他选的是这条候选的第 index 个，
    # 换一条候选就不是同一个元素（不同选择器的匹配顺序不一样）。
    picked = target.get('chosen')
    if picked:
        allowed = {c['kind'] for c in pool_for(framework, counted)}
        hit = next((c for c in counted if c['desc'] == picked.get('desc')), None)
        if hit is not None and hit['kind'] in allowed and (hit.get('count') or 0) > 1:
            return {'locator': _fmt(hit, framework), 'matches': hit['count'],
                    'nth': picked.get('index'), 'mode': 'chosen'}
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


PLAN_RULES = (
    '规划要求：\n'
    '- 任务书里的每个 file / symbol / import 必须来自你真正读过的文件，不许编造。'
    '项目里没有现成封装时 reusable 留空数组，不要硬凑。\n'
    '- steps 要覆盖每一个录制步骤，顺序一致。只有「这个封装覆盖了该步的全部意图」才标 reuse，'
    '其余一律 write；标 reuse 时把要传的参数写进 args。每个步骤都要写 note，'
    '说清这一步具体怎么定位、怎么断言，写手会照它施工。\n'
    '- target_path 是用户配置的写入位置，必须原样返回，不要改动它。'
    '如果你判断这个位置不合适（例如测试框架只会收集某个目录，根目录的脚本根本不会被执行），'
    '把建议路径写进 target_path_suggestion、把依据写进 target_path_reason，'
    '但 target_path 仍要保留原值。\n'
    '- 如果给了 feedback，必须逐条落实：quote 是用户选中的任务书原文，comment 是他的要求。'
    '和任务书原文冲突时以 feedback 为准，并在 changes 里说明每条意见怎么改的。\n'
    '- 你只读不写。确实需要改目标文件以外的文件才能跑通时，写进 needs，不要试图绕过，'
    '也不要复制那些文件的内容进来。\n'
    '- 只输出一个 JSON 对象，不要代码块、不要解释文字。\n'
)


def _plan_step(step):
    """规划阶段只需要「这一步在干什么」，不需要候选定位串。

    给 planner 完整 candidates 会让提示词暴涨，而且它要做的是「项目里有没有现成封装」，
    和「这个元素有几条 css 候选」无关。元素身份用 alias / tag / text 表达就够了。
    """
    item = {'no': step['no'], 'type': step['type']}
    for key in ('text', 'value', 'label', 'url', 'seconds'):
        if step.get(key) is not None:
            item[key] = step[key]
    if step.get('intent'):
        item['intent'] = step['intent']
    target = step.get('target') or {}
    if target:
        snap = target.get('snap') or {}
        item['target'] = {
            'alias': target.get('alias'),
            'tag': snap.get('tag'),
            'text': ((snap.get('text') or '')[:40] or None),
        }
        item['target'] = {k: v for k, v in item['target'].items() if v}
    return item


def _clean_feedback(feedback):
    """用户选中的片段 + 他的意见。空意见丢掉；长度截断，避免一份任务书被评论撑爆。"""
    out = []
    for item in (feedback or [])[:MAX_PLAN_FEEDBACK]:
        if not isinstance(item, dict):
            continue
        comment = str(item.get('comment') or '').strip()
        if not comment:
            continue
        out.append({
            'quote': str(item.get('quote') or '').strip()[:400],
            'comment': comment[:800],
        })
    return out


def _plan_payload(framework, steps, target_path, title, feedback=None, previous_plan=None):
    payload = {
        'framework': framework,
        'target_path': target_path,
        'title': title,
        'steps': [_plan_step(step) for step in steps],
        'output_language': 'Simplified Chinese',
    }
    notes = _clean_feedback(feedback)
    if notes:
        payload['feedback'] = notes
    if previous_plan:
        # steps_sig 是服务端用来判过期的，不该混进给模型看的上一版里。
        payload['previous_plan'] = {k: v for k, v in previous_plan.items() if k != 'steps_sig'}
    return payload


def _plan_prompt(payload):
    head = (
        '下面是一条录制好的测试流程。请先扫一遍这个项目，再产出任务书。\n'
        '你没有写权限，也不应该尝试写任何文件。\n'
    )
    if payload.get('feedback'):
        head += '用户对上一版任务书提了意见，这次必须按意见修订，不要装作没看见。\n'
    return (
        head
        + PLAN_RULES
        + '```json\n' + json.dumps(payload, ensure_ascii=False, indent=2) + '\n```\n'
    )


def _extract_plan(text):
    """从模型回复里抠出任务书。抠不出来返回 (None, 原因)，不抛异常。

    这一步只负责「把 JSON 拿出来」；是否可用由调用方判定，因为规划已经是独立一步，
    失败要明确报给用户，而不是悄悄降级。
    """
    if not text:
        return None, '规划没有返回内容'
    raw = None
    fenced = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.S)
    if fenced:
        raw = fenced.group(1)
    else:
        start, end = text.find('{'), text.rfind('}')
        if start >= 0 and end > start:
            raw = text[start:end + 1]
    if not raw:
        return None, '规划回复里没有 JSON'
    try:
        data = json.loads(raw)
    except ValueError as exc:
        return None, '任务书不是合法 JSON：%s' % exc
    if not isinstance(data, dict):
        return None, '任务书不是对象'
    if not isinstance(data.get('steps'), list):
        data['steps'] = []
    return data, None


def _normalize_plan(plan, target_path):
    """把任务书整理成「路径只当建议」的形状。

    模型确实会顺手改 target_path（这次就改了：它读了 playwright.config.ts，
    发现 testDir 是 ./tests，于是把根目录的 spec 挪进了 tests/ai-chat/）。
    这个判断可能对，但**不能由它直接决定写哪里**：写路径要经过用户确认，
    而且验收和权限也只认请求里的 target_path。所以这里把原值强制写回，
    它改过的路径降级成 target_path_suggestion，交给界面展示。
    """
    data = dict(plan)
    raw_path = str(data.get('target_path') or '').strip().replace('\\', '/')
    explicit = str(data.get('target_path_suggestion') or '').strip().replace('\\', '/')
    reason = str(data.get('target_path_reason') or '').strip()
    suggestion = explicit or raw_path

    data['target_path'] = target_path
    if suggestion and suggestion != target_path and target_ok(suggestion):
        data['target_path_suggestion'] = suggestion
        data['target_path_reason'] = reason or '规划认为这个位置更符合项目约定'
    else:
        data.pop('target_path_suggestion', None)
        data.pop('target_path_reason', None)

    steps = []
    for item in data.get('steps') or []:
        if not isinstance(item, dict):
            continue
        try:
            no = int(item.get('no'))
        except (TypeError, ValueError):
            continue
        step = {'no': no, 'action': 'reuse' if item.get('action') == 'reuse' else 'write'}
        for key in ('target', 'args', 'note'):
            if item.get(key) is not None:
                step[key] = item[key]
        steps.append(step)
    data['steps'] = steps
    data['reusable'] = [x for x in (data.get('reusable') or []) if isinstance(x, dict)][:50]
    return data


def _step_numbers(steps):
    out = []
    for step in steps or []:
        if isinstance(step, dict):
            step = step.get('no')
        try:
            out.append(int(step))
        except (TypeError, ValueError):
            continue
    return sorted(set(out))


def steps_signature(steps):
    """录制步骤的指纹：序号 + 动作 + 元素别名。

    只比步骤号不够：删掉第 2 步再补一个新第 2 步，号对得上、内容已经完全变了。
    """
    parts = []
    for step in steps or []:
        target = step.get('target') or {}
        parts.append('%s:%s:%s' % (step.get('no'), step.get('type'), target.get('alias') or ''))
    return '|'.join(parts)


def plan_matches_steps(plan, steps):
    """任务书必须覆盖且只覆盖当前录制的步骤。

    录制被改过（删了一步、又加了一步）之后，旧任务书就不再可信：
    note 里写的还是旧步骤的做法，照它写会漏步骤或者写错顺序。
    """
    if _step_numbers((plan or {}).get('steps')) != _step_numbers(steps):
        return False
    sig = (plan or {}).get('steps_sig')
    return not sig or sig == steps_signature(steps)


def _prepare(workspace, steps, framework, target_path):
    """规划和写入共用的入口校验：路径、框架、步骤、以及「元素还在不在」。"""
    if not target_ok(target_path):
        raise EmitError('目标路径无效：必须是工作区内的相对路径，不能含 .. 或 * ? [ ]')
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
    return root, degraded


def plan(runner, workspace, model, steps, framework, target_path, title, python=None,
         feedback=None, previous_plan=None, progress=None):
    """只跑规划：读项目、出任务书，不写任何文件。

    返回值里的 plan 已经归一化：target_path 一定是请求里的路径，
    模型想改的那条只出现在 target_path_suggestion 里。
    """
    def mark(stage, detail=''):
        if progress is not None:
            progress(stage, detail)

    root, _ = _prepare(workspace, steps, framework, target_path)
    mark('planning')
    try:
        text = runner.emit_planning(
            root, model,
            _plan_prompt(_plan_payload(framework, steps, target_path, title,
                                       feedback=feedback, previous_plan=previous_plan)),
        )
    except Exception as exc:
        raise EmitError('规划失败：%s' % (str(exc) or exc.__class__.__name__))

    data, error = _extract_plan(text)
    if data is None:
        raise EmitError('任务书解析失败：%s' % error, details=[(text or '')[:500]])
    data = _normalize_plan(data, target_path)
    # 指纹由服务端算，不采信模型给的：写入时要拿它判断「录制有没有变过」。
    data['steps_sig'] = steps_signature(steps)
    if not data['steps']:
        raise EmitError('任务书没有覆盖任何录制步骤，请重试或换一个模型')
    if not plan_matches_steps(data, steps):
        raise EmitError('任务书与录制步骤对不上（录制可能变过），请重新规划')
    size = len(json.dumps(data, ensure_ascii=False))
    if size > MAX_PLAN_BYTES:
        raise EmitError('任务书过大（%d 字符），请重新规划' % size)

    logger.info('录制规划：%s 步 / %s 个复用项 / %s 项额外改动 / %s 字符',
                len(data['steps']), len(data['reusable']), len(data.get('needs') or []), size)
    logger.debug('录制规划：任务书全文 %s', json.dumps(data, ensure_ascii=False))
    return {
        'plan': data,
        'target_path': target_path,
        'suggestion': data.get('target_path_suggestion'),
        'reason': data.get('target_path_reason'),
        'steps': len(data['steps']),
        'chars': size,
    }


def _prompt(payload):
    head = (
        '请按下面的录制结果写入一个测试文件。\n'
        '只写 target_path 指定的文件，不写其他文件。\n'
        '- 唯一允许写入的路径就是最外层 target_path。任务书里若出现别的路径'
        '（包括 plan.target_path、plan.target_path_suggestion），一律不要采用，'
        '也不要据此新建目录或文件。\n'
    )
    if payload.get('plan'):
        head += (
            '本次已经有人替你读过项目，任务书在 plan 字段里。按它写：\n'
            '- project_style 是这套项目真实的写法，优先于下面任何通用规则；两者冲突时以 project_style 为准。\n'
            '- reusable 里列出的封装只要能覆盖某一步，就必须调用它，不要自己重写同样的动作。\n'
            '- steps 里标 reuse 的步骤按 target 和 args 调用；标 write 的步骤用录制的定位手写。\n'
            '- needs 里列出的文件你没有写权限，不要尝试改，也不要把它们的内容复制进来。\n'
        )
    else:
        head += (
            '没有任务书：动笔之前先自己看一眼项目和已有用例，跟上它们的写法再写。\n'
        )
    if payload.get('plan_feedback'):
        head += (
            '用户对任务书提了修改意见（plan_feedback：quote 是原文，comment 是他的要求）。\n'
            '这些意见优先于任务书本身：两者冲突时按用户意见写。\n'
        )
    return (
        head
        + LOCATOR_RULES
        + FRAMEWORK_RULES.get(payload.get('framework'), '')
        + '```json\n' + json.dumps(payload, ensure_ascii=False, indent=2) + '\n```\n'
    )


def _payload(framework, steps, target_path, title, detected, keywords, plan=None,
             plan_feedback=None):
    return {
        'framework': framework,
        'target_path': target_path,
        'title': title,
        'steps': _steps_for_prompt(steps, framework),
        'evidence': detected.get('evidence', []),
        'samples': detected.get('samples', []),
        'keywords': keywords,
        'plan': plan,
        # 用户对任务书的意见：选中片段 + 他自己的话。写手必须照这个改，
        # 否则「选中后评价」只是界面上的装饰。
        'plan_feedback': _clean_feedback(plan_feedback),
        'output_language': 'Simplified Chinese for progress notes; keep code and identifiers unchanged.',
    }


def _validate(framework, python, workspace, target_path):
    """返回 (是否通过, 输出)。缺工具时 toolchain 会抛 ToolchainError。"""
    path = Path(workspace) / target_path
    if framework == RF_FRAMEWORK:
        return toolchain.dryrun(python, path, cwd=str(workspace))
    return toolchain.tsc_check(workspace, target_path=target_path)


def generate(runner, workspace, model, steps, framework, target_path, title, python=None,
             overwrite=False, plan=None, plan_feedback=None, progress=None):
    """按任务书写入脚本。成功返回 {'path', 'changed', 'validated', 'reply', 'plan'}。

    这里**不再跑规划**：规划是独立的一步（见 plan），任务书由用户确认后随请求带进来。
    传 plan=None 仍然可以写，但那只用于测试/兜底；产品入口要求必须有任务书。
    """
    def mark(stage, detail=''):
        if progress is not None:
            progress(stage, detail)

    root, degraded = _prepare(workspace, steps, framework, target_path)

    # 检测结果只作提示（决策 12），与用户选择的框架不一致时也不阻止，由返回值中的 detected 说明。
    mark('detect')
    detected = detect.detect(root, python=python)

    keywords = []
    if framework == RF_FRAMEWORK:
        if not detected.get('python'):
            raise EmitError('找不到 Python 环境，无法导出 SeleniumLibrary 关键字')
        mark('detect', '正在导出 SeleniumLibrary 关键字')
        try:
            keywords = toolchain.export_keywords(detected['python'])['keywords']
        except toolchain.ToolchainError as exc:
            raise EmitError(str(exc))

    target_abs = root / target_path
    existed = target_abs.exists()
    if existed and not overwrite:
        raise EmitError('目标文件已存在：%s。请选择覆盖或改名。' % target_path,
                        details=['conflict:' + target_path])

    if plan:
        reusable = plan.get('reusable') or []
        needs = plan.get('needs') or []
        logger.info('录制生成：按任务书写入 %s 个复用项 / %s 项额外改动 / 风格=%s',
                    len(reusable), len(needs),
                    (plan.get('project_style') or {}).get('runner') or '未说明')
    else:
        logger.warning('录制生成：没有任务书，写手自己看项目')

    before = None
    try:
        # 快照本身也可能失败（git 异常、目录不可读），必须放在 try 里，否则会变成没有原因的 500。
        mark('snapshot')
        before = guard.snapshot(root)
        mark('writing')
        reply = runner.emit_recording(
            root, model,
            _prompt(_payload(framework, steps, target_path, title, detected, keywords,
                             plan, plan_feedback)),
            writable=target_path,
        )
        mark('check')
        if not target_abs.is_file():
            raise EmitError('agent 没有写出目标文件：%s' % target_path,
                            details=_missing_target_details(before, target_path, reply))

        mark('validate')
        validated, output = _check_with_repair(
            runner, root, model, framework, target_path, detected, writable=target_path,
            progress=progress,
        )
        if not validated:
            raise EmitError('校验未通过' if existed else '校验未通过，文件已保留',
                            details=[output] + _target_hint(target_path, existed))
    except EmitError as exc:
        raise _abort(target_abs, exc, target_path, existed)
    except toolchain.ToolchainError as exc:
        raise _abort(target_abs, EmitError(str(exc)), target_path, existed)
    except (RuntimeError, guard.GuardError) as exc:
        raise _abort(target_abs, EmitError(str(exc)), target_path, existed)

    # 校验之后再取一次：产物目录已在 guard 里排除，剩下的就是真正被动过的文件。
    after = guard.changed(before)
    return {
        'path': target_path,
        'changed': after,
        # 目标文件以外的改动：只报告，不回滚。正常情况下它应该为空——不为空说明写权限没拦住。
        'outside': guard.violations(after, target_path),
        'validated': True,
        'framework': framework,
        'detected': detected.get('framework'),
        'reply': reply,
        # 任务书：前端/记录页用它展示「这次打算复用哪些现成封装」。
        'plan': plan,
        # 哪些步骤是降级生成的（定位不唯一，按第 n 个匹配写入），前端据此提醒用户回放确认。
        'degraded': degraded,
    }


def _missing_target_details(before, target_path, reply):
    """目标文件没写出来时，先看看它是不是写到了别的目录。

    上一版就是栽在这里：规划把 tests/ai-chat/xxx.spec.ts 当成目标，写手照做了，
    验收却按根目录的 xxx.spec.ts 找，最后报「没写出文件」——文件其实在，只是没人告诉用户在哪。
    """
    details = []
    if before is not None:
        try:
            stray = [p for p in guard.changed(before) if p != target_path]
        except Exception:  # 快照读取失败不该盖住真正的原因
            stray = []
        if stray:
            details.append('本次实际写出了这些文件：' + '、'.join(stray[:5]))
            details.append('目标路径是 %s；如果不是你想要的位置，请改目标文件后重新生成' % target_path)
    if reply and not details:
        details.append(reply[:500])
    return details


def _target_hint(target_path, existed):
    """原本存在的目标文件被覆盖后不回滚，要把恢复办法写清楚。"""
    if not existed:
        return []
    return ['原内容可用 git 恢复：git checkout -- %s' % target_path]


def _abort(target_abs, error, target_path, existed):
    """失败收尾：**不删**本次写出的文件。

    以前是「原本不存在就删掉」，结果一次无关的校验错误（比如项目基线里的类型错误）
    会把已经写对的内容一起抹掉，用户还得从 opencode 的库里捞回来。校验失败只说明
    「还不能直接用」，不说明「内容一无是处」——保留下来让人看一眼更划算。
    """
    if existed:
        error.details = list(error.details) + _target_hint(target_path, existed)
        return error
    try:
        kept = target_abs.is_file()
    except OSError:
        kept = False
    if kept:
        error.details = list(error.details) + [
            '本次写出的文件已保留：%s（校验未通过，可手动修改或删除）' % target_path
        ]
    return error


def _check_with_repair(runner, root, model, framework, target_path, detected, writable=None,
                       progress=None):
    """校验；失败则把输出交回 agent 修一次；仍失败返回 (False, 输出)。"""
    def mark(stage, detail=''):
        if progress is not None:
            progress(stage, detail)

    python = detected.get('python')
    ok, output = _validate(framework, python, root, target_path)
    rounds = 0
    while not ok and rounds < MAX_REPAIR_ROUNDS:
        rounds += 1
        mark('repair', '第 %d 轮修正校验错误' % rounds)
        fix = (
            '上一次写入的 %s 校验失败。请只修改该文件，修正以下错误，不要改动其他文件。\n'
            '校验输出：\n```\n%s\n```\n' % (target_path, output)
        )
        runner.emit_recording(root, model, fix, writable=writable)
        mark('validate', '第 %d 轮修正后重新校验' % rounds)
        ok, output = _validate(framework, python, root, target_path)
    return ok, output
