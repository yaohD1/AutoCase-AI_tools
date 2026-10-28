"""定位候选的生成、排序与唯一性判定。

纯函数，不依赖 Playwright：候选的 count 由 session 在操作发生时去页面上算出，
这里只决定顺序和状态。

候选分两组：
- Playwright 组（testid / role / row_scoped / label / placeholder / text / class_part / css）：
  用于 Playwright TypeScript。
- Robot Framework 组（rf_testid / rf_id / rf_name / rf_aria / rf_label / rf_xpath /
  rf_row_scoped / rf_class_part / rf_css）：对应 SeleniumLibrary 的 locator 语法。
两组都在录制时数一遍 count，生成时由 pick_for 按目标框架选第一个唯一的候选。

class_part / rf_class_part 用的是 class 里「去掉构建哈希」的名字（CSS Modules 的
`_sidebarListHeaderAdd_`、BEM 的 `user-card__name`），见 class_stem 的注释。
"""
import json
import re

PW_KINDS = ('testid', 'role', 'row_scoped', 'label', 'placeholder', 'role_bare', 'text',
            'class_part', 'css')
# 能做行锚定的 Playwright 定位：结构路径（css）本身就没有「哪个元素」的语义，套上行作用域没有意义。
PW_SEMANTIC_KINDS = ('role', 'label', 'placeholder', 'role_bare', 'text', 'testid', 'class_part')
# RF 组按「语义强度」排：属性型（testid/id/name/aria/label）优先，文本型其次，结构定位兜底。
# 新增 rf_testid / rf_aria / rf_label 是为了让快照里的每个语义信号都有 xpath 等价物，
# 否则 div 按钮、菜单项、图标按钮只能掉到 rf_css（详见 test_recording_locator_parity）。
# rf_row_scoped 排在 rf_xpath 之后：它本身是用文本 xpath 组出来的，
# 只有在「文本在页面上不唯一」时才比裸文本强，所以绝不能压过一条本来就唯一的语义定位。
# rf_id_loose 排在 rf_label 之后、rf_xpath 之前：id 是元素身份，比文本匹配更接近「元素本身」，
# 但它含长数字串、有可能是自增序号，所以让位给 testid / 干净 id / name / aria / label。
# rf_class_part 排在 rf_row_scoped 之后、rf_css 之前：class 名字是「标记语言里的命名」，
# 比文本 xpath 弱（文本是用户看到的东西），但比 css 结构路径强得多——它不依赖元素在第几个位置。
RF_KINDS = ('rf_testid', 'rf_id', 'rf_name', 'rf_aria', 'rf_label', 'rf_id_loose',
            'rf_xpath', 'rf_row_scoped', 'rf_class_part', 'rf_css')
RF_SEMANTIC_KINDS = ('rf_testid', 'rf_id', 'rf_name', 'rf_aria', 'rf_label', 'rf_id_loose',
                     'rf_xpath', 'rf_class_part')
# 排序优先级：Playwright 组在前，RF 组在后。rank() 给出的主定位只用于 Playwright 预览。
PRIORITY = PW_KINDS + RF_KINDS
RF_PREFERENCE = RF_KINDS

