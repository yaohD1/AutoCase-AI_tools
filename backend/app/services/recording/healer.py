"""回放失败的诊断与修复建议。

回放失败的那一刻，浏览器还停在出事的页面上——这是唯一能同时看到「录制快照」和
「当前页面」的时刻。这个模块只做三件事：把候选在当前页面上重数一遍、判断失败
原因、给出一条能立刻验证的补丁。

**它不执行任何动作。** 补丁要等用户在卡片上点确认才生效，重试也由用户触发。
理由很实际：每自动点一次都可能是重复提交、重复删除，这类错没法撤销。

补丁为什么只改 `primary` + `candidates`：

- 回放的 Playwright 走 `choose_locator`，读的就是 `target['primary']`；
- RF 走 `pick_for(framework, candidates)`，按 kind 优先级挑第一条 count==1 的；
- 生成端 `_pick_locator` 用的也是同一套函数。

所以「把计数刷新成当前页面上的真实值 + 把 primary 换成 healer 挑中的那条」，
两个框架、回放与生成会同时改口，不需要再造一套覆盖机制。
"""
import logging
import re

from app.services.recording import replay
from app.services.recording.locators import (FRAGILE_KINDS, build_locator,
                                             build_specs, class_stems, id_spec,
                                             pick_for, text_variants)

logger = logging.getLogger(__name__)

# 同一个步骤最多自动修几次。修两次还不行，说明我们的判断本身就是错的，
# 继续试只会浪费用户时间，也把「到底是脚本问题还是产品问题」搅得更糊。
MAX_ATTEMPT = 2

# L2 的等待时长：元素身份没问题、只是当下禁用/还在提交，先等一会儿再试。
# 够走完一次普通接口，又不至于让人盯着屏幕以为卡死。
RETRY_WAIT_MS = 2000

CATEGORY_LABELS = {
    'locator_changed': '定位失效',
    'ambiguous': '定位不唯一',
    'frame_changed': 'iframe 变了',
    'transient': '临时状态',
    'assertion': '断言不符',
    'env': '环境问题',
    'dialog': '弹窗没出现',
    'unsupported': '不支持自动修复',
    'element_replaced': '元素被替换',
    'row_gone': '目标行消失',
    'element_hidden': '元素不可见',
    'unknown': '原因不明',
}

# 这些东西改了也没用——问题不在脚本里。判错的代价很小（用户会看到「环境问题」
# 然后自己判断），但能让「网络挂了」不再被当成「定位写错了」。
_ENV_PATTERNS = (
    (re.compile(r'net::ERR_'), '页面未加载（网络错误），与定位无关'),
    (re.compile(r'NS_ERROR_'), '页面未加载（浏览器错误）'),
    (re.compile(r'ERR_CERT|SSL'), '证书校验失败'),
    (re.compile(r'ECONNREFUSED|ECONNRESET'), '目标服务拒绝连接'),
    (re.compile(r'Target page, context or browser has been closed'), '浏览器或标签页已关闭'),
    (re.compile(r'Target closed'), '浏览器或标签页已关闭'),
    (re.compile(r'Timeout \d+ms exceeded while navigating'), '页面加载超时（服务端慢或未启动）'),
    (re.compile(r'Execution context was destroyed'), '执行中页面被跳转重置'),
)


def _blank(step, attempt):
    target = step.get('target') or {}
    return {
        'no': step.get('no'),
        'type': step.get('type'),
        'alias': target.get('alias'),
        'status': 'ready',
        'category': 'unknown',
        'title': CATEGORY_LABELS['unknown'],
        'reason': '',
        'confidence': 0.0,
        'changes': [],
        'verify': None,
        'risk': 'none',
        'verdict': None,
        'evidence': [],
        'can_apply': False,
        'blocked_reason': None,
        'attempt': attempt,
        'max_attempt': MAX_ATTEMPT,
        'probes': [],
    }


def _blocked(card, category, reason, blocked_reason=None, probes=None,
             verdict=None, evidence=None):
    card['category'] = category
    card['title'] = CATEGORY_LABELS.get(category, category)
    card['reason'] = reason
    card['status'] = 'blocked'
    card['can_apply'] = False
    card['blocked_reason'] = blocked_reason or '无可自动验证的改法，需人工判断'
    card['verdict'] = verdict
    card['evidence'] = evidence or []
    if probes:
        card['probes'].extend(probes)
    return card


