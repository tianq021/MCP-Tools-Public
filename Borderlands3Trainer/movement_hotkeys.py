"""Windows registered hotkeys; does not poll or intercept ordinary game input."""
import ctypes
from ctypes import wintypes
import queue
import threading


class MovementHotkeys:
    KEYS = {0xB301: 1, 0xB302: 2, 0xB303: 3}

    def __init__(self):
        self.events = queue.Queue()
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.registered = []
        self.failed = []
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        self.ready.wait(3)

    def _run(self):
        self.user32 = ctypes.WinDLL('user32', use_last_error=True)
        self.user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        self.user32.RegisterHotKey.restype = wintypes.BOOL
        self.user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                          wintypes.UINT, wintypes.UINT, wintypes.UINT]
        self.thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        for ident, multiplier in self.KEYS.items():
            # MOD_CONTROL | MOD_ALT | MOD_NOREPEAT
            if self.user32.RegisterHotKey(None, ident, 0x4003, ord(str(multiplier))):
                self.registered.append(ident)
            else:
                self.failed.append(multiplier)
        self.ready.set()
        try:
            message = wintypes.MSG()
            while not self.stop.is_set():
                while self.user32.PeekMessageW(ctypes.byref(message), None, 0x312, 0x312, 1):
                    if message.wParam in self.registered:
                        self.events.put(self.KEYS[message.wParam])
                self.stop.wait(.05)
        finally:
            for ident in self.registered:
                self.user32.UnregisterHotKey(None, ident)
            self.registered.clear()

    def poll(self):
        selected = None
        while True:
            try:
                selected = self.events.get_nowait()
            except queue.Empty:
                return selected

    def close(self):
        self.stop.set()
        self.thread.join(timeout=2)
