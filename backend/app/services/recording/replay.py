"""按 IR 在浏览器中重放一遍，验证录制结果与断言意图是否成立。

只用于验收和预览，不生成代码、不写文件。任一步失败即抛出 ReplayError，并带上步骤号。
"""
from playwright.sync_api import expect, sync_playwright

ASSERT_TIMEOUT_MS = 5000


class ReplayError(Exception):
    pass


def _page_for(context, step):
    # 每一步都记录了它发生在哪个标签页（page_index），不能简单使用“最新打开的页面”。
    pages = context.pages
    index = step.get('page_index') or 0
    return pages[index] if index < len(pages) else pages[-1]


def _locator(page, target):
    if not target or not target.get('primary'):
        raise ReplayError('目标没有唯一定位')
    root = page
    frame_url = (target.get('snap') or {}).get('frame_url')
    if frame_url:
        root = next((f for f in page.frames if f.url == frame_url), None)
        if root is None:
            raise ReplayError('找不到目标 iframe：%s' % frame_url)
    spec = target['primary']['spec']
    return getattr(root, spec['method'])(*spec['args'], **spec['kwargs'])


def _assert(intent, target, page):
    kind = intent['type']
    value = intent.get('value')
    if kind == 'url_contains':
        page.wait_for_url(lambda url: value in url, timeout=ASSERT_TIMEOUT_MS)
        return
    loc = _locator(page, target)
    timeout = ASSERT_TIMEOUT_MS
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


def _run_step(step, page):
    kind = step['type']
    if kind == 'goto':
        page.goto(step['url'], wait_until='domcontentloaded')
    elif kind == 'click':
        _locator(page, step['target']).click()
    elif kind == 'fill':
        _locator(page, step['target']).fill(step.get('value') or '')
    elif kind == 'select':
        _locator(page, step['target']).select_option(step.get('value'))
    elif kind == 'sleep':
        page.wait_for_timeout(float(step['intent']['seconds']) * 1000)
    elif kind in ('assert', 'wait'):
        _assert(step['intent'], step.get('target'), page)
    else:
        raise ReplayError('回放不支持的步骤：%s' % kind)


def replay(steps, headless=True):
    """steps 为 RecordingSession.ir() 的结果。返回执行过的步骤数。"""
    done = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        try:
            context = browser.new_context()
            context.new_page()
            i = 0
            while i < len(steps):
                step = steps[i]
                upcoming = steps[i + 1] if i + 1 < len(steps) else None
                try:
                    if upcoming and upcoming['type'] == 'new_page':
                        # 录制时这一步打开了新标签页，回放时同样要接住它，并等它加载完。
                        with context.expect_page() as info:
                            _run_step(step, _page_for(context, step))
                        info.value.wait_for_load_state()
                        i += 2
                        done += 2
                        continue
                    _run_step(step, _page_for(context, step))
                except Exception as exc:
                    raise ReplayError('第 %s 步（%s）失败：%s' % (step.get('no'), step.get('type'), exc))
                i += 1
                done += 1
        finally:
            browser.close()
    return done
