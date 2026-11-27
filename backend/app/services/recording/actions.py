"""录制步骤的「具名动作」：把连续几步封装成一个可调用单元。

动作是纯数据：子步骤保留原始定位（target / candidates / primary），只把要参数化的
输入值换成 `{{参数名}}` 占位。封装、展开、解封三件事都是对同一份 dict 的改写，
不碰浏览器，所以全部做成纯函数——录制线程拿到结果后只负责重编号。

之所以要在 steps 里放一个 call 节点而不是「标记一段区间」：
- 同一个动作可能被调用多次，各次参数不同，区间标记表达不了；
- 解封要能原样还原，子步骤必须完整留着，不能靠反推。
"""
import copy
import json
import re
import uuid
from pathlib import Path

LIBRARY_DIR = '.autocase'
LIBRARY_FILE = 'actions.json'
LIBRARY_VERSION = 1
# 一个项目攒到 200 个动作已经说明该拆文件了；再大只是让每次录制都多读几百 KB。
MAX_ACTIONS = 200
MAX_NAME = 40
MAX_DESC = 200
MAX_PARAM_KEY = 32

# 只有 value / label 允许参数化：url 参数化会让「登录后跳哪」变成玄学，
# text 是给人看的说明，替进去反而看不懂。
PARAM_FIELDS = ('value', 'label')
# fill / select 才有「值」。按键、弹窗、断言没有可替换的输入。
PARAM_TYPES = ('fill', 'select')

_PLACEHOLDER = re.compile(r'\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}')
_KEY_BAD = re.compile(r'[^A-Za-z0-9_]+')


class ActionError(ValueError):
    """动作定义不合法（选中区间、参数名、动作不存在）。"""


def new_id():
    return 'a' + uuid.uuid4().hex[:10]


def clean(obj):
    """拷贝并剔掉以下划线开头的内部字段。

    `_frame` 是 Playwright 的 Frame 对象，既不能 JSON 序列化，换一次录制也没有意义
    （页面早就不同了）。加载回来缺这些字段是正常的，回放会按主 frame 走。
    """
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items() if not k.startswith('_')}
    if isinstance(obj, list):
        return [clean(v) for v in obj]
    return obj


def suggest_key(alias, used):
    """按元素别名猜一个参数名；中文别名猜不出来就退回 arg1 / arg2。

    不做拼音转换：项目里没有拼音库，为了一个参数名引一个依赖不值当，
    而且用户改一下比猜错再改更快。
    """
    raw = _KEY_BAD.sub('_', (alias or '').strip()).strip('_')
    if raw and raw[0].isdigit():
        raw = 'arg_' + raw
    raw = raw[:MAX_PARAM_KEY]
    if not raw:
        raw = 'arg'
    key = raw
    n = 2
    while key in used:
        suffix = str(n)
        key = raw[:MAX_PARAM_KEY - len(suffix)] + suffix
        n += 1
    used.add(key)
    return key


def key_hint(step):
    """猜参数名的输入：优先元素快照里的 name / placeholder，它们通常就是代码里的标识符。

    别名多半是中文（「用户名」），拿它转不出合法标识符；快照里的 name="username"
    才是人真正想看到的名字。都取不到就返回空，由 suggest_key 退回 arg。
    """
    target = step.get('target') or {}
    snap = target.get('snap') or {}
    for value in (snap.get('name'), snap.get('placeholder'), snap.get('id'), target.get('alias')):
        raw = _KEY_BAD.sub('_', str(value or '')).strip('_')
        if len(raw) > 1:
            return raw
    return ''


def candidates(steps):
    """列出这组步骤里「可以变成参数」的输入值，给封装弹窗预先勾选。

    同一个值出现两次也各自列一条：它们可能是巧合相同的两个字段，合并了反而
    没法只改其中一个。
    """
    out = []
    used = set()
    for step in steps:
        if step.get('type') not in PARAM_TYPES or not step.get('value'):
            continue
        alias = ((step.get('target') or {}).get('alias') or '').strip()
        out.append({
            'step': step.get('no'),
            'field': 'value',
            'key': suggest_key(key_hint(step), used),
            'label': alias or ('输入值' if step.get('type') == 'fill' else '选项值'),
            'value': step.get('value'),
        })
    return out


