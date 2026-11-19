"""在目标项目里枚举用例、执行用例、读回结果。

和 `emit.py` 的区别：那边是「写代码并顺手跑一遍验证」，这边是「跑用户勾选的用例」。
共同点是都得借用目标项目自己的环境——node_modules / venv / playwright.config /
登录态全在项目里，另起沙箱等于把它们全丢掉（实测复制一份 node_modules 就要 47 秒）。

两个框架的差异都收在这一个文件里：

- Playwright 用 `--list --reporter=json` 枚举，用 `文件:行号` 定位单条用例（不用 --grep：
  用例名里全是中文标点和短横线，正则很容易跑偏）。
- Robot 用 `TestSuiteBuilder` 枚举（RF 3.1 的 Test 没有行号，只能按「文件 + 用例名」），
  用 `--test` 执行；`--test` 是模式匹配，所以拿到结果后要回验名字。

对外只有四个入口：`discover()` / `start()` / `stop()` / `artifact_path()`。
"""
import json
import os
import re
import shutil
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from app.models import db
from app.models.test_run import TestRun
from app.services.recording import detect, toolchain

# 枚举结果缓存：Playwright 25 条 2s、Robot 508 条 3s，每次开页面都重算太浪费。
# 用「刷新用例」按钮可以强制重来。
CACHE_TTL = 120
_CACHE = {}
_CACHE_LOCK = threading.Lock()

# 同一时间只跑一次：两批用例同时开浏览器会互相抢窗口焦点，报告也分不清谁是谁。
_ACTIVE = {}
_ACTIVE_LOCK = threading.Lock()

MAX_TAIL = 6000
# 算作「失败」的状态。只用来统计失败条数和判断批次状态，
# 步骤树不按状态裁剪：执行过的每一条都存，见 _pw_jsonl_row / _rf_collect。
FAILED_STATUSES = ('failed', 'timedOut', 'interrupted', 'error')
# 结果 JSON 的体积兜底。步骤树改成「执行就存」之后，一次几百条的全量跑能堆到十几 MB；
# 库里的列吃得下，但接口和浏览器都不想接。真撞上了只留失败用例的步骤，
# 省掉的条数记进 env.steps_trimmed，报告里会写明。
RESULTS_BUDGET = 8 * 1024 * 1024
ANSI = re.compile(r'\x1b\[[0-9;]*m')
# Robot 控制台一行：`用例名(补空格) | PASS |`，长名字会被截断成 `...`，
# 所以这里只用来数进度，真正的结果以 output.xml 为准。
RF_LINE = re.compile(r'^(?P<name>.*?)\s+\|\s+(?P<status>PASS|FAIL|SKIP)\s+\|$')
# Playwright 控制台一行：`  ok 1 [chromium] › tests\a.spec.ts:6:7 › 标题 (2.5s)`
PW_LINE = re.compile(r'^\s+(?:ok|×|✘|✗|-)\s+\d+\s+\[[^\]]+\]\s+›'
                     r'|\s+(?:ok|×|✘|✗|-)\s+\d+\s+\[[^\]]+\]\s+›')

# AutoCase 自带的 Playwright reporter。放在这里而不是项目里：项目不用装包、
# 不用改 playwright.config，换一个项目也照样能用。它比 `--reporter=json` 多给
# 一棵 result.steps（每步耗时），报告里的「卡在哪一步」就靠它。
PW_REPORTER = Path(__file__).resolve().with_name('pw_reporter.js')
PW_REPORTER_MARK = '##AC##'

# Robot 项目里除了 venv 还有 tools（chromedriver），它自己的 run_case.bat 就是这么加 PATH 的。
RF_EXTRA_PATH = ('tools', 'venv/Scripts', '.venv/Scripts')
# 目标环境不健康时的报错标记：这种失败不该算到用例头上，也不值得重试。
ENV_ERROR_MARKS = (
    'ERR_CONNECTION_REFUSED', 'ECONNREFUSED', 'ERR_NAME_NOT_RESOLVED',
    'ERR_ADDRESS_UNREACHABLE', 'ERR_CERT_', 'ERR_SSL_', 'ERR_INTERNET_DISCONNECTED',
    'ERR_TIMED_OUT', 'ERR_EMPTY_RESPONSE', 'net::ERR_',
)


class RunnerError(Exception):
    pass


# ---------------------------------------------------------------- 公共小工具

def _posix(value):
    return str(value or '').replace('\\', '/')


def _rel(path, root):
    try:
        return _posix(Path(path).resolve().relative_to(Path(root).resolve()))
    except (ValueError, OSError):
        return _posix(path)


