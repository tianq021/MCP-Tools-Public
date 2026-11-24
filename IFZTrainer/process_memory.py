#!/usr/bin/env python3
"""Small reusable Win32 process-memory helper for 64-bit trainer research.

This module contains only generic process discovery and checked read/write
primitives.  Game-specific addresses, offsets and value assumptions belong in
separate code and must be validated against the current game build.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import struct


if ctypes.sizeof(ctypes.c_void_p) != 8:
    raise RuntimeError("请使用 64 位 Python 运行本工具。")


TH32CS_SNAPPROCESS = 0x00000002
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_OPERATION = 0x0008
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Process32FirstW.restype = wintypes.BOOL
kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Process32NextW.restype = wintypes.BOOL
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.ReadProcessMemory.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
kernel32.ReadProcessMemory.restype = wintypes.BOOL
kernel32.WriteProcessMemory.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
kernel32.WriteProcessMemory.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL


class ProcessMemoryError(RuntimeError):
    pass


def _win32_error(message: str) -> ProcessMemoryError:
    code = ctypes.get_last_error()
    return ProcessMemoryError(
        f"{message} (Win32 {code}: {ctypes.FormatError(code).strip()})"
    )


def find_process(executable_name: str = "Infection Free Zone.exe") -> int:
    """Return the PID of the first process whose executable name matches."""
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        raise _win32_error("无法枚举进程")
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            raise _win32_error("无法读取进程列表")
        while True:
            if entry.szExeFile.casefold() == executable_name.casefold():
                return int(entry.th32ProcessID)
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snapshot)
    raise ProcessMemoryError(f"未找到 {executable_name}，请先启动游戏。")


class ProcessMemory:
    """Checked process handle with typed read/write helpers."""

    def __init__(self, pid: int, writable: bool = False) -> None:
        access = PROCESS_QUERY_INFORMATION | PROCESS_VM_READ
        if writable:
            access |= PROCESS_VM_WRITE | PROCESS_VM_OPERATION
        self.pid = pid
        self.writable = writable
        self.handle = kernel32.OpenProcess(access, False, pid)
        if not self.handle:
            raise _win32_error(f"无法打开 PID {pid}")

    def close(self) -> None:
        if self.handle:
            kernel32.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self) -> "ProcessMemory":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def read(self, address: int, size: int) -> bytes:
        if address <= 0 or size < 0:
            raise ValueError("地址必须为正数，长度不能为负数。")
        buffer = (ctypes.c_ubyte * size)()
        count = ctypes.c_size_t()
        ok = kernel32.ReadProcessMemory(
            self.handle,
            ctypes.c_void_p(address),
            buffer,
            size,
            ctypes.byref(count),
        )
        if not ok or count.value != size:
            raise _win32_error(f"读取 0x{address:016X} 失败")
        return bytes(buffer)

    def write(self, address: int, data: bytes, expected: bytes | None = None) -> None:
        if not self.writable:
            raise ProcessMemoryError("当前句柄是只读模式。")
        if expected is not None:
            actual = self.read(address, len(expected))
            if actual != expected:
                raise ProcessMemoryError(
                    f"写入前校验失败：0x{address:016X} 的值已变化。"
                )
        source = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        count = ctypes.c_size_t()
        ok = kernel32.WriteProcessMemory(
            self.handle,
            ctypes.c_void_p(address),
            source,
            len(data),
            ctypes.byref(count),
        )
        if not ok or count.value != len(data):
            raise _win32_error(f"写入 0x{address:016X} 失败")
        if self.read(address, len(data)) != data:
            raise ProcessMemoryError(f"写入后校验失败：0x{address:016X}")

    def read_i32(self, address: int) -> int:
        return struct.unpack("<i", self.read(address, 4))[0]

    def read_u64(self, address: int) -> int:
        return struct.unpack("<Q", self.read(address, 8))[0]

    def read_f32(self, address: int) -> float:
        return struct.unpack("<f", self.read(address, 4))[0]

    def write_i32(self, address: int, value: int, expected: int | None = None) -> None:
        self.write(
            address,
            struct.pack("<i", value),
            None if expected is None else struct.pack("<i", expected),
        )

    def write_f32(
        self, address: int, value: float, expected: float | None = None
    ) -> None:
        self.write(
            address,
            struct.pack("<f", value),
            None if expected is None else struct.pack("<f", expected),
        )

