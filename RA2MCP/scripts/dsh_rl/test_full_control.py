"""Offline full-control contract tests; fixtures never enter training datasets."""
import asyncio
import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "dsh_train"))
import spec_v2
from dsh_rl import full_spec as S
from dsh_rl.full_env import RA2FullEnv, deployment_safe_reward


def tc(ptr, name, index, kind=S.KINDS[0], pre=(), cost=500, owner=1):
    return {"pointer_self": ptr, "name": name, "array_index": index, "type": kind,
            "prerequisites": list(pre), "cost": cost, "owner_flags": owner, "tech_level": 1}


CATALOG = [tc(100, "Allied Construction Yard", 0), tc(101, "Power Plant", 1, pre=[0]),
           tc(102, "Barracks", 2, pre=[-1, 0]), tc(103, "Refinery", 3, pre=[-1, 0], cost=2000),
           tc(104, "War Factory", 4, pre=[-6, 0], cost=2000),
           tc(200, "GI", 0, S.KINDS[1], pre=[-3], cost=200),
           tc(300, "Tank", 0, S.KINDS[2], pre=[-2], cost=700),
           tc(999, "Other Faction", 9, owner=2)]
GROUPS = {"power": [1], "barracks": [2], "proc": [3], "factory": [4]}


def obj(addr, ptr=100, name="Allied Construction Yard", kind="ABSTRACT_TYPE_BUILDING", cell=(91, 58)):
    return {"addr": addr, "type_ptr": ptr, "type_name": name, "abstract": kind,
            "cell": list(cell), "xyz": [cell[0] * 256, cell[1] * 256], "health": 750,
            "on_map": True, "in_limbo": False, "owner": "<human player>",
            "cell_shrouded": False, "cloak_state": 0}


def row(own=None, money=10000):
    return {"own_objects": own if own is not None else [obj(1)], "visible_enemies": [],
            "factories": [], "money": money, "t": 10, "frame": 600}