def _under(path, base):
    """只认 base 目录内的文件，返回相对路径；越界返回 None。

    报告里的附件路径来自目标项目的 reporter，可能是用户自己挂的任意绝对路径，
    不能因为「字符串不以 .. 开头」就当成产物发出去。
    """
    if not path:
        return None
    try:
        return _posix(Path(path).resolve().relative_to(Path(base).resolve()))
    except (ValueError, OSError):
        return None


def _tail(text):
    text = ANSI.sub('', text or '').strip()
    return text[-MAX_TAIL:] if len(text) > MAX_TAIL else text


def _clean(text):
    return ANSI.sub('', str(text or '')).strip()


def _json_blob(text):
    """从混了日志的 stdout 里抠出顶层 JSON。

    Playwright 项目常装 dotenvx 之类，会在 JSON 前面打几行带 `{` 的提示
    （`// tip: ⌘ multiple files { path: [...] }`），所以不能用第一个 `{`。
    顶层 JSON 一定顶格开始，按行首找。
    """
    match = re.search(r'^\{', text or '', re.M)
    if not match:
        return None
    try:
        return json.loads(text[match.start():])
    except ValueError:
        return None


def _env_for(root):
    env = dict(os.environ)
    env['FORCE_COLOR'] = '0'
    env['PYTHONIOENCODING'] = 'utf-8'
    # Robot 项目把 chromedriver 放在 tools/，不放进 PATH 就是「driver 找不到」。
    extra = [str(Path(root) / item) for item in RF_EXTRA_PATH if (Path(root) / item).is_dir()]
    if extra:
        env['PATH'] = os.pathsep.join(extra + [env.get('PATH', '')])
    return env


def _kill_tree(proc):
    """连子进程一起杀：Playwright / robot 都会再起浏览器或 driver。"""
    if proc is None or proc.poll() is not None:
        return
    try:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                           capture_output=True, timeout=20)
        else:
            proc.terminate()
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        proc.wait(timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


def _watchdog(proc, seconds, note):
    """进程卡住不出声时也要能收场：光靠读 stdout 的循环永远等不到下一行。"""
    timer = threading.Timer(seconds, lambda: (_kill_tree(proc), note.append('timeout')))
    timer.daemon = True
    timer.start()
    return timer


# ---------------------------------------------------------------- Playwright

def _pw_bin(root):
    for name in ('playwright.cmd', 'playwright'):
        candidate = Path(root) / 'node_modules' / '.bin' / name
        if candidate.exists():
            return candidate
    return None


def _pw_config(root):
    return toolchain._project_config(Path(root))


def _pw_prefix(root):
    """CLI 前缀。没有项目配置时写一份最小的临时配置，别让默认配置把 node_modules 扫一遍。"""
    binary = _pw_bin(root)
    if binary is None:
        raise RunnerError('目标项目缺少 @playwright/test（node_modules/.bin/playwright），请先安装项目依赖')
    base = [str(binary), 'test']
    config = _pw_config(root)
    if config:
        return base + ['--config', config]
    temp = Path(root) / '.autocase.run.config.ts'
    if not temp.exists():
        temp.write_text(toolchain.VERIFY_CONFIG_STANDALONE, encoding='utf-8')
    return base + ['--config', temp.name]


def _pw_walk(node, chain, bucket):
    """把 suites 树拍平。文件级 suite 的标题不用进 describe 链，它就是文件名。"""
    title = node.get('title') or ''
    is_file = bool(node.get('file'))
    here = chain if is_file else (chain + ([title] if title else []))
    for spec in node.get('specs') or []:
        spec['_chain'] = here
        bucket.append(spec)
    for child in node.get('suites') or []:
        _pw_walk(child, here, bucket)


def _pw_cases(root):
    cmd = _pw_prefix(root) + ['--list', '--reporter=json']
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                                errors='replace', timeout=180, cwd=str(root), env=_env_for(root))
    except subprocess.TimeoutExpired:
        raise RunnerError('枚举用例超时（180 秒）：项目配置里可能有很慢的 globalSetup')
    except OSError as exc:
        raise RunnerError('无法启动 Playwright：%s' % exc)

    data = _json_blob(result.stdout or '')
    if data is None:
        raise RunnerError('Playwright 没能列出用例：%s'
                          % _tail((result.stderr or '') + (result.stdout or ''))[-400:])

    root_dir = (data.get('config') or {}).get('rootDir') or str(root)
    specs = []
    for suite in data.get('suites') or []:
        _pw_walk(suite, [], specs)

    cases = []
    for spec in specs:
        rel = _posix(spec.get('file') or '')
        if not rel:
            continue
        absolute = Path(root_dir) / rel
        display = _rel(absolute, root)
        describe = ' › '.join(spec.get('_chain') or [])
        line = spec.get('line') or 0
        title = spec.get('title') or ''
        for test in spec.get('tests') or []:
            project = test.get('projectName') or ''
            cases.append({
                'id': 'pw|%s|%s|%s|%s' % (project, display, line, title),
                'framework': 'playwright-ts',
                'file': display,
                'dir': _posix(Path(display).parent),
                'title': title,
                'suite': describe,
                'project': project,
                'line': line,
                'tags': list(spec.get('tags') or []),
                'generated': detect.is_generated_by_autocase(absolute),
                # 传给 CLI 的路径：Playwright 按 rootDir 解析，--list 给的 file 就是相对 rootDir 的。
                'run_path': rel,
            })
    cases.sort(key=lambda item: (item['file'], item['line'], item['project'], item['title']))
    return cases


