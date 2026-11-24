"""Windows process helpers and the persistent engine registry.

The inference engine is no longer a child of the web service: it is started
detached, described by `data/engine.json`, and controlled by PID. That is what
lets closing the browser, restarting the service or suspending the machine keep
the model alive, while an explicit unload or a shutdown releases it.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import time

if os.name == 'nt':
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
else:  # pragma: no cover - the project targets Windows
    kernel32 = psapi = None

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
PROCESS_VM_READ = 0x0010
SYNCHRONIZE = 0x00100000
STILL_ACTIVE = 259

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


class ProcessMemory(ctypes.Structure):
    _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD),
                ('PeakWorkingSetSize', ctypes.c_size_t), ('WorkingSetSize', ctypes.c_size_t),
                ('QuotaPeakPagedPoolUsage', ctypes.c_size_t), ('QuotaPagedPoolUsage', ctypes.c_size_t),
                ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t), ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                ('PagefileUsage', ctypes.c_size_t), ('PeakPagefileUsage', ctypes.c_size_t)]


def pid_alive(pid):
    if not pid or pid <= 0:
        return False
    if os.name != 'nt':
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def process_path(pid):
    if os.name != 'nt' or not pid_alive(pid):
        return None
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return None
    try:
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return None
        return buffer.value
    finally:
        kernel32.CloseHandle(handle)


def process_birth(pid):
    """Windows creation FILETIME, used to reject a reused PID before termination."""
    if os.name != 'nt' or not pid:
        return None
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return None
    try:
        creation, exit_time, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if kernel32.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_time), ctypes.byref(kernel), ctypes.byref(user)):
            return (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        return None
    finally:
        kernel32.CloseHandle(handle)


def process_memory(pid):
    """Working set / private commit in bytes, or None when unreadable."""
    if os.name != 'nt' or not pid_alive(pid):
        return None
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ, False, int(pid))
    if not handle:
        return None
    try:
        counters = ProcessMemory()
        counters.cb = ctypes.sizeof(ProcessMemory)
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return None
        return {'process_rss': counters.WorkingSetSize, 'process_private': counters.PagefileUsage}
    finally:
        kernel32.CloseHandle(handle)


def terminate_pid(pid, timeout=10.0):
    """End a process we own by PID; returns True when it is gone afterwards."""
    if not pid_alive(pid):
        return True
    if os.name != 'nt':
        try:
            os.kill(pid, 15)
        except OSError:
            return not pid_alive(pid)
        return not pid_alive(pid)
    handle = kernel32.OpenProcess(PROCESS_TERMINATE | SYNCHRONIZE, False, int(pid))
    if not handle:
        return not pid_alive(pid)
    try:
        kernel32.TerminateProcess(handle, 1)
        kernel32.WaitForSingleObject(handle, int(timeout * 1000))
    finally:
        kernel32.CloseHandle(handle)
    return not pid_alive(pid)


def spawn_flags():
    """Detached so the engine survives its launcher; breakaway when the job allows it."""
    if os.name != 'nt':
        return 0
    return DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW


class EngineRegistry:
    """`data/engine.json` — the only record of a running engine between restarts."""

    def __init__(self, data):
        self.path = Path(data) / 'engine.json'

    def read(self):
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None
        return value if isinstance(value, dict) and value.get('pid') and value.get('port') else None

    def write(self, pid, port, settings, exe):
        record = {'pid': int(pid), 'port': int(port), 'exe': str(exe),
                  'started': time.time(), 'settings': settings}
        temp = self.path.with_name(self.path.name + '.tmp')
        try:
            temp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
            os.replace(temp, self.path)
        finally:
            temp.unlink(missing_ok=True)
        return record

    def clear(self):
        """Retire the record without deleting the file: an empty record reads as none."""
        try:
            self.path.write_text('{}', encoding='utf-8')
        except OSError:
            pass
