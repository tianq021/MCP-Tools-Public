"""V1 自动化测试：共享工具与当前契约读取。"""
import json
import pathlib
import tempfile
import unittest

from dsh_learning import common


class CommonTests(unittest.TestCase):
    def test_sha256_text_and_file_consistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / 'a.txt'
            path.write_bytes(b'ra2mcp-v1')
            self.assertEqual(common.sha256_file(path),
                             common.sha256_text('ra2mcp-v1'))
            self.assertEqual(len(common.sha256_file(path)), 64)

    def test_save_json_atomic_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / 'nested' / 'data.json'
            common.save_json_atomic(path, {'ok': True, 'n': 3})
            self.assertTrue(path.exists())
            self.assertEqual(common.load_json(path), {'ok': True, 'n': 3})
            self.assertEqual(common.load_json(pathlib.Path(tmp) / 'missing.json'), None)

    def test_current_contract_has_core_fields(self):
        contract = common.current_contract()
        self.assertEqual(contract['action_space_version'], 'full_ground_v2p1')
        self.assertEqual(contract['action_n'], 301)
        self.assertEqual(contract['obs_dim'], 1801)
        self.assertEqual(contract['reward_version'], 'full_configurable_v4')
        self.assertEqual(contract['unit_slots'], 8)
        self.assertEqual(contract['target_slots'], 8)
        self.assertEqual(len(contract['catalog_sha256'] or ''), 64)
        self.assertIn('full_spec', contract['source'])


if __name__ == '__main__':
    unittest.main()
