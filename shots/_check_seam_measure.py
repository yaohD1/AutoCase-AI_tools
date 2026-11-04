# -*- coding: utf-8 -*-
"""接缝的真正原因：GetWindowRect 含不可见的拉伸边框。

Win10 上每个窗口左右各约 8px 是看不见的 resize 边框（SM_CXSIZEFRAME + PADDEDBORDER），
所以「矩形贴死」视觉上还留着 16px 的缝，露出的是桌面（不是阴影）。
可见边界要用 DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS) 拿。
"""
import ctypes
import subprocess
import tempfile
import time
from ctypes import wintypes

from PIL import ImageGrab
from playwright.sync_api import sync_playwright

user32 = ctypes.WinDLL('user32', use_last_error=True)
dwmapi = ctypes.WinDLL('dwmapi', use_last_error=True)
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

SWP_NOZORDER, SWP_NOACTIVATE = 0x0004, 0x0010
DWMWA_EXTENDED_FRAME_BOUNDS = 9
EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def window_rect(hwnd):
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def visible_rect(hwnd):
    """去掉不可见边框之后真正画在屏幕上的那块。"""
    r = wintypes.RECT()
    rc = dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS,
                                      ctypes.byref(r), ctypes.sizeof(r))
    if rc != 0:
        return window_rect(hwnd)
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def windows_of_pid(pid):
    found = []

    def cb(hwnd, _):
        got = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(got))
        if got.value != pid or not user32.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(hwnd, buf, 128)
        if buf.value != 'Chrome_WidgetWin_1':
            return True
        r = window_rect(hwnd)
        if r[2] < 300 or r[3] < 200:
            return True
        found.append(hwnd)
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return found


def seam_stats(path, x_from, x_to, y, half=60):
    im = ImageGrab.grab(bbox=(x_from, y, x_to, y + 60), all_screens=True).convert('L')
    im.save(path)
    cols = []
    for x in range(im.width):
        vals = [im.getpixel((x, yy)) for yy in range(0, im.height, 4)]
        cols.append(sum(vals) // len(vals))
    dark = [i for i, v in enumerate(cols) if v < 150]
    return {'width': im.width, 'dark_cols': len(dark),
            'dark_range': (min(dark) + x_from, max(dark) + x_from) if dark else None,
            'min': min(cols)}


with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    ctx = browser.new_context(no_viewport=True)
    page = ctx.new_page()
    page.goto('data:text/html,<body style="margin:0;background:%23ffffff;'
              'font:20px sans-serif">PAGE</body>')
    time.sleep(1.2)
    cdp = browser.new_browser_cdp_session()
    pid = next(x['id'] for x in cdp.send('SystemInfo.getProcessInfo')['processInfo']
               if x['type'] == 'browser')
    page_hwnd = windows_of_pid(pid)[0]

    profile = tempfile.mkdtemp(prefix='ac-app-')
    proc = subprocess.Popen([
        p.chromium.executable_path,
        '--app=data:text/html,<body style="margin:0;background:%23f2f4f8;'
        'font:20px sans-serif">PANEL</body>',
        '--user-data-dir=' + profile, '--no-first-run', '--no-default-browser-check',
    ])
    time.sleep(2.5)
    panel = windows_of_pid(proc.pid)[0]

    user32.SetWindowPos(page_hwnd, 0, 100, 80, 900, 700, SWP_NOZORDER | SWP_NOACTIVATE)
    time.sleep(0.4)
    wr, vr = window_rect(page_hwnd), visible_rect(page_hwnd)
    print('page    window', wr, 'visible', vr, '左内缩', vr[0] - wr[0], '右内缩', (wr[0] + wr[2]) - (vr[0] + vr[2]))

    pr = window_rect(panel)
    # 让「可见左边界」正好落在 page 的「可见右边界」上
    target_x = vr[0] + vr[2] - (visible_rect(panel)[0] - pr[0])
    user32.SetWindowPos(panel, 0, target_x, 80, 420, 700, SWP_NOZORDER | SWP_NOACTIVATE)
    time.sleep(0.5)
    pvr = visible_rect(panel)
    print('panel   window', window_rect(panel), 'visible', pvr, '缝隙', pvr[0] - (vr[0] + vr[2]))

    stats = seam_stats('shots/_seam_visible.png', vr[0] + vr[2] - 40, vr[0] + vr[2] + 40, 300)
    print('可见边界贴齐后', stats)

    proc.kill()
    browser.close()
