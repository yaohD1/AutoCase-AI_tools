"""把官方 playwright-test-healer 接到录制回放的失败现场上。

和"规则 healer"的分工：
- 规则 healer（`healer.py`）是我们自己写的判定器，毫秒级，只在自己能证明的情况下给补丁。
- 这里这条是官方 agent：它真的跑测试、真的改文件、真的重跑，代价是几十秒到几分钟。
  录制回放页**不再自动跑**它——失败后只出一个「AI 自愈」按钮，用户点了才动。

它改的是沙箱里的 spec，不是回放步骤。所以最后还要把 spec 的改动翻译回步骤定位，
再交给用户确认写回。翻译不出来的部分宁可如实说，也不猜。

沙箱固定放在 `storage/healer_sandbox/`：
- 必须是独立 Git 仓库根，否则 opencode 把上层仓库当 project root，
  `autocase/**` 这条权限规则会匹配不上，apply_patch 全被拒（实测踩过）。
- 依赖只装一次，之后每次只换 `autocase/` 下的 spec。
"""

import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import requests

from app.services import models_config
from app.services.recording import spec_render

AGENT = 'playwright-test-healer'
DEFAULT_TIMEOUT = 300
INSTALL_TIMEOUT = 240
SPEC_NAME = 'probe.spec.ts'

# 和 opencode_runner 同一份黑名单：模型密钥从不出现在子进程环境里，
# 它们只走 OPENCODE_CONFIG 指向的配置文件。
BLOCKED_ENV = ('KEY', 'TOKEN', 'SECRET', 'PASSWORD', 'PASSWD', 'AUTH',
               'COOKIE', 'CREDENTIAL', 'DATABASE', 'REDIS', 'SENTRY', 'DSN')

_LOCK = threading.Lock()

CONFIG_TS = """import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './autocase',
  timeout: 20000,
  retries: 0,
  workers: 1,
  reporter: [['list']],
  use: { headless: true, channel: 'chrome' },
})
"""

PACKAGE_JSON = {
    'name': 'autocase-healer-sandbox',
    'private': True,
    'version': '1.0.0',
    'devDependencies': {'@playwright/test': '1.65.0'},
}


class HealError(Exception):
    """自愈跑不起来或跑挂了，消息直接给用户看。"""


# ---------------------------------------------------------------- 沙箱

def sandbox_root(app_config=None):
    configured = (app_config or {}).get('AUTOMATION_WORKSPACE_ROOT')
    if configured:
        return Path(configured).resolve().parent / 'healer_sandbox'
    # parents：recording → services → app → backend → 项目根。少一层会落进 backend/storage，
    # 那里不在 .gitignore 里，沙箱的 node_modules 会被 git 看见。
    project = Path(__file__).resolve().parents[4]
    return project / 'storage' / 'healer_sandbox'


def _git(root, *args, check=False):
    proc = subprocess.run([_bin('git'), *args], cwd=str(root), capture_output=True,
                          text=True, encoding='utf-8', errors='replace')
    if check and proc.returncode != 0:
        raise HealError('沙箱 git 操作失败：%s' % (proc.stderr or proc.stdout)[:200])
    return proc


def ensure_sandbox(root):
    """准备一个能跑 `npx playwright test` 的最小项目。返回是否首次安装。"""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / 'autocase').mkdir(exist_ok=True)

    config = root / 'playwright.config.ts'
    if not config.exists() or config.read_text(encoding='utf-8') != CONFIG_TS:
        config.write_text(CONFIG_TS, encoding='utf-8')

    pkg = root / 'package.json'
    pkg.write_text(json.dumps(PACKAGE_JSON, ensure_ascii=False, indent=2), encoding='utf-8')

    ignore = root / '.gitignore'
    if not ignore.exists():
        ignore.write_text('node_modules/\ntest-results/\nplaywright-report/\n'
                          '.playwright-mcp/\n', encoding='utf-8')

    installed = False
    if not (root / 'node_modules' / '@playwright' / 'test').exists():
        # Windows 上 npm 是 npm.cmd，直接写 'npm' 会 WinError 2。
        npm = shutil.which('npm') or shutil.which('npm.cmd') or 'npm'
        proc = subprocess.run([npm, 'install', '--no-audit', '--no-fund'],
                              cwd=str(root), capture_output=True, text=True,
                              encoding='utf-8', errors='replace', timeout=INSTALL_TIMEOUT)
        if proc.returncode != 0:
            raise HealError('沙箱依赖安装失败：%s' % (proc.stderr or proc.stdout)[-300:])
        installed = True

    if not (root / '.git').exists():
        _git(root, 'init', '-q')
    return installed


