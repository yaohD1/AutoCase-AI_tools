"""把录制步骤的定位抽成资源文件里的变量。

**为什么按用例目录分，不按页面分**

页面名只能靠 URL 推，同一个路由从不同入口进来推出来的名字会变（/tab-index/alarm-search
这次因为「故障管理」菜单跳到它而叫故障管理，换个入口可能就叫历史告警）。一旦文件名和
变量前缀跟着这个名字走，改个入口就多出一份文件、一批新变量。

用例目录是人建的，天生稳定。一份目录一个文件，同目录下所有用例共享；页面改版时改这
一处，整个目录的用例一起生效——这才是「定位变了只改一处」的真正含义。

**页面信息没浪费**

文件里按页面分组注释，每行变量末尾也标了来源路由。排查时能直接看出「这个定位是哪一页的」，
也能据此判断同名到底是同一条定位漂移了，还是两个页面恰好都有这个名字的控件。

**只做 Robot（SeleniumLibrary）**

Playwright 的变量文件语法和引用方式都不同，等真要用到再说，不在这里混着写。
"""

import re
from pathlib import Path

from app.services.recording import ir

# 和 emit.SHARED_DIR 一致：平台产物统一放工作区根的 .autocase 下，
# 跟人工维护的 元素定位层/ 完全隔离，git diff 一眼能看出哪些是平台改的。
SHARED_DIR = '.autocase'
LOCATOR_SUBDIR = 'locators'

# 公共文件：动作（登录这类）的定位放这里。动作是跨用例复用的，
# 变量跟着某一个用例目录走的话，换个用例生成就找不到了。
COMMON_DIR = '公共'
COMMON_FILE = '公共页面元素.resource'

# 用例根目录不进文件名：.autocase 本身已经表示「平台生成」。
ROOT_LEVELS = ('测试用例层', 'tests', 'testcases', 'cases')

# Robot 变量名不区分大小写，空格和下划线等价；`.` 会触发扩展变量语法。
# 中文别名里还常带全角标点，所以统一只留中文、字母、数字、下划线。
UNSAFE = re.compile(r'[^0-9A-Za-z\u4e00-\u9fff_]+')
MAX_NAME = 28

# 这些别名只是标签名，说明录制时这个元素没有任何文字，别名退化了。
# 老草稿里存的就是它们（那时还没有 testid / 图标名的推断），生成变量名时按现在的
# 规则重算一次。只影响变量名，不动步骤自己的 alias——断言步骤靠它指认目标。
BARE_TAGS = ('i', 'button', 'div', 'span', 'input', 'select', 'textarea', 'a', 'img', 'label', 'svg')

# 变量行格式：`${名字}<2+空格>定位<4空格># 路由`。
# 用 4 空格把注释和定位隔开：CSS 定位里本身就有 `#id`，2 空格分隔会分不清。
VAR_LINE = re.compile(r'^\$\{(?P<name>[^}]+)\}\s{2,}(?P<loc>.+?)(?:\s{4}#\s*(?P<meta>.*))?$')


def normalize_name(name):
    """Robot 眼里的「同名」：不分大小写，连续空白等同下划线。"""
    return re.sub(r'\s+', '_', str(name).strip().lower())


def file_rel(target_path):
    """用例 → 它该用的资源文件（相对工作区根）。"""
    parents = [p for p in Path(target_path).parent.parts if p not in ('', '.')]
    if parents and parents[0] in ROOT_LEVELS:
        parents = parents[1:]
    leaf = parents[-1] if parents else Path(target_path).stem
    return '/'.join([SHARED_DIR, LOCATOR_SUBDIR] + parents + ['%s页面元素.resource' % leaf])


def common_rel():
    return '/'.join([SHARED_DIR, LOCATOR_SUBDIR, COMMON_DIR, COMMON_FILE])


def _clean(text):
    return UNSAFE.sub('', str(text or ''))[:MAX_NAME]


def _type_suffix(step, snap):
    """控件类型后缀。别名本身已经带了就不重复加（见 _element_name）。"""
    kind = step.get('type')
    if kind == 'fill':
        return '输入框'
    if kind == 'select':
        return '下拉框'
    if kind == 'upload':
        return '上传框'
    if kind in ('click', 'press', 'download'):
        role = str(snap.get('role') or '').lower()
        tag = str(snap.get('tag') or '').lower()
        if role == 'button' or tag == 'button':
            return '按钮'
    return ''