def _pw_exec(cmd, root, handle, budget, on_progress, extra_env=None):
    """跑一组 Playwright，返回 stdout 全文。进度按行回调，几行算一条由调用方决定。"""
    env = _env_for(root)
    env.update(extra_env or {})
    proc = subprocess.Popen(cmd, cwd=str(root), env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                            errors='replace', bufsize=1)
    handle.attach(proc)
    stalled = []
    timer = _watchdog(proc, budget, stalled)

    buffer = []
    done = 0
    assert proc.stdout is not None
    for line in proc.stdout:
        buffer.append(line)
        # 自带 reporter 发 `##AC##`；退回官方 reporter 时是 list 的 `ok 1 [chromium] ›`
        if PW_REPORTER_MARK in line or PW_LINE.search(line):
            done += 1
            on_progress(done)
    proc.wait()
    timer.cancel()
    handle.detach()

    text = ''.join(buffer)
    if stalled:
        text += '\n[执行超时，已中止]\n'
    handle.log_tail = _tail(text)
    return text


def _pw_read_jsonl(path, artifacts):
    """读自带 reporter 的 JSONL。返回 (rows, env)；同一用例的多次重试只留最后一次。"""
    if not path.exists():
        return [], {}
    env = {}
    rows = {}
    for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if item.get('type') == 'env':
            env = item
            continue
        row = _pw_jsonl_row(item, artifacts, env.get('root_dir'))
        key = (row['file'], row['line'], row['title'], row['project'])
        # onTestEnd 每次重试都会回调一次，最后一次才是最终状态
        if key not in rows or row['retry'] >= rows[key]['retry']:
            rows[key] = row
    return list(rows.values()), env


def _pw_jsonl_row(item, artifacts, root_dir):
    file = _posix(item.get('file') or '')
    base = _posix(root_dir or '').rstrip('/')
    # reporter 的 file 是绝对路径，列表里用的是相对 rootDir 的路径，要对齐才能回填 id
    if base and file.startswith(base + '/'):
        file = file[len(base) + 1:]

    shots, extras = [], []
    for attachment in item.get('attachments') or []:
        rel = _under(attachment.get('path') or '', artifacts)
        if rel is None:
            continue
        extras.append({'name': attachment.get('name') or '', 'path': rel,
                       'type': attachment.get('type') or ''})
        if (attachment.get('type') or '').startswith('image/'):
            shots.append(rel)

    error = _clean(item.get('error') or '')[:1500]
    err_loc = item.get('error_location') or {}
    where = ''
    if err_loc.get('file'):
        where = '%s:%s' % (_posix(err_loc.get('file')), err_loc.get('line') or 0)
        if err_loc.get('column'):
            where += ':%s' % err_loc['column']
    status = item.get('status') or 'skipped'
    return {
        'id': '',
        'file': file,
        'line': item.get('line') or 0,
        'title': item.get('title') or '',
        'project': item.get('project') or '',
        'status': status,
        'duration_ms': int(item.get('duration_ms') or 0),
        'start_time': item.get('start_time') or '',
        'error': error,
        'where': where,
        'tags': item.get('tags') or [],
        'annotations': item.get('annotations') or [],
        'screenshots': shots,
        'artifacts': extras,
        'retry': int(item.get('retry') or 0),
        'worker': item.get('worker'),
        # 执行过就留步骤树：回看「走了哪几步、卡在哪一步、哪一步最慢」不只用得上失败用例
        'steps': item.get('steps') or [],
        'env_error': any(mark in (error or '') for mark in ENV_ERROR_MARKS),
    }


def _pw_reporter_broken(text):
    """自带 reporter 没加载起来时的特征。老版本 Playwright 或者路径被策略挡住会这样。"""
    marks = ('Cannot find module', 'ERR_MODULE_NOT_FOUND', 'No tests found',
             'Invalid reporter', 'reporter was not found')
    return any(mark in (text or '') for mark in marks)


