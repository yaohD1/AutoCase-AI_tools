"""表格行断言：在当前页面上找「同一行里多列同时成立」的那一行。

为什么不用「一条 xpath + 元素可见」：

- 条件里常有动态值（带时间戳的文件名），xpath 写不进去；
- 列一多，xpath 就长得没法读，写手很容易把它简化成「行里包含某段文字」，
  列对齐的约束静默丢失，测试绿着但什么也没证明；
- 失败时只会说「找不到元素」，看不到表格里到底是什么。

所以改成：录制时把「行定位 + 表头 + 各列在行内的位置」采下来，判定交给一段固定 JS。
线上脚本走平台落盘的 TableLibrary，这里的 JS 同时用于「验证当前页面」和单测，
两边判定口径完全一致。

**表头是运行时再对一次的**：条件里存表头文字 + 录制时的列序号，运行时先按表头文字
找列，找不到才退回列序号。页面加了一列、调了顺序，只要表头没改名就还能对上。
"""
import re


class TableProbeError(ValueError):
    """表格读不出来 / 判定不了，可向用户展示。"""


# 一个单元格的文字怎么算：块级子元素之间 innerText 会插换行，统一压成单空格。
# 截断（省略号）是 CSS 行为，innerText 仍是全文，所以「包含」匹配不受影响。
MATCH_JS = r"""
(args) => {
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const CELL_SEL = 'td, th, [role="gridcell"], [role="cell"]';
  const cellsOf = (row) => {
    const direct = Array.from(row.children || []).filter((c) => c.matches && c.matches(CELL_SEL));
    if (direct.length) return direct;
    return Array.from(row.querySelectorAll(CELL_SEL)).filter(
      (c) => c.closest('tr, [role="row"], [role="listitem"]') === row);
  };
  // 表头不给选择器时就现场找：Selenium 那边只传行定位，没有第二段选择器可用。
  const headerScopeOf = (row) => {
    const elTable = row.closest('.el-table');
    if (elTable) return { root: elTable, sel: '.el-table__header-wrapper th' };
    const ant = row.closest('.ant-table');
    if (ant) return { root: ant, sel: '.ant-table-thead th' };
    const grid = row.closest('[role="grid"], [role="table"]');
    if (grid) return { root: grid, sel: '[role="columnheader"]' };
    const table = row.closest('table');
    if (table) return { root: table, sel: 'thead th' };
    return null;
  };

  let rowEls = [];
  try { rowEls = Array.from(document.querySelectorAll(args.rows)); } catch (e) { rowEls = []; }
  // role=grid 的 `[role=row]` 会把表头行也算进来（它的子元素是 columnheader）。
  // 统一在这里剔掉：否则「首行」会命中表头，条件永远不成立。
  rowEls = rowEls.filter((r) => !r.querySelector('[role="columnheader"]'));
  if (!rowEls.length) return { ok: false, total: 0, sample: [], error: 'rows-not-found' };

  let headerEls = [];
  try {
    if (args.headerSelector) {
      headerEls = Array.from(document.querySelectorAll(args.headerSelector));
    } else {
      const scope = headerScopeOf(rowEls[0]);
      if (scope) headerEls = Array.from(scope.root.querySelectorAll(scope.sel));
    }
  } catch (e) { headerEls = []; }
  const headerTexts = headerEls.map((c) => clean(c.innerText));

  const columns = args.columns || [];
  const indices = columns.map((col) => {
    const want = clean(col.header || '');
    if (want && headerTexts.length) {
      const at = headerTexts.findIndex((t) => clean(t) === want);
      if (at >= 0) return at;
    }
    return typeof col.index === 'number' ? col.index : 0;
  });

  const escapeRe = (ch) => ch.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const glob = (text, pattern) => {
    let body = '';
    for (const ch of pattern) {
      if (ch === '*') body += '.*';
      else if (ch === '?') body += '.';
      else body += escapeRe(ch);
    }
    try { return new RegExp('^' + body + '$', 'i').test(text); } catch (e) { return false; }
  };
  const hit = (text, value) => {
    const t = clean(text);
    const v = clean(value);
    if (!v) return true;
    if (v.indexOf('*') >= 0 || v.indexOf('?') >= 0) return glob(t, v);
    return t.toLowerCase().indexOf(v.toLowerCase()) >= 0;
  };

  // 行范围：any 全部 / first 只要第一行 / custom 按「第 N 行」或「第 N~M 行」取窗口。
  // 下标一律用绝对行号：命中后报的「第几行」必须跟表格里数出来的一致，
  // 否则从第 3 行开始取窗口时，第 3 行会被说成第 1 行。
  let from = 0;
  let to = rowEls.length;
  if (args.scope === 'first') {
    to = Math.min(1, rowEls.length);
  } else if (args.scope === 'custom') {
    const range = String(args.scopeRows || '').match(/^(\d+)(?:-(\d+))?$/);
    if (range) {
      const a = Math.max(1, parseInt(range[1], 10) || 1);
      const b = Math.max(a, range[2] ? (parseInt(range[2], 10) || a) : a);
      from = Math.min(a - 1, rowEls.length);
      to = Math.min(b, rowEls.length);
    }
  }
  const sample = [];
  for (let i = from; i < to; i++) {
    const cells = cellsOf(rowEls[i]);
    const vals = indices.map((idx) => (cells[idx] ? clean(cells[idx].innerText) : ''));
    if (sample.length < 3) sample.push({ at: i + 1, cells: vals });
    if (indices.every((idx, k) => hit(vals[k], columns[k].value))) {
      return { ok: true, index: i, row: vals, from: from + 1, to: to,
               cells: cells.map((c) => clean(c.innerText)).slice(0, 8),
               total: rowEls.length, headers: headerTexts, indices: indices };
    }
  }
  return { ok: false, total: rowEls.length, headers: headerTexts,
           indices: indices, from: from + 1, to: to, sample: sample };
}
"""

