"""特殊武器总表测试。

运行：python -m unittest discover -s tests -v
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bl2toolkit import itemcodes, specials  # noqa: E402
from bl2toolkit.paths import find_steam_id_dir, list_saves  # noqa: E402
from bl2toolkit.saves import SaveFile  # noqa: E402


def _any_save() -> Path | None:
    d = find_steam_id_dir()
    saves = list_saves(d) if d else []
    return saves[0] if saves else None


class SpecialsCatalogTest(unittest.TestCase):
    def test_catalog_loads(self):
        items = specials.load_catalog()
        self.assertGreater(len(items), 300, "特殊武器表条目太少，先运行 scripts/build_special_weapons.py")

    def test_all_codes_decode(self):
        for item in specials.load_catalog():
            itemcodes.decode(item["code"])

    def test_rarity_groups(self):
        stats = specials.stats()["by_rarity"]
        for key in ("传说", "珠光", "彩色", "炽天使", "E科技"):
            self.assertIn(key, stats, f"缺少稀有度分组 {key}")
            self.assertGreater(stats[key], 0)

    def test_deep_red_alias_maps_to_etech(self):
        # 用户口中的"深红色"按品红/E科技处理
        self.assertEqual(specials.normalize_rarity("深红色"), ["E科技"])
        rows = specials.search(rarity="深红色")
        self.assertTrue(rows)
        self.assertTrue(all(r["rarity"] == "E科技" for r in rows))

    def test_search_known_legendary(self):
        rows = specials.search("哈罗德", rarity="传说", kind="武器")
        self.assertTrue(rows)
        self.assertEqual(rows[0]["name"], "不洁哈罗德")
        self.assertEqual(rows[0]["manufacturer"], "Torgue")

    def test_search_shield(self):
        rows = specials.search("蜜蜂")
        self.assertTrue(rows)
        self.assertEqual(rows[0]["rarity"], "传说")
        self.assertEqual(rows[0]["kind"], "护盾")

    def test_find_by_code(self):
        rows = specials.search("彩虹小马", kind="武器")
        self.assertTrue(rows)
        item = specials.find(rows[0]["code"])
        self.assertEqual(item["name"], rows[0]["name"])

    def test_markdown_and_csv_exist(self):
        self.assertTrue((ROOT / "data" / "特殊武器表.md").is_file())
        self.assertTrue((ROOT / "data" / "特殊武器表.csv").is_file())


class SpecialsAddTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = _any_save()
        if cls.src is None:
            raise unittest.SkipTest("本机没有 BL2 存档")
        cls.tmp = Path(tempfile.mkdtemp(prefix="bl2spec-"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _copy(self) -> Path:
        dst = self.tmp / "spec.sav"
        shutil.copy2(self.src, dst)
        return dst

    def test_add_special_weapon(self):
        path = self._copy()
        sf = SaveFile(path)
        before = sf.item_counts()["bank"]
        result = specials.add_to_save(sf, "不洁哈罗德", count=2, where="bank", dedupe=True)
        self.assertEqual(result["imported"]["bank"], 2)
        self.assertEqual(sf.item_counts()["bank"], before + 2)
        # 再导入应去重
        result2 = specials.add_to_save(sf, "不洁哈罗德", where="bank", dedupe=True)
        self.assertEqual(result2["imported"]["bank"], 0)
        sf.save(backup=False, force=True)
        self.assertEqual(SaveFile(path).item_counts()["bank"], before + 2)

    def test_add_unknown_raises(self):
        sf = SaveFile(self._copy())
        with self.assertRaises(ValueError):
            specials.add_to_save(sf, "这个武器不存在")


if __name__ == "__main__":
    unittest.main()