# 文本型 xpath 只对「通常不嵌套同类标签」的元素生成，避免 //div[...] 把祖先容器也算进来
# （Playwright 的 text 引擎只命中最小元素，两侧计数会对不上）。
TEXT_TAGS = ('button', 'a', 'label', 'span', 'li', 'td', 'th', 'option', 'p',
             'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'strong', 'em')

# 这些定位依赖页面结构（位置、顺序），结构一变就可能指向别的元素。
# 只要唯一就接受（用户确认），但要标黄让用户看见。
FRAGILE_KINDS = ('css', 'rf_css')

# 候选命中的元素和「用户真正点的那个」是什么关系。
# recorder.js 在目标元素上打了 data-autocase-target，每条候选在数 count 的同时
# 在页面里定位这个标记，于是「数出来 1 个」不再等于「数对了一个」：
#   self       命中的就是目标，最理想
#   ancestor   命中的是目标的祖先（典型：getByText 命中包裹层），点它通常能冒泡生效
#   descendant 命中的是目标的后代（典型：目标按钮里的 span），同样多数能生效
#   other      命中的元素与目标互不包含 —— 这条候选指的根本是别人，写进脚本必错
#   gone       目标已经离开页面（页面跳转、或被这次点击自己带走）
RELATION_OK = ('self', 'ancestor', 'descendant')
# count 与 relation 都有了才算「验证过」。缺 relation 的是旧数据/没打标记，一律放行。
VERIFIED_RELATIONS = frozenset(('self', 'ancestor', 'descendant', 'other', 'gone'))

# 组件库 / 框架自动生成的 id，每次挂载都重新算，回放时必然对不上。
#
# 以前这里是 `^el-id-|\d{3,}`（search），等于「数字多就是动态的」——恰好判反了：
#   order-20261028-001  业务单号，最稳的定位，被当垃圾扔掉；
#   user-100            也扔掉；
#   :r1:                React 18 useId，每次渲染都变，反而留着；
#   react-select-3-input / rc-tabs-0-tab-2 / input-0   同样留着。
# 判据应该是「id 里有没有框架生成的特征」，不是「数字多不多」。
_DYNAMIC_ID = re.compile(
    r'^(?:'
    r':r[0-9a-z]+:'            # React 18 useId（:r1: / :R2:），Radix、Headless UI 也用它
    r'|el-id-'                 # Element Plus
    r'|react-select-'          # React Select
    r'|react-aria'             # React Aria
    r'|downshift-'             # Downshift
    r'|headlessui-'
    r'|radix-'
    r'|rc-[a-z]+-'             # rc-* 组件库（antd 的底层），如 rc-tabs-0-tab-2
    r'|mui-'                   # MUI
    r'|ext-gen'                # ExtJS
    r'|gwt-uid'                # GWT
    r'|j_id'                   # JSF
    r'|ember[0-9]'             # Ember
    r')',
    re.I,
)

# 含长数字串、但前缀是语义的：可能是业务键（order-20261028-001、user-100），
# 也可能是自增序号（btn-1234、input-3）。录的时候分不清，就不丢——降级成
# rf_id_loose 排在语义属性之后。最坏是回放时找不到元素（明确失败、好查），
# 而丢掉它只会退到 css 结构路径，那才会静默点错。
_ID_WITH_NUMBER = re.compile(r'\d{3,}')

# 「控件类型 + 序号」形态（input-0、tab-2、item-3）：没人会拿它当业务 id，几乎都是渲染时自增的。
# 数字不足 3 位，逃得过上面那条，所以单独认一次。
_SEQUENTIAL_ID = re.compile(
    r'^(?:input|div|span|item|field|node|row|col|tab|btn|button|li|ul|el|component|'
    r'form|form-item|select|option|radio|checkbox|icon|text|label|panel|card|list)'
    r'[-_]?\d+$',
    re.I,
)


# ---- class 里的「名字部分」：CSS Modules / BEM 的稳定那半截 ----
#
# 真实页面上大量控件没有任何语义属性（无 role、无 aria、无文本），class 却是有的：
#
#   _sidebarListHeaderAdd_ce5l0_53     Vite CSS Modules：[name]_[local]_[hash]_[行号]
#   _itemMenu_fxrlp_33                 同上，哈希里连数字都没有
#   user-card__name                    BEM
#   sidebarListHeaderAdd               styled-components / 手写驼峰
#
# 这些串里，构建哈希每次打包都变、名字部分不变。所以 [class*="_sidebarListHeaderAdd_"]
# 比 `div:nth-of-type(3) > div > button` 稳得多：后者靠位置，改个布局就指到别人。
#
# 但不能见 class 就上：Tailwind 的 p-3 / w-[15.368px]、组件库的 ant-badge、emotion 的
# css-1l86wfl，要么改一次样式就全变，要么全站同名（唯一时也只说明「页面上只有一个 badge」，
# 说明不了是哪个）。判据落在「名字形态」上，而不是「像不像哈希」——
# 能认出名字，才说明它是组件级命名而不是工具类。
_CLASS_UTILITY = frozenset("""
flex grid block inline hidden absolute relative fixed sticky static
w h min max min-w max-w min-h max-h p px py pt pb pl pr m mx my mt mb ml mr
gap space space-x space-y text bg border rounded shadow ring outline
font leading tracking whitespace break truncate overflow object
cursor pointer select pointer-events resize appearance css
justify items content self place order grow shrink basis
between around evenly stretch baseline first last odd even reverse
top bottom left right inset start end z
opacity transition duration ease delay animate transform scale rotate translate
position display visibility
uppercase lowercase capitalize underline antialiased
container group peer sr-only
sm md lg xl xs
auto none full screen fit
white black gray grey red blue green yellow orange purple pink indigo
slate zinc neutral stone amber lime emerald teal cyan sky violet fuchsia rose
50 100 200 300 400 500 600 700 800 900 950
active hover focus selected checked disabled open show hidden visible
primary secondary small large left right center middle inner outer
header footer body title name label icon image wrap main side dark light
""".split())

# 组件库给自己所有组件都打的前缀（ant-badge、el-input、t-icon…）：全站同名，
# 唯一也只能说明「页面上只有一个」，不能说明是哪一个。Angular 的 _ngcontent-xxx
# 更是每个元素都有，必须挡掉——它对定位零信息量，却会把每条候选都命中一遍。
#
# 分隔符必须写进正则：只写 `t` 会把 tab / title / tooltip 全吃掉，只写 `el`
# 会把 element-xxx 吃掉——前缀再准，少了边界就是一场误伤。
_CLASS_VENDOR = re.compile(
    r'^(?:_ng(?:content|host)|'
    r'(?:ant|antd|arco|el|iv|van|nut|t|mui|chakra|semi|swiper|slick|leaflet|monaco|ace|'
    r'sc|css|emotion|styled|jsx|ng|v|vue)[-_])',
    re.I,
)
# 名字本身要有点信息量：至少 4 个字母，且有一个片段长到不可能是 md / sm / xl 这种缩写。
CLASS_MIN_LETTERS = 4
# 一个元素最多看多少个 class 词、最多产几条候选。class 列表再长，有用的也就前几个。
CLASS_TOKEN_MAX = 24
CLASS_STEM_MAX = 2
# class 属性可能很长（工具类堆几十个），只留够判定的部分，别把 IR 撑大。
CLASS_RAW_MAX = 240


def _hash_part(part):
    """下划线分组里，这一段像不像构建哈希 / 行号。

    纯数字（行号）、或短短的小写字母数字串（fxrlp、ce5l0、1a2b3c）都算。
    但 active、primary 这类词是状态/变体名，剥掉就把名字截断了，要留住。
    """
    if not part:
        return False
    if part.isdigit():
        return True
    if not re.fullmatch(r'[a-z0-9]{3,7}', part):
        return False
    return part not in _CLASS_UTILITY


def _hash_seg(seg):
    """中划线分组里，这一段像不像哈希。

    这里比下划线严格：中划线切出来的多半是单词（user-card），不能因为「短」就丢。
    只有「字母数字混着、长度够」的那种才当哈希（1a2b3c、1l86wfl）。
    """
    if seg.isdigit():
        return True
    return bool(re.fullmatch(r'[a-z0-9]{5,}', seg)) and any(c.isdigit() for c in seg) \
        and any(c.isalpha() for c in seg)


def _class_ok(stem):
    """这个 stem 值不值得当候选：剥掉分隔符后要有名字，而且不能全是工具类词。"""
    core = (stem or '').strip('_-')
    if not core:
        return False
    segments = [s for s in re.split(r'[-_]+', core) if s]
    if not segments:
        return False
    if all(s.isdigit() or s.lower() in _CLASS_UTILITY for s in segments):
        return False
    letters = sum(len(re.findall(r'[A-Za-z]', s)) for s in segments)
    if letters < CLASS_MIN_LETTERS:
        return False
    return any(len(s) >= 4 and s.lower() not in _CLASS_UTILITY for s in segments)


def class_stem(token):
    """从一个 class 词里剥出「构建后仍然不变」的那部分，返回 (stem, 是否整词精确匹配)。

    剥不出名字返回 (None, False)。整词返回时用 `[class~=]`（精确 token），
    剥过哈希的用 `[class*=]`（包含）。
    """
    if not token or len(token) > 80:
        return None, False
    # 属性值里有这些字符就没法安全地放进选择器：任意值（w-[15px]）、伪类、引号。
    # 这些恰好也都是 Tailwind 的写法，顺手挡掉。
    if any(ch in token for ch in '[]()/:\\"\'=~^$*+|!@#%&{},;<>'):
        return None, False
    if _CLASS_VENDOR.match(token):
        return None, False

    if '_' in token:
        parts = token.split('_')
        # 从第一个「后面全是哈希/行号」的位置切开：_name_hash_31 → _name_
        for i in range(1, len(parts)):
            if not '_'.join(parts[:i]).strip('_'):
                continue
            if all(_hash_part(p) for p in parts[i:] if p):
                stem = '_'.join(parts[:i]) + '_'
                return (stem, False) if _class_ok(stem) else (None, False)
        return (token, True) if _class_ok(token) else (None, False)

    if '-' in token:
        parts = token.split('-')
        # BEM 修饰符（user-card--active）描述的是状态，块名才是元素身份。
        if '--' in token:
            block = token.split('--')[0]
            return (block, True) if _class_ok(block) else (None, False)
        trimmed = list(parts)
        while len(trimmed) > 1 and _hash_seg(trimmed[-1]):
            trimmed.pop()
        if len(trimmed) != len(parts):
            stem = '-'.join(trimmed) + '-'
            return (stem, False) if _class_ok(stem) else (None, False)
        return (token, True) if _class_ok(token) else (None, False)

    # 没有分隔符：驼峰名（sidebarListHeaderAdd）是组件命名，纯小写单词（button）不是。
    if not re.search(r'[a-z][A-Z]', token):
        return None, False
    return (token, True) if _class_ok(token) else (None, False)


def class_stems(raw):
    """一个元素的 class 属性 → 最多两条 [(stem, exact)]，长的（更具体的）在前。

    同一元素上 `_itemMenu_ _itemMenuIcon_` 这种父子组件同时挂的情况很常见，
    取名字最长的那个才指向真正的那个组件。
    """
    out = []
    seen = set()
    for token in (raw or '')[:CLASS_RAW_MAX].split()[:CLASS_TOKEN_MAX]:
        stem, exact = class_stem(token)
        if not stem or stem in seen:
            continue
        seen.add(stem)
        out.append((stem, exact))
    out.sort(key=lambda item: -len(item[0]))
    return out[:CLASS_STEM_MAX]


def _css_string(value):
    """CSS 属性选择器里的字符串字面量。双引号必须转义，否则整条选择器语法就断了。"""
    return '"%s"' % value.replace('\\', '\\\\').replace('"', '\\"')


def class_selector(stem, exact):
    """[class~="x"]（整词）/ [class*="x"]（包含，用于剥掉哈希后的前缀）。"""
    return '[class%s=%s]' % ('~' if exact else '*', _css_string(stem))


def class_xpath(stem, exact):
    """XPath 没有 ~=：整词匹配要自己把 class 前后补空格再比。"""
    if not exact:
        return '//*[contains(@class, %s)]' % _xpath_literal(stem)
    return "//*[contains(concat(' ', normalize-space(@class), ' '), %s)]" % _xpath_literal(
        ' %s ' % stem)


def _xpath_literal(text):
    """把任意文本转成 XPath 字符串字面量，处理引号。"""
    if "'" not in text:
        return "'%s'" % text
    if '"' not in text:
        return '"%s"' % text
    parts = text.split("'")
    return 'concat(%s)' % ", \"'\", ".join("'%s'" % p for p in parts)


def _spec(kind, method, args, desc, kwargs=None):
    return {'kind': kind, 'method': method, 'args': args, 'kwargs': kwargs or {}, 'desc': desc}


# Playwright 调用名。渲染出的字符串就是最终写进脚本的那一行，写手照抄即可。
_PW_RENDER = {
    'get_by_test_id': 'getByTestId',
    'get_by_role': 'getByRole',
    'get_by_label': 'getByLabel',
    'get_by_placeholder': 'getByPlaceholder',
    'get_by_text': 'getByText',
    'filter': 'filter',
    'locator': 'locator',
}

# Python 侧参数名 → Playwright 选项名。链式里的 filter 要用驼峰，
# 渲染和真实调用必须拼同一处，否则「数的」和「写的」又不是一个查询了。
_JS_KEY = {'has_text': 'hasText', 'has_not_text': 'hasNotText'}


def _js_value(value):
    """渲染成 JS 字面量。字符串用 repr（与 JS 单引号字符串兼容），布尔用 true/false。"""
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, str):
        return repr(value)
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def _js_args(args, kwargs):
    parts = [_js_value(arg) for arg in (args or [])]
    kwargs = kwargs or {}
    if kwargs:
        pairs = ', '.join('%s: %s' % (_JS_KEY.get(key, key), _js_value(value))
                          for key, value in kwargs.items())
        parts.append('{ %s }' % pairs)
    return ', '.join(parts)


