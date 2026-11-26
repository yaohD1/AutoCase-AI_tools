"""执行测试失败后的初步诊断：把现场交给模型，只产出结论和建议。

**这一层严格只读。** 不是没想过顺手改脚本——实测过：同一个失败（终端管理的定位失效），
模型能准确说出原因，甚至识破「后置处理掩盖了主错误」这种隐蔽问题；但它给出的补丁
真跑仍然是 FAIL。原因是失败截图里看得见「终端管理」四个字，看不见 DOM，于是它照着
兄弟菜单的写法造了一个形状正确的 XPath。给一个看起来对、其实错的定位，比不改更糟。
所以写回脚本要等「能看到真实 DOM」那套方案，这里只回答「为什么挂、怎么改」。

材料从三处取，缺一层就降级，不报错：
- `test_runs.results_json`：错误信息、失败位置、步骤树、截图路径（永远在）
- `artifacts/rf-N/output.xml`：RF 的 INFO 执行日志、失败关键字链（产物只留最近 20 次）
- 工作区里的脚本源码：用例块 + 失败链上的关键字（模型必须看到原文才能说「改哪一行」）
"""
import json
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

# 一次最多诊断几条。失败多的时候通常同一个根因（登录失效、站点挂了），
# 全量问一遍既慢又费钱；剩下的在界面上标「未诊断」。
MAX_CASES = 5
# 脚本片段上限。整份文件塞进去会让提示词涨到几万字符——上一轮生成实验已经证明
# 提示词越长越容易跑偏，这里只给相关块。
MAX_EXCERPT_LINES = 140
MAX_EXCERPT_CHARS = 9000
# 一条诊断实测 19～45 秒（带 JSON 结构要求偏慢），给足余量但不至于挂死。
ASK_TIMEOUT = 180

SYSTEM_PROMPT = (
    '你是自动化测试排障专家。根据一次失败运行的现场材料，判断失败原因并给出改法。\n'
    '材料包括：框架与环境、用例名与文件、失败位置、错误信息、执行日志尾部、脚本片段。\n'
    '\n'
    '硬规则：\n'
    '1. 只依据材料。材料里没有的信息（比如页面真实 DOM）不要猜。\n'
    '2. 判定为定位失效、但材料里没有该元素的真实定位时，fixes 必须写\n'
    '   「需要重新录制该步骤或人工核对页面结构」，after 留空。\n'
    '   **禁止编造看起来合理的 XPath**——实测这种补丁跑起来仍然失败，比不改更糟。\n'
    '3. 把「主错误」和「连带错误」分开：后置处理、收尾断言里的失败，通常是主流程没走到\n'
    '   的结果，不要当成独立问题报出来。\n'
    '4. 只输出 JSON，不要输出 JSON 以外的任何内容。\n'
    '\n'
    '字段：\n'
    '  category: 定位失效 / 环境不可达 / 前置条件缺失 / 断言过期 / 脚本实现错误 / 测试数据问题 / 其它\n'
    '  verdict: script（要改脚本）/ env（环境或站点）/ data（测试数据）/ unknown\n'
    '  reason: 一句话结论，不超过 40 字\n'
    '  detail: 判断依据，引用日志或脚本里的原文\n'
    '  evidence: 字符串数组，每条是材料里的一句原文（日志行或脚本行）\n'
    '  fixes: 数组，每项 {label, where, before, after, advice}\n'
    '      where 是位置（关键字名或用例名）；before 是原行，按原文保留缩进；\n'
    '      after 是建议写法，给不出就留空字符串；advice 是为什么这么改\n'
    '  confidence: high / medium / low\n'
)

CATEGORIES = ('定位失效', '环境不可达', '前置条件缺失', '断言过期',
              '脚本实现错误', '测试数据问题', '其它')
VERDICTS = ('script', 'env', 'data', 'unknown')

# 正在诊断的 run_id。同一份记录只允许一个线程在问，否则重诊会把结果互相覆盖。
_RUNNING = set()
_LOCK = threading.Lock()


class DiagnoseError(Exception):
    """诊断跑不起来。消息直接给用户看。"""


# ---------------------------------------------------------------- 对外入口

