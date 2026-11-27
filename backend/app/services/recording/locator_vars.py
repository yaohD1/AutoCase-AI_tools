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


# 滑块和验证码没有 target：定位直接挂在步骤的配置里。它们同样是「页面元素」，
# 改版时一样要跟着变，所以必须进同一份资源文件，不能留在脚本正文里硬编码。
AUX_SUFFIX = {'slider': '滑块', 'captcha': '验证码', 'captcha_input': '验证码输入框'}


def _collect_aux(steps, prefix, out, scope=None):
    """收滑块 / 验证码的定位。

    captcha 有两个定位（图片本身、要填的输入框），分别存两个变量；
    输入框没指定（留空表示自动找）时不建变量，脚本仍按自动找处理。
    """
    seen = set()
    for step in steps or []:
        items = []
        slider = step.get('slider')
        if slider is not None and str(slider.get('selector') or '').strip():
            items.append(('slider', 'css=' + str(slider['selector']).strip()))
        captcha = step.get('captcha')
        if captcha is not None:
            if str(captcha.get('selector') or '').strip():
                items.append(('captcha', 'css=' + str(captcha['selector']).strip()))
            if str(captcha.get('input_selector') or '').strip():
                items.append(('captcha_input', 'css=' + str(captcha['input_selector']).strip()))
        for aux, locator in items:
            key = (scope, step.get('no'), aux, locator)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                'no': step.get('no'),
                'scope': scope,
                'aux': aux,
                'name': prefix + AUX_SUFFIX[aux],
                'locator': locator,
                # 滑块/验证码是手动步骤，没有 target.page，分组注释只能落到「未标记页面」。
                'route': None,
            })
    return out


def table_row_name(route):
    """表格行定位的变量名后半段：路由末段 + 表格行。

    用路由而不是拾取到的那一格文字：那一格常常是「成功」「CPE」这类值，做成变量名
    既看不懂又会随数据变。末段取不到（根路由 / 只有 :id）就叫「表格行」，由目录前缀兜住。
    """
    leaf = ''
    # 先切掉查询串与 hash：`?id=3` 会被当成最后一段，名字里就多出个 id3。
    path = str(route or '').split('?')[0].split('#')[0]
    for part in path.split('/'):
        part = part.strip()
        if part and not part.startswith(':'):
            leaf = part
    base = _clean(leaf)
    return (base + '表格行') if base else '表格行'


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