def render_pw(method, args, kwargs):
    """把 spec 的参数渲染成可以直接粘进脚本的 Playwright 调用。

    desc 以前是手写字符串，和 kwargs 各写各的：label 用 exact=True 去数 count，
    desc 却漏掉 exact，写手照抄后子串匹配撞上 strict mode violation。
    现在 desc 由同一份 args/kwargs 生成，参数只有一处，丢不了；
    以后新增任何参数也自动出现在下发的定位里。
    """
    name = _PW_RENDER.get(method)
    if not name:
        return None
    return '%s(%s)' % (name, _js_args(args, kwargs))


def render_chain(chain):
    """把一串链式调用渲染成 page.getByRole(...).filter(...).getByRole(...) 的后半段。

    和 render_pw 同源：链上每一环的参数也只有一份，改一处两边一起变。
    """
    parts = []
    for link in chain or []:
        method = link.get('method')
        name = _PW_RENDER.get(method)
        if not name:
            return None
        parts.append('%s(%s)' % (name, _js_args(link.get('args'), link.get('kwargs'))))
    return '.'.join(parts) if parts else None


def build_locator(root, spec):
    """按 spec 在 root（frame / page / locator）上构造查询。row_scoped 是链式，其余是一次调用。

    所有需要「在页面上真的跑一遍这条定位」的地方都走这里：计数、复核、回放、预览。
    以前每个地方各写一遍 getattr(root, spec['method'])(...)，加一种新定位形态就得改四遍。
    """
    chain = spec.get('chain')
    if not chain:
        return getattr(root, spec['method'])(*spec['args'], **spec.get('kwargs') or {})
    loc = root
    for link in chain:
        method = link.get('method')
        args = link.get('args') or []
        kwargs = link.get('kwargs') or {}
        if method == 'filter':
            loc = loc.filter(**kwargs)
        else:
            loc = getattr(loc, method)(*args, **kwargs)
    return loc


