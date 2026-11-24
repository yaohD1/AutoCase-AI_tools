"""下载文件的落盘等待与内容校验。

三个容易踩的点都收在这里：

1. 浏览器是先写 `.crdownload` 再改名的。改名没发生就去读，读到的是 0 字节或者干脆不存在。
2. 同名文件会被加上 `(1)`：按精确文件名找，第二次跑必失配。
3. 下载目录里可能有上一轮的残留。不按「本次新增」判断，会拿旧文件报通过——这是最难发现的假通过。

行数校验不引第三方依赖：xlsx 本质是 zip，直接数 `xl/worksheets/*.xml` 里的 `<row`；
csv 用标准库按常见编码依次尝试。目的只有一个——抓住「导出成功但表是空的」。
"""

import csv
import fnmatch
import io
import re
import time
import zipfile
from pathlib import Path

# 未完成的下载：Chromium 用 .crdownload，Firefox 用 .part，其余是少数下载器的习惯。
PART_SUFFIXES = ('.crdownload', '.part', '.download', '.tmp')
_ROW_TAG = re.compile(rb'<row[ >]')

# 从用户拾取到的元素出发找到真正的 input[type=file]，返回它的 CSS 路径（找不到返回空串）。
#
# 用户能点到的永远是按钮或「拖到这里」的框，真 input 被组件藏起来（display:none 或
# 尺寸为 0），拾取工具点不到它。而 Playwright 的 set_input_files 不要求元素可见，
# 只要拿到那个 input 就能塞文件，所以这一步必须由脚本自己找。
# 三级策略：自己是 → 往子孙找 → 逐层往祖先的整棵子树找（很多组件把 input 放在按钮的兄弟位置），
# 最后兜底「全页只有一个就用它」。都失败返回空串，由调用方报错让人重新拾取。
FIND_INPUT_JS = """(el) => {
  const isFile = (n) => !!n && n.tagName === 'INPUT' && String(n.type || '').toLowerCase() === 'file';
  const pathOf = (node) => {
    const parts = [];
    let cur = node;
    while (cur && cur.nodeType === 1) {
      let part = cur.tagName.toLowerCase();
      const parent = cur.parentElement;
      if (parent) {
        const same = Array.prototype.filter.call(parent.children, (c) => c.tagName === cur.tagName);
        if (same.length > 1) part += ':nth-of-type(' + (same.indexOf(cur) + 1) + ')';
      }
      parts.unshift(part);
      cur = parent;
    }
    return parts.join(' > ');
  };
  if (!el) return '';
  let hit = isFile(el) ? el : null;
  if (!hit && el.querySelector) hit = el.querySelector('input[type=file]');
  let cur = el;
  while (!hit && cur && cur.parentElement) {
    cur = cur.parentElement;
    hit = cur.querySelector ? cur.querySelector('input[type=file]') : null;
  }
  if (!hit) {
    const all = document.querySelectorAll('input[type=file]');
    if (all.length === 1) hit = all[0];
  }
  return hit ? pathOf(hit) : '';
}"""


class FileWaitError(Exception):
    """等到超时也没等到文件。"""


def baseline(folder):
    """点击下载之前先拍一张目录快照，之后只认新增的文件。"""
    path = Path(folder)
    if not path.is_dir():
        return set()
    return {item.name for item in path.iterdir() if item.is_file()}


def _finished(item):
    return item.is_file() and not item.name.lower().endswith(PART_SUFFIXES)


def _matches(name, pattern):
    if not pattern:
        return True
    return fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(name.lower(), pattern.lower())


def wait_download(folder, before, timeout=30.0, pattern=None, interval=0.2):
    """等到目录里出现一个「新增且写完」的文件，返回它的路径。

    写完的判定是体积连续两次采样不变。只出现一次就返回会在慢盘上读到半截文件，
    而半截的 xlsx 连 zip 都打不开，报出来的错会指向错误的方向。
    """
    path = Path(folder)
    path.mkdir(parents=True, exist_ok=True)
    seen = {}
    deadline = time.monotonic() + max(1.0, float(timeout))
    while True:
        for item in path.iterdir():
            if item.name in before or not _finished(item) or not _matches(item.name, pattern):
                continue
            try:
                size = item.stat().st_size
            except OSError:
                continue
            prev = seen.get(item.name)
            if prev is not None and prev == size:
                return item
            seen[item.name] = size
        if time.monotonic() >= deadline:
            raise FileWaitError('等了 %g 秒没有出现下载完成的文件（目录：%s）' % (timeout, path))
        time.sleep(interval)


