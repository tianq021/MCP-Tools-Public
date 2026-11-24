"""Windows binding shared by the local game loop and its controlled test fixture."""
import ctypes as C
from ctypes import wintypes as W
import json
import re
from pathlib import Path
import time
import os
import subprocess
import sys
import uuid
import threading
from datetime import datetime, timezone
from actions import KEYS, BUTTONS

DATA = Path(r"G:\Codex_mmx\desktop-control-data")
OBSERVATION = DATA / "observation.json"
PAUSE = DATA / "paused.flag"
MUTEX = r"Local\CodexDshDesktopInput"


class Stopped(RuntimeError):
    pass


class Mouse(C.Structure):
    _fields_ = [("dx", W.LONG), ("dy", W.LONG), ("data", W.DWORD),
                ("flags", W.DWORD), ("time", W.DWORD), ("extra", C.c_size_t)]


class Keyboard(C.Structure):
    _fields_ = [("vk", W.WORD), ("scan", W.WORD), ("flags", W.DWORD),
                ("time", W.DWORD), ("extra", C.c_size_t)]


class Payload(C.Union):
    _fields_ = [("mouse", Mouse), ("key", Keyboard)]


class Input(C.Structure):
    _fields_ = [("type", W.DWORD), ("data", Payload)]


class Windows:
    def __init__(self):
        self.u = C.WinDLL("user32", use_last_error=True)
        self.k = C.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "GetForegroundWindow": ([], W.HWND),
            "GetWindowRect": ([W.HWND, C.POINTER(W.RECT)], W.BOOL),
            "GetClientRect": ([W.HWND, C.POINTER(W.RECT)], W.BOOL),
            "ClientToScreen": ([W.HWND, C.POINTER(W.POINT)], W.BOOL),
            "GetWindowThreadProcessId": ([W.HWND, C.POINTER(W.DWORD)], W.DWORD),
            "GetWindowTextW": ([W.HWND, W.LPWSTR, C.c_int], C.c_int),
            "GetAsyncKeyState": ([C.c_int], C.c_short),
            "GetCursorPos": ([C.POINTER(W.POINT)], W.BOOL),
            "SetCursorPos": ([C.c_int, C.c_int], W.BOOL),
            "SendInput": ([W.UINT, C.POINTER(Input), C.c_int], W.UINT),
            "IsWindow": ([W.HWND], W.BOOL),
            "IsIconic": ([W.HWND], W.BOOL),
            "MapVirtualKeyW": ([W.UINT, W.UINT], W.UINT),
            "GetSystemMetrics": ([C.c_int], C.c_int),
        }
        for name, (args, ret) in signatures.items():
            getattr(self.u, name).argtypes = args
            getattr(self.u, name).restype = ret
        for name, args, ret in [
            ("CreateMutexW", [C.c_void_p, W.BOOL, W.LPCWSTR], W.HANDLE),
            ("WaitForSingleObject", [W.HANDLE, W.DWORD], W.DWORD),
            ("ReleaseMutex", [W.HANDLE], W.BOOL),
            ("CloseHandle", [W.HANDLE], W.BOOL),
            ("OpenProcess", [W.DWORD, W.BOOL, W.DWORD], W.HANDLE),
            ("QueryFullProcessImageNameW", [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)], W.BOOL),
        ]:
            getattr(self.k, name).argtypes = args
            getattr(self.k, name).restype = ret
        try:
            self.u.SetProcessDpiAwarenessContext.argtypes = [C.c_void_p]
            self.u.SetProcessDpiAwarenessContext(C.c_void_p(-4))
        except AttributeError:
            self.u.SetProcessDPIAware()

    def rect(self, hwnd):
        r = W.RECT()
        if not self.u.GetWindowRect(hwnd, C.byref(r)):
            raise Stopped("无法读取窗口位置。")
        return [r.left, r.top, r.right, r.bottom]

    def client(self, hwnd):
        r, p = W.RECT(), W.POINT()
        if not self.u.GetClientRect(hwnd, C.byref(r)) or not self.u.ClientToScreen(hwnd, C.byref(p)):
            raise Stopped("无法读取游戏绘图区。")
        return [p.x, p.y, p.x + r.right, p.y + r.bottom]

    def pid(self, hwnd):
        pid = W.DWORD()
        self.u.GetWindowThreadProcessId(hwnd, C.byref(pid))
        return pid.value

    def executable(self, pid):
        handle = self.k.OpenProcess(0x1000, False, pid)
        if not handle:
            raise Stopped("无法核对游戏进程。")
        try:
            text, length = C.create_unicode_buffer(32768), W.DWORD(32768)
            if not self.k.QueryFullProcessImageNameW(handle, 0, text, C.byref(length)):
                raise Stopped("无法核对游戏程序路径。")
            return text.value
        finally:
            self.k.CloseHandle(handle)

    def cursor(self):
        point = W.POINT()
        if not self.u.GetCursorPos(C.byref(point)):
            raise Stopped("无法读取鼠标位置。")
        return point.x, point.y

    def down(self, key):
        return bool(self.u.GetAsyncKeyState(key) & 0x8000)

    def screen(self):
        return [0, 0, self.u.GetSystemMetrics(0), self.u.GetSystemMetrics(1)]


