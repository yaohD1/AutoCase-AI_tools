# -*- coding: utf-8 -*-
"""Windows 上的窗口摆放：让被测窗口和面板窗口贴成一对，动一个另一个跟着走。

为什么要碰 Win32：这两个窗口一个是 Playwright 拉的 Chromium，一个是 `--app` 起的
面板，CDP 只能改自己那个窗口的位置，管不了另一个。「并排」真正的痛点也不是初始位置，
而是挪一下就得手动再对齐一次。

三个坑，都实测过：

1. **GetWindowRect 比眼睛看到的窗口大**。Win10 每个窗口左右各带约 8px 不可见的拉伸
   边框，按矩形贴死，视觉上漏 16px——露出来的是桌面壁纸，看着像一条黑缝。
   要贴齐必须用 DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS) 拿可见边界，
   再反推回窗口矩形去 SetWindowPos。
2. **钩子必须装在收消息的那个线程**。SetWinEventHook(OUTOFCONTEXT) 的回调是投递到
   安装线程的消息队列的；装在主线程、却去另一个线程 GetMessage，一个事件都收不到。
3. **不能用布尔标志防乒乓**。回调是异步投递的，等它执行时我们那次 SetWindowPos 早
   返回了、标志已经清掉。只能记住「我刚把它放到哪」，收到正好落在那个位置的事件才
   认定是自己干的。

非 Windows 一律 no-op：调用方拿到 None / False 就退回原来的并排。
"""
import ctypes
import sys
import time
from ctypes import wintypes

_IS_WIN = sys.platform == 'win32'

if _IS_WIN:
    _user32 = ctypes.WinDLL('user32', use_last_error=True)
    _dwmapi = ctypes.WinDLL('dwmapi', use_last_error=True)
    _EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _WinEventProc = ctypes.WINFUNCTYPE(
        None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND,
        wintypes.LONG, wintypes.LONG, wintypes.DWORD, wintypes.DWORD)
else:  # pragma: no cover - 只为让 import 不炸
    _user32 = _dwmapi = None
    _EnumWindowsProc = _WinEventProc = None

SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SW_RESTORE = 9
EVENT_OBJECT_LOCATIONCHANGE = 0x800B
OBJID_WINDOW = 0
WINEVENT_OUTOFCONTEXT = 0
DWMWA_EXTENDED_FRAME_BOUNDS = 9
MONITOR_DEFAULTTONEAREST = 2
WM_QUIT = 0x0012

_dpi_done = False


def available():
    return _IS_WIN


def _ensure_dpi():
    """声明 DPI 感知。

    不做这一步，在 125%/150% 缩放的机器上 GetWindowRect 返回的是被系统折算过的坐标，
    算出来的位移会整体偏一圈——正是「贴边贴不准」的另一种表现。
    """
    global _dpi_done
    if _dpi_done or not _IS_WIN:
        return
    _dpi_done = True
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
    except Exception:
        try:
            _user32.SetProcessDPIAware()
        except Exception:
            pass


def window_rect(hwnd):
    """整个窗口矩形，含不可见的拉伸边框。给 SetWindowPos 用。"""
    if not _IS_WIN:
        return None
    _ensure_dpi()
    r = wintypes.RECT()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def visible_rect(hwnd):
    """屏幕上真正看得到的那块。两个窗口要贴齐，比的是这个。"""
    if not _IS_WIN:
        return None
    _ensure_dpi()
    r = wintypes.RECT()
    rc = _dwmapi.DwmGetWindowAttribute(
        hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r))
    if rc != 0:
        return window_rect(hwnd)
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def move_visible(hwnd, left, top, width, height):
    """按「可见矩形」摆窗口：内部换算掉那圈看不见的边框。"""
    if not _IS_WIN or not hwnd:
        return False
    wr, vr = window_rect(hwnd), visible_rect(hwnd)
    if not wr or not vr:
        return False
    dx, dy = wr[0] - vr[0], wr[1] - vr[1]
    dw, dh = wr[2] - vr[2], wr[3] - vr[3]
    try:
        return bool(_user32.SetWindowPos(
            hwnd, 0, int(left) + dx, int(top) + dy,
            int(width) + dw, int(height) + dh, SWP_NOZORDER | SWP_NOACTIVATE))
    except Exception:
        return False