def _ready(card, category, reason, patch, changes, confidence, verify=None,
           risk='low', probes=None, verdict=None, evidence=None):
    card['category'] = category
    card['title'] = CATEGORY_LABELS.get(category, category)
    card['reason'] = reason
    card['status'] = 'ready'
    card['confidence'] = round(confidence, 2)
    card['changes'] = changes
    card['verify'] = verify
    card['risk'] = risk
    card['verdict'] = verdict
    card['evidence'] = evidence or []
    card['can_apply'] = bool(verify and verify.get('ok'))
    if not card['can_apply']:
        card['blocked_reason'] = '预检未通过，改动无法落点'
    card['patch'] = patch
    if probes:
        card['probes'].extend(probes)
    return card


# ---------- 页面探测 ----------

def _count(root, spec):
    try:
        return build_locator(root, spec).count()
    except Exception:
        # 选择器本身在当前浏览器里执行不了（老语法的 :has 之类），当作不命中，
        # 不要让它把整次诊断打断。
        return -1


def _measure(root, spec, old=None):
    """重数一条候选。index / relation 保留录制时的值——那是「和目标的相对关系」，
    重数只能刷新数量，重算关系需要录制时打的那个标记，这里没有。"""
    count = _count(root, spec)
    item = {'kind': spec['kind'], 'desc': spec['desc'], 'count': count, 'spec': spec}
    index = (old or {}).get('index')
    if index is not None and count > 1:
        item['index'] = index
    relation = (old or {}).get('relation')
    if relation:
        item['relation'] = relation
    return item


def refresh_candidates(target, root):
    """把录制时那份候选在当前页面上重数一遍。

    旧候选优先（它们带着录制时才算得出的行锚点、relation），`build_specs` 补上
    后来新增的候选（比如 textContent 那一版），否则老录制的修复会少几条可选。
    """
    snap = target.get('snap') or {}
    old = target.get('candidates') or []
    generated = {}
    for spec in build_specs(snap):
        generated.setdefault(spec['desc'], spec)

    out, seen = [], set()
    for item in old:
        desc = item.get('desc')
        if not desc or desc in seen:
            continue
        seen.add(desc)
        spec = item.get('spec') or generated.get(desc)
        if not spec:
            continue
        # 老数据的 spec 可能只存了 method/args，没带 kind/desc。补上再数，
        # 否则一个字段形态差异就会把整次诊断打断。
        if 'kind' not in spec:
            spec = dict(spec, kind=item.get('kind'), desc=desc)
        out.append(_measure(root, spec, item))
    for desc, spec in generated.items():
        if desc in seen:
            continue
        seen.add(desc)
        out.append(_measure(root, spec))
    return out


def _precheck(root, spec, step, index=None):
    """补丁能不能真的点到：唯一命中 + 身份对得上 + 可见 + 可点（输入类还要可编辑）。

    身份核对排在可见性之前：命中的是另一个元素时，说「它不可见」会把人带偏，
    真正的问题是「它根本不是那个按钮」。
    """
    try:
        locator = build_locator(root, spec)
        count = locator.count()
    except Exception as exc:
        return {'ok': False, 'text': '这条定位在当前页面执行不了：%s' % exc,
                'state': None, 'verdict': 'unknown', 'evidence': []}
    if index is not None:
        if index >= count:
            return {'ok': False, 'text': '当前页面只有 %d 个匹配，取不到第 %d 个' % (count, index + 1),
                    'state': None, 'verdict': 'unknown', 'evidence': []}
        locator = locator.nth(index)
    elif count != 1:
        return {'ok': False, 'text': '当前页面命中 %d 个，不是唯一' % count,
                'state': None, 'verdict': 'unknown', 'evidence': []}

    ident = None
    try:
        ident = read_identity(locator)
    except Exception:
        ident = None
    cmp = compare_identity((step.get('target') or {}).get('snap') or {}, ident) if ident else {
        'verdict': 'unknown', 'evidence': [], 'reason': ''}
    if cmp['verdict'] == 'conflict':
        return {'ok': False, 'text': cmp['reason'], 'state': None,
                'verdict': 'conflict', 'evidence': cmp['evidence']}

    try:
        if not locator.is_visible():
            return {'ok': False, 'text': '命中了唯一元素，但它现在不可见',
                    'state': 'hidden', 'verdict': cmp['verdict'], 'evidence': cmp['evidence']}
    except Exception:
        return {'ok': False, 'text': '命中了唯一元素，但读不到它的可见状态',
                'state': None, 'verdict': cmp['verdict'], 'evidence': cmp['evidence']}
    try:
        box = locator.bounding_box()
    except Exception:
        box = None
    if not box:
        return {'ok': False, 'text': '命中了唯一元素，但它没有布局框（可能是隐藏节点）',
                'state': 'hidden', 'verdict': cmp['verdict'], 'evidence': cmp['evidence']}
    if step.get('type') == 'click':
        try:
            if not locator.is_enabled():
                return {'ok': False, 'text': '唯一命中、身份一致，但它当前是禁用状态',
                        'state': 'disabled', 'verdict': cmp['verdict'], 'evidence': cmp['evidence']}
        except Exception:
            pass
    if step.get('type') in ('fill', 'select'):
        try:
            if not locator.is_editable():
                return {'ok': False, 'text': '元素存在，但不可输入',
                        'state': None, 'verdict': cmp['verdict'], 'evidence': cmp['evidence']}
        except Exception:
            pass
    return {'ok': True, 'text': '唯一命中、身份一致、可见、可落点',
            'state': None, 'verdict': cmp['verdict'], 'evidence': cmp['evidence']}