# 录制端探测：从「用户点的那一格」出发，把这张表的行定位、表头、列位置都采下来。
# 表头与第一行用水平位置对齐，而不是靠序号硬对——Element UI 的勾选列、固定列
# 都会让表头比单元格少一个，硬对会整列错位（断言断到别的列上，还会静默通过）。
DESCRIBE_JS = r"""
(css) => {
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const CELL_SEL = 'td, th, [role="gridcell"], [role="cell"]';
  const cellsOf = (row) => {
    const direct = Array.from(row.children || []).filter((c) => c.matches && c.matches(CELL_SEL));
    if (direct.length) return direct;
    return Array.from(row.querySelectorAll(CELL_SEL)).filter(
      (c) => c.closest('tr, [role="row"], [role="listitem"]') === row);
  };
  const esc = (s) => (window.CSS && CSS.escape) ? CSS.escape(s)
    : String(s).replace(/[^\w\u4e00-\u9fff-]/g, '\\$&');

  const el = css ? document.querySelector(css) : null;
  if (!el) return { ok: false, error: '页面上找不到刚拾取的那一格，请重新拾取' };

  let row = el.closest('tr, [role="row"]');
  if (!row) {
    // li 只在「同一个容器里有一排兄弟」时当行，避免把菜单项当成列表行。
    const li = el.closest('li');
    if (li && li.parentElement && li.parentElement.children.length >= 3) row = li;
  }
  if (!row) return { ok: false, error: '这一格不在表格行里；请点表格里的单元格（或行内的文字）' };

  const shortSel = (node) => {
    if (!node || node.nodeType !== 1) return null;
    if (node.id) return '#' + esc(node.id);
    const tag = node.tagName.toLowerCase();
    const cls = Array.from(node.classList || []).filter((c) => c && c.length <= 40 &&
      !/^(is-|has-|v-|active$|disabled$|hover$|focus$|checked$|selected$|sortable$|ng-|css-)/.test(c));
    const ok = (sel) => {
      try { return document.querySelectorAll(sel).length === 1; } catch (e) { return false; }
    };
    for (const c of cls) {
      const sel = tag + '.' + esc(c);
      if (ok(sel)) return sel;
    }
    for (const a of cls) for (const b of cls) {
      if (a === b) continue;
      const sel = tag + '.' + esc(a) + '.' + esc(b);
      if (ok(sel)) return sel;
    }
    return null;
  };
  const pathSel = (node) => {
    const parts = [];
    let cur = node;
    while (cur && cur.nodeType === 1) {
      if (cur.id) { parts.unshift('#' + esc(cur.id)); break; }
      let part = cur.tagName.toLowerCase();
      const parent = cur.parentElement;
      if (parent) {
        const same = Array.from(parent.children).filter((c) => c.tagName === cur.tagName);
        if (same.length > 1) part += ':nth-of-type(' + (same.indexOf(cur) + 1) + ')';
      }
      parts.unshift(part);
      if (cur === document.body) break;
      cur = parent;
    }
    return parts.join('>');
  };

  const elTable = row.closest('.el-table');
  const antTable = row.closest('.ant-table');
  const nativeTable = row.closest('table');
  const roleRoot = row.closest('[role="grid"], [role="table"]');

  let anchor = null;
  let rowSel = '';
  let headerSel = '';
  let kind = '';
  if (elTable) {
    anchor = shortSel(elTable) || pathSel(elTable);
    rowSel = anchor + ' .el-table__body-wrapper tbody>tr';
    headerSel = anchor + ' .el-table__header-wrapper th';
    kind = 'el-table';
  } else if (antTable) {
    anchor = shortSel(antTable) || pathSel(antTable);
    rowSel = anchor + ' .ant-table-tbody>tr';
    headerSel = anchor + ' .ant-table-thead th';
    kind = 'ant-table';
  } else if (nativeTable) {
    anchor = shortSel(nativeTable) || pathSel(nativeTable);
    rowSel = anchor + '>tbody>tr';
    headerSel = anchor + ' thead th';
    kind = 'table';
  } else if (roleRoot) {
    anchor = shortSel(roleRoot) || pathSel(roleRoot);
    rowSel = anchor + ' [role="row"]';
    headerSel = anchor + ' [role="columnheader"]';
    kind = 'grid';
  } else {
    // 没有表格语义：退回「用户点的那一行的父容器」当行容器。
    const container = row.parentElement;
    if (!container) return { ok: false, error: '找不到这一行的容器' };
    anchor = shortSel(container) || pathSel(container);
    rowSel = anchor + '>' + row.tagName.toLowerCase();
    kind = 'list';
  }

  let rowEls = [];
  try { rowEls = Array.from(document.querySelectorAll(rowSel)); } catch (e) { rowEls = []; }
  if (!rowEls.length) {
    // class 锚点可能选到了隐藏的模板行：退回结构路径，宁可长一点也要能选中。
    const fallback = pathSel(row.parentElement || row);
    rowSel = fallback + '>' + row.tagName.toLowerCase();
    try { rowEls = Array.from(document.querySelectorAll(rowSel)); } catch (e) { rowEls = []; }
  }
  // 和判定端同一个口径：表头行不算数据行（role=grid 的 rows 选择器会把它带进来）。
  rowEls = rowEls.filter((r) => !r.querySelector('[role="columnheader"]'));
  if (!rowEls.length) return { ok: false, error: '找到了行，但用选择器选不中它（页面结构特殊）' };

  const firstCells = cellsOf(rowEls[0]);
  let headerEls = [];
  try { headerEls = headerSel ? Array.from(document.querySelectorAll(headerSel)) : []; }
  catch (e) { headerEls = []; }

  // 表头文字为空的是勾选列 / 展开列，不参与条件。
  let mapping = headerEls.map((_, i) => i);
  if (headerEls.length !== firstCells.length) {
    mapping = headerEls.map((hc) => {
      const r = hc.getBoundingClientRect();
      if (!r.width) return -1;
      let best = -1;
      let dist = Infinity;
      firstCells.forEach((cell, ci) => {
        const cr = cell.getBoundingClientRect();
        if (!cr.width) return;
        const d = Math.abs((cr.left + cr.right) / 2 - (r.left + r.right) / 2);
        if (d < dist) { dist = d; best = ci; }
      });
      return dist <= Math.max(8, r.width * 0.4) ? best : -1;
    });
  }
  let headers = headerEls
    .map((hc, i) => ({ name: clean(hc.innerText), index: mapping[i] }))
    .filter((h) => h.name && h.index >= 0);

  if (!headers.length) {
    // 没有表头（或表头全被合并）：用列序号兜底，名字写成「第 N 列」，
    // 界面上仍然能选，只是页面调列序时对不上——这一点在面板上会明确提示。
    headers = firstCells.map((_, i) => ({ name: '第 ' + (i + 1) + ' 列', index: i }));
  }

  return {
    ok: true,
    kind: kind,
    rows: rowSel,
    header_selector: headerSel,
    headers: headers,
    row_count: rowEls.length,
    first_row: firstCells.map((c) => clean(c.innerText)),
    has_header: headerEls.some((hc) => clean(hc.innerText)),
  };
}
"""

