# -*- coding: utf-8 -*-
"""真实面板地址下的 --app 窗口 + 粘合 + 整屏截图，直接看接缝和比例。

跑完把整屏存成 shots/_windows_pair.png，肉眼判断「像不像一个应用」。
"""
import ctypes
import subprocess
import tempfile
import time
from ctypes import wintypes

from PIL import ImageGrab
from playwright.sync_api import sync_playwright

user32 = ctypes.WinDLL('user32', use_last_error=True)
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

SWP_NOZORDER, SWP_NOACTIVATE, SWP_NOSIZE = 0x0004, 0x0010, 0x0001
EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
PANEL = 'http://localhost:5173/recording?panel=1'


def window_rect(hwnd):
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
        r = window_rect(hwnd)
        if r[2] < 300 or r[3] < 200:
            return True
        found.append(hwnd)
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return found


with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    ctx = browser.new_context(no_viewport=True)
    page = ctx.new_page()
    page.goto('http://localhost:5173/recording', wait_until='domcontentloaded')
    time.sleep(1.5)
    cdp = browser.new_browser_cdp_session()
    pid = next(x['id'] for x in cdp.send('SystemInfo.getProcessInfo')['processInfo']
               if x['type'] == 'browser')
    page_hwnd = windows_of_pid(pid)[0]

    profile = tempfile.mkdtemp(prefix='ac-app-')
    proc = subprocess.Popen([
        p.chromium.executable_path, '--app=' + PANEL,
        '--user-data-dir=' + profile, '--no-first-run', '--no-default-browser-check',
    ])
    time.sleep(3.0)
    wins = windows_of_pid(proc.pid)
    if not wins:
        print('!! 没找到 app 窗口')
        proc.kill(); browser.close(); raise SystemExit(1)
    panel = wins[0]

    lr = window_rect(page_hwnd)
    pr = window_rect(panel)
    # 面板宽度按 420，右边贴到屏幕边
    pw = 420
    user32.SetWindowPos(page_hwnd, 0, 0, 0, lr[2], lr[3], SWP_NOZORDER | SWP_NOACTIVATE)
    lr = window_rect(page_hwnd)
    user32.SetWindowPos(panel, 0, lr[2], 0, pw, lr[3], SWP_NOZORDER | SWP_NOACTIVATE)
    time.sleep(0.8)
    print('page', window_rect(page_hwnd), 'panel', window_rect(panel))
    ImageGrab.grab(all_screens=True).save('shots/_windows_pair.png')
    print('整屏已存 shots/_windows_pair.png')

    proc.kill()
    browser.close()
