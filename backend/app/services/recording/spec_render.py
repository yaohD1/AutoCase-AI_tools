"""把录制步骤渲染成一份可以真的跑起来的 Playwright spec。

**它只服务一件事**：给 AI 自愈提供一个「和回放等价」的现场。

为什么不能直接拿录制步骤去跑官方 healer：官方 healer 的入口是 `test_run`，
它需要一个 Playwright 项目；而回放是 Python 直接驱动浏览器、按步骤 JSON 解释执行的，
磁盘上根本没有 spec。没有 spec，healer 连第一步都过不去。

所以这里做一次投影：把 steps 翻译成 spec。**必须和 `replay._run_step` 一一对应**——
渲染出来的用例点错了元素，healer 修的就是一个不存在的问题，
结论再漂亮也是错的。定位一律走 `locators.render_pw` / `render_chain`，
和回放、计数、预览共用同一份 spec，不另写一套字符串拼接。

已知边界：
- iframe 内的步骤暂不投影（`frameLocator` 需要原始选择器，录制里只有 url/name）。
- `dialog` 简化为统一 accept，按消息内容分流的情况会失真。
这两类会在文件头写进注释，让 healer 和用户都能看到「这份投影不完整」。
"""

import json

from app.services.recording.locators import render_chain, render_pw

# 断言意图 → Playwright 断言方法。没列进来的意图说明投影不了，直接写 todo 注释，
# 不要瞎猜一个断言塞进去——那会让 healer 以为这里本来就该这么验。
_ASSERT_METHOD = {
    'visible': 'toBeVisible',
    'hidden': 'toBeHidden',
    'enabled': 'toBeEnabled',
    'text_contains': 'toContainText',
    'text_equals': 'toHaveText',
    'value_equals': 'toHaveValue',
}

_VALUE_ASSERTS = ('text_contains', 'text_equals', 'value_equals')


def js(value):
    """渲染成 JS 字面量。中文保留原样，引号用双引号并转义。"""
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float)):
        return json.dumps(value)
    if value is None:
        return 'null'
    return json.dumps(str(value), ensure_ascii=False)


def locator_expr(target):
    """target → `page.getByRole(...)` 这样的表达式。拿不到就返回 None。"""
    if not target:
        return None
    primary = target.get('primary') or {}
    spec = primary.get('spec') or {}
    chain = spec.get('chain')
    if chain:
        rendered = render_chain(chain)
        return 'page.' + rendered if rendered else None
    method = spec.get('method')
    if method:
        rendered = render_pw(method, spec.get('args') or [], spec.get('kwargs') or {})
        if rendered:
            return 'page.' + rendered
    # 旧的录制数据里 primary 可能只有 desc 没有 spec：那就退到第一条能渲染的候选，
    # 而不是整步放弃——放弃会让后面的步骤全部错位到另一个页面状态上。
    for item in target.get('candidates') or []:
        item_spec = (item or {}).get('spec') or {}
        method = item_spec.get('method')
        if not method:
            continue
        rendered = render_pw(method, item_spec.get('args') or [], item_spec.get('kwargs') or {})
        if rendered:
            return 'page.' + rendered
    return None


def _timeout_ms(step):
    intent = step.get('intent') or {}
    seconds = intent.get('timeout')
    if seconds:
        try:
            return int(float(seconds) * 1000)
        except (TypeError, ValueError):
            return None
    return None


def _assert_line(step):
    intent = step.get('intent') or {}
    kind = intent.get('type')
    value = intent.get('value')
    timeout = _timeout_ms(step)
    opts = ' { timeout: %d }' % timeout if timeout else ''

    if kind == 'page_ready':
        state = intent.get('state') or 'load'
        return ["  await page.waitForLoadState('%s')" % state]
    if kind == 'url_contains':
        return ['  await expect(page).toHaveURL(new RegExp(%s))' % js(value)]
    if kind == 'page_text_contains':
        return ['  await expect(page.locator(%s)).toContainText(%s%s)'
                % (js('body'), js(value), opts)]
    if kind == 'sleep':
        return ['  await page.waitForTimeout(%d)' % int(float(intent.get('seconds') or 0) * 1000)]

    expr = locator_expr(step.get('target'))
    if not expr:
        return ['  // 无法投影的断言（没有可用定位）：%s' % (kind or '未知')]
    method = _ASSERT_METHOD.get(kind)
    if not method:
        return ['  // 无法投影的断言意图：%s' % (kind or '未知')]
    if kind in _VALUE_ASSERTS:
        return ['  await expect(%s).%s(%s%s)' % (expr, method, js(value), opts)]
    return ['  await expect(%s).%s(%s)' % (expr, method, opts)]