def start(app, run_id, indexes=None):
    """起一个后台线程做诊断，立刻返回当前快照。

    为什么异步：一条 20～45 秒，整批要几分钟，HTTP 请求扛不住这么久；
    而且 5 条里第 1 条出结果就能看了，不必等全部。
    """
    from app.models import db
    from app.models.test_run import TestRun

    with app.app_context():
        run = db.session.get(TestRun, run_id)
        if run is None:
            raise DiagnoseError('运行记录不存在')
        if run.status == 'running':
            raise DiagnoseError('运行还没结束，先等它跑完再诊断')
        targets = _targets(run.results(), indexes)
        if not targets:
            raise DiagnoseError('这次运行没有需要诊断的失败用例')
        model = _model()

        with _LOCK:
            if run_id in _RUNNING:
                # 已经在诊断了：不重复起线程，把现状给前端，让它继续轮询。
                return snapshot(run)
            _RUNNING.add(run_id)

        state = _load_state(run)
        state.update({'state': 'running', 'model': model,
                      'started_at': _now(), 'total_failed': len(targets),
                      'pending': list(targets)})
        state.setdefault('items', {})
        run.diagnoses_json = json.dumps(state, ensure_ascii=False)
        db.session.commit()
        data = snapshot(run)

    thread = threading.Thread(target=_work, args=(app, run_id, indexes),
                              daemon=True, name='autocase-diagnose')
    thread.start()
    return data


def snapshot(run):
    """给前端的状态。没诊断过时返回 idle，前端据此决定要不要显示入口。"""
    state = _load_state(run)
    if not state:
        return {'state': 'idle', 'items': []}
    items = state.get('items') or {}
    ordered = [items[key] for key in sorted(items, key=lambda k: int(k))]
    return {
        'state': state.get('state') or 'idle',
        'model': state.get('model') or '',
        'started_at': state.get('started_at'),
        'finished_at': state.get('finished_at'),
        'total_failed': state.get('total_failed') or 0,
        'pending': state.get('pending') or [],
        'items': ordered,
        'error': state.get('error') or '',
    }


# ---------------------------------------------------------------- 线程主体

def _work(app, run_id, indexes):
    from app.models import db
    from app.models.test_run import TestRun

    try:
        with app.app_context():
            run = db.session.get(TestRun, run_id)
            if run is None:
                return
            rows = run.results()
            workspace = Path(run.workspace)
            model = _model()
            targets = _targets(rows, indexes)
            for index in targets:
                item = _diagnose_one(run, index, rows[index], workspace, model)
                state = _load_state(run)
                state.setdefault('items', {})[str(index)] = item
                # pending 是「还没出结果的那几条」：前端据此只在对应行显示「正在分析」，
                # 而不是一点分析就把整页失败用例都标成进行中。
                state['pending'] = [left for left in targets if str(left) not in state['items']]
                # 一条一存：界面能边诊断边看到结论，中途挂了也不至于全丢。
                run.diagnoses_json = json.dumps(state, ensure_ascii=False)
                db.session.commit()
            state = _load_state(run)
            state['state'] = 'done'
            state['pending'] = []
            state['finished_at'] = _now()
            run.diagnoses_json = json.dumps(state, ensure_ascii=False)
            db.session.commit()
    except Exception as exc:  # noqa: BLE001 - 线程里的异常必须落到记录上，否则界面永远转圈
        _mark_failed(app, run_id, exc)
    finally:
        with _LOCK:
            _RUNNING.discard(run_id)


def _mark_failed(app, run_id, exc):
    from app.models import db
    from app.models.test_run import TestRun

    try:
        with app.app_context():
            run = db.session.get(TestRun, run_id)
            if run is None:
                return
            state = _load_state(run)
            state['state'] = 'failed'
            state['error'] = str(exc)[:300] or exc.__class__.__name__
            state['pending'] = []
            state['finished_at'] = _now()
            run.diagnoses_json = json.dumps(state, ensure_ascii=False)
            db.session.commit()
    except Exception:  # noqa: BLE001 - 兜底路径自己再挂就没救了，不要再抛
        pass


