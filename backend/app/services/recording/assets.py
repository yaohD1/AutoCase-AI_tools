"""工作区里的测试文件资产。

导入类用例需要一个真实文件（Excel 模板、图片、压缩包），而这个文件必须跟着用例走：
换机器、进 CI，绝对路径一定失效。所以统一存进工作区根的 .assets/，
步骤里只记相对路径，回放和生成代码都按项目根解析。

去重按内容哈希：同一份模板被多条用例引用时，磁盘上只有一份。

浏览器拿不到用户选中的文件的绝对路径（安全限制），所以前端是把字节传上来的，
这里负责落盘和命名。文件名会被净化——它同时要出现在磁盘、步骤 JSON、生成的代码里，
带路径分隔符或引号会让后面每一步都要转义。
"""

import hashlib
import re
from pathlib import Path

ASSET_DIRNAME = '.assets'
# 名字里留着中文（业务模板常常就叫「终端导入模板.xlsx」），只截长度。
MAX_STEM = 60
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Windows 的保留设备名：叫 con.xlsx 的文件在 Windows 上根本创建不了。
_RESERVED = {'con', 'prn', 'aux', 'nul'} | {'com%d' % i for i in range(1, 10)} \
            | {'lpt%d' % i for i in range(1, 10)}


class AssetError(Exception):
    """可展示给用户的失败原因。"""


def safe_name(filename):
    """只保留最后一段并去掉非法字符。空名或全被删掉时给个兜底名。"""
    raw = str(filename or '').replace('\\', '/').split('/')[-1].strip()
    cleaned = _BAD_CHARS.sub('', raw).strip(' .')
    stem, dot, suffix = cleaned.rpartition('.')
    if not dot:
        stem, suffix = cleaned, ''
    stem = stem[:MAX_STEM].strip(' .')
    if stem.lower() in _RESERVED:
        stem = '_' + stem
    if not stem:
        stem = 'file'
    return stem + ('.' + suffix[:16] if suffix else '')


def ensure_dir(workspace):
    if not workspace:
        raise AssetError('还没有选择工作区，文件资产要跟着项目走，请先选工作区')
    folder = Path(workspace) / ASSET_DIRNAME
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def rel_path(workspace, path):
    """绝对路径 → 工作区相对路径（正斜杠）。给步骤里存的永远是这一份。"""
    root = Path(workspace).resolve()
    return Path(path).resolve().relative_to(root).as_posix()


def abs_path(workspace, rel):
    """相对路径 → 绝对路径，并挡住 .. 和绝对路径。

    步骤 JSON 会经前端回传、也会被生成侧读取，路径参数不能直接拼接。
    """
    text = str(rel or '').strip().replace('\\', '/')
    if not text:
        raise AssetError('这一步还没有选文件')
    candidate = Path(text)
    if candidate.is_absolute() or '..' in candidate.parts:
        raise AssetError('文件路径必须是工作区内的相对路径：%s' % text)
    root = Path(workspace or '').resolve()
    target = (root / candidate).resolve()
    if target != root and root not in target.parents:
        raise AssetError('文件不在工作区里：%s' % text)
    return target


def sha256_of(path, chunk=1 << 20):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(chunk), b''):
            digest.update(block)
    return digest.hexdigest()


def _same_content(folder, digest, size):
    """按体积先筛，再算哈希。资产通常只有几十个，不值得维护一份索引。"""
    for item in folder.iterdir():
        if not item.is_file() or item.name.startswith('.'):
            continue
        try:
            if item.stat().st_size != size:
                continue
            if sha256_of(item) == digest:
                return item
        except OSError:
            continue
    return None


def _free_name(folder, name):
    target = folder / name
    if not target.exists():
        return target
    stem, dot, suffix = name.rpartition('.')
    if not dot:
        stem, suffix = name, ''
    for index in range(2, 1000):
        candidate = folder / ('%s-%d%s' % (stem, index, ('.' + suffix) if suffix else ''))
        if not candidate.exists():
            return candidate
    raise AssetError('同名文件太多了，换一个名字再试：%s' % name)


def save(workspace, filename, data):
    """落盘并返回描述。内容相同的文件不会存第二份。"""
    folder = ensure_dir(workspace)
    size = len(data)
    if size <= 0:
        raise AssetError('文件是空的：%s' % filename)
    digest = hashlib.sha256(data).hexdigest()
    hit = _same_content(folder, digest, size)
    if hit is not None:
        return {'path': rel_path(workspace, hit), 'name': hit.name,
                'size': size, 'sha256': digest, 'dedup': True}
    target = _free_name(folder, safe_name(filename))
    target.write_bytes(data)
    return {'path': rel_path(workspace, target), 'name': target.name,
            'size': size, 'sha256': digest, 'dedup': False}


def listing(workspace):
    """已有资产，给前端做「从这里挑一个」的下拉。不传哈希，避免每次打开都全盘算一遍。"""
    if not workspace:
        return []
    folder = Path(workspace) / ASSET_DIRNAME
    if not folder.is_dir():
        return []
    items = []
    for item in sorted(folder.iterdir()):
        if not item.is_file() or item.name.startswith('.'):
            continue
        try:
            stat = item.stat()
        except OSError:
            continue
        items.append({'path': rel_path(workspace, item), 'name': item.name,
                      'size': stat.st_size, 'mtime': int(stat.st_mtime)})
    return items
