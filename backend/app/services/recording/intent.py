"""断言 / 等待的自然语言 → 固定 schema 意图。

模型调用由调用方注入（ask_model），这里只负责构造提示词、解析 JSON、校验字段。
校验失败直接抛错，不做静默兜底。
"""
import json
import re

from app.services.recording.ir import (CELL_MATCHES, CONDITION_INTENT_TYPES, DEFAULT_READY_SECONDS,
                                       INTENT_TYPES, PAGE_READY, POLL_INTENT_TYPES, READY_STATES,
                                       ROW_SCOPES)

# 需要目标元素的意图。url_contains / page_text_contains 不需要目标。sleep 只需要秒数。
NEEDS_TARGET = {'visible', 'hidden', 'text_contains', 'text_equals', 'value_equals', 'enabled',
                'wait_visible', 'wait_hidden'}
NEEDS_VALUE = {'text_contains', 'text_equals', 'value_equals', 'url_contains', 'page_text_contains'}
MAX_SLEEP_SECONDS = 60
# 轮询等待的上限。设备重启这类场景常见 300~600 秒，留到 1 小时。
MAX_WAIT_SECONDS = 3600
MIN_POLL_INTERVAL = 1
MAX_POLL_INTERVAL = 300
DEFAULT_POLL_INTERVAL = 5
POLL_KEYS = ('timeout', 'interval', 'reload')

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
    'wait_visible': '（已废弃，用 visible + timeout 代替）',
    'wait_hidden': '（已废弃，用 hidden + timeout 代替）',
    'sleep': '固定等待 seconds 秒',
}

POLL_FIELD_HINT = (
    'timeout：最长等待秒数，可选。不填表示「此刻必须成立」，填了表示「最多等到几秒后成立」；'
    'reload：true 表示等待期间反复刷新页面；'
    'interval：每隔几秒刷新一次，只在 reload 为 true 时有意义；'
    '这三个字段对除 sleep 外的所有类型都有效。'
    '描述里出现「最多等 360 秒」「每 5 秒刷新一次」时，要分别写进 timeout 和 interval，并把 reload 置为 true；'
    '只说「等到某元素出现」不提刷新时，给 timeout、reload 写 false。'
    '不要再输出 wait_visible / wait_hidden，它们已合并进 visible / hidden + timeout。'
)


SYSTEM_PROMPT = (
    '你是一个只做格式转换的助手。把用户给出的中文断言或等待描述转换为一个 JSON 对象。'
    '只输出 JSON，不调用任何工具，不解释，不加代码块以外的文字。'
)


class IntentError(ValueError):
    pass