_WS = re.compile(r'\s+')


def _norm(text):
    return _WS.sub(' ', str(text or '')).strip()


def describe(frame, css):
    """从「刚拾取的那一格」采出这张表的行定位、表头与列位置。"""
    if frame is None:
        raise TableProbeError('没有可用的页面')
    if not css:
        raise TableProbeError('这一格没有可用的定位，请在页面上重新拾取')
    try:
        info = frame.evaluate(DESCRIBE_JS, css)
    except Exception as exc:
        raise TableProbeError('读取表格结构失败：%s' % exc)
    if not isinstance(info, dict):
        raise TableProbeError('读取表格结构失败：页面没有返回结果')
    if not info.get('ok'):
        raise TableProbeError(info.get('error') or '这张表读不出来')
    # 对外统一叫 rows_selector（和 intent.table 的字段名一致）。JS 里叫 rows 是因为
    # 那边同时还有 header_selector，短名字读起来清楚；跨层就统一，免得前端漏字段。
    info['rows_selector'] = info.pop('rows', '')
    return info


def _span_text(intent, raw=None):
    """行范围的人话描述。'any' 不写——不限制范围时提一句反而像有限制。"""
    scope = (intent or {}).get('scope') or 'any'
    if scope == 'first':
        return '第一行'
    if scope != 'custom':
        return ''
    spec = str((intent or {}).get('scope_rows') or '').strip()
    if not spec:
        return '自定义行'
    if '-' in spec:
        a, b = spec.split('-', 1)
        return '第 %s~%s 行' % (a, b)
    return '第 %s 行' % spec


