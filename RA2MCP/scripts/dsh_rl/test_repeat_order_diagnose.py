"""P0-2 诊断工具的离线独立测试（合成轨迹，不启动游戏）。"""
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts/dsh_rl"))

import repeat_order_diagnose as diag  # noqa: E402


def _write_episode(folder, name, actions, damages):
    folder = folder / "workers" / "0" / "episodes"
    folder.mkdir(parents=True, exist_ok=True)
    jsonl = folder / f"{name}.jsonl"
    rows = [{"type": "meta", "episode_id": name}]
    times = [0.0, 0.8, 1.6, 2.4, 3.2]
    for index, t in enumerate(times):
        enemies = [{"addr": 900, "type_name": "GI", "health": 100 - damages.get(index, 0),
                    "x": 20.0, "y": 20.0}]
        rows.append({
            "type": "observation", "t": t,
            "own_objects": [{"addr": 1, "type_name": "GI", "health": 100,
                             "x": 10.0, "y": 10.0}],
            "visible_enemies": enemies,
        })
    for action in actions:
        rows.append({"type": "action", "decision_source": "ppo", **action})
    jsonl.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    result = {"episode_id": name, "kind": "defeat", "elapsed_s": 4.0,
              "outcome_confirmed": True, "metrics": {"damage_dealt": 50, "own_hp_lost": 10}}
    (folder / f"{name}.result.json").write_text(json.dumps(result), encoding="utf-8")
    return jsonl, result


class RepeatOrderDiagnoseTests(unittest.TestCase):
    def test_consecutive_duplicate_and_effective(self):
        with tempfile.TemporaryDirectory() as tmp:
            actions = [
                {"action": "ATTACK", "object_addresses": [11, 12], "target_address": 900},
                {"action": "ATTACK", "object_addresses": [11, 12], "target_address": 900},
                {"action": "MOVE", "object_addresses": [11], "coordinates": [1, 2]},
                {"action": "ATTACK", "object_addresses": [13], "target_address": 901},
            ]
            # 第二局观测 900 掉血 → 第一次唯一攻击视作 effective6
            jsonl, result = _write_episode(pathlib.Path(tmp), "ep1", actions, {2: 10})
            row = diag.analyze_run(pathlib.Path(tmp))["episodes"][0]
            self.assertEqual(row["raw_attack_orders"], 3)
            self.assertEqual(row["unique_attack_orders"], 2)
            self.assertEqual(row["order_repeat_rate"], 0.3333)
            self.assertEqual(row["consecutive_duplicate_count"], 1)
            self.assertEqual(row["target_switches"], 1)
            self.assertGreaterEqual(row["unique_effective6"], 1)
            self.assertIn("own_object_keys", row["observation_order_state_fields"])
            self.assertFalse(row["observation_order_state_fields"]["has_current_order_state"])

    def test_root_cause_hypothesis(self):
        with tempfile.TemporaryDirectory() as tmp:
            actions = []
            for _ in range(12):
                actions.append({"action": "ATTACK", "object_addresses": [11],
                                "target_address": 900})
            jsonl, _ = _write_episode(pathlib.Path(tmp), "ep2", actions, {})
            run = diag.analyze_run(pathlib.Path(tmp))
            self.assertEqual(run["summary"]["root_cause_hypothesis"],
                             "POLICY_REISSUE_WITHOUT_PERSISTENT_ORDER_FEEDBACK")


if __name__ == "__main__":
    unittest.main(verbosity=2)
