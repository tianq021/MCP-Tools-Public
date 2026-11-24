"""Integration test against an owned disposable process, never the game."""
import ctypes as C
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from process_reader import Reader, open_process, close
from patch_transaction import transact
from windows_patches import FrozenThreads, WindowsMemory

CHILD = r'''
import ctypes as C, json, os, sys
k=C.WinDLL('kernel32', use_last_error=True)
k.VirtualAlloc.restype=C.c_void_p
k.VirtualAlloc.argtypes=[C.c_void_p,C.c_size_t,C.c_ulong,C.c_ulong]
p=k.VirtualAlloc(None,4096,0x3000,0x40)
if not p: raise C.WinError(C.get_last_error())
C.memmove(p,b'\xb8\x2a\x00\x00\x00\xc3',6)
fn=C.CFUNCTYPE(C.c_int)(p)
print(json.dumps({'pid':os.getpid(),'address':p}),flush=True)
for line in sys.stdin:
    print(fn(),flush=True)
'''


class WindowsIntegrationTests(unittest.TestCase):
    def test_suspend_write_execute_restore(self):
        child = subprocess.Popen([sys.executable, '-u', '-c', CHILD],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, creationflags=0x08000000)
        try:
            info = json.loads(child.stdout.readline())
            pid, address = info['pid'], info['address']
            original, target = bytes.fromhex('b82a000000c3'), bytes.fromhex('b82b000000c3')
            with Reader(pid) as reader:
                handle = open_process(0x100438, False, pid)
                self.assertTrue(handle)
                try:
                    backend = WindowsMemory(handle, reader)
                    for data, expected in ((target, 43), (original, 42)):
                        report = {}
                        with FrozenThreads(pid, [(address, address + 6)], report):
                            transact(backend, [(address, {original, target}, data)], report)
                        self.assertEqual(report['resume_errors'], [])
                        child.stdin.write('run\n')
                        child.stdin.flush()
                        self.assertEqual(int(child.stdout.readline()), expected)
                    # Reject an instruction-position conflict and still resume.
                    with self.assertRaisesRegex(RuntimeError, 'inside patch range'):
                        with FrozenThreads(pid, [(0, 2**64)], {}):
                            self.fail('Must not enter')
                    child.stdin.write('run\n')
                    child.stdin.flush()
                    self.assertEqual(int(child.stdout.readline()), 42)
                finally:
                    close(handle)
        finally:
            child.kill()
            child.communicate(timeout=5)

    def test_suspend_failure_resumes_acquired_threads(self):
        report = {}
        with patch('windows_patches.FrozenThreads.tids', return_value={1, 2}), \
             patch('windows_patches.open_thread', side_effect=[100, 101]), \
             patch('windows_patches.owner', return_value=999999), \
             patch('windows_patches.suspend', side_effect=[0, 0xFFFFFFFF]), \
             patch('windows_patches.resume', return_value=1) as resume, \
             patch('windows_patches.close'):
            with self.assertRaises(RuntimeError):
                with FrozenThreads(999999, [], report):
                    self.fail('Must not enter')
            resume.assert_called_once_with(100)