def _check_key(key, used):
    key = (key or '').strip()
    if not re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', key):
        raise ActionError('参数名只能用字母、数字和下划线，且不能以数字开头：%s' % (key or '空'))
    if len(key) > MAX_PARAM_KEY:
        raise ActionError('参数名太长：%s' % key)
    if key in used:
        raise ActionError('参数名重复：%s' % key)
    return key


def _tied_range(steps, start, end):
    """把区间扩到不切断「点击 → 标签页切换」的绑定。

    用户框选时很可能正好切在这两步中间；new_page 单独落在外面，回放时它就没有
    对应的点击可以跟，展开后顺序也乱。往前吞一格 / 往后吞一格都比报错让重选合适。
    """
    if steps[start].get('type') == 'new_page' and start > 0:
        start -= 1
    if end + 1 < len(steps) and steps[end + 1].get('type') == 'new_page':
        end += 1
    return start, end


def group(steps, nos, name, desc='', params=None, action_id=None, min_steps=2):
    """把连续的若干步替换成一个 call 步骤。返回 (新 steps, 动作定义)。

    params 每项 `{step, key, label, field}`，step 是原步骤号；为空表示不参数化，
    子步骤原样保留（值写死）。

    min_steps 默认 2：封装动作至少要两步，一步的「动作」没有复用价值，直接留在
    流程里更清楚。后置处理传 1 —— 只用来收尾的「退出登录」常常就一步。
    """
    if not isinstance(nos, (list, tuple)) or len(nos) < min_steps:
        raise ActionError('至少要选%s步才能封装' % ('两' if min_steps > 1 else '一'))
    index = {}
    for i, step in enumerate(steps):
        index[step.get('no')] = i
    picked = []
    for no in nos:
        try:
            picked.append(index[int(no)])
        except (KeyError, TypeError, ValueError):
            raise ActionError('没有编号为 %s 的步骤' % no)
    picked = sorted(set(picked))
    if picked[-1] - picked[0] + 1 != len(picked):
        raise ActionError('只能封装连续的步骤，中间不能跳过')
    start, end = _tied_range(steps, picked[0], picked[-1])

    name = (name or '').strip()
    if not name:
        raise ActionError('动作名不能为空')
    if len(name) > MAX_NAME:
        raise ActionError('动作名最长 %d 个字' % MAX_NAME)
    desc = (desc or '').strip()
    if len(desc) > MAX_DESC:
        raise ActionError('说明最长 %d 个字' % MAX_DESC)

    # 不允许把调用再包一层：嵌套动作在展开、参数替换、解封三个方向上都要
    # 递归处理，而收益只是「少写一行」。真需要时先解封再一起封装。
    if any(steps[i].get('type') == 'call' for i in range(start, end + 1)):
        raise ActionError('这段里已经有动作调用，不能把调用再封装一次（可以先解封）')

    # clean 同时完成深拷贝：`_frame` 是录制线程里的 Playwright 对象，
    # 带进动作定义既不能序列化，换一次录制也没有意义。
    block = [clean(s) for s in steps[start:end + 1]]
    # 动作内部用相对序号：绝对编号会在解封、再次封装后变得没有意义。
    for i, sub in enumerate(block, 1):
        sub['no'] = i

    normalized = []
    used = set()
    for item in (params or []):
        if not isinstance(item, dict):
            raise ActionError('参数格式不对')
        key = _check_key(item.get('key'), used)
        used.add(key)
        try:
            abs_no = int(item.get('step'))
        except (TypeError, ValueError):
            raise ActionError('参数 %s 缺少来源步骤' % key)
        rel = abs_no - steps[start].get('no', start + 1) + 1
        if rel < 1 or rel > len(block):
            raise ActionError('参数 %s 的来源步骤不在封装区间内' % key)
        field = item.get('field') or 'value'
        if field not in PARAM_FIELDS:
            raise ActionError('参数 %s 只能替换 value 或 label' % key)
        if field not in block[rel - 1] or block[rel - 1].get(field) is None:
            raise ActionError('第 %s 步没有 %s 可以替换' % (abs_no, field))
        # 先把原值记下来：替换成占位符之后这里就只剩 `{{key}}` 了，
        # 而调用点需要一个默认实参，否则用户一封装就得手填。
        original = block[rel - 1][field]
        block[rel - 1][field] = '{{%s}}' % key
        normalized.append({
            'key': key,
            'label': (item.get('label') or key).strip()[:MAX_NAME],
            'step': rel,
            'field': field,
            'value': original,
        })

    action = {
        'id': action_id or new_id(),
        'name': name,
        'desc': desc,
        'params': [{k: v for k, v in p.items() if k != 'value'} for p in normalized],
        'steps': block,
    }
    call = {
        'no': steps[start].get('no') or start + 1,
        'type': 'call',
        'source': 'manual',
        'action': action['id'],
        'args': {p['key']: p.get('value', '') for p in normalized},
        # 快照一份名字：动作被删掉后，这条调用还能显示「登录（动作已删除）」
        # 而不是一个空白行。
        'label': name,
        'text': desc or None,
        'target': None,
        'value': None,
        'url': None,
        'page_index': steps[start].get('page_index', 0),
        'intent': None,
        'dialog': None,
    }
    # 返回新列表并重排编号：调用方拿到的必须是自洽的一串步骤，
    # 而不是「中间空了几个号」的半成品。浅拷贝是为了不改到调用方的 dict。
    result = [dict(s) for s in steps[:start]] + [call] + [dict(s) for s in steps[end + 1:]]
    for i, item in enumerate(result, 1):
        item['no'] = i
    return result, action


