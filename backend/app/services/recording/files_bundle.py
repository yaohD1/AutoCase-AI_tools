"""生成到目标项目里的文件工具源码。

两个框架各一份，语义对齐（上传 / 下载 / 校验三个动作同名同参）：

- `FILES_TS` → `utils/files.ts`，给 Playwright 用；
- `FILES_PY` → `libraries/FilesLibrary.py`，给 Robot + SeleniumLibrary 用。

和滑块那次不同的是，这次两边都要落盘：SeleniumLibrary 虽然自带 `Choose File`
（上传一行就够），但下载没有任何等价 API，必须自己配目录、轮询落盘、再校验内容。

两份实现都不引第三方依赖：
- xlsx 本质是 zip，直接读 `xl/worksheets/*.xml` 数 `<row`，不需要 openpyxl / exceljs；
- 下载完成的判定统一是「排除 .crdownload/.part + 体积连续两次采样一致」。
"""

FILES_TS = r"""/**
 * 文件导入 / 导出的通用封装。
 *
 * 由 AutoCase 生成。平台重新生成时会覆盖，不要手改。
 *
 * 两条约定：
 * 1. 路径一律相对项目根（assets/终端导入模板.xlsx）。绝对路径换一台机器就断。
 * 2. 下载目录按「每次运行一个」隔离，否则上一轮的残留文件会让这一次假通过。
 *
 * 上传全程不碰操作系统的文件对话框：setInputFiles 直接把文件写进 input[type=file]，
 * 界面上弹不弹框都不影响结果。
 */
import { existsSync, mkdirSync, readFileSync, statSync } from 'node:fs';
import { basename, extname, join } from 'node:path';
import { inflateRawSync } from 'node:zlib';

import type { Locator, Page } from '@playwright/test';

export interface FileInfo {
  name: string;
  size: number;
  path: string;
}

export interface FileExpect {
  /** 期望的文件名，支持通配（*.xlsx）；留空表示不限制 */
  name?: string;
  /** 最小字节数。抓「导出了 0 字节」 */
  minBytes?: number;
  /** 内容校验方式。none 只看存在和体积 */
  parse?: 'none' | 'xlsx' | 'csv';
  /** 最少行数（含表头）。抓「导出成功但表是空的」 */
  minRows?: number;
}

export interface DownloadOptions {
  /** 落盘目录，默认项目根的 downloads/ */
  dir?: string;
  /** 等下载开始的秒数 */
  timeout?: number;
  /** 给定时顺便校验，省一次调用 */
  expect?: FileExpect;
}

/** 相对项目根的路径 → 绝对路径，顺带挡住绝对路径和 .. */
function projectPath(rel: string): string {
  const text = String(rel || '').replace(/\\/g, '/').trim();
  if (!text) throw new Error('这一步没有选文件');
  if (text.startsWith('/') || /^[a-zA-Z]:/.test(text) || text.split('/').includes('..')) {
    throw new Error(`文件路径必须是项目内的相对路径：${text}`);
  }
  return join(process.cwd(), text);
}

function safeName(name: string): string {
  const base = String(name || '').replace(/\\/g, '/').split('/').pop() || 'download';
  const cleaned = base.replace(/[<>:"/\\|?*\x00-\x1f]/g, '').replace(/^[.\s]+|[.\s]+$/g, '');
  return cleaned || 'download';
}

function matchName(name: string, pattern: string): boolean {
  const escaped = pattern.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*');
  return new RegExp(`^${escaped}$`, 'i').test(name);
}

/**
 * 把文件塞进上传控件。
 *
 * 传进来的通常是「选择文件」按钮，真 input 被组件藏起来了。按三级找：
 * 自己 → 子孙 → 逐层祖先的整棵子树（很多组件把 input 放在按钮的兄弟位置）。
 * 从 target 派生的 locator 会保留 frame 上下文，所以 iframe 里的上传控件也找得到。
 */
export async function uploadFile(target: Locator, relPath: string): Promise<FileInfo> {
  const abs = projectPath(relPath);
  if (!existsSync(abs)) {
    throw new Error(`导入的文件不存在：${relPath}（assets 要跟用例一起提交，不能只在录制那台机器上）`);
  }
  const info: FileInfo = { name: basename(abs), size: statSync(abs).size, path: relPath };

  const selfTag = await target
    .evaluate((el) => `${el.tagName}|${(el as HTMLInputElement).type || ''}`)
    .catch(() => '');
  if (selfTag.toLowerCase() === 'input|file') {
    await target.setInputFiles(abs);
    return info;
  }

  let scope: Locator = target;
  for (let depth = 0; depth < 6; depth += 1) {
    const inputs = scope.locator('input[type="file"]');
    if ((await inputs.count()) > 0) {
      await inputs.first().setInputFiles(abs);
      return info;
    }
    const parent = scope.locator('..');
    if ((await parent.count()) === 0) break;
    scope = parent;
  }
  throw new Error('这个元素不是文件上传控件，也没在它附近找到 input[type="file"]，请重新拾取按钮所在的区域');
}

/**
 * 点一下触发下载，等文件真的落地再返回。
 *
 * 用 download 事件而不是轮询目录：事件由浏览器直接给，saveAs 写完才返回，
 * 天然不会读到 .crdownload 那种半截文件。
 */
export async function downloadFile(
  page: Page,
  trigger: () => Promise<void>,
  options: DownloadOptions = {},
): Promise<FileInfo> {
  const dir = options.dir || join(process.cwd(), 'downloads');
  mkdirSync(dir, { recursive: true });
  const timeout = (options.timeout ?? 30) * 1000;
  const [download] = await Promise.all([
    page.waitForEvent('download', { timeout }),
    trigger(),
  ]);
  const name = safeName(download.suggestedFilename());
  let target = join(dir, name);
  const ext = extname(name);
  for (let index = 2; existsSync(target) && index < 1000; index += 1) {
    target = join(dir, `${name.slice(0, name.length - ext.length)}-${index}${ext}`);
  }
  await download.saveAs(target);
  const result: FileInfo = { name: basename(target), size: statSync(target).size, path: target };
  if (options.expect) verifyFile(result, options.expect);
  return result;
}

/** 按 expect 校验一个已经落盘的文件。不通过直接抛，消息里写清是哪一条不满足。 */
export function verifyFile(file: FileInfo, expect: FileExpect = {}): { rows?: number } {
  if (expect.name && !matchName(file.name, expect.name)) {
    throw new Error(`文件名「${file.name}」不符合期望的「${expect.name}」`);
  }
  if (expect.minBytes && file.size < expect.minBytes) {
    throw new Error(`文件只有 ${file.size} 字节，小于要求的 ${expect.minBytes} 字节`);
  }
  const parse = expect.parse || 'none';
  if (parse === 'none') return {};
  const rows = countRows(file.path, parse);
  if (expect.minRows && rows < expect.minRows) {
    throw new Error(`只有 ${rows} 行，少于要求的 ${expect.minRows} 行（很可能是空表）`);
  }
  return { rows };
}

/** 数行（含表头）。xlsx 走 zip 解压，csv 按文本行数。 */
export function countRows(file: string, kind: 'xlsx' | 'csv'): number {
  if (kind === 'csv') {
    const text = readFileSync(file, 'utf8').replace(/^\uFEFF/, '');
    return text.split(/\r?\n/).filter((line) => line.trim()).length;
  }
  const buf = readFileSync(file);
  const sheet = readZipEntry(buf, (name) => /^xl\/worksheets\/.*\.xml$/.test(name));
  if (!sheet) throw new Error('这个文件不像 xlsx（压缩包里没有 worksheets，或者是只下了一半）');
  return (sheet.toString('utf8').match(/<row[ >]/g) || []).length;
}

/**
 * 从 zip 里读一个 entry。只支持 stored / deflate，不处理 zip64——
 * 够读 xlsx，不值得为它引一个依赖。
 */
function readZipEntry(buf: Buffer, match: (name: string) => boolean): Buffer | null {
  let eocd = -1;
  const floor = Math.max(0, buf.length - 65558);
  for (let i = buf.length - 22; i >= floor; i -= 1) {
    if (buf.readUInt32LE(i) === 0x06054b50) {
      eocd = i;
      break;
    }
  }
  if (eocd < 0) return null;
  const total = buf.readUInt16LE(eocd + 10);
  let ptr = buf.readUInt32LE(eocd + 16);
  for (let i = 0; i < total; i += 1) {
    if (ptr + 46 > buf.length || buf.readUInt32LE(ptr) !== 0x02014b50) return null;
    const method = buf.readUInt16LE(ptr + 10);
    const compSize = buf.readUInt32LE(ptr + 20);
    const nameLen = buf.readUInt16LE(ptr + 28);
    const extraLen = buf.readUInt16LE(ptr + 30);
    const commentLen = buf.readUInt16LE(ptr + 32);
    const localOff = buf.readUInt32LE(ptr + 42);
    const name = buf.toString('utf8', ptr + 46, ptr + 46 + nameLen);
    if (match(name)) {
      const dataStart = localOff + 30 + buf.readUInt16LE(localOff + 26) + buf.readUInt16LE(localOff + 28);
      const raw = buf.subarray(dataStart, dataStart + compSize);
      if (method === 0) return Buffer.from(raw);
      if (method === 8) return inflateRawSync(raw);
      return null;
    }
    ptr += 46 + nameLen + extraLen + commentLen;
  }
  return null;
}
"""


