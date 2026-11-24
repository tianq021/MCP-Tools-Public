"""离线部件解析/查找/组合预览测试（不读写存档、不启动游戏）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bl2toolkit import library, parts  # noqa: E402


class PartsResolveTest(unittest.TestCase):
    def _harold(self):
        for e in library.load_library():
            if e.get("name") == "哈罗德":
                return e
        self.skipTest("本地代码库没有哈罗德")

    def test_data_files_loaded(self):
        s = parts.stats()
        self.assertGreaterEqual(s["weapon_part_defs"], 800)
        self.assertGreaterEqual(s["item_part_defs"], 500)
        self.assertGreaterEqual(s["weapon_type_defs"], 30)
        self.assertIn(0, s["asset_library_sets"])

    def test_decode_harold(self):
        d = parts.decode_resolved(self._harold()["code"])
        self.assertIn("WeaponType_Torgue_Pistol", d["resolved"]["type"]["path"])
        self.assertIn("Pistol_Torgue_5_Calla", d["resolved"]["balance"]["path"])
        self.assertIn("Torgue", d["resolved"]["manufacturer"]["path"])
        slots = {p["slot"] for p in d["resolved"]["parts"] if p["slot"]}
        for slot in ("BodyPartDefinition", "GripPartDefinition", "BarrelPartDefinition", "SightPartDefinition"):
            self.assertIn(slot, slots)
        # 全 1 空槽必须解析成 None，不能当成真实部件
        self.assertTrue(all(p["path"] is None for p in d["resolved"]["parts"] if p["raw"] and p["raw"]["lib"] == 63))

    def test_all_library_codes_resolve_base_fields(self):
        bad = []
        for e in library.load_library():
            try:
                d = parts.decode_resolved(e["code"])
            except Exception as exc:  # noqa: BLE001
                bad.append((e.get("name"), "decode", str(exc)))
                continue
            for k in ("type", "balance", "manufacturer"):
                if not d["resolved"].get(k):
                    bad.append((e.get("name"), k, "未解析"))
            real_parts = [p for p in d["resolved"]["parts"] if p["path"]]
            if not real_parts and e.get("is_weapon"):
                bad.append((e.get("name"), "parts", "无部件"))
        self.assertEqual(bad[:5], [], f"有 {len(bad)} 条解析失败")
        self.assertEqual(bad, [])

    def test_slot_mapping(self):
        slots = parts.slot_table()
        self.assertEqual(slots["weapon"]["Barrel"], "BarrelPartDefinition")
        self.assertEqual(slots["item"]["Alpha"], "AlphaItemPartDefinition")
        self.assertEqual(parts.SLOT_ALIASES["barrel"], "Barrel")
        self.assertEqual(parts.SLOT_ALIASES["alphaitempartdefinition"], "Alpha")

    def test_search(self):
        rows = parts.search_parts("Barrel_Torgue", "weapon", 20)
        self.assertTrue(rows)
        self.assertTrue(any(r["type"] == "Barrel" for r in rows))
        self.assertTrue(all("ref" in r for r in rows))


class CombinePreviewTest(unittest.TestCase):
    def _harold(self):
        for e in library.load_library():
            if e.get("name") == "哈罗德":
                return e
        self.skipTest("本地代码库没有哈罗德")

    def _other_barrel(self):
        rows = parts.search_parts("Pistol_Barrel", "weapon", 200)
        for r in rows:
            if r["type"] == "Barrel" and "Torgue_Calla" not in r["path"] and r.get("set") == 0:
                return r
        self.skipTest("找不到另一个 base set 手枪枪管")

    def test_preview_replace_barrel(self):
        code = self._harold()["code"]
        barrel = self._other_barrel()
        res = parts.preview(code, {"Barrel": barrel["path"]})
        self.assertEqual(res["errors"], [])
        self.assertNotEqual(res["new_code"], code)
        self.assertEqual(len(res["changes"]), 1)
        after = parts.decode_resolved(res["new_code"])
        self.assertEqual(after["resolved"]["parts"][2]["path"], barrel["path"])
        self.assertIn("PREVIEW ONLY", res["note"])

    def test_preview_wrong_slot_is_error(self):
        code = self._harold()["code"]
        barrel = self._other_barrel()
        res = parts.preview(code, {"Elemental": barrel["path"]})
        self.assertTrue(res["errors"])
        self.assertEqual(res["new_code"], code)

    def test_preview_missing_slot_skipped(self):
        code = self._harold()["code"]
        barrel = self._other_barrel()
        res = parts.preview(code, {"Stock": barrel["path"]})
        # 哈罗德没有 Stock 槽，但类型是 Barrel，会先报类型不匹配
        self.assertTrue(res["errors"] or res["warnings"])
        self.assertEqual(res["new_code"], code)

    def test_preview_allow_append(self):
        code = self._harold()["code"]
        # 用一个 base set 的元素部件追加到本来没有 Elemental 槽的手枪
        elem = [r for r in parts.search_parts("Elemental", "weapon", 100) if r["type"] == "Elemental" and r.get("set") == 0]
        if not elem:
            self.skipTest("没有 base set 元素部件样例")
        res = parts.preview(code, {"Elemental": elem[0]["path"]}, allow_append=True)
        self.assertFalse(res["errors"])
        self.assertIn("allow-append", " ".join(res["warnings"]))
        self.assertEqual(res["changes"][0]["slot"], "Elemental")


class ItemCombinePreviewTest(unittest.TestCase):
    def _shield(self):
        import json

        data = json.loads((ROOT / "data" / "special_weapons.json").read_text(encoding="utf-8"))
        for e in data:
            if e.get("kind") == "护盾" and e.get("code"):
                return e
        self.skipTest("特殊表里没有护盾")

    def test_shield_preview_with_item_part(self):
        shield = self._shield()
        base = parts.decode_resolved(shield["code"])
        alpha = [p for p in base["resolved"]["parts"] if p["type"] == "Alpha"]
        if not alpha:
            self.skipTest("该护盾没有 Alpha 部件")
        # 找一个同类别的其他物品部件（不保证一定合法，只测预览路径）
        candidate = None
        for row in parts.search_parts("GD_Shields.Body", "item", 50):
            if row["type"] == "Alpha":
                candidate = row
                break
        if not candidate:
            self.skipTest("找不到同类物品部件")
        res = parts.preview(shield["code"], {"Alpha": candidate["path"]})
        self.assertEqual(res["errors"], [])
        self.assertTrue(res["changes"])
        after = parts.decode_resolved(res["new_code"])
        stats = {p["type"]: p["path"] for p in after["resolved"]["parts"] if p["type"]}
        self.assertEqual(stats.get("Alpha"), candidate["path"])


class CoverageTest(unittest.TestCase):
    def test_coverage_numbers(self):
        c = parts.coverage_report()
        self.assertEqual(c["code_library"]["total"], len(library.load_library()))
        self.assertGreaterEqual(c["special_table"]["total"], 300)
        self.assertIn("gibbed_totals", c)
        self.assertGreaterEqual(c["gibbed_totals"]["item_balance_defs"], 1000)
        self.assertTrue(c["gaps"])
        # 明确提醒条目数不是全游戏总数
        self.assertTrue(any("不是全游戏总数" in g for g in c["gaps"]))


if __name__ == "__main__":
    unittest.main()
