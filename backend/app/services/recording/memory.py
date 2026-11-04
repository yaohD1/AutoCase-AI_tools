"""项目画像：把「这个项目怎么写测试」一次读清楚，之后每次生成直接拿来用。

没有画像时，规划和写手都得现场读项目：规划只拿到几个路径（见 emit._project_facts），
写手拿到的是「最近改过的前 60 行」——60 行常常只有 import 和 describe 开头，
helper 封装、fixture、断言习惯全在后面。于是每次生成都在重新发现同一个项目，
慢、不确定，而且同一处地方不同轮次能给出不同写法。

画像是**只读 agent 读一次、写进库、反复用**：key 是工作区路径，带项目指纹。
指纹没变就直接复用；变了在后台重学，不阻塞这一次生成。

三条底线：
- 学不到就认输。schema 里读不到的字段留空，绝不用「通用最佳实践」补——补出来的
  东西会被当成项目惯例注入，比没有画像更有害。
- 失败不影响生成。学画像是锦上添花，任何异常都吞掉，只落一条 failed 记录给界面看。
- 画像要能被删。模型学歪了没有擦除通道，用户就只能关掉整个功能。
"""
import hashlib
import json
import logging
import re
import subprocess
import threading
from datetime import datetime
from pathlib import Path

from app.models.database import db
from app.models.project_memory import ProjectMemory
from app.services.recording import projectmap

logger = logging.getLogger(__name__)

# 画像会整份进提示词，必须有硬上限。超了宁可截断，也不能一次生成塞进去 5 万字符。
MAX_PROFILE_BYTES = 12 * 1024
MAX_SNIPPET_CHARS = 4000
MAX_TEXT = 400
MAX_HELPERS = 8
MAX_FIXTURES = 6
MAX_ASSERTIONS = 12
LEARN_TIMEOUT = 300
# 录制一开始就学，但先让录制自己把浏览器拉起来——同时抢 CPU 会拖慢首帧采集。
START_DELAY = 6
# 关键文件：改动它们基本等于「项目写法可能变了」。
KEY_FILES = (
    'package.json', 'package-lock.json', 'pnpm-lock.yaml', 'yarn.lock',
    'tsconfig.json', 'playwright.config.ts', 'playwright.config.js',
    'requirements.txt', 'pyproject.toml',
)

PROFILE_KEYS = (
    'framework', 'runner', 'layout', 'naming', 'case_shape',
    'helpers', 'fixtures', 'assertions', 'waits', 'imports',
    'snippet', 'areas', 'read_files', 'confidence',
)
# areas.kind 的取值：渲染成地图时按这个顺序分组。
AREA_KINDS = ('tests', 'pages', 'support', 'config', 'other')

