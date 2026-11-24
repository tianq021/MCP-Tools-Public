"""Bounded Windows x64 thread guard and memory backend."""
import ctypes as C
from ctypes import wintypes as W
import os
import time
from process_reader import bind, entries, close, wait


class ThreadEntry(C.Structure):
    _fields_ = [('size', W.DWORD), ('usage', W.DWORD), ('tid', W.DWORD),
                ('pid', W.DWORD), ('priority', W.LONG), ('delta', W.LONG), ('flags', W.DWORD)]


first = bind('Thread32First', W.BOOL, [W.HANDLE, C.POINTER(ThreadEntry)])
next_thread = bind('Thread32Next', W.BOOL, [W.HANDLE, C.POINTER(ThreadEntry)])
open_thread = bind('OpenThread', W.HANDLE, [W.DWORD, W.BOOL, W.DWORD])
suspend = bind('SuspendThread', W.DWORD, [W.HANDLE])
resume = bind('ResumeThread', W.DWORD, [W.HANDLE])
owner = bind('GetProcessIdOfThread', W.DWORD, [W.HANDLE])
context = bind('GetThreadContext', W.BOOL, [W.HANDLE, C.c_void_p])


class FrozenThreads:
    def __init__(self, pid, ranges, report):
        self.pid, self.ranges, self.report = pid, ranges, report
        self.handles = {}

    def tids(self):
        return {e.tid for e in entries(4, 0, ThreadEntry, first, next_thread) if e.pid == self.pid}

    def __enter__(self):
        if self.pid == os.getpid():
            raise RuntimeError('Refusing to suspend this process')
        deadline = time.monotonic() + 3
        try:
            for _ in range(4):
                tids = self.tids()
                if not tids or len(tids) > 2048:
                    raise RuntimeError('Invalid target thread count')
                for tid in tids - self.handles.keys():
                    if time.monotonic() > deadline:
                        raise RuntimeError('Thread suspension deadline exceeded')
                    handle = open_thread(0x10004A, False, tid)
                    if not handle:
                        raise C.WinError(C.get_last_error())
                    if owner(handle) != self.pid or suspend(handle) == 0xFFFFFFFF:
                        close(handle)
                        raise RuntimeError(f'Cannot suspend target thread {tid}')
                    self.handles[tid] = handle
                if self.tids() <= self.handles.keys():
                    break
            else:
                raise RuntimeError('Target thread set did not stabilize')
            for tid, handle in self.handles.items():
                # AMD64 CONTEXT, 16-byte alignment, CONTEXT_CONTROL.
                buffer = C.create_string_buffer(1232 + 15)
                pointer = (C.addressof(buffer) + 15) & ~15
                C.c_uint32.from_address(pointer + 48).value = 0x100001
                if not context(handle, pointer):
                    raise C.WinError(C.get_last_error())
                rip = C.c_uint64.from_address(pointer + 248).value
                if any(start <= rip < end for start, end in self.ranges):
                    raise RuntimeError(f'Thread {tid} is inside patch range; retry later')
            self.report['suspended_threads'] = len(self.handles)
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *unused):
        errors = []
        for tid, handle in self.handles.items():
            try:
                if resume(handle) == 0xFFFFFFFF and wait(handle, 0) != 0:
                    if resume(handle) == 0xFFFFFFFF:
                        errors.append(tid)
            finally:
                close(handle)
        self.handles.clear()
        self.report['resume_errors'] = errors
        if errors:
            raise RuntimeError(f'Failed to resume threads: {errors}')


class WindowsMemory:
    def __init__(self, handle, reader):
        self.handle, self.read = handle, reader.read
        self.vp = bind('VirtualProtectEx', W.BOOL,
                       [W.HANDLE, C.c_void_p, C.c_size_t, W.DWORD, C.POINTER(W.DWORD)])
        self.wpm = bind('WriteProcessMemory', W.BOOL,
                        [W.HANDLE, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)])
        self.flush_api = bind('FlushInstructionCache', W.BOOL, [W.HANDLE, C.c_void_p, C.c_size_t])

    def protect(self, address, size, protection):
        if address // 4096 != (address + size - 1) // 4096:
            raise RuntimeError('Patch crosses page boundary')
        old = W.DWORD()
        if not self.vp(self.handle, address, size, protection, C.byref(old)):
            raise C.WinError(C.get_last_error())
        return old.value

    def write(self, address, data):
        count = C.c_size_t()
        if not self.wpm(self.handle, address, C.create_string_buffer(data), len(data), C.byref(count)):
            raise C.WinError(C.get_last_error())
        if count.value != len(data):
            raise RuntimeError('Partial code write')

    def flush(self, address, size):
        if not self.flush_api(self.handle, address, size):
            raise C.WinError(C.get_last_error())
