"""Codex research_tools 只读接口测试（module 由 Codex 维护，不改动）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import research_tools  # noqa: E402


class ResearchToolsTest(unittest.TestCase):
    def test_search_mods_chinese_alias(self):
        result = research_tools.search_mods("经验", limit=5)
        self.assertIn("matched", result)
        self.assertIn("items", result)
        self.assertLessEqual(len(result["items"]), 5)
        self.assertIn("上游目录快照", result["scope"])

    def test_search_mods_validation(self):
        with self.assertRaises(ValueError):
            research_tools.search_mods(limit=0)
        with self.assertRaises(ValueError):
            research_tools.search_mods(limit=101)
        with self.assertRaises(ValueError):
            research_tools.search_mods(query=123)

    def test_feature_plan_not_executable(self):
        result = research_tools.feature_plan("经验")
        self.assertIn("不能执行游戏操作", result["status"])
        self.assertIsInstance(result["items"], list)
        for item in result["items"]:
            if "runtime_available" in item:
                self.assertFalse(item["runtime_available"])
                self.assertFalse(item.get("runtime_tested", False))

    def test_register_includes_character_query(self):
        class FakeMcp:
            def __init__(self):
                self.tools = []

            def tool(self):
                def deco(fn):
                    self.tools.append(fn)
                    return fn

                return deco

        fake = FakeMcp()
        research_tools.register(fake)
        names = [f.__name__ for f in fake.tools]
        self.assertIn("bl2_research_mod_search", names)
        self.assertIn("bl2_research_feature_plan", names)
        self.assertIn("bl2_current_character", names)
        self.assertEqual(len(names), len(set(names)))

    def test_no_game_write_calls(self):
        import inspect

        src = inspect.getsource(research_tools)
        for forbidden in (".save(", "write_bytes", "RemoveHook", "RegisterHook", "launcher"):
            self.assertNotIn(forbidden, src)


if __name__ == "__main__":
    unittest.main()
