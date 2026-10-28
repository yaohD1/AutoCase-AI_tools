"""按 IR 在浏览器中重放一遍，验证录制结果与断言意图是否成立。

只用于验收和预览，不生成代码、不写文件。任一步失败即抛出 ReplayError，并带上步骤号。

回放校验的是「这份录制在真实页面上还能不能走通」，也就是定位有没有失效，
而不是「生成的脚本写得对不对」。所以两个框架都能校验：RF 的 rf_* 候选本来就是
用 Playwright 的 xpath= / css= 在浏览器里数出来的，换一套候选即可，不需要 Selenium。
"""
import time

from playwright.sync_api import expect, sync_playwright

from app.services.recording.locators import best_effort, build_locator, pick_for

ASSERT_TIMEOUT_MS = 5000
# 刷新轮询里单次判断的窗口：只等渲染，不等结果。结果要靠下一轮刷新。
POLL_CHECK_MS = 1000
PW_FRAMEWORK = 'playwright-ts'
RF_FRAMEWORK = 'robot-selenium'


class ReplayError(Exception):
    def __init__(self, message, step_no=None, step_type=None):
        super().__init__(message)
        self.step_no = step_no
        self.step_type = step_type


def _page_for(context, step):
    # 每一步都记录了它发生在哪个标签页（page_index），不能简单使用“最新打开的页面”。
    pages = context.pages
    index = step.get('page_index') or 0
    return pages[index] if index < len(pages) else pages[-1]


def _frame_of(page, snap):
    """按录制时记下的 url / name 找 iframe。

    老数据只有 frame_url；新数据还带 frame_name，url 匹配不上时用它兜底
    （SPA 换路由、带临时 query 都会让 url 全等匹配失效）。
    """
    frame_url = snap.get('frame_url')
    frame_name = snap.get('frame_name')
    for frame in page.frames:
        if frame_url and frame.url == frame_url:
            return frame
    if frame_name:
        return next((f for f in page.frames if f.name == frame_name), None)
    return None


def choose_locator(target, framework=PW_FRAMEWORK):
    """按框架挑回放要用的候选。

    Playwright 用录制时判定的主定位；Robot 必须换成 RF 组的候选 —— 两组是不同写法，
    PW 的 getByRole 在 SeleniumLibrary 里没有对应物，拿主定位去回放 RF 等于没验证。
    都不唯一时和生成保持一致：降级到最窄候选 + 录制时的序号。
    """
    candidates = target.get('candidates') or []
    if framework == RF_FRAMEWORK:
        chosen = pick_for(framework, candidates) or best_effort(framework, candidates)
    else:
        chosen = target.get('primary') or best_effort(framework, candidates)
    # 老数据可能没有 candidates（或 RF 组为空），退回主定位，至少有东西可试。
    return chosen or target.get('primary')


def _locator(page, target, framework=PW_FRAMEWORK):
    """返回 (locator, 选中的候选)。候选一并返回，回放报告要知道用的是哪条、命中几个。"""
    if not target:
        raise ReplayError('这一步没有目标元素')
    snap = target.get('snap') or {}
    root = page
    if snap.get('frame_url') or snap.get('frame_name'):
        root = _frame_of(page, snap)
        if root is None:
            raise ReplayError('找不到目标 iframe：%s' % (snap.get('frame_name') or snap.get('frame_url')))
    chosen = choose_locator(target, framework)
    if not chosen:
        raise ReplayError('目标没有可用的定位')
    spec = chosen['spec']
    locator = build_locator(root, spec)
    index = chosen.get('index')
    if index is not None and chosen.get('count', 1) > 1:
        locator = locator.nth(index)
    return locator, chosen


def _assert(intent, target, page, framework=PW_FRAMEWORK):
    """执行一条断言/等待，返回 (选中的候选, 轮询信息或 None)。

    分两种口径：
    - 不刷新：交给 Playwright 自己的等待，一次调用等到超时，行为最接近生成的脚本；
    - 刷新轮询：Playwright 没有「刷到满足条件为止」的原生 API，得自己循环。
      单次判断只给 1 秒窗口，失败就刷新再来，周期约等于 interval，不会因为
      单次判断吃掉整个超时。
    """
    if intent.get('reload'):
        return _poll_with_reload(intent, target, page, framework)
    timeout_ms = int(float(intent['timeout']) * 1000) if intent.get('timeout') else ASSERT_TIMEOUT_MS
    return _assert_once(intent, target, page, framework, timeout_ms), None


def _poll_with_reload(intent, target, page, framework):
    timeout = float(intent['timeout'])
    interval = float(intent.get('interval') or 5)
    started = time.monotonic()
    deadline = started + timeout
    attempts = 0
    last = None
    while True:
        attempts += 1
        try:
            # reload 之后 DOM 已经就绪，这里只是给渲染留一点余量，不能给太长，
            # 否则「1 秒判断 + 间隔」会让实际周期明显大于用户设的间隔。
            chosen = _assert_once(intent, target, page, framework, POLL_CHECK_MS)
            return chosen, {'attempts': attempts, 'reload': True,
                            'interval': interval, 'elapsed': round(time.monotonic() - started, 1)}
        except Exception as exc:  # noqa: BLE001 - 判断失败就是还没好，继续下一轮
            last = exc
        if time.monotonic() >= deadline:
            raise ReplayError(
                '轮询等待 %g 秒仍未成立（每 %g 秒刷新一次，共尝试 %d 次）：%s'
                % (timeout, interval, attempts, last))
        page.reload(wait_until='domcontentloaded')
        page.wait_for_timeout(interval * 1000)


