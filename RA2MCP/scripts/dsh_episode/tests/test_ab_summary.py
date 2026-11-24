"""Offline test for dsh_ab_eval.summarize (no game needed)."""
import pathlib
import sys
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SCRIPTS))

import dsh_ab_eval  # noqa: E402


def rec(side, kind, elapsed, damage, hp_lost, violations=0, invalid=0, policy=None):
    return {"side": side, "episode_id": f"{side}-1", "returncode": 0,
            "wall_s": elapsed,
            "result": {"kind": kind, "elapsed_s": elapsed,
                       "metrics": {"damage_dealt": damage, "own_hp_lost": hp_lost},
                       "stats": {"visibility_violations": violations,
                                 "invalid_orders": invalid,
                                 "policy": policy}}}


class TestABSummary(unittest.TestCase):
    def test_summary_aggregates_sides(self):
        records = [
            rec("rule", "timeout", 240.0, 2475, 340, invalid=10),
            rec("model", "timeout", 240.0, 1200, 100, violations=0, invalid=8,
                policy={"model_actions": 100, "fallback_actions": 20}),
            rec("rule", "defeat", 200.0, 0, 500),
            rec("model", "timeout", 240.0, 900, 80),
        ]
        s = dsh_ab_eval.summarize(records)
        self.assertEqual(s["rule"]["episodes"], 2)
        self.assertEqual(s["model"]["episodes"], 2)
        self.assertEqual(s["rule"]["kinds"], {"timeout": 1, "defeat": 1})
        self.assertEqual(s["model"]["kinds"], {"timeout": 2})
        self.assertEqual(s["rule"]["total_damage_dealt"], 2475)
        self.assertEqual(s["model"]["total_damage_dealt"], 2100)
        self.assertEqual(s["rule"]["total_visibility_violations"], 0)
        self.assertEqual(s["rule"]["total_invalid_orders"], 10)
        self.assertEqual(s["model"]["model_stats"][0]["model_actions"], 100)

    def test_empty_records(self):
        s = dsh_ab_eval.summarize([])
        self.assertEqual(s["rule"]["episodes"], 0)
        self.assertEqual(s["model"]["episodes"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