def merge(existing, entries, reuse_by_locator=False):
    """把本次的变量并进已有文件。

    覆盖只发生在「确实是同一条定位」的情况下。三种情况要区分开：

    - 本次录制里这个名字已经分配给别的定位 → 同一页有两个同名控件，另起名字。
      这是最容易漏的一种：两个都没标签的输入框都叫「输入框」，光看名字和页面
      分不出来，按覆盖处理会把先出现的那个定位抹掉，生成出来的脚本就点错框。
    - 旧文件里有同名、但来源不同（另一个页面 / 另一个动作）→ 同样另起名字。
    - 其余情况才是「同一条定位又用了一次」，直接覆盖——页面改版要的正是这个效果。

    已有但本次没出现的变量原样保留：它可能还被这个目录下别的用例引用着。

    reuse_by_locator 用于动作的公共文件。那份文件不写页面注释，读回来时认不出来源，
    按「同名 + 同来源」判断会永远不相等，于是每生成一次就多一套 _2/_3（实测 6→12→18）。
    变量本来就是「定位的别名」，一条定位留一个名字就够，所以这里改成按定位认领：
    文件里已经有这条定位，就沿用它的名字，绝不再新增。
    """
    merged = dict(existing)
    by_locator = {}
    if reuse_by_locator:
        for item in merged.values():
            by_locator.setdefault(item['locator'], item['name'])
    taken = {}
    for raw in entries:
        entry = dict(raw)
        if reuse_by_locator:
            reused = by_locator.get(entry['locator'])
            if reused:
                entry['name'] = reused
                entry.pop('collided', None)
                taken.setdefault(normalize_name(reused), entry['locator'])
                merged[normalize_name(reused)] = entry
                # 回写到调用方那份：同一个元素被两条规则认领时（两个动作指向同一元素），
                # 两边都要拿到最终名字，回填才不会漏掉其中一条。
                raw['name'] = reused
                continue
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
        raw['name'] = entry['name']
        if reuse_by_locator:
            by_locator.setdefault(entry['locator'], entry['name'])
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
    两个步骤共用这一个变量才是对的，所以按「这条 entry 最终叫什么」回填，
    不能按编号找：按编号找第二处就丢了。

    entry 上的 name 由 merge 回写（撞名改过名的、被别的动作复用走的，都以它为准）。
    """
    known = {normalize_name(item['name']): item['name'] for item in merged.values()}
    out = {}
    for entry in entries:
        if entry.get('scope') != scope:
            continue
        # 滑块/验证码一个步骤可能有两个定位，塞不进「一步一个变量」的映射，
        # 由 _fill_aux 单独回填。
        if entry.get('aux'):
            continue
        # 表格行这类定位不属于任何一步（no=None），只参与落盘，不回填步骤。
        if entry.get('no') is None:
            continue
        name = known.get(normalize_name(entry.get('name')))
        if name:
            out[str(entry['no'])] = '${%s}' % name
    return out


def _fill_aux(merged, entries):
    """滑块/验证码的变量回填：{no: {'slider': '${..}', 'captcha': '${..}'}}。

    验证码图和输入框同属一步，所以这里比 _fill 多一层 aux 键。
    """
    known = {normalize_name(item['name']): item['name'] for item in merged.values()}
    out = {}
    for entry in entries:
        aux = entry.get('aux')
        if not aux or entry.get('no') is None:
            continue
        name = known.get(normalize_name(entry.get('name')))
        if not name:
            continue
        out.setdefault(str(entry['no']), {})[aux] = '${%s}' % name
    return out


def build(steps, actions, target_path, root, locator_of, page_names=None, extras=None):
    """生成资源文件并返回「步骤 → 变量名」映射。

    locator_of(target) 由调用方给：定位跟框架有关，这里只负责命名和落盘。
    extras 是不属于任何一步、但要进资源文件的定位（目前是表格行），
    每项 {key, name, locator, route}；返回的 extra_vars 按 key 给出最终变量名。

    返回 {'files': [{path, state, count}], 'vars': {no: '${...}'},
          'action_vars': {action_id: {no: '${...}'}},
          'aux_vars': {no: {'slider'|'captcha'|'captcha_input': '${...}'}},
          'action_aux_vars': {action_id: {no: {...}}},
          'extra_vars': {key: '${...}'}}
    """
    leaf = Path(target_path).parent.name or Path(target_path).stem
    prefix = _clean(leaf) or '页面'

    entries = _collect(steps, prefix, locator_of, [])
    aux_entries = _collect_aux(steps, prefix, [])
    extra_entries = []
    for item in (extras or []):
        if not item.get('locator'):
            continue
        extra_entries.append({
            'no': None,
            'scope': None,
            'name': prefix + (item.get('name') or '表格行'),
            'locator': item['locator'],
            'route': item.get('route') or None,
            '_key': item.get('key'),
        })
    entries = entries + extra_entries + aux_entries
    action_entries = []
    for action_id, action in (actions or {}).items():
        sub = (action or {}).get('steps') or []
        if not sub:
            continue
        act_prefix = _clean((action or {}).get('name') or '')
        _collect(sub, act_prefix, locator_of, action_entries, scope=action_id)
        _collect_aux(sub, act_prefix, action_entries, scope=action_id)

    files = []
    step_vars = {}
    action_vars = {}
    extra_vars = {}
    aux_vars = {}
    action_aux_vars = {}

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
        aux_vars = _fill_aux(merged, aux_entries)
        # merge 会把撞名后的最终名字写回 raw，这里直接取。
        for item in extra_entries:
            if item.get('_key'):
                extra_vars[str(item['_key'])] = '${%s}' % item['name']

    if action_entries:
        rel = common_rel()
        path = Path(root) / rel
        # 动作用 scope 区分来源，不写页面（同一个动作会被不同页面调用）。
        # 合并按定位认领：这份文件读回来没有来源标记，按来源比会每轮加一套 _N。
        merged = merge(parse(_read(path)), action_entries, reuse_by_locator=True)
        state = write(path, render(merged, source='动作定义（跨用例共享）', grouped=False))
        files.append({'path': rel.replace('\\', '/'), 'state': state, 'count': len(merged)})
        for action_id in {item.get('scope') for item in action_entries}:
            action_vars[action_id] = _fill(merged, action_entries, action_id)
            action_aux_vars[action_id] = _fill_aux(
                merged, [item for item in action_entries if item.get('scope') == action_id])

    return {'files': files, 'vars': step_vars,
            'action_vars': {k: v for k, v in action_vars.items() if v},
            'aux_vars': aux_vars,
            'action_aux_vars': {k: v for k, v in action_aux_vars.items() if v},
            'extra_vars': extra_vars}