def _assert_once(intent, target, page, framework, timeout):
    kind = intent['type']
    value = intent.get('value')
    if kind == 'url_contains':
        page.wait_for_url(lambda url: value in url, timeout=timeout)
        return None
    if kind == 'page_text_contains':
        # 整页文字断言没有目标元素：body 的文字里包含即可，和生成时写的 expect(body).toContainText 同口径。
        expect(page.locator('body')).to_contain_text(value, timeout=timeout)
        return None
    loc, chosen = _locator(page, target, framework)
    if kind in ('visible', 'wait_visible'):
        expect(loc).to_be_visible(timeout=timeout)
    elif kind in ('hidden', 'wait_hidden'):
        expect(loc).to_be_hidden(timeout=timeout)
    elif kind == 'enabled':
        expect(loc).to_be_enabled(timeout=timeout)
    elif kind == 'text_contains':
        expect(loc).to_contain_text(value, timeout=timeout)
    elif kind == 'text_equals':
        expect(loc).to_have_text(value, timeout=timeout)
    elif kind == 'value_equals':
        expect(loc).to_have_value(value, timeout=timeout)
    else:
        raise ReplayError('未知的断言意图：%s' % kind)
    return chosen


def _arm_dialog(page, step, probe):
    """在触发弹窗的那一步之前注册处理方式。

    Playwright 一旦有 dialog 监听就不再自动接管，而等走到 dialog 步骤再注册已经晚了：
    页面在弹窗出现的那一刻就挂起了。所以必须提前一步装好。
    """
    action = step.get('value') or 'accept'

    def on_dialog(dialog):
        try:
            probe['seen'].append({'kind': dialog.type, 'message': dialog.message})
        except Exception:
            pass
        try:
            if action == 'dismiss':
                dialog.dismiss()
            else:
                dialog.accept()
        except Exception:
            pass

    probe['page'] = page
    probe['handler'] = on_dialog
    page.on('dialog', on_dialog)
    return probe


def _disarm_dialog(probe):
    """卸掉没用上的监听。

    多余注册的 handler 会替后面的弹窗拿主意（把 Playwright 默认的 auto-dismiss 改成 accept），
    而这一步其实没有弹窗，必须清掉，否则会悄悄改变后续步骤的行为。
    """
    if not probe:
        return
    page, handler = probe.get('page'), probe.get('handler')
    if page is not None and handler is not None:
        try:
            page.remove_listener('dialog', handler)
        except Exception:
            pass


def _run_step(step, page, framework=PW_FRAMEWORK):
    """执行一步，返回 (这一步实际用到的定位候选, 附加信息)。

    附加信息目前只有轮询等待用得上（试了几次、刷了几轮），没有的话是 None。
    """
    kind = step['type']
    if kind == 'goto':
        page.goto(step['url'], wait_until='domcontentloaded')
        return None, None
    if kind == 'reload':
        # 和 goto 不等价：这里要的正是「重新加载当前文档」，而不是重新请求一个地址。
        page.reload(wait_until='domcontentloaded')
        return None, None
    if kind == 'click':
        loc, chosen = _locator(page, step['target'], framework)
        loc.click()
        return chosen, None
    if kind == 'fill':
        loc, chosen = _locator(page, step['target'], framework)
        loc.fill(step.get('value') or '')
        return chosen, None
    if kind == 'select':
        loc, chosen = _locator(page, step['target'], framework)
        loc.select_option(step.get('value'))
        return chosen, None
    if kind == 'press':
        loc, chosen = _locator(page, step['target'], framework)
        loc.press(step.get('value'))
        return chosen, None
    if kind == 'dialog':
        # accept / dismiss 在触发它的前一步之前就注册好了（见 _run）。
        # 这里只把事件循环转一下，让那个回调落地——同步 API 只在调用期间分发事件。
        page.wait_for_timeout(200)
        return None, None
    if kind == 'sleep':
        page.wait_for_timeout(float(step['intent']['seconds']) * 1000)
        return None, None
    if kind in ('assert', 'wait'):
        return _assert(step['intent'], step.get('target'), page, framework)
    raise ReplayError('回放不支持的步骤：%s' % kind)


def _step_label(step):
    """给明细里没有 alias / url 的步骤补一句人话，否则报告里只剩一个破折号。"""
    kind = step.get('type')
    if kind == 'reload':
        return '刷新当前页面'
    if kind == 'press':
        return '按键：%s' % (step.get('value') or '')
    if kind == 'dialog':
        info = step.get('dialog') or {}
        action = '确定' if (info.get('action') or step.get('value')) == 'accept' else '取消'
        message = (info.get('message') or '').strip()
        return '弹窗（%s）：%s' % (action, message[:40] or info.get('kind') or '未捕获')
    return None


