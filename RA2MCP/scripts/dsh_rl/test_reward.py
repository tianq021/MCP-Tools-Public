"""Offline contracts only; these observations are never used to train PPO."""
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from dsh_rl.reward import hp_losses, reward_parts, military_intent, progress_reward


def obj(addr, hp, owner="Computer", cell=(91, 60), kind="ABSTRACT_TYPE_INFANTRY"):
    return {"addr": addr, "health": hp, "owner": owner, "type_name": "GI", "abstract": kind,
            "cell": list(cell), "cell_shrouded": False, "cloak_state": 0,
            "on_map": True, "in_limbo": False}


class TestReward(unittest.TestCase):
    def test_neutral_cannot_earn_damage_reward(self):
        before = {"visible_enemies": [obj(1, 100, "Neutral"), obj(2, 100)], "t": 0}
        after = {"visible_enemies": [obj(1, 1, "Neutral"), obj(2, 80)], "t": 2}
        self.assertEqual(hp_losses(before, after, True), 20)
        self.assertEqual(reward_parts(before, after)["opponent_hp"], 0.2)

    def test_timeout_does_not_earn_win_reward(self):
        self.assertEqual(reward_parts({}, {}, "timeout")["terminal"], 0)
        self.assertEqual(reward_parts({}, {}, "win")["terminal"], 10)
        self.assertEqual(reward_parts({}, {}, "defeat")["terminal"], -10)

    def test_disappeared_enemy_does_not_earn_fake_kill_credit(self):
        self.assertEqual(hp_losses({"visible_enemies": [obj(1, 100)]}, {}, True), 0)

    def test_own_unit_loss_is_penalized(self):
        self.assertEqual(hp_losses({"own_objects": [obj(1, 125, "<human player>")]}, {}), 125)

    def test_address_reuse_is_not_damage(self):
        a, b = obj(1, 100), obj(1, 10)
        b["type_name"] = "Another Unit"
        self.assertEqual(hp_losses({"visible_enemies": [a]}, {"visible_enemies": [b]}, True), 0)

    def test_attack_never_targets_hidden_neutral_or_aircraft(self):
        hidden = dict(obj(4, 100), cell_shrouded=True)
        obs = {"own_objects": [obj(1, 100, "<human player>")],
               "visible_enemies": [obj(2, 100, "Neutral"), hidden,
                                   obj(5, 100, kind="ABSTRACT_TYPE_AIRCRAFT")]}
        self.assertIsNone(military_intent(1, obs, []))
        obs["visible_enemies"].append(obj(9, 100))
        self.assertEqual(military_intent(1, obs, [])["target_address"], 9)

    def test_progress_uses_public_starts_and_rewards_approach(self):
        base = dict(obj(1, 750, "<human player>", (91, 58), "ABSTRACT_TYPE_BUILDING"),
                    type_name="Allied Construction Yard")
        before = {"own_objects": [base, obj(2, 125, "<human player>", (91, 62))]}
        closer = {"own_objects": [base, obj(2, 125, "<human player>", (91, 80))]}
        away = {"own_objects": [base, obj(2, 125, "<human player>", (91, 55))]}
        starts = [[92, 59], [90, 127]]
        self.assertGreater(progress_reward(before, closer, starts), 0)
        self.assertLess(progress_reward(before, away, starts), 0)


if __name__ == "__main__":
    unittest.main()