PROFILE_PROMPT = (
    '你在给一个已有的测试项目做「画像」：把它的写法和约定总结成 JSON，'
    '供之后生成测试脚本时直接使用。你没有写权限，只读。\n'
    '\n'
    '要读出来的东西（**只写你亲眼在文件里读到的**；读不到就填 null 或空数组，'
    '绝对不要按通用最佳实践猜——猜出来的会被当成项目惯例使用，比没有更糟）：\n'
    '1. runner：测试目录、运行命令、配置文件路径。从 package.json 的 scripts、'
    'playwright.config.* 的 testDir / baseURL 里读，不要凭经验填。\n'
    '2. layout：用例文件放在哪、目录怎么分（按业务模块？按页面？平铺？），一句话。\n'
    '3. naming：文件名与用例标题的命名习惯，观察 2~3 个真实文件后总结。\n'
    '4. case_shape：一条用例的骨架——用 test 还是 test.describe、用 fixture 还是 beforeEach、'
    '有没有 page object、有没有统一的 beforeEach 登录。一句话 + 一小段结构示意。\n'
    '5. helpers：项目里现成、生成时应该直接调用的封装（登录、跳转、等待某个弹窗等）。'
    '每项给 name / file / signature / use；signature 必须能照着调用（参数名和默认值照抄）。\n'
    '6. fixtures：自定义 fixture 的名字与类型。\n'
    '7. assertions：项目里实际出现过的断言方法，去重，只列真实存在的。\n'
    '8. waits：这个项目怎么处理等待（有没有禁用 waitForTimeout 的约定、跳转后等什么）。\n'
    '9. imports：import 的来源习惯（@playwright/test、相对路径、路径别名）。\n'
    '10. snippet：**一条最有代表性的真实用例**，原样抄下来，最多 60 行。'
    '这段会被当成范例给写手看，必须逐字来自文件：不要改写、不要加省略号、不要补注释、'
    '不要挑只写了两行的空壳。\n'
    '11. areas：项目地图。给每个重要目录或文件写一句它是干什么的，供写手判断「该把文件放哪、'
    '该复用谁」。kind 只能是 tests / pages / support / config / other；path 必须和下面'
    '「脚本扫到的项目结构」里的路径**逐字一致**，不要自己拼；role 不超过 40 字；'
    'pages 和 support 另外给 use——生成代码时具体怎么调用它（要先 new 吗、要传什么），'
    '不超过 60 字。最多 16 条，宁缺毋滥，没有把握的路径不要写。\n'
    '\n'
    '预算：最多读 8 个文件、6 轮工具调用。优先顺序：playwright.config.* / package.json → '
    '2 个用例 → helper / fixture 文件。够了就停，不要为了完整性翻遍目录。\n'
    '目录结构已经由脚本扫好放在下面了：**不要再为此 ls / glob / 遍历目录**，'
    '那些轮次省下来读真正的文件内容。\n'
    '只输出一个 JSON 对象，不要代码块、不要解释文字。键名严格按下面的结构：\n'
    '{\n'
    '  "framework": "playwright-ts 或 robot-selenium",\n'
    '  "runner": {"test_dir": "", "command": "", "config": "", "base_url": ""},\n'
    '  "layout": "", "naming": "", "case_shape": "",\n'
    '  "helpers": [{"name": "", "file": "", "signature": "", "use": ""}],\n'
    '  "fixtures": [{"name": "", "file": "", "signature": ""}],\n'
    '  "assertions": [""], "waits": "", "imports": "",\n'
    '  "snippet": {"file": "", "code": ""},\n'
    '  "areas": [{"path": "", "kind": "tests|pages|support|config|other", "role": "", "use": ""}],\n'
    '  "read_files": [""], "confidence": "high|medium|low"\n'
    '}\n'
)


# ---- 指纹：判断「还准不准」 ----

def _git_head(root):
    try:
        result = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'],
                                capture_output=True, text=True, encoding='utf-8',
                                errors='replace', timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ''
    return result.stdout.strip() if result.returncode == 0 else ''


def fingerprint(workspace, framework=None):
    """项目指纹：git HEAD + 关键文件大小 + 用例规模。

    故意不用 mtime：切分支、重新 checkout 会让 mtime 全变，但内容一样，白白重学一次。
    HEAD 变了说明真改过；HEAD 没变时用文件大小和用例数量兜住「改了但没提交」。
    """
    root = Path(workspace)
    parts = [_git_head(root)]
    for name in KEY_FILES:
        path = root / name
        try:
            parts.append('%s:%d' % (name, path.stat().st_size))
        except OSError:
            continue
    try:
        from app.services.recording import detect
        entries = detect.recent_test_paths(root, robot=framework == 'robot-selenium', limit=50)
        newest = 0
        for entry in entries:
            try:
                newest = max(newest, int((root / entry['path']).stat().st_mtime))
            except OSError:
                continue
        parts.append('tests:%d:%d' % (len(entries), newest))
    except Exception:  # noqa: BLE001 - 指纹算不细也还能用，不该因此挡住生成
        logger.debug('项目指纹：用例扫描失败', exc_info=True)
    try:
        # 目录结构变了（新增一个模块目录、或某个目录里多了一批文件），地图就该重扫。
        parts.append('tree:' + projectmap.structure_signature(root))
    except Exception:  # noqa: BLE001
        logger.debug('项目指纹：结构扫描失败', exc_info=True)
    return hashlib.sha256('|'.join(parts).encode('utf-8')).hexdigest()[:32]


# ---- 存取 ----

def _key(workspace):
    return str(Path(workspace).resolve())


def row_for(workspace):
    return ProjectMemory.query.filter_by(workspace=_key(workspace)).first()


def load(workspace, framework=None):
    """取一份可用于注入的画像；没有就返回 None。

    返回 {'profile', 'stale', 'learned_at', 'model', 'framework'}。stale 表示
    项目指纹变了、这份画像可能已经不准——仍然注入（比没有强），同时由后台安排重学。
    """
    if not workspace:
        return None
    row = row_for(workspace)
    if row is None or row.status != 'ready' or not row.profile:
        return None
    profile = row.parsed()
    if not profile:
        return None
    stale = bool(row.fingerprint) and row.fingerprint != fingerprint(workspace, framework)
    return {
        'profile': profile,
        'map': row.map_md or '',
        'stale': stale,
        'learned_at': row.learned_at.isoformat() if row.learned_at else None,
        'model': row.model,
        'framework': row.framework,
    }


