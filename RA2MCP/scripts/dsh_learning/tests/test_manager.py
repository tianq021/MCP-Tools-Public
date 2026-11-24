"""manager V2.2 决策与安全测试（不启动训练）。"""
import unittest

from dsh_learning import manager


def skills(statuses, rates=None):
    rates = rates or {}
    order = ('S1', 'S2', 'S3', 'S4', 'S5')
    return {sid: {'status': statuses.get(sid, 'NOT_MASTERED'),
                  'pass_rate': rates.get(sid, 0.0)} for sid in order}


def record(statuses, rates=None, protocol='protocol-a', version='v2.2-draft',
           hash_='hash-a', generated='2026-10-30T22:00:00+0800', record_id='r1'):
    return {'record_id': record_id, 'generated_at': generated,
            'thresholds_version': version, 'thresholds_hash': hash_,
            'eval_protocol_hash': protocol, 'run': 'run-' + record_id,
            'skills': skills(statuses, rates)}


class ManagerTests(unittest.TestCase):
    def build(self, records):
        return manager.LearningManager({'runs': records})

    def test_no_records_is_incomparable(self):
        proposal = self.build([]).propose_curriculum()
        self.assertEqual(proposal['status'], 'INCOMPARABLE')
        self.assertFalse(proposal['launch_performed'])

    def test_mixed_protocols_rejected(self):
        records = [record({}, protocol='p1'), record({}, protocol='p2', record_id='r2')]
        proposal = self.build(records).propose_curriculum()
        self.assertEqual(proposal['status'], 'INCOMPARABLE')
        self.assertIn('eval_protocol_hash_mixed', proposal['rationale'])

    def test_s1_first_when_all_not_mastered(self):
        proposal = self.build([record({})]).propose_curriculum()
        self.assertEqual(proposal['status'], 'OK')
        self.assertEqual(proposal['course'], {'type': 'LEARN', 'target_skill': 'S1'})
        self.assertFalse(proposal['launch_performed'])
        self.assertTrue(proposal['requires_user_approval'])
        self.assertFalse(proposal['manager_started_training'])

    def test_s2_after_s1_mastered(self):
        proposal = self.build([record({'S1': 'MASTERED'})]).propose_curriculum()
        self.assertEqual(proposal['course'], {'type': 'LEARN', 'target_skill': 'S2'})

    def test_review_on_regression(self):
        records = [record({'S1': 'MASTERED'}, record_id='r1',
                          generated='2026-10-30T22:00:00+0800'),
                   record({'S1': 'NOT_MASTERED'}, record_id='r2',
                          generated='2026-10-31T22:00:00+0800')]
        proposal = self.build(records).propose_curriculum()
        self.assertEqual(proposal['course'], {'type': 'REVIEW', 'target_skill': 'S1'})

    def test_expand_evaluation_on_undetermined(self):
        proposal = self.build([record({'S1': 'UNDETERMINED'})]).propose_curriculum()
        self.assertEqual(proposal['course']['type'], 'EXPAND_EVALUATION')
        self.assertEqual(proposal['course']['target_skill'], 'S1')

    def test_experiment_required_when_all_mastered(self):
        statuses = {'S1': 'MASTERED', 'S2': 'MASTERED', 'S3': 'MASTERED',
                    'S4': 'MASTERED', 'S5': 'INCOMPUTABLE'}
        proposal = self.build([record(statuses)]).propose_curriculum()
        self.assertEqual(proposal['course']['type'], 'EXPERIMENT_REQUIRED')

    def test_best_model_pointer(self):
        records = [record({'S1': 'MASTERED'}, rates={'S1': 1.0}, record_id='r1')]
        mgr = self.build(records)
        compatibility = {'results': [
            {'id': 'R1', 'path': 'reports/rl/r1/model.zip', 'read_only_eval': True},
            {'id': 'R3', 'path': 'reports/rl/r3/model.zip', 'read_only_eval': True}]}
        best = mgr.select_best_model(compatibility, {'R1': 'run-r1'})
        self.assertEqual(best['id'], 'R1')
        self.assertFalse(mgr.rollback_plan()['auto_execute'])

    def test_manager_source_has_no_launch_calls(self):
        source = open(manager.__file__, encoding='utf-8').read()
        for forbidden in ('subprocess', 'Popen', 'os.system', 'train_shared',
                          'start_shared_training'):
            self.assertNotIn(forbidden, source)


if __name__ == '__main__':
    unittest.main()