def count_rows(path, kind):
    """返回数据行数（含表头）。算不出来时抛 ValueError，错误信息要能直接给用户看。"""
    target = Path(path)
    if kind == 'xlsx':
        return _xlsx_rows(target)
    if kind == 'csv':
        return _csv_rows(target)
    raise ValueError('不支持的校验方式：%s' % kind)


def _xlsx_rows(target):
    try:
        with zipfile.ZipFile(target) as bundle:
            sheets = sorted(n for n in bundle.namelist()
                            if n.startswith('xl/worksheets/') and n.endswith('.xml'))
            if not sheets:
                raise ValueError('这个文件不像 xlsx（压缩包里没有 worksheets）')
            data = bundle.read(sheets[0])
    except zipfile.BadZipFile:
        # 最常见的两种：老版 .xls 被改了后缀，或者下载到一半。
        raise ValueError('这个文件不是有效的 xlsx（可能是旧版 xls，或者只下了一半）')
    return len(_ROW_TAG.findall(data))


def _csv_rows(target):
    raw = target.read_bytes()
    if not raw:
        raise ValueError('csv 是空的')
    text = None
    for encoding in ('utf-8-sig', 'gbk', 'utf-16', 'latin-1'):
        try:
            text = raw.decode(encoding)
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    if text is None:
        raise ValueError('csv 的编码认不出来（试过 utf-8 / gbk / utf-16）')
    rows = list(csv.reader(io.StringIO(text)))
    # 只有分隔符的空行不算数据行：导出工具经常在末尾补几个。
    return len([row for row in rows if any(cell.strip() for cell in row)])


def describe(path):
    target = Path(path)
    try:
        stat = target.stat()
    except OSError:
        return {'name': target.name, 'size': 0, 'exists': False}
    return {'name': target.name, 'size': stat.st_size, 'exists': True,
            'ext': target.suffix.lower().lstrip('.')}


def verify(path, expect=None):
    """按 expect 校验一个已经落盘的文件，永远返回结果而不是抛异常。

    expect: {'name': '*.xlsx', 'min_bytes': 1024, 'parse': 'xlsx', 'min_rows': 2}
    留空或 parse 为 none 时只看「存在 + 体积」。
    """
    expect = expect or {}
    target = Path(path)
    info = describe(target)
    result = {'ok': False, 'name': info['name'], 'size': info['size'],
              'path': str(target), 'rows': None, 'error': None}
    if not info['exists']:
        result['error'] = '下载目录里没有这个文件：%s' % info['name']
        return result
    min_bytes = int(expect.get('min_bytes') or 0)
    if min_bytes and info['size'] < min_bytes:
        result['error'] = '文件只有 %d 字节，小于要求的 %d 字节' % (info['size'], min_bytes)
        return result
    pattern = str(expect.get('name') or '').strip()
    if pattern and not _matches(info['name'], pattern):
        result['error'] = '文件名「%s」不符合期望的「%s」' % (info['name'], pattern)
        return result
    parse = str(expect.get('parse') or 'none').strip().lower()
    min_rows = int(expect.get('min_rows') or 0)
    if parse != 'none':
        try:
            rows = count_rows(target, parse)
        except ValueError as exc:
            result['error'] = str(exc)
            return result
        except Exception as exc:  # noqa: BLE001 - 解析失败的原因千奇百怪，统一转成可读信息
            result['error'] = '内容校验失败：%s' % exc
            return result
        result['rows'] = rows
        if min_rows and rows < min_rows:
            result['error'] = '只有 %d 行，少于要求的 %d 行（很可能是空表）' % (rows, min_rows)
            return result
    result['ok'] = True
    return result