def _diagnose_one(run, index, row, workspace, model):
    started = time.monotonic()

    # 站点没连上的失败不用问模型：runner 已经判过，问一遍只会得到「检查网络」。
    if row.get('env_error'):
        return {
            'index': index, 'status': 'skipped', 'verdict': 'env',
            'category': '环境不可达', 'confidence': 'high',
            'reason': '目标站点没连上，先查网络或服务状态，与脚本无关',
            'detail': (row.get('error') or '')[:600],
            'evidence': [], 'fixes': [],
            'model': '', 'elapsed': 0,
        }

    try:
        packet = _packet(run, index, row, workspace)
    except DiagnoseError as exc:
        return {'index': index, 'status': 'skipped', 'reason': str(exc),
                'category': '', 'verdict': 'unknown', 'confidence': '',
                'detail': '', 'evidence': [], 'fixes': [], 'model': '', 'elapsed': 0}

    try:
        reply = _ask(packet, model)
    except Exception as exc:  # noqa: BLE001 - 单条失败不影响其他条
        return {'index': index, 'status': 'failed', 'reason': '模型调用失败：%s' % str(exc)[:200],
                'category': '', 'verdict': 'unknown', 'confidence': '',
                'detail': '', 'evidence': [], 'fixes': [], 'model': model,
                'elapsed': round(time.monotonic() - started, 1)}

    item = _normalize(reply)
    item.update({'index': index, 'status': 'done', 'model': model,
                 'elapsed': round(time.monotonic() - started, 1)})
    return item


def _ask(packet, model):
    from flask import current_app

    from app.services.oc_text import TextWorker

    text = json.dumps(packet, ensure_ascii=False)
    worker = TextWorker.shared(current_app.config)
    reply = worker.ask(model, SYSTEM_PROMPT, text, timeout=ASK_TIMEOUT)
    from app.services.oc_text import parse_json
    return parse_json(reply)


# ---------------------------------------------------------------- 诊断包

def _packet(run, index, row, workspace):
    """组装一条用例的诊断现场。任何取不到的材料都只是缺席，不阻断。"""
    framework = 'robot-selenium' if run.framework == 'robot-selenium' else 'playwright'
    rel_file = (row.get('file') or '').strip()
    script = _read_script(workspace, rel_file)
    error = (row.get('error') or '').strip()

    packet = {
        'framework': framework,
        'env': _env_text(run),
        'case': row.get('title') or '',
        'file': rel_file,
        'status': row.get('status') or '',
        'duration_ms': row.get('duration_ms') or 0,
        'error': error[:1500],
    }

    if framework == 'robot-selenium':
        chain, logs = _rf_failure(run, row)
        # 取第一条：RF 的 keyword 树是文档顺序，正文在前、Test Teardown 在后，
        # 所以第一个 FAIL 才是主错误，后面的多半是「主流程没走到」的连带失败。
        packet['failed_at'] = ' > '.join(chain[0]) if chain else ''
        packet['failed_chain'] = [' > '.join(path) for path in chain[:4]]
        packet['log_tail'] = logs
        packet['script'] = _rf_excerpt(script, row.get('title') or '', chain)
    else:
        packet['failed_at'] = row.get('where') or ''
        packet['log_tail'] = _pw_log(row)
        packet['script'] = _pw_excerpt(script, row.get('where') or '')

    if not error and not packet['log_tail']:
        raise DiagnoseError('这条用例没有留下错误信息，无法诊断')
    return packet


def _env_text(run):
    env = run.env() or {}
    bits = [env.get('framework') or run.framework]
    if env.get('version'):
        bits.append(str(env['version']))
    if env.get('runtime'):
        bits.append(str(env['runtime']))
    return ' · '.join(bit for bit in bits if bit)


def _read_script(workspace, rel_file):
    """读脚本源码。报告里的路径可能带 ../，只允许读工作区内的文件。"""
    if not rel_file:
        return ''
    try:
        path = (workspace / rel_file).resolve()
        root = workspace.resolve()
        if path != root and root not in path.parents:
            return ''
        if not path.is_file():
            return ''
        return path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return ''


# ---- Robot Framework ----

def _rf_failure(run, row):
    """从 output.xml 里取失败关键字链和 INFO 日志。

    步骤树里没有这两样：RF 3.1.2 的 `<kw>` 不带 `<arg>`（定位全丢），
    `_rf_collect` 也没把 error 记到每一步上。所以诊断时重新解析一遍产物。
    产物被清理过（只留最近 20 次）就返回空，靠 error 也能诊断。
    """
    root = _rf_xml(run, row.get('title') or '')
    if root is None:
        return [], []

    chain = []

    def walk(node, stack):
        for kw in node.findall('kw'):
            status = kw.find('status')
            raw = (status.get('status') if status is not None else '') or ''
            here = stack + [kw.get('name') or '']
            if raw == 'FAIL':
                chain.append(here)
            walk(kw, here)

    logs = []
    for test in root.iter('test'):
        if (test.get('name') or '') != (row.get('title') or ''):
            continue
        walk(test, [test.get('name') or ''])
        for msg in test.iter('msg'):
            level = msg.get('level') or ''
            text = (msg.text or '').strip()
            # 截图那行是一段 HTML，混进日志只会干扰判断
            if not text or text.startswith('</td>'):
                continue
            if level in ('INFO', 'FAIL', 'WARN'):
                logs.append('%s  %s' % (level, text[:400]))
    return chain, logs[-30:]


