# -*- coding: utf-8 -*-
"""真起一次 split 录制，验证无壳面板 + 贴齐 + 粘合。

看四件事：
1. 面板是不是 --app 无壳窗口（没有标签栏）；
2. 两个窗口的**可见边界**是不是严丝合缝（差 0px）；
3. 动被测窗口，面板跟不跟；
4. 动面板，被测窗口跟不跟。
"""
import http.server
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
print('status', session.status, 'error', session.error)
print('panel_page', session._panel_page, 'panel_hwnd', session._panel_hwnd,
      'main_hwnd', session._main_hwnd, 'glue', bool(session._glue))

time.sleep(1.5)
main = session._main_hwnd
panel = session._panel_hwnd
mv, pv = winwindows.visible_rect(main), winwindows.visible_rect(panel)
print('可见区 main', mv, 'panel', pv, '缝隙', pv[0] - (mv[0] + mv[2]), '顶差', pv[1] - mv[1])

ImageGrab.grab(all_screens=True).save('shots/_split_pair.png')
print('整屏 shots/_split_pair.png')

# 动被测窗口（走 CDP，和真人拖等价的位置变化）
# 直接挪窗口，和用户拖标题栏是同一种位置变化（Playwright 对象只能在录制线程里用）
winwindows.move_visible(main, mv[0] + 120, mv[1] + 40, mv[2], mv[3])
time.sleep(0.8)
mv2, pv2 = winwindows.visible_rect(main), winwindows.visible_rect(panel)
print('移动 main 后 main', mv2, 'panel', pv2, '缝隙', pv2[0] - (mv2[0] + mv2[2]))
print('  panel 是否跟随:', (pv2[0] - pv[0], pv2[1] - pv[1]), '应为 (120, 40)')

# 反向：直接挪面板窗口，看被测窗口会不会被拽回来
before_main = winwindows.visible_rect(main)
pvr = winwindows.visible_rect(panel)
winwindows.move_visible(panel, pvr[0] + 60, pvr[1], pvr[2], pvr[3])
time.sleep(0.8)
after_main = winwindows.visible_rect(main)
print('移动 panel 后 main', after_main, '位移', (after_main[0] - before_main[0],
                                                after_main[1] - before_main[1]),
      '应为 (60, 0)')

ImageGrab.grab(all_screens=True).save('shots/_split_pair_moved.png')
print('整屏 shots/_split_pair_moved.png')

RecordingSession.dispose_current()
time.sleep(1.0)
server.shutdown()
server.server_close()
Path('shots/_split_done.txt').write_text('ok', encoding='utf-8')
print('done')