def _entry(step, chosen, ok, error=None, poll=None):
    """回放报告里的一行：这一步行不行、用哪条定位、命中几个、取第几个。"""
    target = step.get('target') or {}
    used = None
    if chosen:
        matches = chosen.get('count')
        spec = chosen.get('spec') or {}
        # syntax 是真正会被写进脚本的写法（RF 为 xpath= / css=，PW 为 getByRole(...)），
        # 前端拿它和生成结果对照，避免「回放用的定位」和「生成的定位」各说各话。
        syntax = spec['args'][0] if spec.get('method') == 'locator' and spec.get('args') else chosen['desc']
        used = {
            'kind': chosen['kind'],
            'desc': chosen['desc'],
            'syntax': syntax,
            'matches': matches,
            # 命中多个时才记录取第几个，和生成的语义保持一致（0 基 → 展示 1 基）
            'nth': (chosen.get('index') + 1) if (matches or 0) > 1 and chosen.get('index') is not None else None,
            'degraded': (matches or 0) > 1,
        }
    return {
        'no': step.get('no'),
        'type': step.get('type'),
        'alias': target.get('alias'),
        # goto 没有目标元素，报告里用 URL 代替 alias，否则明细里只剩一个破折号
        'url': step.get('url'),
        # reload 既没有 alias 也没有 url，给它一句说明，别让明细里空着。
        'label': _step_label(step),
        'ok': ok,
        'error': error,
        'used': used,
        # 轮询等待才有的明细：等了几秒、刷新了几轮。失败时靠它区分「设备没起来」和「定位写错了」。
        'poll': poll,
    }


def _run(steps, framework, headless, on_step):
    done = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        try:
            # 和录制用同一套证书策略：内网系统多为自签名证书，校验一开就
            # net::ERR_CERT_AUTHORITY_INVALID，回放连页面都进不去，会被误报成定位失效。
            context = browser.new_context(ignore_https_errors=True)
            context.new_page()
            i = 0
            # 下一步要是弹窗步骤，处理方式必须在当前步执行之前交给浏览器，所以在这里拿着。
            probe = None
            while i < len(steps):
                step = steps[i]
                upcoming = steps[i + 1] if i + 1 < len(steps) else None
                if upcoming and upcoming['type'] == 'dialog' and probe is None:
                    probe = _arm_dialog(_page_for(context, upcoming), upcoming,
                                        {'seen': [], 'no': upcoming.get('no')})
                try:
                    if upcoming and upcoming['type'] == 'new_page':
                        # 录制时这一步打开了新标签页，回放时同样要接住它，并等它加载完。
                        with context.expect_page() as info:
                            chosen, poll = _run_step(step, _page_for(context, step), framework)
                        info.value.wait_for_load_state()
                        if on_step:
                            on_step(_entry(step, chosen, True, poll=poll))
                            on_step(_entry(upcoming, None, True))
                        i += 2
                        done += 2
                        continue
                    chosen, poll = _run_step(step, _page_for(context, step), framework)
                    if step['type'] == 'dialog' and probe and probe.get('no') == step.get('no'):
                        # 弹窗没出现就别装作成功：多半是上一步没点中，或者页面改成了自定义弹层。
                        if not probe['seen']:
                            raise ReplayError('这一步应该有弹窗，但实际没有弹出来（上一步可能没点中）')
                        _disarm_dialog(probe)
                        probe = None
                except Exception as exc:
                    _disarm_dialog(probe)
                    probe = None
                    if on_step:
                        on_step(_entry(step, None, False, str(exc)))
                    raise ReplayError('第 %s 步（%s）失败：%s' % (step.get('no'), step.get('type'), exc),
                                      step_no=step.get('no'), step_type=step.get('type'))
                if on_step:
                    on_step(_entry(step, chosen, True, poll=poll))
                i += 1
                done += 1
        finally:
            browser.close()
    return done


def replay(steps, headless=True, framework=PW_FRAMEWORK):
    """steps 为 RecordingSession.ir() 的结果。返回执行过的步骤数。失败抛 ReplayError。"""
    return _run(steps, framework, headless, None)


def replay_checked(steps, framework=PW_FRAMEWORK, headless=True):
    """回放并把每一步的结果收集成报告。

    失败不抛异常（前端需要展示「前面几步走通了、第 N 步定位失效」），
    返回 {'ok', 'done', 'total', 'steps': [...], 'error', 'failed_no'}。
    """
    trace = []
    payload = {'framework': framework, 'total': len(steps), 'steps': trace}
    try:
        done = _run(steps, framework, headless, trace.append)
    except ReplayError as exc:
        payload.update({'ok': False, 'done': len(trace), 'error': str(exc),
                        'failed_no': exc.step_no, 'failed_type': exc.step_type})
        return payload
    payload.update({'ok': True, 'done': done, 'error': None, 'failed_no': None})
    return payload