def forget(workspace):
    row = row_for(workspace)
    if row is None:
        return False
    db.session.delete(row)
    db.session.commit()
    return True


# ---- 清洗：把模型自由发挥的部分压回 schema ----

def _text(value, limit=MAX_TEXT):
    if not isinstance(value, str):
        return None
    value = ' '.join(value.split())
    return value[:limit] or None


def _dict_list(items, limit, fields, required):
    out = []
    for item in (items or [])[:limit]:
        if not isinstance(item, dict):
            continue
        entry = {}
        for field in fields:
            value = _text(item.get(field), 200)
            if value:
                entry[field] = value
        # name 是唯一不可缺的：没有名字的 helper 给写手看等于噪音。
        if entry.get(required):
            out.append(entry)
    return out


def _areas(items):
    """项目地图条目：路径必填，kind 不认识就归到 other。"""
    out = []
    for item in (items or [])[:projectmap.MAX_AREAS]:
        if not isinstance(item, dict):
            continue
        path = _text(item.get('path'), 200)
        if not path:
            continue
        path = path.replace('\\', '/').strip('/')
        kind = _text(item.get('kind'), 20)
        entry = {'path': path, 'kind': kind if kind in AREA_KINDS else 'other'}
        role = _text(item.get('role'), projectmap.MAX_ROLE)
        if role:
            entry['role'] = role
        use = _text(item.get('use'), projectmap.MAX_USE)
        if use:
            entry['use'] = use
        if path not in [o['path'] for o in out]:
            out.append(entry)
    return out


def _clean(raw, framework=None):
    """只留 schema 里的键，逐项截断。

    模型经常多塞几个自造字段（"best_practices"、"examples"），那些既没有人校验，
    又会随每轮生成变成新提示词——必须挡在入库这一步。
    """
    if not isinstance(raw, dict):
        return None
    data = {k: raw.get(k) for k in PROFILE_KEYS if raw.get(k) is not None}
    profile = {}
    profile['framework'] = _text(data.get('framework'), 40) or framework
    runner = data.get('runner')
    if isinstance(runner, dict):
        clean_runner = {k: _text(runner.get(k), 200) for k in ('test_dir', 'command', 'config', 'base_url')}
        clean_runner = {k: v for k, v in clean_runner.items() if v}
        if clean_runner:
            profile['runner'] = clean_runner
    for key in ('layout', 'naming', 'case_shape', 'waits', 'imports'):
        value = _text(data.get(key))
        if value:
            profile[key] = value
    helpers = _dict_list(data.get('helpers'), MAX_HELPERS,
                         ('name', 'file', 'signature', 'use'), 'name')
    if helpers:
        profile['helpers'] = helpers
    fixtures = _dict_list(data.get('fixtures'), MAX_FIXTURES,
                          ('name', 'file', 'signature'), 'name')
    if fixtures:
        profile['fixtures'] = fixtures
    assertions = [_text(item, 60) for item in (data.get('assertions') or [])[:MAX_ASSERTIONS]]
    assertions = [item for item in assertions if item]
    if assertions:
        profile['assertions'] = assertions
    snippet = data.get('snippet')
    if isinstance(snippet, dict):
        code = snippet.get('code')
        if isinstance(code, str) and len(code.strip()) >= 40:
            profile['snippet'] = {
                'file': _text(snippet.get('file'), 300),
                # 代码不压空白：缩进是它的一部分。
                'code': code[:MAX_SNIPPET_CHARS],
            }
    reads = [_text(item, 300) for item in (data.get('read_files') or [])[:12]]
    reads = [item for item in reads if item]
    if reads:
        profile['read_files'] = reads
    areas = _areas(data.get('areas'))
    if areas:
        profile['areas'] = areas
    profile['confidence'] = _text(data.get('confidence'), 10) or 'medium'
    # 除了 framework 什么都没有 = 这次没学到东西，不该覆盖上一份能用的画像。
    if not any(k in profile for k in ('runner', 'layout', 'case_shape', 'helpers', 'snippet')):
        return None
    return profile