def _identity_refusal(card, verify, require=False):
    """身份对不上（或证据不足）时拒绝发补丁。"""
    verdict = verify.get('verdict')
    if verdict == 'conflict':
        return _blocked(
            card, 'element_replaced',
            verify.get('text') or '命中元素与录制时不一致',
            blocked_reason='换定位会指向其他元素；请确认元素被删、改名或移位',
            probes=['身份核对：命中元素与录制快照冲突'],
            verdict='conflict', evidence=verify.get('evidence'))
    if verdict == 'unknown' and require:
        reason = ('读不到命中元素身份（页面可能正在跳转），无法确认是否同一元素'
                  if not verify.get('evidence')
                  else '命中元素无可比对特征，无法确认身份')
        return _blocked(
            card, 'element_replaced', reason,
            blocked_reason='证据不足时不发补丁，避免点击落到其他元素',
            probes=['身份核对：证据不足'],
            verdict='unknown', evidence=verify.get('evidence'))
    return None


def _wait_retry(card, primary, verify, reason):
    """L2：身份是对的，只是当下不可操作。只等待重试，不改写定位。"""
    seconds = RETRY_WAIT_MS // 1000
    return _ready(
        card, 'transient', reason,
        {'retry_only': True, 'wait_ms': RETRY_WAIT_MS},
        [{'label': '定位', 'before': _short(primary.get('desc')),
          'after': '不改，等 %d 秒后重试' % seconds}],
        0.6,
        verify={'ok': True, 'text': '唯一命中、身份一致；当前暂时不可操作'},
        probes=['身份核对：%s' % verify.get('verdict')],
        verdict='state',
        evidence=verify.get('evidence'),
    )


def _hidden_refusal(card, verify):
    return _blocked(
        card, 'element_hidden',
        '唯一命中元素当前不可见（隐藏占位或被折叠），重试亦无法点击',
        blocked_reason='若本应可见，可能是产品状态变化；若暂时收起，待恢复后手动重试',
        probes=['身份核对：%s' % verify.get('verdict')],
        verdict=verify.get('verdict'), evidence=verify.get('evidence'))



def _root_of(page, snap):
    """返回 (查询用的根, iframe 是否已经不存在)。"""
    if snap.get('frame_url') or snap.get('frame_name'):
        frame = replay._frame_of(page, snap)
        if frame is not None:
            return frame, False
        return page, True
    return page, False


def _same_as_primary(counted, primary):
    desc = (primary or {}).get('desc')
    if not desc:
        return None
    return next((c for c in counted if c['desc'] == desc), None)


def _short(desc, width=72):
    text = (desc or '').replace('\n', ' ')
    return text if len(text) <= width else text[:width - 1] + '…'


# ---------- 身份核对 ----------
#
# `count == 1` 只说明「页面上只有一个」，不说明「还是录制时那个」。候选全部是从
# 录制快照派生的：按钮被换成同 class 的另一个（提交 → 取消），
# `[class~="primary-action"]` 照样唯一命中、照样能点下去——测试绿了，业务点错了。
#
# 所以补丁发出之前先问一句：命中的这个，身上还带着录制时的哪些特征？
# 强特征（id / testid / aria / name）对得上，或「行锚点 + 文案」对得上，才算同一个人。

_STRONG_FIELDS = (
    ('id', 'id', 'id'),
    ('testid', 'testid', 'testid'),
    ('aria-label', 'aria_label', 'aria'),
    ('name', 'attr_name', 'name_attr'),
)

