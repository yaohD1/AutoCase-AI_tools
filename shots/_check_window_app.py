# -*- coding: utf-8 -*-
"""--app 无壳窗口：能不能起来、能不能按进程找到 HWND、能不能和被测窗口粘住。

顺带量一下接缝：两个窗口 0px 贴死之后，中间还剩多少像素的暗带。
"""
import ctypes
import os
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

SWP_NOZORDER, SWP_NOACTIVATE, SWP_NOSIZE = 0x0004, 0x0010, 0x0001
DWMWA_NCRENDERING_POLICY, DWMNCRP_DISABLED = 2, 1
EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


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


def child_pids(pid):
    """--app 起来的进程可能不是直接的那个：从 pid 往下找所有子进程。"""
    out = subprocess.run(['wmic', 'process', 'where', 'ParentProcessId=%d' % pid,
                          'get', 'ProcessId'], capture_output=True, text=True)
    ids = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if line.isdigit():
            ids.append(int(line))
    return ids


class Glue:
    def __init__(self, a, b, gap=0):
        self.a, self.b, self.gap = a, b, gap
        self.events = self.moved = 0
        self.echo = {}

    def start(self):
        self._ready = self._ready = __import__('threading').Event()
        __import__('threading').Thread(target=self._run, daemon=True).start()
        self._ready.wait(2)
        return bool(getattr(self, 'hook', None))

    def _run(self):
        import threading as _t
        proc = ctypes.WINFUNCTYPE(
            None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND,
            wintypes.LONG, wintypes.LONG, wintypes.DWORD, wintypes.DWORD
        )(self._on_event)
        self._proc = proc
        self.hook = user32.SetWinEventHook(0x800B, 0x800B, 0, proc, 0, 0, 0)
        self._ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), 0, 0, 0) != 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _on_event(self, hook, event, hwnd, id_object, id_child, thread, ms):
        if id_object != 0 or id_child != 0 or hwnd not in (self.a, self.b):
            return
        self.events += 1
        rect = window_rect(hwnd)
        if self.echo.get(hwnd) == rect:
            self.echo.pop(hwnd, None)
            return
        src, dst = (self.a, self.b) if hwnd == self.a else (self.b, self.a)
        rd = window_rect(dst)
        want_x = (rect[0] + rect[2] + self.gap) if src == self.a else (rect[0] - rd[2] - self.gap)
        dx, dy = want_x - rd[0], rect[1] - rd[1]
        if dx or dy:
            self.moved += 1
            nx, ny = rd[0] + dx, rd[1] + dy
            self.echo[dst] = (nx, ny, rd[2], rd[3])
            user32.SetWindowPos(dst, 0, nx, ny, 0, 0, SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOSIZE)


with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    ctx = browser.new_context(no_viewport=True)
    page = ctx.new_page()
    page.goto('data:text/html,<title>PAGE</title><body style="margin:0;background:#fff">PAGE</body>')
    time.sleep(1.0)
    cdp = browser.new_browser_cdp_session()
    pid = next(x['id'] for x in cdp.send('SystemInfo.getProcessInfo')['processInfo']
               if x['type'] == 'browser')
    page_hwnd = windows_of_pid(pid)[0]
    print('被测窗口', page_hwnd, window_rect(page_hwnd))

    profile = tempfile.mkdtemp(prefix='ac-app-')
    exe = p.chromium.executable_path
    proc = subprocess.Popen([
        exe, '--app=data:text/html,<title>AUTOCASE PANEL</title>'
             '<body style="margin:0;background:#eef;font:20px sans-serif">PANEL</body>',
        '--user-data-dir=' + profile,
        '--no-first-run', '--no-default-browser-check',
        '--window-size=520,900', '--window-position=1400,100',
    ])
    time.sleep(2.5)
    print('app 进程 pid', proc.pid, '子进程', child_pids(proc.pid))
    cand = windows_of_pid(proc.pid)
    if not cand:
        for cp in child_pids(proc.pid):
            cand += windows_of_pid(cp)
    print('面板窗口', cand)
    if not cand:
        print('!! --app 窗口没找到')
        proc.kill()
        browser.close()
        raise SystemExit(1)
    panel = cand[0]
    print('面板 rect', window_rect(panel))
    # --app 没有标签栏：比较窗口高度和内容区
    print('chromium 版本标记', user32.GetWindowTextW(panel, ctypes.create_unicode_buffer(64), 64))

    lr = window_rect(page_hwnd)
    pr = window_rect(panel)
    user32.SetWindowPos(panel, 0, lr[0] + lr[2], lr[1], pr[2], lr[3], SWP_NOZORDER | SWP_NOACTIVATE)
    time.sleep(0.4)
    lr, pr = window_rect(page_hwnd), window_rect(panel)
    print('贴好后 page', lr, 'panel', pr)

    glue = Glue(page_hwnd, panel, 0)
    print('hook', glue.start())
    time.sleep(0.3)
    user32.SetWindowPos(page_hwnd, 0, lr[0] - 200, lr[1] + 30, 0, 0, SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOSIZE)
    time.sleep(0.5)
    print('拖 page 后 page', window_rect(page_hwnd), 'panel', window_rect(panel),
          'events', glue.events, 'moved', glue.moved)

    seam_x = window_rect(page_hwnd)[0] + window_rect(page_hwnd)[2]
    box = (seam_x - 50, 120, seam_x + 50, 220)
    ImageGrab.grab(bbox=box).resize((300, 300), 0).save('shots/_seam_app.png')
    print('接缝截图', box)

    # 关掉非客户区渲染，看阴影会不会消失
    for hwnd in (page_hwnd, panel):
        v = ctypes.c_int(DWMNCRP_DISABLED)
        dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_NCRENDERING_POLICY, ctypes.byref(v), 4)
    time.sleep(0.4)
    seam_x = window_rect(page_hwnd)[0] + window_rect(page_hwnd)[2]
    ImageGrab.grab(bbox=(seam_x - 50, 120, seam_x + 50, 220)).resize((300, 300), 0).save(
        'shots/_seam_app_nc.png')
    print('再见 seam', seam_x)

    proc.kill()
    browser.close()
