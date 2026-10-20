"""录制结果的中间表示（IR）。

框架无关：只描述用户做了什么、目标是什么、用哪种定位。代码由后续的 adapter 生成。
"""

STEP_TYPES = ('goto', 'click', 'fill', 'select', 'new_page', 'assert', 'wait', 'sleep')

# 断言 / 等待的意图类型，固定 schema。代码片段由生成 agent 按框架写。
INTENT_TYPES = (
    'visible', 'hidden', 'text_contains', 'text_equals', 'value_equals', 'enabled',
    'url_contains', 'wait_visible', 'wait_hidden', 'sleep',
)


def step_type_for_intent(kind):
    """断言 / 等待 / 睡眠三类步骤由意图类型决定。"""
    if kind == 'sleep':
        return 'sleep'
    if kind in ('wait_visible', 'wait_hidden'):
        return 'wait'
    return 'assert'


def make_target(snap, candidates, primary, status):
    """目标元素。alias 用于在模板里引用这个元素。"""
    alias = snap.get('name') or snap.get('label') or snap.get('placeholder') or snap.get('text') or snap.get('tag')
    return {
        'alias': (alias or '')[:40],
        'snap': snap,
        'candidates': candidates,
        'primary': primary,
        'status': status,
    }


def make_step(step_type, no, target=None, value=None, label=None, url=None, page_index=0, source='record'):
    """一步操作。source 为 record（录制得到）或 manual（用户在工具栏添加）。"""
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
    }


def replay_view(step):
    """完整步骤：去掉内部引用（以下划线开头的字段），保留定位 spec 与 frame_url。"""
    return {k: v for k, v in step.items() if not k.startswith('_')}


def public_step(step):
    """返回给前端的步骤视图。去掉内部字段（frame 引用等）。"""
    out = {k: v for k, v in step.items() if not k.startswith('_')}
    if step.get('target'):
        out['target'] = dict(step['target'])
        out['target']['candidates'] = [
            {'kind': c['kind'], 'desc': c['desc'], 'count': c['count']} for c in step['target']['candidates']
        ]
        if step['target'].get('primary'):
            out['target']['primary'] = {
                'kind': step['target']['primary']['kind'],
                'desc': step['target']['primary']['desc'],
                'count': step['target']['primary']['count'],
            }
    return out
