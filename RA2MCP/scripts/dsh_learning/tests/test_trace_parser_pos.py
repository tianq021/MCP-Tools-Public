"""P0-1 unit tests: trace_parser position extraction after the `_pos` fix.

Run: python -m unittest scripts.dsh_learning.tests.test_trace_parser_pos -v
(no game/bridge/training needed; read-only on recorded episode files)
"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
from dsh_learning import trace_parser as tp  # noqa: E402

EPISODE = (ROOT / "reports" / "rl" / "stage-A-round1-20261030-153225"
           / "workers" / "0" / "episodes" / "ppo-20261030-153252-a27bc6.jsonl")


class PosExtractionTest(unittest.TestCase):
    def test_cell_has_priority(self):
        obj = {"cell": [10, 20], "xyz": [2560, 5120], "x": 99, "y": 99}
        self.assertEqual(tp._pos(obj), (10.0, 20.0))

    def test_xyz_fallback_converts_leptons(self):
        obj = {"xyz": [512, 1024]}
        self.assertEqual(tp._pos(obj), (2.0, 4.0))

    def test_legacy_xy_fallback(self):
        self.assertEqual(tp._pos({"x": 3, "y": 4}), (3.0, 4.0))

    def test_missing_returns_none(self):
        self.assertIsNone(tp._pos({}))
        self.assertIsNone(tp._pos({"cell": [None, 2]}))

    def test_recorded_episode_positions_resolve(self):
        """Regression: old code returned None for every recorded episode object."""
        if not EPISODE.exists():
            self.skipTest("recorded episode fixture not present")
        observations, _actions = tp._load_records(EPISODE)
        self.assertGreater(len(observations), 0)
        checked = 0
        for obs in observations[:200]:
            for obj in (obs.get("own_objects") or []) + (obs.get("visible_enemies") or []):
                pos = tp._pos(obj)
                if pos is not None:
                    checked += 1
                    self.assertIsInstance(pos[0], float)
                    self.assertIsInstance(pos[1], float)
            if checked > 20:
                break
        self.assertGreater(checked, 0, "no position resolved from real episode data")


if __name__ == "__main__":
    unittest.main(verbosity=2)
