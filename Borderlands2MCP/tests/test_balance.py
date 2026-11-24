"""数值平衡模型测试（只读/预览，不写存档、不启动游戏）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bl2toolkit import balance  # noqa: E402


class BalanceModelTest(unittest.TestCase):
    def test_models_and_sources(self):
        data = balance.load_models()
        self.assertIn("scaling_adjuster", data["sources"])
        self.assertEqual(data["universal_scaler"]["object"], "GD_Balance_HealthAndDamage.HealthAndDamage.Att_UniversalBalanceScaler:ConstantAttributeValueResolver_0")
        self.assertIn("BADASS", "BADASS")  # noop readability
        self.assertTrue(data["badass"]["factors"]["gun_damage"] > 1)

    def test_scaler_curve_geometry(self):
        rows = balance.curve([50, 55, 60], 1.13)
        self.assertAlmostEqual(rows[0]["relative"], 1.0, places=9)
        self.assertAlmostEqual(rows[2]["relative"] / rows[1]["relative"], 1.13 ** 5, places=9)
        lower = balance.curve([80], 1.08)[0]["relative"]
        ref = balance.curve([80], 1.13)[0]["relative"]
        self.assertLess(lower, ref)

    def test_compare_scenarios_and_op(self):
        data = balance.compare([55, 60, 72, 80], include_op=True)
        by = {(r["level"], r["op"]): r for r in data["rows"]}
        ref55 = by[(55, 0)]["scenarios"]["reference_ucp"]["enemy_health_index"]
        self.assertAlmostEqual(ref55, 1.0, places=9)
        ref80 = by[(80, 0)]["scenarios"]["reference_ucp"]["enemy_health_index"]
        nif80 = by[(80, 0)]["scenarios"]["nifsara_regular"]["enemy_health_index"]
        low80 = by[(80, 0)]["scenarios"]["scaling_1.08"]["enemy_health_index"]
        self.assertLess(nif80, ref80)
        self.assertLess(low80, ref80)
        self.assertGreater(by[(80, 10)]["scenarios"]["reference_ucp"]["enemy_health_index"], by[(80, 0)]["scenarios"]["reference_ucp"]["enemy_health_index"])
        self.assertNotIn("badass", by[(55, 0)])

    def test_nifsara_no_regen_and_slag_removed(self):
        data = balance.compare([80], include_op=False)
        sc = data["rows"][0]["scenarios"]
        self.assertEqual(sc["nifsara_regular"]["enemy_health_regen"], 0.0)
        self.assertEqual(sc["nifsara_regular"]["slag_amp"], 0.0)
        self.assertGreater(sc["reference_ucp"]["slag_amp"], 1)

    def test_badass_independent_and_simplified_warning(self):
        b = balance.badass_report()
        self.assertAlmostEqual(b["factors"]["gun_damage"], 1.282, places=3)
        self.assertAlmostEqual(b["factors"]["fire_rate"], 1.257, places=3)
        self.assertAlmostEqual(b["simplified_two_factor_product"], 1.612, places=3)
        self.assertIn("不是实际 DPS", b["simplified_note"])
        self.assertIn("独立因子", b["note"])
        self.assertIn("1.282*1.257", b["sample"])
        # 账号因子不能出现在等级曲线 compare 结果里
        data = balance.compare([55, 60], include_op=False)
        self.assertNotIn("badass", data["rows"][0])

    def test_equipment_delta(self):
        rows = {r["delta"]: r["relative"] for r in balance.equipment_delta(const=1.13)}
        self.assertAlmostEqual(rows[0], 1.0)
        self.assertGreater(rows[10], 1)
        self.assertLess(rows[-10], 1)

    def test_op_table(self):
        rows = balance.op_table(1.13)
        self.assertEqual([r["op"] for r in rows], [0, 5, 10])
        self.assertGreater(rows[2]["scaler_relative_vs_80"], rows[1]["scaler_relative_vs_80"])
        self.assertGreater(rows[0]["scaler_relative_vs_80"], 0)

    def test_preview_is_preview_only(self):
        p = balance.preview(1.08)
        self.assertIn("PREVIEW ONLY", p["text"])
        self.assertIn("ConstantValue 1.080000", p["text"])
        self.assertIn("GD_Balance_HealthAndDamage.HealthAndDamage.Att_UniversalBalanceScaler", p["objects"]["universal_scaler"])
        self.assertIn("没有应用到运行中游戏", p["note"])

    def test_55_60_72_80_table_shape(self):
        data = balance.compare([55, 60, 72, 80], include_op=False)
        self.assertEqual([r["level"] for r in data["rows"]], [55, 60, 72, 80])
        for r in data["rows"]:
            for sid in ("reference_ucp", "scaling_1.08", "nifsara_regular", "nifsara_hard"):
                self.assertIn(sid, r["scenarios"])


if __name__ == "__main__":
    unittest.main()
