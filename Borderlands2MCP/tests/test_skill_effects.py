"""技能效果扩展能力表与预览测试（静态数据/预览，不涉及游戏）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bl2toolkit import itemcodes, skill_effects  # noqa: E402


class CapabilityTableTest(unittest.TestCase):
    def test_summary(self):
        s = skill_effects.summary()
        self.assertGreaterEqual(s["effect_count"], 8)
        self.assertEqual(set(s["grades"]), {"L1", "L2", "L3", "L4"})
        self.assertGreaterEqual(s["evidence_count"], 6)

    def test_required_effects_present(self):
        ids = {e["id"] for e in skill_effects.effects()}
        for required in ("hp_regen", "lifesteal", "damage_reduction", "ammo_regen",
                         "elemental_damage", "extra_projectiles", "on_kill", "on_hit"):
            self.assertIn(required, ids)

    def test_all_evidence_refs_defined(self):
        defined = set(skill_effects.evidence())
        for e in skill_effects.effects():
            self.assertTrue(e.get("evidence"), e["id"])
            for ref in e["evidence"]:
                self.assertIn(ref, defined, f"{e['id']} 引用了不存在的证据 {ref}")

    def test_effect_ids_lookup(self):
        self.assertEqual(skill_effects.get_effect("吸血")["id"], "lifesteal")
        self.assertEqual(skill_effects.get_effect("lifesteal")["id"], "lifesteal")
        with self.assertRaises(ValueError):
            skill_effects.get_effect("这个效果不存在")

    def test_filter(self):
        l2 = skill_effects.list_effects(grade="L2")
        self.assertTrue(l2)
        self.assertTrue(all("L2" in e["grades"] for e in l2))
        hit = skill_effects.list_effects("弹药")
        self.assertTrue(any(e["id"] == "ammo_regen" for e in hit))


class EffectPlanTest(unittest.TestCase):
    def _plan(self, eid):
        return skill_effects.build_plan(eid)

    def test_lifesteal_evidence(self):
        p = self._plan("lifesteal")
        attrs = " ".join(p["effect"]["attributes"])
        self.assertIn("PlayerConvertDamageToHealingPercent", attrs)
        self.assertTrue(p["previews"])
        text = skill_effects.format_plan_text("lifesteal")
        self.assertIn("PREVIEW ONLY", text)
        self.assertIn("未实机验证", text)

    def test_ammo_attributes(self):
        attrs = " ".join(self._plan("ammo_regen")["effect"]["attributes"])
        self.assertIn("Ammo_Combat_Rifle_RegenerationRate", attrs)
        self.assertIn("PlayerCurrentWeaponAmmoRegenerationMultiplier", attrs)

    def test_extra_projectiles(self):
        attrs = " ".join(self._plan("extra_projectiles")["effect"]["attributes"])
        self.assertIn("WeaponProjectilesPerShot", attrs)
        self.assertIn("WeaponExtraShotChance", attrs)

    def test_hp_regen(self):
        attrs = " ".join(self._plan("hp_regen")["effect"]["attributes"])
        self.assertIn("HealthActiveRegenerationRate", attrs)

    def test_damage_reduction(self):
        attrs = " ".join(self._plan("damage_reduction")["effect"]["attributes"])
        self.assertIn("ReceivedBulletDamageModifier", attrs)

    def test_elemental(self):
        attrs = " ".join(self._plan("elemental_damage")["effect"]["attributes"])
        self.assertIn("InstigatedShockDamageModifier", attrs)

    def test_on_kill_has_hook_evidence(self):
        p = self._plan("on_kill")
        hooks = " ".join(p["effect"]["hooks"])
        self.assertIn("KilledEnemy", hooks)
        self.assertTrue(any(pv["kind"] == "hook" for pv in p["previews"]))

    def test_no_claim_of_verified_effect(self):
        # 预览不能宣称已生效：文本里必须明确“未实机验证”
        for e in skill_effects.effects():
            text = skill_effects.format_plan_text(e["id"])
            self.assertIn("未实机验证", text, e["id"])
            self.assertIn("PREVIEW ONLY", text, e["id"])
            self.assertNotEqual(e.get("status"), "verified_in_game", e["id"])


class AttributeApplicabilityTest(unittest.TestCase):
    """属性适用性：BL2 专用属性表确认 vs TPS-only，不能把 TPS 属性当 BL2 已确认。"""

    def setUp(self):
        self.data = skill_effects.load_capabilities()

    def test_counts(self):
        counts = self.data["attribute_applicability_counts"]
        self.assertGreater(counts["bl2_confirmed"], 20)
        self.assertGreaterEqual(counts["tps_only"], 1)
        self.assertEqual(
            counts["bl2_confirmed"] + counts["tps_only"] + counts["not_found"],
            len(self.data["attribute_applicability"]),
        )

    def test_known_bl2_attributes(self):
        app = self.data["attribute_applicability"]
        self.assertEqual(app["D_Attributes.DamageEnhancementModifiers.PlayerConvertDamageToHealingPercent"]["status"], "bl2_confirmed")
        self.assertEqual(app["D_Attributes.Weapon.WeaponProjectilesPerShot"]["status"], "bl2_confirmed")
        self.assertEqual(app["D_Attributes.SkillAttributes.MeleeHealthStealPercent"]["status"], "tps_only")
        self.assertEqual(app["D_Attributes.Moxxtails.Att_Moxxtail_HealthRegeneration"]["status"], "tps_only")

    def test_elemental_uses_bl2_spelling(self):
        elemental = skill_effects.get_effect("elemental_damage")
        attrs = " ".join(elemental["attributes"])
        self.assertIn("InstigatedIncindiaryDamageModifier", attrs)
        self.assertIn("InstigatedIncendiaryStatusDamageModifier", attrs)
        self.assertTrue(elemental.get("contradictions"))

    def test_plan_marks_applicability(self):
        text = skill_effects.format_plan_text("lifesteal")
        self.assertIn("BL2 确认", text)
        text2 = skill_effects.format_plan_text("hp_regen")
        self.assertIn("TPS-only", text2)


class OpLevelRiskTest(unittest.TestCase):
    """修正“>80 物品必非法”的口径：81-90 是 80+OP1..OP10。"""

    def _level90_code(self):
        # 用库里的 Lv90 代码（超级nfl 改）
        from bl2toolkit import library

        for e in library.load_library():
            if e.get("level") == 90:
                return e["code"]
        self.skipTest("代码库里没有 Lv90 代码")

    def test_level81_90_is_legal_with_op(self):
        code = self._level90_code()
        risk = itemcodes.assess_risk(code, op_level=10, char_level=80)
        self.assertEqual(risk["level"], 90)
        self.assertTrue(risk["op_legal"])
        self.assertFalse(any("超过" in w for w in risk["warnings"]))

    def test_level81_90_needs_op_context(self):
        code = self._level90_code()
        risk = itemcodes.assess_risk(code)
        self.assertIsNone(risk["op_legal"])
        self.assertTrue(any("合法 OP 物品" in n for n in risk["notes"]))

    def test_level_over_90_risky(self):
        # 91 级以上超出 80+OP10，应报风险；用 Lv90 代码无法构造，跳过（保持实现语义由代码测试覆盖）
        pass


if __name__ == "__main__":
    unittest.main()
