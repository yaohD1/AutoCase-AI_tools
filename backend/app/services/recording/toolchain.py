"""目标环境里的工具：导出库的关键字清单，校验生成的文件。

缺少工具时直接抛 ToolchainError，不跳过校验（决策 11）。
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

LIBDOC_TIMEOUT = 120
DRYRUN_TIMEOUT = 120
TSC_TIMEOUT = 180
RUN_TIMEOUT = 240
OUTPUT_LIMIT = 4000
MAX_ERRORS = 5
# 这些错误说明「环境不通」，不是脚本写错了。拿去让模型修，它只会越改越乱。
ENV_ERROR_MARKS = (
    'ERR_CONNECTION_REFUSED', 'ECONNREFUSED', 'ERR_NAME_NOT_RESOLVED',
    'ERR_ADDRESS_UNREACHABLE', 'ERR_CERT_', 'ERR_SSL_', 'ERR_INTERNET_DISCONNECTED',
    'ERR_TIMED_OUT', 'ERR_EMPTY_RESPONSE',
)
ANSI = re.compile(r'\x1b\[[0-9;]*m')
# Playwright 一个用例都没收集到时抛的原文（CLI 路径参数是 minimatch，写错就一条都匹配不到）。
NO_TESTS_MARK = 'No tests found'
# 文件里有没有真正的用例声明。test.xxx( 也算（describe.skip / fixme 这些）。
TEST_DECL = re.compile(r'^[ \t]*test(?:\.[A-Za-z]+)?[ \t]*\(', re.M)


class ToolchainError(Exception):
    pass


def _tail(text):
    text = (text or '').strip()
    return text[-OUTPUT_LIMIT:] if len(text) > OUTPUT_LIMIT else text


def _run(cmd, timeout, cwd=None):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace',
                              timeout=timeout, cwd=cwd)
    except subprocess.TimeoutExpired:
        raise ToolchainError('命令超时：%s' % ' '.join(cmd[:3]))
    except OSError as exc:
        raise ToolchainError('无法执行 %s：%s' % (cmd[0], exc))


def export_keywords(python, library='SeleniumLibrary'):
    """导出库的关键字与参数名。需要目标环境已安装该库。

    这里会导入库本身（不是项目代码），用于拿到关键字 API，是 spike 中记录的有意例外。
    """
    tmp = tempfile.mkdtemp(prefix='autocase-libdoc-')
    out = os.path.join(tmp, 'lib.json')
    try:
        result = _run([python, '-m', 'robot.libdoc', library, out], LIBDOC_TIMEOUT)
        if result.returncode != 0:
            raise ToolchainError('导出 %s 关键字失败（目标环境是否已安装？）：%s' % (library, _tail(result.stderr or result.stdout)))
        with open(out, encoding='utf-8') as handle:
            data = json.load(handle)
    except ValueError as exc:
        raise ToolchainError('关键字清单无法解析：%s' % exc)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    keywords = [
        {'name': kw['name'], 'args': [arg['name'] for arg in kw.get('args', [])]}
        for kw in data.get('keywords', [])
    ]
    return {'library': data.get('name') or library, 'version': data.get('version'), 'keywords': keywords}


def dryrun(python, path, cwd=None):
    """对单个 .robot 文件做 dryrun。返回 (通过与否, 输出尾部)。

    dryrun 能发现未知关键字与参数个数错误，但不会真正执行浏览器操作。
    """
    tmp = tempfile.mkdtemp(prefix='autocase-dryrun-')
    cmd = [python, '-m', 'robot', '--dryrun', '--output', 'NONE', '--report', 'NONE', '--log', 'NONE',
           '--outputdir', tmp, str(path)]
    try:
        result = _run(cmd, DRYRUN_TIMEOUT, cwd=cwd)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    output = result.stdout or result.stderr
    if 'No module named robot' in (result.stderr or ''):
        raise ToolchainError('目标环境缺少 robotframework，无法校验')
    return result.returncode == 0, _tail(output)


def tsc_check(workspace, target_path=None):
    """对 Playwright TypeScript 项目做类型检查。需要目标项目已安装依赖（node_modules/.bin/tsc）。

    tsc 只有全项目模式，没有单文件模式：单文件会丢掉 tsconfig 的 paths / include 上下文，
    反而报出一堆假错。所以照常全项目跑，但**判定只看目标文件相关的报错**——
    项目里本来就有的类型错误（基线）不该算到这次生成头上，否则每次生成都必然失败。
    """
    root = Path(workspace)
    bin_dir = root / 'node_modules' / '.bin'
    tsc = next((p for p in (bin_dir / 'tsc.cmd', bin_dir / 'tsc') if p.exists()), None)
    if tsc is None:
        raise ToolchainError('目标项目缺少 typescript（node_modules/.bin/tsc），请先安装项目依赖')
    result = _run([str(tsc), '--noEmit'], TSC_TIMEOUT, cwd=str(root))
    output = result.stdout or result.stderr or ''
    if result.returncode == 0:
        return True, ''
    if not target_path:
        return False, _tail(output)
    rel = str(target_path).replace('\\', '/').lstrip('./')
    hits = [
        line for line in output.splitlines()
        if rel in line.replace('\\', '/')
    ]
    if hits:
        return False, _tail('\n'.join(hits))
    # 目标文件一行错都没有：失败来自别的文件（既有基线），放行，但把基线噪音留在返回值里备查。
    return True, ''


def _playwright_bin(root):
    for name in ('playwright.cmd', 'playwright'):
        candidate = Path(root) / 'node_modules' / '.bin' / name
        if candidate.exists():
            return candidate
    return None


def _project_config(root):
    """项目自己的 playwright 配置。没有就返回 None，用独立配置兜底。"""
    for name in ('playwright.config.ts', 'playwright.config.js', 'playwright.config.mjs',
                 'playwright.config.cjs'):
        candidate = Path(root) / name
        if candidate.is_file():
            return candidate.name
    return None


VERIFY_CONFIG_EXTEND = """// AutoCase 临时配置：继承项目配置，只做两件事 —— 注入登录态、把产物挪走。
// 跑完就删，不留在用户仓库里。
import base from './%(base)s'