_IDENTITY_JS = r"""el => {
  const t = s => (s || '').replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim();
  const row = el.closest ? el.closest('tr,[role="row"],li,[role="listitem"]') : null;
  const owner = el.closest ? el.closest('button,[role="button"],a') : null;
  const pack = node => node ? {
    tag: (node.tagName || '').toLowerCase(),
    id: node.id || '',
    testid: node.getAttribute('data-testid') || '',
    aria: node.getAttribute('aria-label') || '',
    name_attr: node.getAttribute('name') || '',
    text: t(node.innerText || node.textContent || '')
  } : null;
  return {
    tag: (el.tagName || '').toLowerCase(),
    id: el.id || '',
    testid: el.getAttribute('data-testid') || '',
    aria: el.getAttribute('aria-label') || '',
    name_attr: el.getAttribute('name') || '',
    text: t(el.innerText || el.textContent || ''),
    row_text: row ? t(row.innerText || row.textContent || '') : '',
    class_raw: el.getAttribute('class') || '',
    owner: pack(owner),
    visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length),
    enabled: !el.disabled && el.getAttribute('aria-disabled') !== 'true'
  };
}"""


def _norm(value):
    return ' '.join((value or '').replace('\u00a0', ' ').split())


def read_identity(locator):
    """读命中元素身上的身份特征。字段名和录制快照对齐，才能逐项比对。"""
    return locator.evaluate(_IDENTITY_JS)


def compare_identity(snap, ident):
    """录制快照 vs 当前命中元素：给结论、逐项证据、以及冲突说明。

    结论四档，只有 verified / probable 才允许发补丁：

    - verified：强身份信号（id / testid / aria / name）对得上，或「行锚点 + 文案」对得上；
    - probable：没有冲突，tag + class/文案至少一项对得上（典型：只有 class 的图标按钮）；
    - unknown：证据不足。不判错，但也不给补丁——宁可不修，不猜；
    - conflict：明确指向别的元素（id 不同、行锚点消失、文案不同且无强证据）。
    """
    snap = snap or {}
    ident = ident or {}
    evidence = []
    strong_ok, strong_bad = [], []

    for label, snap_key, ident_key in _STRONG_FIELDS:
        recorded = _norm(snap.get(snap_key))
        if not recorded:
            continue
        actual = _norm(ident.get(ident_key))
        if actual and actual == recorded:
            strong_ok.append(label)
            evidence.append({'label': label, 'recorded': recorded, 'actual': actual,
                             'ok': True, 'level': 'match'})
        elif actual:
            strong_bad.append(label)
            evidence.append({'label': label, 'recorded': recorded, 'actual': actual,
                             'ok': False, 'level': 'conflict'})
        else:
            # 录制时有、现在没有：可能是换了个壳，也可能是另一个元素。不当作冲突，
            # 让它退到弱信号去判（1c：图标按钮换容器后 id 丢了，class 仍然唯一）。
            evidence.append({'label': label, 'recorded': recorded, 'actual': '',
                             'ok': False, 'level': 'info'})

    values = [value for value in (_norm(item) for item in text_variants(snap)) if value]

    # 命中的可能是目标里的子节点（典型：getByText 落在按钮的 span 上）。子节点自己
    # 没有 id，但它所属的那个按钮有——父级对得上，仍然算同一个元素，而且能据此判
    # 「点它等于点按钮」（事件冒泡）。
    owner = ident.get('owner') or {}
    if not strong_ok:
        for label, snap_key, ident_key in _STRONG_FIELDS:
            recorded = _norm(snap.get(snap_key))
            if not recorded:
                continue
            own_value = _norm(ident.get(ident_key))
            owner_value = _norm(owner.get(ident_key))
            if owner_value and owner_value == recorded and owner_value != own_value:
                strong_ok.append(label + '（父级）')
                evidence.append({'label': label + '（父级）', 'recorded': recorded,
                                 'actual': owner_value, 'ok': True, 'level': 'match'})
                break

    actual_text = _norm(ident.get('text'))
    text_ok = bool(actual_text and actual_text in values)
    text_bad = bool(values and actual_text and not text_ok)
    if values or actual_text:
        evidence.append({'label': '文案', 'recorded': values[0] if values else '',
                         'actual': actual_text, 'ok': text_ok,
                         'level': 'match' if text_ok else ('conflict' if text_bad else 'info')})

    scope = snap.get('row_scope') or {}
    row_key = _norm(scope.get('key'))
    row_ok = row_bad = False
    if row_key:
        row_ok = row_key in _norm(ident.get('row_text'))
        row_bad = not row_ok
        evidence.append({'label': '行锚点', 'recorded': row_key,
                         'actual': _short(ident.get('row_text'), 48),
                         'ok': row_ok, 'level': 'match' if row_ok else 'conflict'})

    tag = _norm(snap.get('tag')).lower()
    actual_tag = _norm(ident.get('tag')).lower()
    tag_ok = bool(tag and actual_tag and tag == actual_tag)
    if tag or actual_tag:
        evidence.append({'label': 'tag', 'recorded': tag, 'actual': actual_tag,
                         'ok': tag_ok,
                         # tag 不同不足以判定「换了元素」：getByText 天然可能落在
                         # 按钮里的 span、卡片里的 div 上。只用它做弱证据。
                         'level': 'match' if tag_ok else 'info'})

    rec_stems = {stem for stem, _ in class_stems(snap.get('class_raw'))}
    act_stems = {stem for stem, _ in class_stems(ident.get('class_raw'))}
    class_ok = bool(rec_stems & act_stems)
    if rec_stems:
        evidence.append({'label': 'class', 'recorded': ' '.join(sorted(rec_stems)),
                         'actual': ' '.join(sorted(act_stems)),
                         'ok': class_ok, 'level': 'match' if class_ok else 'info'})

    if strong_bad or row_bad:
        verdict = 'conflict'
    elif strong_ok or (row_ok and text_ok):
        verdict = 'verified'
    elif tag_ok and (class_ok or text_ok) and not text_bad:
        verdict = 'probable'
    elif text_bad:
        verdict = 'conflict'
    else:
        verdict = 'unknown'

    diffs = ['%s %s → %s' % (item['label'], item['recorded'] or '（空）',
                             item['actual'] or '（空）')
             for item in evidence if item['level'] == 'conflict']
    reason = ''
    if verdict == 'conflict':
        reason = '命中的元素和录制时不是同一个'
        if diffs:
            reason += '（%s）' % '；'.join(diffs[:3])
        reason += '。换定位会点到别的元素，所以不给改法。'
    return {'verdict': verdict, 'evidence': evidence, 'reason': reason,
            'strong_ok': strong_ok, 'strong_bad': strong_bad}