def _pw_spec(kind, method, args, kwargs=None):
    """Playwright 候选：desc 由 args/kwargs 渲染，保证「数的」和「写的」是同一条查询。"""
    kwargs = kwargs or {}
    return _spec(kind, method, args, render_pw(method, args, kwargs), kwargs)


def build_specs(snap):
    """根据录制快照生成候选描述。每项包含调用 Playwright 所需的方法和参数。"""
    specs = []
    if snap.get('testid'):
        specs.append(_pw_spec('testid', 'get_by_test_id', [snap['testid']]))
    role = snap.get('role')
    name = snap.get('name')
    if role and name:
        # exact 必须同时出现在「数 count」和「写进脚本」两边：用子串匹配数出 1 个、
        # 却让脚本按子串去点，遇到「密码 / 密码登录」这种包含关系就会撞 strict mode violation。
        specs.append(_pw_spec('role', 'get_by_role', [role], {'name': name, 'exact': True}))
    elif role:
        # 没有可访问名称时的退路（典型：没有 label 的 select / checkbox、纯图标按钮）。
        # 页面上同类元素只有它一个时这仍是语义定位；多于一个则 count > 1，被 rank 跳过，
        # 交给录制时记下的 index 降级。排位放在 placeholder 之后：有名称的定位永远更强。
        specs.append(_pw_spec('role_bare', 'get_by_role', [role]))
    if snap.get('label'):
        specs.append(_pw_spec('label', 'get_by_label', [snap['label']], {'exact': True}))
    if snap.get('placeholder'):
        specs.append(_pw_spec('placeholder', 'get_by_placeholder', [snap['placeholder']],
                              {'exact': True}))
    text = snap.get('text')
    if text and len(text) <= 40 and snap.get('tag') in ('button', 'a', 'label', 'span', 'div'):
        specs.append(_pw_spec('text', 'get_by_text', [text], {'exact': True}))
    if snap.get('css'):
        specs.append(_pw_spec('css', 'locator', [snap['css']]))

    # ---- class 名字候选：两套框架各一条 ----
    # 放在语义候选之后、css 结构路径之前（顺序由 PRIORITY 决定，与这里的追加顺序无关）。
    # 只对「class 里有名字」的元素生成：见 class_stem 的判据。
    for stem, exact in class_stems(snap.get('class_raw')):
        specs.append(_pw_spec('class_part', 'locator', [class_selector(stem, exact)]))
        specs.append(_spec('rf_class_part', 'locator', ['xpath=' + class_xpath(stem, exact)],
                           'rf class:' + stem))

    # ---- Robot Framework 组：把同一份快照里的每个语义信号投影成等价 xpath ----
    # 只投影「可证明等价」的属性（testid / id / name / aria-label / placeholder / label 关联 / 文本），
    # 这样两套框架的命中集合才对得上；结构路径（css）永远排最后兜底。
    testid = snap.get('testid')
    if testid:
        expr = '//*[@data-testid=%s]' % _xpath_literal(testid)
        specs.append(_spec('rf_testid', 'locator', ['xpath=' + expr], 'rf testid:' + testid))

    element_id = snap.get('id')
    if element_id and not _DYNAMIC_ID.search(element_id):
        expr = '//*[@id=%s]' % _xpath_literal(element_id)
        # 含长数字串或「控件名+序号」的 id 降一级：它可能是业务键（能一直用），
        # 也可能是自增序号（回放时变），光看字符串分不清，所以既不丢也不当首选。
        loose = _ID_WITH_NUMBER.search(element_id) or _SEQUENTIAL_ID.match(element_id)
        kind = 'rf_id_loose' if loose else 'rf_id'
        specs.append(_spec(kind, 'locator', ['xpath=' + expr], 'rf id:' + element_id))

    attr_name = snap.get('attr_name')
    if attr_name:
        expr = '//*[@name=%s]' % _xpath_literal(attr_name)
        specs.append(_spec('rf_name', 'locator', ['xpath=' + expr], 'rf name:' + attr_name))

    aria = snap.get('aria_label')
    if aria:
        expr = '//*[@aria-label=%s]' % _xpath_literal(aria)
        specs.append(_spec('rf_aria', 'locator', ['xpath=' + expr], 'rf aria:' + aria))

    label = snap.get('label')
    if label:
        # for= 关联和包裹式 <label> 是两种结构，各出一条；命中数为 0 的那条自然被跳过。
        specs.append(_spec('rf_label', 'locator',
                           ['xpath=//*[@id=//label[normalize-space(.)=%s]/@for]' % _xpath_literal(label)],
                           'rf label(for):' + label))
        specs.append(_spec('rf_label', 'locator',
                           ['xpath=//label[normalize-space(.)=%s]//*[self::input or self::textarea or self::select]'
                            % _xpath_literal(label)],
                           'rf label(wrap):' + label))

    placeholder = snap.get('placeholder')
    if placeholder:
        expr = '//*[@placeholder=%s]' % _xpath_literal(placeholder)
        specs.append(_spec('rf_xpath', 'locator', ['xpath=' + expr], 'rf xpath:' + expr))

    tag = snap.get('tag')
    text = snap.get('text')
    if text and len(text) <= 40:
        # 显式 role 用 //*[@role=...]，能覆盖 div/span 这类组件库按钮；
        # 隐式 role 不能这么写（真实 <button> 上没有 role 属性），所以标签名单独出一条。
        role_attr = snap.get('role_attr')
        if role_attr:
            expr = '//*[@role=%s][normalize-space(.)=%s]' % (_xpath_literal(role_attr), _xpath_literal(text))
            specs.append(_spec('rf_xpath', 'locator', ['xpath=' + expr], 'rf xpath:' + expr))
        if tag in TEXT_TAGS:
            expr = '//%s[normalize-space(.)=%s]' % (tag, _xpath_literal(text))
            specs.append(_spec('rf_xpath', 'locator', ['xpath=' + expr], 'rf xpath:' + expr))

    if snap.get('css'):
        specs.append(_spec('rf_css', 'locator', ['css=' + snap['css']], 'rf css:' + snap['css']))
    return specs