def fill_args(step, args):
    """把 `{{key}}` 换成实参，返回深拷贝。缺失的键保持占位符原样。

    不报错是有意的：动作可能是从旧版本存下来的，多出来的占位符让它显式暴露在
    界面上，比整条调用直接失效更容易修。
    """
    clone = copy.deepcopy(step)

    def walk(node):
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        if isinstance(node, str):
            return _PLACEHOLDER.sub(lambda m: str(args.get(m.group(1), m.group(0))), node)
        return node

    return walk(clone)


def expand(steps, actions):
    """把 call 展开成子步骤，按展开后的顺序重排编号。

    只在回放用。生成走的是另一条路：写手需要看到 call 节点本身才知道要抽函数，
    展开成平铺反而会让它把动作复制 N 遍（见 emit._steps_for_prompt）。
    """
    out = []
    for step in steps:
        if step.get('type') != 'call':
            out.append(step)
            continue
        action = (actions or {}).get(step.get('action'))
        if not action:
            out.append(step)
            continue
        for sub in action.get('steps') or []:
            clone = fill_args(sub, step.get('args') or {})
            clone['_action'] = {
                'id': action.get('id'),
                'name': action.get('name'),
                'call_no': step.get('no'),
            }
            out.append(clone)
    for i, step in enumerate(out, 1):
        step['no'] = i
    return out


def ungroup(steps, actions, no):
    """把某一次调用还原成它的子步骤。动作定义本身保留（可能还有别的调用点）。"""
    idx = next((i for i, s in enumerate(steps) if s.get('no') == no), None)
    if idx is None:
        raise ActionError('没有编号为 %s 的步骤' % no)
    step = steps[idx]
    if step.get('type') != 'call':
        raise ActionError('这一步不是动作调用')
    action = (actions or {}).get(step.get('action'))
    if not action:
        raise ActionError('动作不存在，无法解封')
    subs = [fill_args(sub, step.get('args') or {}) for sub in action.get('steps') or []]
    result = [dict(s) for s in steps[:idx]] + subs + [dict(s) for s in steps[idx + 1:]]
    for i, item in enumerate(result, 1):
        item['no'] = i
    return result