def _row_gone(root, snap):
    """录制时锁定的那一行还在不在。返回行键（消失）或 None。"""
    scope = snap.get('row_scope') or {}
    key = _norm(scope.get('key'))
    if not key:
        return None
    role = (scope.get('role') or '').strip()
    tag = (scope.get('tag') or '').strip().lower()
    try:
        if role in ('row', 'listitem'):
            count = root.get_by_role(role).filter(has_text=key).count()
        elif tag in ('tr', 'li'):
            count = root.locator(tag).filter(has_text=key).count()
        elif role:
            count = root.get_by_role(role).filter(has_text=key).count()
        else:
            count = root.locator('tr').filter(has_text=key).count()
    except Exception:
        return None
    return key if count == 0 else None


# ---------- 诊断 ----------

def diagnose(page, step, framework, error, attempt=1):
    """给一次失败定性，并在能修时给出补丁。

    返回的 dict 直接当卡片发给前端；补丁本体放在 `patch` 里（`snapshot()` 会摘掉）。
    """
    text = str(error or '')
    card = _blank(step, attempt)

    if attempt > MAX_ATTEMPT:
        return _blocked(card, 'unknown',
                        '已自动修复 %d 次仍未通过，同一方向继续尝试意义有限'
                        % MAX_ATTEMPT,
                        blocked_reason='建议先判断是脚本问题还是页面变化')

    for pattern, reason in _ENV_PATTERNS:
        if pattern.search(text):
            return _blocked(card, 'env', reason,
                            blocked_reason='此类问题改脚本无效，先确认目标站点可用')

    kind = step.get('type')
    if kind == 'dialog':
        return _blocked(card, 'dialog',
                        '未出现浏览器原生弹窗；多为上一步未点中，或页面改用了自定义弹层')
    if kind == 'new_page':
        return _blocked(card, 'unsupported', '新标签页未打开，触发它的步骤可能未生效')
    if not step.get('target'):
        return _blocked(card, 'unsupported', '此步无目标元素，无法通过换定位修复')
    if kind in ('assert', 'wait'):
        return _diagnose_assert(page, card, step, framework, text)
    return _diagnose_action(page, card, step, framework, text)


