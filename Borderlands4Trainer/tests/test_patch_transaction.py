import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from patch_transaction import transact


class Memory:
    def __init__(self, fault=None):
        self.data = {4096: b'ABC', 8192: b'DEF'}
        self.protection = {4096: 0x20, 8192: 0x20}
        self.fault, self.fired = fault, False

    def fail(self, name):
        if name == self.fault and not self.fired:
            self.fired = True
            raise RuntimeError(name)

    def read(self, address, size):
        if self.data[address] == b'XYZ':
            self.fail('readback')
        return self.data[address]

    def protect(self, address, size, value):
        if value == 0x20:
            self.fail('restore_protection')
        old = self.protection[address]
        self.protection[address] = value
        return old

    def write(self, address, data):
        self.data[address] = data[:1] + self.data[address][1:]
        self.fail('partial')
        if address == 8192:
            self.fail('second')
        self.data[address] = data

    def flush(self, address, size):
        self.fail('flush')


REQUESTS = [(4096, {b'ABC', b'XYZ'}, b'XYZ'), (8192, {b'DEF', b'UVW'}, b'UVW')]


class TransactionTests(unittest.TestCase):
    def test_success(self):
        memory, report = Memory(), {}
        transact(memory, REQUESTS, report)
        self.assertEqual(memory.data, {4096: b'XYZ', 8192: b'UVW'})
        self.assertEqual(report['transaction'], 'committed')
        self.assertEqual(set(memory.protection.values()), {0x20})

    def test_batch_preflight(self):
        memory, report = Memory(), {}
        memory.data[8192] = b'???'
        with self.assertRaises(RuntimeError):
            transact(memory, REQUESTS, report)
        self.assertEqual(memory.data[4096], b'ABC')
        self.assertEqual(report['writes'], [])

    def test_failures_restore_all_attempted_bytes(self):
        for fault in ('partial', 'second', 'flush', 'readback', 'restore_protection'):
            with self.subTest(fault=fault):
                memory, report = Memory(fault), {}
                with self.assertRaises(RuntimeError):
                    transact(memory, REQUESTS, report)
                self.assertEqual(memory.data, {4096: b'ABC', 8192: b'DEF'})
                self.assertEqual(set(memory.protection.values()), {0x20})
                self.assertTrue(all(x['restored'] for x in report['rollback']))

    def test_rollback_failure_visible(self):
        memory, report = Memory('flush'), {}
        original_write = memory.write
        def write(address, data):
            if data == b'ABC':
                raise RuntimeError('rollback denied')
            original_write(address, data)
        memory.write = write
        with self.assertRaises(RuntimeError):
            transact(memory, REQUESTS, report)
        self.assertFalse(report['rollback'][0]['restored'])

    def test_already_applied_is_noop(self):
        memory, report = Memory(), {}
        memory.data[4096] = b'XYZ'
        transact(memory, REQUESTS[:1], report)
        self.assertEqual(report['writes'], [])

    def test_persistent_protection_failure_is_reported(self):
        memory, report = Memory(), {}
        protect = memory.protect
        def deny_restore(address, size, value):
            if value == 0x20:
                raise RuntimeError('protection denied')
            return protect(address, size, value)
        memory.protect = deny_restore
        with self.assertRaisesRegex(RuntimeError, 'protection restoration failed'):
            transact(memory, REQUESTS, report)
        self.assertEqual(memory.data, {4096: b'ABC', 8192: b'DEF'})
        self.assertTrue(report['protection_errors'])
        self.assertEqual(report['transaction'], 'failed')
