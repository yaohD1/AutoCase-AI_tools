"""项目地图：静态扫出目录结构，再让模型标「每块是干什么的」。

为什么先静态扫：让模型自己 ls/glob 会漏、会猜、会按通用习惯补。而目录结构是客观的——
脚本走一遍（跳过 node_modules 与构建产物）就能拿到准确的两级树、每个目录的文件数、
以及哪些文件值得读。模型只在这棵树上写「这块负责什么」，错不到哪去，也好核对。

产出是一份 markdown：项目地图 + 写法约定 + 现成封装。它同时落到两个地方：

- `<workspace>/.autocase/project-map.md`：它本质是文档，能打开、能 diff、能跟着代码演进。
- 库里的 `project_memories.map_md`：卡片显示用，工作区换机器/没挂载时也看得到。

只记结构不记内容：地图里不会出现 .env 的值、账号密码，只有路径和一句职责。
"""
import logging
import os
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

MAP_REL = '.autocase/project-map.md'
# 记到三层：src/pages/router-admin 这种「模块下一层的实现目录」正好在第三层，
# 再深就是具体文件了，写手按路径自己找得到。
SCAN_DEPTH = 3
MAX_DIRS = 60
MAX_FILES = 4000
MAX_KEY_FILES = 36
MAX_ROLE = 140
MAX_USE = 180
MAX_AREAS = 24
# 地图会整份进提示词（规划用），必须有硬上限。
MAX_MAP_CHARS = 8000

SKIP_DIRS = frozenset({
    'node_modules', '.git', '.svn', '.hg', '.idea', '.vscode', 'dist', 'build', 'out',
    'coverage', '.next', '.nuxt', 'target', '__pycache__', '.venv', 'venv',
    'reports', 'test-results', 'playwright-report', 'allure-results', 'allure-report',
    'midscene_run', 'midscene_run_cache', '.playwright-mcp', '.cache', 'tmp', 'temp',
})

# 决定「怎么跑」的文件，永远排最前面。只认文件名，不认内容。
NAMED_KEY_FILES = (
    'package.json', 'playwright.config.ts', 'playwright.config.js', 'tsconfig.json',
    'README.md', 'AGENTS.md', '.env.example', 'pom.xml', 'requirements.txt',
)
# 这些后缀不算关键文件：产物与样式不该占名额。
NOISE_SUFFIXES = ('.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico', '.webp', '.css',
                  '.scss', '.less', '.lock', '.log', '.map', '.woff', '.woff2', '.ttf')


def scan(root):
    """扫目录：两层目录 + 每层直接子文件数 + 三层以内的文件路径（用于挑关键文件）。

    不读文件内容，所以对任何大小的工作区都是几十毫秒级。
    """
    root = Path(root)
    dirs, files = [], []
    total = 0
    try:
        walker = os.walk(root)
        for current, dirnames, filenames in walker:
            rel = Path(current).relative_to(root)
            parts = () if str(rel) == '.' else rel.parts
            depth = len(parts)
            # 剪枝：跳过依赖/产物，也别进隐藏目录（.autocase 是我们自己的东西）。
            dirnames[:] = sorted(d for d in dirnames
                                 if d not in SKIP_DIRS and not d.startswith('.'))
            # 隐藏文件一律不进地图，除了 .env.example 这种「只有结构没有凭据」的样例。
            here = [f for f in filenames if not f.startswith('.') or f in NAMED_KEY_FILES]
            total += len(here)
            if depth <= SCAN_DEPTH:
                dirs.append({'path': '/'.join(parts), 'depth': depth,
                             'name': parts[-1] if parts else '', 'files': len(here)})
            if depth <= SCAN_DEPTH + 1:
                files.extend('/'.join(parts + (name,)) for name in here)
            if depth >= SCAN_DEPTH + 1:
                dirnames[:] = []
            if len(dirs) >= MAX_DIRS or len(files) >= MAX_FILES:
                break
    except OSError as exc:
        logger.info('项目地图扫描中断：%s', exc)
    return {'dirs': dirs, 'files': files, 'total': total}


