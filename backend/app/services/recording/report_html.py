"""把一次运行导出成单文件 HTML。

为什么不用项目自带的 report.html / allure：前者与被测项目的框架绑定，
后者要求项目装插件、还要 193MB 的产物目录。这里只吃数据库里已经存好的结果，
产出一个可以离线双击打开的文件 —— 截图内联成 base64，不依赖任何外部资源。
"""
import base64
import html
from datetime import datetime

from app.services.recording import runner

# 单张截图和总量都设上限：RF 项目一张截图可能 1MB，几十条失败全内联会做出几十兆的文件。
MAX_SHOT_BYTES = 2 * 1024 * 1024
MAX_SHOT_TOTAL = 20 * 1024 * 1024
# 低于这个耗时的通过步骤算「胶水」（Call Method / Evaluate 这类），报告里默认不出现。
QUIET_STEP_MS = 10

STATUS_LABEL = {
    'passed': '通过', 'failed': '失败', 'timedOut': '超时',
    'interrupted': '中断', 'skipped': '跳过', 'error': '错误', 'stopped': '已停止',
}

CSS = """
:root{--bg:#f7f8fa;--card:#fff;--ink:#1b1f24;--ink2:#4a5361;--muted:#8a94a3;
--line:#e3e7ed;--ok:#2f9e5e;--bad:#d64545;--warn:#c98a13;--accent:#2f6fed;--sunken:#f1f3f7}
@media (prefers-color-scheme:dark){:root{--bg:#12161d;--card:#1a1f27;--ink:#e8ecf2;--ink2:#a9b3c1;
--muted:#77808d;--line:#2a3140;--ok:#4ac07a;--bad:#ef6b6b;--warn:#d9a43a;--accent:#5b8cff;--sunken:#151a21}}
*{box-sizing:border-box}
body{margin:0;padding:28px 22px 60px;background:var(--bg);color:var(--ink);
font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif}
.wrap{max-width:1080px;margin:0 auto}
h1{font-size:19px;margin:0 0 4px;font-weight:600}
.sub{color:var(--muted);font-size:12.5px;margin-bottom:18px}
.env{font-size:12px;color:var(--ink2);background:var(--sunken);border:1px solid var(--line);
border-radius:8px;padding:7px 11px;margin-bottom:16px;word-break:break-all}
.note{font-size:11.5px;color:var(--warn);margin:-8px 0 16px 2px}
.stats{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-bottom:20px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:11px 13px}
.stat b{display:block;font-size:21px;line-height:1.25;font-weight:600;font-variant-numeric:tabular-nums}
.stat span{font-size:11.5px;color:var(--muted)}
.stat.is-ok b{color:var(--ok)}.stat.is-bad b{color:var(--bad)}
.group{margin-bottom:14px}
.group-head{font-size:12px;color:var(--muted);padding:6px 2px;font-family:ui-monospace,Consolas,monospace}
.case{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:11px 14px;margin-bottom:8px}
.case.is-bad{border-color:color-mix(in srgb,var(--bad) 40%,var(--line))}
.row{display:flex;align-items:baseline;gap:9px}
.dot{width:7px;height:7px;border-radius:50%;flex:none;background:var(--ok);margin-top:7px}
.dot.bad{background:var(--bad)}.dot.warn{background:var(--warn)}.dot.mut{background:var(--muted)}
.title{font-size:13.5px;flex:1;min-width:0;word-break:break-word}
.dur{font-size:12px;color:var(--muted);font-variant-numeric:tabular-nums;flex:none}
.meta{font-size:11.5px;color:var(--muted);margin:3px 0 0 16px;word-break:break-all}
.err{margin:9px 0 0 16px;padding:9px 11px;background:color-mix(in srgb,var(--bad) 8%,transparent);
border-left:2px solid var(--bad);border-radius:4px;font:12px/1.55 ui-monospace,Consolas,monospace;
color:var(--ink2);white-space:pre-wrap;word-break:break-word;max-height:320px;overflow:auto}
.where{color:var(--accent);font-family:ui-monospace,Consolas,monospace}
.steps{margin:9px 0 0 16px;border:1px solid var(--line);border-radius:7px;overflow:hidden}
.steps-label{font-size:11px;color:var(--muted);margin:11px 0 5px 16px}
.step{display:flex;gap:8px;padding:4px 10px;font:11.5px/1.5 ui-monospace,Consolas,monospace;
border-bottom:1px solid var(--line);color:var(--ink2);align-items:baseline}
.step:last-child{border-bottom:0}
.step.bad{background:color-mix(in srgb,var(--bad) 10%,transparent);color:var(--bad)}
/* 折叠：details/summary 自带展开态，不需要一行 JS，直接拿浏览器当组件用 */
details.fold>summary{list-style:none}
details.fold>summary::-webkit-details-marker{display:none}
details.fold>summary{cursor:pointer}
.caret{flex:none;width:11px;text-align:center;color:var(--muted)}
details.fold[open]>summary .caret{display:inline-block;transform:rotate(90deg)}
.step-t{flex:0 1 auto;min-width:0;word-break:break-word}
.step-c{flex:none;margin-right:auto;font-size:10px;color:var(--muted)}
.step-d{flex:none;margin-left:12px;color:var(--muted);font-variant-numeric:tabular-nums}
.step-d.slow{color:var(--warn)}
.shots{display:flex;gap:8px;flex-wrap:wrap;margin:9px 0 0 16px}
.shot{display:block;border:1px solid var(--line);border-radius:6px;overflow:hidden;max-width:240px}
.shot img{display:block;width:100%;height:auto}
.foot{margin-top:24px;font-size:11.5px;color:var(--muted);text-align:center}
"""


