from pathlib import Path
import struct
import subprocess
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from reward_hooks import inspect, wrapper, SPECS, entry_bytes, absolute_jump, DATA, plan
from patch_transaction import transact


class HookTests(unittest.TestCase):
    def test_xp_install_switch_restore_transaction(self):
        base, page = 0x140000000, 0x130000000
        rva, original = SPECS['xp']
        class Memory:
            def __init__(self):
                self.data = {base + rva: original,
                             page: wrapper('xp', page, base + rva, original),
                             page + DATA: struct.pack('<Q', 1) + bytes(24),
                             page + DATA + 0x30: bytes(8)}
            def read(self, address, size):
                return self.data[address][:size]
            def write(self, address, data):
                self.data[address] = data + self.data[address][len(data):]
            def protect(self, address, size, value):
                return 0x04
            def flush(self, address, size):
                pass
        memory = Memory()
        with patch('reward_hooks.allocate_wrapper', return_value=page) as allocation:
            for multiplier in (2, 3, 4, 5, 7, 100):
                requests, ranges, _ = plan(memory, memory, base, 'xp', multiplier=multiplier, owner=123)
                self.assertIn((page, page + DATA), ranges)
                transact(memory, requests, {})
                status = inspect(memory, base, 'xp')
                self.assertEqual(status['multiplier'], multiplier)
                self.assertEqual(status['owner'], 123)
            allocation.assert_called_once()
            requests, ranges, _ = plan(memory, memory, base, 'xp', restore=True)
            self.assertIn((page, page + DATA), ranges)
            transact(memory, requests, {})
            self.assertEqual(inspect(memory, base, 'xp'), dict(active=False, multiplier=1))
            self.assertEqual(memory.read(base + rva, len(original)), original)

    def test_real_cpu_wrappers_in_disposable_process(self):
        child = subprocess.run([sys.executable, str(Path(__file__).with_name('reward_machine_child.py'))],
                               capture_output=True, text=True, timeout=20, creationflags=0x08000000)
        self.assertEqual(child.returncode, 0, child.stdout + child.stderr)
        self.assertIn('64 XP boundary cases', child.stdout)

    def test_inspection_rejects_foreign_code_and_bad_parameters(self):
        base, page = 0x140000000, 0x130000000
        site, original = SPECS['xp']
        expected = wrapper('xp', page, base + site, original)
        memory = {base + site: entry_bytes('xp', page), page: expected,
                  page + DATA: struct.pack('<Q', 2) + b'\0' * 24,
                  page + DATA + 0x30: struct.pack('<Q', 12345)}
        class Reader:
            def read(self, address, size):
                return memory[address][:size]
        self.assertEqual(inspect(Reader(), base, 'xp')['multiplier'], 2)
        memory[page] = b'?' + expected[1:]
        with self.assertRaisesRegex(RuntimeError, 'Unknown xp wrapper code'):
            inspect(Reader(), base, 'xp')
        memory[page] = expected
        memory[page + DATA] = struct.pack('<Q', 999) + b'\0' * 24
        with self.assertRaisesRegex(RuntimeError, 'Invalid XP'):
            inspect(Reader(), base, 'xp')

    def test_prologue_relocation_and_entry_lengths(self):
        base, page = 0x140000000, 0x24000000000
        for kind, (site, original) in SPECS.items():
            self.assertEqual(len(entry_bytes(kind, page)), len(original))
            code = wrapper(kind, page, base + site, original)
            source = base + site + len(original) + struct.unpack('<i', original[-4:])[0]
            self.assertIn(b'\x48\xa1' + struct.pack('<Q', source), code)
            self.assertIn(absolute_jump(base + site + len(original)), code)
