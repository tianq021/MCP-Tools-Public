from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import level_traits


class LevelTraitsTest(unittest.TestCase):
    def test_blank_range_and_fixed_zero_have_distinct_meanings(self):
        profile = level_traits.make_profile("80702", "", "", "0", "0", "1", "3")
        self.assertIsNone(profile["ambitionRange"])
        self.assertEqual(profile["dangerRange"], {"min": 0, "max": 0})
        self.assertEqual(profile["countRange"], {"min": 1, "max": 3})
        self.assertEqual(profile["seed"], 80702)

    def test_rejects_missing_changes_and_out_of_range_values(self):
        for values in (("80702", "", "", "", "", "0", "0"), ("0", "1", "1", "", "", "1", "1"),
                       ("80702", "3", "", "", "", "1", "1"), ("80702", "9", "1", "", "", "1", "1"),
                       ("80702", "", "", "0", "5", "0", "0"), ("80702", "", "", "", "", "-1", "1"),
                       ("80702", "1", "2", "", "", "3", "2"), ("80702", "1", "2", "", "", "", "2")):
            with self.subTest(values=values), self.assertRaises(ValueError):
                level_traits.make_profile(*values)

    def test_save_and_replace_local_profile(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "traits"
            self.assertIsNone(level_traits.read_record(root / "profile.json"))
            for ambition in ("0", "10"):
                expected = level_traits.make_profile("80702", "0", ambition, "1", "4", "2", "2")
                level_traits.save_profile(root, expected)
                self.assertEqual(level_traits.read_record(root / "profile.json"), expected)
            self.assertEqual([file.name for file in root.iterdir()], ["profile.json"])

    def test_status_requires_matching_profile_and_seed(self):
        saved = level_traits.make_profile("80702", "8", "8", "", "", "2", "3")
        other = level_traits.make_profile("80702", "7", "7", "", "", "2", "3")
        snapshot = {
            "state": "ready",
            "profile": other,
            "expeditions": [{"seed": 80702, "applied": True, "ambition": 7, "danger": 1}],
        }
        self.assertIn("下次启动", level_traits.observed_status(snapshot, saved))
        snapshot["profile"] = saved
        snapshot["expeditions"][0]["seed"] = 80703
        self.assertIn("尚未生成", level_traits.observed_status(snapshot, saved))
        snapshot["expeditions"][0].update(seed=80702, ambition=8, addedModifiers=["a", "b"])
        self.assertIn("雄心 8", level_traits.observed_status(snapshot, saved))
        self.assertIn("追加 2 项", level_traits.observed_status(snapshot, saved))
        snapshot["expeditions"][0].update(applied=False, error="not_enough_modifiers", availableCount=1)
        self.assertIn("最多可追加 1 项", level_traits.observed_status(snapshot, saved))

    def test_reads_previous_fixed_value_profiles(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            records = (
                {"schema": 1, "build": "26049821", "seed": 80702,
                 "ambition": 3, "danger": 1, "modifier": "old_effect"},
                {"schema": 2, "build": "26049821", "seed": 80702,
                 "ambitionRange": {"min": 3, "max": 4}, "dangerRange": None, "count": 2, "rollSeed": 42},
            )
            for old in records:
                level_traits.save_profile(root, old)
                self.assertEqual(level_traits.read_record(root / "profile.json"), old)


if __name__ == "__main__":
    unittest.main()
