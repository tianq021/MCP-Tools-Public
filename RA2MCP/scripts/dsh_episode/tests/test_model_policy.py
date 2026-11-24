"""Offline tests for ModelPolicy (v2 multi-head -> native intents + fallback).

No game/network required: a fake model object returns predictions.
"""
import pathlib
import sys
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "dsh_train"))

import spec_v2 as S  # noqa: E402
from dsh_episode import model_policy  # noqa: E402


def obj(addr, type_name, abstract, cell, hp=1000, mission="Mission_Guard"):
    return {"addr": addr, "type_ptr": 1, "type_name": type_name,
            "abstract": abstract, "owner": "<human player>", "health": hp,
            "cell": [cell[0], cell[1]], "xyz": [cell[0] * 256, cell[1] * 256],
            "mission": mission, "in_limbo": False, "on_map": True,
            "selected": False, "cell_shrouded": False, "cell_mapped": True,
            "discovered_by_current_player": True, "is_visible": True,
            "cloak_state": 0}


def obs_row(enemies=None, own=None, factories=None, base=(91, 58)):
    own = own or [
        obj(1, "Allied Construction Yard", "ABSTRACT_TYPE_BUILDING", base),
        obj(3, "GI", "ABSTRACT_TYPE_INFANTRY", (base[0], base[1] + 2)),
    ]
    return {
        "type": "observation", "frame": 600, "wall": "x", "stage": "STAGE_INGAME",
        "money": 8000,
        "houses": [{"ptr": 1, "name": "<human player>", "faction": "A",
                    "array_index": 0, "current_player": True, "is_human": True,
                    "money": 8000, "power_output": 200, "power_drain": 50,
                    "defeated": False, "is_loser": False, "is_winner": False,
                    "is_game_over": False, "start_credits": 10000},
                   {"ptr": 2, "name": "Computer", "faction": "A", "array_index": 1,
                    "current_player": False, "is_human": False, "money": 0,
                    "power_output": 0, "power_drain": 0, "defeated": False,
                    "is_loser": False, "is_winner": False, "is_game_over": False,
                    "start_credits": 0}],
        "own_objects": own,
        "visible_enemies": enemies or [],
        "factories": factories or [],
        "hidden_enemy_count": 0,
        "map": {"width": 182, "height": 182, "mapped": 400, "shrouded": 16000,
                "cells": 33124},
        "map_patch": None,
    }


def enemy(addr, cell, owner="Computer"):
    e = obj(addr, "GI", "ABSTRACT_TYPE_INFANTRY", cell, hp=100)
    e["owner"] = owner
    e["cell_shrouded"] = False
    return e


class FakeModel:
    def __init__(self, pred, enabled=True, catalog=None):
        self.enabled = enabled
        self._pred = pred
        self.meta = {"data_info": {"type_catalog": catalog or ["Allied Barracks", "GI"],
                                   "map_width": 182, "map_height": 182}}

    def act(self, features):
        return dict(self._pred, enabled=True, confidence=self._pred.get("confidence", 0.9))


def make_ctx(obs, t=10.0):
    return {"t": t, "frame": 600,
            "own_objects": obs["own_objects"],
            "visible_enemies": obs["visible_enemies"],
            "factories": obs["factories"],
            "hero": {"self": 1, "name": "<human player>", "money": 8000},
            "map": obs["map"], "observation": obs,
            "type_catalog": [{"pointer_self": 102, "name": "Allied Barracks",
                              "array_index": 3, "type": "BUILDINGTYPE"},
                             {"pointer_self": 103, "name": "GI",
                              "array_index": 0, "type": "INFANTRYTYPE"}],
            "last_acks": []}


