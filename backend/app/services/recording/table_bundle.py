"""表格行断言的辅助库源码（生成到目标项目里，不在这里执行）。

和 FilesLibrary / SliderLibrary 同一个规格：内容写进 `.autocase/libraries/`（RF）
或 `<用例目录>/utils/`（Playwright），由生成端在 Settings / import 里引用。

判定逻辑只有一份（table_probe.MATCH_JS）：平台里「验证当前页面」和生成出去的回放
用的是同一段 JS，不会出现「平台上说成立、跑起来却不成立」。
"""
import json

from app.services.recording.table_probe import MATCH_JS

_TABLE_PY = r'''"""表格行断言：同一行里多列同时成立才算通过。

Robot 里这样用（条件三元组是「表头文字, 录制时的列序号, 期望值」）::

    Assert Table Row    ${操作日志表格行}    [('操作', 0, '导入终端资产'), ('操作结果', 3, '成功')]    scope=any

- 先按表头文字找列，表头里找不到才退回列序号：页面加一列、调一下顺序，
  只要表头没改名就还对得上。
- 期望值里带 * 或 ? 时按通配匹配（``RF4039_REF_*`` 能匹配带时间戳的文件名），
  否则按「包含」匹配。省略号是 CSS 截断，读到的是全文，不影响包含判定。
- ``scope=first`` 只认第一行（日志表里就是最新一条），默认 any 是「任意一行」。
  ``scope=custom`` 配 ``row_range`` 只认指定的几行，例如 ``row_range=2`` 或 ``row_range=2-5``。
- ``timeout`` 大于 0 时每秒重查一次，超时才失败。轮询刷新由调用方用
  ``Wait Until Keyword Succeeds`` 包，不在这里做——刷新是脚本的事。

由 AutoCase 生成。平台重新生成时会覆盖，不要手改。
"""
import ast
import time

from robot.api import logger
from robot.libraries.BuiltIn import BuiltIn

MATCH_JS = r"""
__MATCH_JS__
"""

# Selenium 的 execute_script 把脚本文本当**函数体**执行：直接传箭头函数只会求值、
# 不会调用，返回值永远是 None（不是报错，最容易漏）。所以这里包成「调用 + return」。
_MATCH_CALL = 'return (' + MATCH_JS + ')(arguments[0]);'


class TableLibrary(object):
    """同一行里多列同时成立。"""

    ROBOT_LIBRARY_SCOPE = 'GLOBAL'

    def __init__(self):
        self._selenium = None

    @property
    def selenium(self):
        # 不在 __init__ 里取：Robot 加载库的顺序不保证 SeleniumLibrary 已经建好。
        if self._selenium is None:
            self._selenium = BuiltIn().get_library_instance('SeleniumLibrary')
        return self._selenium

    def assert_table_row(self, rows, conditions, scope='any', timeout=0, row_range=''):
        """断言表格里有一行同时满足全部列条件。

        scope=custom 时 row_range 写「2」或「2-5」（都是表格里的绝对行号，从 1 数）。
        """
        checks = self._conditions(conditions)
        try:
            limit = max(0.0, float(timeout or 0))
        except (TypeError, ValueError):
            limit = 0.0
        deadline = time.time() + limit
        while True:
            result = self._match(rows, checks, scope, row_range)
            if result.get('ok'):
                at = int(result.get('index') or 0) + 1
                logger.info('表格行断言通过：第 %d 行 %s'
                            % (at, ' / '.join(result.get('row') or [])))
                return result
            if limit > 0 and time.time() < deadline:
                time.sleep(1)
                continue
            raise AssertionError(self._failure(checks, scope, result, row_range))

    def _match(self, rows, checks, scope, row_range=''):
        selector = str(rows or '').strip()
        if selector.lower().startswith('css='):
            selector = selector[4:].strip()
        if selector.lower().startswith('xpath='):
            raise AssertionError(
                '表格行断言的行定位必须用 CSS：%s\n'
                '（XPath 不能交给浏览器的 querySelectorAll。重新拾取这张表即可）' % rows)
        return self.selenium.driver.execute_script(_MATCH_CALL, {
            'rows': selector,
            'headerSelector': '',
            'columns': checks,
            'scope': scope or 'any',
            'scopeRows': str(row_range or '').strip(),
        })

    @staticmethod
    def _conditions(raw):
        data = raw
        if isinstance(raw, str):
            text = raw.strip()
            try:
                data = ast.literal_eval(text)
            except (ValueError, SyntaxError):
                # 兼容手写的 «表头=期望值;表头=期望值» 写法，列序号按 0 兜底。
                data = []
                for part in text.split(';'):
                    part = part.strip()
                    if not part:
                        continue
                    bits = part.split('=')
                    if len(bits) >= 2:
                        data.append((bits[0].strip(), 0, '='.join(bits[1:]).strip()))
        if not isinstance(data, (list, tuple)):
            raise AssertionError('表格行断言的条件格式不对：%r' % (raw,))
        out = []
        for item in data:
            try:
                if isinstance(item, dict):
                    out.append({'header': str(item.get('header') or ''),
                                'index': int(item.get('index') or 0),
                                'value': str(item.get('value') or '')})
                elif isinstance(item, (list, tuple)) and len(item) >= 3:
                    out.append({'header': str(item[0]), 'index': int(item[1]),
                                'value': str(item[2])})
                elif isinstance(item, (list, tuple)) and len(item) == 2:
                    out.append({'header': str(item[0]), 'index': 0, 'value': str(item[1])})
            except (TypeError, ValueError):
                continue
        if not out:
            raise AssertionError('表格行断言没有可用的列条件：%r' % (raw,))
        return out

    @staticmethod
    def _failure(checks, scope, result, row_range=''):
        lines = ['表格里没有同时满足这些条件的行：']
        for item in checks:
            name = item['header'] or ('第 %d 列' % (item['index'] + 1))
            lines.append('  · %s 包含 %s' % (name, item['value']))
        if scope == 'first':
            span = '第一行'
        elif scope == 'custom':
            span = ('第 %s 行' % str(row_range).replace('-', '~')) if row_range else '自定义行'
        else:
            span = '任意一行'
        lines.append('行范围：%s；找到 %s 行' % (span, result.get('total')))
        headers = [h.strip() for h in (result.get('headers') or []) if h and h.strip()]
        if headers:
            lines.append('表头：%s' % ' / '.join(headers))
        sample = result.get('sample') or []
        if sample:
            lines.append('看到 %d 行：' % len(sample))
            for item in sample:
                if isinstance(item, dict):
                    cells = item.get('cells') or []
                    lines.append('  %s) %s' % (item.get('at') or '?', ' / '.join(cells)))
                else:
                    lines.append('  %s' % ' / '.join(item))
        if not result.get('total'):
            lines.append('提示：一行都没找到，表格行定位可能已经变了，重新拾取这张表')
        return '\n'.join(lines)
'''