def row_scoped_spec(row_role, row_key, inner):
    """把目标挂到「这一行」下面：先按行键锁定行，再在行内找目标。

    行键是录制时试出来的（session._pick_row_key 要求它能让这一行唯一命中、且真的包住目标），
    所以比 .nth(i) 稳：列表增删一行、换个排序都不会指错。人工维护这类页面时也是这么写的。
    """
    chain = [
        {'method': 'get_by_role', 'args': [row_role]},
        {'method': 'filter', 'kwargs': {'has_text': row_key}},
    ]
    if inner.get('chain'):
        chain.extend(inner['chain'])
    else:
        chain.append({'method': inner['method'], 'args': inner['args'],
                      'kwargs': inner.get('kwargs') or {}})
    desc = render_chain(chain)
    if not desc:
        return None
    return {
        'kind': 'row_scoped',
        'chain': chain,
        'desc': desc,
        # 兼容仍按普通 spec 读字段的旧逻辑；真正构造查询一律走 build_locator。
        'method': 'locator',
        'args': [row_key],
        'kwargs': {},
    }


def _rf_relative(locator):
    """取出可以直接接在行容器后面的 xpath 片段：xpath=//a/b → //a/b。

    XPath 里 `A//B` 本身就是「A 的后代里的 B」，所以不需要（也不能）加前导点：
    `//tr[c]` 拼上 `//button` 得到 `//tr[c]//button`，是合法的相对路径。
    css= 前缀没法嵌进 xpath（Selenium 的 css 选择器不能接在 xpath 后面），返回 None，
    这类候选就不参与行锚定——宁可退回「取第 N 个」，也不要写出一个语法上就不成立的定位。
    """
    expr = (locator or '').strip()
    if expr.startswith('xpath='):
        expr = expr[6:].strip()
    return expr if expr.startswith('//') else None


