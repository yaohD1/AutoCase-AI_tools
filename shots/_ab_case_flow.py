"""对比「双模型两段」与「单模型一轮」生成用例的质量差异。

只做测量，不改任何业务代码。

    .venv/Scripts/python.exe shots/_ab_case_flow.py --phase ab
    .venv/Scripts/python.exe shots/_ab_case_flow.py --phase c      # 自反省第二轮
    .venv/Scripts/python.exe shots/_ab_case_flow.py --report       # 汇总指标

A（现状）：多模态模型读图拆模块 → 每个模块单独调生成模型出用例（默认不带图）
B（单轮）：同一个模型，一次把图 + 要求直接换成用例
C（自省）：B 之后把草稿 + 图再喂一轮，要求补漏/去重/修可验证性

结果落在 shots/_ab_out/。
"""
import argparse
import json
import os
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'backend'))

from app.services import models_config            # noqa: E402
from app.services.case_generator import CaseGenerator  # noqa: E402
from app.services.oc_text import TextError        # noqa: E402

MODEL = os.environ.get('AB_MODEL', '4rount-hyper/gpt-6-luna')
TYPES = ['functional', 'ui', 'boundary', 'exception']
PER_MODULE = 10          # 首页模块卡默认值
OUT_DIR = os.path.join(ROOT, 'shots', '_ab_out')

PAGES = [
    {
        'key': 'login',
        'path': os.path.join(ROOT, 'shots', '_case_src_login.png'),
        'desc': '产品登录页，支持微信扫码登录与账号密码登录',
        'keywords': ['欢迎登录', '微信登录', '账号密码登录', '手机号或邮箱', '密码',
                     '忘记密码', '登录', '服务协议', '隐私政策'],
    },
    {
        'key': 'homework',
        'path': os.path.join(ROOT, 'storage', 'uploads',
                             '41f2aee1-0e1d-4fb3-88dd-c85de37cc504.png'),
        'desc': '作业预览与编辑页，左侧预览作业内容，右侧配置基础信息和页面装修',
        'keywords': ['作业管理', '作业预览与编辑', '基础信息', '作业名称', '学科', '学段',
                     '作业类型', '作业装修', '页面边距', '上边距', '下边距', '左边距',
                     '右边距', '内容间距', '题型间距', '题目间距', '作业题目数',
                     '清空题目', '发布作业', '题库选题', '展开答案', '配置作答区域'],
    },
    {
        'key': 'mastery',
        'path': os.path.join(ROOT, 'storage', 'uploads',
                             'a3826bd0-0a0e-4741-b623-39dd23fa698f.png'),
        'desc': '学生知识点掌握情况弹窗，顶部三张指标卡，下方题目明细表',
        'keywords': ['掌握程度', '掌握等级', '关联题目数', '题干', '来源/关联作业',
                     '正确/错误', '操作', '查看作答情况', '新开页面',
                     '跳转人工校验页面', '掌握薄弱'],
    },
]


def path_for(key, variant):
    return os.path.join(OUT_DIR, '%s.%s.json' % (key, variant))


def save(key, variant, payload):
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(path_for(key, variant), 'w', encoding='utf-8') as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    print('   -> %s  %d 条  %.1fs' % (path_for(key, variant),
                                     len(payload.get('cases') or []),
                                     payload.get('secs') or 0))


def load(key, variant):
    try:
        with open(path_for(key, variant), encoding='utf-8') as handle:
            return json.load(handle)
    except FileNotFoundError:
        return None


# ---------------------------------------------------------------- 三条路线

def run_a(generator, page):
    """现状：分析模型拆模块 → 每个模块各调一次生成模型。"""
    started = time.time()
    modules = generator.analyze_images([page['path']], page['desc'])
    analyze_secs = time.time() - started
    print('  A 分析 %.1fs 拆出 %d 个模块：%s'
          % (analyze_secs, len(modules), [m.get('module') for m in modules]))

    cases, per_module = [], []
    for module in modules:
        each = time.time()
        try:
            got = generator.generate_from_modules([module], TYPES, PER_MODULE, page['desc'])
        except TextError as exc:
            print('     模块「%s」生成失败：%s' % (module.get('module'), str(exc)[:80]))
            got = []
        per_module.append({'module': module.get('module'), 'count': len(got),
                           'secs': round(time.time() - each, 1)})
        cases.extend(got)
        print('     %-14s %2d 条  %.1fs'
              % (str(module.get('module'))[:14], len(got), time.time() - each))

    return {'variant': 'A_dual', 'model': MODEL, 'modules': modules,
            'per_module': per_module, 'cases': cases,
            'secs': round(time.time() - started, 1),
            'analyze_secs': round(analyze_secs, 1)}