def build_prompt(text, aliases):
    """aliases: 已录入的目标别名列表。模型只能从中选择，不能编造。"""
    alias_lines = '\n'.join('- %s' % a for a in aliases) or '- （暂无已录元素）'
    type_lines = '\n'.join('- %s：%s' % (kind, TYPE_HINTS.get(kind, '')) for kind in CONDITION_INTENT_TYPES)
    return (
        '把下面的中文描述转换为一个 JSON 对象，只输出 JSON，不要解释。\n'
        '可用的 type 只有：' + ', '.join(CONDITION_INTENT_TYPES) + '\n'
        '各类型的含义：\n%s\n'
        '字段：type（必填）、target（已录元素别名，只能从下面的别名里选；不需要 target 的类型必须省略或写 null）、'
        'value（文字或值，text_contains / text_equals / value_equals / url_contains / page_text_contains 必填）、'
        'seconds（仅 sleep 使用，0 到 %d 之间的数字）。\n'
        '%s\n'
        '描述里说的是「页面上出现/包含某段文字」而不是某个具体元素时，用 page_text_contains，不要硬选一个别名。\n'
        '已录元素别名：\n%s\n'
        '描述：%s\n' % (type_lines, MAX_SLEEP_SECONDS, POLL_FIELD_HINT, alias_lines, text)
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


MAX_COLUMNS = 8
MAX_SELECTOR = 600
MAX_CELL_VALUE = 200
# 自定义行号上限：一页几百行已经超出正常用法，再大基本是把总行数填进来了。
MAX_SCOPE_ROW = 2000


def scope_rows(raw):
    """把「第几行」规范成 '2' 或 '2-5'；认不出来返回空串。

    只认数字（允许「第」「行」和 ~ ～ - 至 这类连接词）。「倒数第二行」「最后一行」
    不解析——那不是行号，猜错了会静默断到别的行上，不如让用户自己数清楚。
    """
    text = re.sub(r'\s+', '', str(raw or ''))
    text = text.replace('～', '-').replace('~', '-').replace('—', '-').replace('－', '-')
    text = text.replace('至', '-').replace('到', '-')
    text = text.replace('第', '').replace('行', '')
    m = re.match(r'^(\d{1,4})(?:-(\d{1,4}))?$', text)
    if not m:
        return ''
    start = int(m.group(1))
    end = int(m.group(2)) if m.group(2) else start
    if start < 1 or end < start or end > MAX_SCOPE_ROW:
        return ''
    return str(start) if start == end else '%d-%d' % (start, end)


def _row_match_fields(obj):
    """校验「表格行断言」的表格与列条件。

    条件是「同一行里这些列同时成立」，所以每一列都必须有位置。位置存两份：
    表头文字（运行时优先按它找列）和录制时的列序号（表头找不到时兜底）。
    """
    table = obj.get('table')
    if not isinstance(table, dict):
        raise IntentError('表格行断言需要表格信息，请先拾取表格里的单元格')
    rows = str(table.get('rows_selector') or '').strip()
    if not rows:
        raise IntentError('缺少表格行定位，请重新拾取表格')
    if len(rows) > MAX_SELECTOR:
        raise IntentError('表格行定位过长，请重新拾取表格')
    try:
        page_index = int(table.get('page_index') or 0)
    except (TypeError, ValueError):
        page_index = 0
    headers = table.get('headers') or []
    if not isinstance(headers, list):
        headers = []
    table_out = {
        'rows_selector': rows,
        'header_selector': str(table.get('header_selector') or '').strip()[:MAX_SELECTOR],
        'headers': [str(h).strip()[:60] for h in headers if str(h).strip()][:40],
        # alias 用来在「验证当前页面」时回到录制那一刻的 frame；表格在 iframe 里时
        # 没有它就只能拿当前页猜，会读错文档。
        'alias': str(table.get('alias') or '')[:80],
        'route': str(table.get('route') or '')[:200],
        'page_index': page_index,
    }

    raw_columns = obj.get('columns')
    if not isinstance(raw_columns, list) or not raw_columns:
        raise IntentError('至少填一列条件')
    if len(raw_columns) > MAX_COLUMNS:
        raise IntentError('一条断言最多 %d 列条件' % MAX_COLUMNS)
    columns = []
    for raw in raw_columns:
        if not isinstance(raw, dict):
            raise IntentError('列条件格式不对')
        value = str(raw.get('value') or '').strip()
        if not value:
            raise IntentError('每列都要填期望值')
        if len(value) > MAX_CELL_VALUE:
            raise IntentError('期望值太长（最多 %d 字）' % MAX_CELL_VALUE)
        try:
            index = int(raw.get('index'))
        except (TypeError, ValueError):
            index = -1
        if index < 0:
            raise IntentError('列条件缺少列位置，请重新拾取表格')
        # 面板只产 contains；值里带 * / ? 就是通配。存下来是为了生成端和回放端
        # 不必各自再猜一遍（也方便以后加 equals）。
        op = raw.get('op')
        if op not in CELL_MATCHES:
            op = 'glob' if ('*' in value or '?' in value) else 'contains'
        columns.append({
            'header': str(raw.get('header') or '').strip()[:60],
            'index': index,
            'value': value,
            'op': op,
        })

    scope = obj.get('scope') or 'any'
    if scope not in ROW_SCOPES:
        raise IntentError('行范围只能是 %s' % ' / '.join(ROW_SCOPES))
    spec = ''
    if scope == 'custom':
        spec = scope_rows(obj.get('scope_rows') or obj.get('scopeRows'))
        if not spec:
            raise IntentError('自定义行范围请填「第几行」，例如 2 或 2-5（最多 %d 行）'
                              % MAX_SCOPE_ROW)
    return {'table': table_out, 'columns': columns, 'scope': scope,
            'scope_rows': spec or None}


def validate(obj, aliases):
    """校验意图对象，返回规范化后的意图。字段不合法时抛 IntentError。"""
    if not isinstance(obj, dict):
        raise IntentError('意图必须是对象')
    kind = obj.get('type')
    if kind not in INTENT_TYPES + (PAGE_READY,):
        raise IntentError('未知的意图类型：%r' % kind)

    intent = {'type': kind, 'target': None, 'value': None, 'seconds': None,
              'timeout': None, 'interval': None, 'reload': False,
              # 只有 row_match 会用；其余类型保持 null，前端按类型判断，不需要额外分支。
              'table': None, 'columns': None, 'scope': None, 'scope_rows': None}

    if kind == 'row_match':
        intent.update(_row_match_fields(obj))
        intent.update(_poll_fields(obj))
        return intent

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

    if kind == PAGE_READY:
        state = obj.get('state') or 'load'
        if state not in READY_STATES:
            raise IntentError('等待加载的状态只能是 %s' % ' / '.join(READY_STATES))
        intent['state'] = state
        # 只认超时，不认「等不到就刷新」：刷新会把这个文档的加载状态重置掉，
        # 越刷越等不到，语义上也不成立。
        intent.update(_poll_fields({k: v for k, v in obj.items() if k != 'reload'}))
        if not intent['timeout']:
            intent['timeout'] = DEFAULT_READY_SECONDS
        return intent

    # 除 sleep 外都能带「最长等待」：填了就从立即断言变成轮询等待，不用选两次。
    if kind != 'sleep':
        intent.update(_poll_fields(obj))

    return intent


def apply_poll(intent, changes):
    """就地改「最长等待 / 刷新间隔」，返回新的 intent。

    changes 里出现的键才覆盖，没出现的保持原值；timeout 显式给 null 表示改回立即断言。
    校验仍然走 _poll_fields，避免编辑入口和插入入口出现两套规则。
    """
    merged = dict(intent or {})
    for key in POLL_KEYS:
        if key in changes:
            merged[key] = changes[key]
    merged.update(_poll_fields(merged))
    return merged


def _number(raw, what):
    try:
        return float(raw)
    except (TypeError, ValueError):
        raise IntentError('%s必须是秒数' % what)


def _poll_fields(obj):
    """轮询等待的三个可选参数。

    timeout 缺省表示「沿用框架默认超时」（PW/RF 都约 5 秒），保持老行为不变；
    但勾了 reload 就必须显式给 timeout —— 每 5 秒刷一次却只等 5 秒，等于没等。
    """
    out = {'timeout': None, 'interval': None, 'reload': bool(obj.get('reload'))}
    raw_timeout = obj.get('timeout')
    if raw_timeout in (None, ''):
        if out['reload']:
            raise IntentError('勾了「等不到就刷新页面」就必须填写最长等待秒数')
        return out
    timeout = _number(raw_timeout, '最长等待')
    # 模型有时用 0 表达「不等待」。这和没填是同一个意思，不该变成一个莫名其妙的报错；
    # 但勾了「等不到就刷新」时 0 秒仍然不成立，按缺省处理继续报错。
    if timeout == 0:
        if out['reload']:
            raise IntentError('勾了「等不到就刷新页面」就必须填写最长等待秒数')
        return out
    if not 0 < timeout <= MAX_WAIT_SECONDS:
        raise IntentError('最长等待需在 0 到 %d 秒之间' % MAX_WAIT_SECONDS)
    out['timeout'] = timeout
    if not out['reload']:
        return out
    raw_interval = obj.get('interval')
    interval = DEFAULT_POLL_INTERVAL if raw_interval in (None, '') else _number(raw_interval, '刷新间隔')
    if not MIN_POLL_INTERVAL <= interval <= MAX_POLL_INTERVAL:
        raise IntentError('刷新间隔需在 %d 到 %d 秒之间' % (MIN_POLL_INTERVAL, MAX_POLL_INTERVAL))
    if interval > timeout:
        raise IntentError('刷新间隔不能大于最长等待')
    out['interval'] = interval
    return out


def parse_intent(text, aliases, ask_model):
    """ask_model(system, prompt) -> str，由调用方提供（所选 OpenCode 模型）。"""
    if not (text or '').strip():
        raise IntentError('描述不能为空')
    raw = ask_model(SYSTEM_PROMPT, build_prompt(text.strip(), aliases))
    return validate(_extract_json(raw), aliases)
