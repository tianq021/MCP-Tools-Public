"""右删失统计测试（合成数据，仅测试统计函数；不涉及真实实验结论）。"""
import unittest

from dsh_learning import survival_stats


class KaplanMeierTests(unittest.TestCase):
    def test_all_events_km_and_median(self):
        records = [{'seed': 1, 'duration': 1, 'event': True},
                   {'seed': 2, 'duration': 2, 'event': True},
                   {'seed': 3, 'duration': 3, 'event': True},
                   {'seed': 4, 'duration': 4, 'event': True}]
        km = survival_stats.kaplan_meier(records)
        self.assertEqual(km['survival'], [0.75, 0.5, 0.25, 0.0])
        self.assertEqual(survival_stats.median_survival(records), 2)
        self.assertAlmostEqual(survival_stats.survival_at(km, 1.5), 0.75)
        self.assertAlmostEqual(survival_stats.rmst(records, 4), 2.5)

    def test_censored_sample_kept_and_median_handling(self):
        records = [{'seed': 1, 'duration': 2, 'event': True},
                   {'seed': 2, 'duration': 4, 'event': False},
                   {'seed': 3, 'duration': 6, 'event': True}]
        summary = survival_stats.summarize_arm(records)
        self.assertEqual(summary['n_seeds'], 3)
        self.assertEqual(summary['n_events'], 2)
        self.assertEqual(summary['n_censored'], 1)
        # 删失样本没有被删除；KM 在 t=4 不下降
        km = summary['km']
        index = km['times'].index(4)
        self.assertEqual(km['events'][index], 0)
        self.assertAlmostEqual(km['survival'][index], km['survival'][index - 1])
        self.assertGreater(survival_stats.rmst(records, 6), 0)

    def test_empty_sample_rejected(self):
        with self.assertRaises(ValueError):
            survival_stats.kaplan_meier([])
        with self.assertRaises(ValueError):
            survival_stats.rmst([], 100)


class LogrankTests(unittest.TestCase):
    def test_identical_groups_not_significant(self):
        group = [{'seed': 1, 'duration': 10, 'event': True},
                 {'seed': 2, 'duration': 20, 'event': True}]
        result = survival_stats.logrank(group, list(group))
        self.assertGreater(result['p_value'], 0.9)

    def test_very_different_groups_significant(self):
        fast = [{'seed': i, 'duration': 10, 'event': True} for i in range(1, 6)]
        slow = [{'seed': i, 'duration': 500, 'event': False} for i in range(1, 6)]
        result = survival_stats.logrank(fast, slow)
        self.assertLess(result['p_value'], 0.05)


class BootstrapTests(unittest.TestCase):
    def test_bootstrap_deterministic_with_seed(self):
        a = [{'seed': i, 'duration': 100 + i, 'event': True} for i in range(1, 5)]
        b = [{'seed': i, 'duration': 200 + i, 'event': True} for i in range(1, 5)]
        first = survival_stats.bootstrap_stat_ci(
            a, b, survival_stats.median_survival, n_boot=200, seed=42)
        second = survival_stats.bootstrap_stat_ci(
            a, b, survival_stats.median_survival, n_boot=200, seed=42)
        self.assertEqual(first, second)
        self.assertLess(first['ci_high'], 0)

    def test_compare_arms_median_not_identifiable(self):
        a = [{'seed': 1, 'duration': 100, 'event': False}]
        b = [{'seed': 2, 'duration': 100, 'event': False}]
        result = survival_stats.compare_arms(a, b, tau=100, stat='median')
        self.assertEqual(result['stat'], 'median')
        self.assertIsNone(result['diff'])
        self.assertIn('median_not_identifiable', result['note'])

    def test_compare_arms_rmst(self):
        a = [{'seed': i, 'duration': 100, 'event': True} for i in range(1, 4)]
        b = [{'seed': i, 'duration': 300, 'event': True} for i in range(1, 4)]
        result = survival_stats.compare_arms(a, b, tau=100, stat='rmst')
        self.assertEqual(result['stat'], 'rmst')
        self.assertIsNotNone(result['bootstrap']['ci_low'])


if __name__ == '__main__':
    unittest.main()
