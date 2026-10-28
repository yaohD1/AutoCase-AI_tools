"""插入断言前的「当前页面状态」检查。

回答的是一个很窄的问题：这条断言，此刻在这个页面上成不成立？
不等待、不重跑流程、不生成代码——所以它不能替代回放校验：

- 断言插在「点击提交」之后时，当前页面还没提交，现在不成立是正常的；
- 反之现在成立，也不代表流程走到那里时仍然成立（例如提示语已经消失）。

正因为口径是「此刻」，结果里必须带上实际看到的值，让用户自己判断有没有意义。
"""
from app.services.recording.locators import best_effort, build_locator, pick_for

PW_FRAMEWORK = 'playwright-ts'
MAX_ACTUAL = 200
# 读文字/状态都是瞬时动作，给个短超时防止被挂在未加载完的元素上。
READ_TIMEOUT_MS = 2000
# innerText 对 input/textarea 是空的，断言「文字包含」时用户想看的往往是输入框里的值。
JS_VISIBLE_TEXT = (
    "el => (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') "
    "? (el.value || '') : (el.innerText || el.textContent || '')"
)
JS_BODY_TEXT = "() => (document.body ? (document.body.innerText || '') : '')"


class VerifyError(ValueError):
    """验证无法给出结论（元素定位不可用、页面读不到等），可向用户展示。"""


def _norm(text):
    return ' '.join(str(text or '').split())


def _clip(text):
    text = _norm(text)
    return text if len(text) <= MAX_ACTUAL else text[:MAX_ACTUAL] + '…'


def _locator(frame, target):
    """在当前页面上还原这个目标。和生成同口径：优先唯一候选，否则最窄候选 + 序号。"""
    candidates = target.get('candidates') or []
    chosen = pick_for(PW_FRAMEWORK, candidates) or best_effort(PW_FRAMEWORK, candidates)
    if not chosen:
        raise VerifyError('这个元素当前在页面上找不到（所有候选都匹配 0 个）')
    spec = chosen.get('spec') or {}
    try:
        locator = build_locator(frame, spec)
    except Exception as exc:
        raise VerifyError('无法按这条定位查找元素：%s' % exc)
    index = chosen.get('index')
    if index is not None and (chosen.get('count') or 1) > 1:
        locator = locator.nth(index)
    return locator, chosen


def _result(kind, ok, actual, expected=None, detail=None, chosen=None, matches=None):
    out = {
        'ok': bool(ok),
        'kind': kind,
        'expected': expected,
        'actual': _clip(actual) if actual is not None else None,
        'detail': detail,
    }
    if chosen:
        out['locator'] = chosen['desc']
        out['matches'] = chosen.get('count')
        out['nth'] = (chosen.get('index') + 1) if chosen.get('index') is not None else None
    if matches is not None:
        out['matches'] = matches
    return out


def check(frame, target, intent):
    """在当前 frame 上判断一条断言，返回 {'ok', 'kind', 'expected', 'actual', 'detail', ...}。

    ok 只代表「此刻成立」，语义由调用方在界面里说明清楚。

    轮询等待例外：它等的是「之后」的状态，此刻不成立完全是正常的，
    所以额外带一个 poll 字段，前端据此换掉红色结论和提示语。
    """
    result = _check(frame, target, intent)
    if intent.get('timeout'):
        result['poll'] = {
            'timeout': intent.get('timeout'),
            'interval': intent.get('interval'),
            'reload': bool(intent.get('reload')),
        }
        if not result.get('ok'):
            result['detail'] = '这是轮询等待，等的是之后的页面状态，此刻不成立属正常'
    return result


def _check(frame, target, intent):
    if frame is None:
        raise VerifyError('没有可用的页面')
    kind = intent.get('type')
    value = intent.get('value')
    try:
        if kind == 'page_text_contains':
            text = _norm(frame.evaluate(JS_BODY_TEXT))
            return _result(kind, value in text, text, expected=value,
                           detail=None if value in text else '当前页面文字里没有这段内容')
        if kind == 'url_contains':
            url = frame.url or ''
            return _result(kind, value in url, url, expected=value)
        if kind in ('sleep',):
            return _result(kind, True, None, detail='固定等待不需要校验')
        if target is None:
            raise VerifyError('这一步需要目标元素')
        locator, chosen = _locator(frame, target)
        total = locator.count()
        if total == 0:
            return _result(kind, False, None, expected=value, chosen=chosen, matches=0,
                           detail='当前页面上匹配不到这个元素')
        return _check_with_locator(kind, value, locator, chosen)
    except VerifyError:
        raise
    except Exception as exc:
        raise VerifyError('读取当前页面失败：%s' % exc)


def _check_with_locator(kind, value, locator, chosen):
    if kind in ('visible', 'wait_visible'):
        visible = locator.is_visible()
        return _result(kind, visible, '可见' if visible else '不可见', chosen=chosen)
    if kind in ('hidden', 'wait_hidden'):
        visible = locator.is_visible()
        return _result(kind, not visible, '可见' if visible else '不可见', chosen=chosen)
    if kind == 'enabled':
        enabled = locator.is_enabled()
        return _result(kind, enabled, '可用' if enabled else '不可用', chosen=chosen)
    actual = _norm(locator.evaluate(JS_VISIBLE_TEXT, timeout=READ_TIMEOUT_MS))
    if kind == 'text_contains':
        return _result(kind, value in actual, actual, expected=value, chosen=chosen)
    if kind == 'text_equals':
        return _result(kind, _norm(value) == actual, actual, expected=value, chosen=chosen)
    if kind == 'value_equals':
        raw = locator.input_value(timeout=READ_TIMEOUT_MS)
        return _result(kind, _norm(raw) == _norm(value), raw, expected=value, chosen=chosen)
    raise VerifyError('未知的断言意图：%s' % kind)


def describe(result):
    """给日志/测试用的一句话结论。"""
    if result.get('ok'):
        return '当前页面状态：成立'
    return '当前页面状态：不成立（%s）' % (result.get('detail') or '')
