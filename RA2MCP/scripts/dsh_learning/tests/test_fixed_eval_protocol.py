"""P3 固定评测协议/计划测试（离线，不启动游戏）。"""
import json
import pathlib
import shutil
import unittest
import uuid
import zipfile

from dsh_learning import common, fixed_eval_protocol


class FixedEvalProtocolTests(unittest.TestCase):
    def setUp(self):
        self.base = common.ROOT / '.tmp' / 'fixed-eval-tests' / uuid.uuid4().hex[:12]
        self.base.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def fake_model(self, name='model.zip', payload=b'fake-ppo-bytes'):
        path = self.base / name
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('policy.pth', payload)
            archive.writestr('data', b'{}')
        return path

    def test_protocol_hash_deterministic_and_complete(self):
        self.assertEqual(len(fixed_eval_protocol.PROTOCOL_HASH), 64)
        self.assertEqual(fixed_eval_protocol.protocol_hash_of(),
                         fixed_eval_protocol.PROTOCOL_HASH)
        config = fixed_eval_protocol.EVAL_CONFIG
        for key in ('map', 'difficulty', 'credits', 'units', 'episode_seconds',
                    'spawn_strategy', 'frozen_assertion', 'thresholds_version',
                    'thresholds_hash', 'record_schema'):
            self.assertIn(key, config)
        self.assertEqual(config['frozen_assertion'], 'optimizer_updates == 0')

    def test_plan_is_planned_not_executed_and_sha_from_file(self):
        model = self.fake_model()
        plan = fixed_eval_protocol.build_plan([model], n_episodes=20)
        self.assertEqual(plan['status'], 'PLANNED_NOT_EXECUTED')
        self.assertFalse(plan['launch_performed'])
        self.assertTrue(plan['approval_required'])
        self.assertIn('D3', plan['approval_gate'])
        self.assertEqual(plan['models'][0]['model']['sha256'],
                         fixed_eval_protocol.validate_model(model)['sha256'])
        corners = plan['requirements']['corner_plan']
        self.assertEqual(sum(item['min_episodes'] for item in corners), 20)
        self.assertIn('不是评测结果', plan['requirements']['not_a_result'])
        # 计划 JSON 可序列化且不包含任何“已执行”字段
        json.dumps(plan, ensure_ascii=False)
        self.assertNotIn('executed_at', plan)

    def test_model_sha_changes_with_file_content(self):
        model = self.fake_model()
        first = fixed_eval_protocol.validate_model(model)['sha256']
        self.fake_model(payload=b'tampered')
        second = fixed_eval_protocol.validate_model(model)['sha256']
        self.assertNotEqual(first, second)

    def test_requires_at_least_20_episodes(self):
        model = self.fake_model()
        with self.assertRaises(ValueError):
            fixed_eval_protocol.build_plan([model], n_episodes=10)

    def test_missing_or_invalid_model_rejected(self):
        with self.assertRaises(ValueError):
            fixed_eval_protocol.build_plan([self.base / 'nope.zip'])
        bad = self.base / 'bad.zip'
        bad.write_bytes(b'not-a-zip')
        with self.assertRaises(ValueError):
            fixed_eval_protocol.build_plan([bad])


if __name__ == '__main__':
    unittest.main()