def _pw_run(root, handle, selected, artifacts, report):
    """按 project 分组跑：同一个文件可能属于多个 project，混在一起会跑重。"""
    prefix = _pw_prefix(root)
    groups = {}
    for case in selected:
        groups.setdefault(case.get('project') or '', []).append(case)

    results = []
    env = {}
    for index, (project, items) in enumerate(groups.items()):
        if handle.stopped:
            break
        out_dir = artifacts / ('pw-%d' % index)
        out_dir.mkdir(parents=True, exist_ok=True)
        args = ['%s:%s' % (item['run_path'], item['line']) for item in items]
        label = '（%s）' % project if project else ''
        budget = max(300, 90 * len(items))

        def progress(done, _label=label):
            report('已执行 %d / %d 条%s' % (len(results) + done, len(selected), _label),
                   len(results) + done, len(selected))

        report('正在执行 %d 条用例%s' % (len(items), label), len(results), len(selected))

        # 先用 AutoCase 自带的 reporter：只有它能拿到 result.steps（每步耗时）。
        jsonl = artifacts / ('pw-%d.jsonl' % index)
        cmd = prefix + args + ['--reporter', str(PW_REPORTER), '--output', str(out_dir)]
        if project:
            cmd += ['--project', project]
        text = _pw_exec(cmd, root, handle, budget, progress,
                        extra_env={'AUTOCASE_PW_OUT': str(jsonl)})
        rows, found_env = _pw_read_jsonl(jsonl, artifacts)
        if found_env:
            env = found_env

        if not rows:
            # 自带 reporter 没产出：老版本 Playwright 或加载被挡。退回官方 JSON reporter，
            # 少一份步骤明细，但结果不能丢。
            if not _pw_reporter_broken(text):
                raise RunnerError('Playwright 没有产出结果：%s' % _tail(text)[-500:])
            json_path = artifacts / ('pw-%d.json' % index)
            text = _pw_exec(prefix + args + ['--reporter=list,json', '--output', str(out_dir)],
                            root, handle, budget, progress,
                            extra_env={'PLAYWRIGHT_JSON_OUTPUT_NAME': str(json_path)})
            data = None
            if json_path.exists():
                data = _json_blob(json_path.read_text(encoding='utf-8', errors='replace'))
            if data is None:
                data = _json_blob(text)
            if data is None:
                raise RunnerError('Playwright 没能启动或没有产出报告：%s' % _tail(text)[-500:])
            rows = _pw_collect(data, artifacts)
            env = env or {'framework': 'playwright', 'version': (data.get('config') or {}).get('version') or ''}
        results.extend(rows)

    return results, env


def _pw_collect(data, artifacts):
    specs = []
    for suite in data.get('suites') or []:
        _pw_walk(suite, [], specs)

    rows = []
    for spec in specs:
        file = _posix(spec.get('file') or '')
        line = spec.get('line') or 0
        title = spec.get('title') or ''
        for test in spec.get('tests') or []:
            project = test.get('projectName') or ''
            results = test.get('results') or []
            last = results[-1] if results else {}
            status = last.get('status') or 'skipped'
            error = _clean((last.get('error') or {}).get('message'))[:1500]
            shots, extras = [], []
            for attachment in last.get('attachments') or []:
                path = attachment.get('path') or ''
                if not path:
                    continue
                rel = _under(path, artifacts)
                if rel is None:
                    continue
                extras.append({'name': attachment.get('name') or '', 'path': rel,
                                'type': attachment.get('contentType') or ''})
                if (attachment.get('contentType') or '').startswith('image/'):
                    shots.append(rel)
            rows.append({
                'id': '',
                'file': file,
                'line': line,
                'title': title,
                'project': project,
                'status': status,
                'duration_ms': int(last.get('duration') or 0),
                'error': error,
                'screenshots': shots,
                'artifacts': extras,
                'retry': int(last.get('retry') or 0),
                'env_error': any(mark in (error or '') for mark in ENV_ERROR_MARKS),
            })
    return rows


# ---------------------------------------------------------------- Robot Framework

RF_DUMP = r'''
import json, sys
from robot.api import TestSuiteBuilder
suite = TestSuiteBuilder().build(sys.argv[1])
rows = []
def walk(node, path):
    here = path + ([node.name] if getattr(node, 'name', None) else [])
    for test in getattr(node, 'tests', []) or []:
        rows.append({
            'name': test.name,
            'suite': ' > '.join(here[:-1]),
            'source': getattr(node, 'source', '') or '',
            'tags': list(getattr(test, 'tags', []) or []),
        })
    for child in getattr(node, 'suites', []) or []:
        walk(child, here)
walk(suite, [])
sys.stdout.write(json.dumps(rows, ensure_ascii=False))
'''


def _rf_python(root):
    found, _basis = detect.find_python(root)
    return found