def _sample_text(row):
    """失败信息里的一行。新格式是 {at, cells}，老格式是纯数组（读旧快照时还会遇到）。"""
    if isinstance(row, dict):
        cells = row.get('cells') or []
        body = ' / '.join(str(c) for c in cells)
        at = row.get('at')
        return ('第 %s 行 %s' % (at, body)) if at else body
    if isinstance(row, (list, tuple)):
        return ' / '.join(str(c) for c in row)
    return str(row)


def check(frame, intent):
    """判断这条行断言此刻是否成立，返回 verify 统一口径的结果。"""
    table = intent.get('table') or {}
    rows = (table.get('rows_selector') or '').strip()
    if not rows:
        raise TableProbeError('这条断言缺少表格行定位，请重新拾取表格')
    columns = []
    for col in (intent.get('columns') or []):
        columns.append({
            'header': col.get('header') or '',
            'index': int(col.get('index') or 0),
            'value': col.get('value') or '',
        })
    if not columns:
        raise TableProbeError('这条断言没有列条件')
    args = {
        'rows': rows,
        'headerSelector': table.get('header_selector') or '',
        'columns': columns,
        'scope': intent.get('scope') or 'any',
        'scopeRows': intent.get('scope_rows') or '',
    }
    try:
        raw = frame.evaluate(MATCH_JS, args)
    except Exception as exc:
        raise TableProbeError('在当前页面上查表格失败：%s' % exc)
    if not isinstance(raw, dict):
        raise TableProbeError('在当前页面上查表格失败：页面没有返回结果')
    expected = '；'.join('%s 包含 %s' % (c['header'] or ('第 %d 列' % (c['index'] + 1)), c['value'])
                        for c in columns)
    if raw.get('ok'):
        # 实际值给整行：只回被断言那两列的话，用户看不出「这一行是不是我要的那条」。
        cells = raw.get('cells') or raw.get('row') or []
        at = int(raw.get('index') or 0) + 1
        return {
            'ok': True,
            'kind': 'row_match',
            'expected': expected,
            'actual': '第 %d 行：%s' % (at, ' / '.join(cells)),
            'detail': '命中第 %d 行' % at,
            'row_index': at,
            'row_total': raw.get('total'),
            'row': cells,
        }
    if not raw.get('total'):
        detail = '一行都没找到：表格行定位可能已经变了，重新拾取这张表'
    else:
        detail = '表格里没有同时满足这些条件的行'
        # 自定义范围下「一行都不成立」也要说清楚看的是哪几行，否则用户会以为全表都查过了。
        span = _span_text(intent, raw)
        if raw.get('from') and raw.get('to') and raw.get('from') > raw.get('to'):
            detail = '表格现在只有 %s 行，行范围「%s」里没有可断的行' % (
                raw.get('total'), span or '自定义')
        elif span:
            detail += '（行范围：%s）' % span
        sample = raw.get('sample') or []
        if sample:
            detail += '；看到 %d 行：%s' % (
                len(sample), '；'.join(_sample_text(r) for r in sample))
    return {
        'ok': False,
        'kind': 'row_match',
        'expected': expected,
        'actual': None,
        'detail': detail,
        'row_total': raw.get('total'),
    }
