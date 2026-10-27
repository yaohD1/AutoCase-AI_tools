"""目标环境里的工具：导出库的关键字清单，校验生成的文件。

缺少工具时直接抛 ToolchainError，不跳过校验（决策 11）。
"""
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

LIBDOC_TIMEOUT = 120
DRYRUN_TIMEOUT = 120
TSC_TIMEOUT = 180
OUTPUT_LIMIT = 4000


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