def _rf_test_root(root):
    """从哪开始枚举。项目习惯把用例放在「测试用例层」，有就只扫它，省得把 libspecs 也读一遍。"""
    root_path = Path(root)
    for name in ('测试用例层', 'tests', 'test', 'suites'):
        candidate = root_path / name
        if candidate.is_dir():
            return candidate
    return root_path


def _rf_cases(root, python):
    target = _rf_test_root(root)
    try:
        result = subprocess.run([python, '-c', RF_DUMP, str(target)],
                                capture_output=True, text=True, encoding='utf-8',
                                errors='replace', timeout=300, cwd=str(root), env=_env_for(root))
    except subprocess.TimeoutExpired:
        raise RunnerError('枚举用例超时（300 秒）')
    except OSError as exc:
        raise RunnerError('无法启动 Python：%s' % exc)

    if 'No module named' in (result.stderr or ''):
        raise RunnerError('目标环境没有安装 robotframework（%s），无法枚举用例' % python)
    if result.returncode != 0:
        raise RunnerError('枚举用例失败：%s' % _tail(result.stderr)[-400:])

    text = result.stdout or ''
    match = re.search(r'^\[', text, re.M)
    if not match:
        raise RunnerError('枚举用例没有返回结果')
    rows = json.loads(text[match.start():])

    cases = []
    for row in rows:
        source = row.get('source') or ''
        file = _rel(source, root) if source else ''
        cases.append({
            'id': 'rf|%s|%s' % (file, row.get('name') or ''),
            'framework': 'robot-selenium',
            'file': file,
            'dir': _posix(Path(file).parent),
            'title': row.get('name') or '',
            'suite': row.get('suite') or '',
            'project': '',
            'line': 0,
            'tags': list(row.get('tags') or []),
            'generated': detect.is_generated_by_autocase(Path(source)) if source else False,
            'run_path': file,
        })
    cases.sort(key=lambda item: (item['file'], item['title']))
    return cases


def _rf_run(root, handle, selected, artifacts, report):
    python = _rf_python(root)
    if not python:
        raise RunnerError('目标项目没有可用的 Python，无法执行 Robot 用例')

    # 整个文件都被选中时不必加 --test；只选了一部分的文件要单跑，否则 --test 会跨文件误伤。
    picked = {}
    for case in selected:
        picked.setdefault(case['file'], []).append(case)
    whole = [file for file, items in picked.items()
             if len(items) >= (items[0].get('file_total') or len(items))]
    partial = {file: items for file, items in picked.items() if file not in whole}

    jobs = []
    if whole:
        jobs.append((whole, []))
    for file, items in partial.items():
        jobs.append(([file], [item['title'] for item in items]))

    results = []
    env = {}
    done = 0
    for index, (files, names) in enumerate(jobs):
        if handle.stopped:
            break
        out_dir = artifacts / ('rf-%d' % index)
        out_dir.mkdir(parents=True, exist_ok=True)
        cmd = [python, '-m', 'robot', '--outputdir', str(out_dir), '--output', 'output.xml',
               '--log', 'NONE', '--report', 'NONE']
        for name in names:
            cmd += ['--test', name]
        cmd += files

        count = sum(len(picked[f]) for f in files)
        report('正在执行 %d 条用例' % count, done, len(selected))
        proc = subprocess.Popen(cmd, cwd=str(root), env=_env_for(root), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                                errors='replace', bufsize=1)
        handle.attach(proc)
        stalled = []
        timer = _watchdog(proc, max(600, 120 * count), stalled)

        buffer = []
        assert proc.stdout is not None
        for line in proc.stdout:
            buffer.append(line)
            if RF_LINE.match(line.rstrip()):
                done += 1
                report('已执行 %d / %d 条' % (done, len(selected)), done, len(selected))
        proc.wait()
        timer.cancel()
        handle.detach()

        text = ''.join(buffer)
        if stalled:
            text += '\n[执行超时，已中止]\n'
        handle.log_tail = _tail(text)
        rows, found_env = _rf_collect(out_dir, artifacts, root)
        env = env or found_env
        results.extend(rows)

    return results, env


def _rf_stamp(value):
    """RF 的时间戳是 `20261119 16:33:58.870`，转成 epoch 好算耗时。"""
    try:
        # 格式固定 17 位日期时间 + `.毫秒`，多切一位会把小数点带进 strptime。
        return time.mktime(time.strptime(value[:17], '%Y%m%d %H:%M:%S')) + float(value[17:] or 0)
    except (ValueError, TypeError, IndexError):
        return None