def rf_row_exprs(scope):
    """行容器的 xpath 候选。

    recorder.js 现在会带上 tag，所以绝大多数情况能写成 //tr[...] / //li[...]；
    tag 是组件库 div 时用 @role 收窄，否则 //div[...] 会把任意祖先都算进来。
    更早录的快照只有 role，按 row/listitem 猜 tr/li——猜错也只会数不到，不会锚错行。
    """
    role = (scope or {}).get('role')
    tag = ((scope or {}).get('tag') or '').strip().lower()
    out = []
    if re.fullmatch(r'[a-z][a-z0-9-]*', tag or ''):
        if tag in ('tr', 'li'):
            out.append('//' + tag)
        elif role:
            out.append('//%s[@role=%s]' % (tag, _xpath_literal(role)))
    elif role in ('row', 'listitem'):
        out.append('//tr' if role == 'row' else '//li')
    if role:
        out.append('//*[@role=%s]' % _xpath_literal(role))
    return out


def rf_row_scoped_specs(scope, key, inner):
    """把 RF 定位挂到「这一行」下面，产出一条自足的 xpath（可多条，按优先级试）。

    //tr[contains(normalize-space(.), 'WO-20261028-001')]//button[normalize-space(.)='删除']

    SeleniumLibrary 只接一个定位字符串，没法像 Playwright 那样链式调用，所以行作用和
    内层定位必须在同一条 xpath 里表达。内层必须改写成 .// 开头——不改成相对路径的话，
    //button 会从文档根开始找，行作用域就形同虚设。
    """
    inner_expr = _rf_relative(((inner or {}).get('spec') or {}).get('args', [None])[0])
    if not inner_expr:
        return []
    key_expr = _xpath_literal(key)
    out = []
    for container in rf_row_exprs(scope):
        expr = '%s[contains(normalize-space(.), %s)]%s' % (container, key_expr, inner_expr)
        out.append(_spec('rf_row_scoped', 'locator', ['xpath=' + expr], 'rf row:' + expr))
    return out