def run_b(generator, page, count):
    started = time.time()
    cases = generator.generate(page['path'], TYPES, count, page['desc'])
    return {'variant': 'B_single', 'model': MODEL, 'count_arg': count,
            'cases': cases, 'secs': round(time.time() - started, 1)}


REVIEW_SYSTEM = """你是一位资深测试架构师，负责评审并完善一份 UI 测试用例。

给你一份已有用例和原始 UI 设计图，请完成：
1. 对照设计图找出被漏掉的测试点（未覆盖的控件、状态、跳转、边界）
2. 删除或合并重复的用例
3. 修正不可验证的预期（「正常显示」这类要改成能判定的结果）
4. 补齐缺失字段，步骤要能被人照着执行（写清点哪个按钮、填什么、看哪里）

输出仍是同一个 JSON 数组，不要解释，不要 Markdown 围栏。字段保持不变：
module / test_point / title / priority / preconditions / steps / expected / case_type

module 必须填页面上对应的功能区域名（例如「账号密码登录表单」「忘记密码」），
不同区域的用例不要共用同一个 module。"""


def run_c(generator, page, draft, base, variant='C_reflect'):
    started = time.time()
    payload = json.dumps(draft, ensure_ascii=False)
    prompt = ('## 已有用例（共 %d 条）\n```json\n%s\n```\n\n'
              '设计图上的每个控件、状态和跳转都要有用例覆盖，缺的补上。'
              '请按要求和设计图核对后输出最终版本。' % (len(draft), payload))
    cases = generator._ask_json(REVIEW_SYSTEM, prompt, images=[page['path']])
    from app.services.case_generator import _normalise
    return {'variant': variant, 'model': MODEL, 'from_count': len(draft),
            'base': base, 'cases': _normalise(cases),
            'secs': round(time.time() - started, 1)}


def run_d(generator, page, modules, total):
    """两阶段，但每个阶段只调一次：拆完模块后把全部模块一起喂进去。

    和 A 的差别只在「生成调用次数」：A 是每模块一次（8 次），D 是一次（2 次总数）。
    """
    started = time.time()
    cases = generator.generate_from_modules(modules, TYPES, total, page['desc'])
    return {'variant': 'D_batch', 'model': MODEL, 'modules': modules,
            'count_arg': total, 'cases': cases,
            'secs': round(time.time() - started, 1)}


# ---------------------------------------------------------------- 指标

STEP_VERBS = ['点击', '输入', '选择', '勾选', '滑动', '上传', '打开', '进入', '切换',
              '拖拽', '粘贴', '刷新', '关闭', '取消', '确认', '提交', '返回']
EXPECT_WORDS = ['显示', '提示', '跳转', '出现', '置灰', '禁用', '清空', '保存', '校验',
                '报错', '成功', '失败', '不可', '不能', '无法', '等于', '包含', '默认',
                '高亮', '排序', '新增', '删除']


def bigrams(text):
    text = ''.join(ch for ch in (text or '') if ch.strip())
    return {text[i:i + 2] for i in range(len(text) - 1)} or {text}


def similarity(left, right):
    a, b = bigrams(left), bigrams(right)
    return len(a & b) / float(len(a | b) or 1)


def metrics(payload, page):
    cases = payload.get('cases') or []
    total = len(cases) or 1
    fields = ('module', 'test_point', 'title', 'priority', 'preconditions',
              'steps', 'expected', 'case_type')
    missing = sum(1 for c in cases if not all(c.get(f) for f in fields))

    steps_counts, concrete, verifiable, keyword_hits = [], 0, 0, set()
    titles, types, prios = [], {}, {}
    for case in cases:
        steps = [s for s in (case.get('steps') or []) if str(s).strip()]
        steps_counts.append(len(steps))
        joined = ' '.join(str(s) for s in steps)
        if any(v in joined for v in STEP_VERBS):
            concrete += 1
        expected = str(case.get('expected') or '')
        if len(expected) >= 8 and any(w in expected for w in EXPECT_WORDS):
            verifiable += 1
        titles.append(str(case.get('title') or ''))
        types[str(case.get('case_type') or '?')] = types.get(str(case.get('case_type') or '?'), 0) + 1
        prios[str(case.get('priority') or '?')] = prios.get(str(case.get('priority') or '?'), 0) + 1
        for word in page['keywords']:
            if word in joined or word in str(case.get('title') or '') \
                    or word in str(case.get('expected') or ''):
                keyword_hits.add(word)

    dup_pairs = 0
    for i in range(len(titles)):
        for j in range(i + 1, len(titles)):
            if similarity(titles[i], titles[j]) > 0.75:
                dup_pairs += 1

    modules = {str(c.get('module') or '?') for c in cases}
    return {
        'variant': payload.get('variant'),
        'count': len(cases),
        'secs': payload.get('total_secs') or payload.get('secs'),
        'fields_missing': missing,
        'steps_avg': round(statistics.mean(steps_counts), 1) if steps_counts else 0,
        'steps_lt2': sum(1 for n in steps_counts if n < 2),
        'step_concrete_pct': round(100.0 * concrete / total, 1),
        'expected_verifiable_pct': round(100.0 * verifiable / total, 1),
        'dup_pairs': dup_pairs,
        'modules': len(modules),
        'keywords_hit': '%d/%d' % (len(keyword_hits), len(page['keywords'])),
        'types': types,
        'prios': prios,
    }


