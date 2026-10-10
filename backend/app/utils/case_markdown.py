"""Parse and normalise test cases written as Markdown for the cases-driven automation mode.

Input format (one `####` heading per case):

    **Seed:** tests/seed.spec.ts

    #### 用例标题
    - 优先级：P0
    - 模块：登录页
    - 测试点：验证码校验
    - 前置条件：
      1. 打开登录页
    - 步骤：
      1. 输入正确用户名
    - 预期：
      - 提示"验证码错误"

Labels such as `步骤：` and `预期：` are stripped here, so the generator only sees plain content.
"""
import re

PRIORITIES = ('P0', 'P1', 'P2', 'P3')

_SEED_RE = re.compile(r'^\*\*Seed:\*\*\s*`?([^`\n]+?)`?\s*$', re.M)
_HEADING_RE = re.compile(r'^####\s+(.+?)\s*$')
_TITLE_SUFFIX_RE = re.compile(r'\s*#P[0-3]\s*$')
_LIST_MARK_RE = re.compile(r'^(?:\d+[.、]|[-*])\s*')
_FIELD_RE = re.compile(r'^[-*]\s*(优先级|模块|测试点|前置条件|步骤|预期)[：:]\s*(.*)$')
_LABEL_PREFIX_RE = re.compile(r'^(?:步骤|预期|前置条件|模块|测试点|优先级)[：:]\s*')


def _clean_item(text):
    # 先去列表序号再去标签：`1. 步骤：xxx` 的标签在序号之后，顺序反了就去不掉。
    text = _LIST_MARK_RE.sub('', text.strip())
    text = _LABEL_PREFIX_RE.sub('', text)
    return text.strip()


def parse_cases(markdown):
    """Return (seed_file, cases, errors). Each case is a dict with the TestCase field names."""
    errors = []
    text = (markdown or '').replace('\r\n', '\n')

    seed_match = _SEED_RE.search(text)
    seed_file = seed_match.group(1).strip() if seed_match else ''
    if not seed_file:
        errors.append('缺少 **Seed:** 行，例如 **Seed:** tests/seed.spec.ts')
    elif not seed_file.endswith('.spec.ts'):
        errors.append('Seed 必须是 .spec.ts 文件')

    blocks = []
    current = None
    for line in text.split('\n'):
        heading = _HEADING_RE.match(line)
        if heading:
            current = {'title': _TITLE_SUFFIX_RE.sub('', heading.group(1)).strip(), 'lines': []}
            blocks.append(current)
        elif current is not None:
            current['lines'].append(line)

    cases = []
    for index, block in enumerate(blocks, 1):
        case = {'title': block['title'], 'priority': 'P2', 'module': '', 'test_point': '',
                'preconditions': [], 'steps': [], 'expected': []}
        section = None
        for raw in block['lines']:
            if not raw.strip():
                continue
            field = _FIELD_RE.match(raw)
            if field:
                label, value = field.group(1), field.group(2).strip()
                section = label
                if label == '优先级':
                    case['priority'] = value.upper()
                elif label in ('模块', '测试点'):
                    case['module' if label == '模块' else 'test_point'] = value
                elif value:
                    case[_SECTION_KEYS[label]].append(value)
                continue
            if section in ('前置条件', '步骤', '预期'):
                item = _clean_item(raw)
                if item:
                    case[_SECTION_KEYS[section]].append(item)

        if not case['title']:
            errors.append(f'第 {index} 条用例缺少标题')
            continue
        if case['priority'] not in PRIORITIES:
            errors.append(f'「{case["title"]}」优先级必须是 P0–P3，当前为 {case["priority"]}')
        if not case['steps']:
            errors.append(f'「{case["title"]}」缺少步骤')
        if not case['expected']:
            errors.append(f'「{case["title"]}」缺少预期结果')
        cases.append(case)

    if not cases and not errors:
        errors.append('没有找到任何用例，每条用例需要以 #### 开头的标题')
    return seed_file, cases, errors


_SECTION_KEYS = {'前置条件': 'preconditions', '步骤': 'steps', '预期': 'expected'}


def render_cases(seed_file, cases):
    """Render normalised cases back to the canonical Markdown the generator reads."""
    lines = [f'**Seed:** `{seed_file}`', '']
    for case in cases:
        lines.append(f'#### {case["title"]}')
        lines.append(f'- 优先级：{case["priority"]}')
        if case.get('module'):
            lines.append(f'- 模块：{case["module"]}')
        if case.get('test_point'):
            lines.append(f'- 测试点：{case["test_point"]}')
        if case.get('preconditions'):
            lines.append('- 前置条件：')
            lines.extend(f'  {i}. {item}' for i, item in enumerate(case['preconditions'], 1))
        lines.append('- 步骤：')
        lines.extend(f'  {i}. {item}' for i, item in enumerate(case['steps'], 1))
        lines.append('- 预期：')
        lines.extend(f'  - {item}' for item in case['expected'])
        lines.append('')
    return '\n'.join(lines)
