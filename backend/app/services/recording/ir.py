"""录制结果的中间表示（IR）。

框架无关：只描述用户做了什么、目标是什么、用哪种定位。代码由后续的 adapter 生成。
"""

import re

STEP_TYPES = ('goto', 'reload', 'click', 'fill', 'select', 'press', 'dialog',
              'new_page', 'assert', 'wait', 'sleep', 'call', 'slider', 'captcha',
              'upload', 'download',
              # 关掉被测浏览器。录制时不会真的关（关了就录不下去了），它只是一条
              # 「这里该结束浏览器」的声明，真正执行在生成出的脚本/回放之外。
              'close')

# 下载后要校验到什么程度。none 只看「有文件、体积过线」；
# xlsx / csv 用 zipfile 与 csv 模块直接数行，不引第三方依赖。
DOWNLOAD_PARSERS = ('none', 'xlsx', 'csv')

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

# 等「整页加载到某个状态」：没有目标元素，也不是干等秒数。
# 不进 INTENT_TYPES —— 那是自然语言解析的词表，让模型自己产出这个类型，
# 它会把「等元素出现」也塞进来。这一档只由工具栏的三个按钮直接给。
PAGE_READY = 'page_ready'
READY_STATES = ('load', 'domcontentloaded', 'networkidle')
# 默认 15 秒：内网页面在慢网络下 5 秒经常不够（Playwright 的 expect 默认就是 5 秒），
# 30 秒又会让真正挂掉的用例白等。超时算失败，所以不能给太大。
DEFAULT_READY_SECONDS = 15

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
    # 等整页就绪天生带超时，但它的语义是「等到」，不是「此刻就要成立」。
    if kind == PAGE_READY:
        return 'wait'
    if kind in ('wait_visible', 'wait_hidden'):
        return 'wait'
    if intent and intent.get('timeout'):
        return 'wait'
    return 'assert'


# 别名要显示在下拉里，也要写进给模型的提示词，太长会挤掉真正有区分度的部分。
ALIAS_MAX_LENGTH = 40

# 元素自己不带文字时（图标、纯图标按钮），别名以前直接退化成标签名，界面上于是出现
# 一堆「i」「button」「div」，谁都分不清；把它抽成定位变量后，变量名也会变成
# ${终端资产button} 这种没法用的东西。这里从 testid 末段反推一个能读的词。
# 只收常见动作词，认不出就保留原名——猜错比不猜更麻烦。
ALIAS_WORDS = {
    'refresh': '刷新', 'reload': '刷新',
    'add': '新增', 'create': '新增', 'new': '新增',
    'delete': '删除', 'del': '删除', 'remove': '删除',
    'edit': '编辑', 'update': '修改', 'modify': '修改',
    'search': '查询', 'query': '查询', 'filter': '筛选',
    'export': '导出', 'import': '导入', 'download': '下载', 'upload': '上传',
    'close': '关闭', 'cancel': '取消', 'confirm': '确定', 'ok': '确定',
    'save': '保存', 'submit': '提交', 'reset': '重置', 'back': '返回',
    'more': '更多', 'menu': '菜单', 'expand': '展开', 'collapse': '收起',
    'setting': '设置', 'settings': '设置', 'config': '配置',
    'help': '帮助', 'info': '详情', 'detail': '详情',
}

# 图标单独一张表：方向箭头在界面里的说法和按钮不一样，照搬词表反而看不懂。
ICON_WORDS = {
    'chevron-right': '展开箭头', 'chevron-down': '收起箭头',
    'chevron-left': '左箭头', 'chevron-up': '上箭头',
    'arrow-right': '右箭头', 'arrow-left': '返回箭头',
    'dots-vertical': '更多', 'dots-horizontal': '更多',
    'close': '关闭图标', 'plus': '新增图标', 'pencil': '编辑图标',
    'magnify': '搜索图标', 'delete': '删除图标',
}

# testid 里这些词只说明「这是个什么控件」，不说明「它是干什么的」。
TESTID_NOISE = ('btn', 'button', 'icon', 'ico', 'el', 'the')

# 一个字的线索都没有时，至少把控件类型说成人话：`input` 当变量名比 `${xxx输入框}` 差得多。
# 只收常见控件，div / span 这类还得保留原名——叫「区块」「文本」反而更看不懂。
TAG_WORDS = {
    'input': '输入框', 'select': '下拉框', 'textarea': '多行输入框',
    'button': '按钮', 'a': '链接', 'img': '图片',
}


