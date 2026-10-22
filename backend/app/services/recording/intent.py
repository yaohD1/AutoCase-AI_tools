"""断言 / 等待的自然语言 → 固定 schema 意图。

模型调用由调用方注入（ask_model），这里只负责构造提示词、解析 JSON、校验字段。
校验失败直接抛错，不做静默兜底。
"""
import json
import re

from app.services.recording.ir import INTENT_TYPES

# 需要目标元素的意图。url_contains / page_text_contains 不需要目标。sleep 只需要秒数。
NEEDS_TARGET = {'visible', 'hidden', 'text_contains', 'text_equals', 'value_equals', 'enabled',
                'wait_visible', 'wait_hidden'}
NEEDS_VALUE = {'text_contains', 'text_equals', 'value_equals', 'url_contains', 'page_text_contains'}
MAX_SLEEP_SECONDS = 60

# 给模型看的一句话说明：哪些要目标元素、哪些只看整页/地址。
# 之前的提示词只列了类型名，模型只能靠猜，常常把「页面提示出现」解析成需要选元素的 text_contains。
TYPE_HINTS = {
    'visible': '某个元素可见（需要 target）',
    'hidden': '某个元素不存在或不可见（需要 target）',
    'text_contains': '某个元素的文字包含 value（需要 target）',
    'text_equals': '某个元素的文字等于 value（需要 target）',
    'value_equals': '输入框的值等于 value（需要 target）',
    'enabled': '某个元素可用、未被禁用（需要 target）',
    'url_contains': '当前地址包含 value（不需要 target）',
    'page_text_contains': '整个页面的文字里包含 value（不需要 target）',
    'wait_visible': '等到某个元素出现（需要 target）',
    'wait_hidden': '等到某个元素消失（需要 target）',
    'sleep': '固定等待 seconds 秒',
}


SYSTEM_PROMPT = (
    '你是一个只做格式转换的助手。把用户给出的中文断言或等待描述转换为一个 JSON 对象。'
    '只输出 JSON，不调用任何工具，不解释，不加代码块以外的文字。'
)


class IntentError(ValueError):
    pass


def build_prompt(text, aliases):
    """aliases: 已录入的目标别名列表。模型只能从中选择，不能编造。"""
    alias_lines = '\n'.join('- %s' % a for a in aliases) or '- （暂无已录元素）'
    type_lines = '\n'.join('- %s：%s' % (kind, TYPE_HINTS.get(kind, '')) for kind in INTENT_TYPES)
    return (
        '把下面的中文描述转换为一个 JSON 对象，只输出 JSON，不要解释。\n'
        '可用的 type 只有：' + ', '.join(INTENT_TYPES) + '\n'
        '各类型的含义：\n%s\n'
        '字段：type（必填）、target（已录元素别名，只能从下面的别名里选；不需要 target 的类型必须省略或写 null）、'
        'value（文字或值，text_contains / text_equals / value_equals / url_contains / page_text_contains 必填）、'
        'seconds（仅 sleep 使用，0 到 %d 之间的数字）。\n'
        '描述里说的是「页面上出现/包含某段文字」而不是某个具体元素时，用 page_text_contains，不要硬选一个别名。\n'
        '已录元素别名：\n%s\n'
        '描述：%s\n' % (type_lines, MAX_SLEEP_SECONDS, alias_lines, text)
    )


def _extract_json(raw):
    raw = (raw or '').strip()
    fence = re.search(r'```(?:json)?\s*(\{.*\})\s*```', raw, re.S)
    if fence:
        raw = fence.group(1)
    start, end = raw.find('{'), raw.rfind('}')
    if start < 0 or end < start:
        raise IntentError('模型没有返回 JSON')
    try:
        return json.loads(raw[start:end + 1])
    except ValueError as exc:
        raise IntentError('模型返回的 JSON 无法解析：%s' % exc)


def validate(obj, aliases):
    """校验意图对象，返回规范化后的意图。字段不合法时抛 IntentError。"""
    if not isinstance(obj, dict):
        raise IntentError('意图必须是对象')
    kind = obj.get('type')
    if kind not in INTENT_TYPES:
        raise IntentError('未知的意图类型：%r' % kind)

    intent = {'type': kind, 'target': None, 'value': None, 'seconds': None}

    if kind in NEEDS_TARGET:
        target = obj.get('target')
        if target not in aliases:
            raise IntentError('目标 %r 不在已录元素中，请从下拉框选择或先拾取' % target)
        intent['target'] = target

    if kind in NEEDS_VALUE:
        value = obj.get('value')
        if not isinstance(value, str) or not value.strip():
            raise IntentError('%s 需要填写文字或值' % kind)
        intent['value'] = value.strip()

    if kind == 'sleep':
        try:
            seconds = float(obj.get('seconds'))
        except (TypeError, ValueError):
            raise IntentError('sleep 需要秒数')
        if not 0 < seconds <= MAX_SLEEP_SECONDS:
            raise IntentError('sleep 秒数需在 0 到 %d 之间' % MAX_SLEEP_SECONDS)
        intent['seconds'] = seconds

    return intent


def parse_intent(text, aliases, ask_model):
    """ask_model(system, prompt) -> str，由调用方提供（所选 OpenCode 模型）。"""
    if not (text or '').strip():
        raise IntentError('描述不能为空')
    raw = ask_model(SYSTEM_PROMPT, build_prompt(text.strip(), aliases))
    return validate(_extract_json(raw), aliases)
