"""Read-only resource accounting for the owned inference process."""
import ctypes
from ctypes import wintypes
import os

import pynvml


class MemoryStatus(ctypes.Structure):
    _fields_ = [('length', wintypes.DWORD), ('load', wintypes.DWORD)] + [
        (name, ctypes.c_ulonglong) for name in (
            'total', 'available', 'total_page', 'available_page',
            'total_virtual', 'available_virtual', 'available_extended')]


class ProcessMemory(ctypes.Structure):
    _fields_ = [('cb', wintypes.DWORD), ('faults', wintypes.DWORD)] + [
        (name, ctypes.c_size_t) for name in (
            'peak_working_set', 'working_set', 'peak_paged', 'paged',
            'peak_nonpaged', 'nonpaged', 'pagefile', 'peak_pagefile', 'private')]


def memory_snapshot(pid=None):
    result = {'total': None, 'available': None, 'process_rss': 0 if pid is None else None,
              'process_private': 0 if pid is None else None}
    if os.name != 'nt':
        return result
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    status = MemoryStatus()
    status.length = ctypes.sizeof(status)
    kernel.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(MemoryStatus)]
    kernel.GlobalMemoryStatusEx.restype = wintypes.BOOL
    if kernel.GlobalMemoryStatusEx(ctypes.byref(status)):
        result.update(total=status.total, available=status.available)
    if pid is None:
        return result
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessMemory), wintypes.DWORD]
    kernel.K32GetProcessMemoryInfo.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if handle:
        try:
            counters = ProcessMemory()
            counters.cb = ctypes.sizeof(counters)
            if kernel.K32GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                result.update(process_rss=counters.working_set, process_private=counters.private)
        finally:
            kernel.CloseHandle(handle)
    return result


def process_gpu_memory(handle, pid, total):
    if pid is None:
        return 0
    values = []
    for query in (pynvml.nvmlDeviceGetComputeRunningProcesses, pynvml.nvmlDeviceGetGraphicsRunningProcesses):
        try:
            for process in query(handle):
                if process.pid == pid:
                    used = process.usedGpuMemory
                    # WDDM reports None / NVML_VALUE_NOT_AVAILABLE, never a real byte count.
                    if used is None or used < 0 or used > total:
                        return None
                    values.append(used)
        except pynvml.NVMLError:
            pass
    # The same PID may appear in both lists; do not double-count it. No data != zero.
    return max(values) if values else None


def resource_snapshot(pid=None):
    """Read system totals and owned-process usage without loading any models."""
    gpu = None
    try:
        pynvml.nvmlInit()
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            memory = pynvml.nvmlDeviceGetMemoryInfo(handle)
            gpu = {'name': pynvml.nvmlDeviceGetName(handle), 'used': memory.used,
                   'total': memory.total, 'free': memory.free,
                   'utilization': pynvml.nvmlDeviceGetUtilizationRates(handle).gpu,
                   'process_used': process_gpu_memory(handle, pid, memory.total)}
        finally:
            pynvml.nvmlShutdown()
    except pynvml.NVMLError:
        pass
    return {'gpu': gpu, 'memory': memory_snapshot(pid)}
