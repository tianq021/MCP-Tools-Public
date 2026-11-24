"""实验 A 编排/分析测试（离线；不启动训练）。"""
import unittest

from dsh_learning import experiment_a


class ExperimentAPlanTests(unittest.TestCase):
    def test_plan_structure_and_not_executed(self):
        plan = experiment_a.build_plan()
        self.assertEqual(plan['schema'], 'v2.2-experiment-a')
        self.assertEqual(plan['status'], 'PLANNED_NOT_EXECUTED')
        self.assertFalse(plan['training_executed'])
        self.assertFalse(plan['game_executed'])
        self.assertTrue(plan['approval_required'])
        self.assertIn('D4', plan['approval_gate'])
        self.assertEqual(plan['design']['budget_steps'], 15000)
        self.assertEqual(plan['design']['seeds'], [11, 22, 33])
        self.assertEqual(plan['design']['evidence_level'], 'EXPLORATORY')
        self.assertEqual(plan['censoring_rule_id'], 'v2.2-right-censored-at-budget')
        self.assertEqual(plan['seed_unit_id'], 'v2.2-seed-level-primary-inference')
        self.assertIn('不是训练结果', plan['not_a_result'])
        arm_ids = [arm['arm'] for arm in plan['arms']]
        self.assertEqual(arm_ids, ['A', "A'"])
        self.assertEqual(sum(len(arm['runs']) for arm in plan['arms']), 6)
        for arm in plan['arms']:
            for run in arm['runs']:
                self.assertEqual(run['status'], 'NOT_STARTED')

    def test_formal_plan_uses_five_seeds(self):
        plan = experiment_a.build_plan(formal=True)
        self.assertEqual(len(plan['design']['seeds']), 5)
        self.assertEqual(plan['design']['evidence_level'], 'CONFIRMATORY')
        self.assertEqual(sum(len(arm['runs']) for arm in plan['arms']), 10)

    def test_empty_analysis_is_not_measured(self):
        analysis = experiment_a.analyze_arms([], [])
        self.assertEqual(analysis['status'], 'NOT_MEASURED')
        self.assertEqual(analysis['na_reason'], 'experiment_not_executed')

    def test_synthetic_analysis_flagged(self):
        a = [{'seed': 1, 'duration': 5000, 'event': True},
             {'seed': 2, 'duration': 6000, 'event': True}]
        b = [{'seed': 3, 'duration': 12000, 'event': True},
             {'seed': 4, 'duration': 15000, 'event': False}]
        analysis = experiment_a.analyze_arms(a, b, mark_synthetic=True)
        self.assertEqual(analysis['status'], 'ANALYZED')
        self.assertTrue(analysis['synthetic_input'])
        self.assertIn('logrank', analysis)

    def test_no_launch_code_in_module(self):
        source = open(experiment_a.__file__, encoding='utf-8').read()
        for forbidden in ('subprocess', 'Popen', 'train_shared', 'start_shared_training'):
            self.assertNotIn(forbidden, source)


if __name__ == '__main__':
    unittest.main()
