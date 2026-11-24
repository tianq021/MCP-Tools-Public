"""存档读写测试：全部在临时副本上做，不会改真实存档。

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

from bl2toolkit import itemcodes  # noqa: E402
from bl2toolkit.paths import find_steam_id_dir, list_saves, resolve_save  # noqa: E402
from bl2toolkit.saves import SaveFile  # noqa: E402

BLACK_GUN = "BL2(hwAAAABc0wCE5wRABgGF6sNohWEQowQKQgyEGB4wEGIcxNjEkgnl)"


def _any_save() -> Path | None:
    saves = list_saves(find_steam_id_dir()) if find_steam_id_dir() else []
    return saves[0] if saves else None


class SaveFileTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = _any_save()
        if cls.src is None:
            raise unittest.SkipTest("本机没有 BL2 存档")
        cls.tmp = Path(tempfile.mkdtemp(prefix="bl2test-"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _copy(self, name: str) -> Path:
        dst = self.tmp / name
        shutil.copy2(self.src, dst)
        return dst

    def test_summary(self):
        sf = SaveFile(self._copy("a.sav"))
        sm = sf.summary()
        self.assertTrue(sm["name"])
        self.assertGreaterEqual(sm["level"], 1)
        self.assertIn("bank", sm["items"])

    def test_resave_without_change_keeps_data(self):
        path = self._copy("b.sav")
        sf = SaveFile(path)
        before = sf.summary()
        out = self.tmp / "b-resaved.sav"
        sf.save(out, backup=False, force=True)
        after = SaveFile(out).summary()
        for key in ("name", "class", "level", "experience", "skill_points", "items", "currency"):
            self.assertEqual(after[key], before[key], key)

    def test_skill_points_roundtrip(self):
        path = self._copy("c.sav")
        sf = SaveFile(path)
        before = sf.summary()
        sf.set_skill_points(88)
        sf.save(backup=False, force=True)
        after = SaveFile(path).summary()
        self.assertEqual(after["skill_points"], 88)
        self.assertEqual(after["level"], before["level"])
        self.assertEqual(after["name"], before["name"])
        self.assertEqual(after["items"], before["items"])

    def test_export_import_dedupe(self):
        path = self._copy("d.sav")
        sf = SaveFile(path)
        codes = sf.export_codes()
        bank = codes["bank"][:3]
        self.assertTrue(bank)
        # 同一批代码再导入应全部被去重
        result = sf.import_codes(bank, where="bank", dedupe=True)
        self.assertEqual(result["bank"], 0)
        # 关掉去重则应该都进去
        before = sf.item_counts()["bank"]
        result = sf.import_codes(bank, where="bank", dedupe=False)
        self.assertEqual(result["bank"], len(bank))
        self.assertEqual(sf.item_counts()["bank"], before + len(bank))

    def test_import_black_gun_and_remove(self):
        path = self._copy("e.sav")
        sf = SaveFile(path)
        before = sf.item_counts()["bank"]
        result = sf.import_codes([BLACK_GUN], where="bank")
        self.assertEqual(result["bank"], 1)
        self.assertEqual(sf.item_counts()["bank"], before + 1)
        removed = sf.remove_items([BLACK_GUN])
        self.assertEqual(removed, 1)
        self.assertEqual(sf.item_counts()["bank"], before)

    def test_edit_level_and_money(self):
        path = self._copy("f.sav")
        sf = SaveFile(path)
        before = sf.summary()
        after = sf.edit(level=50, money=123456)
        self.assertEqual(after["level"], 50)
        self.assertEqual(sf.currency().get("money"), 123456)
        # 等级改动不应影响物品数量
        self.assertEqual(after["items"], before["items"])

    def test_codes_stay_valid_after_save(self):
        path = self._copy("g.sav")
        sf = SaveFile(path)
        sf.set_skill_points(10)
        sf.save(backup=False, force=True)
        sf2 = SaveFile(path)
        for code in sf2.all_codes()[:50]:
            itemcodes.decode(code)


if __name__ == "__main__":
    unittest.main()