def _int_of(value, what):
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ActionError('%s必须是整数' % what)


def _step_name(step):
    """报错时指的「哪一步」：优先用元素别名，其次类型，最后编号。"""
    target = step.get('target') or {}
    return (target.get('alias') or step.get('label') or step.get('type')
            or ('第 %s 步' % step.get('no')))


def _placeholder_left(node):
    """深找第一个还没替换掉的 {{参数}}，没有返回空串。

    移出动作的子步骤要填上这次调用的实参；缺参数时不能把 `{{key}}` 漏进流程，
    否则生成的脚本里会出现一段没人替换的模板。
    """
    if isinstance(node, dict):
        for value in node.values():
            key = _placeholder_left(value)
            if key:
                return key
        return ''
    if isinstance(node, list):
        for value in node:
            key = _placeholder_left(value)
            if key:
                return key
        return ''
    if isinstance(node, str):
        match = _PLACEHOLDER.search(node)
        return match.group(1) if match else ''
    return ''


def edit(steps, actions, action_id, call_no, items, flow, dropped=None):
    """按前端草稿重建动作定义和流程：拖进、拖出、删除、重排一次生效。

    `items` 是动作的新顺序，每项 `{src: 'action'|'flow', no}`：
    - action 取自原动作的第 no 步；
    - flow 取自当前流程的第 no 步（同时从流程里移走）。

    `flow` 是流程的新顺序，每项 `{kind: 'step'|'out', no}`：
    - step 保留当前流程里的第 no 步；
    - out 把原动作的第 no 步填上这次调用的实参后插进流程。

    `dropped` 是「从流程拖进动作后又点了删除」的流程步骤号：动作里没有、流程里也没有，
    等于整步删掉。只做数据改写，不碰浏览器。编号对不上一律报错——宁可让用户重来，
    也不能悄悄丢一步。
    """
    action = (actions or {}).get(action_id)
    if not action:
        raise ActionError('动作不存在')
    call_no = _int_of(call_no, '调用步骤编号')
    call = next((s for s in steps if s.get('no') == call_no), None)
    if call is None or call.get('type') != 'call' or call.get('action') != action_id:
        raise ActionError('第 %s 步不是这个动作的调用，可能已被删除或改动' % call_no)

    old_subs = action.get('steps') or []
    old_by_no = {s.get('no'): s for s in old_subs}
    old_index = {s.get('no'): i for i, s in enumerate(old_subs)}

    if not isinstance(items, list) or not items:
        raise ActionError('动作至少要保留一步；要删掉整个动作请用动作库的删除')
    if not isinstance(flow, list):
        raise ActionError('流程顺序格式不对')

    # ---- 动作的新步骤 ----
    picked_action = set()
    picked_flow = set()
    sources = []  # [(src, no, step)]
    for item in items:
        if not isinstance(item, dict):
            raise ActionError('动作步骤格式不对')
        src = item.get('src')
        no = _int_of(item.get('no'), '动作步骤编号')
        if src == 'action':
            if no in picked_action:
                raise ActionError('动作步骤 %s 重复' % no)
            sub = old_by_no.get(no)
            if sub is None:
                raise ActionError('动作里没有第 %s 步' % no)
            if sub.get('type') == 'call':
                raise ActionError('动作里不能嵌套动作调用')
            picked_action.add(no)
            sources.append(('action', no, sub))
        elif src == 'flow':
            if no in picked_flow:
                raise ActionError('流程步骤 %s 重复拖入' % no)
            step = next((s for s in steps if s.get('no') == no), None)
            if step is None:
                raise ActionError('流程里没有第 %s 步' % no)
            if step.get('type') == 'call':
                raise ActionError('动作调用不能拖进动作里（暂不支持嵌套）')
            if step.get('type') == 'new_page':
                raise ActionError('标签页切换要跟着它的前一步一起动，不能单独拖进动作')
            idx = next(i for i, s in enumerate(steps) if s.get('no') == no)
            if idx + 1 < len(steps) and steps[idx + 1].get('type') == 'new_page':
                raise ActionError('这一步后面跟着标签页切换，不能单独拖进动作')
            picked_flow.add(no)
            sources.append(('flow', no, step))
        else:
            raise ActionError('动作步骤来源只能是 action 或 flow')

    # 「从流程拖进动作后又点了删除」：这一步在动作里和流程里都不该再出现。
    dropped_nos = set()
    for value in (dropped or []):
        no = _int_of(value, '删除步骤编号')
        if no in dropped_nos:
            continue
        if no in picked_flow:
            raise ActionError('第 %s 步既拖进了动作又被删除' % no)
        step = next((s for s in steps if s.get('no') == no), None)
        if step is None:
            raise ActionError('流程里没有第 %s 步' % no)
        if step.get('type') == 'call':
            raise ActionError('调用步骤不能从动作编辑器里删除')
        if step.get('type') == 'new_page':
            raise ActionError('标签页切换要跟着它的前一步一起处理，不能单独删除')
        idx = next(i for i, s in enumerate(steps) if s.get('no') == no)
        if idx + 1 < len(steps) and steps[idx + 1].get('type') == 'new_page':
            raise ActionError('这一步后面跟着标签页切换，不能单独删除')
        dropped_nos.add(no)
    picked_flow |= dropped_nos

    # ---- 标签页切换的绑定 ----
    # 「前一步 → new_page」是一对：new_page 留在动作里，前一步就必须紧挨在它前面；
    # new_page 被移出/删除时，前一步也不能单独留在动作里。
    item_keys = [(src, no) for src, no, _ in sources]
    for i, sub in enumerate(old_subs):
        if sub.get('type') != 'new_page' or i == 0:
            continue
        prev = old_subs[i - 1]
        key = ('action', sub.get('no'))
        if key in item_keys:
            pos = item_keys.index(key)
            if pos == 0 or item_keys[pos - 1] != ('action', prev.get('no')):
                raise ActionError('标签页切换「%s」要留在这步的前一步后面，不能分开'
                                  % _step_name(sub))
        elif ('action', prev.get('no')) in item_keys:
            raise ActionError('标签页切换不能单独删除或移出')

    new_subs = []
    rel_map = {}
    for new_no, (src, no, sub) in enumerate(sources, 1):
        clone = clean(sub)
        clone['no'] = new_no
        new_subs.append(clone)
        if src == 'action':
            rel_map[no] = new_no

    # 参数跟着它所在的步骤走；步骤被移出或删掉，参数一起消失。
    new_params = []
    for param in action.get('params') or []:
        if not isinstance(param, dict):
            continue
        step_no = param.get('step')
        if step_no not in rel_map:
            continue
        item = dict(param)
        item['step'] = rel_map[step_no]
        new_params.append(item)
    new_params.sort(key=lambda p: p.get('step') or 0)

    # ---- 流程的新顺序 ----
    remaining = [s for s in steps if s.get('no') not in picked_flow]
    remaining_by_no = {s.get('no'): s for s in remaining}
    if call_no not in remaining_by_no:
        raise ActionError('调用步骤不能移进动作')

    seen_steps = set()
    out_seen = set()
    new_steps = []
    for entry in flow:
        if not isinstance(entry, dict):
            raise ActionError('流程顺序格式不对')
        kind = entry.get('kind') or 'step'
        no = _int_of(entry.get('no'), '流程步骤编号')
        if kind == 'step':
            if no in seen_steps:
                raise ActionError('流程步骤 %s 重复' % no)
            step = remaining_by_no.get(no)
            if step is None:
                if no in picked_flow:
                    raise ActionError('第 %s 步已经拖进动作，不能再留在流程里' % no)
                raise ActionError('流程里没有第 %s 步' % no)
            seen_steps.add(no)
            # 原对象直接复用：`_frame` 这类录制期引用不能因为一次编辑就丢掉。
            new_steps.append(step)
        elif kind == 'out':
            if no in out_seen:
                raise ActionError('动作步骤 %s 重复移出' % no)
            if no in picked_action:
                raise ActionError('动作步骤 %s 既在动作里又要移出' % no)
            sub = old_by_no.get(no)
            if sub is None:
                raise ActionError('动作里没有第 %s 步' % no)
            if sub.get('type') == 'call':
                raise ActionError('动作里不能嵌套动作调用')
            if sub.get('type') == 'new_page':
                raise ActionError('标签页切换要跟着它的前一步一起处理，不能单独移出')
            idx = old_index.get(no)
            if idx is not None and idx + 1 < len(old_subs) \
                    and old_subs[idx + 1].get('type') == 'new_page':
                raise ActionError('这一步后面跟着标签页切换，不能单独移出')
            filled = fill_args(sub, call.get('args') or {})
            left = _placeholder_left(filled)
            if left:
                raise ActionError('参数 %s 没有实参，不能把这一步移出动作' % left)
            out_seen.add(no)
            new_steps.append(clean(filled))
        else:
            raise ActionError('流程步骤来源只能是 step 或 out')

    if seen_steps != set(remaining_by_no):
        missing = sorted(set(remaining_by_no) - seen_steps)
        raise ActionError('流程步骤对不上（第 %s 步既没保留也没移进动作）'
                          % '、'.join(str(n) for n in missing))

    # 拖出来的步骤不能插进「点击 → new_page」中间：那一步会失去「谁打开了这个标签页」。
    # 用对象身份判断，普通步骤在 new_steps 里是原对象，只有 out 是新造的。
    prev_of_new_page = {}
    for i, step in enumerate(steps):
        if step.get('type') == 'new_page' and i > 0:
            prev_of_new_page[step.get('no')] = steps[i - 1]
    for i, step in enumerate(new_steps):
        if step.get('type') != 'new_page':
            continue
        prev = prev_of_new_page.get(step.get('no'))
        if prev is None:
            continue
        if i == 0 or new_steps[i - 1] is not prev:
            raise ActionError('不能在点击和它打开的标签页之间插入步骤')

    for i, step in enumerate(new_steps, 1):
        step['no'] = i

    new_action = dict(action)
    new_action['steps'] = new_subs
    new_action['params'] = new_params
    return new_steps, new_action


