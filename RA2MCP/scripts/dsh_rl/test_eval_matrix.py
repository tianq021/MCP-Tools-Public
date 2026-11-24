"""P0-1 eval_matrix 的离线独立测试（不启动任何游戏）。

运行：
  set PYTHONPATH=E:\RA2MCP\.venv-rl\Lib\site-packages;E:\RA2MCP\scripts
  python -m unittest scripts.dsh_rl.test_eval_matrix -v
"""
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts/dsh_rl"))

import eval_matrix  # noqa: E402


def _write_episode(folder, name, attacks, money_series, result_metrics, elapsed=100.0):
    folder.mkdir(parents=True, exist_ok=True)
    jsonl = folder / f"{name}.jsonl"
    rows = [{"type": "meta", "meta": {"episode_id": name, "policy": "maskable_ppo_combat", "model": {"fixed_economy": True}}}]
    for index, money in enumerate(money_series):
        rows.append({
            "type": "observation",
            "t": index * 0.8,
            "money": money,
            "own_objects": [{"addr": 1, "type_name": "GI", "health": 100,
                             "x": 10.0, "y": 10.0}],
            "visible_enemies": [],
        })
    for units, target in attacks:
        rows.append({
            "type": "action", "action": "ATTACK", "decision_source": "ppo",
            "object_addresses": list(units), "target_address": target,
        })
    jsonl.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    result = {
        "episode_id": name, "kind": "defeat", "is_win": False,
        "outcome_confirmed": True, "elapsed_s": elapsed,
        "metrics": dict({"damage_dealt": 100.0, "own_hp_lost": 200.0,
                         "buildings_peak": 3, "units_peak": 8,
                         "economy_invalid_orders": 0}, **result_metrics),
    }
    result_path = folder / f"{name}.result.json"
    result_path.write_text(json.dumps(result), encoding="utf-8")
    return jsonl, result


class EvalMatrixTests(unittest.TestCase):
    def test_episode_metrics_dedup_and_economy(self):
        with tempfile.TemporaryDirectory() as tmp:
            attacks = [([11, 12], 900), ([11, 12], 900), ([13], 901)]
            jsonl, result = _write_episode(
                pathlib.Path(tmp), "ep1", attacks, [1000, 1200, 1100, 1400], {})
            metrics = eval_matrix.episode_metrics(jsonl, result)
            self.assertEqual(metrics["attacks_issued"], 2)
            self.assertEqual(metrics["order_repeat_rate"], 0.3333)
            self.assertEqual(metrics["damage_exchange"], 0.5)
            self.assertEqual(metrics["economy"]["status"],
                             "INCOMPUTABLE_FIXED_SCRIPTED_ECONOMY")
            self.assertIsNotNone(metrics["economy"]["money_income_per_min"])
            self.assertEqual(metrics["economy"]["money_final"], 1400)

    def test_summarize_marks_insufficient_sample(self):
        rows = [
            {"kind": "defeat", "is_win": False, "damage_dealt": 100.0, "own_hp_lost": 200.0,
             "damage_exchange": 0.5, "attacks_issued": 2, "attacks_effective6": 1,
             "attack_effective6_rate": 0.5, "order_repeat_rate": 0.2, "attacks_per_min": 3.0,
             "hp_exchange": 0.4, "own_losses_per_min": 3.0,
             "kills_per_1000_unit_damage": 4.0, "first_effective_attack_s": 10.0,
             "elapsed_s": 100.0},
            {"kind": "timeout", "is_win": False, "damage_dealt": 300.0, "own_hp_lost": 100.0,
             "damage_exchange": 3.0, "attacks_issued": 4, "attacks_effective6": 2,
             "attack_effective6_rate": 0.5, "order_repeat_rate": 0.1, "attacks_per_min": 5.0,
             "hp_exchange": 2.0, "own_losses_per_min": 1.0,
             "kills_per_1000_unit_damage": 6.0, "first_effective_attack_s": 8.0,
             "elapsed_s": 300.0},
        ]
        summary = eval_matrix.summarize(rows)
        self.assertEqual(summary["n_episodes"], 2)
        self.assertEqual(summary["sample_status"], "INSUFFICIENT_SAMPLE")
        self.assertFalse(summary["mastery_claim"])
        self.assertEqual(summary["damage_exchange"]["mean"], 1.75)
        self.assertEqual(summary["win_rate"], 0.0)

    def test_paired_compare_ordinal(self):
        det = [{"episode_id": "a", "damage_exchange": 1.0},
               {"episode_id": "b", "damage_exchange": 2.0}]
        sto = [{"episode_id": "c", "damage_exchange": 1.5},
               {"episode_id": "d", "damage_exchange": 1.0}]
        value = eval_matrix.paired_compare(det, sto, "damage_exchange")
        self.assertEqual(value["n_pairs"], 0)
        self.assertIsNone(value["mean_delta_stochastic_minus_deterministic"])
        self.assertEqual(value["status"], "UNPAIRED_NO_CASE_ID")

    def test_sha256_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "x.bin"
            path.write_bytes(b"abc")
            self.assertEqual(eval_matrix.sha256_file(path),
                             "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")


if __name__ == "__main__":
    unittest.main(verbosity=2)
