"""M0-A analyze 单测（合成 episode，不启动游戏）。"""
import json
import pathlib
import tempfile
import unittest

from eval_m0a import analyze


def _obs(t, own, money=20000, power=(0, 0)):
    return {"type": "observation", "t": t, "money": money,
            "houses": [{"current_player": True, "power_output": power[0],
                        "power_drain": power[1]}],
            "own_objects": own, "visible_enemies": []}


def _mcv():
    return [{"addr": 1, "type_name": "Allied Construction Vehicle",
             "abstract": "ABSTRACT_TYPE_UNIT", "on_map": True, "in_limbo": False,
             "cell": [10, 10]}]


def _cy():
    return [{"addr": 2, "type_name": "Allied Construction Yard",
             "abstract": "ABSTRACT_TYPE_BUILDING", "on_map": True, "in_limbo": False,
             "cell": [10, 10]}]


class AnalyzeTest(unittest.TestCase):
    def _write(self, rows):
        tmp = tempfile.NamedTemporaryFile('w', suffix='.jsonl', delete=False, encoding='utf-8')
        for row in rows:
            tmp.write(json.dumps(row) + '\n')
        tmp.close()
        return pathlib.Path(tmp.name)

    def test_noop_episode_flags(self):
        rows = [_obs(0, _mcv()), _obs(2, _mcv()),
                {"type": "action", "action": "MOVE", "object_addresses": [1],
                 "decision_source": "ppo"}]
        path = self._write(rows)
        result = {"episode_id": "e1", "kind": "timeout", "elapsed_s": 2,
                  "metrics": {"attacks_issued": 0, "attacks_effective": 0}}
        out = analyze.analyze_episode(path, result)
        self.assertEqual(out["model_actions"], 1)
        self.assertEqual(out["non_model_actions"], 0)
        self.assertTrue(out["capabilities"]["is_script_free_full_env"])
        self.assertFalse(out["capabilities"]["can_deploy_mcv"])
        self.assertFalse(out["capabilities"]["wins"])

    def test_deploy_and_build(self):
        rows = [_obs(0, _mcv()),
                {"type": "action", "action": "DEPLOY", "object_addresses": [1],
                 "decision_source": "ppo"},
                _obs(1, _cy(), power=(100, 0)),
                _obs(2, _cy() + [{"addr": 3, "type_name": "Allied Power Plant",
                                  "abstract": "ABSTRACT_TYPE_BUILDING", "on_map": True,
                                  "in_limbo": False, "cell": [12, 10]}],
                     power=(200, 10)),
                {"type": "action", "action": "ATTACK", "object_addresses": [9],
                 "target_address": 77, "decision_source": "ppo"}]
        path = self._write(rows)
        result = {"episode_id": "e2", "kind": "defeat", "elapsed_s": 3,
                  "metrics": {"attacks_issued": 1, "attacks_effective": 1,
                              "truth_kills_infantry": 1}}
        out = analyze.analyze_episode(path, result)
        caps = out["capabilities"]
        self.assertTrue(caps["can_deploy_mcv"])
        self.assertTrue(caps["can_build_power"])
        self.assertFalse(caps["can_build_refinery"])
        self.assertTrue(caps["issues_attack"])
        self.assertTrue(caps["effective_attack"])
        self.assertEqual(out["unique_attack_targets"], 1)
        self.assertEqual(out["attacks_effective_env"], 1)

    def test_script_actions_detected(self):
        rows = [_obs(0, _mcv()),
                {"type": "action", "action": "PRODUCE",
                 "object_addresses": [], "decision_source": "fixed_economy"}]
        path = self._write(rows)
        out = analyze.analyze_episode(path, {"episode_id": "e3", "kind": "timeout",
                                             "metrics": {}})
        self.assertEqual(out["non_model_actions"], 1)
        self.assertFalse(out["capabilities"]["is_script_free_full_env"])


if __name__ == "__main__":
    unittest.main()
