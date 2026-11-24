import ctypes
import os
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from value_scan import scan, refine, validate_identity
from process_reader import Reader


class FakeReader:
    def __init__(self, data):
        self.data = data

    def require_alive(self):
        pass

    def regions(self):
        yield 0x1000, len(self.data)

    def read(self, address, size):
        return self.data[address - 0x1000:address - 0x1000 + size]


class ScannerTests(unittest.TestCase):
    def test_cross_chunk_64bit_value(self):
        reader = FakeReader(b'\x00' * 12 + struct.pack('<q', 199749873) + b'\x00' * 16)
        result = scan(reader, 199749873, 'i64', chunk_size=16)
        self.assertEqual(result['addresses'], [0x100C])
        self.assertTrue(result['complete'])

    def test_limit_is_not_reported_as_complete(self):
        result = scan(FakeReader(struct.pack('<iii', 7, 7, 7)), 7, max_candidates=2)
        self.assertEqual(len(result['addresses']), 2)
        self.assertFalse(result['complete'])
        self.assertEqual(result['stop_reason'], 'candidate_limit')

    def test_unreadable_memory_is_reported(self):
        class FailedReader(FakeReader):
            def read(self, address, size):
                raise OSError('page disappeared')
        result = scan(FailedReader(b'\x00' * 16), 7)
        self.assertFalse(result['complete'])
        self.assertEqual(result['skipped_bytes'], 16)

    def test_refine_discards_unchanged_copy(self):
        reader = FakeReader(struct.pack('<iii', 7, 8, 7))
        previous = scan(reader, 7)
        reader.data = struct.pack('<iii', 7, 8, 10)
        self.assertEqual(refine(reader, previous, 10)['addresses'], [0x1008])

    def test_reused_pid_rejected(self):
        with self.assertRaises(ValueError):
            validate_identity({'pid': 1, 'created': 1}, {'pid': 1, 'created': 2})

    def test_actual_read_of_own_process(self):
        value = ctypes.c_int32(199749873)
        with Reader(os.getpid(), sys.executable) as reader:
            reader.require_alive()
            self.assertEqual(reader.read(ctypes.addressof(value), 4), struct.pack('<i', value.value))
            self.assertGreater(reader.created, 0)

    def test_wrong_executable_rejected(self):
        with self.assertRaises(ValueError):
            Reader(os.getpid(), 'C:\\wrong\\Borderlands4.exe')


if __name__ == '__main__':
    unittest.main()