def _step_lines(step, unsupported):
    kind = step.get('type')
    value = step.get('value')

    if kind == 'goto':
        return ['  await page.goto(%s, { waitUntil: \'domcontentloaded\' })' % js(step.get('url'))]
    if kind == 'reload':
        return ['  await page.reload({ waitUntil: \'domcontentloaded\' })']
    if kind == 'close':
        return ['  await page.close()']
    if kind == 'sleep':
        intent = step.get('intent') or {}
        return ['  await page.waitForTimeout(%d)' % int(float(intent.get('seconds') or 0) * 1000)]
    if kind == 'dialog':
        # 统一 accept：具体是 accept 还是 dismiss 由开头注册的处理器决定，
        # 这里只需要把事件循环转一下，让弹窗回调落地。
        return ['  await page.waitForTimeout(200)']
    if kind == 'new_page':
        unsupported.append('第 %s 步是「新开标签页」，投影不支持多标签页' % step.get('no'))
        return ['  // 新开标签页：投影不支持']
    if kind == 'slider':
        # 投影不执行滑块：它要调视觉模型，沙箱里没有那份配置。写成空操作而不是
        # 写一个不存在的 solveSlider 调用——后者会让 healer 去「修」一个假报错。
        unsupported.append('第 %s 步是滑块验证，投影只占位不执行（需要视觉模型）' % step.get('no'))
        return ['  // 滑块验证：投影不执行，回放时由视觉求解器完成']
    if kind == 'captcha':
        # 同理不投影：沙箱里没有视觉模型的配置，写一个不存在的 solveCaptcha
        # 只会让 healer 去「修」一个假报错。
        unsupported.append('第 %s 步是验证码识别，投影只占位不执行（需要视觉模型）' % step.get('no'))
        return ['  // 验证码识别：投影不执行，回放时由视觉求解器完成']
    if kind in ('assert', 'wait'):
        return _assert_line(step)

    expr = locator_expr(step.get('target'))
    if not expr:
        unsupported.append('第 %s 步（%s）没有可用定位' % (step.get('no'), kind))
        return ['  // 第 %s 步（%s）没有可用定位，跳过' % (step.get('no'), kind)]

    if kind == 'click':
        return ['  await %s.click()' % expr]
    if kind == 'fill':
        return ['  await %s.fill(%s)' % (expr, js(value or ''))]
    if kind == 'select':
        return ['  await %s.selectOption(%s)' % (expr, js(value))]
    if kind == 'press':
        return ['  await %s.press(%s)' % (expr, js(value))]
    unsupported.append('第 %s 步类型 %s 不能投影' % (step.get('no'), kind))
    return ['  // 不支持的步骤类型：%s' % kind]


def render(steps, title='AutoCase 录制回放', upto=None):
    """把步骤渲染成一份 spec。

    `upto` 是「渲染到第几步为止」（0 基下标，含）。自愈时只渲染到失败那一步：
    后面的步骤依赖失败之后的状态，硬塞进去只会制造一批二次失败，把真正的错误淹掉。

    返回 `{'code': str, 'unsupported': [str], 'errors': [str]}`。
    errors 是投影本身的硬伤（比如一条定位都拿不到），调用方据此决定要不要放弃自愈。
    """
    steps = list(steps or [])
    if upto is not None:
        steps = steps[:upto + 1]

    body = []
    unsupported = []
    for step in steps:
        body.extend(_step_lines(step, unsupported))

    dialog_accepts = any((s.get('type') == 'dialog'
                          and (s.get('value') or 'accept') == 'accept') for s in steps)
    dialog_dismiss = any((s.get('type') == 'dialog'
                          and (s.get('value') or '') == 'dismiss') for s in steps)

    head = [
        '// 由 AutoCase 自动生成：录制步骤的可执行投影，仅供 AI 自愈验证使用。',
        '// 和回放共用同一份定位规格；改这里的定位等于改回放行为，请同步。',
        'import { test, expect } from \'@playwright/test\'',
        '',
        'test(%s, async ({ page }) => {' % js(title),
    ]
    if dialog_accepts or dialog_dismiss:
        action = 'dismiss' if dialog_dismiss and not dialog_accepts else 'accept'
        head.append("  page.on('dialog', (dialog) => dialog.%s())" % action)

    tail = ['})', '']
    code = '\n'.join(head + body + tail)
    return {'code': code, 'unsupported': unsupported, 'errors': []}
