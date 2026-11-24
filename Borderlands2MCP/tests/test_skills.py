"""技能修改测试（全部在存档副本上做）。"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bl2toolkit import skills  # noqa: E402
from bl2toolkit.paths import find_steam_id_dir, list_saves  # noqa: E402
from bl2toolkit.saves import SaveFile  # noqa: E402


def _save_for(class_id: str) -> Path | None:
    d = find_steam_id_dir()
    if d is None:
        return None
    for p in list_saves(d):
        try:
            sf = SaveFile(p)
        except Exception:  # noqa: BLE001
            continue
        if skills.resolve_class_id(sf) == class_id:
            return p
    return None


class SkillCatalogTest(unittest.TestCase):
    def test_catalog(self):
        cat = skills.load_catalog()
        self.assertGreater(len(cat), 150)

    def test_all_classes_have_paths(self):
        for cid in skills.CLASS_LABELS:
            lst = skills.class_skills(cid)
            self.assertTrue(lst, f"{cid} 没有技能")
            missing = [s.name for s in lst if not s.path]
            self.assertFalse(missing, f"{cid} 有技能缺内部路径: {missing}")


class SkillEditTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = _save_for("gunzerker")
        if cls.src is None:
            raise unittest.SkipTest("本机没有狂枪手存档")
        cls.tmp = Path(tempfile.mkdtemp(prefix="bl2skill-"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _copy(self) -> Path:
        dst = self.tmp / "skill.sav"
        shutil.copy2(self.src, dst)
        return dst

    def test_set_and_remove_skill(self):
        sf = SaveFile(self._copy())
        before = sf.used_skill_points
        result = skills.set_skill_level(sf, "Locked And Loaded", 5)
        self.assertEqual(result["after"], 5)
        self.assertGreaterEqual(sf.used_skill_points, before)
        path = result["path"]
        levels = skills.current_levels(sf)
        self.assertEqual(levels[path], 5)
        # 设为 0 移除
        skills.set_skill_level(sf, "Locked And Loaded", 0)
        self.assertNotIn(result["path"], skills.current_levels(sf))

    def test_over_max_rejected(self):
        sf = SaveFile(self._copy())
        with self.assertRaises(ValueError):
            skills.set_skill_level(sf, "Locked And Loaded", 9)
        # 放开限制后可以写
        result = skills.set_skill_level(sf, "Locked And Loaded", 9, allow_over_max=True)
        self.assertEqual(result["after"], 9)
        self.assertTrue(result["warnings"])

    def test_tier_check(self):
        sf = SaveFile(self._copy())
        # 先清空技能，然后尝试直接点高层技能
        skills.reset_skills(sf)
        with self.assertRaises(ValueError):
            skills.set_skill_level(sf, "I'm Your Huckleberry", 1, enforce_tier=True)

    def test_fill_tree_and_reset(self):
        sf = SaveFile(self._copy())
        skills.reset_skills(sf)
        result = skills.fill_tree(sf, "Brawn")
        self.assertGreater(result["skills_filled"], 5)
        self.assertGreater(sf.used_skill_points, 0)
        skills.reset_skills(sf)
        self.assertEqual(sf.used_skill_points, 0)

    def test_save_roundtrip_keeps_other_fields(self):
        path = self._copy()
        sf = SaveFile(path)
        before = sf.summary()
        skills.set_skill_level(sf, "Locked And Loaded", 5)
        sf.save(backup=False, force=True)
        after = SaveFile(path).summary()
        for key in ("name", "level", "skill_points", "items"):
            self.assertEqual(after[key], before[key], key)
        self.assertIn("GD_Mercenary_Skills.Gun_Lust.LockedandLoaded", [s["name"] for s in SaveFile(path).list_skills()])

    def test_action_skill_case_insensitive(self):
        src = _save_for("psycho")
        if src is None:
            self.skipTest("本机没有疯子存档")
        dst = self.tmp / "psycho.sav"
        shutil.copy2(src, dst)
        sf = SaveFile(dst)
        result = skills.set_skill_level(sf, "Buzz Axe Rampage", 1, enforce_tier=False)
        self.assertEqual(result["after"], 1)
        self.assertTrue(result["path"].lower().endswith("skill_psycho"))

    def test_internal_skills_not_treated_as_tree(self):
        src = _save_for("psycho")
        if src is None:
            self.skipTest("本机没有疯子存档")
        dst = self.tmp / "psycho2.sav"
        shutil.copy2(src, dst)
        sf = SaveFile(dst)
        summary = skills.skill_summary(sf)
        internal = [x["path"] for x in summary["internal_skills"]]
        self.assertTrue(internal, "疯子存档应有内部辅助技能")
        self.assertTrue(any("firestatusdetector" in p for p in internal))
        # reset 不应删除内部辅助条目
        skills.reset_skills(sf)
        internal_after = [x["path"] for x in skills.skill_summary(sf)["internal_skills"]]
        self.assertEqual(sorted(internal), sorted(internal_after))


if __name__ == "__main__":
    unittest.main()