def parse(text, framework=None):
    """从模型回复里抠画像。返回 (profile, 原因)。

    只取**第一个完整 JSON 对象**：模型经常在 JSON 后面再补一段「说明」，
    用「第一个 { 到最后一个 }」去切会把尾巴也切进来，直接变成 Extra data 解析失败——
    这种情况在真实项目上真发生过一次，画像白等了三分钟。
    """
    if not text:
        return None, '画像没有返回内容'
    fenced = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.S)
    candidate = fenced.group(1) if fenced else text
    start = candidate.find('{')
    if start < 0:
        return None, '画像回复里没有 JSON'
    try:
        data, _end = json.JSONDecoder().raw_decode(candidate[start:])
    except ValueError as exc:
        return None, '画像不是合法 JSON：%s' % exc
    profile = _clean(data, framework)
    if profile is None:
        return None, '画像里没有可用内容（runner / layout / case_shape / helpers / snippet 全空）'
    size = len(json.dumps(profile, ensure_ascii=False))
    if size > MAX_PROFILE_BYTES:
        return None, '画像过大（%d 字符）' % size
    return profile, None


# ---- 学 ----

def learn(runner, workspace, model, framework=None, force=False):
    """读一次项目、写一份画像。返回 {'learned': bool, 'reason'|'profile': ...}。

    调用方负责 app context（后台线程里由 worker 建）。runner 由外面传，测试可以塞假的。
    """
    root = _key(workspace)
    fp = fingerprint(root, framework)
    row = row_for(root)
    if not force and row is not None and row.status == 'ready' and row.fingerprint == fp:
        return {'learned': False, 'reason': 'unchanged'}

    if row is None:
        row = ProjectMemory(workspace=root)
        db.session.add(row)
    # 旧画像还在时不能因为一次刷新失败就把整份画像作废：这一轮生成还在用它，
    # 下个项目可能只是 git 暂时读不到。失败只记 error，状态退回 ready。
    had_profile = bool(row.profile) and row.status == 'ready'
    row.status = 'learning'
    row.model = model
    row.error = None
    db.session.commit()

    # 目录结构先由脚本扫出来：让模型在这棵准确的树上标注，而不是自己 ls 一遍再猜。
    # 扫不出来也要继续——少一段结构信息，画像仍然可用。
    try:
        tree = projectmap.scan(root)
    except Exception:  # noqa: BLE001
        logger.debug('项目地图扫描失败', exc_info=True)
        tree = {'dirs': [], 'files': [], 'total': 0}

    try:
        text = runner.emit_profile(
            root, model, PROFILE_PROMPT + '\n\n' + projectmap.prompt_block(tree),
            timeout=LEARN_TIMEOUT,
        )
        profile, error = parse(text, framework)
        if profile is None:
            raise ValueError(error)
    except Exception as exc:  # noqa: BLE001 - 学不会不影响生成
        logger.warning('项目画像学习失败：%s', exc)
        row.status = 'ready' if had_profile else 'failed'
        row.error = str(exc)[:400]
        row.updated_at = datetime.utcnow()
        db.session.commit()
        return {'learned': False, 'reason': str(exc)[:200]}

    row.status = 'ready'
    row.profile = json.dumps(profile, ensure_ascii=False)
    row.framework = profile.get('framework') or framework
    row.fingerprint = fp
    row.learned_at = datetime.utcnow()
    row.updated_at = datetime.utcnow()
    # 地图渲染失败不该让整份画像作废：画像本身已经能用了。
    try:
        map_text = projectmap.render(profile, tree, name=Path(root).name)
        row.map_md = map_text
        written = projectmap.write(root, map_text)
        if written:
            logger.info('项目地图已写入：%s', written)
    except Exception:  # noqa: BLE001
        logger.debug('项目地图渲染失败', exc_info=True)
    db.session.commit()
    logger.info('项目画像已更新：%s（%s 个区域 / %s 个 helper / 读了 %s 个文件）',
                root, len(profile.get('areas') or []), len(profile.get('helpers') or []),
                len(profile.get('read_files') or []))
    return {'learned': True, 'profile': profile}


_inflight = set()
_inflight_lock = threading.Lock()


def start_background(app, config, workspace, model, framework=None, force=False, delay=START_DELAY):
    """录制一开始就在后台学。同一个工作区同时只学一份，重复调用直接忽略。

    app 必须是真实对象（current_app._get_current_object()）：线程里没有请求上下文，
    要自己 app_context 才能写库。
    """
    if not workspace or '/' not in (model or ''):
        return False
    # 测试里不真调模型：路由测试会把 app 标成 TESTING，学习线程如果真跑起来，
    # 会一边调真实 provider 一边往库里写临时工作区的画像。
    if app.config.get('TESTING'):
        return False
    key = _key(workspace)
    with _inflight_lock:
        if key in _inflight:
            return False
        _inflight.add(key)

    def worker():
        try:
            if delay:
                threading.Event().wait(delay)
            with app.app_context():
                from app.services.opencode_runner import OpenCodeRunner
                try:
                    learn(OpenCodeRunner(config), workspace, model, framework, force=force)
                finally:
                    # 线程里开出来的会话不还回去，连接会一直挂在池子里。
                    db.session.remove()
        except Exception:  # noqa: BLE001 - 后台线程的异常没人接，只能记下来
            logger.warning('项目画像后台学习异常', exc_info=True)
        finally:
            with _inflight_lock:
                _inflight.discard(key)

    threading.Thread(target=worker, name='project-profile', daemon=True).start()
    return True


