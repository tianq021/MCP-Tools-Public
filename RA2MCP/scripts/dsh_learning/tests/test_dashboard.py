"""D6/P7 dashboard 生成测试：真实记录渲染、N/A、INCOMPARABLE、无模拟数据、无外链。"""
import json
import pathlib
import shutil
import unittest
import uuid

from dsh_learning import common, dashboard


def record(record_id='r1', generated='2026-10-30T22:00:00+0800',
           protocol='p1', version='v2.2-draft', hash_='h1', skills=None):
    default_skills = {sid: {'status': 'NOT_MASTERED', 'pass_rate': 0.0,
                            'wilson_low': 0.0, 'wilson_high': 0.2, 'n_episodes': 5}
                      for sid in ('S1', 'S2', 'S3', 'S4')}
    default_skills.update({'S5': {'status': 'INCOMPUTABLE', 'pass_rate': None,
                                  'wilson_low': None, 'wilson_high': None,
                                  'n_episodes': 0},
                           'S5.1': {'status': 'INCOMPUTABLE', 'pass_rate': None},
                           'S5.2': {'status': 'PROVISIONAL', 'pass_rate': None},
                           'S5.3': {'status': 'INCOMPUTABLE', 'pass_rate': None}})
    return {'schema_version': 'v2.2-skill-record', 'record_id': record_id,
            'generated_at': generated, 'thresholds_version': version,
            'thresholds_hash': hash_, 'eval_protocol_hash': protocol,
            'run': 'run-' + record_id,
            'data_sources': [{'path': 'reports/rl/x/ep.jsonl', 'sha256': 'a' * 64}],
            'skills': skills or default_skills}


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.base = common.ROOT / '.tmp' / 'dashboard-tests' / uuid.uuid4().hex[:12]
        self.base.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def write(self, name, payload):
        path = self.base / name
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        return str(path)

    def test_renders_real_status_and_na_without_fake_data(self):
        skill_payload = self.write('skill.json', {'runs': [record()]})
        efficiency = self.write('eff.json', {
            'runs': [{'run': 'run-r1', 'workers': 8, 'training_steps': 1234,
                      'steps_per_hour': 30000.0, 'wall_seconds_observed': 148.0,
                      'status': 'complete'}],
            'mastery_events': {'S1': {'status': 'NOT_ACHIEVED', 'steps_to_mastery': None},
                               'S2': {'status': 'NOT_ACHIEVED', 'steps_to_mastery': None},
                               'S3': {'status': 'NOT_ACHIEVED', 'steps_to_mastery': None},
                               'S4': {'status': 'NOT_ACHIEVED', 'steps_to_mastery': None},
                               'S5': {'status': 'NOT_ACHIEVED', 'steps_to_mastery': None}}})
        payload = dashboard.build_dashboard([skill_payload], efficiency)
        self.assertTrue(payload['source_status']['consistency']['ok'])
        self.assertEqual(payload['panels']['skills']['S1']['status'], 'NOT_MASTERED')
        self.assertEqual(payload['panels']['skills']['S5']['status'], 'INCOMPUTABLE')
        self.assertIsNone(payload['panels']['learning']['skills']['S1']['steps_to_mastery'])
        self.assertEqual(payload['panels']['learning']['skills']['S1']['na_reason'],
                         'not_mastered_or_no_mastery_evidence')
        self.assertIsNone(payload['panels']['transfer']['ratio'])
        self.assertEqual(payload['panels']['transfer']['status'], 'NOT_MEASURED')
        html = dashboard.render_html(payload)
        self.assertIn('NOT_MASTERED', html)
        self.assertIn('N/A', html)
        self.assertIn('训练步数仅作上下文', html)
        self.assertNotIn('http://', html)
        self.assertNotIn('https://', html)
        # 训练步数只出现在上下文表格，不进入技能状态
        for sid in ('S1', 'S2', 'S3', 'S4'):
            self.assertNotEqual(payload['panels']['skills'][sid]['status'], 'MASTERED')

    def test_mixed_protocol_is_incomparable(self):
        payload = dashboard.build_dashboard([
            self.write('a.json', {'runs': [record('r1', protocol='p1')]}),
            self.write('b.json', {'runs': [record('r2', protocol='p2',
                                                   generated='2026-10-31T22:00:00+0800')]}),
        ])
        self.assertFalse(payload['source_status']['consistency']['ok'])
        self.assertEqual(payload['panels']['skills']['S1']['status'], 'INCOMPARABLE')
        self.assertTrue(payload['source_status']['warnings'])

    def test_legacy_schema_rejected_not_rendered(self):
        legacy = self.write('legacy.json', {'runs': [{'run': 'old', 'skills': {}}]})
        payload = dashboard.build_dashboard([legacy])
        self.assertEqual(payload['source_status']['records_used'], 0)
        self.assertTrue(any('legacy' in w for w in payload['source_status']['warnings']))
        self.assertEqual(payload['panels']['skills']['S1']['status'], 'UNKNOWN')

    def test_missing_sources_render_na(self):
        payload = dashboard.build_dashboard([], str(self.base / 'missing.json'))
        self.assertEqual(payload['panels']['learning']['skills'], {})
        self.assertEqual(payload['panels']['best_model']['status'], 'NOT_MEASURED')
        self.assertEqual(payload['panels']['current_target']['status'], 'NOT_MEASURED')
        html = dashboard.render_html(payload)
        self.assertIn('N/A', html)

    def test_allowed_state_enum(self):
        payload = dashboard.build_dashboard([self.write('skill.json', {'runs': [record()]})])
        for sid, block in payload['panels']['skills'].items():
            self.assertIn(block['status'],
                          set(dashboard.STATE_ENUM) | {'INCOMPARABLE'}, sid)


if __name__ == '__main__':
    unittest.main()
