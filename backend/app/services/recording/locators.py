"""定位候选的生成、排序与唯一性判定。

纯函数，不依赖 Playwright：候选的 count 由 session 在操作发生时去页面上算出，
这里只决定顺序和状态。

候选分两组：
- Playwright 组（testid / role / label / placeholder / text / css）：用于 Playwright TypeScript。
- Robot Framework 组（rf_id / rf_name / rf_xpath / rf_css）：对应 SeleniumLibrary 的 locator 语法。
两组都在录制时数一遍 count，生成时由 pick_for 按目标框架选第一个唯一的候选。
"""
import re

PW_KINDS = ('testid', 'role', 'label', 'placeholder', 'text', 'css')
# RF 组按「语义强度」排：属性型（testid/id/name/aria/label）优先，文本型其次，结构定位兜底。
# 新增 rf_testid / rf_aria / rf_label 是为了让快照里的每个语义信号都有 xpath 等价物，
# 否则 div 按钮、菜单项、图标按钮只能掉到 rf_css（详见 test_recording_locator_parity）。
RF_KINDS = ('rf_testid', 'rf_id', 'rf_name', 'rf_aria', 'rf_label', 'rf_xpath', 'rf_css')
# 排序优先级：Playwright 组在前，RF 组在后。rank() 给出的主定位只用于 Playwright 预览。
PRIORITY = PW_KINDS + RF_KINDS
RF_PREFERENCE = ('rf_testid', 'rf_id', 'rf_name', 'rf_aria', 'rf_label', 'rf_xpath', 'rf_css')

# 文本型 xpath 只对「通常不嵌套同类标签」的元素生成，避免 //div[...] 把祖先容器也算进来
# （Playwright 的 text 引擎只命中最小元素，两侧计数会对不上）。
TEXT_TAGS = ('button', 'a', 'label', 'span', 'li', 'td', 'th', 'option', 'p',
             'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'strong', 'em')

# 这些定位依赖页面结构（位置、顺序），结构一变就可能指向别的元素。
# 只要唯一就接受（用户确认），但要标黄让用户看见。
FRAGILE_KINDS = ('css', 'rf_css')

# 组件库生成的 id（如 el-id-749-6）每次渲染都会变，不能用于定位。
_DYNAMIC_ID = re.compile(r'^el-id-|\d{3,}')


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


def build_specs(snap):
    """根据录制快照生成候选描述。每项包含调用 Playwright 所需的方法和参数。"""
    specs = []
    if snap.get('testid'):
        specs.append(_spec('testid', 'get_by_test_id', [snap['testid']], "getByTestId(%r)" % snap['testid']))
    role = snap.get('role')
    name = snap.get('name')
    if role and name:
        specs.append(_spec('role', 'get_by_role', [role], "getByRole(%r, name=%r)" % (role, name),
                           {'name': name, 'exact': True}))
    if snap.get('label'):
        specs.append(_spec('label', 'get_by_label', [snap['label']], "getByLabel(%r)" % snap['label'],
                           {'exact': True}))
    if snap.get('placeholder'):
        specs.append(_spec('placeholder', 'get_by_placeholder', [snap['placeholder']],
                           "getByPlaceholder(%r)" % snap['placeholder'], {'exact': True}))
    text = snap.get('text')
    if text and len(text) <= 40 and snap.get('tag') in ('button', 'a', 'label', 'span', 'div'):
        specs.append(_spec('text', 'get_by_text', [text], "getByText(%r)" % text, {'exact': True}))
    if snap.get('css'):
        specs.append(_spec('css', 'locator', [snap['css']], "locator(%r)" % snap['css']))

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
        specs.append(_spec('rf_id', 'locator', ['xpath=' + expr], 'rf id:' + element_id))

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


def rank(counted):
    """counted: [{'kind', 'desc', 'count', ...}]。返回 (主定位或 None, 排序后的候选)。

    排序按 PRIORITY，同一类型内保持原顺序。主定位为优先级最高且 count == 1 的候选。
    """
    order = {kind: i for i, kind in enumerate(PRIORITY)}
    ordered = sorted(counted, key=lambda c: order.get(c['kind'], len(PRIORITY)))
    primary = next((c for c in ordered if c['count'] == 1), None)
    return primary, ordered


def pick_for(framework, counted):
    """按目标框架选定位。返回唯一的候选，没有则 None。

    playwright-ts 只看 Playwright 组；robot-selenium 只看 RF 组，并按 RF_PREFERENCE 排序。
    """
    return next((c for c in pool_for(framework, counted) if c['count'] == 1), None)


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
    pool = [c for c in pool_for(framework, counted) if c.get('count', -1) > 0]
    if not pool:
        return None
    order = {kind: i for i, kind in enumerate(PRIORITY)}
    return sorted(pool, key=lambda c: (c['count'], order.get(c['kind'], len(PRIORITY))))[0]



def status_of(primary):
    """unique：主定位来自语义定位；fragile：主定位只能靠结构定位；ambiguous：没有唯一候选。"""
    if primary is None:
        return 'ambiguous'
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