def _score(path):
    """关键文件排序：越可能是「写测试时要照着看/该复用」的，分越高。"""
    score = 0
    if '/' not in path and path in NAMED_KEY_FILES:
        score += 100
    if path.startswith('src/') or path.startswith('app/'):
        score += 10
    padded = '/' + path
    if '/pages/' in padded:
        score += 45
    if '/fixtures/' in padded:
        score += 40
    if '/helpers/' in padded or '/utils/' in padded or '/support/' in padded:
        score += 30
    if path.endswith(('.spec.ts', '.spec.js', '.robot')):
        score += 20
    if path.startswith(('configs/', 'config/')) or '/config' in path:
        score += 15
    return score


def key_files(scan_result):
    """从扫到的文件里挑出值得模型看一眼的那些，宁少勿多。"""
    picked = []
    for path in scan_result.get('files') or []:
        lowered = path.lower()
        if lowered.endswith(NOISE_SUFFIXES):
            continue
        if path.endswith(('.env', '.env.local', '.env.prod')):
            # 只要示例，不要真凭据进地图。
            continue
        picked.append(path)
    picked.sort(key=lambda p: (-_score(p), p))
    return picked[:MAX_KEY_FILES]


def prompt_block(scan_result):
    """给模型看的那段「目录事实」：先给树，再给关键文件清单。"""
    lines = ['# 脚本扫到的项目结构（客观事实，可以直接采信，不用再 ls / glob）', '']
    dirs = [item for item in scan_result.get('dirs') or [] if item.get('path')]
    if dirs:
        lines.append('## 目录（括号里是直接子文件数）')
        for item in dirs:
            if item['depth'] > SCAN_DEPTH:
                continue
            indent = '  ' * max(0, item['depth'] - 1)
            lines.append('%s%s (%d)' % (indent, item['path'], item['files']))
        lines.append('')
    names = key_files(scan_result)
    if names:
        lines.append('## 值得打开看的文件')
        lines.extend(names[:24])
        lines.append('')
    lines.append('标注 areas 时，路径必须和上面完全一致；没有出现在上面的路径不要写。')
    return '\n'.join(lines)


def _clip(value, limit):
    if not isinstance(value, str):
        return ''
    return ' '.join(value.split())[:limit]


def _pad(text, width):
    """中文按两个字宽算，否则列会对不齐。"""
    shown = sum(2 if ord(ch) > 127 else 1 for ch in text)
    return text + ' ' * max(0, width - shown)


def _width(items, cap=36):
    return min(cap, max([sum(2 if ord(c) > 127 else 1 for c in (i.get('path') or '')) + 2
                         for i in items] or [20]))


