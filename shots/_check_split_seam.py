# -*- coding: utf-8 -*-
"""验证「拖接缝」是不是分隔条语义：接缝跟着走，两条外边界都不许动。

之前的 bug：Glue 只同步位置、从不改尺寸，所以拖接缝等于把对面整个推走
（推 A 的右边 → B 整体右移 → B 的右边跑出屏幕），看着就是「拖中间，外面的边也在跑」。

用 move_visible 改一个窗口的宽/高，和真人拖它的边框是同一种窗口变化，
粘合线程分不出来也不需要分。
"""
import http.server
import ctypes
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'backend'))

from PIL import ImageGrab

from app.services.recording import winwindows
from app.services.recording.session import RecordingSession

HTML = b"""<!doctype html><html><head><title>TestApp</title></head>
<body style="margin:0;font:16px sans-serif"><h1>TestApp</h1><p>recorded page</p></body></html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(HTML)))
        self.end_headers()
        self.wfile.write(HTML)

    def log_message(self, *a):
        pass


server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
base = 'http://127.0.0.1:%d/' % server.server_address[1]

RecordingSession._current = None
session = RecordingSession.start_new(base, headless=False, ask_daemon=False,
                                     panel_url='http://localhost:5173/recording?panel=1',
                                     panel_ratio=0.42)
for _ in range(60):
    if session.status in ('recording', 'failed'):
        break
    time.sleep(0.25)

main, panel = session._main_hwnd, session._panel_hwnd
print('status', session.status, 'main', main, 'panel', panel, 'glue', bool(session._glue))
time.sleep(1.5)


def snap():
    return winwindows.visible_rect(main), winwindows.visible_rect(panel)


def report(tag, before, after):
    (m0, p0), (m1, p1) = before, after
    print('\n== %s ==' % tag)
    print('  main ', m0, '->', m1)
    print('  panel', p0, '->', p1)
    print('  接缝    main右 %d / panel左 %d  差 %d' % (
        m1[0] + m1[2], p1[0], p1[0] - (m1[0] + m1[2])))
    print('  外边界  main左 %d (%+d)  panel右 %d (%+d)' % (
        m1[0], m1[0] - m0[0], p1[0] + p1[2], (p1[0] + p1[2]) - (p0[0] + p0[2])))
    return m1[0] + m1[2] == p1[0] and m1[0] == m0[0] and p1[0] + p1[2] == p0[0] + p0[2]


before = snap()
print('初始 main', before[0], 'panel', before[1])

# 1) 拖被测窗口的右边（接缝）向右 120 —— 面板应该从左边缩，右边不动
m, p = before
winwindows.move_visible(main, m[0], m[1], m[2] + 120, m[3])
time.sleep(0.8)
ok1 = report('拖 main 的右边 +120', before, snap())
ImageGrab.grab(all_screens=True).save('shots/_split_seam_right.png')

# 2) 拖面板的左边（接缝）向左 100 —— 被测窗口应该从右边缩，左边不动
before = snap()
m, p = before
winwindows.move_visible(panel, p[0] - 100, p[1], p[2] + 100, p[3])
time.sleep(0.8)
ok2 = report('拖 panel 的左边 -100', before, snap())
ImageGrab.grab(all_screens=True).save('shots/_split_seam_left.png')

# 3) 整窗口平移 —— 这一条不能因为改成分隔条语义就坏掉
before = snap()
m, p = before
winwindows.move_visible(main, m[0] + 90, m[1] + 30, m[2], m[3])
time.sleep(0.8)
after = snap()
mv, pv = after
(dm0, dp0) = before
print('\n== 整窗口平移 +90/+30 ==')
print('  main 位移', (mv[0] - dm0[0], mv[1] - dm0[1]), '尺寸', (mv[2], mv[3]),
      '应 (90, 30) / 尺寸不变')
print('  panel 位移', (pv[0] - dp0[0], pv[1] - dp0[1]), '尺寸', (pv[2], pv[3]))
ok3 = (mv[0] - dm0[0], mv[1] - dm0[1]) == (90, 30) and \
      (pv[0] - dp0[0], pv[1] - dp0[1]) == (90, 30) and \
      pv[2] == dp0[2] and pv[3] == dp0[3]

# 4) 极限：把被测窗口拖到把面板压死。面板卡在下限，被测窗口的右边要被推回来，
#    接缝不能裂；也不能因为「推回来」和回声判定打架而抖成一团。
before = snap()
m, p = before
winwindows.move_visible(main, m[0], m[1], m[2] + 600, m[3])
time.sleep(1.0)
after = snap()
m1, p1 = after
print('\n== 极限：main 右边 +600（面板会被压死）==')
print('  main ', m1, '  panel', p1)
print('  接缝差 %d   面板宽 %d（下限 %d）' % (
    p1[0] - (m1[0] + m1[2]), p1[2], winwindows.SPLIT_MIN_W))
time.sleep(0.6)
again = snap()
ok4 = (p1[0] - (m1[0] + m1[2])) == 0 and p1[2] >= winwindows.SPLIT_MIN_W \
    and again == after

print('\n结果: 拖右缝 %s / 拖左缝 %s / 整体平移 %s / 压到下限 %s' % (
    'OK' if ok1 else 'FAIL', 'OK' if ok2 else 'FAIL', 'OK' if ok3 else 'FAIL',
    'OK' if ok4 else 'FAIL'))

# 5) 面板被最大化之后再点「并排」——用户最常见的动作，也是「点了没反应」的来源：
#    is_usable 把最大化判成不可用，原生路径整条跳过，CDP 又够不着另一个进程的窗口。
SW_MAXIMIZE = 3
ctypes.windll.user32.ShowWindow(panel, SW_MAXIMIZE)
time.sleep(0.8)
mz = winwindows.window_rect(panel)
print('\n== 把面板最大化 ==')
print('  panel 矩形', mz, '是否最大化', bool(ctypes.windll.user32.IsZoomed(panel)))
print('  relayout ->', session.relayout())
time.sleep(1.0)
m5, p5 = snap()
print('  main ', m5, '  panel', p5)
gap5 = p5[0] - (m5[0] + m5[2])
print('  接缝差 %d   面板还是最大化吗 %s' % (gap5, bool(ctypes.windll.user32.IsZoomed(panel))))
ok5 = gap5 == 0 and not ctypes.windll.user32.IsZoomed(panel) and m5[2] > 480

print('\n最终: 拖右缝 %s / 拖左缝 %s / 平移 %s / 下限 %s / 最大化后并排 %s' % (
    'OK' if ok1 else 'FAIL', 'OK' if ok2 else 'FAIL', 'OK' if ok3 else 'FAIL',
    'OK' if ok4 else 'FAIL', 'OK' if ok5 else 'FAIL'))

# 6) 面板被关掉之后再点「并排」：不能再谎报成功（以前会弹出「已重新并排」）
winwindows.kill_tree(winwindows.pid_of_window(panel))
time.sleep(1.0)
res = session.relayout()
print('\n== 关掉面板后点并排 ==')
print('  relayout ->', res, '应为 laid_out=False')
ok6 = res.get('laid_out') is False
print('\n第六项', 'OK' if ok6 else 'FAIL')

RecordingSession.dispose_current()
time.sleep(1.0)
server.shutdown()
server.server_close()
print('done')
