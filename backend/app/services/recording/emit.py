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

from app.services.recording import detect, guard, locator_vars, toolchain
from app.services.recording.locators import (best_effort, false_positive, inline_nth,
                                            pick_for, pool_for)
from app.services.recording.captcha_ts import CAPTCHA_TS
from app.services.recording.files_bundle import FILES_PY, FILES_TS
from app.services.recording.slider_bundle import SLIDER_PY
from app.services.recording.slider_ts import SLIDER_TS

logger = logging.getLogger(__name__)

RF_FRAMEWORK = 'robot-selenium'
PW_FRAMEWORK = 'playwright-ts'
MAX_REPAIR_ROUNDS = 1
# 运行时失败和类型错误不是一回事：类型错误看一眼就能改，跑不过往往要试两下。
# 但也只给两轮 —— 每轮是一次完整模型调用加一次真实执行，再多就不如让人接手。
MAX_RUN_REPAIR_ROUNDS = 2
MAX_CANDIDATES_IN_PROMPT = 5
# 任务书会原样进写手提示词、也会经前端回传，体积必须有上限，
# 否则模型偶尔吐一份巨型 JSON 就能把一次生成撑爆。
MAX_PLAN_BYTES = 64 * 1024
MAX_PLAN_FEEDBACK = 20
# 规划的项目事实最多扫几个测试文件：要给「项目原有用例」和「我们自己生成的旧版本」
# 各留位置，只扫 3 个时后者会把前者挤掉。
FACT_TEST_SCAN = 8
LOCATOR_RULES = (
    '要求：\n'
    '- 定位优先用 target.locator；不要自己编造选择器，需要更精确时只能从 target.candidates 里挑或组合。\n'
    '- target.locator 里出现 [class*="..."] / [class~="..."]（或 xpath 里的 contains(@class, ...)）'
    '是录制有意选的：那是 CSS Modules / BEM 类名去掉构建哈希后的名字部分，同一份代码重新打包也不变，'
    '而且不依赖元素在第几个位置。照原样写，不要把它改写成标签路径，也不要再加 .nth()。\n'
    '- intent.type 为 url_contains / page_text_contains / sleep 时没有 target：'
    'url_contains 断言整条地址，page_text_contains 断言整个页面的文字，不要为它们编造元素定位。\n'
    '- step.type 为 reload 表示「刷新当前标签页」：这一步没有 target 也没有 url。\n'
    '- step.type 为 close 表示「关闭被测浏览器」：这一步没有 target、没有值，'
    '它是一条收尾声明，不是对页面元素的操作。\n'
    '- step.type 为 press 表示「在目标元素上按一个键」，step.value 是键名（如 Enter、Control+Enter）。\n'
    '- step.type 为 dialog 表示「上一步操作会弹出原生对话框」，它没有 target，也没有定位。'
    'step.value 是 accept 或 dismiss，step.dialog.message 是弹窗文字（只用于写注释，不要拿去断言）。'
    '弹窗这一步本身不产生新动作，只是把「上一步会引起弹窗、以及怎么处理」记下来。\n'
    '- step.type 为 upload 表示「把 step.file.path 指向的项目内文件塞进上传控件」，'
    'step.file.name / size 只用于注释。target 指向的是「选择文件」按钮，不是 input 本身：'
    '不要为它编造 input 的选择器，工具函数会自己往下往上找。也不要写「先点按钮再选文件」两行。\n'
    '- step.type 为 download 表示「点 target 触发下载，再校验落盘的文件」，'
    'step.expect 是校验口径（name / min_bytes / parse / min_rows / timeout）。'
    '这一步除了那次点击不产生别的交互，不要把它改写成对页面元素的断言。\n'
    '- target.match_count > 1 表示这条定位在页面上匹配了多个元素，属于「不唯一但仍可用」：\n'
    '  · target.nth_applied 为 true：序号已经写进 target.locator 里了（形如 xpath=(...)[3]），\n'
    '    原样使用。不要再写 .nth()，也不要再包一层 (...)[n]——那会变成「第 3 个里的第 3 个」，\n'
    '    一个都匹配不到。target.nth 只用来写注释，不要拼进定位。\n'
    '  · 没有 nth_applied、给了 target.nth（下标从 0 开始）：playwright-ts 写 locator.nth(n)。\n'
    '  · 没有 nth_applied、locator 又是 css= 前缀：css 选择器没法带序号，按 locator 原样写，\n'
    '    并在该行上方加一行 TODO，说明「本应取第 n+1 个，需要改成 xpath，或让页面提供稳定属性」。\n'
    '  · 既没有 nth_applied 也没有 nth，就取第一个，并在该行上方加一行注释：匹配 N 个元素，取第 1 个，请确认。\n'
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
        '    这个关键字里只放 Reload Page 和判断，**不要重放前面的点击 / 输入**：'
        '每轮都会多花几秒，还会反复提交表单被风控拦掉。刷新后必须重新操作才能复现时，'
        '按「刷新 → 重新操作 → 立即断言」拆成三步录，而不是塞进轮询关键字。\n'
        '    不要用 Sleep 拼循环，也不要用 FOR 手写重试。\n'
        '- 等页面加载（intent.type 为 page_ready）：Selenium 没有等价 API，用 Wait For Condition 判 readyState。\n'
        '  intent.state 为 load → Wait For Condition    return document.readyState === "complete"    ${intent.timeout}s\n'
        '  intent.state 为 domcontentloaded → Wait For Condition    return document.readyState !== "loading"    ${intent.timeout}s\n'
        '  intent.state 为 networkidle → Selenium 侧没有对应物，按 load 那一档写，并加一行注释说明「后台请求可能未停」。\n'
        '- 如果某一步带 nav_to（录制时这一下点击确实跳转了），点击之后先等地址落地再继续，'
        '否则站点慢的时候下一步会直接找不到元素：\n'
        '  Wait Until Location Contains    <nav_to 的路径片段>    15s\n'
        '  只取路径，不要把带查询串的整条 URL 写死（参数一变就失配），也不要用 Sleep。\n'
        '- target.locator 已带 xpath= 前缀，原样使用，不要再包 css= 或再拼前缀。\n'
        '- 导入文件（type 为 upload）：用已生成的文件库，不要自己点「选择文件」按钮。\n'
        '  · *** Settings *** 里加一行：Library    ${CURDIR}/libraries/FilesLibrary.py\n'
        '  · 这一步写：Upload File    <这一行的 target.locator>    <step.file.path>\n'
        '  · step.file.path 是项目内相对路径（assets/终端导入模板.xlsx），原样写，不要转成绝对路径。\n'
        '  · 这一行之前不要加 Click Element：点那个按钮会拉起操作系统的文件对话框，'
        'Selenium 会一直挂在那里直到超时。也不要用 Press Keys 往按钮上打路径。\n'
        '- 下载校验（type 为 download）：\n'
        '  · 写：${file}=    Download File    <这一行的 target.locator>    expected=<step.expect.name>'
        '    min_bytes=<step.expect.min_bytes>    parse=<step.expect.parse>'
        '    min_rows=<step.expect.min_rows>    timeout=<step.expect.timeout>\n'
        '  · expect 里为空或为 0 的参数直接省略，不要出现 expected= 这种空值。'
        'parse 为 none 时不要传 parse 和 min_rows。\n'
        '  · driver 的 options 必须已经配好下载目录：download.default_directory 指向 downloads 目录、'
        'download.prompt_for_download 为 False。项目里有现成的公共 resource 就只补这两个选项，'
        '不要重写整个 driver 初始化。\n'
        '  · 中文文件名到磁盘可能被 URL 编码，断言优先用通配的 expected（*.xlsx），不要把全名写死。\n'
        '- 关闭浏览器（type 为 close）：写 Close Browser，位置就放在它那一步。'
        '这一步没有 target、没有值，不要加等待，也不要改成 Quit All Browsers。\n'
    ),
    'playwright-ts': (
        '框架写法（Playwright，自带 auto-wait）：\n'
        '- 直接 click / fill / selectOption，不要用 waitForTimeout 充当等待。\n'
        '- 断言用 expect(locator)：toBeVisible / toBeHidden / toHaveText / toHaveValue；'
        '地址断言用 expect(page).toHaveURL(...)；整页文字断言用 expect(page.locator("body")).toContainText(...)。\n'
        '- 新标签页用 context.waitForEvent("page")；frame 内用 page.frameLocator(目标 frame 的 url)。\n'
        '- 刷新页面（type 为 reload）用 page.reload()，不要写成 page.goto(page.url())：'
        '后者在 SPA 里可能根本不重载。\n'
        '- 等页面加载（intent.type 为 page_ready）：await page.waitForLoadState("${intent.state}", '
        '{ timeout: <intent.timeout 换算成毫秒> })。state 原样用 intent.state'
        '（load / domcontentloaded / networkidle），不要换成 waitForTimeout，也不要自己加 waitForURL；'
        '它没有目标元素，不要套用上面 expect(locator) 那套映射。\n'
        '- 如果某一步带 nav_to（录制时这一下点击确实跳转了），点击之后必须等跳转落地再进下一步，'
        '否则站点慢的时候下一步会直接找不到元素：\n'
        '  await page.waitForURL("**" + <nav_to 的路径部分>, { timeout: 15000 });\n'
        '  只取路径，不要把带查询串的整条 URL 写死（参数一变就失配），也不要用 waitForTimeout。\n'
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
        '    **poll 回调里只能有 reload 和那一句即时判断，不许把前面的点击 / 输入重放一遍。**'
        '实测重放一轮 2.9 秒、只 reload 0.3 秒，30 秒超时里能跑的轮数从六十多轮掉到三轮，'
        '而且每轮都会再提交一次表单，很快被对方的风控拦掉。\n'
        '    刷新会清空表单、导致「必须重新操作才能复现」时，这仍然是录制时该解决的事：'
        '正确的步骤是「刷新 → 重新操作 → 立即断言」三步，不是把操作塞进 poll 里。'
        '遇到这种断言，按规则写 reload + 判断，并在 poll 上方加一行注释：\n'
        '    // TODO: 该条件刷新后需重新操作才会出现，建议拆成「刷新 / 重新操作 / 立即断言」三步\n'
        '- 导入文件（type 为 upload）：调已生成的文件工具，不要自己点「选择文件」按钮。\n'
        '  · import { uploadFile } from "<files.ts 相对路径>";\n'
        '  · await uploadFile(<这一行的 target.locator>, <step.file.path>);\n'
        '  · 第一个参数传目标元素本身（通常是按钮），函数会自己往下、往上找 input[type=file]。'
        '直接 click 那个按钮会拉起操作系统的文件对话框，之后所有步骤都会阻塞。\n'
        '  · step.file.path 是项目内相对路径，原样写，不要拼绝对路径。\n'
        '- 下载校验（type 为 download）：\n'
        '  · const file = await downloadFile(page, async () => { await <这一行的 target.locator>.click(); });\n'
        '  · verifyFile(file, { name: "<step.expect.name>", minBytes: <step.expect.min_bytes>, '
        'parse: "<step.expect.parse>", minRows: <step.expect.min_rows> });\n'
        '  · 也可以把 { expect: {...} } 直接传给 downloadFile 的第三个参数，省掉 verifyFile。\n'
        '  · expect 里为空或为 0 的键不要写。parse 为 none 时连 verifyFile 都省掉：'
        '文件存在、体积过线这两条已由 downloadFile 保证。\n'
        '  · 下载目录默认是项目根的 downloads/，不要改成绝对路径，也不要写死盘符。\n'
        '- 关闭浏览器（type 为 close）：写 await page.close()，位置就放在它那一步。'
        '不要写 browser.close()（关掉整个浏览器会让后续页面全部失效），也不要加 waitForTimeout。\n'
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


# 目标路径光「相对、无 ..」不够：这条路径既是写权限白名单，也是生成后的验收对象。
# 所以再收一层——必须是本框架的测试文件、必须在工作区里、不能是依赖或配置。
TARGET_SUFFIXES = {
    PW_FRAMEWORK: ('.spec.ts', '.test.ts', '.spec.js', '.test.js'),
    RF_FRAMEWORK: ('.robot',),
}
TARGET_DENY_DIRS = ('node_modules', '.git', '.autocase', '.venv', 'venv', 'dist', 'build')
TARGET_DENY_NAMES = (
    'playwright.config.ts', 'playwright.config.js', 'tsconfig.json', 'package.json',
    'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml',
)


def target_problem(workspace, target_path, framework):
    """校验目标文件路径，返回中文原因；没问题返回 None。

    模型现在可以自己提名路径（见 plan），这条校验就是那道闸：用户确认的值和
    模型提名的值都要过这里，不能因为「模型读过项目」就信它给的字符串。
    """
    if not target_path:
        return '没有写入路径'
    if not target_ok(target_path):
        return '目标路径无效：必须是工作区内的相对路径，不能含 .. 或 * ? [ ]'
    rel = target_path.replace('\\', '/')
    parts = Path(rel).parts
    if any(part in TARGET_DENY_DIRS for part in parts[:-1]):
        return '目标路径不能放在 %s 这类目录里' % '/'.join(
            sorted({p for p in parts[:-1] if p in TARGET_DENY_DIRS}))
    if parts[-1].lower() in TARGET_DENY_NAMES:
        return '目标路径不能是项目配置文件：%s' % parts[-1]
    suffixes = TARGET_SUFFIXES.get(framework) or ()
    if suffixes and not parts[-1].lower().endswith(suffixes):
        return '目标文件扩展名不对：%s 需要 %s' % (
            framework, ' / '.join(suffixes))
    try:
        root = Path(workspace).resolve()
        resolved = (root / rel).resolve()
        # resolve() 会把符号链接和 ../ 都展开，这里再比一次，防止绕过前面的字符串检查。
        if resolved != root and root not in resolved.parents:
            return '目标路径跑出工作区了：%s' % target_path
    except OSError:
        return '目标路径无法解析：%s' % target_path
    return None


def _steps_for_prompt(steps, framework, actions=None, locator_vars=None):
    """把 IR 整理成 agent 能直接用的形式：每一步只带需要的定位与意图，不带内部字段。

    call 步骤**不展开**：写手要看到「这是一次动作调用」才会写成函数调用；
    展开成平铺的 4 步，它就会真的写 4 行，封装等于白做。动作内容在 actions 里给。

    locator_vars 给了「步骤号 → ${变量名}」时，只发变量名、不发裸定位。
    两样同时给，写手一定会挑省事的那条（实测它对长 xpath 有很强的改写冲动），
    资源文件就白建了。
    """
    vars_by_no = locator_vars or {}
    known = actions or {}
    out = []
    for step in steps:
        if step.get('type') == 'call':
            action = known.get(step.get('action')) or {}
            item = {
                'no': step['no'],
                'type': 'action',
                'action_id': step.get('action'),
                'name': action.get('name') or step.get('label') or '动作',
                'args': dict(step.get('args') or {}),
            }
            if step.get('text'):
                item['text'] = step['text']
            out.append(item)
            continue
        # text 是元素上的原文，用来写注释；没有就不占位（null 对写手没有信息量）。
        item = {'no': step['no'], 'type': step['type']}
        if step.get('text'):
            item['text'] = step['text']
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
        # 点击引起的跳转不单独记 goto，但要告诉写手「这一下真的跳走了」，
        # 否则写出来是「点完直接找下一个元素」，站点一慢就扑空。
        if step.get('nav_to'):
            item['nav_to'] = step['nav_to']
        if step.get('intent'):
            item['intent'] = step['intent']
            # 轮询刷新是写手最容易自作主张的一步：它会把「刷新后要重新登录」推断成
            # 「把登录操作也写进轮询回调」，实测每轮从 0.3 秒涨到 2.9 秒，还会反复提交
            # 表单被风控拦掉。长规则里说一遍压不住，所以贴着这一步的数据再说一次。
            if (step['intent'] or {}).get('reload'):
                item['poll_rule'] = ('轮询回调里只写 reload + 一句即时判断，'
                                     '不要重放前面的点击 / 输入')
        if step.get('dialog'):
            item['dialog'] = step['dialog']
        if step.get('slider') is not None:
            # 滑块不是「点一个元素」，写手拿到 locator 也没用。把三个参数原样给它，
            # 规则里会说明必须调 solveSlider，不要自己拼拖动代码。
            item['slider'] = step['slider']
        if step.get('captcha') is not None:
            # 同理：验证码是一段流程（截图 → 问模型 → 填入），把配置原样交出去，
            # 规则里要求必须调 solveCaptcha。
            item['captcha'] = step['captcha']
        if step.get('file') is not None:
            # 文件上传：资产引用 + 体积。不把 selector 交给写手——那是录制那一刻的
            # 结构路径，只适合回放兜底；生成的代码按 target.locator 现场找更耐改版。
            item['file'] = {k: v for k, v in (step['file'] or {}).items() if k != 'selector'}
        if step.get('expect') is not None:
            item['expect'] = step['expect']
        if step.get('seconds') is not None:
            item['seconds'] = step['seconds']
        target = step.get('target')
        if target:
            snap = target.get('snap') or {}
            var = vars_by_no.get(str(step.get('no')))
            if var:
                # 定位已经写进资源文件，这里只给变量名——连候选都不必给，
                # 写手没有任何理由再去拼一条自己的定位。
                item['target'] = {'locator_var': var}
                if target.get('alias'):
                    item['target']['alias'] = target['alias']
                # 但「实际用第几个」这条信息不能丢：css 选择器带不了序号，nth 没能
                # 内联进定位，变量名里也看不出「这是第 2 个匹配」。不告诉写手，
                # 它会以为变量指向唯一元素，页面上一改就点错。
                info = _locator_info(target, framework)
                if info and info.get('nth') is not None and not info.get('nth_applied'):
                    item['target']['nth'] = info['nth']
                    item['target']['match_count'] = info['matches']
            else:
                info = _locator_info(target, framework)
                matches = info['matches'] if info else None
                nth = info['nth'] if info else None
                item['target'] = {'locator': info['locator'] if info else None}
                if target.get('alias'):
                    item['target']['alias'] = target['alias']
                # 候选清单是「录制时怎么挑定位」的中间产物：模型只需要一条能跑通的定位。
                # 唯一命中（match_count == 1 且没有 nth）时整段不发——实测这能省掉提示词
                # 三成体积，而写手从来没在里面挑过东西。真不唯一时才给，那时的候选才有用。
                if matches != 1 or nth is not None:
                    item['target']['match_count'] = matches
                    item['target']['nth'] = nth
                    item['target']['candidates'] = _candidates_for_prompt(target, framework)
                # 序号已经算进 locator 里了（RF 的 xpath=(...)[n]）：必须明说，
                # 否则写手会照着 nth 再拼一次，变成「第 n 个里的第 n 个」，直接定位不到。
                if info and info.get('nth_applied'):
                    item['target']['nth_applied'] = True
                # 用户在界面上看过框、亲手挑的那个，要让它出现在提示词里，
                # 否则模型只看到「取第 2 个」，却不知道这 2 个分别是什么，也就写不出有意义的注释。
                matched = _match_texts(target)
                if matched:
                    item['target']['matches'] = matched
            # 元素在 iframe 里时，模型必须知道要先切框架，否则会写成顶层 page.getByRole。
            # 这条和变量无关：变量只解决「定位写在哪」，切框架仍要写在正文里。
            if snap.get('frame_url') or snap.get('frame_name'):
                item['target']['frame'] = {'url': snap.get('frame_url'), 'name': snap.get('frame_name')}
        # 标签页序号：0 是主标签页，非 0 说明这一步是在别的标签页里发生的。
        if step.get('page_index'):
            item['page_index'] = step['page_index']
        # 这一步的「执行前等待」：面板上给这一步开了开关并填了秒数。
        # 等的是**这一步自己的目标元素**——先等它出现，再动它。
        # 不是等下一步的元素：那是「点完等结果」，和这个开关绑的不是同一件事。
        settle = step.get('settle') or None
        if settle:
            item['settle'] = {'timeout': settle.get('timeout') or 5}
        out.append(item)
    return out


def _actions_for_prompt(actions, framework, steps, action_vars=None):
    """动作定义：子步骤按写手需要的形状（定位串 + `{{参数}}` 占位符）。

    called_at 是抽函数的关键信号。**只调用一次也要抽**——用户封装就是要那个名字，
    不是为了去重；但写手必须知道同一动作在流程里出现几次，出现多次时绝不能
    展开成多段重复代码。
    """
    out = []
    for action in (actions or {}).values():
        calls = [s.get('no') for s in steps
                 if s.get('type') == 'call' and s.get('action') == action.get('id')]
        out.append({
            'id': action.get('id'),
            'name': action.get('name'),
            'desc': action.get('desc') or '',
            'params': [p.get('key') for p in action.get('params') or []],
            'called_at': calls,
            'steps': _steps_for_prompt(action.get('steps') or [], framework,
                                       locator_vars=(action_vars or {}).get(action.get('id'))),
        })
    return out


def _teardown_for_prompt(teardown, framework, action_vars=None):
    """后置处理给写手的形状：名字 + 参数 + 子步骤。

    它就是「一次不在流程里的调用」，所以子步骤和动作定义同一套形状；区别只在
    写手要把名字写成 Test Teardown / afterEach，而不是写进用例正文。
    """
    action = (teardown or {}).get('action') or {}
    if not action.get('name'):
        return None
    return {
        'id': action.get('id'),
        'name': action['name'],
        'desc': action.get('desc') or '',
        'params': [p.get('key') for p in action.get('params') or []],
        'args': {k: str(v) for k, v in ((teardown or {}).get('args') or {}).items()},
        'steps': _steps_for_prompt(action.get('steps') or [], framework,
                                   locator_vars=(action_vars or {}).get(action.get('id'))),
    }


def _fmt(candidate, framework):
    """把一个候选变成目标框架里可以直接写的定位字符串；没有真定位时返回 None。

    RF 的 desc 是展示串（`rf xpath://...`），当定位写出去是错的，所以只认 spec.args；
    存档续录的老草稿可能没带 spec，这里必须容错——缺定位要由上层汇总成
    「第 N 步请重新拾取」，而不是冒一个 KeyError 到全局 500。
    """
    if framework == RF_FRAMEWORK:
        args = (candidate.get('spec') or {}).get('args') or []
        return args[0] if args else None
    return candidate.get('desc') or None


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


def _fit_nth(locator, index, framework):
    """把「取第几个」落到定位里：RF 直接内联，Playwright 留给写手写 .nth(n)。

    Playwright 侧是方法调用，写成 locator.nth(1) 不容易错，而且 desc 还要拿去做
    界面预览，不该混进执行细节；RF 侧是字符串拼接，必须在这里一次算准。
    """
    if framework != RF_FRAMEWORK:
        return locator, False
    return inline_nth(locator, index)


def _pick_locator(target, framework):
    """挑出「生成时到底用哪条候选、第几个」。

    返回 (candidate, index, mode)，一条能命中的都没有时 (None, None, None)。
    单独抽出来的理由很实际：界面上的「定位详情」要回答的是同一个问题，
    两边各写一份判断迟早会「弹窗说 A、生成写 B」。
    """
    counted = target.get('candidates') or []
    # 用户看过框、亲手挑过的，优先于任何自动选择：他选的是这条候选的第 index 个，
    # 换一条候选就不是同一个元素（不同选择器的匹配顺序不一样）。
    picked = target.get('chosen')
    if picked:
        allowed = {c['kind'] for c in pool_for(framework, counted)}
        hit = next((c for c in counted if c['desc'] == picked.get('desc')), None)
        if hit is not None and hit['kind'] in allowed and (hit.get('count') or 0) > 1:
            return hit, picked.get('index'), 'chosen'
    chosen = pick_for(framework, counted)
    if chosen is not None:
        return chosen, None, 'unique'
    best = best_effort(framework, counted)
    if best is None:
        # 录制那一刻页面就跳走了，元素一条都数不到。这类步骤的定位本来就是按快照推的，
        # 放行但标 stale 让用户在回放里确认；真正失效的步骤没有 unverified 标记，仍然拦下。
        if target.get('unverified'):
            pool = pool_for(framework, counted)
            if pool:
                return pool[0], None, 'stale'
        return None, None, None
    return best, best.get('index'), 'ambiguous'


def _locator_info(target, framework):
    """选定位：有唯一的就用唯一的；没有就降级到「命中最少」的那条 + 录制时的序号。

    返回 {'locator', 'matches', 'nth', 'mode'}，RF 下序号可能已经内联进 locator
    （此时带 nth_applied）。连一条能命中的候选都没有时返回 None。
    kind / desc / spec 是给界面「定位详情」用的：它得知道这条长什么样、能不能拿去查页面。
    候选在、但没带定位 spec（老存档续录）时返回 missing_spec=True，
    让调用方按「请重新拾取」报错，而不是把它当成「元素已经不在页面上」。
    """
    candidate, index, mode = _pick_locator(target, framework)
    if candidate is None:
        return None
    locator = _fmt(candidate, framework)
    if locator is None:
        return {
            'locator': None,
            'matches': candidate.get('count') or 0,
            'nth': index,
            'mode': mode,
            'kind': candidate.get('kind'),
            'desc': candidate.get('desc'),
            'spec': candidate.get('spec'),
            'missing_spec': True,
        }
    info = {
        'locator': locator,
        'matches': candidate.get('count') or 0,
        'nth': index,
        'mode': mode,
        'kind': candidate.get('kind'),
        'desc': candidate.get('desc'),
        'spec': candidate.get('spec'),
    }
    if mode in ('chosen', 'ambiguous'):
        info['locator'], applied = _fit_nth(info['locator'], index, framework)
        if applied:
            info['nth_applied'] = True
    return info


def _candidates_for_prompt(target, framework):
    """给 agent 的候选清单：只留有命中、确实落在目标上的，最多 5 条，避免提示词被撑爆。

    「命中」以前只要求 count > 0，于是 getByText 数到提示语里那两个字也会进清单。
    现在录制时带了 relation，指向别的元素的候选直接剔掉——把它交给写手，等于递过去一个必错的选项。
    """
    out = []
    for candidate in pool_for(framework, target.get('candidates') or []):
        if candidate.get('count', -1) <= 0:
            continue
        if false_positive(candidate):
            continue
        index = candidate.get('index')
        locator = _fmt(candidate, framework)
        if locator is None:
            # 没带定位 spec 的老存档：先把这条候选剔掉，_prepare 会整体拦下并说清第几步。
            continue
        applied = False
        if candidate['count'] > 1:
            # RF 下多匹配的候选也把序号内联进去：写手从清单里挑备选用时，
            # 拿到的同样是「直接能写」的定位，不用再自己拼 (...)[n]。
            locator, applied = _fit_nth(locator, index, framework)
        item = {'locator': locator, 'count': candidate['count'], 'nth': index}
        if applied:
            item['nth_applied'] = True
        relation = candidate.get('relation')
        if relation and relation != 'self':
            # 命中的是目标的祖先/后代：能点，但不如精确命中的那条可靠，让写手知道这个差别。
            item['hit'] = relation
        out.append(item)
        if len(out) >= MAX_CANDIDATES_IN_PROMPT:
            break
    return out


PLAN_RULES = (
    '规划要求：\n'
    '- 你只做三件事：定写入路径、定项目风格、给每一步一句施工说明。'
    '「项目里有没有现成封装」不是你的职责——写手动笔时会读项目，别为了找封装翻目录。\n'
    '- 任务书里的每个 file / symbol / import 必须来自你真正读过的文件，不许编造。'
    'reusable 只填「读过的参考用例里本来就在调用、并且覆盖了该步全部意图」的封装；'
    '其余情况留空数组，不要专门去找。\n'
    '- steps 要覆盖每一个录制步骤，顺序一致。标 reuse 时把要传的参数写进 args。'
    '每个步骤都要写 note，说清这一步做什么；只有录制里的 assert / wait 步骤才写断言或等待要求，'
    'click / fill 这类步骤不要自己加断言。upload 的 note 写「把哪个文件导进哪个控件」，'
    'download 的 note 写「等到什么文件、至少几行」——这两类的校验口径已经在 step.file / step.expect 里，'
    '不要再另编一套断言。不要写定位表达式——'
    '录制时已经把定位算准了，写手会直接用 dispatch 里的 locator，'
    '你猜的跟它不一致反而要让写手在两个版本之间打架。\n'
    '- intent.reload 为 true 的等待步骤，note 只描述「反复刷新，直到条件成立」。'
    '不要写「刷新后重新登录 / 重新填表再提交」——写手会照着把整段操作重放进轮询回调，'
    '每轮多花两三秒，还会反复提交表单被风控拦掉。真需要重新操作才能复现的，'
    '在 note 里写明「刷新后需重新操作」，让写手按 TODO 标出来，由用户决定要不要拆步骤。\n'
    '- 同一流程可能已经有旧版本：project_facts.previous_outputs 就是 AutoCase 以前为它生成的文件，'
    '它们不是项目惯例。遇到时不要覆盖旧版本，另起一个新文件名（在原名后加动作后缀），'
    '并在 risks 里写一句「已存在旧版本 xxx，本次写新文件」。'
    '只有 payload.target_path 明确指向旧版本时才覆盖。\n'
    '- 读项目不设文件数、也不设工具调用轮数的硬上限，但要收敛：先看 project_facts / '
    'project_memory，再读一个同业务域的用例，之后只在需要确认封装签名、目录约定或配置时才有针对性地再读。'
    '不要重复读同一类文件，也不要 glob 整个仓库。能写出每一步的施工说明时立刻输出 JSON'
    '——再读下去不会更准。全程控制在 15 分钟以内。\n'
    '- 写入路径：payload.target_path 非空表示用户已经指定，必须原样返回；'
    '为空表示由你按项目约定选一个，直接写进 target_path，并在 target_path_reason 里给一句依据'
    '（例如「playwright.config.ts 的 testDir 是 ./tests，参考 tests/login-page/login-success.spec.ts」）。'
    '路径必须是工作区内的相对路径，不能含 ..，不能放进 node_modules，也不能指向配置文件。'
    '定一次就够，不要为路径反复权衡：用户会在任务书上确认最终位置。\n'
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
    if step.get('type') == 'call':
        item = {'no': step['no'], 'type': 'action',
                'name': step.get('label') or '动作', 'args': dict(step.get('args') or {})}
        if step.get('text'):
            item['text'] = step['text']
        return item
    item = {'no': step['no'], 'type': step['type']}
    for key in ('text', 'value', 'label', 'url', 'seconds'):
        if step.get(key) is not None:
            item[key] = step[key]
    if step.get('intent'):
        item['intent'] = step['intent']
    if step.get('file'):
        # 规划阶段只要知道「这一步要导入哪个文件」，不影响它判断项目里有没有现成封装。
        item['file'] = {k: v for k, v in step['file'].items() if k != 'selector'}
    if step.get('expect'):
        item['expect'] = step['expect']
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


def _project_facts(workspace, framework):
    """把「路径该怎么放」的客观依据直接给模型，省掉它自己 glob 三轮。

    上一版什么都不给，模型要读 playwright.config.ts、再翻同目录用例，才敢判断
    该写哪个位置；这一步的 reasoning 经常就是几十秒。这些事实后端本来就能算，
    提前给，模型的思考量直接砍一半。

    recent_tests 和 previous_outputs 必须分开：前者是项目自己的写法，
    后者是 AutoCase 以前为同一条流程生成的旧版本。混在一起时，规划会读出一份
    「已经有一模一样的用例了」，然后在覆盖还是新建之间反复权衡——240 秒超时就是这么来的。
    """
    root = Path(workspace)
    facts = {}
    try:
        if framework == PW_FRAMEWORK:
            config = root / 'playwright.config.ts'
            if config.is_file():
                text = config.read_text(encoding='utf-8', errors='replace')
                found = re.search(r"testDir\s*:\s*['\"]([^'\"]+)['\"]", text)
                if found:
                    facts['test_dir'] = found.group(1)
        entries = detect.recent_test_paths(root, robot=framework == RF_FRAMEWORK,
                                          limit=FACT_TEST_SCAN)
        recent = [e['path'] for e in entries if not e['generated']][:3]
        previous = [e['path'] for e in entries if e['generated']][:2]
        if recent:
            facts['recent_tests'] = recent
        if previous:
            facts['previous_outputs'] = previous
    except (OSError, ValueError):
        # 探测失败不影响规划：模型自己会读项目。
        pass
    return facts


def _plan_payload(framework, steps, target_path, title, feedback=None, previous_plan=None,
                  workspace=None, memory=None, context_reused=False, actions=None):
    payload = {
        'framework': framework,
        # 空字符串 = 用户没指定，由规划按项目约定选一个。
        'target_path': target_path or '',
        'title': title,
        'steps': [_plan_step(step) for step in steps],
        'output_language': 'Simplified Chinese',
    }
    if actions:
        # planner 只看意图，但 action 调用的 note 必须写出「这一段具体做了什么」，
        # 否则任务书里这一步就是一句「调用登录」，写手拿不到任何可执行信息。
        payload['actions'] = [{
            'id': action.get('id'),
            'name': action.get('name'),
            'desc': action.get('desc') or '',
            'params': [p.get('key') for p in action.get('params') or []],
            'called_at': [s.get('no') for s in steps
                          if s.get('type') == 'call' and s.get('action') == action.get('id')],
            'steps': [_plan_step(sub) for sub in action.get('steps') or []],
        } for action in actions.values()]
    if context_reused:
        # 同一会话的第二轮：项目已经读过了，别让模型以为还要再读一遍。
        payload['context_reused'] = True
    if workspace:
        facts = _project_facts(workspace, framework)
        if facts:
            payload['project_facts'] = facts
    # 项目画像长期存在库里，project_facts 是这次现算的——两者都在时以画像为准，
    # 它看过真实用例，而 facts 只有路径。
    if memory:
        payload['project_memory'] = memory
    notes = _clean_feedback(feedback)
    if notes:
        payload['feedback'] = notes
    if previous_plan:
        # steps_sig 是服务端用来判过期的，不该混进给模型看的上一版里。
        payload['previous_plan'] = {k: v for k, v in previous_plan.items() if k != 'steps_sig'}
    return payload


def _plan_prompt(payload, revise=False):
    if revise:
        # 同一会话里对任务书的修订：项目已经读过了，再读一遍纯属烧时间。
        head = (
            '这是同一个会话里对任务书的修订。上一轮你已经读过这个项目、给过一版任务书'
            '（previous_plan）。\n'
            '本轮只按 feedback 改任务书：不要重新扫项目，不要重新论证目录结构，'
            '只改被点到的部分，其余原样保留。\n'
            '你没有写权限，也不应该尝试写任何文件。只输出 JSON。\n'
        )
        return (
            head
            + PLAN_RULES
            + '```json\n' + json.dumps(payload, ensure_ascii=False, indent=2) + '\n```\n'
        )
    head = (
        '下面是一条录制好的测试流程。请产出任务书。\n'
        '你没有写权限，也不应该尝试写任何文件。\n'
    )
    if payload.get('project_memory'):
        # 有画像时不能把「先读一个用例」当默认动作：画像本身就是读完的结果，
        # 再读一遍等于把 60~120 秒重新烧掉。
        head += '项目画像已经在 project_memory 里，不用再读项目文件，直接出任务书。\n'
    else:
        head += '读项目不设硬上限，但要收敛：能写出每一步的施工说明时就停（要求见下）。\n'
    if payload.get('project_facts'):
        head += (
            '项目事实已经探好放在 project_facts 里（测试目录、项目原有用例 recent_tests、'
            '你自己以前生成的旧版本 previous_outputs），直接用它们定写入路径和风格，'
            '不要为了这两件事再翻一遍目录。\n'
        )
    if payload.get('project_memory'):
        head += (
            '这次有 project_memory：它是之前有人读遍这个项目后总结的画像（测试目录、目录与命名习惯、'
            '现成封装、断言与等待习惯），比 project_facts 的路径列表准得多。\n'
            '- 定写入路径、定风格、认封装**一律以 project_memory 为准**，不要再去读任何项目文件，'
            '也不要为了「确认一下」去 glob 或 ls——那只会把时间烧掉。\n'
            '- 这次的阅读预算更省：画像已经覆盖大部分事实，只有 project_memory 缺了某个关键信息'
            '（例如 target_path 为空而画像又没写 test_dir）时才读一次，读完立刻输出 JSON。\n'
            '- project_memory 带 stale: true 表示项目在画像之后被改过。此时仍然按它写，'
            '但把「画像可能过时」记进 risks。\n'
        )
        if (payload.get('project_memory') or {}).get('map'):
            head += (
                '- project_memory.map 是这个项目的「地图」：整个仓库的目录职责、用例该放哪、'
                '有哪些现成封装该怎么调。定 target_path 时按「测试」那一节里的目录划分来选，'
                '不要另起一个和现有结构不一致的目录；reusable 里要优先收进地图「现成封装」里'
                '能覆盖本次步骤的那些。\n'
            )
    if payload.get('feedback'):
        head += '用户对上一版任务书提了意见，这次必须按意见修订，不要装作没看见。\n'
    return (
        head
        + PLAN_RULES
        + '```json\n' + json.dumps(payload, ensure_ascii=False, indent=2) + '\n```\n'
    )


def _partial_note(partial):
    """超时诊断：跑了几轮、推理写了多少。

    用户看「超过 420 秒」只知道失败，看不出它是跑偏了还是只差收尾。
    带上这两个数，就能判断该改提示词还是该加预算。
    """
    if not partial:
        return ''
    rounds = partial.get('rounds') or 0
    reasoning = partial.get('reasoning_chars') or 0
    if not rounds and not reasoning:
        return ''
    return '（已跑 %d 轮，推理 %d 字符）' % (rounds, reasoning)


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


def _normalize_plan(plan, target_path, workspace=None, framework=None):
    """整理任务书里的写入路径：用户给了就锁死，没给就采用规划提名的那条。

    以前是「永远用请求里的路径」：用户随手填一个根目录的 spec，模型读了
    playwright.config.ts 发现 testDir 是 ./tests、那个位置根本不会被收集，
    想改又不敢改，于是在 reasoning 里反复权衡——上次 240 秒超时就是这么烧掉的。
    现在路径可以由规划提名，但只到「建议」为止：真正写哪个文件仍要看用户在
    任务书上确认的值，而且提名本身要过 target_problem 那道校验。
    """
    data = dict(plan)
    raw_path = str(data.get('target_path') or '').strip().replace('\\', '/')
    explicit = str(data.get('target_path_suggestion') or '').strip().replace('\\', '/')
    reason = str(data.get('target_path_reason') or '').strip()

    if target_path:
        # 用户明确指定了路径：以请求为准，模型改过的降级成建议。
        suggestion = explicit or raw_path
        data['target_path'] = target_path
        if suggestion and suggestion != target_path and target_ok(suggestion):
            data['target_path_suggestion'] = suggestion
            data['target_path_reason'] = reason or '规划认为这个位置更符合项目约定'
        else:
            data.pop('target_path_suggestion', None)
            data.pop('target_path_reason', None)
    else:
        # 没指定：采纳规划提名的路径（有 suggestion 优先），再校验一次。
        chosen = explicit or raw_path
        problem = target_problem(workspace, chosen, framework) if chosen else '规划没有给出写入路径'
        if problem:
            raise EmitError('规划给的写入路径不可用：%s。请重新规划，或在配置里自己指定目标文件。'
                            % problem)
        data['target_path'] = chosen
        # 已经采纳成正式路径，就不该再展示成「建议改到」。
        data.pop('target_path_suggestion', None)
        data['target_path_reason'] = reason or '规划按项目约定选的位置'

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


def _prepare(workspace, steps, framework, target_path, actions=None):
    """规划和写入共用的入口校验：路径、框架、步骤、以及「元素还在不在」。

    target_path 允许为空：规划阶段可以不给路径，让 agent 自己按项目约定提名，
    用户确认后再带着确定路径进入写入。为空时跳过路径校验，由调用方在拿到
    规划结果后补一次（见 target_problem）。

    动作的子步骤也要一起校验：call 本身没有 target，只看主步骤的话，一个封装了
    4 步的动作里元素全没了也照样放行，写手只能照着死定位写。
    """
    if target_path:
        problem = target_problem(workspace, target_path, framework)
        if problem:
            raise EmitError(problem)
    root = Path(workspace).resolve()
    if not root.is_dir():
        raise EmitError('工作区目录不存在：%s' % workspace)
    if framework not in (RF_FRAMEWORK, PW_FRAMEWORK):
        raise EmitError('暂不支持的框架：%s' % framework)
    if not steps:
        raise EmitError('没有可生成的步骤')
    # 定位不唯一不再拦整体生成：降级成「最窄候选 + 录制时的序号」。
    # 序号对 RF 已经内联进 xpath，对 Playwright 由写手写成 .nth(n)——两边都不再是
    # 「让模型自己拼字符串」，剩下的风险只在 css 候选上（见 LOCATOR_RULES）。
    # 只有连一条候选都命不中（元素已经不在页面上）才拒绝——那种情况写不出任何定位。
    blocked, degraded, unlocated = [], [], []
    checks = [(step, None) for step in steps]
    for action in (actions or {}).values():
        for sub in action.get('steps') or []:
            checks.append((sub, action.get('name')))
    for step, owner in checks:
        target = step.get('target')
        if not target:
            continue
        info = _locator_info(target, framework)
        if info is None:
            blocked.append((owner, step.get('no')))
        elif info.get('missing_spec'):
            # 候选还在、只是没带定位表达式（老存档只存了前端视图）。这和「元素找不到」
            # 是两件事：重拾一次就能恢复，不该让用户去页面上重新找元素。
            unlocated.append((owner, step.get('no')))
        elif info['mode'] == 'ambiguous':
            entry = {
                'no': step.get('no'),
                'alias': target.get('alias'),
                'locator': info['locator'],
                'matches': info['matches'],
                'nth': info['nth'],
            }
            if owner:
                entry['action'] = owner
            degraded.append(entry)
    if unlocated:
        where = ['第 %s 步' % no if not owner else '动作《%s》第 %s 步' % (owner, no)
                 for owner, no in unlocated]
        raise EmitError(
            '%s 缺少定位信息（存档续录可能没带上），请重新拾取这几步的目标元素后再生成'
            % '、'.join(where),
            details=['unlocated:%s' % (no or 0) for _, no in unlocated],
        )
    if blocked:
        where = ['第 %s 步' % no if not owner else '动作《%s》第 %s 步' % (owner, no)
                 for owner, no in blocked]
        raise EmitError(
            '%s 的目标元素在页面上已经找不到（匹配数为 0），请重新拾取后再生成'
            % '、'.join(where),
            details=['missing:%s' % (no or 0) for _, no in blocked],
        )
    return root, degraded


def plan(runner, workspace, model, steps, framework, target_path, title, python=None,
         feedback=None, previous_plan=None, progress=None, memory=None, session=None,
         actions=None):
    """只跑规划：读项目、出任务书，不写任何文件。

    返回值里的 plan 已经归一化：用户配了 target_path 就锁死它、把模型想改的
    那条留在 target_path_suggestion；没配就用规划提名的路径，此时 `chosen`
    标记为 True，告诉界面「这个位置是 AI 定的，需要你确认」。

    session 给出时走同一个 opencode 会话（重规划也复用它，不再重读项目）；
    不给就退回一次性的 emit_planning，行为和以前完全一致。
    """
    def mark(stage, detail=''):
        if progress is not None:
            progress(stage, detail)

    root, _ = _prepare(workspace, steps, framework, target_path, actions)
    mark('planning')
    partial = {}
    reused = session is not None
    prompt = _plan_prompt(
        _plan_payload(framework, steps, target_path, title,
                      feedback=feedback, previous_plan=previous_plan,
                      workspace=workspace, memory=memory, context_reused=reused,
                      actions=actions),
        revise=bool(reused and previous_plan),
    )
    try:
        if session is not None:
            text = session.plan_turn(prompt, revise=bool(previous_plan))
        else:
            text = runner.emit_planning(root, model, prompt)
    except Exception as exc:
        # 超时不一定颗粒无收：模型经常已经在正文里把 JSON 写了大半。
        # 能拼出完整任务书就直接用，拼不出才报错，并且说清它跑到哪一步。
        partial = dict(getattr(exc, 'partial', None) or {})
        reason = str(exc) or exc.__class__.__name__
        partial['_reason'] = reason
        text = (partial.get('text') or '').strip()
        if not text:
            raise EmitError('规划失败：%s%s' % (reason, _partial_note(partial)))
        logger.warning('规划中止（%s），正文已有 %d 字符，尝试抢救', reason, len(text))

    data, error = _extract_plan(text)
    if data is None:
        if partial:
            raise EmitError(
                '规划失败：%s%s（已输出 %d 字符，没能拼成完整任务书）'
                % (partial.get('_reason') or '规划中止', _partial_note(partial), len(text)),
                details=[text[-600:]],
            )
        raise EmitError('任务书解析失败：%s' % error, details=[(text or '')[:500]])
    if partial:
        logger.warning('规划在中止前已写出完整任务书（%d 字符），直接采用', len(text))
    data = _normalize_plan(data, target_path, workspace=workspace, framework=framework)
    # 指纹由服务端算，不采信模型给的：写入时要拿它判断「录制有没有变过」。
    data['steps_sig'] = steps_signature(steps)
    if not data['steps']:
        if partial:
            raise EmitError(
                '规划失败：%s%s（任务书里没有任何步骤）'
                % (partial.get('_reason') or '规划中止', _partial_note(partial))
            )
        raise EmitError('任务书没有覆盖任何录制步骤，请重试或换一个模型')
    if not plan_matches_steps(data, steps):
        raise EmitError('任务书与录制步骤对不上（录制可能变过），请重新规划')
    size = len(json.dumps(data, ensure_ascii=False))
    if size > MAX_PLAN_BYTES:
        raise EmitError('任务书过大（%d 字符），请重新规划' % size)

    logger.info('录制规划：%s 步 / %s 个复用项 / %s 项额外改动 / %s 字符 / 写入 %s',
                len(data['steps']), len(data['reusable']), len(data.get('needs') or []),
                size, data['target_path'])
    logger.debug('录制规划：任务书全文 %s', json.dumps(data, ensure_ascii=False))
    return {
        'plan': data,
        'target_path': data['target_path'],
        # 用户没配路径时，这个位置是 AI 选的：界面要明确提示「需要你确认」。
        'chosen': not target_path,
        'suggestion': data.get('target_path_suggestion'),
        'reason': data.get('target_path_reason'),
        'steps': len(data['steps']),
        'chars': size,
    }


def _has_slider(steps=None, actions=None):
    """本次流程里有没有滑块。

    滑块**通常不在顶层步骤里**，而在动作（比如「登录」）的子步骤里：用户录一个
    「登录」动作，里面是填账号、填密码、点登录、拖滑块四步。只扫顶层会漏掉它，
    这正是之前提示词里那段滑块规则从来没注入过的原因。
    """
    if any(step.get('type') == 'slider' for step in (steps or [])):
        return True
    items = actions.values() if isinstance(actions, dict) else (actions or [])
    for action in items:
        for step in (action.get('steps') or []):
            if step.get('type') == 'slider':
                return True
    return False


# 平台工具的共享目录：放在工作区根，所有用例共用一份。
# 以前是 target 同级一份 libraries/：同一个项目里每录一条用例就复制一份 9KB + 20KB 的库，
# 内容完全一样；模板改了还得回头把历史目录逐个覆盖。
SHARED_DIR = '.autocase'


def _shared_lib_rel(target_path, name):
    """从用例目录回到工作区根，取 .autocase/libraries/<name> 的相对路径。

    返回值直接拼 `${CURDIR}/`（RF 里 CURDIR 是 suite 所在目录）。用相对路径而不是绝对路径，
    是为了让整个项目换机器、进 CI 都能直接用；代价是用例被挪到别的深度要重新生成。
    """
    parents = [part for part in Path(target_path).parent.parts if part not in ('', '.')]
    return '/'.join(['..'] * len(parents) + [SHARED_DIR, 'libraries', name])


def _write_shared_library(path, content):
    """内容一样就不动文件；返回 created / updated / reused。

    「先检索、有就直接用」不能只判断存在：模板修过（比如上传那个 TypeError）之后，
    旧文件还在，但里面是坏的。所以比对内容——一样才复用，不一样就更新。
    """
    try:
        existing = path.read_text(encoding='utf-8')
    except OSError:
        existing = None
    if existing == content:
        return 'reused'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')
    return 'created' if existing is None else 'updated'


def _note_platform_file(notes, rel, state):
    """把「平台自己写的文件」记下来，随生成结果返回给界面。

    这些文件在 guard.snapshot 之前落盘，不会被算成越界写入，所以以前界面上只说
    「未创建辅助文件」——用户只看到 .robot，目录里却多出 libraries/，对不上。
    """
    if notes is not None:
        notes.append({'path': rel.replace('\\', '/'), 'state': state})


def _ensure_slider_utils(root, target_path, steps, framework, actions=None, notes=None):
    """把滑块工具写进目标项目，返回相对路径；不需要就返回 None。

    Robot 落盘到工作区根的 .autocase/libraries/（所有用例共用一份），Playwright
    仍放 target 同级的 utils/（它要按相对路径 import，跨目录反而更绕）。
    两个文件里的算法是同一套，只差语言。

    必须在 guard.snapshot 之前落盘：写完才算「项目原有文件」，既不进 diff，
    也不会被越界检查当成 agent 乱写。写手被要求引用它，文件不存在时它会自己造一个，
    而 write_scope=exact 会把那次写入拒掉。
    """
    if not _has_slider(steps, actions):
        return None
    target = Path(target_path)
    if framework == RF_FRAMEWORK:
        rel = _shared_lib_rel(target_path, 'SliderLibrary.py')
        # rel 是相对用例目录算的（${CURDIR} 往上退），落盘时要按同一个基准还原成绝对路径。
        absolute = (Path(root) / target.parent / rel).resolve()
        _note_platform_file(notes, rel, _write_shared_library(absolute, SLIDER_PY))
        return rel
    utils_dir = Path(root) / target.parent / 'utils'
    utils_dir.mkdir(parents=True, exist_ok=True)
    (utils_dir / 'slider.ts').write_text(SLIDER_TS, encoding='utf-8')
    return (target.parent / 'utils' / 'slider.ts').as_posix()


def _ensure_captcha_utils(root, target_path, steps, framework):
    """把验证码工具写进 target_path 同级的 utils/，返回相对路径；不需要就返回 None。

    和滑块同一个位置、同一个理由：必须在 guard.snapshot 之前落盘，写完才算
    「项目原有文件」，既不进 diff，也不会被越界检查当成 agent 乱写。
    """
    if framework != PW_FRAMEWORK:
        return None
    if not any((step.get('type') == 'captcha') for step in (steps or [])):
        return None
    target = Path(target_path)
    utils_dir = Path(root) / target.parent / 'utils'
    utils_dir.mkdir(parents=True, exist_ok=True)
    (utils_dir / 'captcha.ts').write_text(CAPTCHA_TS, encoding='utf-8')
    return (target.parent / 'utils' / 'captcha.ts').as_posix()


def _ensure_files_utils(root, target_path, steps, framework, notes=None):
    """把文件工具写进目标项目，返回相对路径；没有相关步骤就返回 None。

    Robot 落盘到工作区根的 .autocase/libraries/（所有用例共用一份，写前比对内容），
    Playwright 仍放 target 同级的 utils/。和滑块一样必须在 guard.snapshot 之前写：
    写完才算「项目原有文件」，既不进 diff，也不会被越界检查当成 agent 乱写。
    """
    if not any(step.get('type') in ('upload', 'download') for step in (steps or [])):
        return None
    target = Path(target_path)
    if framework == RF_FRAMEWORK:
        rel = _shared_lib_rel(target_path, 'FilesLibrary.py')
        # rel 是相对用例目录算的（${CURDIR} 往上退），落盘时要按同一个基准还原成绝对路径。
        absolute = (Path(root) / target.parent / rel).resolve()
        _note_platform_file(notes, rel, _write_shared_library(absolute, FILES_PY))
        return rel
    folder = Path(root) / target.parent / 'utils'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'files.ts').write_text(FILES_TS, encoding='utf-8')
    return (target.parent / 'utils' / 'files.ts').as_posix()


def _rel_from_case(rel_path, target_path):
    """工作区相对路径 → 从用例目录往后退的形式（拼在 ${CURDIR}/ 后面用）。

    和 _shared_lib_rel 同一个思路：用相对路径，换机器、进 CI 都能直接用；
    代价是用例挪到别的深度要重新生成。
    """
    parents = [p for p in Path(target_path).parent.parts if p not in ('', '.')]
    return '/'.join(['..'] * len(parents) + [rel_path])


def _bare_locators(script_path, vars_path):
    """正文里原样出现了资源文件中的定位串，说明这一步没走变量。

    只查资源文件里的那几条，不对全文禁 xpath / css：断言、轮询条件里用到的元素
    是录制步骤之外的补充，本来就该由写手自己写定位。
    """
    try:
        script = script_path.read_text(encoding='utf-8')
        entries = locator_vars.parse(vars_path.read_text(encoding='utf-8'))
    except OSError:
        return []
    return [item['locator'] for item in entries.values()
            if item.get('locator') and item['locator'] in script]


def _build_locator(root, target_path, steps, actions, framework, teardown=None, notes=None):
    """把定位抽成资源文件里的变量，返回 _payload 要的结构。

    和 _ensure_*_utils 一样必须在 guard.snapshot 之前调用：新写的文件要先成为
    「项目原有文件」，否则会被越界检查当成写手乱写。

    抽变量失败不阻断生成——退回内联定位，脚本照样能跑，只是回到老样子。
    宁可生成一份「没有复用」的脚本，也不要因为整理定位而让整次生成失败。
    """
    def locator_of(target):
        info = _locator_info(target, framework)
        return info['locator'] if info else None

    # 后置处理是「一次不在流程里的调用」，它的子步骤要按动作处理（进公共文件），
    # 不能混进用例文件——那份文件是按用例目录共享的，后置却是跨用例复用的。
    all_actions = dict(actions or {})
    td_action = (teardown or {}).get('action') or {}
    if td_action.get('id'):
        all_actions[td_action['id']] = td_action

    try:
        built = locator_vars.build(steps, all_actions, target_path, root, locator_of)
    except Exception:
        logger.exception('录制生成：定位变量抽取失败，本次退回内联定位')
        return None
    if not built.get('files'):
        return None

    for item in built['files']:
        _note_platform_file(notes, item['path'], item['state'])
        logger.info('录制生成：定位资源 %s（%s，%d 个变量）',
                    item['path'], item['state'], item['count'])

    # 变量清单：给写手写断言时复用现成的名字。同一个元素可能出现在多步，按名字去重。
    aliases = {}
    for step in steps or []:
        target = step.get('target') or {}
        var = built['vars'].get(str(step.get('no')))
        if var and var not in aliases:
            aliases[var] = target.get('alias') or ''
    rel = locator_vars.file_rel(target_path)
    return {
        'vars': built['vars'],
        'action_vars': built['action_vars'],
        'lib': _rel_from_case(rel, target_path),
        'file': {'path': rel,
                 'variables': [{'name': k, 'alias': v} for k, v in aliases.items()]},
    }


# 平台补头的固定内容：浏览器怎么启动、什么时候关。
# 这些行录制里没有——录制侧用 ignore_https_errors 把证书警告页遮掉了，页面本来就是开着的——
# 但生成的脚本缺了就跑不起来：自签名 https 会停在 NET::ERR_CERT_AUTHORITY_INVALID，
# 而 Go To 自己不报错，后面每一步都找不到元素，看起来像「定位写错了」。
def _rf_boot_lines(download=False):
    """平台补头的固定内容：浏览器怎么启动、什么时候关。

    这些行录制里没有——录制侧用 ignore_https_errors 把证书警告页遮掉了，页面本来就是开着的——
    但生成的脚本缺了就跑不起来：自签名 https 会停在 NET::ERR_CERT_AUTHORITY_INVALID，
    而 Go To 自己不报错，后面每一步都找不到元素，看起来像「定位写错了」。

    download=True 时多三行，把浏览器的下载目录绑到 FilesLibrary 用的那个目录上。
    Chrome 只认 Windows 原生反斜杠路径：`${CURDIR}/downloads` 这种混合斜杠会被**静默忽略**，
    文件落到用户默认下载目录，Download File 干等 30 秒报「没有出现下载完成的文件」。
    `Ensure Download Dir` 由 FilesLibrary 提供，返回的就是它自己那个目录的绝对路径，
    两边永远一致，平台不用再算一遍。
    """
    lines = [
        "    ${options}=    Evaluate    sys.modules['selenium.webdriver'].ChromeOptions()"
        "    sys, selenium.webdriver",
        '    Call Method    ${options}    add_argument    --ignore-certificate-errors',
    ]
    if download:
        lines += [
            '    ${download_dir}=    Ensure Download Dir',
            '    ${prefs}=    Create Dictionary    download.default_directory=${download_dir}'
            '    download.prompt_for_download=${False}',
            '    Call Method    ${options}    add_experimental_option    prefs    ${prefs}',
        ]
    lines += [
        '    Create Webdriver    Chrome    chrome_options=${options}',
        '    Set Selenium Implicit Wait    10',
        '    Maximize Browser Window',
    ]
    return lines

# 这些行由平台统一写，模型写了也要删掉，否则会二次打开浏览器 / 覆盖等待参数。
_RF_INIT_PREFIXES = (
    'open browser', 'open chrome',
    'create webdriver', 'maximize browser window',
    'set selenium implicit wait', 'set selenium timeout', 'set selenium speed',
    'close browser',
)
_RF_INIT_CN_PREFIXES = ('打开浏览器', '打开谷歌浏览器', '关闭浏览器')


def _looks_like_boot(body):
    """这一行是不是「浏览器初始化 / 收尾」，该由平台统一写。"""
    if not body:
        return False
    low = ' '.join(body.lower().split())
    if 'chromeoptions()' in low:
        return True
    if low.startswith('call method') and '${options}' in low:
        return True
    for prefix in _RF_INIT_PREFIXES:
        if low.startswith(prefix):
            return True
    return any(body.startswith(prefix) for prefix in _RF_INIT_CN_PREFIXES)


def _strip_fence(text):
    lines = text.strip().splitlines()
    if lines and lines[0].strip().startswith('```'):
        lines = lines[1:]
    if lines and lines[-1].strip() == '```':
        lines = lines[:-1]
    return '\n'.join(lines)


def _split_robot_sections(text):
    """按 `*** Xxx ***` 切段，段名统一小写；tasks 当成 test cases。"""
    out = {}
    current = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('***') and stripped.endswith('***') and len(stripped) > 6:
            name = stripped.strip('*').strip().lower()
            if name == 'tasks':
                name = 'test cases'
            current = name
            out.setdefault(current, [])
            continue
        if current is not None:
            out[current].append(line.rstrip())
    return out


def _first_goto_url(steps, actions=None):
    for step in (steps or []):
        if step.get('type') == 'goto' and step.get('url'):
            return step['url']
    items = actions.values() if isinstance(actions, dict) else (actions or [])
    for action in items:
        for step in (action.get('steps') or []):
            if step.get('type') == 'goto' and step.get('url'):
                return step['url']
    return None


def _trim_blank(lines):
    """去掉首尾空行。不去的话每跑一次规范化就多几行空行，文件越滚越乱。"""
    out = list(lines)
    while out and not out[0].strip():
        out.pop(0)
    while out and not out[-1].strip():
        out.pop()
    return out


def _is_close_line(body):
    """这一行是不是「关闭浏览器」。"""
    low = ' '.join((body or '').lower().split())
    return low.startswith('close browser') or (body or '').strip().startswith('关闭浏览器')


def _normalize_rf_script(root, target_path, steps, actions=None,
                         slider_lib=None, files_lib=None, teardown=None, locator_lib=None):
    """把 agent 写的 .robot 改成「平台头 + 模型正文」，返回被替换掉的行。

    只保留 Test Cases / Keywords / Variables 三段，Settings 整段重写：
    浏览器初始化带 `--ignore-certificate-errors`（内网系统几乎都是自签名证书），
    加隐式等待（录制的 goto 之后元素还没渲染，项目封装里靠 Sleep 5~7 秒硬等），
    `Test Teardown`（用户设的后置处理，失败也执行），以及
    `Suite Teardown Close Browser`（录制结束时不会关浏览器）。

    后置处理存在时，正文里的 `Close Browser` 会被摘掉：它跑在后置**之前**，浏览器一关，
    后置里的步骤就全部落到已关闭的会话上。收尾统一交给 Suite Teardown，顺序才对。
    """
    path = Path(root) / target_path
    try:
        raw = path.read_text(encoding='utf-8')
    except OSError:
        return []
    sections = _split_robot_sections(_strip_fence(raw))
    cases = sections.get('test cases')
    if not cases:
        # 结构不认识就别动：宁可让它原样失败，也不要猜着改坏
        return []

    action = (teardown or {}).get('action') or {}
    teardown_name = (action.get('name') or '').strip()
    has_close = any(step.get('type') == 'close' for step in (steps or []))
    # 正文里该不该保留 close：只有「用户显式加了 close、又没有后置」时才保留。
    keep_body_close = has_close and not teardown_name

    notes = []
    cleaned = []
    close_kept = False
    for line in cases:
        body = line.strip()
        if _looks_like_boot(body):
            notes.append(body)
            if keep_body_close and _is_close_line(body) and not close_kept:
                # 用户显式要的收尾，位置就是他放的那一步；只保留第一次。
                cleaned.append('    Close Browser')
                close_kept = True
            continue
        cleaned.append(line)
    cleaned = _trim_blank(cleaned)

    name_at = None
    for index, line in enumerate(cleaned):
        body = line.strip()
        if body and not line[:1].isspace() and not body.startswith('#'):
            name_at = index
            break
    if name_at is None:
        return []

    # BOOT 要插在用例名之后、[Documentation]/[Tags] 这类设置行之后。
    insert_at = name_at + 1
    while insert_at < len(cleaned):
        body = cleaned[insert_at].strip()
        if not body or (body.startswith('[') and ']' in body):
            insert_at += 1
            continue
        break

    block = _rf_boot_lines(download=bool(files_lib))
    if not any(line.strip().lower().startswith('go to') for line in cleaned):
        url = _first_goto_url(steps, actions)
        if url:
            block.append('    Go To    ' + url)
    cleaned[insert_at:insert_at] = block

    # 模型漏写 close 时补在正文末尾：用户既然加了这一步，就该在脚本里看到它。
    if keep_body_close and not close_kept:
        cleaned = _trim_blank(cleaned)
        cleaned.append('    Close Browser')
        notes.append('补 Close Browser')

    out = []
    origin = next((line for line in raw.splitlines()
                   if line.strip().startswith('# @autocase')), None)
    out.append(origin or '# 头部由平台生成：浏览器怎么启动、什么时候关，这里统一保证。')
    out.append('*** Settings ***')
    out.append('Library    SeleniumLibrary    timeout=10')
    if files_lib:
        # 库在 .autocase/libraries（工作区根，所有用例共用），路径从 CURDIR 往上退到根。
        # 下载目录仍然按用例隔离：默认相对当前工作目录的 downloads/ 会让同项目所有脚本
        # 共用一个目录，并发跑时 A 脚本会看到 B 脚本刚下的文件。
        out.append('Library    ${CURDIR}/%s    ${CURDIR}/downloads' % files_lib)
    if slider_lib:
        out.append('Library    ${CURDIR}/%s' % slider_lib)
    if locator_lib:
        # 定位变量：同目录用例共享一份，改版只改这一个文件。
        # 放在 Library 之后、Teardown 之前，和项目里人工维护的资源文件写法一致。
        out.append('Resource    ${CURDIR}/%s' % locator_lib)
    if teardown_name:
        # 实参由平台写：后置是用户在界面上设的，模型只负责写同名关键字。
        args = [str((teardown.get('args') or {}).get(p.get('key'), ''))
                for p in action.get('params') or []]
        # 空值会让 ${EMPTY} 一样的空单元格把参数位错开，宁可不传，让关键字用默认值。
        args = [value for value in args if value != '']
        out.append('    '.join(['Test Teardown', teardown_name] + args))
    out.append('Suite Teardown    Close Browser')
    out.append('')

    variables = _trim_blank(sections.get('variables') or [])
    if variables:
        out.append('*** Variables ***')
        out.extend(variables)
        out.append('')
    out.append('*** Test Cases ***')
    out.extend(cleaned)
    keywords = _trim_blank(sections.get('keywords') or [])
    if keywords:
        out.append('')
        out.append('*** Keywords ***')
        out.extend(keywords)

    text = '\n'.join(out).rstrip() + '\n'
    if text == raw:
        return notes
    path.write_text(text, encoding='utf-8')
    return notes


def _normalize_pw_script(root, target_path):
    """给 Playwright spec 补 ignoreHTTPSErrors，返回是否改过。

    和 RF 的 `--ignore-certificate-errors` 是同一件事：录制侧 newContext 时开了
    ignore_https_errors，所以警告页在录制中不可见、IR 里没有「继续前往」可录；
    生成侧不开，`page.goto` 到自签名 https 会直接抛
    `net::ERR_CERT_AUTHORITY_INVALID`。

    两种写法分别处理：test runner 用文件级 `test.use`，手动建 context 的写进
    `newContext({...})` —— 后者 `test.use` 不生效。
    """
    path = Path(root) / target_path
    try:
        raw = path.read_text(encoding='utf-8')
    except OSError:
        return False
    text = _strip_fence(raw)
    if 'ignoreHTTPSErrors' in text:
        return False
    lines = text.splitlines()

    manual = next((i for i, line in enumerate(lines) if 'newContext(' in line), None)
    if manual is not None:
        line = lines[manual]
        pos = line.find('newContext(') + len('newContext(')
        rest = line[pos:].lstrip()
        if rest.startswith('{'):
            lines[manual] = line[:pos] + ' { ignoreHTTPSErrors: true,' + rest[1:]
        elif rest.startswith(')'):
            # 原来无参：newContext() -> newContext({ ignoreHTTPSErrors: true })
            lines[manual] = line[:pos] + '{ ignoreHTTPSErrors: true }' + rest
        else:
            lines[manual] = line[:pos] + '{ ignoreHTTPSErrors: true }, ' + rest
    else:
        last_import = -1
        for index, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith('import ') or stripped.startswith('} from '):
                last_import = index
        if last_import < 0:
            # 没有 import 就不是 test runner 的 spec，别猜着插
            return False
        lines[last_import + 1:last_import + 1] = ['', 'test.use({ ignoreHTTPSErrors: true });']

    path.write_text('\n'.join(lines).rstrip() + '\n', encoding='utf-8')
    return True


def _write_slider_env(root):
    """把视觉模型的三个环境变量补进项目根的 .env。已存在的键一个都不覆盖。

    滑块和验证码共用这一组（SLIDER_MODEL_*）：都是「能看图的模型」，
    没必要配两遍。captcha.ts 里优先读 CAPTCHA_MODEL_*，缺了才回落这里写的值，
    所以想给验证码单独换模型时，自己往 .env 加三个 CAPTCHA_MODEL_* 就行。

    用户明确要求「直接写到 env，如果之前已经加过就不写了」：生成出来的代码要能
    开箱即跑，但同一个项目反复生成不能把 .env 越堆越乱，所以按 key 去重。
    写的是当前配置里的值，换模型后重新生成不会改已有行——那属于用户自己的环境。
    """
    from app.services import models_config
    ref = models_config.slider_model()
    resolved = models_config.resolve(ref) if ref else None
    if not resolved or not resolved.api_base:
        return None
    path = Path(root) / '.env'
    existing = path.read_text(encoding='utf-8') if path.exists() else ''
    pairs = (
        ('SLIDER_MODEL_BASE_URL', resolved.api_base),
        ('SLIDER_MODEL_NAME', resolved.model),
        ('SLIDER_MODEL_API_KEY', resolved.api_key or ''),
    )
    added = ['%s=%s' % (key, value) for key, value in pairs
             if not re.search(r'^\s*%s\s*=' % re.escape(key), existing, re.M)]
    if not added:
        return None
    block = ('\n' if existing and not existing.endswith('\n') else '') + '\n'.join(added) + '\n'
    with open(path, 'a', encoding='utf-8') as fh:
        fh.write(block)
    return path


def _settle_rules(payload):
    """「执行前等待」怎么写。

    面板上给某一步开了开关（steps 里带 settle），语义是「先等这一步的元素出现，再动它」。
    必须说清楚插在动作**之前**、等的是**这一步自己的 target**，而且只对带 settle 的步骤生效——
    不然写手会给每一步都补一行 Wait Until（它上一版就是这么把 9 步写成 18 行的）。
    """
    if not any(step.get('settle') for step in (payload.get('steps') or [])):
        return ''
    if payload.get('framework') == RF_FRAMEWORK:
        return (
            '本次有步骤带「执行前等待」（steps 里带 settle 的项）。\n'
            '- 对带 settle 的步骤：**先写一行等待，紧接着写这一步的动作**，'
            '等待的元素就是这一步自己的 target.locator：\n'
            '  `Wait Until Element Is Visible    <这一步 target.locator>    <settle.timeout>s`\n'
            '  不要等下一步的元素，不要改超时，不要调整这两行的先后。\n'
            '- **没有 settle 的步骤一律不要加 Wait Until / Sleep**，一行写完就结束；'
            '元素出现这件事由平台设置的隐式等待负责，不要再抄一遍。\n'
            '- 这条同样管 actions 里的子步骤：动作内部没有 settle 的步骤也不要写等待。\n'
        )
    return (
        '本次有步骤带「执行前等待」（steps 里带 settle 的项）。\n'
        '- 对带 settle 的步骤：**先等这一步自己的元素，再执行动作**：\n'
        '  `await expect(page.locator(<这一步 target.locator>)).toBeVisible({ timeout: <settle.timeout> * 1000 })`\n'
        '  不要等下一步的元素，不要改超时。\n'
        '- **没有 settle 的步骤不要加任何 expect().toBeVisible / waitForTimeout**。\n'
    )


def _slider_rules(payload):
    """滑块那一步怎么写。

    单独抽出来是因为它有两个容易踩的点：
    1. 滑块通常在**动作的子步骤**里，只扫顶层会漏（这正是它之前从没被注入过的原因）；
    2. 两个框架的能力位置不同 —— PW 是 `utils/slider.ts` 里的 `solveSlider`，
       RF 是 `libraries/SliderLibrary.py` 里的 `Solve Slider`。写成同一句话，
       总有一个框架拿到用不了的指令。
    """
    if not _has_slider(payload.get('steps'), payload.get('actions')):
        return ''
    head = '本次步骤里有**滑块验证**（可能在某个动作的子步骤里）。\n'
    head += '- 不要自己写截图、找缺口、算坐标、拼拖动轨迹的代码，也不要去找页面上的 canvas。\n'
    if payload.get('framework') == RF_FRAMEWORK:
        head += (
            '- 直接写一行 `Solve Slider    <selector>`：selector 取该项 slider.selector 原值，'
            'attempts / timeout 用 slider 字段里的值（没给就省略）。\n'
            '- 这个关键字来自平台库，`Library` 行由平台写在文件头里，你不要写 Settings。\n'
        )
    else:
        head += (
            '- 直接调用平台注入的 `solveSlider(page, config)`，config 用该项的 slider 字段原值。\n'
            '- 从平台生成的 `utils/slider` 导入：`import { solveSlider } from \'./utils/slider\';`'
            '（工具文件由平台写在 target_path 同级的 utils/ 里，不要覆盖它）。\n'
            '- 这个调用是异步的，必须 await；它内部最多重试 3 次，失败会抛错，不要再包一层重试。\n'
        )
    head += '- 不要给这一步写断言：滑块通过后页面自己会跳转，后续步骤的断言已经覆盖了结果。\n'
    return head


def _prompt(payload, resumed=False):
    head = ''
    if resumed:
        # 同一会话的第二轮：项目已经在这个会话里读过了，重复扫只会烧时间。
        head += (
            '这是同一个会话的第二轮：上一轮你已经读过这个项目，并给出了任务书。\n'
            '- 不要重新扫项目，不要再 glob / ls 找惯例；只有需要某个文件的精确内容'
            '（一行 import、一个封装的签名）时才读 1~2 个文件。\n'
            '- 任务书以本轮 plan 字段为准（用户可能改了 target_path、提了意见），'
            '不要沿用上一轮的原始版本；本轮给的 steps / samples / keywords 是完整事实，以它为准。\n'
            '- 本轮不要再输出任务书，直接写文件；完成后用一句中文说明写了哪个文件、覆盖了几步。\n'
            '- 直接写 target_path 这个文件，不要为它新建目录：目录结构是用户/项目定的，'
            '建目录会被拒。辅助文件同理，放在它所在的目录里。\n'
        )
    head += (
        '请按下面的录制结果写入一个测试文件。\n'
        '- 只写 target_path 这一个文件，路径以 payload 里的 target_path 为准。\n'
    )
    if payload.get('framework') == RF_FRAMEWORK:
        head += (
            '- **不要写 `*** Settings ***`**：Library、Suite Teardown 全部由平台补。\n'
            '- 只写 `*** Test Cases ***`（和需要时的 `*** Keywords ***`），'
            '从录制里的第一步 `Go To` 开始，一步一步翻译。\n'
            '- 不要写任何 `Open Browser` / `Create Webdriver` / `ChromeOptions` / '
            '`Maximize Browser Window` / `Set Selenium Implicit Wait` / `Close Browser`，'
            '也不要调用项目里的「打开浏览器并登录…」这类封装：浏览器由平台打开，'
            '证书、等待、收尾都在平台那一层处理。\n'
        )
    if payload.get('write_scope') == 'workspace':
        head += (
            '- 这几步如果确实需要一个辅助文件（helper / fixture / 类型定义），可以写，'
            '但要放在 target_path 同一个测试目录下，能内联就内联。\n'
            '- 不许改 package.json、playwright.config、tsconfig、.env 这类配置，'
            '也不要动其他已存在的测试文件——那会影响别人的用例能不能跑，'
            '而且这些改动会被列成「越界」报给用户。\n'
            '- 写完在回复里逐个说明额外文件：路径、用途、为什么不能内联。\n'
        )
    elif payload.get('write_scope') == 'instruction_only':
        head += (
            '- 本次没有运行时兜底（规划期的会话被复用）。写到 target_path 以外的任何文件'
            '都会被记录成越界并报给用户。\n'
        )
    if payload.get('plan'):
        head += (
            '本次已经有人替你读过项目，每一步要做什么写在 plan.steps 里。\n'
            '- plan.steps 的 note 是这一步的人话说明，只拿来写注释，不要照它编造定位。\n'
            '- 动作怎么写、用哪条定位，一律以 payload.steps 为准。\n'
        )
    else:
        head += (
            '没有任务书：动笔之前先自己看一眼项目和已有用例，跟上它们的写法再写。\n'
        )
    if payload.get('actions'):
        head += (
            '本次步骤里有**封装动作**：steps 里 type 为 action 的项不是普通操作，'
            '而是对 actions 里某个动作的一次调用。\n'
            '- actions 的每个条目写成一个函数（Robot Framework 写成一个 Keyword）：'
            '函数名用 name，参数按 params 的顺序。子步骤里的 `{{key}}` 是占位符，'
            '函数体必须用同名参数，绝不能把 `{{...}}` 原样写进代码。\n'
            '- steps 里每次 action 调用写成对这个函数的一次调用，实参取该项的 args。'
            '同一动作被调用多次时（见 called_at），**必须复用同一个函数**，'
            '绝不允许展开成多段重复代码——封装的目的正是这个。\n'
            '- 即使 called_at 只有一处也要抽成函数：封装是用户给的命名，不是自动去重。\n'
            '- actions 里 steps 的 no 是**动作内部的相对序号**，不是流程里的步号；'
            '流程位置只看 called_at。\n'
            '- 函数放在 target_path 里（import 之后、test 之前）。项目里已经有覆盖同样动作的'
            '封装时用项目那个，不要重复定义。\n'
        )
    if payload.get('project_memory'):
        if (payload.get('project_memory') or {}).get('map'):
            head += (
                'project_memory.map 是这个项目的「地图」：整个仓库里哪块代码在哪、新用例该放哪个目录、'
                '有哪些现成封装该怎么调。'
                + ('samples 里是一条真实用例。\n' if payload.get('samples') else '\n') +
                '- 优先级：plan（本次任务书）> project_memory（项目长期写法）> 下面的通用规则。'
                '两者冲突时以 plan 为准，它是针对这一次的。\n'
                '- 「现成封装」里能覆盖某一步的，直接按它写的调用方式用，不要重写同样的动作。\n'
                '- 「测试」那一节说明新文件该放哪、怎么命名，跟上它。\n'
                '- 地图是某个时间点的快照；和你眼前读到的文件冲突时，以文件为准。\n'
            )
        else:
            head += (
                'project_memory 是这个项目长期积累的画像（有人读遍项目后总结的）。\n'
                '- 优先级：plan（本次任务书）> project_memory（项目长期写法）> 下面的通用规则。'
                '两者冲突时以 plan 为准，它是针对这一次的。\n'
                '- helpers / fixtures 里列出的封装只要能覆盖某一步，就直接调用，不要重写同样的动作。\n'
                '- assertions 是这个项目真实用过的断言；没有特别理由不要引入清单外的写法。\n'
            )
    if payload.get('plan_feedback'):
        head += (
            '用户对任务书提了修改意见（plan_feedback：quote 是原文，comment 是他的要求）。\n'
            '这些意见优先于任务书本身：两者冲突时按用户意见写。\n'
        )
    if payload.get('toolchain'):
        head += '目标环境的真实情况（不是猜测，动笔前必须按它对齐）：\n'
        head += (
            '- toolchain 是这台机器上实际安装的版本。只写这个版本支持的东西：'
            '参数个数、关键字是否存在都以它为准，不要凭记忆用更新版本才有的写法。\n'
        )
    head += _slider_rules(payload)
    head += _settle_rules(payload)
    if payload.get('locator_file'):
        head += (
            '本次的**定位已经抽到资源文件里**，正文只写变量名：\n'
            '- steps 里每一步的 target.locator_var 就是这一步该用的变量，原样写；'
            '不要换成 xpath / css，也不要自己拼一条「更稳」的。\n'
            '- 资源文件由平台写好，`*** Settings ***` 的 Resource 行也由平台补，不要自己写。\n'
            '- 不要新增变量，也不要改那个文件。\n'
            '- 录制步骤之外的判断（断言、轮询条件）用到的元素如果出现在 '
            'locator_file.variables 里，优先用现成的变量；那里没有的才自己写定位。\n'
        )
    if any(step.get('type') == 'captcha' for step in payload.get('steps') or []):
        head += (
            '本次步骤里有**图形验证码识别**（steps 里 type 为 captcha 的项）。\n'
            '- 不要自己写截图、放大、调模型、填输入框的代码，也不要去读验证码元素的 innerText。\n'
            '- 直接调用平台注入的 `solveCaptcha(page, config)`，config 用该项的 captcha 字段原值\n'
            '  （字段名是 camelCase：selector / inputSelector / charset / length / attempts）。\n'
            '- 从平台生成的 `utils/captcha` 导入：`import { solveCaptcha } from \'./utils/captcha\';`'
            '（工具文件由平台写在 target_path 同级的 utils/ 里，不要覆盖它）。\n'
            '- 这个调用是异步的，必须 await；它内部已经做了刷新重试，失败会抛错，不要再包一层重试。\n'
            '- 它只负责把码填进输入框，**不点提交**：点击登录/确定是后面独立的步骤，不要并进来。\n'
            '- 不要给这一步写断言。码对不对由后面的提交结果决定，这里断言不了。\n'
        )
    if payload.get('teardown'):
        if payload.get('framework') == RF_FRAMEWORK:
            head += (
                '本次有**后置处理**（payload.teardown）：它是收尾动作，**不写在用例正文里**。\n'
                '- 在 `*** Keywords ***` 里写一个与 teardown.name **完全同名**的 Keyword，'
                '子步骤照常翻译；`{{key}}` 用 params 里的同名参数，默认实参取 args。\n'
                '- **不要**在正文里调用它，也**不要**写 `Test Teardown`：\n'
                '  平台会把 `Test Teardown    <teardown.name>` 写进 Settings，用例失败时也会执行。\n'
                '- 不要在这个 Keyword 里加 Close Browser：收尾时的关浏览器由平台的 Suite Teardown 负责。\n'
                '- teardown 存在时，正文里的 close 步骤不写 `Close Browser`（同样交给 Suite Teardown）；'
                '它不会漏执行，只是执行在最后。\n'
            )
        else:
            head += (
                '本次有**后置处理**（payload.teardown）：它是收尾动作，**不写在 test 正文里**。\n'
                '- 写成 `test.afterEach(async ({ page }) => { ... })`，放在所有 test 之外。\n'
                '- 子步骤照常翻译；`{{key}}` 用 params 里的同名参数，默认实参取 args。\n'
                '- 不要在 test 里调用它，也不要写成 beforeEach。\n'
                '- 脚本不是 test runner（自己 newContext）时没有 afterEach：把正文包进 '
                '`try { ... } finally { ... }`，后置放 finally，保证失败也执行。\n'
                '- teardown 存在时，正文里不要再写 `page.close()`：把 close 步骤放到 afterEach '
                '（或 finally）的最后一行，否则后置会跑在已经关掉的页面上。\n'
            )
    return (
        head
        + LOCATOR_RULES
        + FRAMEWORK_RULES.get(payload.get('framework'), '')
        # 紧挨着数据说：规则写在几百行上面时模型会漏，字段就在它要写的这一步旁边时不会。
        + 'steps 里带 poll_rule 字段的那一步，poll_rule 就是硬约束，必须照做。\n'
        + '```json\n' + json.dumps(payload, ensure_ascii=False, indent=2) + '\n```\n'
    )


def _plan_for_prompt(plan):
    """任务书只把「每一步做什么」交给写手，其余字段在这里拦下。

    三个必须拦的：
    - `project_style.auth_setup` 写着「Suite Setup 登录」，而录制步骤里本来就有登录。
      照它写就是双重登录，实测过，正文登录完再 Setup 一次，用例直接跑不过。
    - `project_style.import_sample` / `reference_files` 会勾着写手去 Resource 项目封装，
      但生成的每一步都要用录制下来的定位，调封装等于把录制的定位白扔。
    - `target_path` / `target_path_suggestion` 和 payload.target_path 冲突，
      提示词开头得专门写一句「不要拿它当主文件」，那句话本身就是这个冲突的代价。

    `_note_fix` 之类是 planner 自己的修订备注，只对写 planner 的人有意义。
    """
    if not plan:
        return plan
    out = {}
    if plan.get('steps'):
        out['steps'] = [{'no': item.get('no'), 'note': item.get('note')}
                        for item in plan['steps'] if item.get('note')]
    # 风险和缺口是给人看的结论，写手看到会试图「顺手处理掉」，反而跑偏。
    return out


def _payload(framework, steps, target_path, title, detected, keywords, plan=None,
             plan_feedback=None, memory=None, context_reused=False, write_scope='exact',
             actions=None, teardown=None, locator=None):
    """locator 是 locator_vars.build 的结果再补一个 file 清单：
    {'vars': {步骤号: '${变量}'}, 'action_vars': {动作id: {子步骤号: '${变量}'}},
     'file': {'path': ..., 'variables': [...]}}。为空表示这次没抽变量。"""
    samples = list(detected.get('samples') or [])
    snippet = (memory or {}).get('snippet') or {}
    if snippet.get('code'):
        # 画像里的范例优先于 detect 的「最近改过的前 60 行」：后者常常只有 import 和
        # describe 开头，helper 封装、断言习惯全在 60 行之后，等于给了目录没给正文。
        samples = [{
            'path': snippet.get('file') or 'project sample',
            'head': snippet['code'],
            'generated': False,
        }]
    else:
        # 自动采集的「项目里最近改过的用例」不再交给写手。
        # 它是写手抄项目封装的直接原因：里面每一行都是项目里的写法，写手会照着它
        # 把录制的定位换成 `打开浏览器并登录网管` 这类封装，连「导出」都会抄成
        # Click Element（实测就是这么丢的下载校验）。而且它常占提示词三成体积。
        # 只有用户在画像里明确挑过范例（snippet）时才给。
        samples = []
    payload = {
        'framework': framework,
        'target_path': target_path,
        'title': title,
        'steps': _steps_for_prompt(steps, framework, actions,
                                   (locator or {}).get('vars')),
        'evidence': detected.get('evidence', []),
        'samples': samples,
        # keywords 不再下发。176 条清单占 11,000 字符，而一次用例平均只用 6~8 个关键字；
        # 更糟的是规则曾经写着「必须能在这份清单里找到」，清单里没有滑块（Solve Slider）
        # 时写手直接拒绝生成。实测不给清单，它照样写对（见 _promptlab 实验报告）。
        'plan': _plan_for_prompt(plan),
        # 项目画像：跨多次录制复用，plan 是针对这一次的，两者同时在场。
        'project_memory': memory,
        # 用户对任务书的意见：选中片段 + 他自己的话。写手必须照这个改，
        # 否则「选中后评价」只是界面上的装饰。
        'plan_feedback': _clean_feedback(plan_feedback),
        'output_language': 'Simplified Chinese for progress notes; keep code and identifiers unchanged.',
    }
    # 版本是硬事实：老环境里 syntax 对不等于关键字存在。只在真探测到时加，别给写手一堆 null。
    toolchain_versions = {
        name: detected.get(key)
        for name, key in (('robot', 'robot_version'), ('seleniumlibrary', 'seleniumlibrary_version'))
        if detected.get(key)
    }
    if toolchain_versions:
        payload['toolchain'] = toolchain_versions
    if context_reused:
        # 同一会话的第二轮：读过的项目还在上下文里，别让它从头再扫一遍。
        payload['context_reused'] = True
    payload['write_scope'] = (write_scope if write_scope in ('exact', 'workspace', 'instruction_only')
                              else 'exact')
    if actions:
        # 只在真有过封装时才加这个键：没有动作时多一个空列表会让写手以为漏了什么。
        payload['actions'] = _actions_for_prompt(actions, framework, steps,
                                                 (locator or {}).get('action_vars'))
    if (locator or {}).get('file'):
        # 变量清单给出来是让它写断言时能复用现成的名字，而不是为了让它挑定位——
        # 定位已经在文件里，改动由平台统一管。
        payload['locator_file'] = locator['file']
    shaped = _teardown_for_prompt(teardown, framework, (locator or {}).get('action_vars'))
    if shaped:
        payload['teardown'] = shaped
    return payload


def _validate(framework, python, workspace, target_path):
    """返回 (是否通过, 输出)。缺工具时 toolchain 会抛 ToolchainError。"""
    path = Path(workspace) / target_path
    if framework == RF_FRAMEWORK:
        return toolchain.dryrun(python, path, cwd=str(workspace))
    return toolchain.tsc_check(workspace, target_path=target_path)


def _write_with_session(session, prompt, runner, root, model, target_path):
    """写轮优先走同一个会话；会话没了就退回一次性生成。

    返回 (回复文本, 会话是否还可用)。退回时会话已不可用，后面的修复轮也别再试它。
    """
    from app.services.recording.emit_session import SessionLost
    try:
        return session.emitter_turn(prompt), True
    except SessionLost as exc:
        logger.warning('生成会话已失效（%s），退回一次性生成', exc)
        return runner.emit_recording(root, model, prompt, writable=target_path), False


def _repair_turn(session, runner, root, model, target_path):
    """修复轮也留在同一个会话里：它知道刚才写了什么，比重新读一遍准。"""
    from app.services.recording.emit_session import SessionLost

    def turn(prompt):
        try:
            return session.repair_turn(prompt)
        except SessionLost as exc:
            logger.warning('修复轮会话已失效（%s），退回一次性生成', exc)
            return runner.emit_recording(root, model, prompt, writable=target_path)

    return turn


def generate(runner, workspace, model, steps, framework, target_path, title, python=None,
             overwrite=False, plan=None, plan_feedback=None, progress=None, storage_state=None,
             memory=None, session=None, write_scope='exact', actions=None, teardown=None):
    """按任务书写入脚本。返回 {'path', 'changed', 'outside', 'backup_id', 'validated', ...}。

    这里**不再跑规划**：规划是独立的一步（见 plan），任务书由用户确认后随请求带进来。
    传 plan=None 仍然可以写，但那只用于测试/兜底；产品入口要求必须有任务书。

    session 给出时，写轮和后面的修复轮都走同一个 opencode 会话；不给就退回一次性的
    emit_recording，行为与以前完全一致。write_scope 告诉写手运行时允许写到哪：
    `workspace`（整个工作区）、`exact`（只准写 target_path）、`instruction_only`
    （本轮没有运行时兜底）。
    """
    def mark(stage, detail=''):
        if progress is not None:
            progress(stage, detail)

    root, degraded = _prepare(workspace, steps, framework, target_path, actions)

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
        if _has_slider(steps, actions):
            # 平台库的关键字不在 SeleniumLibrary 的清单里。不补这一条，
            # 「只能用清单里的关键字」那条规则会让写手认为 Solve Slider 违规，
            # 上一版就是这么被逼到「拒绝生成」的。
            keywords.append({'name': 'Solve Slider',
                             'args': ['selector', 'attempts=2', 'timeout=40']})

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
    if memory:
        logger.info('录制生成：注入项目记忆（地图 %d 字符 / 范例=%s）',
                    len(memory.get('map') or ''),
                    (memory.get('snippet') or {}).get('file') or '无')

    before = None
    resumed = session is not None
    # 后置处理的子步骤也要参与「需要哪些平台工具」的判定：后置里可能有下载校验、
    # 验证码或滑块，按顶层 steps 扫会漏掉，生成出来的文件就少一个 Library。
    teardown_steps = list(((teardown or {}).get('action') or {}).get('steps') or [])
    all_steps = list(steps or []) + teardown_steps
    # 有滑块 / 验证码步骤时先把工具文件落到项目里（在快照之前，见函数注释）。
    platform_files = []
    slider_utils = _ensure_slider_utils(root, target_path, all_steps, framework, actions,
                                        notes=platform_files)
    captcha_utils = _ensure_captcha_utils(root, target_path, all_steps, framework)
    files_utils = _ensure_files_utils(root, target_path, all_steps, framework,
                                      notes=platform_files)
    # 定位抽成资源文件里的变量。只传顶层 steps：后置的子步骤走 teardown 里的动作，
    # 混进来的话会用用例目录前缀命名，和动作那条路径撞车。
    locator = None
    if framework == RF_FRAMEWORK:
        locator = _build_locator(root, target_path, steps, actions, framework,
                                 teardown=teardown, notes=platform_files)
    for item in platform_files:
        logger.info('录制生成：平台文件 %s（%s）', item['path'], item['state'])
    if slider_utils or captcha_utils:
        _write_slider_env(root)
    try:
        # 快照本身也可能失败（git 异常、目录不可读），必须放在 try 里，否则会变成没有原因的 500。
        mark('snapshot')
        before = guard.snapshot(root)
        mark('writing')
        payload = _payload(framework, steps, target_path, title, detected, keywords,
                           plan, plan_feedback, memory,
                           context_reused=resumed, write_scope=write_scope,
                           actions=actions, teardown=teardown, locator=locator)
        prompt = _prompt(payload, resumed=resumed)

        if resumed:
            reply, session_alive = _write_with_session(session, prompt, runner, root, model,
                                                       target_path)
        else:
            reply = runner.emit_recording(root, model, prompt, writable=target_path)
            session_alive = False

        repair_turn = (_repair_turn(session, runner, root, model, target_path)
                       if session_alive else None)
        mark('check')
        if not target_abs.is_file():
            raise EmitError('agent 没有写出目标文件：%s' % target_path,
                            details=_missing_target_details(before, target_path, reply))

        # 头部（Settings + 浏览器初始化 + Teardown）一律由平台重写。
        # 录制的第一步是 Go To，但录制时页面已经开着、证书警告页也被 ignore_https_errors
        # 遮掉了，所以「怎么打开浏览器」这件事在 IR 里根本不存在，只能由平台保证。
        if framework == RF_FRAMEWORK:
            replaced = _normalize_rf_script(root, target_path, steps, actions,
                                            slider_utils, files_utils, teardown,
                                            locator_lib=(locator or {}).get('lib'))
            if replaced:
                logger.info('录制生成：头部已由平台重写，替换 %s 行：%s',
                            len(replaced), ' / '.join(replaced[:4]))
        elif framework == PW_FRAMEWORK:
            if _normalize_pw_script(root, target_path):
                logger.info('录制生成：已补 ignoreHTTPSErrors（内网系统多是自签名证书）')

        mark('validate')

        def _rewrite_head():
            """修复轮之后重新盖平台头：模型重写整个文件时会把它改回自己的写法。"""
            if framework == RF_FRAMEWORK:
                _normalize_rf_script(root, target_path, steps, actions,
                                     slider_utils, files_utils, teardown,
                                     locator_lib=(locator or {}).get('lib'))
            elif framework == PW_FRAMEWORK:
                _normalize_pw_script(root, target_path)

        validated, output = _check_with_repair(
            runner, root, model, framework, target_path, detected, writable=target_path,
            progress=progress, turn=repair_turn, after_fix=_rewrite_head,
        )
        if not validated:
            raise EmitError('校验未通过' if existed else '校验未通过，文件已保留',
                            details=[output] + _target_hint(target_path, existed))

        # 静态校验之后真跑一遍。tsc 只证明「能编译」，证明不了「点下去能过」；
        # 录制的定位是现成的，但等待、跳转、弹窗时机全靠写手想象，只有执行能证伪。
        # Robot 不做：录制侧只有 Playwright，Selenium 环境不在这一侧，硬跑会变成假失败。
        run = None
        if framework == PW_FRAMEWORK:
            try:
                run = _run_with_repair(runner, root, model, target_path, storage_state, progress,
                                       turn=repair_turn)
            except Exception as exc:  # 验证层自己出问题，不该把已经写好的文件判成失败
                logger.exception('执行验证失败：%s', target_path)
                run = {'ran': False, 'reason': '执行验证时出错：%s' % exc}
    except EmitError as exc:
        raise _abort(target_abs, exc, target_path, existed)
    except toolchain.ToolchainError as exc:
        raise _abort(target_abs, EmitError(str(exc)), target_path, existed)
    except (RuntimeError, guard.GuardError) as exc:
        raise _abort(target_abs, EmitError(str(exc)), target_path, existed)

    # 校验之后再取一次：产物目录已在 guard 里排除，剩下的就是真正被动过的文件。
    after = guard.changed(before)
    outside = guard.violations(after, target_path)
    backup_id = None
    if outside:
        # 立刻把越界文件在「生成前」的内容导出来。stash create 出来的 commit 没有 ref，
        # git gc 之后就取不到了；导出之后用户点还原才不依赖它。
        try:
            backup_id, _entries = guard.capture(before, outside)
        except Exception:  # noqa: BLE001 - 备份失败不该让已经写好的文件判成失败
            logger.exception('导出越界备份失败：%s', target_path)
    # 正文里还留着资源文件中的定位原文 = 写手没用变量。只提示不失败：
    # 脚本照样能跑，只是失去「改版改一处」的意义，得让人知道。
    locator_report = None
    if locator:
        locator_report = {
            'file': locator['file']['path'],
            'variables': len(locator['file']['variables']),
            'bare': _bare_locators(target_abs, Path(root) / locator['file']['path']),
        }
        if locator_report['bare']:
            logger.warning('录制生成：正文里有 %d 条定位没走变量：%s',
                           len(locator_report['bare']),
                           ' / '.join(locator_report['bare'][:2]))
    return {
        'path': target_path,
        'changed': after,
        # 目标文件以外的改动：不自动还原（模型可能确实需要写 helper），界面列出来让用户决定。
        'outside': outside,
        'backup_id': backup_id,
        'validated': True,
        # 真实执行的结果：None 表示没跑（Robot），ran=False 表示跑不起来，
        # ok=False 表示跑了但没过。三种情况界面上的说法必须不一样。
        'run': run,
        'framework': framework,
        'detected': detected.get('framework'),
        'reply': reply,
        # 平台自己在任务书之外写/复用的辅助文件（共享库）。不列出来，用户只会看到
        # .robot，却在目录里发现 .autocase/，对不上账。
        'platform_files': platform_files,
        # 任务书：前端/记录页用它展示「这次打算复用哪些现成封装」。
        'plan': plan,
        # 哪些步骤是降级生成的（定位不唯一，按第 n 个匹配写入），前端据此提醒用户回放确认。
        'degraded': degraded,
        # 定位资源文件：写了哪个文件、几个变量、正文里还有几条没走变量。
        'locator': locator_report,
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
                       progress=None, turn=None, after_fix=None):
    """校验；失败则把输出交回 agent 修一次；仍失败返回 (False, 输出)。

    turn 给出时走同一个 opencode 会话（见 emit_session）；不给就退回一次性的
    emit_recording，行为和以前完全一致。

    after_fix 在每轮修正之后调用。修复轮是让模型重写整个文件，它会按自己的习惯
    把平台补的头部改回去（`Suite Teardown 关闭浏览器` 这种项目里根本没有的关键字），
    于是「改对了正文、改坏了头」还是校验不过。头部只能由平台保证，修完必须再盖一次。
    """
    def mark(stage, detail=''):
        if progress is not None:
            progress(stage, detail)

    def send(text):
        if turn is not None:
            return turn(text)
        return runner.emit_recording(root, model, text, writable=writable)

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
        send(fix)
        if after_fix is not None:
            after_fix()
        mark('validate', '第 %d 轮修正后重新校验' % rounds)
        ok, output = _validate(framework, python, root, target_path)
    return ok, output


def _run_with_repair(runner, root, model, target_path, storage_state, progress=None, turn=None):
    """真跑一遍；失败把运行时错误交回写手修，再跑。

    只在 Playwright 上用。登录态通过临时文件注入（见 toolchain.run_playwright），
    所以「跑不过」里的登录问题已经被排除掉了 —— 剩下的才是脚本自己的问题。
    环境不通（服务没起、域名解析不了）不修：那不是代码错，硬修只会把定位改坏。
    """
    def mark(stage, detail=''):
        if progress is not None:
            progress(stage, detail)

    def send(text):
        if turn is not None:
            return turn(text)
        return runner.emit_recording(root, model, text, writable=target_path)

    mark('run', '正在真实执行生成的用例')
    result = toolchain.run_playwright(root, target_path, storage_state)
    rounds = 0
    last = None
    while result.get('ran') and not result.get('ok') and rounds < MAX_RUN_REPAIR_ROUNDS:
        if result.get('env_error'):
            mark('run', '页面打不开，未自动修复')
            result['note'] = '目标站点不可达，这次没有判断脚本本身对不对'
            break
        signature = _run_signature(result)
        # 修完还是同一个错误：再修一轮大概率还是它，停手比空转好。
        if signature and signature == last:
            break
        last = signature
        rounds += 1
        mark('run_repair', '第 %d 轮修正运行时错误' % rounds)
        try:
            send(_run_fix_prompt(target_path, result))
        except (RuntimeError, ValueError) as exc:
            result['repair_error'] = '自动修正没能执行：%s' % exc
            break
        mark('run', '第 %d 轮修正后重新执行' % rounds)
        result = toolchain.run_playwright(root, target_path, storage_state)
    result['auth'] = bool(storage_state)
    # 没抓到登录态时，「跑不过」可能只是没登录。必须说清楚，否则用户会去改本来正确的定位。
    if not storage_state and result.get('ran') and not result.get('ok') and not result.get('note'):
        result['note'] = '这次没有可注入的登录态（录制结束后浏览器可能已关闭），失败也可能来自登录。'
    return _compact_run(result)


def _run_fix_prompt(target_path, run):
    errors = '\n'.join('- ' + item for item in (run.get('errors') or []))
    if not errors:
        errors = '- （没有拿到结构化错误）'
    tail = (run.get('tail') or '').strip()
    return (
        '上一次写入的 %s 能通过类型检查，但真跑起来失败了。请只修改该文件，不要改动其他文件。\n'
        '运行方式：playwright test %s。运行环境已经注入录制时的登录态，'
        '所以「登录不上」「被重定向到登录页」不是失败原因，不要为了绕过登录去改流程。\n'
        '失败信息：\n%s\n' % (target_path, target_path, errors)
        + ('原始输出尾部：\n```\n%s\n```\n' % tail if tail else '')
        + '修正原则：\n'
        '- 定位匹配到多个元素（strict mode violation）→ 用 .first() / .nth(i) 或更窄的定位，不要用 force。\n'
        '- 等不到元素 → 先判断是不是缺了等待、或上一步的跳转没等，不要只把超时改大。\n'
        '- 断言失败 → 看 received 值，先确认断言方法有没有用错'
        '（toHaveText 是相等，toContainText 才是包含）。\n'
        '- 不允许删步骤、删断言、加 test.skip / test.fixme 来让测试通过。\n'
        '- 不要改 playwright 配置，不要新建文件。\n'
    )


def _run_signature(run):
    """判断「改了没有」用的指纹：Playwright 的错误里带计数和耗时，
    原样比会永远不相等（`14 × locator resolved` 每轮都变），必须先把数字抹掉。"""
    text = '\n'.join(run.get('errors') or []) or (run.get('tail') or '')
    return re.sub(r'\d+', '#', text)[:600]


def _compact_run(run):
    """返回给前端前收一下体积：原始输出只在「根本没跑起来」时才有价值。"""
    if not isinstance(run, dict):
        return run
    result = dict(run)
    if result.get('ran'):
        result.pop('tail', None)
    elif result.get('tail'):
        result['tail'] = result['tail'][-1200:]
    result['errors'] = list(result.get('errors') or [])[:3]
    return result