def _element_name(step, target):
    """目录前缀 + 元素别名 + 类型后缀，成为变量名的后半段。"""
    snap = target.get('snap') or {}
    alias = target.get('alias') or ''
    # 老草稿里可能是 i / button / input#2 这种退化别名，按现在的规则重算。
    if re.sub(r'[#_]\d+$', '', alias).lower() in BARE_TAGS:
        alias = ir.alias_of(snap) or alias
    base = _clean(alias)
    # 上传常常拾取到包着 input 的 div，别名就是「div」。清空后由后缀补成「上传框」，
    # 不然会拼出「div上传框」这种名字。
    if base in ('div', 'span', 'label') and step.get('type') == 'upload':
        base = ''
    suffix = _type_suffix(step, snap)
    # 既没别名也没类型可用时，给个占位，不能返回空串（变量名会只剩前缀）。
    if not base and not suffix:
        base = '元素'
    # 判断「后缀已经有了」要把末尾的撞名编号剥掉：输入框#2 清理成 输入框2，
    # 不剥的话会拼成「输入框2输入框」。
    if suffix and not base.endswith(suffix) and not re.sub(r'\d+$', '', base).endswith(suffix):
        base += suffix
    return base


def _collect(steps, prefix, locator_of, out, scope=None):
    """把带定位的步骤收成 (变量名, 定位, 页面, 来源)。

    scope 区分「平铺步骤」和「动作里的子步骤」：两者的 no 都从 1 开始，
    只按 no 回填会串到一起。
    """
    seen = set()
    for step in steps or []:
        target = step.get('target') or {}
        if not target:
            continue
        locator = locator_of(target)
        if not locator:
            continue
        key = (scope, step.get('no'), locator)
        if key in seen:
            continue
        seen.add(key)
        page = target.get('page') or {}
        out.append({
            'no': step.get('no'),
            'scope': scope,
            'name': prefix + _element_name(step, target),
            'locator': locator,
            'route': page.get('route') or None,
        })
    return out


def parse(text):
    """读已有资源文件里的变量，返回 {归一化名: {name, locator, route}}（保序）。"""
    found = {}
    for line in (text or '').splitlines():
        match = VAR_LINE.match(line.strip())
        if not match:
            continue
        route = (match.group('meta') or '').strip()
        name = match.group('name')
        found[normalize_name(name)] = {
            'name': name,
            'locator': match.group('loc').strip(),
            'route': None if route in ('', '-') else route,
        }
    return found


def _scope_of(entry):
    """判断两条变量是不是「同一个来源」。

    平铺步骤按页面区分，动作里的子步骤按动作 id 区分。两个来源不同却同名，
    说明是不同元素撞了名字，不能当成「定位漂移」直接覆盖。
    """
    return entry.get('scope') or entry.get('route') or ''


def _next_name(taken, base):
    """撞名时的备用名。用数字后缀而不是页面名：页面名本身就是不稳定的东西。"""
    index = 2
    while normalize_name('%s_%d' % (base, index)) in taken:
        index += 1
    return '%s_%d' % (base, index)


def merge(existing, entries):
    """把本次的变量并进已有文件。

    覆盖只发生在「确实是同一条定位」的情况下。三种情况要区分开：

    - 本次录制里这个名字已经分配给别的定位 → 同一页有两个同名控件，另起名字。
      这是最容易漏的一种：两个都没标签的输入框都叫「输入框」，光看名字和页面
      分不出来，按覆盖处理会把先出现的那个定位抹掉，生成出来的脚本就点错框。
    - 旧文件里有同名、但来源不同（另一个页面 / 另一个动作）→ 同样另起名字。
    - 其余情况才是「同一条定位又用了一次」，直接覆盖——页面改版要的正是这个效果。

    已有但本次没出现的变量原样保留：它可能还被这个目录下别的用例引用着。
    """
    merged = dict(existing)
    taken = {}
    for raw in entries:
        entry = dict(raw)
        base_key = normalize_name(entry['name'])
        known = taken.get(base_key)
        if known is not None and known != entry['locator']:
            entry['name'] = _next_name(set(merged) | set(taken), entry['name'])
            entry['collided'] = True
        prev = merged.get(normalize_name(entry['name']))
        if prev is not None and _scope_of(prev) != _scope_of(entry):
            entry['name'] = _next_name(set(merged) | set(taken), entry['name'])
            entry['collided'] = True
        taken.setdefault(base_key, entry['locator'])
        merged[normalize_name(entry['name'])] = entry
    return merged


