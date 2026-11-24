"""物品代码编解码测试。

运行：python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bl2toolkit import itemcodes  # noqa: E402
from bl2toolkit import library as gunlib  # noqa: E402

# 参考资料里的黑枪代码：超级哈罗德
BLACK_GUN = "BL2(hwAAAABc0wCE5wRABgGF6sNohWEQowQKQgyEGB4wEGIcxNjEkgnl)"
# 护盾代码：蜜蜂
SHIELD = "BL2(BwAAAAC0QwAWEWqgwxBI5AGEBEQHBFfE//////////9WxA3EWsQ=)"


class ItemCodeTest(unittest.TestCase):
    def test_decode_black_gun(self):
        info = itemcodes.decode(BLACK_GUN)
        self.assertEqual(info["is_weapon"], 1)
        self.assertEqual(list(info["level"]), [80, 80])
        self.assertEqual(len(info["parts"]), 11)
        self.assertEqual(info["manufacturer"], {"lib": 1, "asset": 3})

    def test_encode_roundtrip(self):
        info = itemcodes.decode(BLACK_GUN)
        self.assertEqual(itemcodes.encode(info), BLACK_GUN)

    def test_shield_is_not_weapon(self):
        info = itemcodes.decode(SHIELD)
        self.assertEqual(info["is_weapon"], 0)

    def test_with_key_zero_is_stable(self):
        self.assertEqual(itemcodes.with_key_zero(BLACK_GUN), itemcodes.with_key_zero(BLACK_GUN))

    def test_extract_codes_from_text(self):
        text = f"名称A {BLACK_GUN}\n名称B：{SHIELD}\n"
        self.assertEqual(itemcodes.extract_codes(text), [BLACK_GUN, SHIELD])

    def test_invalid_code_raises(self):
        with self.assertRaises(ValueError):
            itemcodes.decode("BL2(not-base64!)")

    def test_library_codes_all_decode(self):
        entries = gunlib.load_library()
        if not entries:
            self.skipTest("代码库还没生成：python scripts\\build_code_library.py")
        for entry in entries[:50]:
            info = itemcodes.decode(entry["code"])
            self.assertIn("parts", info)


if __name__ == "__main__":
    unittest.main()