def alias_of(snap):
    """这个元素该叫什么。

    有文字的元素一直命名得对，不要动它——别名还被断言步骤用来指认目标，
    改了会影响老草稿。只在「完全没有文字」时才从结构里推：
    testid 末段 → 图标名 → 标签名（仍推不出来就认输）。
    """
    text = (snap.get('name') or snap.get('label') or snap.get('placeholder')
            or snap.get('text') or '')
    if str(text).strip():
        return str(text)
    testid = str(snap.get('testid') or '').strip()
    if testid:
        parts = [p for p in re.split(r'[-_\s]+', testid)
                 if p and p.lower() not in TESTID_NOISE]
        if parts:
            tail = parts[-1].lower()
            return ALIAS_WORDS.get(tail, tail)
    icon = re.search(r'\bmdi-([a-z0-9]+(?:-[a-z0-9]+)*)', str(snap.get('class_raw') or ''))
    if icon:
        name = icon.group(1)
        return ICON_WORDS.get(name, name)
    tag = str(snap.get('tag') or '').lower()
    return TAG_WORDS.get(tag, tag)


# 旧名字保留：测试与外部调用用过 _alias_of。
_alias_of = alias_of


def make_target(snap, candidates, primary, status, page=None):
    """目标元素。alias 用于在模板里引用这个元素。

    page 是「这个元素在哪个页面」：{'route': '/terminal/asset', 'title': '终端资产'}，
    由录制时读到的地址归一化而来（见 session.normalize_route）。取不到就不写这个键，
    老数据、手动插入的步骤都保持原样，前端据 v-if 决定显不显示。
    """
    target = {
        'alias': (alias_of(snap) or '')[:ALIAS_MAX_LENGTH],
        'snap': snap,
        'candidates': candidates,
        'primary': primary,
        'status': status,
    }
    if page:
        target['page'] = page
    return target


def make_step(step_type, no, target=None, value=None, label=None, url=None, page_index=0,
              source='record', dialog=None, action=None, args=None, slider=None, captcha=None,
              file=None, expect=None):
    """一步操作。source 为 record（录制得到）或 manual（用户在工具栏添加）。

    press 用 value 存键名（Playwright 写法，如 Enter / Control+Enter）；
    dialog 用 dialog 存 {'kind', 'message', 'action'}，value 另存一遍 action 便于前端直接读。
    call 是「调用一个具名动作」：action 指向动作 id，args 是这次调用的实参。
    slider 是滑块验证的配置：{'selector', 'attempts', 'timeout'}，selector 空表示自动识别。
    captcha 是图形验证码的配置：{'selector', 'input_selector', 'charset', 'length', ...}，
    selector 空表示按常见图片命名猜一遍（猜不到会报错让人拾取，不静默跳过）。
    upload 的 file 是 {'path', 'name', 'size', 'sha256', 'selector'}：path 是工作区相对路径，
    selector 是从拾取到的按钮出发找到的 input[type=file] 的 CSS 路径（回放优先用它）。
    download 的 expect 是 {'name', 'min_bytes', 'parse', 'min_rows'}，留空表示有文件即可。
    """
    if step_type not in STEP_TYPES:
        raise ValueError('unknown step type: %s' % step_type)
    step = {
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
    # 只给 call 挂这两个字段：别的步骤多两个 null 会让前端判断和提示词都变脏。
    if step_type == 'call':
        step['action'] = action
        step['args'] = args or {}
    # 同理只给 slider 挂：它不是「指向一个元素」，而是一段固定流程 + 三个参数。
    if step_type == 'slider':
        step['slider'] = dict(slider or {})
    # captcha 也一样：两段流程（识别、填入）由它自己完成，不是一次元素操作。
    if step_type == 'captcha':
        step['captcha'] = dict(captcha or {})
    # 文件类同样是自有字段：upload 要带资产引用，download 要带校验口径。
    if step_type == 'upload':
        step['file'] = dict(file or {})
    if step_type == 'download':
        step['expect'] = dict(expect or {})
    return step


def replay_view(step):
    """完整步骤：去掉内部引用（以下划线开头的字段），保留定位 spec 与 frame_url。"""
    return {k: v for k, v in step.items() if not k.startswith('_')}


def public_step(step):
    """返回给前端的步骤视图。去掉内部字段（frame 引用等）。"""
    out = {k: v for k, v in step.items() if not k.startswith('_')}
    if step.get('target'):
        out['target'] = dict(step['target'])
        # index：不唯一时用户点的是第几个匹配（降级定位要用），前端据此显示「将取第 2 个」。
        # relation：这一条到底有没有落在目标上（self/ancestor/descendant/other/gone），
        # 前端据此把「唯一但指错」的候选标出来——以前这种候选和真正可靠的看起来一模一样。
        out['target']['candidates'] = [
            {'kind': c['kind'], 'desc': c['desc'], 'count': c['count'],
             'index': c.get('index'), 'relation': c.get('relation')}
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