class TestFullControl(unittest.TestCase):
    def setUp(self):
        self.spec = S.FullControlSpec(CATALOG, GROUPS)

    def action(self, name):
        return S.PRODUCE_START + next(i for i, t in enumerate(self.spec.types) if t["name"] == name)

    def test_production_choices_follow_prerequisites_without_fixed_order(self):
        r = row()
        m = self.spec.masks(r, [])
        self.assertTrue(m[self.action("Power Plant")])
        self.assertFalse(m[self.action("Barracks")])
        r["own_objects"].append(obj(2, 101, "Power Plant"))
        m = self.spec.masks(r, [])
        self.assertTrue(m[self.action("Barracks")])
        self.assertTrue(m[self.action("Refinery")])
        self.assertFalse(m[self.action("GI")])
        r["own_objects"].append(obj(3, 102, "Barracks"))
        self.assertTrue(self.spec.masks(r, [])[self.action("GI")])

    def test_funds_and_foreign_faction_masked(self):
        self.assertFalse(self.spec.masks(row(money=100), [])[self.action("Power Plant")])
        self.assertNotIn("Other Faction", [t["name"] for t in self.spec.types])

    def test_native_unspecified_country_lists_are_not_all_forbidden(self):
        t = dict(CATALOG[1], required_houses=-1, forbidden_houses=-1)
        self.assertTrue(S.country_allowed(t, 1))
        self.assertFalse(S.country_allowed(dict(t, forbidden_houses=1), 1))

    def test_initial_mcv_needs_model_deploy_choice(self):
        r = row([obj(9, 400, "Allied Construction Vehicle", "ABSTRACT_TYPE_UNIT")])
        mask = self.spec.masks(r, [])
        self.assertTrue(mask[0])
        self.assertTrue(mask[1])
        self.assertFalse(mask[self.action("Power Plant")])
        self.assertEqual(self.spec.intent(1, r, [])["object_addresses"], [9])

    def test_individual_unit_and_target_slot_selected_by_action(self):
        r = row([obj(1), obj(10, 200, "GI", "ABSTRACT_TYPE_INFANTRY"),
                 obj(11, 200, "GI", "ABSTRACT_TYPE_INFANTRY")])
        r["visible_enemies"] = [dict(obj(20, 200, "GI", "ABSTRACT_TYPE_INFANTRY", (92, 60)), owner="Computer"),
                                 dict(obj(21, 300, "Tank", "ABSTRACT_TYPE_UNIT", (100, 65)), owner="Computer")]
        action = S.UNIT_START + 2 * S.UNIT_ACTIONS + 9 + 1
        command = self.spec.intent(action, r, [])
        self.assertEqual(command["object_addresses"], [11])
        self.assertEqual(command["target_address"], 21)
        r["visible_enemies"][1]["cell_shrouded"] = True
        self.assertFalse(self.spec.masks(r, [])[action])

    def test_neutral_attack_never_enabled(self):
        r = row([obj(10, 200, "GI", "ABSTRACT_TYPE_INFANTRY")])
        r["visible_enemies"] = [dict(obj(20), owner="Neutral")]
        self.assertFalse(self.spec.masks(r, [])[S.UNIT_START + 9])

    def test_group_order_controls_all_units_beyond_individual_slot_limit(self):
        r = row([obj(i, 200, "GI", "ABSTRACT_TYPE_INFANTRY") for i in range(20)])
        command = self.spec.intent(S.UNIT_START, r, [])
        self.assertEqual(len(command["object_addresses"]), 20)

    def test_placement_requires_completed_building_and_retains_sector_choice(self):
        r = row()
        self.assertFalse(self.spec.masks(r, [])[S.PLACE_START])
        p = dict(obj(9, 101, "Power Plant"), on_map=False, in_limbo=True)
        r["own_objects"].append(p)
        r["factories"] = [{"object_ptr": 9, "completed": True}]
        east = self.spec.intent(S.PLACE_START, r, [])
        west = self.spec.intent(S.PLACE_START + 4, r, [])
        self.assertNotEqual(east["anchor"], west["anchor"])
        self.assertEqual(east["building_address"], 9)

    def test_manifest_ignores_relocated_native_pointers(self):
        relocated = [dict(t, pointer_self=t["pointer_self"] + 10000) for t in CATALOG]
        self.assertEqual(self.spec.manifest()["catalog_sha256"], S.FullControlSpec(relocated, GROUPS).manifest()["catalog_sha256"])

    def test_deployment_not_penalized_as_unit_death(self):
        mcv = obj(9, 400, "Allied Construction Vehicle", "ABSTRACT_TYPE_UNIT")
        mcv["health"] = 1000
        before, after = row([mcv]), row([obj(10)])
        self.assertEqual(deployment_safe_reward(before, after)["own_hp"], 0)
        self.assertEqual(deployment_safe_reward(before, row([]))["own_hp"], -10)

    def test_full_mode_never_calls_fixed_economy_or_warmup_commands(self):
        with tempfile.TemporaryDirectory() as td:
            env = RA2FullEnv(td)
            env.current = row([obj(9, 400, "Allied Construction Vehicle", "ABSTRACT_TYPE_UNIT")])
            env._commands = AsyncMock()
            env._run(env._warmup_controller())
            env._run(env._economy())
            env._commands.assert_not_called()
            env.close()

    def test_enemy_factories_and_economy_do_not_reach_policy(self):
        with tempfile.TemporaryDirectory() as td:
            env = RA2FullEnv(td)
            env.hero = SimpleNamespace(self=1)
            raw = {"factories": [{"owner_ptr": 1}, {"owner_ptr": 2}],
                   "houses": [{"ptr": 1, "money": 100}, {"ptr": 2, "money": 99999}]}
            clean = env._filter_observation(raw)
            self.assertEqual(clean["factories"], [{"owner_ptr": 1}])
            self.assertEqual(clean["houses"], [{"ptr": 1, "money": 100}])
            env.close()

    def test_extra_observation_and_masks_have_fixed_contract(self):
        r = row()
        x = self.spec.features(r, [])
        self.assertEqual(x.shape, (S.EXTRA_DIM,))
        self.assertEqual(self.spec.masks(r, []).shape, (S.ACTION_N,))

    def test_enemy_spawn_is_far_public_start_anchored_to_base(self):
        starts = [[92, 59], [90, 127]]
        r = row()
        self.assertEqual(S.enemy_start(r, starts), [90, 127])
        advancing = row([obj(10, 200, "GI", "ABSTRACT_TYPE_INFANTRY", (90, 120))]
                        + r["own_objects"])
        self.assertEqual(S.enemy_start(advancing, starts), [90, 127])  # base-anchored, not army-anchored
        self.assertIsNone(S.enemy_start(r, []))

    def test_local_move_legs_are_longer_and_not_limited_to_explored_cells(self):
        map_soa = SimpleNamespace(map_width=128, map_height=128,
                                  mapped=bytearray(128 * 128), passability=bytearray(128 * 128))
        r = row([obj(10, 200, "GI", "ABSTRACT_TYPE_INFANTRY", (60, 60))])
        command = self.spec.intent(S.UNIT_START + 1, r, [], map_soa)  # east
        self.assertEqual(command["action"], "MOVE")
        self.assertEqual(command["coordinates"], [int((60 + S.MOVE_CELLS) * 256 + 128),
                                                  int(60 * 256 + 128)])
        self.assertEqual(S.MOVE_CELLS, 10)
        edge = row([obj(10, 200, "GI", "ABSTRACT_TYPE_INFANTRY", (125, 60))])
        self.assertFalse(self.spec.masks(edge, [], map_soa)[S.UNIT_START + 1])
        # without a map snapshot local moves are masked, not blindly sent
        self.assertFalse(self.spec.masks(r, [], None)[S.UNIT_START + 1])

    def test_unit_features_include_approach_to_enemy_spawn(self):
        starts = [[92, 59], [90, 127]]
        r = row([obj(1), obj(10, 200, "GI", "ABSTRACT_TYPE_INFANTRY", (90, 120))])
        x = self.spec.features(r, starts)
        unit_block = x[S.MAX_TYPES * 10: S.MAX_TYPES * 10 + S.UNIT_FEATURES]
        self.assertEqual(len(unit_block), 11)
        self.assertGreater(unit_block[8], 0)          # distance to enemy spawn (normalised)
        self.assertAlmostEqual(unit_block[9], 0.0, places=5)  # dx
        self.assertGreater(unit_block[10], 0)         # dy

    def test_history_features_mark_previous_action(self):
        with tempfile.TemporaryDirectory() as td:
            env = RA2FullEnv(td)
            try:
                self.assertEqual(env.observation_space.shape,
                                 (spec_v2.OBS_DIM + S.EXTRA_DIM + S.HISTORY_DIM,))
                self.assertEqual(len(env._history_features()), S.HISTORY_DIM)
                self.assertEqual(env._history_features(), [0.0] * S.HISTORY_DIM)
                env.last_action = S.UNIT_START + 3  # local move north-east
                self.assertEqual(env._history_features()[0], 1.0)
                self.assertEqual(env._history_features()[5 + 2], 1.0)
                env.last_action = S.UNIT_START + 9  # attack slot 0
                self.assertEqual(env._history_features()[2], 1.0)
                env.last_action = S.UNIT_START + 17  # scout
                self.assertEqual(env._history_features()[1], 1.0)
                env.last_action = S.PRODUCE_START + 5
                self.assertEqual(env._history_features()[4], 1.0)
            finally:
                env.close()


if __name__ == "__main__":
    unittest.main()