# ---- 注入：只给用得上的部分 ----

def for_plan(entry):
    """规划阶段要的：项目地图（放哪、复用谁、怎么写）。

    有地图就只给地图：地图本身就是下面那些字段的渲染结果，两份一起给等于同一段话说两遍，
    还会在截断边界上互相打架。没有地图（老记录、或渲染失败）才退回结构化字段。
    """
    entry = entry or {}
    if entry.get('map'):
        out = {'map': entry['map']}
        if entry.get('stale'):
            out['stale'] = True
        return out
    profile = entry.get('profile') or {}
    out = {}
    for key in ('framework', 'runner', 'layout', 'naming', 'case_shape', 'waits', 'imports'):
        if profile.get(key):
            out[key] = profile[key]
    if profile.get('helpers'):
        out['helpers'] = [
            {k: h[k] for k in ('name', 'signature', 'use', 'file') if h.get(k)}
            for h in profile['helpers'][:6]
        ]
    if profile.get('fixtures'):
        out['fixtures'] = [
            {k: f[k] for k in ('name', 'signature') if f.get(k)}
            for f in profile['fixtures'][:4]
        ]
    if (entry or {}).get('stale'):
        # 让模型知道这份画像可能过时：和读到的文件冲突时以文件为准。
        out['stale'] = True
    return out


def for_writer(entry):
    """写手要的：项目地图 + 一段真代码当骨架。

    地图里已经有断言清单和封装签名，所以不再重复给；真正缺的是「照着这个写」的那段原文。
    """
    out = for_plan(entry)
    profile = (entry or {}).get('profile') or {}
    if out.get('map'):
        if (profile.get('snippet') or {}).get('code'):
            out['snippet'] = profile['snippet']
        return out
    if profile.get('assertions'):
        out['assertions'] = profile['assertions']
    if (profile.get('snippet') or {}).get('code'):
        out['snippet'] = profile['snippet']
    return out


def snippet_sample(entry):
    """把画像里的范例转成 detect.samples 的形状，顶掉「最近改过的前 60 行」。"""
    snippet = ((entry or {}).get('profile') or {}).get('snippet') or {}
    if not (snippet.get('code') or '').strip():
        return None
    return {'path': snippet.get('file') or 'snippet', 'head': snippet['code'], 'generated': False}


# ---- 界面 ----

def snapshot(workspace, framework=None):
    """给前端看的一份摘要：学了什么、准不准、什么时候学的。"""
    if not workspace:
        return {'status': 'missing', 'stale': False, 'profile': None}
    row = row_for(workspace)
    if row is None:
        return {'status': 'missing', 'stale': False, 'profile': None}
    profile = row.parsed()
    # 「坑」这个字段已经废弃：它是让模型自己找「哪里容易写错」，结果常被当成写法建议照抄，
    # 反而把生成带到错误方向（实测会写出项目里并不存在的绕行做法）。老记录里还留着，这里剥掉。
    profile.pop('pitfalls', None)
    stale = bool(row.fingerprint) and row.fingerprint != fingerprint(workspace, framework)
    summary = {
        'test_dir': (profile.get('runner') or {}).get('test_dir'),
        'layout': profile.get('layout'),
        'naming': profile.get('naming'),
        'helpers': [h.get('name') for h in (profile.get('helpers') or []) if h.get('name')],
        'fixtures': [f.get('name') for f in (profile.get('fixtures') or []) if f.get('name')],
        'assertions': profile.get('assertions') or [],
        'areas': len(profile.get('areas') or []),
        'files': len(profile.get('read_files') or []),
        'confidence': profile.get('confidence'),
    }
    return {
        'status': row.status,
        'stale': stale,
        'learned_at': row.learned_at.isoformat() if row.learned_at else None,
        'model': row.model,
        'error': row.error,
        'framework': row.framework,
        'summary': summary,
        # 项目地图（markdown）。卡片直接显示它，不再拼字段。
        'map': row.map_md or '',
        'profile': profile,
    }
