"""Parser boundary tests without a game process."""
import struct
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import quest_state as q
from research_state import ResearchReadError


class Memory:
    base = 0

    def __init__(self, result=-1, kind=1, expires=q.NO_TIME, mismatch=False, empty=False):
        self.parts = {}
        self.parts[q.QUEST_SYSTEM_RVA] = struct.pack('<Q', 0x1000)
        self.parts[0x2B8F800] = struct.pack('<Q', 0x2000)
        self.parts[0x20B0] = struct.pack('<q', 150)
        self.parts[0x11C0] = struct.pack('<QI', 0x3000, 0 if empty else 1)
        entry = bytearray(0x90)
        struct.pack_into('<I', entry, 0, 7)
        struct.pack_into('<Q', entry, 0x10, 0x4000)
        struct.pack_into('<i', entry, 0x18, result)
        struct.pack_into('<qqq', entry, 0x60, 100, expires, q.NO_TIME)
        struct.pack_into('<QI', entry, 0x78, 0x5000, 1)
        self.parts[0x3000] = entry
        config = bytearray(0xE0)
        struct.pack_into('<Q', config, 0, 0x9000)
        struct.pack_into('<QI', config, 0x30, 0x6000, 1)
        struct.pack_into('<Q', config, 0xA0, 0x9000)
        struct.pack_into('<i', config, 0xD8, kind)
        self.parts[0x4000] = config
        self.parts[0x5000] = struct.pack('<16siiiBB2x', b'A'*16, 0, 2, 5, 0, 1)
        self.parts[0x6000] = struct.pack('<Q', 0x7000)
        objective = bytearray(0x70)
        objective[0x60:0x70] = b'B'*16 if mismatch else b'A'*16
        struct.pack_into('<Q', objective, 0x50, 0x9000)
        self.parts[0x7000] = objective
        self.parts[0x9000] = b'label\0'+bytes(4096)

    def read(self, address, size):
        for start, data in self.parts.items():
            if start <= address and address+size <= start+len(data):
                return bytes(data[address-start:address-start+size])
        raise ResearchReadError('unmapped fixture memory')

    def pointer(self, address):
        return struct.unpack('<Q', self.read(address, 8))[0]


class QuestStateTests(unittest.TestCase):
    def setUp(self):
        types = patch.object(q, 'type_name', side_effect=lambda r,p:
            '.?AVFrostpunkQuestSystem@@' if p == 0x1000 else '.?AVFrostpunkCustomQuestObjective@@')
        types.start()
        self.addCleanup(types.stop)

    def snapshot(self, **kwargs):
        return q.get_tasks(SimpleNamespace(reader=Memory(**kwargs)))

    def test_inactive_objective_and_remaining_time(self):
        task = self.snapshot(expires=200)['tasks'][0]
        self.assertEqual(task['remaining_time_raw'], '50')
        self.assertEqual(task['objectives'][0]['status'], 'inactive')
        self.assertEqual(task['objectives'][0]['progress'], 2)

    def test_no_deadline(self):
        task = self.snapshot()['tasks'][0]
        self.assertIsNone(task['expires_at_raw'])
        self.assertIsNone(task['remaining_time_raw'])

    def test_hidden_and_finished_filter(self):
        self.assertEqual(self.snapshot(kind=9)['tasks'], [])
        self.assertEqual(self.snapshot(result=0)['tasks'], [])
        full = q.get_tasks(SimpleNamespace(reader=Memory(result=1, kind=9)), True, True)
        self.assertEqual(full['tasks'][0]['status'], 'failure')

    def test_guid_mismatch(self):
        with self.assertRaisesRegex(ResearchReadError, 'GUID'):
            self.snapshot(mismatch=True)

    def test_unknown_result(self):
        with self.assertRaisesRegex(ResearchReadError, 'result enum'):
            self.snapshot(result=5)

    def test_empty_container(self):
        self.assertEqual(self.snapshot(empty=True)['count'], 0)


if __name__ == '__main__':
    unittest.main()
