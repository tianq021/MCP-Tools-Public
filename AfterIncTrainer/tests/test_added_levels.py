import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

import added_levels as levels


class AddedLevelsTests(unittest.TestCase):
    def test_descriptions_match_all_icons_and_have_no_game_markup(self):
        catalogue = levels.read_json(levels.CATALOGUE_FILE)
        self.assertEqual(set(catalogue), {p.stem for p in levels.ICON_DIR.glob("*.png")})
        for info in catalogue.values():
            self.assertRegex(info["description"], r"[\u4e00-\u9fff]")
            self.assertNotRegex(info["description"], r"<[^>]+>|\{[^}]+\}")

    def test_picker_explains_and_blocks_conflicts_across_filters(self):
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        with tempfile.TemporaryDirectory() as temp, patch.object(levels, "DIRECTORY", Path(temp)):
            panel = levels.AddOptions(root)
            panel.open_picker()
            picker = panel.picker
            boost, drop = "zone_production_boost_sheep", "zone_production_drop_sheep"
            self.assertEqual(picker.tree.set(boost, "description"), "绵羊区域的食物产量翻倍")
            picker.toggle(boost)
            picker.query.set("减半")
            self.assertEqual(picker.tree.set(drop, "check"), "不可选")
            picker.toggle(drop)
            self.assertEqual(panel.selected, {boost})
            self.assertIn("庇护草甸", picker.detail.get())
            self.assertIn("腐蹄疫", picker.detail.get())
            picker.check_selection()
            self.assertIn("相容", picker.detail.get())
            picker.query.set("")
            picker.toggle(boost)
            self.assertNotIn(drop, picker.reasons)
            picker.toggle(drop)
            self.assertEqual(panel.selected, {drop})
            panel.random_min.set("0")
            panel.random_max.set("0")
            picker.check_selection()
            self.assertIn("最多 0 项", picker.detail.get())
            picker.toggle(drop)
            self.assertFalse(panel.selected)

    def test_game_ambition_random_range_and_reset_from_real_widgets(self):
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        with tempfile.TemporaryDirectory() as temp, patch.object(levels, "DIRECTORY", Path(temp)):
            panel = levels.AddOptions(root)
            self.assertEqual(panel.ambition.get(), "游戏生成")
            self.assertIsNone(panel.options())
            panel.random_min.set("2")
            panel.random_max.set("4")
            panel.open_picker()
            picker = panel.picker
            picker.query.set("盲区")
            self.assertEqual(picker.tree.get_children(), ("soldier_combat_debuff_motorway",))
            icon = panel.icon_for("soldier_combat_debuff_motorway")
            self.assertEqual((icon.width(), icon.height()), (40, 40))
            self.assertEqual(picker.tree.item("soldier_combat_debuff_motorway", "image"), (str(icon),))
            self.assertIsNone(panel.icon_for("unknown_trait"))
            picker.toggle("soldier_combat_debuff_motorway")
            self.assertEqual(panel.summary_image.height(), 20)
            picker.query.set("腐蹄疫")
            self.assertEqual(picker.tree.get_children(), ("zone_production_drop_sheep",))
            picker.query.set("")
            picker.only_selected.set(True)
            picker.render()
            self.assertEqual(picker.tree.get_children(), ("soldier_combat_debuff_motorway",))
            picker.destroy()
            panel.refresh()
            self.assertEqual(panel.options(), {"modifiers": ["soldier_combat_debuff_motorway"], "ambition": None, "danger": None,
                                              "totalMin": 2, "totalMax": 4, "proceduralMap": False})
            panel.reset()
            panel.procedural_map.set(True)
            self.assertTrue(panel.options()["proceduralMap"])
            panel.reset()
            self.assertIsNone(panel.options())

    def test_random_bounds_checked_before_add(self):
        for low, high in (("2", "1"), ("-1", "1"), ("1", ""), ("0", "1.5"), ("0", "9999999999")):
            with self.subTest(low=low, high=high), self.assertRaises(ValueError):
                levels.make_options("游戏生成", "游戏生成", [], {}, low, high)
        self.assertEqual(levels.make_options("", "低", [], {}, "2", "2")["totalMax"], 2)
        self.assertEqual(levels.make_options("", "低", [], {}, "0", "0")["totalMax"], 0)
        with self.assertRaisesRegex(ValueError, "已手选"):
            levels.make_options("", "低", ["a"], {"a": {"title": "A"}}, "0", "0")

    def test_readback_reports_final_count_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "levels.json"
            options = levels.make_options("游戏生成", "游戏生成", [], {}, "1", "2")
            levels.write_json(path, {"123": options})
            levels.write_json(path.with_suffix(".results.json"), {"123": {"options": options, "ambition": 7,
                "danger": 1, "traits": ["原有", "新特性"], "randomTraits": ["新特性"]}})
            with patch.object(levels, "session_file", return_value=path):
                report = levels.readback(None, 1, [123])
                self.assertIn("特性共 2 项（原有、新特性）", report)
                self.assertEqual(report.count("新特性"), 1)

    def test_appends_group_only_levels_from_same_button_click(self):
        options = levels.make_options("", "中", [], {}, "2", "4")
        first = levels.next_settings({}, [], [10, 11], options)
        second = levels.next_settings(first, [10, 11], [12], options)
        self.assertEqual(first["10"]["batchId"], second["11"]["batchId"])
        self.assertNotEqual(first["10"]["batchId"], second["12"]["batchId"])
        self.assertNotIn("batchId", options)

    def test_preflight_range_and_exclusions(self):
        catalogue = dict(levels.FALLBACK_TRAITS, zone_production_boost_sheep={"title": "羊群增产"})
        with self.assertRaisesRegex(ValueError, "互斥"):
            levels.make_options("15", "中", ["zone_production_drop_sheep", "zone_production_boost_sheep"], catalogue)
        with self.assertRaisesRegex(ValueError, "危险度"):
            levels.make_options("15", "无", ["soldier_combat_debuff_motorway"], catalogue)
        self.assertEqual(levels.make_options("", "无", [], catalogue)["danger"], 0)

    def test_manual_traits_with_no_common_danger_rejected_before_add(self):
        catalogue = {"guard": {"title": "城市守卫", "dangerMin": 2},
                     "low": {"title": "低危", "dangerMax": 1}}
        with self.assertRaisesRegex(ValueError, "没有共同"):
            levels.make_options("游戏生成", "游戏生成", ["guard", "low"], catalogue)

    def test_append_preserves_older_settings_and_clear_discards_all(self):
        first = {"ambition": 15}
        second = {"danger": 2}
        config = levels.next_settings({}, [], [22222, 23333], first)
        result = levels.next_settings(config, [22222, 23333], [24444], second)
        self.assertEqual(result, {"22222": first, "23333": first, "24444": second})
        self.assertEqual(levels.next_settings(result, [], [], None), {})

    def test_read_only_reports_generated_result_for_current_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "levels.json"
            options = {"ambition": 15, "danger": 2, "modifiers": list(levels.FALLBACK_TRAITS)}
            levels.write_json(file, {"23456": options})
            with patch.object(levels, "session_file", return_value=file):
                self.assertIn("等待地图生成", levels.readback(None, 1, [23456]))
                levels.write_json(file.with_suffix(".results.json"), {"23456": {"options": options, "ambition": 15,
                    "danger": 2, "traits": ["盲区", "腐蹄疫"], "error": None}})
                self.assertIn("危险度 中，特性共 2 项（盲区、腐蹄疫）", levels.readback(None, 1, [23456]))