def work_area(hwnd):
    """窗口所在那块显示器的工作区（已扣掉任务栏）。多屏时不至于全摆到主屏上。"""
    if not _IS_WIN:
        return None
    _ensure_dpi()

    class MONITORINFO(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.DWORD), ('rcMonitor', wintypes.RECT),
                    ('rcWork', wintypes.RECT), ('dwFlags', wintypes.DWORD)]

    try:
        hmon = _user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if not _user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            return None
        r = info.rcWork
        return r.left, r.top, r.right - r.left, r.bottom - r.top
    except Exception:
        return None


def find_windows(pid, min_width=300, min_height=200):
    """按进程号找它的顶层内容窗口。

    Chrome 的窗口类名固定是 Chrome_WidgetWin_1；同一个进程还会有几个 0 宽高的隐藏壳，
    用尺寸过滤掉。`--app` 起的实例也是同一个类名。
    """
    if not _IS_WIN or not pid:
        return []
    _ensure_dpi()
    found = []

    def cb(hwnd, _):
        got = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(got))
        if got.value != int(pid) or not _user32.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(128)
        _user32.GetClassNameW(hwnd, buf, 128)
        if buf.value != 'Chrome_WidgetWin_1':
            return True
        r = window_rect(hwnd)
        if not r or r[2] < min_width or r[3] < min_height:
            return True
        found.append(hwnd)
        return True

    try:
        _user32.EnumWindows(_EnumWindowsProc(cb), 0)
    except Exception:
        return []
    return found


def pid_of_window(hwnd):
    """窗口属于哪个进程。

    关面板要用它：`--app` 的启动器把活交给真正的浏览器进程后自己就退了，
    拿启动器的 Popen 去 terminate 是空操作，窗口会一直留在桌面上。
    """
    if not _IS_WIN or not hwnd:
        return 0
    got = wintypes.DWORD()
    try:
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(got))
    except Exception:
        return 0
    return int(got.value)


