# -*- coding: utf-8 -*-
"""能不能把两个浏览器窗口「粘」在一起：动一个，另一个跟着走。

只验三件事：
1. 能不能按 PID 找到两个窗口的 HWND（Playwright 的窗口没有好用的句柄来源）；
2. SetWinEventHook 能不能收到拖动/移动事件；
3. 同步移动另一个窗口时不会打乒乓（B 被我们挪动又会触发 A 再挪一次）。
"""
import ctypes
import threading
import time
from ctypes import wintypes

from playwright.sync_api import sync_playwright

user32 = ctypes.WinDLL('user32', use_last_error=True)
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

try:
    # 不声明 DPI 感知的话，GetWindowRect 返回的是被系统缩放过的小数坐标，
    # 两台机器缩放不同就会算出偏一半的位移。
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
EVENT_OBJECT_LOCATIONCHANGE = 0x800B
OBJID_WINDOW = 0
WINEVENT_OUTOFCONTEXT = 0
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_NOSIZE = 0x0001
GA_ROOT = 2


def window_rect(hwnd):
    r = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def windows_of_pid(pid):
    found = []

    def cb(hwnd, _):
        got = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(got))
        if got.value != pid or not user32.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        if buf.value != 'Chrome_WidgetWin_1':
            return True
        rect = window_rect(hwnd)
        # 只认真正的内容窗口：Chrome 还有些 0 宽高的隐藏壳
        if not rect or rect[2] < 400 or rect[3] < 300:
            return True
        title = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title, 256)
        found.append((hwnd, title.value, rect))
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return found


class Glue:
    """把 a 的位移同步到 b，保持 b 永远贴在 a 右边。

    这里不能用「正在移动」的布尔标志防乒乓：WinEventHook(OUTOFCONTEXT) 的回调是
    投递到消息队列的，等它执行时我们的 SetWindowPos 早就返回了，标志已经清掉。
    正确做法是记住「我刚把 b 放到哪」，收到这个位置的 b 事件就认定是自己干的。
    """

    def __init__(self, a, b, gap=0):
        self.a, self.b = a, b
        self.gap = gap
        self.events = 0
        self.moved = 0
        self.echo = {}
        self.seen = 0

    def start(self):
        # 钩子必须在**收消息的那个线程**里装：OUTOFCONTEXT 的回调是投递到安装线程的
        # 消息队列里的，装在主线程、在别的线程 GetMessage，一个事件都收不到（实测事件 0）。
        self._ready = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()
        self._ready.wait(2)
        return bool(getattr(self, 'hook', None))

    def _run(self):
        proc = ctypes.WINFUNCTYPE(
            None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND,
            wintypes.LONG, wintypes.LONG, wintypes.DWORD, wintypes.DWORD
        )(self._on_event)
        self._proc = proc  # 必须留引用，否则回调会被回收
        self.hook = user32.SetWinEventHook(
            EVENT_OBJECT_LOCATIONCHANGE, EVENT_OBJECT_LOCATIONCHANGE,
            0, proc, 0, 0, WINEVENT_OUTOFCONTEXT)
        self._ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), 0, 0, 0) != 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _on_event(self, hook, event, hwnd, id_object, id_child, thread, ms):
        if id_object != OBJID_WINDOW or id_child != 0:
            return
        if hwnd not in (self.a, self.b):
            return
        self.events += 1
        rect = window_rect(hwnd)
        if not rect:
            return
        # 自己刚放的位置：消费掉，别当成用户拖动
        if self.echo.get(hwnd) == rect:
            self.echo.pop(hwnd, None)
            self.seen += 1
            return
        self.seen = 0
        src, dst = (self.a, self.b) if hwnd == self.a else (self.b, self.a)
        rd = window_rect(dst)
        if not rd:
            return
        if src == self.a:
            want_x = rect[0] + rect[2] + self.gap
        else:
            want_x = rect[0] - rd[2] - self.gap
        dx, dy = want_x - rd[0], rect[1] - rd[1]
        if dx or dy:
            self.moved += 1
            nx, ny = rd[0] + dx, rd[1] + dy
            self.echo[dst] = (nx, ny, rd[2], rd[3])
            user32.SetWindowPos(dst, 0, nx, ny, 0, 0,
                                SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOSIZE)


with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    pages = []
    for name in ('LEFT', 'RIGHT'):
        ctx = browser.new_context(no_viewport=True)
        page = ctx.new_page()
        page.goto('data:text/html,<title>%s</title><body style="font:20px sans-serif">%s</body>'
                  % (name, name))
        pages.append(page)
    time.sleep(1.0)

    cdp = browser.new_browser_cdp_session()
    procs = cdp.send('SystemInfo.getProcessInfo')['processInfo']
    pid = next(x['id'] for x in procs if x['type'] == 'browser')
    wins = windows_of_pid(pid)
    print('PID', pid, '找到窗口', len(wins))
    for hwnd, title, rect in wins:
        print('  hwnd=%s title=%r rect=%s' % (hwnd, title, rect))

    if len(wins) < 2:
        print('!! 找不到两个窗口，粘合无从谈起')
        browser.close()
        raise SystemExit(1)

    left, right = wins[0][0], wins[1][0]
    rl, rr = window_rect(left), window_rect(right)
    # 先摆成左右紧贴
    b = rl[0] + rl[2]
    user32.SetWindowPos(right, 0, b, rl[1], rr[2], rl[3],
                        SWP_NOZORDER | SWP_NOACTIVATE)
    time.sleep(0.3)
    print('贴好后 LEFT', window_rect(left), 'RIGHT', window_rect(right))

    glue = Glue(left, right, gap=0)
    print('hook 安装:', glue.start())
    time.sleep(0.3)

    # 1) 整体右移 120 / 下移 40，看 RIGHT 是否跟随
    user32.SetWindowPos(left, 0, rl[0] + 120, rl[1] + 40, 0, 0,
                        SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOSIZE)
    time.sleep(0.5)
    print('移动 LEFT 后: LEFT', window_rect(left), 'RIGHT', window_rect(right))
    print('  事件', glue.events, '同步次数', glue.moved)

    # 2) 只动 RIGHT（模拟用户拖面板），看 LEFT 会不会被拽回去
    before = window_rect(left)
    user32.SetWindowPos(right, 0, window_rect(right)[0] + 80, window_rect(right)[1], 0, 0,
                        SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOSIZE)
    time.sleep(0.5)
    print('移动 RIGHT 后: LEFT', window_rect(left), 'RIGHT', window_rect(right))
    print('  LEFT 是否被拽动:', before != window_rect(left))

    browser.close()
