"""Exercise the actual add/read/clear transaction with simulated process memory."""
from contextlib import ExitStack
import struct
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import task_extension as t


class TaskBuildTests(unittest.TestCase):
    def exercise(self, build, fail_write=False, custom=False):
        layout = t.TASK_PROFILES[build]
        base, dll = 0x180000000, 0x200000000
        klass, statics, campaign, delegate = 0x10000, 0x20000, 0x30000, 0x40000
        state, hook = dll+0x1000, dll+0x2000
        memory = {}
        def write(handle, address, raw):
            for i, byte in enumerate(raw):
                memory[address+i] = byte
        def read(handle, address, size):
            return bytes(memory.get(address+i, 0) for i in range(size))
        for address, value in [(base+layout['campaign'], klass), (klass+0xB8, statics),
                               (statics, campaign), (campaign+0x80, delegate),
                               (delegate+0x18, base+layout['invoke']),
                               (delegate+0x10, base+layout['original'])]:
            write(1, address, struct.pack('<Q', value))
        write(1, base+layout['point'], b'\x41\xb9\x06\0\0\0')
        write(1, state, t.STATE.pack(t.MAGIC, 0, 0, 0, 0, 0, *([0]*10), 0, 0, 0))
        original = dict(memory)
        failed = False
        def checked_write(handle, address, raw):
            nonlocal failed
            if fail_write and address == delegate+0x10 and not failed:
                failed = True
                raise OSError('simulated entry write failure')
            write(handle, address, raw)
        with ExitStack() as stack:
            config = Path(stack.enter_context(TemporaryDirectory())) / 'levels.json'
            options = {'ambition': 15, 'danger': 2, 'modifiers': list(t.added_levels.FALLBACK_TRAITS)} if custom else None
            stack.enter_context(patch.object(t.added_levels, 'session_file', return_value=config if custom else None))
            patches = [
                (t.mem, 'find_process', dict(return_value=123)),
                (t.game_build, 'verified', dict(return_value=dict(base=base, build=build))),
                (t.k, 'OpenProcess', dict(return_value=1)),
                (t.k, 'CloseHandle', dict(return_value=True)),
                (t.mem, 'read_memory', dict(side_effect=read)),
                (t.mem, 'write_memory', dict(side_effect=checked_write)),
                (t, 'write_code', dict(side_effect=write)),
                (t, 'extension_module', dict(return_value=(dll, t.dll_path()))),
                (t, 'exports', dict(return_value=dict(TaskState=0x1000, TaskLevels=0x2000))),
                (t.nt, 'NtSuspendProcess', dict(return_value=0)),
                (t.nt, 'NtResumeProcess', dict(return_value=0)),
                (t, 'plan_seeds', dict(return_value=[23456])),
                (t.Path, 'mkdir', dict(return_value=None)),
                (t.session_log, 'operation_dir', dict(return_value=config.parent)),
            ]
            for obj, name, kwargs in patches:
                stack.enter_context(patch.object(obj, name, **kwargs))
            if fail_write:
                with self.assertRaises(OSError):
                    t.operate('add', options=options)
                self.assertEqual(memory, original)
                self.assertEqual(t.added_levels.read_json(config, {}), {})
                return
            result = t.operate('add', options=options)
            self.assertEqual((result['build'], result['count']), (int(build), 1))
            self.assertEqual(t.pointer(1, delegate+0x10), hook)
            self.assertEqual(read(1, base+layout['point'], 6), b'\x41\xb9\x07\0\0\0')
            if custom:
                self.assertEqual(t.added_levels.read_json(config), {'23456': options})
            self.assertTrue(t.operate('read')['active'])
            result = t.operate('clear')
            self.assertEqual(result['count'], 0)
            self.assertEqual(t.added_levels.read_json(config, {}), {})
            self.assertEqual(t.pointer(1, delegate+0x10), base+layout['original'])
            self.assertEqual(read(1, base+layout['point'], 6), b'\x41\xb9\x06\0\0\0')

    def test_add_read_clear_both_builds(self):
        for build in t.TASK_PROFILES:
            with self.subTest(build=build):
                self.exercise(build)

    def test_failed_write_rolls_back_both_builds(self):
        for build in t.TASK_PROFILES:
            with self.subTest(build=build):
                self.exercise(build, fail_write=True)

    def test_custom_settings_are_committed_and_cleared_with_the_level(self):
        self.exercise('26049821', custom=True)

    def test_failed_add_rolls_back_custom_settings(self):
        self.exercise('26049821', fail_write=True, custom=True)