def kill_tree(pid):
    """连同子进程一起杀掉。Chromium 是多进程的，只杀根进程会留下渲染进程和窗口。"""
    if not _IS_WIN or not pid:
        return False
    import subprocess
    try:
        subprocess.run(['taskkill', '/F', '/T', '/PID', str(int(pid))],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        return True
    except Exception:
        return False


def all_windows(min_width=260, min_height=180):
    """桌面上所有 Chromium 顶层窗口。

    `--app` 起的实例是独立进程，拿不到它自己的 pid 时，就在启动前后各扫一次，
    差集里那个新出现的窗口就是它。
    """
    if not _IS_WIN:
        return set()
    _ensure_dpi()
    found = set()

    def cb(hwnd, _):
        if not _user32.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(128)
        _user32.GetClassNameW(hwnd, buf, 128)
        if buf.value != 'Chrome_WidgetWin_1':
            return True
        r = window_rect(hwnd)
        if r and r[2] >= min_width and r[3] >= min_height:
            found.add(hwnd)
        return True

    try:
        _user32.EnumWindows(_EnumWindowsProc(cb), 0)
    except Exception:
        return set()
    return found


def is_usable(hwnd):
    """窗口还在、且不是最大化/最小化。

    最大化时不能再做跟随：另一个窗口会被拽到屏幕边缘外。
    """
    if not _IS_WIN or not hwnd:
        return False
    return bool(_user32.IsWindow(hwnd)) and bool(_user32.IsWindowVisible(hwnd)) \
        and not _user32.IsZoomed(hwnd) and not _user32.IsIconic(hwnd)


def prepare(hwnd, timeout=0.6):
    """把窗口恢复到「可以摆位置」的状态：最大化/最小化先还原。

    并排和「重新并排」都会走到这儿。用户很常见的动作是先把某个窗口最大化看完，
    再回头点「并排」——以前 `is_usable` 直接把最大化判成不可用，整条原生路径被
    跳过，只剩 CDP 兜底；而无壳面板是另一个进程的窗口，CDP 根本够不着它，
    结果就是「只有被测窗口动了一下，面板原地不动」，前端还弹「已重新并排」。
    """
    if not _IS_WIN or not hwnd:
        return False
    if not _user32.IsWindow(hwnd):
        return False
    if _user32.IsIconic(hwnd) or _user32.IsZoomed(hwnd):
        try:
            _user32.ShowWindow(hwnd, SW_RESTORE)
        except Exception:
            return False
        # 还原是异步的，等它真的变回普通窗口再往下走。
        deadline = timeout
        while deadline > 0:
            if is_usable(hwnd):
                return True
            time.sleep(0.02)
            deadline -= 0.02
    return is_usable(hwnd)


def align_pair(left_hwnd, right_hwnd, gap=0):
    """把 right 的可见左边界贴到 left 的可见右边界上，并对齐顶边。"""
    lv, rv = visible_rect(left_hwnd), visible_rect(right_hwnd)
    if not lv or not rv:
        return False
    move_visible(left_hwnd, lv[0], lv[1], lv[2], lv[3])
    return move_visible(right_hwnd, lv[0] + lv[2] + gap, lv[1], rv[2], lv[3])


# 拖接缝时窗口能被拖到多窄。再窄 Vue 面板排不开，Chromium 自己也有一道下限。
SPLIT_MIN_W = 360
SPLIT_MIN_H = 240


def plan_follow(prev_src, cur_src, dst, src_is_left=True, gap=0,
                min_w=SPLIT_MIN_W, min_h=SPLIT_MIN_H):
    """src 变了以后，另一个窗口该摆到哪。纯计算，不碰 Win32，方便单测。

    必须区分两种变化，这是「拖接缝」能用得像分隔条的前提：

    * **整体平移**——左右两条边的位移一样：照搬位移，尺寸不动。
    * **拖接缝**——只有靠缝那条边动：接缝跟着走，另一个窗口从靠缝那一侧伸缩，
      **外侧那条边钉死**。只搬位置的话，接缝一动会把整对窗口一起推走，
      外面那道边也跟着漂——看着就是「拖中间，外边的边怎么也在跑」。

    返回 ``(dst_rect, src_fix)``。src_fix 一般是 None；只有当对面已经窄到下限、
    得把被拖的那条边推回来（否则接缝会裂开）时才给出 src 的新矩形。
    """
    px, py, pw, ph = prev_src
    cx, cy, cw, ch = cur_src
    dx, dy, dw, dh = dst
    fix_x = fix_y = None

    # ---- 横向 ----
    d_lo = cx - px
    d_hi = (cx + cw) - (px + pw)
    if abs(d_lo - d_hi) <= 1:
        nx, nw = dx + d_lo, dw
    elif src_is_left:
        # src 的右边是接缝：dst 的左边贴上去，dst 的右边不动
        seam = cx + cw + gap
        nx, nw = seam, (dx + dw) - seam
        if nw < min_w:
            nx, nw = (dx + dw) - min_w, min_w
            fix_x = (cx, (nx - gap) - cx)
    else:
        # src 的左边是接缝：dst 的右边贴过去，dst 的左边不动
        seam = cx - gap
        nx, nw = dx, seam - dx
        if nw < min_w:
            nw = min_w
            new_left = dx + min_w + gap
            fix_x = (new_left, (cx + cw) - new_left)

    # ---- 纵向 ----
    # 顶边跟着走，高度跟 src 一致：两个窗口得凑成一个方块，不然下沿是锯齿。
    ny = dy + (cy - py)
    nh = ch
    if nh < min_h:
        nh = min_h
        fix_y = (cy, min_h)

    src_fix = None
    if fix_x or fix_y:
        src_fix = (fix_x[0] if fix_x else cx, fix_y[0] if fix_y else cy,
                   fix_x[1] if fix_x else cw, fix_y[1] if fix_y else ch)
    return (nx, ny, nw, nh), src_fix


class Glue:
    """把两个窗口粘成一对：拖任意一个，另一个跟着走。

    「跟着走」= 被拖的决定，另一个保持贴着。拖整窗口就跟着平移；拖接缝（两个窗口
    中间那条边）就跟着伸缩，外侧边不动——不然拖接缝会像推积木一样把整对推走。
    """

    def __init__(self, left_hwnd, right_hwnd, gap=0):
        self.left = int(left_hwnd) if left_hwnd else 0
        self.right = int(right_hwnd) if right_hwnd else 0
        self.gap = gap
        self._thread = None
        self._tid = 0
        self._hook = None
        self._proc = None
        self._echo = {}
        # 上一次看到的可见矩形。要判断「是平移还是拖接缝」，得两条边分别比。
        self._last = {}
        self.running = False

    def start(self):
        if not _IS_WIN or not self.left or not self.right:
            return False
        if self.running:
            return True
        # 先记下当前摆位当基准，否则第一条事件没有「之前」可比，位移全丢。
        self._last = {}
        for hwnd in (self.left, self.right):
            r = visible_rect(hwnd)
            if r:
                self._last[hwnd] = r
        self._echo = {}
        import threading
        ready = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(ready,),
                                        name='ac-window-glue', daemon=True)
        self._thread.start()
        ready.wait(3)
        self.running = bool(self._hook)
        return self.running

    def _run(self, ready):
        try:
            self._tid = ctypes.windll.kernel32.GetCurrentThreadId()
            self._proc = _WinEventProc(self._on_event)
            self._hook = _user32.SetWinEventHook(
                EVENT_OBJECT_LOCATIONCHANGE, EVENT_OBJECT_LOCATIONCHANGE,
                0, self._proc, 0, 0, WINEVENT_OUTOFCONTEXT)
        except Exception:
            self._hook = None
        ready.set()
        if not self._hook:
            return
        msg = wintypes.MSG()
        try:
            while _user32.GetMessageW(ctypes.byref(msg), 0, 0, 0) > 0:
                _user32.TranslateMessage(ctypes.byref(msg))
                _user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            try:
                _user32.UnhookWinEvent(self._hook)
            except Exception:
                pass
            self._hook = None

    def stop(self):
        if not _IS_WIN or not self._tid:
            self.running = False
            return
        try:
            ctypes.windll.user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
        except Exception:
            pass
        self.running = False

    def _is_echo(self, hwnd, rect):
        """这条事件是不是我刚才摆出来的。

        留 4px 容差：SetWindowPos 之后窗口管理器可能把尺寸取整，或者被窗口自己的
        最小尺寸顶住，落点和命令值差一两像素。严格相等会漏判，于是把自己的一次操作
        当成用户拖动，两个窗口开始对推。
        """
        want = self._echo.get(hwnd)
        if not want:
            return False
        if all(abs(a - b) <= 4 for a, b in zip(want, rect)):
            self._echo.pop(hwnd, None)
            return True
        return False

    def _place(self, hwnd, rect):
        self._echo[hwnd] = rect
        move_visible(hwnd, rect[0], rect[1], rect[2], rect[3])

    def _on_event(self, hook, event, hwnd, id_object, id_child, thread, ms):
        # 只关心「整个窗口」这一层，子对象（按钮、输入框）也会发这个事件
        if id_object != OBJID_WINDOW or id_child != 0:
            return
        if hwnd not in (self.left, self.right):
            return
        if not is_usable(self.left) or not is_usable(self.right):
            return
        cur = visible_rect(hwnd)
        if not cur:
            return
        prev = self._last.get(hwnd)
        self._last[hwnd] = cur
        if prev is None or prev == cur:
            return
        # 自己刚摆的位置：消费掉，别当成用户拖动（否则会无限对推）
        if self._is_echo(hwnd, cur):
            return
        dst = self.right if hwnd == self.left else self.left
        dprev = self._last.get(dst)
        if not dprev:
            return
        target, src_fix = plan_follow(prev, cur, dprev,
                                      src_is_left=(hwnd == self.left), gap=self.gap)
        if src_fix:
            # 对面已经窄到下限，接缝不能再往那边走了：把被拖的这条边推回去。
            # 不推的话窗口继续变宽，接缝就裂开了。
            self._place(hwnd, src_fix)
            self._last[hwnd] = src_fix
        if target != dprev:
            self._place(dst, target)
            self._last[dst] = target