def _commit_baseline(root):
    _git(root, 'add', '-A')
    _git(root, '-c', 'user.email=healer@autocase', '-c', 'user.name=autocase',
         'commit', '-qm', 'baseline', '--allow-empty')


# ---------------------------------------------------------------- 定位表达式解析

_METHOD_MAP = {
    'getByRole': ('get_by_role', 'role'),
    'getByText': ('get_by_text', 'text'),
    'getByTestId': ('get_by_test_id', 'testid'),
    'getByLabel': ('get_by_label', 'label'),
    'getByPlaceholder': ('get_by_placeholder', 'placeholder'),
    'locator': ('locator', 'css'),
}


def _split_top(text):
    """按顶层逗号切分：括号和引号里的逗号不算。"""
    out, buf, depth, quote = [], [], 0, None
    for ch in text:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ('"', "'"):
            quote = ch
            buf.append(ch)
        elif ch in '([{':
            depth += 1
            buf.append(ch)
        elif ch in ')]}':
            depth -= 1
            buf.append(ch)
        elif ch == ',' and depth == 0:
            out.append(''.join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if buf:
        out.append(''.join(buf).strip())
    return [item for item in out if item]


def _unquote(token):
    body = token[1:-1]
    return body.replace('\\"', '"').replace("\\'", "'").replace('\\\\', '\\')


def _parse_value(token):
    token = token.strip()
    if not token:
        return None
    if token[0] in ('"', "'") and token[-1] == token[0]:
        return _unquote(token)
    if token.startswith('{'):
        obj = {}
        for pair in _split_top(token[1:-1]):
            if ':' not in pair:
                continue
            key, _, raw = pair.partition(':')
            key = key.strip().strip('"\'')
            obj[_camel_to_snake(key)] = _parse_value(raw)
        return obj
    if token in ('true', 'false'):
        return token == 'true'
    if re.fullmatch(r'-?\d+', token):
        return int(token)
    if re.fullmatch(r'-?\d+\.\d+', token):
        return float(token)
    if token.startswith('/') and token.rfind('/') > 0:
        return token[1:token.rfind('/')]
    return None


_JS_KEY = {'hasText': 'has_text', 'hasNotText': 'has_not_text'}


def _camel_to_snake(key):
    return _JS_KEY.get(key, key)


def _call_at(text, start):
    """从 start 处的 `name(` 开始，返回 (name, 参数字符串, 结束下标)。"""
    open_paren = text.index('(', start)
    depth, quote = 0, None
    for i in range(open_paren, len(text)):
        ch = text[i]
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in ('"', "'"):
            quote = ch
        elif ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                return text[start:open_paren].strip(), text[open_paren + 1:i], i + 1
    raise ValueError('括号不闭合')


def parse_locator(expr):
    """把 `page.getByRole('button', { name: '登录' })` 解析回 locator spec。

    解析不了就返回 None —— 调用方据此把结论标成「AI 改了但这处映射不回来」，
    而不是把一条解析错的定位写进录制。
    """
    text = (expr or '').strip()
    if text.startswith('page.'):
        text = text[len('page.'):]
    if not text:
        return None

    match = re.match(r'([A-Za-z_$][\w$]*)\(', text)
    if not match or match.group(1) not in _METHOD_MAP:
        return None
    name, args_text, end = _call_at(text, 0)
    method, kind = _METHOD_MAP[name]
    values = [_parse_value(item) for item in _split_top(args_text)]
    if not values:
        return None
    head = {'method': method, 'args': [values[0]], 'kwargs': {}}
    if len(values) > 1 and isinstance(values[1], dict):
        head['kwargs'] = dict(values[1])

    chain = []
    rest = text[end:]
    for link in re.finditer(r'\.([A-Za-z_$][\w$]*)\(', rest):
        lname = link.group(1)
        start = link.start() + 1
        try:
            _, largs, _ = _call_at(rest, start)
        except ValueError:
            return None
        lvalues = [_parse_value(item) for item in _split_top(largs)]
        if lname in _METHOD_MAP:
            # 链上还能再套 getByRole（「行里找按钮」就是这种），
            # 不认的话整条表达式会被判成解析失败，白白丢掉一条能用的定位。
            lmethod, _ = _METHOD_MAP[lname]
            if not lvalues:
                return None
            link_spec = {'method': lmethod, 'args': [lvalues[0]], 'kwargs': {}}
            if len(lvalues) > 1 and isinstance(lvalues[1], dict):
                link_spec['kwargs'] = dict(lvalues[1])
            chain.append(link_spec)
        elif lname == 'nth' and lvalues:
            chain.append({'method': 'nth', 'args': [lvalues[0]], 'kwargs': {}})
        elif lname == 'first':
            chain.append({'method': 'first', 'args': [], 'kwargs': {}})
        elif lname == 'last':
            chain.append({'method': 'last', 'args': [], 'kwargs': {}})
        elif lname == 'filter' and lvalues and isinstance(lvalues[0], dict):
            chain.append({'method': 'filter', 'args': [], 'kwargs': dict(lvalues[0])})
        else:
            return None

    spec = dict(head)
    if chain:
        spec['chain'] = [head] + chain
    return {'kind': kind, 'desc': expr.strip(), 'spec': spec}


# ---------------------------------------------------------------- 起 serve / 派 agent

def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def _child_env(config_path):
    env = {k: v for k, v in os.environ.items()
           if not any(marker in k.upper() for marker in BLOCKED_ENV)}
    env['OPENCODE_CONFIG'] = str(config_path)
    return env


def _bin(name):
    """Windows 上有些命令是 .cmd，直接写名字会 WinError 2。"""
    return shutil.which(name) or name


def _start_serve(root, port, log_path):
    log = open(log_path, 'w', encoding='utf-8')
    command = [_bin('opencode'), 'serve', '--port', str(port), '--hostname', '127.0.0.1']
    flags = 0
    if os.name == 'nt':
        flags = getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0) | \
            getattr(subprocess, 'CREATE_BREAKAWAY_FROM_JOB', 0)
    try:
        return subprocess.Popen(
            command, cwd=str(root), env=_child_env(models_config.runtime_config()),
            stdout=subprocess.DEVNULL, stderr=log, creationflags=flags)
    except OSError:
        return subprocess.Popen(
            command, cwd=str(root), env=_child_env(models_config.runtime_config()),
            stdout=subprocess.DEVNULL, stderr=log)