def _esc(value):
    return html.escape(str(value if value is not None else ''))


def _status_class(status):
    if status == 'passed':
        return 'dot'
    if status in ('skipped',):
        return 'dot mut'
    if status in ('failed', 'timedOut', 'interrupted', 'error'):
        return 'dot bad'
    return 'dot warn'


def _ms(value):
    millis = int(value or 0)
    if millis < 1000:
        return '%dms' % millis
    if millis < 60000:
        return '%.1fs' % (millis / 1000.0)
    return '%dm%02ds' % (millis // 60000, round(millis % 60000 / 1000.0))


def _env_line(env, run):
    if not env:
        return ''
    bits = []
    tool = env.get('tool') or ('Playwright' if env.get('framework') == 'playwright-ts' else 'Robot')
    if env.get('version'):
        bits.append('%s %s' % (tool, env['version']))
    if env.get('runtime'):
        bits.append(env['runtime'])
    if env.get('projects'):
        bits.append('projects: %s' % ', '.join(env['projects']))
    bits.append(run.workspace)
    return '<div class="env">%s</div>' % _esc(' · '.join(bits))


def _trim_note(env):
    """结果超过体积上限时只留了失败用例的步骤，报告里得说清楚少了什么。"""
    try:
        dropped = int(env.get('steps_trimmed') or 0)
    except (TypeError, ValueError):
        return ''
    if not dropped:
        return ''
    return ('<div class="note">结果体积超过上限，已省略 %d 条通过用例的执行步骤；'
            '失败用例的步骤都还在。</div>' % dropped)


def _step_tree(steps):
    """把拍平的 steps 按 depth 还原成树。

    RF 的 keyword 树是代码级的，一个登录用例能拍出一百步，大半是 Call Method 这种
    0ms 胶水；整片铺出来没人看得下去，所以报告里默认只铺顶层，靠折叠往下钻。
    """
    roots, stack = [], []
    for index, step in enumerate(steps):
        node = dict(step)
        node['idx'] = index
        node['children'] = []
        depth = max(0, int(step.get('depth') or 0))
        parent = stack[depth - 1] if depth > 0 and depth - 1 < len(stack) else None
        (parent['children'] if parent else roots).append(node)
        stack = stack[:depth] + [node]
    return roots


def _node_failed(node):
    return node.get('status') == 'failed' or any(_node_failed(item) for item in node['children'])


def _node_quiet(node):
    """0ms 且没有子节点的通过步骤：框架内部的胶水调用，默认不出现在报告里。"""
    return (not node['children'] and node.get('status') == 'passed'
            and (node.get('duration_ms') or 0) < QUIET_STEP_MS)


def _step_row(node, depth, slowest):
    cost = node.get('duration_ms') or 0
    # 单步耗时占这条用例最慢一步的 60% 以上时标黄：一眼看出时间花在哪
    slow = ' slow' if cost >= slowest * 0.6 and cost > 200 else ''
    return ('<span class="caret" style="margin-left:%dpx">%s</span>'
            '<span class="step-t">%s</span><span class="step-c">%s</span>'
            '<span class="step-d%s">%s</span>'
            % (depth * 13, '›' if node['children'] else '', _esc(node.get('title')),
               _esc(node.get('category') or ''), slow, _esc(_ms(cost))))


def _steps_html(steps):
    if not steps:
        return ''
    slowest = max((step.get('duration_ms') or 0) for step in steps) or 1
    roots = _step_tree(steps)
    hidden = [0]

    def walk(nodes, depth):
        out = []
        for node in nodes:
            if _node_quiet(node):
                hidden[0] += 1
                continue
            bad = ' bad' if node.get('status') == 'failed' else ''
            if node['children']:
                # 带失败的子树默认展开：打开报告就能看到挂在哪一步，不用一格格点
                out.append('<details class="fold"%s><summary class="step%s">%s</summary>'
                           '<div class="kids">%s</div></details>'
                           % (' open' if _node_failed(node) else '', bad,
                              _step_row(node, depth, slowest), walk(node['children'], depth + 1)))
            else:
                out.append('<div class="step%s">%s</div>' % (bad, _step_row(node, depth, slowest)))
        return ''.join(out)

    body = walk(roots, 0)
    label = ('共 %d 步' % len(steps) if len(roots) == len(steps)
             else '%d 个顶层步骤 · 共 %d 步' % (len(roots), len(steps)))
    if hidden[0]:
        label += '（已隐藏 %d 个 0ms 步骤）' % hidden[0]
    return '<div class="steps-label">执行步骤 · %s</div><div class="steps">%s</div>' % (label, body)


def _shots_html(run, paths, budget):
    """返回 (html, 用掉的字节)。单张过大或总量超限就跳过，宁可少图也不要几十兆的文件。"""
    parts = []
    for rel in paths or []:
        if budget[0] <= 0:
            break
        try:
            path = runner.artifact_path(run, rel)
        except runner.RunnerError:
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if len(data) > MAX_SHOT_BYTES or len(data) > budget[0]:
            continue
        budget[0] -= len(data)
        encoded = base64.b64encode(data).decode('ascii')
        parts.append('<a class="shot" href="data:image/png;base64,%s" target="_blank">'
                     '<img src="data:image/png;base64,%s" alt="%s"></a>'
                     % (encoded, encoded, _esc(rel)))
    if not parts:
        return ''
    return '<div class="shots">%s</div>' % ''.join(parts)


def _case_html(run, row, budget):
    status = row.get('status') or ''
    ok = status == 'passed'
    parts = ['<div class="case%s">' % ('' if ok else ' is-bad')]
    parts.append('<div class="row"><span class="%s"></span><span class="title">%s</span>'
                 '<span class="dur">%s</span></div>'
                 % (_status_class(status), _esc(row.get('title')),
                    _esc(_ms(row.get('duration_ms')))))

    # 文件名已经在分组头上，卡片里不再重复一遍
    meta = []
    if row.get('project'):
        meta.append('project %s' % _esc(row['project']))
    if row.get('tags'):
        meta.append(' / '.join(_esc(tag) for tag in row['tags']))
    if row.get('retry'):
        meta.append('重试 %d 次' % int(row['retry']))
    if meta:
        parts.append('<div class="meta">%s</div>' % ' · '.join(meta))

    if row.get('where'):
        parts.append('<div class="meta">失败位置 <span class="where">%s</span></div>' % _esc(row['where']))
    if row.get('error'):
        parts.append('<pre class="err">%s</pre>' % _esc(row['error']))
    parts.append(_steps_html(row.get('steps') or []))
    parts.append(_shots_html(run, row.get('screenshots') or [], budget))
    parts.append('</div>')
    return ''.join(parts)


def render(run):
    """整份报告。run 是已经加载完的 TestRun。"""
    rows = run.results()
    env = run.env() or {}
    status = STATUS_LABEL.get(run.status, run.status or '')
    started = run.started_at.strftime('%Y-%m-%d %H:%M:%S') if run.started_at else ''
    title = '%s · %s' % (run.workspace.rsplit('\\', 1)[-1].rsplit('/', 1)[-1] or '测试运行', status)

    groups = {}
    for row in rows:
        groups.setdefault(row.get('file') or '（未归属文件）', []).append(row)

    budget = [MAX_SHOT_TOTAL]
    body = []
    for file, items in groups.items():
        body.append('<div class="group"><div class="group-head">%s</div>' % _esc(file))
        for row in items:
            body.append(_case_html(run, row, budget))
        body.append('</div>')

    if not rows:
        body.append('<div class="case"><div class="row"><span class="dot mut"></span>'
                    '<span class="title">这次运行没有产生结果</span></div></div>')

    stat_defs = [
        ('通过', run.passed, 'is-ok'), ('失败', run.failed, 'is-bad'),
        ('跳过', run.skipped, ''), ('不稳定', run.flaky, ''), ('耗时', _ms(run.duration_ms), ''),
    ]
    stats = ''.join('<div class="stat %s"><b>%s</b><span>%s</span></div>' % (cls, _esc(value), _esc(label))
                    for label, value, cls in stat_defs)

    return """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%s</title><style>%s</style></head>
<body><div class="wrap">
<h1>%s</h1>
<div class="sub">%s 开始 · 共 %d 条用例%s</div>
%s
%s
<div class="stats">%s</div>
%s
<div class="foot">由 AutoCase 导出 · %s</div>
</div></body></html>""" % (
        _esc(title), CSS, _esc(title), _esc(started), len(rows),
        _esc(' · %s' % env.get('generator', '')) if env.get('generator') else '',
        _env_line(env, run), _trim_note(env), stats, ''.join(body),
        datetime.now().strftime('%Y-%m-%d %H:%M'),
    )