def inline_nth(locator, index):
    """把「取第几个」直接写进 RF 定位表达式，返回 (locator, 是否已内联)。

    Selenium 的 find_element 匹配多个时**取第一个且不报错**，所以序号只要靠写手
    事后拼字符串，漏改一次就是「脚本绿着点错行」——而这类错不会失败，只会点错。
    这里直接把序号算进表达式，写手原样用：

        xpath=//button[...]  →  xpath=(//button[...])[3]

    只处理 xpath= 前缀：css= 没法在 Selenium 里加索引（css 选择器不能嵌进 xpath），
    返回原样，由调用方降级成 TODO，而不是拼出一句语法上就不成立的定位。
    """
    if index is None or index < 0:
        return locator, False
    expr = (locator or '').strip()
    if not expr.startswith('xpath='):
        return locator, False
    body = expr[6:].strip()
    if not body:
        return locator, False
    return 'xpath=(%s)[%d]' % (body, index + 1), True


def hits_target(item):
    """这条候选是否真的落在目标元素上。

    没算过关系（旧数据、没打标记、或计数时就抛异常）时不否定——宁可放行也不要
    因为一个新加的校验把原本能用的步骤判死。
    """
    relation = item.get('relation')
    if relation not in VERIFIED_RELATIONS:
        return True
    if relation == 'gone':
        # 目标已经不在页面上：这条候选既不算命中，也不算指错，交给上层的 cancelled 逻辑。
        return False
    if relation == 'other':
        return False
    index = item.get('index')
    return index is None or index >= 0


