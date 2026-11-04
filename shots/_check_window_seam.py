# -*- coding: utf-8 -*-
"""两个窗口 0px 贴死后，接缝到底长什么样：圆角缺口、阴影暗边。

对比三档：默认 / 只去圆角 / 去圆角+去非客户区渲染，把接缝附近裁出来放大看。
"""
import ctypes
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
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_NCRENDERING_POLICY = 2
DWMWCP_DONOTROUND = 1
DWMNCRP_DISABLED = 1
EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def rect(hwnd):
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
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
        r = rect(hwnd)
        if r[2] < 400:
            return True
        found.append(hwnd)
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return found


def set_dwm(hwnd, attr, value):
    v = ctypes.c_int(value)
    rc = dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))
    return rc == 0


with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    pages = []
    for name in ('LEFT', 'RIGHT'):
        ctx = browser.new_context(no_viewport=True)
        pg = ctx.new_page()
        pg.goto('data:text/html,<title>%s</title><body style="margin:0;background:#fff;'
                'font:24px sans-serif;padding:40px">%s</body>' % (name, name))
        pages.append(pg)
    time.sleep(1.2)
    cdp = browser.new_browser_cdp_session()
    pid = next(x['id'] for x in cdp.send('SystemInfo.getProcessInfo')['processInfo']
               if x['type'] == 'browser')
    wins = windows_of_pid(pid)
    print('窗口', wins)
    left, right = wins[0], wins[1]

    def flush():
        lr = rect(left)
        user32.SetWindowPos(right, 0, lr[0] + lr[2], lr[1], 0, 0, SWP_NOZORDER | SWP_NOACTIVATE | 0x0001)
        time.sleep(0.35)

    # 1) 默认
    flush()
    lr, rr = rect(left), rect(right)
    box = (lr[0] + lr[2] - 60, lr[1] + 60, lr[0] + lr[2] + 60, lr[1] + 160)
    ImageGrab.grab(bbox=box).resize((240, 200), 0).save('shots/_seam_default.png')
    print('default 接缝', box)

    # 2) 去圆角
    print('去圆角 left/right:', set_dwm(left, DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_DONOTROUND),
          set_dwm(right, DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_DONOTROUND))
    time.sleep(0.4)
    flush()
    ImageGrab.grab(bbox=box).resize((240, 200), 0).save('shots/_seam_noround.png')

    # 3) 去圆角 + 关掉非客户区渲染（阴影跟着没）
    print('关非客户区渲染:', set_dwm(left, DWMWA_NCRENDERING_POLICY, DWMNCRP_DISABLED),
          set_dwm(right, DWMWA_NCRENDERING_POLICY, DWMNCRP_DISABLED))
    time.sleep(0.4)
    flush()
    ImageGrab.grab(bbox=box).resize((240, 200), 0).save('shots/_seam_noshadow.png')
    print('最终 left/right', rect(left), rect(right))
    browser.close()