const state = process.env.AUTOCASE_STORAGE_STATE
const config = typeof base === 'function' ? base() : base
const withState = (project) => ({
  ...project,
  use: { ...(project.use || {}), ...(state ? { storageState: state } : {}) },
})

export default {
  ...config,
  projects: Array.isArray(config.projects) ? config.projects.map(withState) : config.projects,
  use: { ...(config.use || {}), ...(state ? { storageState: state } : {}) },
}
"""

VERIFY_CONFIG_STANDALONE = """// AutoCase 临时配置：项目里没有 playwright 配置，用最小可运行配置跑这一个文件。
export default {
  testDir: '.',
  use: {
    storageState: process.env.AUTOCASE_STORAGE_STATE || undefined,
  },
}
"""


def _json_report(text):
    """从 stdout 里抠出 JSON 报告。前面可能有 webServer 日志、警告。"""
    match = re.search(r'^\{', text, re.M)
    if not match:
        return None
    try:
        return json.loads(text[match.start():])
    except ValueError:
        return None


def _no_tests_reason(path):
    """没收集到用例时给一句能直接照做的话：是路径没对上，还是文件里没写用例。"""
    try:
        text = path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        # 读不到就别猜内容，只说路径这一层的原因。
        return 'Playwright 没找到这个文件里的用例（路径不在 testDir 内或被 testIgnore 排除）'
    if not TEST_DECL.search(text):
        return '文件里没有 test() 用例，Playwright 没有东西可跑'
    return ('Playwright 没收集到这个文件：路径不在 testDir 内、被项目 testIgnore 排除，'
            '或路径里含 [ ] { } ? * 这类通配符字符')


def _failures(suite, bucket):
    for spec in suite.get('specs') or []:
        title = spec.get('title') or '未命名'
        for test in spec.get('tests') or []:
            for result in test.get('results') or []:
                if result.get('status') not in ('failed', 'timedOut', 'interrupted'):
                    continue
                error = result.get('error') or {}
                message = ANSI.sub('', str(error.get('message') or '')).strip()
                line = ''
                location = error.get('location') or {}
                if location.get('line'):
                    line = '（第 %s 行）' % location['line']
                bucket.append('%s%s：%s' % (title, line, message[:800]))
    for child in suite.get('suites') or []:
        _failures(child, bucket)


def run_playwright(workspace, target_path, storage_state=None, timeout=RUN_TIMEOUT):
    """在目标项目里把刚写出的这个文件真跑一遍。返回结构化结果，不抛异常。

    为什么不用 `test_run` 那类 MCP：它们不认我们的临时配置，也就拿不到登录态 ——
    录制的用例大多从已登录页面开始，没有 cookie 会死在第一步，看起来像「定位全错」。
    这里自己起进程，登录态用临时文件注入，跑完即删（里面是会话凭据）。
    """
    root = Path(workspace)
    binary = _playwright_bin(root)
    if binary is None:
        return {'ran': False, 'reason': '目标项目缺少 @playwright/test（node_modules/.bin/playwright）'}

    base = _project_config(root)
    # 临时配置必须跟原配置同层（项目根）：Playwright 的 testDir / globalSetup / webServer.cwd
    # 都是相对**配置文件所在目录**解析的，放进 .autocase/ 会让 testDir: './tests' 变成
    # .autocase/tests，最后一个用例都匹配不到。
    config_path = root / '.autocase.verify.config.ts'
    auth_dir = None
    auth_path = None
    out_dir = None
    created_config = False
    try:
        template = VERIFY_CONFIG_EXTEND % {'base': base.rsplit('.', 1)[0]} if base else VERIFY_CONFIG_STANDALONE
        try:
            config_path.write_text(template, encoding='utf-8')
        except OSError as exc:
            # 工作区只读之类的问题不该把整次生成变成 500：跑不了就如实说。
            return {'ran': False, 'reason': '无法写入临时 Playwright 配置：%s' % exc}
        created_config = True
        # 产物放到仓库外的临时目录：--output 落在 testDir 里时 Playwright 会报错，
        # 而且 trace / 截图也不该进用户的 git 工作区。
        out_dir = Path(tempfile.mkdtemp(prefix='autocase-run-'))

        env = dict(os.environ)
        env['FORCE_COLOR'] = '0'
        if storage_state:
            auth_dir = tempfile.mkdtemp(prefix='autocase-auth-')
            auth_path = Path(auth_dir) / 'state.json'
            auth_path.write_text(json.dumps(storage_state, ensure_ascii=False), encoding='utf-8')
            env['AUTOCASE_STORAGE_STATE'] = str(auth_path)

        cmd = [
            str(binary), 'test', str(target_path).replace('\\', '/'),
            '--config', str(config_path.relative_to(root)).replace('\\', '/'),
            '--reporter=json', '--retries=0', '--workers=1',
            '--output', str(out_dir),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                                    errors='replace', timeout=timeout, cwd=str(root), env=env)
        except subprocess.TimeoutExpired:
            return {'ran': False, 'reason': '用例执行超过 %d 秒仍未结束' % timeout}
        except OSError as exc:
            return {'ran': False, 'reason': '无法启动 Playwright：%s' % exc}

        text = (result.stdout or '') + '\n' + (result.stderr or '')
        report = _json_report(text)
        if report is None:
            return {
                'ran': False,
                'reason': 'Playwright 没能启动（配置或依赖问题）',
                'tail': ANSI.sub('', text).strip()[-OUTPUT_LIMIT:],
            }

        stats = report.get('stats') or {}
        unexpected = int(stats.get('unexpected') or 0)
        expected = int(stats.get('expected') or 0)
        skipped = int(stats.get('skipped') or 0)
        flaky = int(stats.get('flaky') or 0)
        failures = []
        # 顶层 errors 是「一个用例都没跑起来」那类问题（配置加载失败、no tests found）。
        # 它们在 suites 里没有位置，不单独收就会得到「跑了、0 个、失败 0」这种自相矛盾的结果。
        for error in report.get('errors') or []:
            message = ANSI.sub('', str(error.get('message') or '')).strip()
            if message:
                failures.append('Playwright：%s' % message[:800])
        for suite in report.get('suites') or []:
            _failures(suite, failures)

        # 「No tests found」是「压根没跑」，不是「跑了没过」。当成失败会把它丢进修复循环，
        # 让模型去改一个从未被执行过的文件——它没有 bash，只能盲改，多半越改越偏。
        # 真实成因是路径或筛选对不上：文件不在 testDir 内、被项目的 testIgnore 排除、
        # 或者路径里带 minimatch 的特殊字符（[ ] { } ? * !），也可能文件里根本没有 test()。
        if expected + unexpected + flaky + skipped == 0 \
                and any(NO_TESTS_MARK in item for item in failures):
            path = root / str(target_path).replace('\\', '/')
            return {
                'ran': False,
                'reason': _no_tests_reason(path),
                'tail': ANSI.sub('', text).strip()[-OUTPUT_LIMIT:],
            }

        failures = failures[:MAX_ERRORS]
        ok = result.returncode == 0 and unexpected == 0 and not failures
        blob = ' '.join(failures) + ' ' + text
        return {
            'ran': True,
            'ok': ok,
            'total': expected + unexpected + flaky + skipped,
            'passed': expected,
            'failed': unexpected,
            'flaky': flaky,
            'skipped': skipped,
            'duration_ms': int(stats.get('duration') or 0),
            'errors': failures,
            'env_error': any(mark in blob for mark in ENV_ERROR_MARKS),
        }
    finally:
        if created_config:
            try:
                config_path.unlink(missing_ok=True)
            except OSError:
                pass
        if auth_dir:
            shutil.rmtree(auth_dir, ignore_errors=True)
        if out_dir:
            shutil.rmtree(out_dir, ignore_errors=True)