class TestModelPolicy(unittest.TestCase):
    def test_attack_target_slot_resolves_to_visible_enemy(self):
        obs = obs_row(enemies=[enemy(900, (92, 60)), enemy(901, (99, 80))])
        model = FakeModel({"class_id": S.ACTION_CLASS_IDS["ATTACK"],
                           "target_slot": 1, "move_dx": 0.0, "move_dy": 0.0,
                           "produce_type_id": -1, "place_dx": 0.0, "place_dy": 0.0,
                           "confidence": 0.9})
        p = model_policy.ModelPolicy(model, make_ctx(obs)["type_catalog"])
        intents = p.step(make_ctx(obs))
        atk = [i for i in intents if i["action"] == "ATTACK"]
        self.assertTrue(atk)
        self.assertEqual(atk[0]["target_address"], 901)  # slot 1 = farther enemy

    def test_low_confidence_falls_back_to_rule(self):
        obs = obs_row(enemies=[enemy(900, (92, 60))])
        model = FakeModel({"class_id": S.ACTION_CLASS_IDS["ATTACK"],
                           "target_slot": 0, "confidence": 0.1})
        p = model_policy.ModelPolicy(model, make_ctx(obs)["type_catalog"],
                                     confidence_threshold=0.35)
        intents = p.step(make_ctx(obs))
        self.assertEqual(p.stats["model_actions"], 0)
        self.assertEqual(p.stats["low_confidence"], 1)
        # rule fallback may legitimately return [] or its own orders
        self.assertIsInstance(intents, list)

    def test_hidden_target_never_attacked(self):
        obs = obs_row(enemies=[enemy(900, (92, 60))])
        model = FakeModel({"class_id": S.ACTION_CLASS_IDS["ATTACK"],
                           "target_slot": 0, "confidence": 0.99})
        # tamper: the resolved slot points to a hidden enemy (defence in depth)
        patched = dict(obs, visible_enemies=[dict(obs["visible_enemies"][0],
                                                  cell_shrouded=True)])
        p = model_policy.ModelPolicy(model, make_ctx(obs)["type_catalog"])
        intents = p.step(make_ctx(patched))
        self.assertFalse(any(i["action"] == "ATTACK" for i in intents))
        self.assertGreaterEqual(p.violations, 1)

    def test_produce_maps_type_id_to_catalog_pointer(self):
        obs = obs_row()
        model = FakeModel({"class_id": S.ACTION_CLASS_IDS["PRODUCE"],
                           "target_slot": -1, "move_dx": 0.0, "move_dy": 0.0,
                           "produce_type_id": 0,  # catalog[0] = Allied Barracks
                           "place_dx": 0.0, "place_dy": 0.0, "confidence": 0.9})
        p = model_policy.ModelPolicy(model, make_ctx(obs)["type_catalog"])
        intents = p.step(make_ctx(obs))
        prod = [i for i in intents if i["action"] == "PRODUCE"]
        self.assertTrue(prod)
        self.assertEqual(prod[0]["production_type"], "Allied Barracks")
        self.assertEqual(prod[0]["production_type_ptr"], 102)

    def test_move_uses_continuous_offsets(self):
        obs = obs_row()
        model = FakeModel({"class_id": S.ACTION_CLASS_IDS["MOVE"],
                           "target_slot": -1, "move_dx": 0.2, "move_dy": 0.1,
                           "produce_type_id": -1, "place_dx": 0.0, "place_dy": 0.0,
                           "confidence": 0.9})
        p = model_policy.ModelPolicy(model, make_ctx(obs)["type_catalog"])
        intents = p.step(make_ctx(obs))
        mv = [i for i in intents if i["action"] == "MOVE"]
        self.assertTrue(mv)
        self.assertGreater(mv[0]["coordinates"][0], 90 * 256)

    def test_place_uses_pending_building(self):
        pending = obj(2, "Allied Barracks", "ABSTRACT_TYPE_BUILDING", (0, 0))
        pending["in_limbo"] = True
        pending["on_map"] = False
        obs = obs_row(own=[obj(1, "Allied Construction Yard",
                               "ABSTRACT_TYPE_BUILDING", (91, 58)), pending],
                      factories=[{"owner_ptr": 1, "object_ptr": 2,
                                  "progress_timer": 100, "completed": True,
                                  "on_hold": False, "queued_objects": []}])
        obs["type_catalog_for_policy"] = None
        model = FakeModel({"class_id": S.ACTION_CLASS_IDS["PLACE"],
                           "target_slot": -1, "move_dx": 0.0, "move_dy": 0.0,
                           "produce_type_id": -1, "place_dx": 0.2, "place_dy": 0.0,
                           "confidence": 0.9})
        p = model_policy.ModelPolicy(model, make_ctx(obs)["type_catalog"])
        intents = p.step(make_ctx(obs))
        places = [i for i in intents if i["action"] == "PLACE"]
        self.assertTrue(places)
        self.assertEqual(places[0]["building_address"], 2)

    def test_disabled_model_returns_rule_intents(self):
        obs = obs_row()
        model = FakeModel({"class_id": 0}, enabled=False)
        p = model_policy.ModelPolicy(model, make_ctx(obs)["type_catalog"])
        self.assertIsInstance(p.step(make_ctx(obs)), list)


if __name__ == "__main__":
    unittest.main(verbosity=2)