def _rf_xml(run, title):
    """找这次运行的 output.xml。

    生产路径是 `artifacts/rf-0/output.xml`（按 project 分组，一次运行可能有多个），
    但手工复跑留下的目录里 xml 是直接放在根上的，两种都认。
    """
    if not run.artifacts_dir:
        return None
    base = Path(run.artifacts_dir)
    candidates = sorted(base.glob('rf-*/output.xml')) + sorted(base.glob('output.xml'))
    for path in candidates:
        try:
            root = ET.parse(str(path)).getroot()
        except (ET.ParseError, OSError):
            continue
        if any((test.get('name') or '') == title for test in root.iter('test')):
            return root
    return None


def _rf_sections(text):
    """按 `*** Settings ***` 这类标题切段。"""
    sections = {}
    current = None
    for line in (text or '').splitlines():
        stripped = line.strip()
        if stripped.startswith('***') and stripped.endswith('***') and len(stripped) > 6:
            current = stripped.strip('* ').strip().lower()
            sections[current] = []
        elif current is not None:
            sections[current].append(line.rstrip())
    return sections


def _rf_blocks(lines):
    """段内按「顶格行」切块：键是块名（用例名、关键字名），值是原行。"""
    blocks = []
    name, body = None, []
    for line in lines or []:
        if line and not line[:1].isspace():
            if name is not None:
                blocks.append((name, body))
            name, body = line.strip(), [line]
        elif name is not None:
            body.append(line)
    if name is not None:
        blocks.append((name, body))
    return [(name, body) for name, body in blocks if not name.startswith('#')]


def _rf_excerpt(text, title, chain):
    """用例块 + 失败链上的关键字定义。

    只给这两样：Settings 段是平台写的（模型不该动），其他用例和关键字与本条失败无关，
    塞进去只会稀释注意力。
    """
    if not text.strip():
        return ''
    sections = _rf_sections(text)
    cases = dict(_rf_blocks(sections.get('test cases')))
    keywords = dict(_rf_blocks(sections.get('keywords')))

    picked = []
    if title in cases:
        picked.append(('用例 %s' % title, cases[title]))

    names = []
    # 失败链上的用户关键字（收尾处理这类，失败信息常常挂在这里）
    for path in chain:
        for name in path[1:]:
            if name in keywords and name not in names:
                names.append(name)
    # 用例正文里调用的用户关键字（比如封装好的「登录」）。它们没失败，但模型要读懂
    # 整条流程就不能缺——否则会以为是正文里少了一步。
    if title in cases:
        for line in cases[title][1:]:
            body = line.strip()
            if body and '    ' not in body and body in keywords and body not in names:
                names.append(body)
    for name in names[:4]:
        picked.append(('关键字 %s' % name, keywords[name]))

    if not picked:
        # 用例名对不上（报告里被改名、或文件里有多个 suite）：退回整份，但限长。
        body = text.splitlines()
        head = body[:MAX_EXCERPT_LINES]
        note = '\n# ...（原文共 %d 行，已截断）' % len(body) if len(body) > len(head) else ''
        return '\n'.join(head) + note

    out = []
    used = 0
    for label, body in picked:
        if used >= MAX_EXCERPT_LINES:
            break
        room = MAX_EXCERPT_LINES - used
        chunk = body[:room]
        out.append('# ---- %s ----' % label)
        out.extend(chunk)
        if len(body) > len(chunk):
            out.append('# ...（该块还有 %d 行）' % (len(body) - len(chunk)))
        used += len(chunk)
    result = '\n'.join(out)
    return result[:MAX_EXCERPT_CHARS]


# ---- Playwright ----

def _pw_log(row):
    """PW 没有逐行日志，但步骤树里每步都带 error，这就是最准的现场。"""
    out = []
    for step in (row.get('steps') or []):
        title = step.get('title') or ''
        if not title:
            continue
        line = '%s（%s）' % (title, step.get('status') or '')
        if step.get('error'):
            line += '：%s' % str(step['error'])[:300]
        out.append(line)
    if row.get('where'):
        out.append('失败位置 %s' % row['where'])
    return out[-30:]