def _rf_steps(test):
    """把 Robot 的 keyword 树拍平。这是 RF 报告里最有价值的一块：
    每个 keyword 都有库名、状态、起止时间，能直接看出「时间花在哪一步、失败在哪一步」。"""
    rows = []
    limit = 200

    def walk(node, depth):
        for kw in node.findall('kw'):
            if len(rows) >= limit:
                return
            status = kw.find('status')
            raw = (status.get('status') if status is not None else '') or ''
            start = _rf_stamp(status.get('starttime')) if status is not None else None
            end = _rf_stamp(status.get('endtime')) if status is not None else None
            rows.append({
                'title': kw.get('name') or '',
                'category': kw.get('library') or '',
                'duration_ms': int(max(0, (end - start) * 1000)) if start and end else 0,
                'status': 'passed' if raw == 'PASS' else ('skipped' if raw == 'SKIP' else 'failed'),
                'error': '',
                'depth': depth,
            })
            walk(kw, depth + 1)

    walk(test, 0)
    return rows


def _rf_env(root):
    raw = root.get('generator') or ''
    # 形如 `Robot 3.1.2 (Python 3.9.13 on win32)`
    match = re.match(r'(.+?)\s+([\d][\w.]*)\s*\((.*?)\)', raw)
    if match:
        return {'framework': 'robot-selenium', 'tool': match.group(1).strip(),
                'version': match.group(2), 'runtime': match.group(3).strip(),
                'generator': raw}
    return {'framework': 'robot-selenium', 'version': '', 'runtime': '', 'generator': raw}


def _rf_collect(out_dir, artifacts, workspace=None):
    xml_path = Path(out_dir) / 'output.xml'
    if not xml_path.exists():
        return [], {}
    try:
        root = ET.parse(str(xml_path)).getroot()
    except ET.ParseError:
        return [], {}

    rows = []
    # 按 suite 遍历而不是 iter('test')：只有这样才能拿到用例所属文件的 source，
    # 报告里的用例名匹配失败时还有文件可以兜底。
    for suite in root.iter('suite'):
        source = suite.get('source') or ''
        rel_file = _rel(source, workspace) if (source and workspace) else _posix(source)
        for test in suite.findall('test'):
            status = test.find('status')
            raw = (status.get('status') if status is not None else '') or ''
            start = _rf_stamp(status.get('starttime')) if status is not None else None
            end = _rf_stamp(status.get('endtime')) if status is not None else None
            duration = int(max(0, (end - start) * 1000)) if start and end else 0
            state = 'passed' if raw == 'PASS' else ('skipped' if raw == 'SKIP' else 'failed')

            messages = [(m.get('level'), _clean(m.text)) for m in test.iter('msg')]
            error = ''
            for level, text in messages:
                if level == 'FAIL' and text:
                    error = text[:1500]
                    break

            # 用例自己 Capture Page Screenshot 的产物：msg 里有 href="xxx.png"，按文件名归属。
            shots = []
            for _level, text in messages:
                for name in re.findall(r'href="([^"]+\.(?:png|jpg|jpeg))"', text or '', re.I):
                    candidate = Path(out_dir) / name
                    if candidate.exists():
                        rel = _under(candidate, artifacts)
                        if rel and rel not in shots:
                            shots.append(rel)

            doc = test.find('doc')
            # Robot 3.x 把标签放在 <tags><tag>..</tag></tags> 里，
            # 老格式才是 <test> 下直接挂 <tag>；两种都收。
            tags = [tag.text for tag in test.findall('tags/tag') if tag.text]
            if not tags:
                tags = [tag.text for tag in test.findall('tag') if tag.text]
            rows.append({
                'id': '',
                'file': rel_file,
                'line': 0,
                'title': test.get('name') or '',
                'project': '',
                'status': state,
                'duration_ms': duration,
                'start_time': (status.get('starttime') if status is not None else '') or '',
                'error': error,
                'where': '',
                'tags': tags,
                'annotations': [],
                'doc': _clean(doc.text)[:300] if doc is not None and doc.text else '',
                'screenshots': shots,
                'artifacts': [],
                'retry': 0,
                'worker': None,
                # 跟 Playwright 一样：跑过的用例一律留步骤，不只失败的那几条
                'steps': _rf_steps(test),
                'env_error': False,
            })

    return rows, _rf_env(root)


# ---------------------------------------------------------------- 统一入口

def _framework(workspace):
    found = detect.detect(str(workspace))
    return (found or {}).get('framework')


