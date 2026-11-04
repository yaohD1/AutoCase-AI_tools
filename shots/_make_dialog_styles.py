# -*- coding: utf-8 -*-
"""生成三种「匹配到多个元素」弹窗样式的对照图（只用于选型，不进产品代码）。"""
import io
import os
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
IMG = '_live_target.png'
# 截图裁到被测区域后剩 1139 CSS 像素宽（原 1536 × 0.742），高 738
W, H = 1139.0, 738.0
BOXES = [
    (720, 62, 88, 32, '智能问答'),
    (818, 62, 88, 32, '智能摘要'),
    (916, 62, 88, 32, '文档解析'),
    (1014, 62, 88, 32, '分段规则'),
]
ROWS = [
    ('智能问答', 'button · 顶部工具栏'),
    ('智能摘要', 'button · 顶部工具栏 · 录制时点的'),
    ('文档解析', 'button · 顶部工具栏'),
    ('分段规则', 'button · 顶部工具栏'),
]


def boxes():
    out = []
    for i, (x, y, w, h, _t) in enumerate(BOXES):
        out.append(
            '<span class="box%s" style="left:%.3f%%;top:%.3f%%;width:%.3f%%;height:%.3f%%">'
            '<b>%d</b></span>' % (' on' if i == 1 else '',
                                  x / W * 100, y / H * 100, w / W * 100, h / H * 100, i + 1))
    return ''.join(out)


def rows(compact=False):
    out = []
    for i, (name, meta) in enumerate(ROWS):
        on = ' on' if i == 1 else ''
        if compact:
            out.append('<div class="row%s"><i>%d</i><span>%s</span></div>' % (on, i + 1, name))
        else:
            out.append('<div class="row%s"><i>%d</i><div><b>%s</b><small>%s</small></div></div>'
                       % (on, i + 1, name, meta))
    return ''.join(out)