def render(profile, scan_result, name=''):
    """把画像 + 扫描结果渲染成一份 markdown。纯函数，方便测试。"""
    profile = profile or {}
    scan_result = scan_result or {}
    lines = ['# 项目地图 · %s' % (name or '当前项目'), '']
    lines.append('> 自动生成于 %s，依据 %d 个文件（已忽略 node_modules、构建与测试产物）。'
                 % (datetime.now().strftime('%Y-%m-%d %H:%M'), scan_result.get('total') or 0))
    lines.append('> 录制开始时按项目指纹自动刷新，手工修改会在下次刷新时被覆盖。')
    lines.append('')

    areas = [a for a in (profile.get('areas') or []) if a.get('path')]
    # 模型偶尔会自己拼一个看起来合理的路径。地图是给写手当依据的，编出来的路径比没有更糟，
    # 所以只在扫到的路径集合里保留（扫盘失败时 known 为空，那就都留着）。
    known = {d.get('path') for d in scan_result.get('dirs') or [] if d.get('path')}
    known |= set(scan_result.get('files') or [])
    if known and areas:
        kept = [a for a in areas if a['path'] in known]
        if len(kept) != len(areas):
            logger.info('项目地图：丢掉 %d 条不在扫描结果里的路径', len(areas) - len(kept))
        areas = kept
    if areas:
        titles = (('tests', '测试'), ('pages', '页面封装'), ('support', '支撑'),
                  ('config', '配置'), ('other', '其它'))
        for kind, title in titles:
            group = [a for a in areas if (a.get('kind') or 'other') == kind]
            if not group:
                continue
            width = _width(group)
            lines.append('## ' + title)
            for item in group:
                lines.append(_pad(item['path'], width).rstrip() if not item.get('role')
                             else '%s %s' % (_pad(item['path'], width), item['role']))
                if item.get('use'):
                    lines.append('%s 用法：%s' % (' ' * width, item['use']))
            lines.append('')
    else:
        # 模型没给 areas 时退回纯结构：至少把测试目录列出来，比空白强。
        tests = [d for d in scan_result.get('dirs') or []
                 if d.get('path', '').startswith('tests')]
        if tests:
            width = _width(tests)
            lines.append('## 测试')
            for item in tests:
                lines.append('%s %d 个文件' % (_pad(item['path'], width), item['files']))
            lines.append('')

    facts = []
    runner = profile.get('runner') or {}
    if runner.get('test_dir'):
        facts.append(('用例目录', runner['test_dir']))
    if runner.get('command'):
        facts.append(('跑法', runner['command']))
    if runner.get('config'):
        facts.append(('配置', runner['config']))
    for key, label in (('naming', '命名'), ('case_shape', '骨架'), ('waits', '等待'),
                       ('imports', '导入')):
        if profile.get(key):
            facts.append((label, profile[key]))
    if profile.get('assertions'):
        facts.append(('断言', '、'.join(profile['assertions'][:10])))
    if facts:
        lines.append('## 写法')
        lines.extend('- %s：%s' % (label, value) for label, value in facts)
        lines.append('')

    helpers = profile.get('helpers') or []
    if helpers:
        lines.append('## 现成封装')
        for helper in helpers:
            head = helper.get('signature') or helper.get('name')
            line = '- `%s`' % head
            if helper.get('use'):
                line += ' — %s' % helper['use']
            if helper.get('file'):
                line += '（%s）' % helper['file']
            lines.append(line)
        lines.append('')

    text = '\n'.join(lines).rstrip() + '\n'
    if len(text) > MAX_MAP_CHARS:
        text = text[:MAX_MAP_CHARS].rstrip() + '\n…（已截断）\n'
    return text


def write(root, text):
    """把地图写进工作区。写不进去只记一条日志——地图是锦上添花，不能因此打断学习。"""
    if not text:
        return None
    path = Path(root) / MAP_REL
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
    except OSError as exc:
        logger.info('项目地图写不进工作区（%s）：%s', path, exc)
        return None
    return MAP_REL


def read(root):
    """读工作区里的地图（卡片在库为空时用它兜底）。"""
    try:
        return (Path(root) / MAP_REL).read_text(encoding='utf-8')
    except OSError:
        return ''


def structure_signature(root):
    """只按结构算指纹：一级目录名 + 各自条目数。

    新增/删除一个目录、或某个目录里多了一批文件，都说明「地图该重扫了」；
    而不读内容、不递归，几十毫秒。切分支导致 mtime 全变不会误触发。
    """
    root = Path(root)
    parts = []
    try:
        entries = sorted(os.scandir(root), key=lambda e: e.name)
    except OSError:
        return ''
    for entry in entries:
        if entry.name.startswith('.') and entry.name != '.env.example':
            continue
        if entry.is_dir():
            if entry.name in SKIP_DIRS:
                continue
            try:
                count = sum(1 for _ in os.scandir(entry.path))
            except OSError:
                count = -1
            parts.append('%s=%d' % (entry.name, count))
        else:
            parts.append(entry.name)
    return ','.join(parts)[:2000]
