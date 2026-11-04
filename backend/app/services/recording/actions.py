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


def group(steps, nos, name, desc='', params=None, action_id=None):
    """把连续的若干步替换成一个 call 步骤。返回 (新 steps, 动作定义)。

    params 每项 `{step, key, label, field}`，step 是原步骤号；为空表示不参数化，
    子步骤原样保留（值写死）。
    """
    if not isinstance(nos, (list, tuple)) or len(nos) < 2:
        raise ActionError('至少要选两步才能封装')
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
