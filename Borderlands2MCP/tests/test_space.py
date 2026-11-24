"""背包/银行空间上限测试。

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

from bl2toolkit import space  # noqa: E402
from bl2toolkit.paths import find_steam_id_dir, list_saves  # noqa: E402
from bl2toolkit.saves import SaveFile  # noqa: E402


def _any_save() -> Path | None:
    d = find_steam_id_dir()
    saves = list_saves(d) if d else []
    return saves[0] if saves else None


class ExeCapsTest(unittest.TestCase):
    def setUp(self):
        self.exe = space.find_game_exe()
        if self.exe is None:
            self.skipTest("本机没找到 Borderlands2.exe")

    def test_detect_installed_caps(self):
        caps = space.analyze_exe(self.exe)
        # 原版 clamp：背包 12/39，银行 6/24（如果以后换了补丁 exe，这里会失败是正常的）
        self.assertEqual(caps.backpack_min, 12)
        self.assertEqual(caps.bank_min, 6)
        self.assertGreaterEqual(caps.backpack_max, 39)
        self.assertGreaterEqual(caps.bank_max, 24)

    def test_patch_output_copy(self):
        tmp = Path(tempfile.mkdtemp(prefix="bl2exe-"))
        try:
            out = tmp / "patched.exe"
            result = space.patch_exe(
                self.exe,
                output=out,
                backpack_max=100,
                bank_max=30,
                patch_cmp=False,
            )
            self.assertTrue(out.is_file())
            new_caps = space.analyze_exe(out)
            self.assertEqual(new_caps.backpack_max, 100)
            self.assertEqual(new_caps.bank_max, 30)
            # 只改动了两个上限字段
            old = self.exe.read_bytes()
            new = out.read_bytes()
            diff = [i for i in range(len(old)) if old[i] != new[i]]
            self.assertLessEqual(len(diff), 4)
            # 原 exe 没被动
            self.assertEqual(space.analyze_exe(self.exe).backpack_max, result["caps_before"]["backpack_max"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_patch_with_cmp(self):
        tmp = Path(tempfile.mkdtemp(prefix="bl2exe-"))
        try:
            out = tmp / "patched250.exe"
            space.patch_exe(self.exe, output=out, backpack_max=250, bank_max=250, patch_cmp=True)
            caps = space.analyze_exe(out)
            self.assertEqual(caps.backpack_max, 250)
            self.assertEqual(caps.backpack_cmp, 250)
            self.assertEqual(caps.bank_max, 250)
            self.assertEqual(caps.bank_cmp, 250)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class SaveSpaceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = _any_save()
        if cls.src is None:
            raise unittest.SkipTest("本机没有 BL2 存档")
        cls.tmp = Path(tempfile.mkdtemp(prefix="bl2space-"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _copy(self) -> Path:
        dst = self.tmp / "space.sav"
        shutil.copy2(self.src, dst)
        return dst

    def test_space_info(self):
        sf = SaveFile(self._copy())
        info = space.space_info(sf)
        self.assertIn("backpack", info)
        self.assertIn("bank", info)
        self.assertGreater(info["backpack"]["capacity"], 0)

    def test_set_vanilla_max(self):
        path = self._copy()
        sf = SaveFile(path)
        result = space.set_space(sf, backpack=39, bank=24)
        self.assertEqual(result["after"]["backpack"]["capacity"], 39)
        self.assertEqual(result["after"]["bank"]["capacity"], 24)
        self.assertEqual(result["after"]["backpack"]["sdu_level"], 9)
        self.assertEqual(result["after"]["bank"]["sdu_level"], 9)
        sf.save(backup=False, force=True)
        again = space.space_info(SaveFile(path))
        self.assertEqual(again["backpack"]["capacity"], 39)
        self.assertEqual(again["bank"]["capacity"], 24)

    def test_set_step_validation(self):
        sf = SaveFile(self._copy())
        with self.assertRaises(ValueError):
            space.set_space(sf, backpack=40)  # 超过原版上限
        with self.assertRaises(ValueError):
            space.set_space(sf, backpack=38)  # 步长不对
        with self.assertRaises(ValueError):
            space.set_space(sf, bank=25)

    def test_set_oversize(self):
        sf = SaveFile(self._copy())
        result = space.set_space(sf, backpack=100, bank=60, allow_oversize=True)
        self.assertEqual(result["after"]["backpack"]["capacity"], 100)
        self.assertEqual(result["after"]["bank"]["capacity"], 60)
        self.assertEqual(result["after"]["backpack"]["sdu_level"], 9)

    def test_warning_when_save_exceeds_exe(self):
        sf = SaveFile(self._copy())
        space.set_space(sf, backpack=100, allow_oversize=True)
        info = space.space_info(sf)
        if info["installed_exe"] and info["installed_exe"]["backpack_max"] < 100:
            self.assertTrue(any("超过当前 exe 上限" in w for w in info["warnings"]))


if __name__ == "__main__":
    unittest.main()