def _wait_healthy(base, process, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise HealError('opencode serve 提前退出（code=%s）' % process.returncode)
        try:
            if requests.get(base + '/global/health', timeout=3).status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(0.5)
    raise HealError('opencode serve 在 %d 秒内没有就绪' % timeout)


def _stop(process, base):
    try:
        requests.post('%s/instance/dispose' % base, timeout=4)
    except Exception:  # noqa: BLE001
        pass
    if process is None:
        return
    try:
        process.terminate()
        process.wait(8)
    except Exception:  # noqa: BLE001
        try:
            process.kill()
        except Exception:  # noqa: BLE001
            pass


def _collect_messages(base, sid):
    try:
        resp = requests.get('%s/session/%s/message' % (base, sid), timeout=15)
        if resp.status_code != 200:
            return None
    except requests.RequestException:
        return None
    return resp.json() if isinstance(resp.json(), list) else None


def _summarize(items):
    """从消息里抽两样东西：最后一句话 + 有没有真的产出。"""
    texts, tools = [], []
    for item in items or []:
        info = item.get('info') or {}
        if info.get('role') != 'assistant':
            continue
        for part in item.get('parts') or []:
            if part.get('type') == 'text' and (part.get('text') or '').strip():
                texts.append(part['text'].strip())
            elif part.get('type') == 'tool':
                tools.append(part.get('tool'))
    return texts, tools


def _run_agent(root, title, prompt, model_ref, timeout, on_tick=None):
    """派一次官方 healer，阻塞到它收工或超时。返回消息列表与耗时。"""
    port = _free_port()
    base_url = 'http://127.0.0.1:%d' % port
    process = _start_serve(root, port, root / 'serve.log')
    started = time.monotonic()
    try:
        _wait_healthy(base_url, process)
        provider, _, mid = (model_ref or '').partition('/')
        model = {'providerID': provider, 'id': mid} if provider and mid else None

        body = {'title': title, 'agent': AGENT}
        if model:
            body['model'] = model
        create = requests.post(base_url + '/session', json=body, timeout=30)
        if create.status_code not in (200, 201):
            raise HealError('创建会话失败（%d）：%s' % (create.status_code, create.text[:200]))
        sid = (create.json() or {}).get('id')
        if not sid:
            raise HealError('创建会话没有返回 id')

        payload = {'parts': [{'type': 'text', 'text': prompt}], 'agent': AGENT}
        if model:
            payload['model'] = {'providerID': model['providerID'], 'modelID': model['id']}
        send = requests.post('%s/session/%s/prompt_async' % (base_url, sid),
                             json=payload, timeout=60)
        if send.status_code not in (200, 202, 204):
            raise HealError('派发提示词失败（%d）：%s' % (send.status_code, send.text[:200]))

        deadline = time.monotonic() + timeout
        idle_since = time.monotonic()
        last_sig = None
        items = []
        while time.monotonic() < deadline:
            time.sleep(2)
            if on_tick:
                try:
                    on_tick()
                except Exception:  # noqa: BLE001 - 出帧失败不该影响调查
                    pass
            if process.poll() is not None:
                raise HealError('opencode 进程中途退出（code=%s）' % process.returncode)
            got = _collect_messages(base_url, sid)
            if got is None:
                continue
            items = got
            texts, tools = _summarize(items)
            sig = (len(texts), sum(len(t) for t in texts), len(tools))
            if sig != last_sig:
                last_sig = sig
                idle_since = time.monotonic()
                continue
            if time.monotonic() - idle_since > 150:
                break
        return items, round(time.monotonic() - started, 1)
    finally:
        _stop(process, base_url)


# ---------------------------------------------------------------- 从 diff 回收补丁

def _changed_lines(root):
    proc = _git(root, 'diff', '--unified=0', '--', 'autocase/')
    return proc.stdout or ''


def _pair_changes(diff_text):
    """把 diff 里的「旧行 → 新行」配对。只关心成对出现的改动。"""
    pairs, bucket = [], []
    for line in diff_text.splitlines():
        if line.startswith('---') or line.startswith('+++'):
            continue
        if line.startswith('-'):
            bucket.append(('old', line[1:]))
        elif line.startswith('+'):
            bucket.append(('new', line[1:]))
        else:
            if bucket:
                olds = [t for kind, t in bucket if kind == 'old']
                news = [t for kind, t in bucket if kind == 'new']
                if olds and news:
                    pairs.append((olds, news))
                bucket = []
    if bucket:
        olds = [t for kind, t in bucket if kind == 'old']
        news = [t for kind, t in bucket if kind == 'new']
        if olds and news:
            pairs.append((olds, news))
    return pairs


_LOCATOR_IN_LINE = re.compile(
    r'page\.((?:getBy|locator\()[^;]*?\))\s*\.(?:click|fill|selectOption|press)\(')


def extract_patch(root):
    """从 spec 的改动里提取出「这一步的新定位」。

    只认「点击/填写类那一行被改」的情况。断言值、等待时长之类的改动不产生定位补丁，
    但仍然会作为 spec_diff 呈现给用户——那可能是 AI 顺手改掉的东西，用户有权看到。
    """
    diff_text = _changed_lines(root)
    if not diff_text.strip():
        return None, '', 'AI 没有改动任何文件。'
    pairs = _pair_changes(diff_text)
    if not pairs:
        return None, diff_text, 'AI 改了文件，但没有成对的可比对行。'

    for _olds, news in pairs:
        for line in news:
            match = _LOCATOR_IN_LINE.search(line)
            if not match:
                continue
            parsed = parse_locator('page.' + match.group(1))
            if parsed:
                return parsed, diff_text, ''
    return None, diff_text, 'AI 改了文件，但改动里没有能映射回步骤的定位。'


# ---------------------------------------------------------------- 对外入口

def investigate(page, steps, app_config=None, title='AutoCase 录制回放', on_tick=None):
    """跑一次官方 healer，返回卡片字段（不含写回决定）。

    `page` 是回放当前持有的页面：只有它能证明「AI 给的新定位真的点得到」。
    `steps` 只应包含到失败那一步为止——后面的步骤依赖失败之后的状态，
    塞进去只会制造一批二次失败，把真正的错误淹掉。
    """
    started = time.monotonic()
    root = sandbox_root(app_config)
    rendered = spec_render.render(steps, title=title)
    if not rendered['code'].strip():
        raise HealError('没有可渲染的步骤，无法投影给 healer。')

    ensure_sandbox(root)
    # 清掉上一次的 spec：留着会让 test_run 跑两份用例，失败信息互相盖住。
    for old in (root / 'autocase').glob('*.spec.ts'):
        old.unlink()
    (root / 'autocase' / SPEC_NAME).write_text(rendered['code'], encoding='utf-8')
    _commit_baseline(root)

    model_ref = models_config.healer_model()
    if not model_ref:
        raise HealError('没有可用的模型。请先在「AI 自愈设置」里选一个模型。')

    try:
        failed_url = page.url if page else ''
    except Exception:  # noqa: BLE001
        failed_url = ''

    last = steps[-1] if steps else {}
    prompt = '\n'.join([
        'requirement: 按录制步骤回放，修复第 %s 步（%s）失败的问题'
        % (last.get('no'), last.get('type')),
        'base_url: %s' % (failed_url or '(见 spec 内的 goto)'),
        'specs_path: ./autocase',
        'max_heal_attempts: 3',
        'generated_files: %s' % SPEC_NAME,
        'Run the tests against the complete base_url above.',
    ])

    try:
        items, elapsed = _run_agent(root, 'AutoCase healer', prompt, model_ref,
                                    DEFAULT_TIMEOUT, on_tick=on_tick)
    except subprocess.TimeoutExpired:
        raise HealError('AI 调查超时（%d 秒）' % DEFAULT_TIMEOUT)

    texts, tools = _summarize(items)
    if not tools:
        raise HealError('AI 没有开始工作（可能是模型返回空，换一个模型再试）。')

    parsed, diff_text, note = extract_patch(root)
    summary = texts[-1] if texts else ''

    card = {
        'no': last.get('no'),
        'type': last.get('type'),
        'alias': (last.get('target') or {}).get('alias'),
        'status': 'blocked',
        'title': 'AI 自愈',
        'category': 'ai_heal',
        'reason': summary or 'AI 没有留下结论。',
        'summary': summary[:600],
        'spec_diff': diff_text,
        'probes': ['模型 %s' % model_ref,
                   '工具：%s' % '、'.join(dict.fromkeys(tools)),
                   '耗时 %.1f 秒' % elapsed],
        'attempt': 1,
        'max_attempt': 3,
        'can_apply': False,
        'changes': [],
        'verify': None,
        'risk': 'medium',
        'verdict': None,
        'blocked_reason': note,
        'writeback_ok': False,
        'writeback_default': False,
        'unsupported': rendered['unsupported'],
        'elapsed': round(time.monotonic() - started, 1),
    }

    if not parsed:
        return card

    # AI 说它跑通了，但那是沙箱里的事。这条定位要在**当前页面上**也成立，
    # 否则写回去下次照样点不到——这一步不省。
    from app.services.recording import healer as rule_healer
    try:
        verify = rule_healer._precheck(page, parsed['spec'], last)
    except Exception as exc:  # noqa: BLE001 - 预检自身出错也要给用户一个交代
        verify = {'ok': False, 'text': '预检执行失败：%s' % str(exc)[:120]}

    card['verify'] = verify
    card['patch'] = {'primary': {'kind': parsed['kind'], 'desc': parsed['desc'],
                                 'count': None, 'spec': parsed['spec']},
                     'clear_frame': False}
    card['changes'] = [{'label': '主定位', 'before': '（录制时）', 'after': parsed['desc']}]

    if verify.get('ok'):
        card['status'] = 'ready'
        card['can_apply'] = True
        card['verdict'] = 'verified'
        card['risk'] = 'low'
        card['writeback_ok'] = True
        # AI 自愈是用户主动点的，默认勾上写回；它改的是定位，不是断言。
        card['writeback_default'] = True
        card['blocked_reason'] = None
    else:
        card['blocked_reason'] = 'AI 给出的新定位在当前页面验证没通过：%s' % verify.get('text')
    return card