class Guard:
    def __init__(self, win, observation_id, expected_exe=None, cancel=None, duration_s=25):
        self.win = win
        self.handle = None
        self.held = False
        self.applied = False
        self.keys = set()
        self.key_events = {}
        self.buttons = set()
        self.id = observation_id
        self.expected_exe = Path(expected_exe) if expected_exe else None
        self.cancel = cancel
        self.deadline = time.perf_counter() + duration_s
        self.session = uuid.uuid4().hex
        self.journal = DATA / "active-input.json"
        self.watchdog = None

    def __enter__(self):
        self.handle = self.win.k.CreateMutexW(None, False, MUTEX)
        if not self.handle:
            raise Stopped("无法取得共享控制锁。")
        result = self.win.k.WaitForSingleObject(self.handle, 0)
        if result not in (0, 0x80):
            self.win.k.CloseHandle(self.handle)
            self.handle = None
            raise Stopped("另一个控制器正在操作桌面。")
        self.held = True
        try:
            from watchdog import recover
            recover(self.win)
            self.saved = json.loads(OBSERVATION.read_text(encoding="utf-8-sig"))
            if self.saved["id"] != self.id:
                raise Stopped("观察已更新，请重新观察。")
            self.hwnd = self.saved["hwnd"]
            actual = Path(self.win.executable(self.saved["pid"]))
            if self.expected_exe is not None and actual.resolve() != self.expected_exe.resolve():
                raise Stopped("当前窗口不是指定程序。")
            self.expected_exe = actual
            self.client_rect = self.win.client(self.hwnd)
            self.expected_cursor = self.win.cursor()
            self.check()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def consume(self):
        import uuid
        self.id = "consumed-" + uuid.uuid4().hex
        self.saved["id"] = self.id
        OBSERVATION.write_text(json.dumps(self.saved), encoding="utf-8")

    def renew(self):
        import uuid
        self.check()
        self.saved.update(id=uuid.uuid4().hex, created_utc=datetime.now(timezone.utc).isoformat())
        self.id = self.saved["id"]
        OBSERVATION.write_text(json.dumps(self.saved), encoding="utf-8")
        return self.id

    def check(self):
        w = self.win
        if self.cancel is not None and self.cancel.is_set():
            raise Stopped("调用已断开或取消，已停止并释放输入。")
        if time.perf_counter() > self.deadline:
            raise Stopped("本地控制到达时限，已停止并释放输入。")
        if PAUSE.exists():
            raise Stopped("桌面控制已暂停；仅在用户要求恢复后继续。")
        if w.down(0x1B):
            PAUSE.write_text("escape", encoding="utf-8")
            raise Stopped("检测到 Esc，已持续暂停。")
        pos = w.cursor()
        if 0 <= pos[0] <= 1 and 0 <= pos[1] <= 1:
            PAUSE.write_text("pointer-failsafe", encoding="utf-8")
            raise Stopped("鼠标位于主屏左上角，已持续暂停。")
        if max(abs(pos[i] - self.expected_cursor[i]) for i in (0, 1)) > 5:
            l, t, r, b = self.client_rect
            centered = abs(pos[0] - (l + r) // 2) <= 3 and abs(pos[1] - (t + b) // 2) <= 3
            if not getattr(self, "relative_mode", False) or not centered:
                raise Stopped("检测到使用者移动鼠标，已停止本次任务。")
            self.expected_cursor = pos
        # .NET writes seven fractional digits; Python 3.10 accepts at most six.
        stamp = re.sub(r"(\.\d{6})\d+", r"\1", self.saved["created_utc"])
        created = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - created.astimezone(timezone.utc)).total_seconds()
        if age < -1 or age > 120:
            raise Stopped("观察已过期，请重新观察。")
        if (w.u.GetForegroundWindow() != self.hwnd or not w.u.IsWindow(self.hwnd)
                or w.u.IsIconic(self.hwnd) or w.pid(self.hwnd) != self.saved["pid"]
                or w.rect(self.hwnd) != self.saved["window_rect"]
                or w.screen() != self.saved["screen_rect"]
                or w.client(self.hwnd) != self.client_rect):
            raise Stopped("游戏失焦、移动、缩放或显示器变化，已停止。")
        l, t, r, b = self.client_rect
        if l < 0 or t < 0 or r > w.screen()[2] or b > w.screen()[3] or r <= l or b <= t:
            raise Stopped("游戏绘图区必须完整位于主显示器。")
        for vk in (0x10, 0x11, 0x12, 0x5B, 0x5C, 1, 2, 4):
            if vk not in self.keys and vk not in self.buttons and w.down(vk):
                raise Stopped("检测到使用者正在按键或按住鼠标，已停止。")
        if json.loads(OBSERVATION.read_text(encoding="utf-8-sig"))["id"] != self.id:
            raise Stopped("共享观察发生变化，已停止。")

    def inject(self, events):
        self.check()
        self.applied = True
        array = (Input * len(events))(*events)
        if self.win.u.SendInput(len(events), array, C.sizeof(Input)) != len(events):
            raise Stopped("Windows 未接受全部输入；可能已部分执行，不能重放。")

    def write_journal(self):
        record = {"session": self.session, "pid": os.getpid(),
                  "keys": [{"scan": scan, "flags": flags} for scan, flags in self.key_events.values()],
                  "buttons": [up for vk, _, up in BUTTONS.values() if vk in self.buttons]}
        pending = self.journal.with_suffix(".pending")
        pending.write_text(json.dumps(record), encoding="utf-8")
        pending.replace(self.journal)
        if self.watchdog is None:
            self.watchdog = subprocess.Popen(
                [sys.executable, "-B", str(Path(__file__).with_name("watchdog.py")), self.session],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW)
            ready = threading.Event()
            accepted = []
            def handshake():
                accepted.append(self.watchdog.stdout.readline().strip() == b"ready")
                ready.set()
            threading.Thread(target=handshake, daemon=True).start()
            if not ready.wait(2) or not accepted[0]:
                raise Stopped("输入恢复监护程序未就绪，未提交新的输入。")

    def move(self, x, y):
        self.check()
        l, t, r, b = self.client_rect
        px, py = l + int(x), t + int(y)
        if not l <= px < r or not t <= py < b:
            raise ValueError("点击位置超出游戏绘图区。")
        self.applied = True
        if not self.win.u.SetCursorPos(px, py):
            raise Stopped("Windows 拒绝移动鼠标。")
        self.expected_cursor = self.win.cursor()
    def button_down(self, name="left"):
        vk, down, up = BUTTONS[name]
        if vk in self.buttons:
            return
        self.check()
        if self.win.down(vk):
            raise Stopped("目标鼠标按钮已由使用者按住。")
        self.buttons.add(vk)
        self.write_journal()
        self.inject([Input(type=0, data=Payload(mouse=Mouse(flags=down)))])

    def button_up(self, name="left"):
        vk, down, up = BUTTONS[name]
        if vk in self.buttons:
            self._release(Input(type=0, data=Payload(mouse=Mouse(flags=up))))
            self.buttons.remove(vk)
            self.write_journal()

    def click(self, x, y, button="left", duration=0.035):
        self.move(x, y)
        if BUTTONS[button][0] in self.buttons:
            raise ValueError("不能点击已经由当前会话按住的鼠标按钮。")
        try:
            self.button_down(button)
            self.wait(duration)
        finally:
            self.button_up(button)

    def relative_move(self, dx, dy, duration=0):
        # FPS games may recenter the cursor. Do not interpret that as human input.
        self.relative_mode = True
        count = max(1, round(duration * 120))
        last_x = last_y = 0
        for i in range(1, count + 1):
            x, y = round(dx * i / count), round(dy * i / count)
            self.inject([Input(type=0, data=Payload(mouse=Mouse(dx=x-last_x, dy=y-last_y, flags=1)))])
            last_x, last_y = x, y
            self.expected_cursor = self.win.cursor()
            if duration:
                self.wait(duration / count)

    def scroll(self, amount):
        self.inject([Input(type=0, data=Payload(mouse=Mouse(data=(amount * 120) & 0xFFFFFFFF, flags=0x800)))])

    def drag(self, x, y, to_x, to_y, duration, button):
        self.move(x, y)
        try:
            self.button_down(button)
            count = max(2, round(duration * 60))
            for i in range(1, count + 1):
                self.move(round(x + (to_x - x) * i / count), round(y + (to_y - y) * i / count))
                self.wait(duration / count)
        finally:
            self.button_up(button)

    def key_down(self, names):
        self.check()
        for name in names:
            vk = KEYS[name]
            if vk in self.keys:
                continue
            if self.win.down(vk):
                raise Stopped("目标按键已由使用者按住。")
            scan = self.win.u.MapVirtualKeyW(vk, 0)
            if not scan:
                raise ValueError("当前键盘映射中没有此按键。")
            flags = 8 | (1 if 0x21 <= vk <= 0x2E else 0)
            self.keys.add(vk)
            self.key_events[vk] = (scan, flags)
            self.write_journal()
            self.inject([Input(type=1, data=Payload(key=Keyboard(scan=scan, flags=flags)))])

    def key_up(self, names):
        for name in reversed(names):
            vk = KEYS[name]
            if vk in self.keys:
                scan, flags = self.key_events[vk]
                self._release(Input(type=1, data=Payload(key=Keyboard(scan=scan, flags=flags | 2))))
                self.keys.remove(vk)
                del self.key_events[vk]
                self.write_journal()

    def press_keys(self, names, duration=0.035):
        if any(KEYS[name] in self.keys for name in names):
            raise ValueError("不能短按已经由当前会话按住的键。")
        try:
            self.key_down(names)
            self.wait(duration)
        finally:
            self.key_up(names)

    def key(self, name):
        self.press_keys([name])

    def wait(self, seconds):
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            self.check()
            time.sleep(min(0.005, max(0, end - time.perf_counter())))

    def _release(self, event):
        if self.win.u.SendInput(1, C.byref(event), C.sizeof(Input)) != 1:
            raise Stopped("输入释放未被系统确认，请检查按键状态。")

    def release_button(self):
        failures = []
        for name, (vk, _, _) in BUTTONS.items():
            try:
                self.button_up(name)
            except Exception as error:
                failures.append(str(error))
        if failures:
            raise Stopped("; ".join(failures))

    def release_keys(self):
        failures = []
        for name, vk in KEYS.items():
            if vk in self.keys:
                try:
                    self.key_up([name])
                except Exception as error:
                    failures.append(str(error))
        if failures:
            raise Stopped("; ".join(failures))

    def __exit__(self, *_):
        try:
            try:
                self.release_button()
            finally:
                self.release_keys()
            if not self.keys and not self.buttons and self.journal.exists():
                record = json.loads(self.journal.read_text(encoding="utf-8"))
                if record["session"] == self.session:
                    self.journal.unlink()
        finally:
            if self.held:
                self.win.k.ReleaseMutex(self.handle)
                self.held = False
            if self.handle:
                self.win.k.CloseHandle(self.handle)
                self.handle = None
