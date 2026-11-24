"""Exclusive lock so multiple collectors cannot fight over one game instance.

Regression: a manual episode was launched while a batch collector was running;
the runner's `kill_games()` killed the batch's game and the batch recorded a
bogus `crash`. A lock file with the holder pid makes that collision explicit.

This module has no heavy dependencies (no pyra2yr / aiohttp) so it is unit
testable offline.
"""
import json
import os
import pathlib
import subprocess
import time
import hashlib

_MUTEXES = {}

# Default lock directory. RUN02 run-protection: the controller may run under a
# low-integrity interpreter (real .venv-rl) that cannot write the Medium-integrity
# `build/` tree; DSH_LOCK_DIR lets an approved run keep the lock in a writable
# run-scoped directory without changing lock semantics (still one lock, same
# acquire/release/pid-check code).
_ROOT = pathlib.Path(__file__).resolve().parents[2]


def lock_dir() -> pathlib.Path:
    override = os.environ.get("DSH_LOCK_DIR")
    return pathlib.Path(override) if override else _ROOT / "build"


def _take_mutex(path):
    """Serialize contenders, including stale-file recovery (Windows host)."""
    key = str(path.resolve()).casefold()
    if key in _MUTEXES:
        raise RuntimeError(f"another collector is running (lock={path})")
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    name = "Local\\RA2MCP-" + hashlib.sha256(key.encode()).hexdigest()
    handle = kernel.CreateMutexW(None, False, name)
    if not handle:
        raise RuntimeError(f"cannot create game mutex: {ctypes.get_last_error()}")
    if kernel.WaitForSingleObject(handle, 0) not in (0, 0x80):
        kernel.CloseHandle(handle)
        raise RuntimeError(f"another collector is running (lock={path})")
    return kernel, handle


def _drop_mutex(mutex):
    if mutex:
        kernel, handle = mutex
        kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


def _pid_alive(pid):
    """Unknown process state is treated as alive; never steal on query failure."""
    pid = int(pid)
    if pid == os.getpid():
        return True
    if pid <= 0:
        return True
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        handle = kernel.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() != 87  # INVALID_PARAMETER = absent PID
        try:
            code = wintypes.DWORD()
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return True


def _pid_start_epoch(pid):
    """Process creation time (Unix epoch) for reuse detection; None if unknown."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, int(pid))  # QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        creation, exit_time, kernel_time, user_time = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
        if not kernel.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_time),
                                      ctypes.byref(kernel_time), ctypes.byref(user_time)):
            return None
        ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        return ticks / 10_000_000.0 - 11644473600.0
    finally:
        kernel.CloseHandle(handle)


def _pid_reused(pid, created_at_epoch):
    """True only when the process start time proves the PID was recycled.

    Windows recycles PIDs quickly, so a live PID is not evidence that the lock
    is still held: if the process started after the lock file was written, the
    original holder is dead. Unknown age is never treated as evidence, and a
    missing start time keeps the conservative behaviour.
    """
    try:
        created_at = float(created_at_epoch)
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0 or created_at <= 0:
        return False
    started = _pid_start_epoch(pid)
    return started is not None and started > created_at + 2.0


def reclaim_stale(directory, patterns=("*.lock",)):
    """Delete lock files whose recorded holder is provably gone or recycled.

    Must run before acquiring locks (process start), never while an episode may
    be live. Live holders and unreadable files are kept for a human to inspect.
    """
    removed, kept = [], []
    for pattern in patterns:
        for path in sorted(pathlib.Path(directory).glob(pattern)):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                holder = int(data.get("pid") or 0)
                created_at = data.get("created_at_epoch")
            except (OSError, ValueError, TypeError):
                kept.append((path, "unreadable"))
                continue
            if not holder or (_pid_alive(holder) and not _pid_reused(holder, created_at)):
                kept.append((path, f"live pid={holder}"))
                continue
            mutex = None
            try:
                mutex = _take_mutex(path)
                path.unlink()
                removed.append(path)
            except RuntimeError:
                kept.append((path, "in use"))
            except OSError:
                kept.append((path, "delete failed"))
            finally:
                _drop_mutex(mutex)
    return removed, kept


def acquire(path, stale_after_s=6 * 3600):
    """Create the lock or raise RuntimeError when a live collector holds it."""
    path = pathlib.Path(path)
    mutex = _take_mutex(path)
    try:
        held = _acquire_file(path, stale_after_s)
    except BaseException:
        _drop_mutex(mutex)
        raise
    _MUTEXES[str(path.resolve()).casefold()] = mutex
    return held


def _acquire_file(path, stale_after_s):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        holder, created_at = None, None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            holder = data.get("pid")
            created_at = data.get("created_at_epoch")
        except (ValueError, OSError):
            raise RuntimeError(f"cannot validate game lock; inspect before recovery: {path}") from None
        if not holder:
            raise RuntimeError(
                f"cannot validate game lock; inspect before recovery: {path}")
        # Age alone is never evidence that a live controller has stopped, but a
        # live PID whose process is newer than the lock proves PID recycling.
        if _pid_alive(holder) and not _pid_reused(holder, created_at):
            raise RuntimeError(
                f"another collector is running (pid={holder}, lock={path})")
        try:
            path.unlink()
        except OSError:
            pass
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RuntimeError(f"lock race: {path}") from None
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps({"pid": os.getpid(),
                            "created_at_epoch": time.time(),
                            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S")}))
    return path


def release(path):
    path = pathlib.Path(path)
    try:
        if path.exists():
            holder = json.loads(path.read_text(encoding="utf-8")).get("pid")
            if holder == os.getpid():
                path.unlink()
    except Exception:  # noqa: BLE001
        pass
    finally:
        _drop_mutex(_MUTEXES.pop(str(path.resolve()).casefold(), None))
