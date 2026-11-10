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
    'reports', 'results', 'screenshots', 'screenshot', 'htmlcov', 'logs',
    'test-results', 'playwright-report', 'allure-results', 'allure-report',
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
# 扫描阶段就丢掉的后缀：不只是「不挑成关键文件」，而是连文件计数都不算。
# 一个 8000 张截图的目录足以吃满 MAX_FILES，把后面的业务目录全挤出去；
# 额度一旦被噪声占满，地图就只剩噪声。实测：加这份名单前，一个 Robot 项目
# 只扫到 3 个目录（其中两个是 screenshots / libspecs），四个中文业务目录一个没进。
SCAN_SKIP_SUFFIXES = frozenset(NOISE_SUFFIXES) | frozenset({
    '.html', '.htm', '.xml', '.jsonl', '.zip', '.gz', '.7z', '.rar', '.exe', '.dll',
    '.so', '.dylib', '.pyd', '.pyc', '.pyo', '.class', '.jar', '.whl', '.pdf',
    '.xls', '.xlsx', '.doc', '.docx', '.ppt', '.pptx', '.mp4', '.mp3', '.avi', '.mov',
    '.db', '.sqlite', '.sqlite3', '.bin', '.dat', '.pkl', '.npy', '.npz',
})
# 单个目录最多收多少条文件路径。不设的话，一个平铺上万文件的目录会一次吃光
# MAX_FILES，同级的其它目录就永远排不进地图。
PER_DIR_FILES = 120


def _gitignore_rules(root):
    """把根目录 .gitignore 里能读懂的规则翻成三份集合：目录名、后缀、路径前缀。

    只做「跳过」，不做反向还原（`!important.log` 之类直接忽略）：地图少记一条无关紧要，
    把作者明确排掉的东西又扫进来才是本末倒置。覆盖 `dir/`、`a/b`、`*.log`、
    `output/*` 四种常见写法，复杂正则交给静态名单兜底。
    """
    dirs, suffixes, prefixes = set(), set(), set()
    try:
        text = (Path(root) / '.gitignore').read_text(encoding='utf-8', errors='replace')
    except OSError:
        return dirs, suffixes, prefixes
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or line.startswith('!'):
            continue
        line = line.replace(chr(92), '/').strip('/')
        if not line or line in ('*', '**') or line.startswith('..'):
            continue
        if line.startswith('*.'):
            suffixes.add(line[1:].lower())
            continue
        if '*' in line or '?' in line:
            head = line.split('*')[0].split('?')[0].strip('/')
            if head and not any(ch in head for ch in '[]'):
                prefixes.add(head.lower())
            continue
        if '/' in line:
            prefixes.add(line.lower())
        else:
            dirs.add(line.lower())
    return dirs, suffixes, prefixes


def _pick_dirs(raw):
    """从扫到的目录里挑出要写进地图的那批。

    顶层结构永远保留——一级目录就是「这个项目有什么」；更深的只留有代码文件的，
    空目录对写手没有信息量。超上限时按「文件多的优先」，否则深层业务目录会被
    字典序靠前的无关目录挤掉：中文目录名恰好排在最后，最容易受害。
    """
    top = [item for item in raw if item['depth'] <= 1]
    deep = [item for item in raw if item['depth'] > 1 and item.get('files')]
    if len(top) + len(deep) > MAX_DIRS:
        room = max(0, MAX_DIRS - len(top))
        deep = sorted(deep, key=lambda d: (-d['files'], d['depth'], d['path']))[:room]
    keep = top + deep
    keep.sort(key=lambda d: (d['depth'], d['path']))
    return keep


def scan(root):
    """扫目录：三层目录 + 每层直接子文件数 + 四层以内的文件路径（用于挑关键文件）。

    不读文件内容，所以对任何大小的工作区都是几十毫秒级。三条保护都为了「别让噪声吃掉额度」：
    跳过名单合并项目自己的 .gitignore；文件计数只算代码类文件；每个目录最多贡献 PER_DIR_FILES 条。
    """
    root = Path(root)
    git_dirs, git_suffixes, git_prefixes = _gitignore_rules(root)
    skip_dirs = SKIP_DIRS | git_dirs
    dirs, files = [], []
    total = 0

    def ignored(rel_path, name):
        if Path(name).suffix.lower() in git_suffixes:
            return True
        lowered = rel_path.lower()
        return any(lowered == prefix or lowered.startswith(prefix + '/')
                   for prefix in git_prefixes)

    try:
        walker = os.walk(root)
        for current, dirnames, filenames in walker:
            rel = Path(current).relative_to(root)
            parts = () if str(rel) == '.' else rel.parts
            depth = len(parts)
            base = '/'.join(parts)
            # 剪枝：跳过依赖/产物/项目自己声明的忽略项，也别进隐藏目录
            #（.autocase 是我们自己的东西）。
            dirnames[:] = sorted(
                d for d in dirnames
                if d.lower() not in skip_dirs and not d.startswith('.')
                and not ignored(('%s/%s' % (base, d)).strip('/'), d)
            )
            # 隐藏文件一律不进地图，除了 .env.example 这种「只有结构没有凭据」的样例。
            here = []
            for name in filenames:
                if name.startswith('.') and name not in NAMED_KEY_FILES:
                    continue
                if Path(name).suffix.lower() in SCAN_SKIP_SUFFIXES:
                    continue
                if ignored(('%s/%s' % (base, name)).strip('/'), name):
                    continue
                here.append(name)
            total += len(here)
            if depth <= SCAN_DEPTH:
                dirs.append({'path': base, 'depth': depth,
                             'name': parts[-1] if parts else '', 'files': len(here)})
            if depth <= SCAN_DEPTH + 1:
                files.extend('/'.join(parts + (name,)) for name in here[:PER_DIR_FILES])
            if depth >= SCAN_DEPTH + 1:
                dirnames[:] = []
            # 单目录限额已经挡住「一个目录吃满」，这里只做极端情况的兜底。
            if len(dirs) >= MAX_DIRS * 4 or len(files) >= MAX_FILES:
                break
    except OSError as exc:
        logger.info('项目地图扫描中断：%s', exc)
    return {'dirs': _pick_dirs(dirs), 'files': files, 'total': total}


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
    skip_dirs = SKIP_DIRS | _gitignore_rules(root)[0]
    for entry in entries:
        if entry.name.startswith('.') and entry.name != '.env.example':
            continue
        if entry.is_dir():
            if entry.name.lower() in skip_dirs:
                continue
            try:
                count = sum(1 for _ in os.scandir(entry.path))
            except OSError:
                count = -1
            parts.append('%s=%d' % (entry.name, count))
        else:
            parts.append(entry.name)
    return ','.join(parts)[:2000]