FILES_PY = r'''"""文件导入 / 导出的通用关键字（Robot Framework + SeleniumLibrary）。

由 AutoCase 生成。平台重新生成时会覆盖，不要手改。

上传只用 SeleniumLibrary 自带的 Choose File —— 它内部就是给 input[type=file]
send_keys 一个路径，不会拉起操作系统的文件对话框。

下载必须自己来：Selenium 没有 download 事件，只能靠浏览器配置指定下载目录，
再轮询目录等文件写完。所以这里的 Download File 有两个前提：
  1. driver 的 options 里 download.default_directory 必须等于本库的 download_dir；
  2. download.prompt_for_download 必须是 False。
两条都在 *** Settings *** 的公共 resource 里配一次，见生成时写下的注释。
"""
import csv
import fnmatch
import io
import os
import re
import time
import zipfile
from pathlib import Path

from robot.api.deco import keyword

PART_SUFFIXES = ('.crdownload', '.part', '.download', '.tmp')
ROW_TAG = re.compile(rb'<row[ >]')
FIND_INPUT_JS = """
const isFile = (n) => !!n && n.tagName === 'INPUT' && String(n.type || '').toLowerCase() === 'file';
if (isFile(arguments[0])) return arguments[0];
let hit = arguments[0].querySelector ? arguments[0].querySelector('input[type=file]') : null;
let cur = arguments[0];
while (!hit && cur && cur.parentElement) {
  cur = cur.parentElement;
  hit = cur.querySelector ? cur.querySelector('input[type=file]') : null;
}
return hit || null;
"""


class FilesLibrary:
    ROBOT_LIBRARY_SCOPE = 'GLOBAL'
    ROBOT_LIBRARY_VERSION = '1.0'

    def __init__(self, download_dir=None):
        base = download_dir or os.environ.get('AUTOCASE_DOWNLOAD_DIR') or 'downloads'
        self.download_dir = Path(base).resolve()
        self._selenium = None

    @property
    def selenium(self):
        if self._selenium is None:
            from robot.libraries.BuiltIn import BuiltIn
            self._selenium = BuiltIn().get_library_instance('SeleniumLibrary')
        return self._selenium

    # ---- 上传 ----

    @keyword('Upload File')
    def upload_file(self, locator, rel_path, timeout=10):
        """把项目内的文件塞进上传控件，返回 {name, size, path}。

        locator 指向的通常是「选择文件」按钮，不是 input 本身：真 input 被组件藏着，
        点不到也拾取不到，所以这里从它出发往上逐层找最近的 input[type=file]。
        """
        path = self._project_path(rel_path)
        if not path.is_file():
            raise AssertionError('导入的文件不存在：%s（assets 要跟用例一起提交）' % rel_path)
        element = self.selenium.get_webelement(locator)
        self.selenium.wait_until_element_is_enabled(element, timeout)
        target = self._file_input(element)
        if target is None:
            raise AssertionError(
                '这个元素不是文件上传控件，也没在它附近找到 input[type=file]，请重新拾取按钮所在的区域')
        self.selenium.choose_file(target, str(path))
        return {'name': path.name, 'size': path.stat().st_size, 'path': rel_path}

    def _file_input(self, element):
        return self.selenium.execute_javascript(FIND_INPUT_JS, element)

    # ---- 下载 ----

    @keyword('Download File')
    def download_file(self, locator, expected='', min_bytes=0, parse='none', min_rows=0, timeout=30):
        """点一下触发下载，等文件写完再校验，返回 {name, size, rows, path}。

        timeout 只覆盖「等文件出现」这一段，点击本身的超时由 Selenium 的
        implicit wait 决定，两者别混在一个数字里。
        """
        before = self._snapshot()
        self.selenium.click_element(locator)
        path = self._wait_download(before, expected, float(timeout))
        rows = self._verify(path, expected, int(min_bytes), parse, int(min_rows))
        return {'name': path.name, 'size': path.stat().st_size, 'rows': rows, 'path': str(path)}

    @keyword('Wait For Download')
    def wait_for_download(self, expected='', timeout=30):
        """不点击，只等目录里出现一个完成态的新文件。"""
        return str(self._wait_download(set(), expected, float(timeout)))

    def _snapshot(self):
        if not self.download_dir.is_dir():
            return set()
        return {item.name for item in self.download_dir.iterdir() if item.is_file()}

    def _wait_download(self, before, pattern, timeout):
        self.download_dir.mkdir(parents=True, exist_ok=True)
        seen = {}
        deadline = time.monotonic() + max(1.0, timeout)
        while True:
            for item in self.download_dir.iterdir():
                if item.name in before or not item.is_file():
                    continue
                if item.name.lower().endswith(PART_SUFFIXES):
                    continue
                if pattern and not (fnmatch.fnmatch(item.name, pattern)
                                    or fnmatch.fnmatch(item.name.lower(), pattern.lower())):
                    continue
                try:
                    size = item.stat().st_size
                except OSError:
                    continue
                # 体积连续两次不变才算写完：只出现一次就返回，会在慢盘上读到半截文件。
                if seen.get(item.name) == size:
                    return item
                seen[item.name] = size
            if time.monotonic() >= deadline:
                raise AssertionError(
                    '等了 %g 秒没有出现下载完成的文件（目录：%s）' % (timeout, self.download_dir))
            time.sleep(0.2)

    # ---- 校验 ----

    def _verify(self, path, expected, min_bytes, parse, min_rows):
        size = path.stat().st_size
        if expected and not fnmatch.fnmatch(path.name, expected):
            raise AssertionError('文件名「%s」不符合期望的「%s」' % (path.name, expected))
        if min_bytes and size < min_bytes:
            raise AssertionError('文件只有 %d 字节，小于要求的 %d 字节' % (size, min_bytes))
        parse = (parse or 'none').strip().lower()
        if parse == 'none':
            return None
        rows = self._count_rows(path, parse)
        if min_rows and rows < min_rows:
            raise AssertionError('只有 %d 行，少于要求的 %d 行（很可能是空表）' % (rows, min_rows))
        return rows

    def _count_rows(self, path, kind):
        if kind == 'xlsx':
            try:
                with zipfile.ZipFile(str(path)) as bundle:
                    sheets = sorted(n for n in bundle.namelist()
                                    if n.startswith('xl/worksheets/') and n.endswith('.xml'))
                    if not sheets:
                        raise AssertionError('这个文件不像 xlsx（压缩包里没有 worksheets）')
                    data = bundle.read(sheets[0])
            except zipfile.BadZipFile:
                raise AssertionError('这个文件不是有效的 xlsx（可能是旧版 xls，或者只下了一半）')
            return len(ROW_TAG.findall(data))
        if kind == 'csv':
            raw = path.read_bytes()
            for encoding in ('utf-8-sig', 'gbk', 'utf-16', 'latin-1'):
                try:
                    text = raw.decode(encoding)
                    break
                except (UnicodeDecodeError, UnicodeError):
                    continue
            else:
                raise AssertionError('csv 的编码认不出来（试过 utf-8 / gbk / utf-16）')
            rows = list(csv.reader(io.StringIO(text)))
            return len([row for row in rows if any(cell.strip() for cell in row)])
        raise AssertionError('不支持的校验方式：%s' % kind)

    def _project_path(self, rel):
        text = str(rel or '').replace('\\', '/').strip()
        if not text:
            raise AssertionError('这一步没有选文件')
        candidate = Path(text)
        if candidate.is_absolute() or '..' in candidate.parts:
            raise AssertionError('文件路径必须是项目内的相对路径：%s' % text)
        return (Path.cwd() / candidate).resolve()
'''