def calls_of(steps, action_id):
    """这个动作在流程里被调用了几次、分别在哪几步。生成时要用它告诉写手复用。"""
    return [s.get('no') for s in steps
            if s.get('type') == 'call' and s.get('action') == action_id]


def library_path(workspace):
    if not workspace:
        return None
    return Path(workspace) / LIBRARY_DIR / LIBRARY_FILE


def load(workspace):
    """读项目动作库。文件不存在 / 坏了都返回空字典：动作库读不出来不该挡住录制。"""
    path = library_path(workspace)
    if path is None or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text('utf-8'))
    except (OSError, ValueError):
        return {}
    items = data.get('actions') if isinstance(data, dict) else None
    if not isinstance(items, list):
        return {}
    out = {}
    for item in items:
        if not isinstance(item, dict) or not item.get('id') or not item.get('name'):
            continue
        if not isinstance(item.get('steps'), list) or not item['steps']:
            continue
        out[item['id']] = {
            'id': item['id'],
            'name': item['name'],
            'desc': item.get('desc') or '',
            'params': [p for p in (item.get('params') or []) if isinstance(p, dict) and p.get('key')],
            'steps': item['steps'],
        }
        if len(out) >= MAX_ACTIONS:
            break
    return out


def save(workspace, actions):
    """整体覆盖写。返回写入路径；没有工作区或写失败返回 None。

    用临时文件 + replace：中途崩了不会留下半个 JSON，否则下次录制整个动作库都读不出来。
    """
    path = library_path(workspace)
    if path is None:
        return None
    payload = {
        'version': LIBRARY_VERSION,
        'actions': [clean(a) for a in list(actions.values())[:MAX_ACTIONS]],
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(LIBRARY_FILE + '.tmp')
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), 'utf-8')
        tmp.replace(path)
        return path
    except OSError:
        return None
