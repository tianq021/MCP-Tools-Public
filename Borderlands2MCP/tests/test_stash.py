"""Claptrap 共享储藏箱（profile.bin）测试：全部在副本上做。"""

from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bl2toolkit import itemcodes  # noqa: E402
from bl2toolkit import profile as profilemod  # noqa: E402
from bl2toolkit import specials  # noqa: E402


class StashTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = profilemod.default_profile_path()
        if cls.src is None:
            raise unittest.SkipTest("本机没有 profile.bin")
        cls.tmp = Path(tempfile.mkdtemp(prefix="bl2stash-"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _copy(self) -> Path:
        dst = self.tmp / "profile.bin"
        shutil.copy2(self.src, dst)
        return dst

    def test_parse_real_profile(self):
        pf = profilemod.ProfileFile(self._copy())
        self.assertGreater(len(pf.entries), 10)
        self.assertEqual(len(pf.stash_slots()), 4)
        self.assertEqual(len(pf.stash_summary()["stash"]), 4)
        # 条目 ID 130-133 必须存在且是 Binary
        for entry_id in profilemod.STASH_ENTRY_IDS:
            entry = pf.require_entry(entry_id)
            self.assertEqual(entry.dtype, profilemod.BINARY)

    def test_set_and_clear_stash(self):
        path = self._copy()
        pf = profilemod.ProfileFile(path)
        weapon = specials.search(kind="武器", limit=1)[0]
        code = weapon["code"]
        pf.set_stash_slot(1, code)
        self.assertEqual(itemcodes.with_key_zero(pf.stash_slots()[0]), itemcodes.with_key_zero(code))
        pf.save(backup=False, force=True)

        pf2 = profilemod.ProfileFile(path)
        self.assertEqual(itemcodes.with_key_zero(pf2.stash_slots()[0]), itemcodes.with_key_zero(code))
        # 清空
        pf2.set_stash_slot(1, None)
        pf2.save(backup=False, force=True)
        pf3 = profilemod.ProfileFile(path)
        self.assertIsNone(pf3.stash_slots()[0])

    def test_roundtrip_preserves_other_entries(self):
        path = self._copy()
        pf = profilemod.ProfileFile(path)
        before = {e.id: (e.dtype, e.raw_value) for e in pf.entries}
        code = specials.search(kind="武器", limit=1)[0]["code"]
        pf.set_stash_slot(2, code)
        pf.save(backup=False, force=True)
        pf2 = profilemod.ProfileFile(path)
        after = {e.id: (e.dtype, e.raw_value) for e in pf2.entries}
        self.assertEqual(set(before), set(after))
        for entry_id in before:
            if entry_id == 131:
                continue
            self.assertEqual(before[entry_id], after[entry_id], f"条目 {entry_id} 被意外改动")

    def test_sha1_header_valid(self):
        path = self._copy()
        pf = profilemod.ProfileFile(path)
        code = specials.search(kind="武器", limit=1)[0]["code"]
        pf.set_stash_slot(3, code)
        pf.save(backup=False, force=True)
        data = path.read_bytes()
        self.assertEqual(hashlib.sha1(data[20:]).digest(), data[:20])

    def test_empty_slot_is_40_zero_bytes(self):
        pf = profilemod.ProfileFile(self._copy())
        entry = pf.require_entry(130)
        if all(b == 0 for b in entry.value):
            self.assertEqual(len(entry.value), 40)

    def test_risk_assessment(self):
        # 正常物品不应报等级风险
        normal = specials.search(kind="武器", rarity="传说", limit=1)[0]["code"]
        risk = itemcodes.assess_risk(normal)
        self.assertEqual(risk["version"], 7)
        self.assertFalse(any("超过原版上限" in w for w in risk["warnings"]))

    def test_save_item_to_stash_to_another_save(self):
        """完整链路：存档物品代码 -> 储藏箱(profile.bin) -> 另一个存档，验证代码字节一致。"""
        from bl2toolkit.paths import find_steam_id_dir, list_saves
        from bl2toolkit.saves import SaveFile

        saves = list_saves(find_steam_id_dir()) if find_steam_id_dir() else []
        if len(saves) < 2:
            self.skipTest("需要至少两个存档")
        src_save = self.tmp / "src.sav"
        dst_save = self.tmp / "dst.sav"
        shutil.copy2(saves[0], src_save)
        shutil.copy2(saves[1], dst_save)

        sf = SaveFile(src_save)
        code = sf.export_codes(("weapons",))["weapons"][0]

        path = self._copy()
        pf = profilemod.ProfileFile(path)
        pf.set_stash_slot(1, code)
        pf.save(backup=False, force=True)
        stash_code = profilemod.ProfileFile(path).stash_slots()[0]
        self.assertEqual(itemcodes.with_key_zero(stash_code), itemcodes.with_key_zero(code))

        # 再从储藏箱导入到另一个存档副本
        target = SaveFile(dst_save)
        before = target.item_counts()["bank"]
        result = target.import_codes([stash_code], where="bank")
        self.assertEqual(result["bank"], 1)
        self.assertEqual(target.item_counts()["bank"], before + 1)


if __name__ == "__main__":
    unittest.main()