def _diagnose_assert(page, card, step, framework, text):
    intent = step.get('intent') or {}
    target = step.get('target') or {}
    snap = target.get('snap') or {}
    primary = target.get('primary') or {}
    root, frame_missing = _root_of(page, snap)
    counted = refresh_candidates(target, root)
    same = _same_as_primary(counted, primary)

    if intent.get('type') in ('text_contains', 'text_equals', 'value_equals',
                              'page_text_contains', 'url_contains'):
        # 内容类断言失败永远是「改脚本会把问题盖住」：要么产品真的变了，要么这是个 bug。
        return _blocked(card, 'assertion',
                        '页面内容与录制时不一致；改断言会把本次变化当作预期',
                        blocked_reason='请判断是产品改版还是缺陷，再决定是否调整期望值')

    if same and same['count'] == 1:
        spec = same.get('spec') or (primary or {}).get('spec')
        locator = None
        if spec:
            try:
                locator = build_locator(root, spec)
            except Exception:
                locator = None
        ident = None
        if locator is not None:
            try:
                ident = read_identity(locator)
            except Exception:
                ident = None
        cmp = compare_identity(snap, ident) if ident else {
            'verdict': 'unknown', 'evidence': [], 'reason': ''}
        if cmp['verdict'] == 'conflict':
            return _blocked(
                card, 'element_replaced',
                '断言目标元素已不存在，当前唯一命中的是另一元素。%s'
                % (cmp.get('reason') or ''),
                blocked_reason='替换元素等于更换断言对象',
                probes=['身份核对：命中元素与录制快照冲突'])

        visible = enabled = True
        if locator is not None:
            try:
                visible = locator.is_visible()
            except Exception:
                visible = True
            try:
                enabled = locator.is_enabled()
            except Exception:
                enabled = True
        kind = intent.get('type')
        if kind in ('visible', 'wait_visible') and not visible:
            return _blocked(
                card, 'assertion',
                '元素存在但不可见，重试不会改变状态',
                blocked_reason='更可能是产品行为变化，或上一步未完成',
                probes=['断言状态：hidden'])
        if kind in ('hidden', 'wait_hidden') and visible:
            return _blocked(
                card, 'assertion',
                '元素当前仍可见，与「应隐藏」不符',
                blocked_reason='页面状态已变，改断言会把变化当作预期',
                probes=['断言状态：visible'])
        if kind == 'enabled' and not enabled:
            return _blocked(
                card, 'assertion',
                '元素存在，但当前为禁用状态',
                blocked_reason='多为产品状态或上一步失败，非定位问题',
                probes=['断言状态：disabled'])

        # 去掉失效的 iframe 本身就是修复，别把它并进「纯重试」——纯重试不写回，
        # 下次回放还会撞同一堵墙。
        patch = {'clear_frame': True} if frame_missing else {'retry_only': True}
        return _ready(
            card, 'transient',
            '元素当前正常（唯一命中、状态一致），失败或为当时未渲染完',
            patch,
            [{'label': '定位', 'before': _short(primary.get('desc')), 'after': '不改，直接重试'}],
            0.7,
            verify={'ok': True, 'text': '元素当前唯一命中、状态一致'},
            probes=['重数主定位：当前命中 1 个'],
            verdict='state',
        )

    return _blocked(card, 'locator_changed',
                    '断言目标元素在当前页面命中 0 个',
                    blocked_reason='替换元素等于更换断言对象，需确认',
                    probes=['重数主定位：命中 0 个'])