# ---------------------------------------------------------------- 主流程

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', default='', choices=['', 'ab', 'c', 'd'])
    parser.add_argument('--only', default='')
    parser.add_argument('--base', default='B_ten', choices=['B_ten', 'B_single'])
    parser.add_argument('--tag', default='', help='给自省结果加后缀，便于留两版对比')
    parser.add_argument('--report', action='store_true')
    args = parser.parse_args()

    if args.report:
        report()
        return

    resolved = models_config.resolve(MODEL)
    if not resolved:
        raise SystemExit('模型不存在：%s' % MODEL)
    print('模型 %s  vision=%s' % (resolved.ref, resolved.supports_vision))
    generator = CaseGenerator(resolved)

    pages = [p for p in PAGES if not args.only or p['key'] == args.only]

    for page in pages:
        print('\n=== %s ===' % page['key'])
        if args.phase == 'ab':
            if load(page['key'], 'A_dual') is None:
                save(page['key'], 'A_dual', run_a(generator, page))
            else:
                print('  A 已有结果，跳过')
            a = load(page['key'], 'A_dual')
            # 单次请求的产量有硬上限：homework 页按 A 的 88 条等量请求直接 300s 超时。
            # 所以等量对比封顶 30 条，超过的部分单独说明。
            total = min(len(a.get('cases') or []) or 10, 30)
            if load(page['key'], 'B_single') is None:
                save(page['key'], 'B_single', run_b(generator, page, total))
            else:
                print('  B 已有结果，跳过')
            if load(page['key'], 'B_ten') is None:
                save(page['key'], 'B_ten', run_b(generator, page, 10))
            else:
                print('  B10 已有结果，跳过')
        elif args.phase == 'c':
            draft = load(page['key'], args.base)
            if not draft:
                print('  先跑 --phase ab')
                continue
            variant = 'C_reflect' + (('_' + args.tag) if args.tag else '')
            if load(page['key'], variant) is not None:
                print('  C 已有结果，跳过')
                continue
            try:
                save(page['key'], variant,
                     run_c(generator, page, draft['cases'], args.base, variant))
            except TextError as exc:
                print('  C 失败：%s' % str(exc)[:120])
        elif args.phase == 'd':
            a = load(page['key'], 'A_dual')
            if not a:
                print('  先跑 --phase ab')
                continue
            if load(page['key'], 'D_batch') is not None:
                print('  D 已有结果，跳过')
                continue
            total = min(len(a.get('cases') or []) or PER_MODULE * len(a.get('modules') or [1]), 40)
            try:
                save(page['key'], 'D_batch', run_d(generator, page, a['modules'], total))
            except TextError as exc:
                print('  D 失败：%s' % str(exc)[:120])


def report():
    rows = []
    order = ('A_dual', 'B_single', 'B_ten', 'D_batch', 'C_reflect', 'C_reflect_module',
             'E_same_ctx', 'E_same_attach', 'E_same_attach_ext')
    for page in PAGES:
        for variant in order:
            payload = load(page['key'], variant)
            if payload:
                label = variant
                if variant.startswith('B_'):
                    label = 'B_%d条' % len(payload.get('cases') or [])
                rows.append((page['key'], label, metrics(payload, page)))

    header = ('page     variant     count  secs   miss  steps  lt2  conc%  verif%  dup  mod  kw')
    print(header)
    print('-' * len(header))
    for key, label, m in rows:
        print('%-8s %-11s %5d %6s %5d %6s %4d %6s %7s %4d %4d  %s'
              % (key, label, m['count'], m['secs'], m['fields_missing'],
                 m['steps_avg'], m['steps_lt2'], m['step_concrete_pct'],
                 m['expected_verifiable_pct'], m['dup_pairs'], m['modules'],
                 m['keywords_hit']))

    print('\n类型 / 优先级分布')
    for key, label, m in rows:
        print('%-8s %-11s %s  %s' % (key, label, m['types'], m['prios']))


if __name__ == '__main__':
    main()
