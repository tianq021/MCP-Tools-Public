"""Minimal Win32 helpers used by the headless-window feasibility harness.

Only window visibility/identity + process resource queries are wrapped; nothing
here changes game state or injects code.  All functions are read-only unless the
caller explicitly asks to hide/show a window.
"""
import ctypes
import ctypes.wintypes as w
import os
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)

# window styles / commands
SW_HIDE = 0
SW_SHOWNORMAL = 1
SW_SHOW = 5
SW_RESTORE = 9
GWL_STYLE = -16
WS_VISIBLE = 0x10000000
WS_MINIMIZE = 0x20000000

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010

WNDENUMPROC = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", w.DWORD), ("dwHighDateTime", w.DWORD)]


class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    _fields_ = [
        ("cb", w.DWORD), ("PageFaultCount", w.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


user32.EnumWindows.argtypes = [WNDENUMPROC, w.LPARAM]
user32.EnumWindows.restype = w.BOOL
user32.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
user32.GetWindowThreadProcessId.restype = w.DWORD
user32.GetWindowTextLengthW.argtypes = [w.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.IsWindowVisible.argtypes = [w.HWND]
user32.IsWindowVisible.restype = w.BOOL
user32.IsIconic.argtypes = [w.HWND]
user32.IsIconic.restype = w.BOOL
user32.IsWindow.argtypes = [w.HWND]
user32.IsWindow.restype = w.BOOL
user32.ShowWindow.argtypes = [w.HWND, ctypes.c_int]
user32.ShowWindow.restype = w.BOOL
user32.GetWindowRect.argtypes = [w.HWND, ctypes.POINTER(RECT)]
user32.GetWindowRect.restype = w.BOOL
user32.GetWindowLongW.argtypes = [w.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.GetForegroundWindow.restype = w.HWND
user32.GetWindow.argtypes = [w.HWND, ctypes.c_uint]
user32.GetWindow.restype = w.HWND
user32.GetShellWindow.restype = w.HWND

psapi.EnumProcesses.argtypes = [ctypes.POINTER(w.DWORD), w.DWORD, ctypes.POINTER(w.DWORD)]
psapi.EnumProcesses.restype = w.BOOL
kernel32.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
kernel32.OpenProcess.restype = w.HANDLE
kernel32.CloseHandle.argtypes = [w.HANDLE]
kernel32.CloseHandle.restype = w.BOOL
kernel32.GetProcessTimes.argtypes = [w.HANDLE, ctypes.POINTER(FILETIME),
                                     ctypes.POINTER(FILETIME),
                                     ctypes.POINTER(FILETIME),
                                     ctypes.POINTER(FILETIME)]
kernel32.GetProcessTimes.restype = w.BOOL
kernel32.GetExitCodeProcess.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
kernel32.GetExitCodeProcess.restype = w.BOOL
kernel32.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR,
                                                ctypes.POINTER(w.DWORD)]
kernel32.QueryFullProcessImageNameW.restype = w.BOOL
psapi.GetProcessMemoryInfo.argtypes = [w.HANDLE, ctypes.c_void_p, w.DWORD]
psapi.GetProcessMemoryInfo.restype = w.BOOL


def _window_text(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 2)
    user32.GetWindowTextW(hwnd, buf, n + 2)
    return buf.value


def _window_class(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def list_windows():
    """All top-level windows with pid/title/class/visibility/rect."""
    result = []

    @WNDENUMPROC
    def _cb(hwnd, _lparam):
        pid = w.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        rect = RECT()
        ok = user32.GetWindowRect(hwnd, ctypes.byref(rect))
        result.append({
            "hwnd": int(hwnd),
            "pid": int(pid.value),
            "title": _window_text(hwnd),
            "class": _window_class(hwnd),
            "visible": bool(user32.IsWindowVisible(hwnd)),
            "iconic": bool(user32.IsIconic(hwnd)),
            "style": int(user32.GetWindowLongW(hwnd, GWL_STYLE)),
            "rect": [rect.left, rect.top, rect.right, rect.bottom] if ok else None,
            "width": (rect.right - rect.left) if ok else 0,
            "height": (rect.bottom - rect.top) if ok else 0,
        })
        return True

    user32.EnumWindows(_cb, 0)
    return result


def list_processes():
    """[(pid, exe_path_lower)] for all processes we may open."""
    arr = (w.DWORD * 4096)()
    needed = w.DWORD(0)
    if not psapi.EnumProcesses(arr, ctypes.sizeof(arr), ctypes.byref(needed)):
        return []
    count = needed.value // ctypes.sizeof(w.DWORD)
    out = []
    size = w.DWORD(1024)
    buf = ctypes.create_unicode_buffer(1024)
    for i in range(count):
        pid = int(arr[i])
        if pid == 0:
            continue
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            continue
        try:
            size = w.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                out.append((pid, buf.value))
        finally:
            kernel32.CloseHandle(handle)
    return out


def find_processes_by_exe(exe_name):
    """PIDs whose image basename equals exe_name (case-insensitive)."""
    want = exe_name.lower()
    return [pid for pid, path in list_processes()
            if os.path.basename(path).lower() == want]


def is_process_alive(pid):
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return False
    try:
        code = w.DWORD(0)
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == 259  # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def process_exit_code(pid):
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return None
    try:
        code = w.DWORD(0)
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return None
        return None if code.value == 259 else code.value
    finally:
        kernel32.CloseHandle(handle)


def process_cpu_memory(pid):
    """(cpu_total_seconds, working_set_bytes, private_bytes) or None."""
    handle = kernel32.OpenProcess(
        PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, int(pid))
    if not handle:
        return None
    try:
        creation, exit_t, kernel, user = FILETIME(), FILETIME(), FILETIME(), FILETIME()
        if not kernel32.GetProcessTimes(handle, ctypes.byref(creation),
                                        ctypes.byref(exit_t), ctypes.byref(kernel),
                                        ctypes.byref(user)):
            return None

        def _to_sec(ft):
            return ((ft.dwHighDateTime << 32) | ft.dwLowDateTime) / 1e7

        cpu = _to_sec(kernel) + _to_sec(user)
        counters = PROCESS_MEMORY_COUNTERS_EX()
        counters.cb = ctypes.sizeof(counters)
        ws = priv = 0
        if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters),
                                      ctypes.sizeof(counters)):
            ws = int(counters.WorkingSetSize)
            priv = int(counters.PrivateUsage)
        return cpu, ws, priv
    finally:
        kernel32.CloseHandle(handle)


def window_rect(hwnd):
    rect = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return [int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)]


def is_window_visible(hwnd):
    return bool(user32.IsWindow(hwnd)) and bool(user32.IsWindowVisible(hwnd))


def hide_window(hwnd):
    """ShowWindow(SW_HIDE): keep HWND, process, message loop and DDraw alive."""
    user32.ShowWindow(hwnd, SW_HIDE)
    return not is_window_visible(hwnd)


def show_window(hwnd):
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    user32.ShowWindow(hwnd, SW_SHOW)
    return is_window_visible(hwnd)


def candidate_game_windows(pid, visible_only=True):
    """Top-level windows owned by pid, biggest first.

    The game creates several windows (main game window, CnCNet spawner help
    window, splash etc).  The first entry with a plausible client area and no
    child relationship is used as the game window.
    """
    wins = [x for x in list_windows() if x["pid"] == int(pid)]
    if visible_only:
        wins = [x for x in wins if x["visible"]]
    wins = [x for x in wins if x["width"] > 200 and x["height"] > 100]
    wins.sort(key=lambda x: x["width"] * x["height"], reverse=True)
    return wins


def pick_game_window(pid):
    wins = candidate_game_windows(pid)
    if not wins:
        return None
    preferred = None
    for x in wins:
        text = (x["title"] or "") + " " + (x["class"] or "")
        if any(key in text.lower() for key in ("yuri", "gamemd", "ra2", "red alert",
                                               "command", "cncnet")):
            preferred = x
            break
    return preferred or wins[0]


def snapshot_windows(pid):
    return [x for x in list_windows() if x["pid"] == int(pid)]


def wait_for_window(pid, timeout_s=60.0, poll_s=0.25):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        win = pick_game_window(pid)
        if win is not None:
            return win
        if not is_process_alive(pid):
            return None
        time.sleep(poll_s)
    return None