def render(merged, page_names=None, source='', grouped=True):
    """grouped=False 用于动作的公共文件：那里没有页面概念，不印分组标题。"""
    names = page_names or {}
    lines = [
        '# 由平台生成，请勿手工修改（下次生成会覆盖）。',
        '# 来源：%s' % (source or '录制生成'),
        '',
        '*** Variables ***',
    ]
    if not grouped:
        for item in merged.values():
            lines.append('${%s}    %s' % (item['name'], item['locator']))
        return '\n'.join(lines) + '\n'

    groups = {}
    for entry in merged.values():
        groups.setdefault(entry.get('route'), []).append(entry)
    for route, items in groups.items():
        title = route or '未标记页面'
        if route and names.get(route):
            title = '%s（%s）' % (route, names[route])
        lines.append('')
        lines.append('# ---- %s ----' % title)
        for item in items:
            lines.append('${%s}    %s    # %s' % (
                item['name'], item['locator'], route or '-'))
    return '\n'.join(lines) + '\n'


def write(path, content):
    """内容一样就不动文件；返回 created / updated / reused。"""
    try:
        existing = path.read_text(encoding='utf-8')
    except OSError:
        existing = None
    if existing == content:
        return 'reused'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')
    return 'created' if existing is None else 'updated'


def _read(path):
    try:
        return path.read_text(encoding='utf-8')
    except OSError:
        return ''


def _fill(merged, entries, scope):
    """把合并后的变量名回填到步骤编号上。

    一个元素被点了两次会产生两个步骤、同一个定位，合并后只留一条变量——
    两个步骤共用这一个变量才是对的，所以回填必须按定位找，不能按编号。
    """
    by_key = {(item.get('scope'), item['locator']): item['name']
              for item in merged.values()}
    out = {}
    for entry in entries:
        if entry.get('scope') != scope:
            continue
        name = by_key.get((scope, entry['locator']))
        if name:
            out[str(entry['no'])] = '${%s}' % name
    return out


def build(steps, actions, target_path, root, locator_of, page_names=None):
    """生成资源文件并返回「步骤 → 变量名」映射。

    locator_of(target) 由调用方给：定位跟框架有关，这里只负责命名和落盘。
    返回 {'files': [{path, state, count}], 'vars': {no: '${...}'},
          'action_vars': {action_id: {no: '${...}'}}}
    """
    leaf = Path(target_path).parent.name or Path(target_path).stem
    prefix = _clean(leaf) or '页面'

    entries = _collect(steps, prefix, locator_of, [])
    action_entries = []
    for action_id, action in (actions or {}).items():
        sub = (action or {}).get('steps') or []
        if not sub:
            continue
        act_prefix = _clean((action or {}).get('name') or '')
        _collect(sub, act_prefix, locator_of, action_entries, scope=action_id)

    files = []
    step_vars = {}
    action_vars = {}

    if entries:
        rel = file_rel(target_path)
        path = Path(root) / rel
        merged = merge(parse(_read(path)), entries)
        state = write(path, render(merged, page_names,
                                   source='%s（录制生成）' % target_path))
        files.append({'path': rel.replace('\\', '/'), 'state': state, 'count': len(merged)})
        # 回填按「来源 + 定位」找：同一个元素点两次只会留下一条变量，
        # 按 no 回填的话第二处就丢了（两个步骤共用一个变量才是对的）。
        step_vars.update(_fill(merged, entries, None))

    if action_entries:
        rel = common_rel()
        path = Path(root) / rel
        # 动作用 scope 区分来源，不写页面（同一个动作会被不同页面调用）。
        merged = merge(parse(_read(path)), action_entries)
        state = write(path, render(merged, source='动作定义（跨用例共享）', grouped=False))
        files.append({'path': rel.replace('\\', '/'), 'state': state, 'count': len(merged)})
        for action_id in {item.get('scope') for item in action_entries}:
            action_vars[action_id] = _fill(merged, action_entries, action_id)

    return {'files': files, 'vars': step_vars,
            'action_vars': {k: v for k, v in action_vars.items() if v}}
