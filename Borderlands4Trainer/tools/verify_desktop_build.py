"""Launch a standalone copy, observe its window/read-only startup, then close."""
import ctypes as C
import argparse
from ctypes import wintypes as W
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
C.windll.shcore.SetProcessDpiAwareness(1)
parser = argparse.ArgumentParser()
parser.add_argument('--exe', type=Path, default=ROOT / 'dist/BL4Trainer.exe')
exe = parser.parse_args().exe.resolve()
raw = exe.read_bytes()
pe = struct.unpack_from('<I', raw, 0x3c)[0]
assert raw[pe:pe + 4] == b'PE\0\0'
assert struct.unpack_from('<H', raw, pe + 4)[0] == 0x8664
assert struct.unpack_from('<H', raw, pe + 24 + 68)[0] == 2, 'Must be Windows GUI subsystem'
user = C.WinDLL('user32', use_last_error=True)
callback_type = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
user.EnumWindows.argtypes = [callback_type, W.LPARAM]
user.EnumThreadWindows.argtypes = [W.DWORD, callback_type, W.LPARAM]
user.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
user.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, C.c_int]
user.GetWindowRect.argtypes = [W.HWND, C.POINTER(W.RECT)]
user.PostMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
from sys import path
path.insert(0, str(ROOT / 'src'))
from process_reader import ProcessEntry, entries, process_first, process_next
from windows_patches import ThreadEntry, first, next_thread

with tempfile.TemporaryDirectory(prefix='bl4-desktop-', dir=ROOT / 'build') as temporary:
    folder = Path(temporary)
    target = folder / exe.name
    shutil.copy2(exe, target)
    runtime = folder / 'runtime'
    runtime.mkdir()
    # Tcl cannot traverse the sandbox's redirected user TEMP directory.
    # Keep this test's extraction in its owned workspace directory instead.
    process = subprocess.Popen([str(target)], cwd=folder,
                               env={**os.environ, 'TEMP': str(runtime), 'TMP': str(runtime)})
    hwnd = None
    try:
        deadline = time.monotonic() + 40
        log = folder / 'logs/runtime_patches.jsonl'
        while time.monotonic() < deadline:
            pids = {process.pid}
            pids.update(e.pid for e in entries(2, 0, ProcessEntry, process_first, process_next) if e.parent == process.pid)
            found = []
            @callback_type
            def collect(window, unused):
                pid = W.DWORD()
                user.GetWindowThreadProcessId(window, C.byref(pid))
                title = C.create_unicode_buffer(256)
                user.GetWindowTextW(window, title, len(title))
                if pid.value in pids and title.value == '无主之地 4 修改器':
                    found.append(window)
                return True
            user.EnumWindows(collect, 0)
            for thread in entries(4, 0, ThreadEntry, first, next_thread):
                if thread.pid in pids:
                    user.EnumThreadWindows(thread.tid, collect, 0)
            if found and log.exists():
                hwnd = found[0]
                break
            if process.poll() is not None:
                raise RuntimeError(f'GUI exited early: {process.returncode}')
            time.sleep(0.2)
        assert hwnd, 'Desktop window/startup log missing'
        time.sleep(0.4)
        report = json.loads(log.read_text(encoding='utf-8').splitlines()[-1])
        assert report['requested'] == dict(skill=None, xp=None, backpack_add=0)
        assert report['writes'] == []
        rect = W.RECT()
        assert user.GetWindowRect(hwnd, C.byref(rect))
        width, height = rect.right - rect.left, rect.bottom - rect.top
        screenshot = ROOT / 'research/gui_preview.png'
        declaration = 'using System; using System.Runtime.InteropServices; public class Capture { [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr hwnd, IntPtr dc, uint flags); }'
        command = f"""Add-Type -AssemblyName System.Drawing
Add-Type '{declaration}'
$image = New-Object System.Drawing.Bitmap({width}, {height})
$graphics = [System.Drawing.Graphics]::FromImage($image)
$dc = $graphics.GetHdc()
$ok = [Capture]::PrintWindow([IntPtr]{hwnd}, $dc, 2)
$graphics.ReleaseHdc($dc)
$image.Save('{str(screenshot).replace(chr(39), chr(39)*2)}', [System.Drawing.Imaging.ImageFormat]::Png)
$graphics.Dispose()
$image.Dispose()
if (-not $ok) {{ throw 'PrintWindow failed' }}
"""
        subprocess.run(['powershell', '-NoProfile', '-Command', command],
                       creationflags=0x08000000, check=True, timeout=15)
        user.PostMessageW(hwnd, 0x10, 0, 0)
        assert process.wait(timeout=10) == 0
        evidence = dict(exe=str(exe), size=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                        subsystem='Windows GUI (2)', standalone_launch=True, window_found=True,
                        read_only_startup=report, close_exit_code=0,
                        screenshot=str(screenshot), window_size=[width, height],
                        test_temp='owned workspace directory due to sandbox Tcl TEMP access restriction')
        (ROOT / 'research/gui_build_verification.local.json').write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(evidence, ensure_ascii=False, indent=2))
    finally:
        if process.poll() is None:
            if hwnd:
                user.PostMessageW(hwnd, 0x10, 0, 0)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
