"""XP 加成离线计算测试（不写真实 XP/等级/存档）。"""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bl2toolkit import xp  # noqa: E402


class XpFormulaTest(unittest.TestCase):
    def test_bonus_floor(self):
        self.assertEqual(xp.bonus(1000, 50), 500)
        self.assertEqual(xp.bonus(999, 33), math.floor(999 * 33 / 100))
        self.assertEqual(xp.bonus(1, 0.5), 0)
        self.assertEqual(xp.bonus(0, 100), 0)
        self.assertEqual(xp.bonus(1000, 0), 0)

    def test_total_formula(self):
        r = xp.calculate(1000, 50)
        self.assertEqual(r["base_xp"], 1000)
        self.assertEqual(r["bonus"], 500)
        self.assertEqual(r["total"], 1500)
        self.assertEqual(r["applies"], True)
        self.assertIsNone(r["level_gain"])

    def test_small_percent_live_case(self):
        # 与主 MCP 新进程实测一致：10000 × 0.29% -> bonus 29, total 10029
        r = xp.calculate(10000, 0.29)
        self.assertEqual(r["bonus"], 29)
        self.assertEqual(r["total"], 10029)

    def test_negative_and_bad_inputs(self):
        for base in (-1, -100):
            with self.assertRaises(ValueError):
                xp.calculate(base, 50)
        for pct in (-0.1, -50, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                xp.calculate(1000, pct)
        with self.assertRaises(ValueError):
            xp.calculate(1000.5, 50)
        with self.assertRaises(ValueError):
            xp.calculate(True, 50)
        with self.assertRaises(ValueError):
            xp.calculate(1000, "50")

    def test_large_values(self):
        r = xp.calculate(2_000_000_000, 150)
        self.assertEqual(r["bonus"], 3_000_000_000)
        self.assertEqual(r["total"], 5_000_000_000)
        self.assertGreaterEqual(r["bonus"], 0)

    def test_percent_above_1000_warns(self):
        r = xp.calculate(100, 2000)
        self.assertTrue(any("非常大" in n for n in r["notes"]))
        self.assertEqual(r["bonus"], 2000)


class XpCapTest(unittest.TestCase):
    def test_at_level_cap_defaults_no_apply(self):
        r = xp.calculate(1000, 50, current_level=80, level_cap=80)
        self.assertTrue(r["at_cap"])
        self.assertFalse(r["applies"])
        self.assertEqual(r["bonus"], 500)  # 仍可离线看到计算值
        self.assertEqual(r["total"], 1500)

    def test_at_cap_apply_flag(self):
        r = xp.calculate(1000, 50, current_level=80, level_cap=80, apply_at_cap=True)
        self.assertTrue(r["at_cap"])
        self.assertTrue(r["applies"])

    def test_below_cap_applies(self):
        r = xp.calculate(1000, 50, current_level=79, level_cap=80)
        self.assertFalse(r["at_cap"])
        self.assertTrue(r["applies"])

    def test_bad_cap_inputs(self):
        with self.assertRaises(ValueError):
            xp.calculate(100, 50, current_level=-1)
        with self.assertRaises(ValueError):
            xp.calculate(100, 50, level_cap=0)


class XpTableAndCapabilitiesTest(unittest.TestCase):
    def test_table(self):
        rows = xp.table([100, 500, 1000], 50)
        self.assertEqual([r["total"] for r in rows], [150, 750, 1500])

    def test_capabilities_evidence(self):
        c = xp.capabilities()
        self.assertIn("formula", c)
        hooks = " ".join(h["hook"] for h in c["runtime_hooks"])
        self.assertIn("WillowPlayerController.ExpEarn", hooks)
        self.assertIn("WillowGameInfo.AwardCombatExperience", hooks)
        self.assertIn("NotifyKilledEnemy", hooks)
        exp = c["runtime_hooks"][0]
        self.assertIn("observed_calls", exp)
        args = {call["args"] for call in exp["observed_calls"]}
        self.assertEqual(args, {2, 3})
        self.assertIn("未通过反射确认", exp["signature_status"])
        self.assertIn("可选参数", exp["signature_status"])
        self.assertNotIn("args_old_sdk", exp)
        self.assertTrue(c["verification_steps"])
        self.assertGreaterEqual(len(c["verification_steps"]), 5)
        self.assertIn("TotalExpPoints", c["base_semantics"]["not_confirmed"][0])
        self.assertIn("Exp Adjuster", c["mods"]["exp_adjuster"]["name"])
        self.assertIn("保持禁用", c["mods"]["exp_adjuster"]["warning"])
        self.assertEqual(c["coverage"]["challenges"]["status"], "未知")
        self.assertIn("mission.GetExperienceReward", c["coverage"]["missions"]["evidence"])
        self.assertIn("DoInjectedCallNext", c["recursion_guards"][1])
        self.assertEqual(c["level_cap"]["character_max_level"], 80)

    def test_preview_is_preview_only(self):
        p = xp.preview(1000, 50)
        self.assertIn("PREVIEW ONLY", p["text"])
        self.assertIn("本预览不写任何文件", p["text"])
        self.assertEqual(p["calculation"]["bonus"], 500)
        self.assertIn("TotalExpPoints", p["text"])
        self.assertIn("重入", p["text"])
        self.assertIn("可选", p["text"])


if __name__ == "__main__":
    unittest.main()