def false_positive(item):
    """明确指向了别的元素（不是目标的祖先/后代，也不是目标本身）。"""
    return item.get('relation') == 'other'


def rank(counted):
    """counted: [{'kind', 'desc', 'count', ...}]。返回 (主定位或 None, 排序后的候选)。

    排序按 PRIORITY，同一类型内保持原顺序。主定位为优先级最高、count == 1 且
    确实命中目标的候选——只数到 1 个还不够，还得是「对的那一个」。
    """
    order = {kind: i for i, kind in enumerate(PRIORITY)}
    ordered = sorted(counted, key=lambda c: order.get(c['kind'], len(PRIORITY)))
    primary = next((c for c in ordered if c['count'] == 1 and hits_target(c)), None)
    if primary is None:
        # 有唯一候选但全被判成假阳性。不直接判 ambiguous：那种情况会让本来能录的
        # 步骤变成「未核实」，用户无从判断。保留原选择，但标 suspect 让界面提示。
        fallback = next((c for c in ordered if c['count'] == 1), None)
        if fallback is not None:
            fallback['suspect'] = True
            return fallback, ordered
    return primary, ordered


def pick_for(framework, counted):
    """按目标框架选定位。返回唯一的候选，没有则 None。

    playwright-ts 只看 Playwright 组；robot-selenium 只看 RF 组，并按 RF_PREFERENCE 排序。
    """
    return next((c for c in pool_for(framework, counted) if c['count'] == 1 and hits_target(c)), None)


def pool_for(framework, counted):
    """目标框架能用的候选，已按该框架的偏好排好序。"""
    if framework == 'robot-selenium':
        pool = [c for c in counted if c['kind'] in RF_KINDS]
        return sorted(pool, key=lambda c: RF_PREFERENCE.index(c['kind']))
    pool = [c for c in counted if c['kind'] in PW_KINDS]
    order = {kind: i for i, kind in enumerate(PW_KINDS)}
    pool.sort(key=lambda c: order[c['kind']])
    return pool


def best_effort(framework, counted):
    """没有唯一候选时的降级选择：返回目标框架下「命中最少」的候选，全都不命中则 None。

    不唯一不代表不能定位——录制时算出的 index 能指出用户点的是第几个匹配，
    所以降级的原则是「挑最窄的那条，再按 index 索引」，而不是整体拒绝生成。
    count 相同再比语义优先级：数量一样时用更语义化的那条。
    """
    pool = [c for c in pool_for(framework, counted)
            if c.get('count', -1) > 0 and not false_positive(c)]
    if not pool:
        return None
    order = {kind: i for i, kind in enumerate(PRIORITY)}
    return sorted(pool, key=lambda c: (c['count'], order.get(c['kind'], len(PRIORITY))))[0]



def narrowest(counted):
    """命中最少的那条候选（不看框架）。

    用途和 best_effort 不同：这里只关心「哪条候选最能说明这批匹配元素」，
    给用户看「页面上有 3 个，分别是哪 3 个」。所以不在乎目标框架能不能用它。
    """
    pool = [c for c in counted if c.get('count', -1) > 0]
    if not pool:
        return None
    order = {kind: i for i, kind in enumerate(PRIORITY)}
    return sorted(pool, key=lambda c: (c['count'], order.get(c['kind'], len(PRIORITY))))[0]


def status_of(primary):
    """unique：主定位来自语义定位；fragile：主定位只能靠结构定位；
    suspect：唯一候选全都指向了别的元素，只是被迫用了其中一条；
    ambiguous：没有唯一候选。
    """
    if primary is None:
        return 'ambiguous'
    if primary.get('suspect'):
        return 'suspect'
    if primary['kind'] in FRAGILE_KINDS:
        return 'fragile'
    return 'unique'


def stale_check(previous_desc, primary):
    """停止录制时重新校验：唯一性是否还在、主定位是否还是同一个。

    返回 'unique' / 'fragile' / 'ambiguous' / 'stale'。stale 表示定位还唯一，但指向的已经不是录制时那一个。
    """
    if primary is None:
        return 'ambiguous'
    if primary['desc'] != previous_desc:
        return 'stale'
    return status_of(primary)