_TABLE_TS = r'''/**
 * 表格行断言：同一行里多列同时成立才算通过。
 *
 * 由 AutoCase 生成。平台重新生成时会覆盖，不要手改。
 *
 * 判定口径和平台里的「验证当前页面」完全一致（同一段浏览器侧逻辑）：
 * - 先按表头文字找列，找不到才退回录制时的列序号；
 * - 期望值含 * 或 ? 按通配，否则按「包含」；
 * - scope 为 first 只认第一行，默认 any 是任意一行；custom 配 rowRange（'2' 或 '2-5'）
 *   只认指定的几行（绝对行号，从 1 数）；
 * - timeout 大于 0 时每秒重查一次。轮询刷新由调用方用 expect.poll 包。
 *
 * 注意：表格在 iframe 里时要把 frame 传进来（page.frame(...)），
 * `page.evaluate` 只跑在主文档上。
 */
import { Frame, Page } from '@playwright/test';

export type RowCondition = [string, number, string];
export type RowScope = 'any' | 'first' | 'custom';

const MATCH_JS = __MATCH_JS__;

function matchArgs(rows: string, conditions: RowCondition[], scope: RowScope, rowRange = '') {
  return {
    rows,
    headerSelector: '',
    columns: conditions.map(([header, index, value]) => ({ header, index, value })),
    scope,
    scopeRows: rowRange || '',
  };
}

/** 只回答「现在成不成立」。刷新轮询用 expect.poll 包它，避免 assert 版直接抛错。 */
export async function tableRowMatches(
  page: Page | Frame,
  rows: string,
  conditions: RowCondition[],
  scope: RowScope = 'any',
  rowRange = ''
): Promise<boolean> {
  const result = await page.evaluate(MATCH_JS, matchArgs(rows, conditions, scope, rowRange));
  return !!(result && result.ok);
}

export async function assertTableRow(
  page: Page | Frame,
  rows: string,
  conditions: RowCondition[],
  scope: RowScope = 'any',
  timeout = 0,
  rowRange = ''
): Promise<void> {
  const deadline = Date.now() + Math.max(0, timeout) * 1000;
  let last: any = null;
  while (true) {
    last = await page.evaluate(MATCH_JS, matchArgs(rows, conditions, scope, rowRange));
    if (last && last.ok) return;
    if (timeout > 0 && Date.now() < deadline) {
      await page.waitForTimeout(1000);
      continue;
    }
    throw new Error(failureMessage(conditions, scope, last, rowRange));
  }
}

function failureMessage(conditions: RowCondition[], scope: string, result: any, rowRange = ''): string {
  const lines = ['表格里没有同时满足这些条件的行：'];
  for (const [header, index, value] of conditions) {
    lines.push(`  · ${header || `第 ${index + 1} 列`} 包含 ${value}`);
  }
  const span = scope === 'first' ? '第一行'
    : scope === 'custom' ? (rowRange ? `第 ${rowRange.replace('-', '~')} 行` : '自定义行')
      : '任意一行';
  lines.push(`行范围：${span}；找到 ${result?.total ?? 0} 行`);
  const headers = (result?.headers || []).filter((h: string) => h && h.trim());
  if (headers.length) lines.push(`表头：${headers.join(' / ')}`);
  const sample: any[] = result?.sample || [];
  if (sample.length) {
    lines.push(`看到 ${sample.length} 行：`);
    for (const item of sample) {
      if (item && typeof item === 'object' && !Array.isArray(item)) {
        lines.push(`  ${item.at ?? '?'}) ${(item.cells || []).join(' / ')}`);
      } else {
        lines.push(`  ${(item || []).join(' / ')}`);
      }
    }
  }
  if (!result?.total) lines.push('提示：一行都没找到，表格行定位可能已经变了，重新拾取这张表');
  return lines.join('\n');
}
'''

TABLE_PY = _TABLE_PY.replace('__MATCH_JS__', MATCH_JS)
# TS 里必须写字面量：模板字符串会把 JS 正则里的 \s \\ 当转义处理（\s 直接语法错），
# 用 JSON 转义成普通字符串最稳。
TABLE_TS = _TABLE_TS.replace('__MATCH_JS__', json.dumps(MATCH_JS))
