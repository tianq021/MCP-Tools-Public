import struct
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'src'),
                str(Path(__file__).resolve().parents[1] / 'tools')]
from reflection import Reflection
import apply_runtime_patches as cli


class ScanTests(unittest.TestCase):
    def test_shared_page_is_read_once_and_not_reused_across_scans(self):
        data = bytearray(4096)
        calls = []
        struct.pack_into('<iiQ', data, 8, 0, 1, 123)
        struct.pack_into('<iiQ', data, 72, 16, 2, 456)
        def read(address, size):
            calls.append((address, size))
            return bytes(data[address - 4096:address - 4096 + size])
        r = object.__new__(Reflection)
        r.p = SimpleNamespace(read=read)
        r.objects_iter = lambda: iter([(1, 4096), (2, 4160)])
        self.assertEqual(list(r.object_headers()), [(1, 4096, 0, 123), (2, 4160, 16, 456)])
        self.assertEqual(calls, [(4096, 4096)])
        struct.pack_into('<Q', data, 16, 789)
        self.assertEqual(list(r.object_headers())[0][-1], 789)
        self.assertEqual(len(calls), 2)

    def test_unreadable_pages_fall_back_and_stale_objects_are_skipped(self):
        calls = []
        def read(address, size):
            calls.append((address, size))
            if size == 4096 or address == 4200:
                raise OSError('unreadable')
            return struct.pack('<iiQ', 0, 1, 999)
        r = object.__new__(Reflection)
        r.p = SimpleNamespace(read=read)
        r.objects_iter = lambda: iter([(1, 4096), (2, 4192), (3, 8180)])
        self.assertEqual(list(r.object_headers()), [(1, 4096, 0, 999), (3, 8180, 0, 999)])
        self.assertEqual(calls.count((4096, 4096)), 1)
        self.assertIn((8188, 16), calls)

    def test_version_cache_invalidates_on_file_or_process_change(self):
        cli._verified_files.clear()
        reader = SimpleNamespace(pid=10, created=20, require_alive=lambda: None)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'game.exe'
            path.write_bytes(b'a')
            with patch.object(cli, 'sha256', return_value=cli.VERIFIED_SHA256) as digest:
                cli.verified_hash(path, reader)
                cli.verified_hash(path, reader)
                self.assertEqual(digest.call_count, 1)
                reader.created = 21
                cli.verified_hash(path, reader)
                self.assertEqual(digest.call_count, 2)
                path.write_bytes(b'changed size')
                cli.verified_hash(path, reader)
                self.assertEqual(digest.call_count, 3)
        cli._verified_files.clear()

    def test_wrong_hash_is_never_cached(self):
        cli._verified_files.clear()
        reader = SimpleNamespace(pid=10, created=20, require_alive=lambda: None)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'game.exe'
            path.write_bytes(b'a')
            with patch.object(cli, 'sha256', return_value='wrong') as digest:
                for _ in range(2):
                    with self.assertRaisesRegex(RuntimeError, 'Unsupported executable'):
                        cli.verified_hash(path, reader)
                self.assertEqual(digest.call_count, 2)
        self.assertFalse(cli._verified_files)

    def test_file_change_during_hash_is_rejected(self):
        cli._verified_files.clear()
        reader = SimpleNamespace(pid=10, created=20, require_alive=lambda: None)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'game.exe'
            path.write_bytes(b'a')
            def digest(_):
                path.write_bytes(b'changed')
                return cli.VERIFIED_SHA256
            with patch.object(cli, 'sha256', side_effect=digest):
                with self.assertRaisesRegex(RuntimeError, 'changed during'):
                    cli.verified_hash(path, reader)
        self.assertFalse(cli._verified_files)
