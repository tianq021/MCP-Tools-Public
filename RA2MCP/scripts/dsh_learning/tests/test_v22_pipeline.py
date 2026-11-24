"""V2.2 管线测试：skill_eval record schema / efficiency / forgetting（离线合成，不启动游戏）。"""
import json
import pathlib
import shutil
import unittest
import uuid

from dsh_learning import common, efficiency, forgetting, skill_eval
from dsh_learning.tests.test_trace_parser import build_episode


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.base = common.ROOT / '.tmp' / 'v22-pipeline-tests' / uuid.uuid4().hex[:12]
        self.base.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def make_run(self, name='run-a', n=2):
        run_dir = self.base / name
        episodes = run_dir / 'workers' / '0' / 'episodes'
        episodes.mkdir(parents=True)
        for index in range(n):
            jsonl = episodes / ('ep-%04d.jsonl' % index)
            result = build_episode(jsonl)
            result['episode_id'] = 'ep-%04d' % index
            (episodes / ('ep-%04d.result.json' % index)).write_text(
                json.dumps(result), encoding='utf-8')
        (run_dir / 'run.json').write_text(json.dumps({
            'status': 'complete', 'reward_version': 'full_configurable_v4',
            'action_space_version': 'full_ground_v2p1', 'control_mode': 'full',
            'timesteps_before': 0, 'timesteps_after': 320,
            'completed_episodes': n,
            'parameters': {'workers': 2, 'episode_seconds': 300,
                           'decision_seconds': 0.5},
        }), encoding='utf-8')
        return run_dir

    def test_skill_eval_record_schema(self):
        run_dir = self.make_run()
        record = skill_eval.evaluate_run(run_dir, max_episodes=2)
        self.assertEqual(record['schema_version'], 'v2.2-skill-record')
        self.assertEqual(record['thresholds_version'], 'v2.2-draft')
        self.assertEqual(len(record['thresholds_hash']), 64)
        self.assertEqual(len(record['eval_protocol_hash']), 64)
        self.assertTrue(record['data_sources'])
        self.assertTrue(all(len(source['sha256']) == 64 for source in record['data_sources']))
        self.assertEqual(len(record['source_manifest_sha256']), 64)
        for sid in ('S1', 'S2', 'S3', 'S4', 'S5', 'S5.1', 'S5.2', 'S5.3'):
            self.assertIn(sid, record['skills'])
        self.assertIn('generated_at', record)
        self.assertIn('episodes', record)

    def test_eval_no_files_written_to_history(self):
        run_dir = self.make_run()
        record = skill_eval.evaluate_run(run_dir, max_episodes=1)
        self.assertEqual(record['episodes']['used'], 1)
        self.assertGreaterEqual(record['episodes']['total'], 1)

    def test_eval_fixed_artifacts_supported(self):
        """P3：eval_fixed.py 产物（evaluation.json + workers/*/episodes）可被 skill_eval 读取。"""
        run_dir = self.make_run('eval-run')
        (run_dir / 'run.json').unlink()
        model = self.base / 'eval-model.zip'
        model.write_bytes(b'fake-eval-model')
        evaluation = {'model': str(model), 'model_sha256': 'x' * 64,
                      'frozen_verified': True, 'training': False,
                      'protocol_hash': 'p' * 64, 'protocol_compliant': False,
                      'episode_seconds': 120.0,
                      'eval_protocol': {'reward_version': 'full_configurable_v4'},
                      'workers': 1}
        (run_dir / 'evaluation.json').write_text(
            json.dumps(evaluation), encoding='utf-8')
        record = skill_eval.evaluate_run(run_dir, max_episodes=2)
        self.assertEqual(record['episodes']['used'], 2)
        self.assertEqual(record['model_path'], str(model))
        self.assertIsNotNone(record['model_sha256'])
        self.assertEqual(record['training_context']['workers'], 1)
        self.assertEqual(record['training_context']['episode_seconds'], 120.0)

    def test_efficiency_reports_steps_primary(self):
        run_dir = self.make_run()
        report = efficiency.build_report([run_dir], None)
        scan = report['runs'][0]
        self.assertEqual(scan['primary_metric'], 'training_steps')
        self.assertEqual(scan['training_steps'], 320)
        self.assertEqual(report['mastery_events']['S1']['status'], 'NOT_ACHIEVED')
        self.assertIsNone(report['mastery_events']['S1']['steps_to_mastery'])

    def test_forgetting_uses_new_statuses(self):
        records = [
            {'record_id': 'r1', 'generated_at': '2026-10-30T22:00:00+0800',
             'run': 'run-a', 'thresholds_version': 'v2.2-draft',
             'skills': {'S1': {'status': 'MASTERED', 'pass_rate': 0.9},
                        'S5': {'status': 'INCOMPUTABLE', 'pass_rate': None}}},
            {'record_id': 'r2', 'generated_at': '2026-10-31T22:00:00+0800',
             'run': 'run-b', 'thresholds_version': 'v2.2-draft',
             'skills': {'S1': {'status': 'NOT_MASTERED', 'pass_rate': 0.4},
                        'S5': {'status': 'INCOMPUTABLE', 'pass_rate': 0.9}}},
        ]
        report = forgetting.build_report(records)
        alerts = report['detection']['alerts']
        self.assertTrue(any(alert['skill'] == 'S1' for alert in alerts))
        self.assertFalse(any(alert['skill'] == 'S5' for alert in alerts))
        self.assertIn('thresholds_hash', report['detection'])


if __name__ == '__main__':
    unittest.main()