def _diagnose_action(page, card, step, framework, text):
    target = step.get('target') or {}
    snap = target.get('snap') or {}
    primary = target.get('primary') or {}
    root, frame_missing = _root_of(page, snap)
    if frame_missing:
        card['probes'].append('iframe 已不存在，改在主页面上重新查找')

    counted = refresh_candidates(target, root)
    if not counted:
        return _blocked(card, 'unknown', '所有候选均失效，页面结构可能已变化')

    same = _same_as_primary(counted, primary)
    primary_now = same['count'] if same else -1
    card['probes'].append('重数录制时主定位：命中 %s 个' % (primary_now if primary_now >= 0 else '未知'))

    # 0) iframe 已经不存在：这一条必须排在「临时状态」之前。去掉约束后主定位往往
    #    恰好唯一命中，会被误判成「当时没渲染完」——可重试时还是同一个 iframe，
    #    只会原样再失败一次。
    if frame_missing and primary_now == 1:
        verify = _precheck(root, (same or {}).get('spec') or (primary or {}).get('spec'),
                           step)
        refusal = _identity_refusal(card, verify, require=True)
        if refusal:
            return refusal
        return _ready(
            card, 'frame_changed',
            'iframe 已不存在；去掉约束后主页面上唯一命中且身份一致',
            {'clear_frame': True},
            [{'label': 'iframe', 'before': snap.get('frame_name') or snap.get('frame_url'),
              'after': '不限定 iframe'}],
            0.85,
            verify=verify,
            probes=['iframe 已消失，主页面重新计数命中 1 个'],
            verdict=verify.get('verdict'),
            evidence=verify.get('evidence'),
        )

    # 0.5) 行锚点没了：不能退到全局同名元素。行是身份的一部分——
    #      Alice 那行被删了，页面上还剩 Bob 那一行的「删除」，换过去就是删错人。
    if not frame_missing:
        gone = _row_gone(root, snap)
        if gone:
            return _blocked(
                card, 'row_gone',
                '行锚点（%s）已不在页面；其余同名按钮属于其他行，换过去会点错' % gone,
                blocked_reason='该行可能被删除、被筛除，或上一步失败',
                probes=['行锚点：%s 已不存在' % gone],
                verdict='conflict',
                evidence=[{'label': '行锚点', 'recorded': gone,
                           'actual': '页面上已没有这一行', 'ok': False,
                           'level': 'conflict'}])

    # 1) 原定位现在唯一命中：先核对身份，再决定是「重试」还是「它已经换人了」。
    if primary_now == 1:
        spec = (same or {}).get('spec') or (primary or {}).get('spec')
        verify = _precheck(root, spec, step)
        refusal = _identity_refusal(card, verify)
        if refusal:
            return refusal
        if verify.get('state') == 'disabled':
            return _wait_retry(
                card, primary, verify,
                '身份一致但处于禁用状态（提交/加载中）；改定位无效，稍后用原定位重试')
        if verify.get('state') == 'hidden':
            return _hidden_refusal(card, verify)
        if not verify.get('ok'):
            return _ready(
                card, 'locator_changed', '主定位唯一命中，预检未通过',
                {'retry_only': True, 'clear_frame': frame_missing},
                [{'label': '定位', 'before': _short(primary.get('desc')), 'after': '不改，直接重试'}],
                0.5, verify=verify, risk='medium',
                verdict=verify.get('verdict'), evidence=verify.get('evidence'))
        return _ready(
            card, 'transient',
            '原定位当前正常（唯一命中、身份一致），失败或为当时未渲染完',
            {'retry_only': True, 'clear_frame': frame_missing},
            [{'label': '定位', 'before': _short(primary.get('desc')), 'after': '不改，直接重试'}],
            0.72,
            verify={'ok': True, 'text': '当前唯一命中、身份一致，直接重试'},
            probes=['主定位当前命中 1 个，身份核对通过'],
            verdict='state',
            evidence=verify.get('evidence'),
        )

    # 2) 变成多个：只有录制时真记过序号才敢往 .nth 上修，而且第 N 个本身也要核对身份。
    if primary_now > 1:
        index = primary.get('index')
        if index is not None and 0 <= index < primary_now:
            spec = (same or {}).get('spec') or (primary or {}).get('spec')
            verify = _precheck(root, spec, step, index=index)
            refusal = _identity_refusal(card, verify, require=True)
            if refusal:
                return refusal
            patch = {'primary': dict(primary, count=primary_now), 'candidates': counted,
                     'clear_frame': frame_missing}
            return _ready(
                card, 'ambiguous',
                '主定位当时 1 个，现为 %d 个；录制序号为第 %d 个且身份一致，可按序号定位'
                % (primary_now, index + 1),
                patch,
                [{'label': '主定位', 'before': _short(primary.get('desc')),
                  'after': _short(primary.get('desc')) + '  → 取第 %d 个' % (index + 1)}],
                0.65,
                verify={'ok': True, 'text': '按录制序号取第 %d 个，身份核对通过' % (index + 1)},
                risk='medium',
                probes=['主定位当前命中 %d 个，记录序号 %d' % (primary_now, index + 1)],
                verdict='probable',
                evidence=verify.get('evidence'),
            )
        return _blocked(card, 'ambiguous',
                        '%d 个元素匹配原定位，且未记录录制时点击序号' % primary_now,
                        blocked_reason='无序号无法定位，建议按文本或属性重新录制')

    # 3) 一条都不中：用当前页面重新挑一条唯一的语义定位。
    replacement = pick_for(framework, counted)
    stable = id_spec(snap)
    stable_item = None
    if stable is not None:
        item = _measure(root, stable)
        if item['count'] == 1:
            stable_item = item
    # class / 结构候选比 id 弱：元素改了名时，[class~="primary-action"] 只说明
    # 「页面上有个主按钮」，#login 才说明「是它」。有稳定 id 就优先用 id。
    if stable_item is not None and (replacement is None
                                    or replacement['kind'] in FRAGILE_KINDS
                                    or replacement['kind'] == 'class_part'):
        replacement = stable_item
    if replacement is None:
        alive = [c for c in counted if c.get('count', 0) > 1]
        if alive:
            return _blocked(card, 'ambiguous',
                            '主定位失效，其余候选均非唯一（最多命中 %d 个）'
                            % max(c['count'] for c in alive),
                            blocked_reason='无唯一候选，需人工处理')
        return _blocked(card, 'locator_changed',
                        '原定位及备选在当前页面均匹配 0 个',
                        blocked_reason='元素可能已删除或页面已切换，需确认')

    verify = _precheck(root, replacement['spec'], step)
    refusal = _identity_refusal(card, verify, require=True)
    if refusal:
        return refusal
    if verify.get('state') == 'disabled':
        return _wait_retry(
            card, primary, verify,
            '候选身份一致但处于禁用状态（提交/加载中）；稍后用原定位重试')
    if verify.get('state') == 'hidden':
        return _hidden_refusal(card, verify)
    if frame_missing and not verify.get('ok'):
        return _blocked(card, 'frame_changed',
                        '去掉 iframe 约束后仍无唯一命中候选')
    if not verify.get('ok'):
        return _ready(
            card, 'locator_changed', '已找到候选，预检未通过',
            {'primary': dict(replacement, spec=replacement['spec']), 'candidates': counted,
             'clear_frame': frame_missing},
            [{'label': '主定位', 'before': _short(primary.get('desc')),
              'after': _short(replacement['desc'])}],
            0.5, verify=verify, risk='medium',
            verdict=verify.get('verdict'), evidence=verify.get('evidence'))

    changes = []
    if frame_missing:
        changes.append({'label': 'iframe',
                        'before': snap.get('frame_name') or snap.get('frame_url'),
                        'after': '不限定 iframe'})
    changes.append({'label': '主定位',
                    'before': _short(primary.get('desc')),
                    'after': _short(replacement['desc'])})

    verdict = verify.get('verdict')
    if verdict == 'verified':
        risk, confidence = 'low', 0.9
    else:
        risk, confidence = 'medium', 0.8
    if replacement['kind'] in FRAGILE_KINDS and replacement is not stable_item:
        risk = 'medium'
        confidence = min(confidence, 0.78)
    # `primary` 的约定是「Playwright 组的主定位」（rank 的注释写得很清楚），RF 回放
    # 读的是 candidates。所以即使这次是 RF 回放，写回的 primary 也必须挑 PW 候选，
    # 否则 PW 那条链路会拿一条 xpath= 去 build_locator，直接崩。
    pw_pick = replacement if replacement is stable_item else (
        pick_for('playwright-ts', counted) or replacement)
    category = 'frame_changed' if frame_missing else 'locator_changed'
    reason = ('原定位在当前页面匹配 0 个，'
              '但同期记录的另一条候选仍唯一命中且身份一致')
    if frame_missing:
        reason = ('iframe 在当前页面已不存在；去掉该约束后，'
                  '主页面上有一条候选唯一命中且身份一致。')
    if verdict == 'probable':
        reason += '仅 class / 结构特征吻合，改版后可能再次失效。'
    if replacement['kind'] in FRAGILE_KINDS and replacement is not stable_item:
        reason += '新定位为结构路径，改版后仍会失效。'

    return _ready(
        card, category, reason,
        {'primary': {'kind': pw_pick['kind'], 'desc': pw_pick['desc'],
                     'count': pw_pick.get('count'), 'spec': pw_pick['spec']},
         'candidates': counted,
         'clear_frame': frame_missing},
        changes, confidence, verify=verify,
        risk=risk,
        verdict=verdict,
        evidence=verify.get('evidence'),
        probes=['身份核对：%s' % verdict],
    )



# ---------- 应用 ----------

def apply_patch(step, patch):
    """把补丁套到一个步骤的副本上。原件不动——写回是另一个决定。"""
    import copy

    out = dict(step)
    target = copy.deepcopy(step.get('target') or {})
    if patch.get('primary'):
        target['primary'] = copy.deepcopy(patch['primary'])
    if patch.get('candidates'):
        target['candidates'] = copy.deepcopy(patch['candidates'])
    if patch.get('clear_frame'):
        snap = dict(target.get('snap') or {})
        snap['frame_url'] = None
        snap['frame_name'] = None
        target['snap'] = snap
    out['target'] = target
    return out
