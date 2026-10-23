"""录制结果的中间表示（IR）。

框架无关：只描述用户做了什么、目标是什么、用哪种定位。代码由后续的 adapter 生成。
"""

STEP_TYPES = ('goto', 'reload', 'click', 'fill', 'select', 'press', 'dialog',
              'new_page', 'assert', 'wait', 'sleep')

# 断言 / 等待的意图类型，固定 schema。代码片段由生成 agent 按框架写。
INTENT_TYPES = (
    'visible', 'hidden', 'text_contains', 'text_equals', 'value_equals', 'enabled',
    'url_contains', 'page_text_contains', 'wait_visible', 'wait_hidden', 'sleep',
)

# 轮询等待：带「最长等待」的条件判断。
# 和立即断言的区别只有一条：暂时不成立在预期内，到点还没成立才算失败。
# 设备重启、异步任务、报表生成这类场景都属于它，用 sleep 硬等是错的：既慢又不可靠。
# wait_visible / wait_hidden 是合并前的老写法，只为读旧数据保留，前端不再产出。
POLL_INTENT_TYPES = ('wait_visible', 'wait_hidden')

# 给用户和模型看的类型集合：等待不再是独立类型，而是任意条件 + timeout。
CONDITION_INTENT_TYPES = tuple(t for t in INTENT_TYPES if t not in POLL_INTENT_TYPES)


def step_type_for_intent(kind, intent=None):
    """断言 / 等待 / 睡眠三类步骤由意图决定。

    「断言」和「等待」在语义上只差一件事：容不容忍「此刻还没成立」。
    所以不再让用户先选是断言还是等待，而是看有没有给最长等待：
    给了就是 wait（生成端据此写等待 / 轮询代码），没给就是立即断言的 assert。
    """
    if kind == 'sleep':
        return 'sleep'
    if kind in ('wait_visible', 'wait_hidden'):
        return 'wait'
    if intent and intent.get('timeout'):
        return 'wait'
    return 'assert'


# 别名要显示在下拉里，也要写进给模型的提示词，太长会挤掉真正有区分度的部分。
ALIAS_MAX_LENGTH = 40


def make_target(snap, candidates, primary, status):
    """目标元素。alias 用于在模板里引用这个元素。"""
    alias = snap.get('name') or snap.get('label') or snap.get('placeholder') or snap.get('text') or snap.get('tag')
    return {
        'alias': (alias or '')[:ALIAS_MAX_LENGTH],
        'snap': snap,
        'candidates': candidates,
        'primary': primary,
        'status': status,
    }


def make_step(step_type, no, target=None, value=None, label=None, url=None, page_index=0,
              source='record', dialog=None):
    """一步操作。source 为 record（录制得到）或 manual（用户在工具栏添加）。

    press 用 value 存键名（Playwright 写法，如 Enter / Control+Enter）；
    dialog 用 dialog 存 {'kind', 'message', 'action'}，value 另存一遍 action 便于前端直接读。
    """
    if step_type not in STEP_TYPES:
        raise ValueError('unknown step type: %s' % step_type)
    return {
        'no': no,
        'type': step_type,
        'source': source,
        'target': target,
        'value': value,
        'label': label,
        'url': url,
        'page_index': page_index,
        'intent': None,
        'text': None,
        'dialog': dialog,
    }


def replay_view(step):
    """完整步骤：去掉内部引用（以下划线开头的字段），保留定位 spec 与 frame_url。"""
    return {k: v for k, v in step.items() if not k.startswith('_')}


def public_step(step):
    """返回给前端的步骤视图。去掉内部字段（frame 引用等）。"""
    out = {k: v for k, v in step.items() if not k.startswith('_')}
    if step.get('target'):
        out['target'] = dict(step['target'])
        # index：不唯一时用户点的是第几个匹配（降级定位要用），前端据此显示「将取第 2 个」。
        out['target']['candidates'] = [
            {'kind': c['kind'], 'desc': c['desc'], 'count': c['count'], 'index': c.get('index')}
            for c in step['target']['candidates']
        ]
        if step['target'].get('primary'):
            out['target']['primary'] = {
                'kind': step['target']['primary']['kind'],
                'desc': step['target']['primary']['desc'],
                'count': step['target']['primary']['count'],
                'index': step['target']['primary'].get('index'),
            }
    return out