def _pw_excerpt(text, where):
    """失败行前后各 25 行：spec 动辄几百行，只要故障附近那段。"""
    if not text.strip():
        return ''
    lines = text.splitlines()
    at = None
    # where 是 `file:line:column`，Windows 路径本身还带盘符冒号。
    # 取倒数第二个数字才是行号——直接取最后一个会拿到列号（实测踩过）。
    numbers = [part for part in (where or '').split(':')[1:] if part.isdigit()]
    if numbers:
        at = int(numbers[-2] if len(numbers) >= 2 else numbers[0]) - 1
    if at is None or not (0 <= at < len(lines)):
        head = lines[:MAX_EXCERPT_LINES]
        note = '\n// ...（原文共 %d 行，已截断）' % len(lines) if len(lines) > len(head) else ''
        return '\n'.join(head) + note

    start = max(0, at - 25)
    end = min(len(lines), at + 26)
    chunk = lines[start:end]
    out = []
    if start > 0:
        out.append('// ...（前 %d 行省略）' % start)
    for offset, line in enumerate(chunk):
        lineno = start + offset + 1
        # 标出失败行：模型最容易犯的错是说「第 N 行」而算错偏移。
        out.append('%s%4d | %s' % ('>>' if start + offset == at else '  ', lineno, line))
    if end < len(lines):
        out.append('// ...（后 %d 行省略）' % (len(lines) - end))
    return '\n'.join(out)[:MAX_EXCERPT_CHARS]


# ---------------------------------------------------------------- 结果整形

def _normalize(data):
    """把模型回复收成固定形状。字段缺、类型错、枚举越界都当降级处理，不抛。"""
    data = data if isinstance(data, dict) else {}

    category = str(data.get('category') or '').strip()
    if category not in CATEGORIES:
        category = '其它' if category else ''

    verdict = str(data.get('verdict') or '').strip().lower()
    if verdict not in VERDICTS:
        verdict = 'unknown'

    fixes = []
    for fix in data.get('fixes') or []:
        if not isinstance(fix, dict):
            continue
        label = str(fix.get('label') or '').strip()
        advice = str(fix.get('advice') or fix.get('why') or '').strip()
        if not label and not advice:
            continue
        fixes.append({
            'label': label[:80],
            'where': str(fix.get('where') or '').strip()[:120],
            'before': str(fix.get('before') or '')[:600],
            'after': str(fix.get('after') or '')[:600],
            'advice': advice[:500],
        })

    evidence = []
    for line in data.get('evidence') or []:
        text = str(line or '').strip()
        if text:
            evidence.append(text[:300])

    return {
        'category': category,
        'verdict': verdict,
        'reason': str(data.get('reason') or '').strip()[:200],
        'detail': str(data.get('detail') or '').strip()[:1500],
        'evidence': evidence[:8],
        'fixes': fixes[:6],
        'confidence': _confidence(data.get('confidence')),
    }


def _confidence(value):
    """模型有时返回 0.94 这种数字，别让它原样进界面。"""
    if isinstance(value, (int, float)):
        value = float(value)
        return 'high' if value >= 0.8 else ('medium' if value >= 0.5 else 'low')
    text = str(value or '').strip().lower()
    if text in ('high', 'medium', 'low'):
        return text
    if text in ('高', '较高'):
        return 'high'
    if text in ('中', '中等'):
        return 'medium'
    if text in ('低', '较低'):
        return 'low'
    return ''


# ---------------------------------------------------------------- 杂项

def _targets(rows, indexes):
    """挑要诊断的失败用例。自动模式只取前 MAX_CASES 条。"""
    from app.services.recording import runner as runner_mod

    failed = [i for i, row in enumerate(rows or [])
              if (row or {}).get('status') in runner_mod.FAILED_STATUSES]
    if indexes is None:
        return failed[:MAX_CASES]
    picked = []
    for value in indexes:
        try:
            value = int(value)
        except (TypeError, ValueError):
            continue
        if value in failed and value not in picked:
            picked.append(value)
    return picked[:MAX_CASES]


def _model():
    from app.services import models_config

    ref = models_config.healer_model() or models_config.default_model()
    if not ref:
        raise DiagnoseError('没有可用的模型，先到「设置 → 模型服务」里选一个')
    return ref


def _load_state(run):
    try:
        data = json.loads(run.diagnoses_json or '{}')
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _now():
    return datetime.utcnow().isoformat(timespec='seconds')