def discover(workspace, force=False):
    """返回 {framework, cases, note}。不执行任何用例。"""
    text = str(workspace or '').strip()
    if not text:
        raise RunnerError('还没有选择工作区')
    root = Path(text).resolve()
    if not root.is_dir():
        raise RunnerError('工作区不存在：%s' % text)

    framework = _framework(root)
    key = (str(root), framework)
    now = time.time()
    if not force:
        with _CACHE_LOCK:
            hit = _CACHE.get(key)
        if hit and now - hit[0] < CACHE_TTL:
            return hit[1]

    note = None
    if framework == 'robot-selenium':
        python = _rf_python(str(root))
        if not python:
            raise RunnerError('工作区里没有 Python 环境（venv/.venv），无法枚举 Robot 用例')
        cases = _rf_cases(str(root), python)
    elif framework == 'playwright-ts':
        cases = _pw_cases(str(root))
    else:
        raise RunnerError('这个工作区既没有 .robot 用例，也没有 @playwright/test（package.json），无法执行')

    if not cases:
        note = '没有找到可执行的用例'

    # 文件级总数：Robot 用它判断「整个文件都选中了」，可以省掉 --test 参数。
    totals = {}
    for case in cases:
        totals[case['file']] = totals.get(case['file'], 0) + 1
    for case in cases:
        case['file_total'] = totals[case['file']]

    data = {'framework': framework, 'cases': cases, 'note': note,
            'root': str(root), 'discovered_at': now}
    with _CACHE_LOCK:
        _CACHE[key] = (now, data)
    return data


class RunHandle:
    """一次运行的可变状态：线程、子进程、已停止标记、实时进度。

    进度只放内存不落库：一次几百条用例，每来一行就写 sqlite 会把库锁住。
    """

    def __init__(self, run_id):
        self.run_id = run_id
        self.proc = None
        self.stopped = False
        self.thread = None
        self.message = '准备执行'
        self.done = 0
        self.total = 0
        self.lock = threading.Lock()

    def attach(self, proc):
        with self.lock:
            self.proc = proc

    def detach(self):
        with self.lock:
            self.proc = None

    def report(self, message, done, total):
        with self.lock:
            self.message = message
            self.done = done
            self.total = total

    def stop(self):
        self.stopped = True
        with self.lock:
            proc = self.proc
        _kill_tree(proc)

    def snapshot(self):
        with self.lock:
            return {'message': self.message, 'done': self.done, 'total': self.total,
                    'stopped': self.stopped}


def progress(run_id):
    with _ACTIVE_LOCK:
        handle = _ACTIVE.get(run_id)
    return handle.snapshot() if handle else None


def start(app, workspace, selection, framework=None):
    """建立运行记录并起线程。selection 是用例 id 列表。"""
    root = Path(str(workspace or '')).resolve()
    data = discover(str(root))
    framework = framework or data['framework']
    known = {case['id']: case for case in data['cases']}
    picked = [case_id for case_id in (selection or []) if case_id in known]
    if not picked:
        raise RunnerError('选中的用例在当前工作区里已经找不到了，请刷新用例后重选')

    with _ACTIVE_LOCK:
        if _ACTIVE:
            raise RunnerError('已经有一次运行在进行中，等它结束再开始')
        run = TestRun(workspace=str(root), framework=framework, status='running',
                      total=len(picked), selection_json=json.dumps(picked, ensure_ascii=False))
        db.session.add(run)
        db.session.commit()
        handle = RunHandle(run.id)
        handle.total = len(picked)
        _ACTIVE[run.id] = handle

    thread = threading.Thread(target=_execute, args=(app, run.id, handle, known),
                              name='test-run-%s' % run.id, daemon=True)
    handle.thread = thread
    thread.start()
    return run


def stop(run_id):
    with _ACTIVE_LOCK:
        handle = _ACTIVE.get(run_id)
    if handle is None:
        return False
    handle.stop()
    return True


def _execute(app, run_id, handle, cases):
    with app.app_context():
        run = db.session.get(TestRun, run_id)
        if run is None:
            return
        artifacts = Path(_artifacts_root()) / run_id
        artifacts.mkdir(parents=True, exist_ok=True)
        run.artifacts_dir = str(artifacts)
        db.session.commit()

        started = time.time()
        try:
            selected = [cases[case_id] for case_id in run.selection() if case_id in cases]
            if run.framework == 'robot-selenium':
                rows, env = _rf_run(Path(run.workspace), handle, selected, artifacts, handle.report)
                rows = _label(rows, selected, by_name=True)
            else:
                rows, env = _pw_run(Path(run.workspace), handle, selected, artifacts, handle.report)
                rows = _label(rows, selected, by_name=False)
            _persist(run, rows, started, handle, env)
        except RunnerError as exc:
            run.status = 'stopped' if handle.stopped else 'error'
            run.error = str(exc)
            run.finished_at = datetime.utcnow()
            run.duration_ms = int((time.time() - started) * 1000)
            db.session.commit()
        except Exception as exc:  # noqa: BLE001 - 线程里不能把异常吞掉，要落到记录上
            db.session.rollback()
            run.status = 'error'
            run.error = '执行器内部错误：%s' % exc
            run.finished_at = datetime.utcnow()
            db.session.commit()
        finally:
            with _ACTIVE_LOCK:
                _ACTIVE.pop(run_id, None)
            _prune()