HTML = '''<!doctype html><html><head><meta charset="utf-8"><style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"Microsoft YaHei","PingFang SC",system-ui,sans-serif;background:#fff}
.stage{position:relative;width:1180px;height:760px;overflow:hidden;
  background:linear-gradient(135deg,#eef1f6,#e6eaf2 60%,#e9edf4)}
/* 背景假装是产品界面：左边内容区 + 右边一条面板，用来看遮罩和层级 */
.fake{position:absolute;inset:0;padding:26px 0 26px 30px}
.fake .bar{height:38px;width:520px;border-radius:8px;background:#fff;opacity:.75;margin-bottom:16px}
.fake .blk{height:150px;border-radius:10px;background:#fff;opacity:.6;margin-bottom:14px}
.fake .side{position:absolute;right:0;top:0;width:268px;height:100%;background:#fff;opacity:.75;
  border-left:1px solid rgba(0,0,0,.05)}
.scrim{position:absolute;inset:0;background:rgba(17,24,39,.42)}
.dlg{position:absolute;background:#fff;border-radius:14px;box-shadow:0 24px 70px rgba(15,23,42,.28);
  display:flex;flex-direction:column;overflow:hidden}
.hd{display:flex;align-items:flex-start;gap:12px;padding:18px 20px 12px}
.hd h2{font-size:16px;color:#1f2430;letter-spacing:.2px}
.hd p{margin-top:4px;font-size:12.5px;color:#6b7280}
.hd p b{color:#374151;font-weight:600}
.x{margin-left:auto;width:26px;height:26px;border:0;border-radius:7px;background:transparent;
  color:#9aa3af;font-size:17px;line-height:1;cursor:pointer}
.bd{flex:1;min-height:0;display:flex;gap:14px;padding:0 20px 4px}
.shot{position:relative;border:1px solid #e4e7ec;border-radius:10px;overflow:hidden;background:#f7f8fa}
.shot img{display:block;width:100%;height:100%;object-fit:cover;object-position:top left}
.box{position:absolute;border:1.6px solid #2563eb;border-radius:4px;
  background:rgba(37,99,235,.10)}
.box.on{border-color:#d97706;background:rgba(217,119,6,.14)}
.box b{position:absolute;left:-1px;top:-1px;min-width:15px;height:15px;padding:0 3px;
  display:flex;align-items:center;justify-content:center;border-radius:4px 0 4px 0;
  background:#2563eb;color:#fff;font:600 10px/1 ui-monospace,Menlo,monospace}
.box.on b{background:#d97706}
.list{display:flex;flex-direction:column;gap:6px;overflow:auto;padding-right:2px}
.row{display:flex;align-items:center;gap:9px;padding:9px 11px;border:1px solid #e4e7ec;
  border-radius:9px;cursor:pointer}
.row i{flex:none;width:20px;height:20px;border-radius:6px;background:#eef1f6;color:#5b6472;
  font:600 11px/20px ui-monospace,monospace;text-align:center;font-style:normal}
.row b{display:block;font-size:13px;font-weight:600;color:#1f2430}
.row small{display:block;margin-top:2px;font-size:11.5px;color:#8a93a0}
.row.on{border-color:#2563eb;background:#f2f7ff;box-shadow:inset 2px 0 0 #2563eb}
.row.on i{background:#2563eb;color:#fff}
.ft{display:flex;align-items:center;gap:10px;padding:12px 20px 16px}
.ft .hint{font-size:12px;color:#8a93a0}
.ft .right{margin-left:auto;display:flex;gap:8px}
.btn{height:32px;padding:0 14px;border-radius:8px;font-size:13px;cursor:pointer;border:1px solid #d9dee7;
  background:#fff;color:#374151}
.btn.pri{background:#2563eb;border-color:#2563eb;color:#fff;font-weight:600}
/* A：左右分栏 */
.a{left:140px;top:70px;width:900px;height:620px}
.a .bd .shot{flex:0 0 62%}
.a .bd .list{flex:1}
/* B：紧凑单列 */
.b{left:310px;top:80px;width:560px;height:600px}
.b .bd{flex-direction:column;gap:10px}
.b .shot{flex:0 0 300px}
.b .list{flex:1;gap:4px}
.b .row{padding:7px 10px}
.b .row i{width:18px;height:18px;line-height:18px;font-size:10px}
/* C：右侧抽屉 */
.c{right:0;top:0;width:430px;height:760px;border-radius:14px 0 0 14px}
.c .bd{flex-direction:column;gap:10px}
.c .shot{flex:0 0 236px}
.c .list{flex:1}
.c .ft{padding:10px 16px 16px}
.c .ft .hint{display:none}
.c .ft .right{margin-left:0;width:100%}
.c .ft .btn{flex:1}
</style></head><body>

<div class="stage" id="a">
  <div class="fake"><div class="bar"></div><div class="blk"></div><div class="blk"></div><div class="side"></div></div>
  <div class="scrim"></div>
  <div class="dlg a">
    <div class="hd"><div><h2>选择这一步的目标元素</h2><p>第 <b>41</b> 步 · 点击 · 匹配到 <b>4</b> 个，选一个继续</p></div><button class="x">×</button></div>
    <div class="bd"><div class="shot"><img src="IMG">BOXES</div><div class="list">ROWS</div></div>
    <div class="ft"><span class="hint">还有 2 步待确认</span><div class="right"><button class="btn">允许降级</button><button class="btn pri">就这个，继续</button></div></div>
  </div>
</div>

<div class="stage" id="b">
  <div class="fake"><div class="bar"></div><div class="blk"></div><div class="blk"></div><div class="side"></div></div>
  <div class="scrim"></div>
  <div class="dlg b">
    <div class="hd"><div><h2>这一步匹配到多个元素</h2><p>第 <b>41</b> 步 · 点击 · 点框或点下面任一条</p></div><button class="x">×</button></div>
    <div class="bd"><div class="shot"><img src="IMG">BOXES</div><div class="list">CROWS</div></div>
    <div class="ft"><div class="right"><button class="btn">允许降级</button><button class="btn pri">就这个，继续</button></div></div>
  </div>
</div>

<div class="stage" id="c">
  <div class="fake"><div class="bar"></div><div class="blk"></div><div class="blk"></div><div class="side"></div></div>
  <div class="scrim"></div>
  <div class="dlg c">
    <div class="hd"><div><h2>选择目标元素</h2><p>第 <b>41</b> 步 · 匹配到 <b>4</b> 个</p></div><button class="x">×</button></div>
    <div class="bd"><div class="shot"><img src="IMG">BOXES</div><div class="list">ROWS</div></div>
    <div class="ft"><div class="right"><button class="btn">允许降级</button><button class="btn pri">就这个，继续</button></div></div>
  </div>
</div>

</body></html>'''

html = (HTML.replace('IMG', IMG).replace('BOXES', boxes())
            .replace('CROWS', rows(compact=True)).replace('ROWS', rows()))
path = os.path.join(HERE, '_dialog_styles.html')
io.open(path, 'w', encoding='utf-8').write(html)

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={'width': 1180, 'height': 760})
    pg.goto('file:///' + path.replace('\\', '/'))
    pg.wait_for_timeout(400)
    for key, name in (('a', '_style_a.png'), ('b', '_style_b.png'), ('c', '_style_c.png')):
        pg.locator('#' + key).screenshot(path=os.path.join(HERE, name))
        print('wrote', name)
    b.close()
