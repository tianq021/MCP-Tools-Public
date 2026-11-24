"""V1 自动化测试：模型注册表（轻量扫描，不加载真实模型）。"""
import json
import pathlib
import tempfile
import unittest

from dsh_learning import common, model_registry


class RegistryTests(unittest.TestCase):
    def test_file_info_and_sha(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / 'm.bin'
            path.write_bytes(b'abc')
            info = common.file_info(path)
            self.assertTrue(info['exists'])
            self.assertEqual(info['size'], 3)
            self.assertEqual(info['sha256'], common.sha256_text('abc'))
            self.assertFalse(common.file_info(pathlib.Path(tmp) / 'none.bin')['exists'])

    def test_discover_run_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = pathlib.Path(tmp) / 'somerun'
            run_dir.mkdir()
            common.save_json_atomic(run_dir / 'run.json', {
                'status': 'stopped_by_request', 'control_mode': 'full',
                'reward_version': 'full_configurable_v4',
                'action_space_version': 'full_ground_v2p1',
                'timesteps_before': 0, 'timesteps_after': 100,
                'parameters': {'workers': 8, 'episode_seconds': 300},
                'source_sha256': {'env.py': 'x'},
            })
            common.save_json_atomic(run_dir / 'action-contract.json', {
                'version': 'full_ground_v2', 'action_space_version': 'full_ground_v2p1',
                'action_n': 301, 'catalog_sha256': 'c' * 64,
            })
            meta = model_registry.discover_run_metadata(run_dir / 'model.zip')
            self.assertEqual(meta['run_json']['reward_version'], 'full_configurable_v4')
            self.assertEqual(meta['run_json']['action_space_version'], 'full_ground_v2p1')
            self.assertEqual(meta['run_parameters']['workers'], 8)
            self.assertEqual(meta['action_contract']['action_n'], 301)

    def test_registry_assets_present_without_deep_inspection(self):
        registry = model_registry.build_registry(deep=False, include_checkpoints=False,
                                                 extra_discovery=False)
        ids = {a['id'] for a in registry['assets']}
        for expected in ('R1', 'R2', 'R3', 'OLD_REFERENCE', 'RUN_203156',
                         'RUN_210239_CHECKPOINT', 'RUN_210239_EMERGENCY',
                         'BC_ACTION_MODEL'):
            self.assertIn(expected, ids)
        self.assertEqual(registry['contract']['action_n'], 301)
        for asset in registry['assets']:
            self.assertIn('file', asset)
            self.assertNotIn('inspection', asset)  # 非 deep 模式不加载模型
        r1 = next(a for a in registry['assets'] if a['id'] == 'R1')
        self.assertTrue(r1['file']['exists'])
        self.assertEqual(len(r1['file']['sha256']), 64)


if __name__ == '__main__':
    unittest.main()