def _dump_results(env, rows):
    return json.dumps({'env': env, 'rows': rows}, ensure_ascii=False)


def _trim_steps(rows):
    """把通过用例的步骤清掉，只留失败的。返回清掉了多少条。

    只在结果超过 RESULTS_BUDGET 时兜底调用：正常选几十条跑远到不了这个量级。
    """
    dropped = 0
    for row in rows:
        if row.get('steps') and row['status'] not in FAILED_STATUSES:
            row['steps'] = []
            dropped += 1
    return dropped


def _persist(run, rows, started, handle, env=None):
    # results_json 从「一个数组」扩成 {env, rows}：报告头部要展示「什么环境跑出来的」，
    # 但 test_runs 表已经是既有的，加列不会自动迁移，只能塞进这个 JSON 列。
    # 读取侧兼容旧的纯数组格式，见 TestRun.results()。
    env = dict(env or {})
    text = _dump_results(env, rows)
    if len(text.encode('utf-8')) > RESULTS_BUDGET:
        env['steps_trimmed'] = _trim_steps(rows)
        text = _dump_results(env, rows)
    run.results_json = text
    run.total = len(rows)
    run.passed = sum(1 for row in rows if row['status'] == 'passed')
    run.failed = sum(1 for row in rows if row['status'] in FAILED_STATUSES)
    run.skipped = sum(1 for row in rows if row['status'] == 'skipped')
    # 重试过才通过的才算 flaky。Playwright 的 status 永远不会有 'flaky' 这个值，
    # 之前按 status 数，所以这一栏一直是 0。
    run.flaky = sum(1 for row in rows if row['status'] == 'passed' and row.get('retry'))
    run.duration_ms = int((time.time() - started) * 1000)
    run.finished_at = datetime.utcnow()
    if handle.stopped:
        run.status = 'stopped'
    elif not rows:
        run.status = 'error'
        run.error = run.error or '没有执行到任何用例'
    elif run.failed:
        run.status = 'failed'
    else:
        run.status = 'passed'
    db.session.commit()


def _label(rows, selected, by_name=False):
    """把报告里的条目对回用例列表里的那条。

    Playwright 报告的 file 是相对 rootDir 的，跟列表里相对工作区的路径不是一套，
    所以优先用「run_path + 行号 + 标题 + project」精确匹配，退回「文件名 + 标题」。
    Robot 的 output.xml 没有文件归属，只能按名字对。
    """
    if by_name:
        buckets = {}
        for case in selected:
            buckets.setdefault(case['title'], []).append(case)
        for row in rows:
            bucket = buckets.get(row['title']) or []
            hit = bucket.pop(0) if bucket else None
            if hit:
                row['id'] = hit['id']
                row['file'] = hit['file']
                row['project'] = hit['project']
            else:
                row['id'] = 'rf|%s|%s' % (row['file'], row['title'])
        return rows

    exact = {(case['run_path'], case['line'], case['title'], case['project']): case
             for case in selected}
    loose = {}
    for case in selected:
        loose.setdefault((Path(case['file']).name, case['title'], case['project']), []).append(case)

    for row in rows:
        hit = exact.get((row['file'], row['line'], row['title'], row['project']))
        if hit is None:
            bucket = loose.get((Path(row['file']).name, row['title'], row['project'])) or []
            hit = bucket.pop(0) if bucket else None
        if hit:
            row['id'] = hit['id']
            row['file'] = hit['file']
    return rows


# ---------------------------------------------------------------- 产物

def _artifacts_root():
    from flask import current_app
    try:
        configured = current_app.config.get('TEST_RUN_FOLDER')
    except RuntimeError:
        configured = None
    root = Path(configured) if configured else (Path(__file__).resolve().parents[4] / 'storage' / 'test-runs')
    root.mkdir(parents=True, exist_ok=True)
    return root


def artifact_path(run, rel):
    """只允许读这次运行目录里的文件：路径参数来自前端，不能直接拼。"""
    if not run.artifacts_dir:
        raise RunnerError('这次运行没有产物目录')
    base = Path(run.artifacts_dir).resolve()
    target = (base / str(rel or '')).resolve()
    if target != base and base not in target.parents:
        raise RunnerError('非法的产物路径')
    if not target.is_file():
        raise RunnerError('产物不存在')
    return target


def _prune():
    """产物只留最近 N 次；记录本身不清，报告页还能看到结论和错误。"""
    from flask import current_app
    try:
        keep = int(current_app.config.get('TEST_RUN_KEEP', 20) or 20)
    except RuntimeError:
        keep = 20
    runs = TestRun.query.order_by(TestRun.started_at.desc()).all()
    for run in runs[keep:]:
        if not run.artifacts_dir:
            continue
        path = Path(run.artifacts_dir)
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        run.artifacts_dir = None
    db.session.commit()
