from __future__ import annotations

import ctypes
import os
from pathlib import Path
import tempfile
import unittest

import memory_scan
import runtime_features


class MemoryScanTests(unittest.TestCase):
    def test_find_aligned_i32_uses_absolute_alignment(self) -> None:
        value = 0x12345678
        data = bytearray(32)
        data[4:8] = value.to_bytes(4, "little", signed=True)
        data[9:13] = value.to_bytes(4, "little", signed=True)
        self.assertEqual(
            memory_scan.find_aligned_i32(bytes(data), 0x1000, value),
            [0x1004],
        )

    def test_snapshot_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            memory_scan.save_snapshot(
                directory,
                pid=123,
                process_path=r"C:\Games\Borderlands3.exe",
                process_sha256="ABCD",
                value=500,
                candidates=[0x1000, 0x2000],
            )
            metadata, candidates = memory_scan.load_snapshot(directory)
            self.assertEqual(metadata["candidate_count"], 2)
            self.assertEqual(metadata["value"], 500)
            self.assertEqual(candidates, [0x1000, 0x2000])

    def test_wildcard_aob(self) -> None:
        data = bytes.fromhex("90 48 8B 01 75 03 03 41 18 90")
        matches = memory_scan.find_aob_in_buffer(
            data, 0x5000, "48 8B ?? 75 03 03 41 18"
        )
        self.assertEqual(matches, [0x5001])

    def test_relative_jump(self) -> None:
        jump = memory_scan.relative_jump(0x1000, 0x2000)
        self.assertEqual(jump[0], 0xE9)
        displacement = int.from_bytes(jump[1:], "little", signed=True)
        self.assertEqual(0x1000 + 5 + displacement, 0x2000)

    def test_capture_stubs_replay_original_bytes(self) -> None:
        remote = 0x10000000
        hook = 0x10010000
        original = bytes.fromhex("8B 80 D8 01 00 00")
        stub, pointer = runtime_features.build_capture_stub(
            "rax", remote, hook, original
        )
        self.assertEqual(pointer, remote + 0x40)
        self.assertIn(original, stub[:0x40])
        self.assertEqual(len(stub), 0x48)

    @unittest.skipUnless(os.name == "nt", "Windows only")
    def test_checked_write_on_own_process(self) -> None:
        value = ctypes.c_int32(13579)
        address = ctypes.addressof(value)
        with memory_scan.ProcessMemory(os.getpid(), writable=True) as process:
            self.assertEqual(process.read_i32(address), 13579)
            self.assertEqual(process.write_i32(address, 24680, 13579), 24680)
            self.assertEqual(value.value, 24680)
            process.write_i32(address, 13579, 24680)
        self.assertEqual(value.value, 13579)


if __name__ == "__main__":
    unittest.main()
