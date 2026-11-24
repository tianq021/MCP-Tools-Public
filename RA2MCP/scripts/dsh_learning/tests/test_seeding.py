"""P4/W1 随机种子工具测试（不启动训练）。"""
import random
import unittest

from dsh_learning import seeding


class SeedingTests(unittest.TestCase):
    def test_same_seed_reproducible(self):
        seeding.seed_everything(123)
        first = [random.random() for _ in range(3)]
        seeding.seed_everything(123)
        second = [random.random() for _ in range(3)]
        self.assertEqual(first, second)

    def test_different_seed_differs(self):
        seeding.seed_everything(1)
        first = [random.random() for _ in range(5)]
        seeding.seed_everything(2)
        second = [random.random() for _ in range(5)]
        self.assertNotEqual(first, second)

    def test_numpy_seeded_when_available(self):
        result = seeding.seed_everything(7)
        self.assertEqual(result['seed'], 7)
        self.assertIn('random', result['applied'])
        try:
            import numpy as np
        except Exception:  # noqa: BLE001
            return
        self.assertIn('numpy', result['applied'])
        seeding.seed_everything(7)
        a = np.random.rand(3).tolist()
        seeding.seed_everything(7)
        b = np.random.rand(3).tolist()
        self.assertEqual(a, b)

    def test_none_seed_is_noop(self):
        result = seeding.seed_everything(None)
        self.assertIsNone(result['seed'])
        self.assertEqual(result['applied'], [])


if __name__ == '__main__':
    unittest.main()
