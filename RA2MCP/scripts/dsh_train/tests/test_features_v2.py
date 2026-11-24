"""Tests for the v2 feature/action interface and dataset builder.

Run with the training environment (numpy required):
  F:\\conda\\envs\\python_mmx_LLM\\python.exe -m unittest discover -s scripts/dsh_train/tests -v
"""
import json
import pathlib
import sys
import tempfile
import unittest

TRAIN_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TRAIN_DIR))

import dataset_v2  # noqa: E402
import features_v2 as F  # noqa: E402
import spec_v2 as S  # noqa: E402


def obj(addr, type_name, abstract, cell, hp=1000, mission="Mission_Guard",
        in_limbo=False, on_map=True, cloak=0, owner="<human player>"):
    return {"addr": addr, "type_ptr": 1, "type_name": type_name,
            "abstract": abstract, "owner": owner, "health": hp,
            "cell": [cell[0], cell[1]], "xyz": [cell[0] * 256, cell[1] * 256],
            "mission": mission, "in_limbo": in_limbo, "on_map": on_map,
            "selected": False, "cell_shrouded": False, "cell_mapped": True,
            "discovered_by_current_player": True, "is_visible": True,
            "cloak_state": cloak}


def make_obs(frame=600, own=None, enemies=None, factories=None, money=8000,
             patch=None, hidden=0):
    return {
        "type": "observation", "frame": frame,
        "wall": "2026-10-29T00:00:00", "stage": "STAGE_INGAME",
        "money": money,
        "houses": [
            {"ptr": 1, "name": "<human player>", "faction": "Americans",
             "array_index": 0, "current_player": True, "is_human": True,
             "money": money, "power_output": 200, "power_drain": 50,
             "defeated": False, "is_loser": False, "is_winner": False,
             "is_game_over": False, "start_credits": 10000},
            {"ptr": 2, "name": "Computer", "faction": "Americans",
             "array_index": 1, "current_player": False, "is_human": False,
             "money": 10000, "power_output": 0, "power_drain": 0,
             "defeated": False, "is_loser": False, "is_winner": False,
             "is_game_over": False, "start_credits": 0},
        ],
        "own_objects": own or [],
        "visible_enemies": enemies or [],
        "factories": factories or [],
        "hidden_enemy_count": hidden,
        "map": {"width": 182, "height": 182, "mapped": 500, "shrouded": 15000,
                "cells": 33124},
        "map_patch": patch,
        "game_frame_present": True,
    }


def base_own():
    return [
        obj(1, "Allied Construction Yard", "ABSTRACT_TYPE_BUILDING", (91, 58)),
        obj(2, "Allied Power Plant", "ABSTRACT_TYPE_BUILDING", (93, 58), hp=750),
        obj(3, "GI", "ABSTRACT_TYPE_INFANTRY", (92, 60), hp=125,
            mission="Mission_Attack"),
        obj(4, "GI", "ABSTRACT_TYPE_INFANTRY", (92, 61), hp=125),
    ]


def patch9(center_mapped=1):
    mapped = [0] * 81
    shrouded = [1] * 81
    pas = [0] * 81
    for y in range(3, 6):
        for x in range(3, 6):
            i = y * 9 + x
            mapped[i] = center_mapped
            shrouded[i] = 0
            pas[i] = 1
    return {"center": [92, 60], "size": 9, "mapped": mapped,
            "shrouded": shrouded, "passability": pas}


class TestObsV2(unittest.TestCase):
    def test_dim_and_hidden_count_not_used(self):
        o = make_obs(own=base_own(), hidden=0)
        v1 = F.build_obs_v2(o, {})
        self.assertEqual(len(v1), S.OBS_DIM)
        o2 = dict(o, hidden_enemy_count=999)
        v2 = F.build_obs_v2(o2, {})
        self.assertEqual(v1, v2, "hidden enemy count must not leak into obs")

    def test_economy_power_factory_features(self):
        factories = [
            {"owner_ptr": 1, "object_ptr": 2, "progress_timer": 50,
             "completed": False, "on_hold": False, "queued_objects": [2, 3]},
            {"owner_ptr": 1, "object_ptr": 3, "progress_timer": 100,
             "completed": True, "on_hold": False, "queued_objects": []},
        ]
        o = make_obs(own=base_own(), factories=factories)
        v = F.build_obs_v2(o, {})
        glob = v[:S.GLOBAL_DIM]
        names = dict(zip(S.GLOBAL_NAMES_V2, glob))
        self.assertGreater(names["power_ratio"], 1.0)
        self.assertGreater(names["factory_active"], 0.0)
        self.assertGreater(names["factory_completed"], 0.0)
        self.assertGreater(names["factory_queued_n"], 0.0)

    def test_map_patch_downsample_and_features(self):
        o = make_obs(own=base_own(), patch=patch9())
        v = F.build_obs_v2(o, {})
        self.assertEqual(len(v), S.OBS_DIM)
        patch = v[-S.PATCH_DIM:]
        self.assertTrue(any(patch), "patch features should be non-zero")
        sampled = F.downward_sample_patch(patch9(), 5)
        self.assertEqual(len(sampled["mapped"]), 25)
        self.assertEqual(sampled["mapped"][12], 1)  # centre of 5x5

    def test_visibility_validation_rejects_hidden_enemy(self):
        bad = dict(obj(900, "Pill Box", "ABSTRACT_TYPE_BUILDING", (100, 80),
                       owner="Computer"), cell_shrouded=True)
        with self.assertRaises(ValueError):
            F.validate_visibility(make_obs(own=base_own(), enemies=[bad]))


class TestActionLabelV2(unittest.TestCase):
    def setUp(self):
        self.type_ids = {"Allied Barracks": 5, "GI": 6}
        self.obs = make_obs(own=base_own(), enemies=[
            obj(900, "GI", "ABSTRACT_TYPE_INFANTRY", (94, 60), hp=100,
                owner="Computer"),
            obj(901, "Pill Box", "ABSTRACT_TYPE_BUILDING", (95, 61), hp=400,
                owner="Computer"),
        ])

    def test_attack_target_slot_preserved(self):
        raw = {"type": "action", "action": "ATTACK", "object_addresses": [3],
               "target_address": 901, "error": None}
        label = S.action_label(raw, self.obs, self.type_ids, S.SLOTS_ENEMY)
        self.assertIsNotNone(label)
        self.assertEqual(label["class_id"], S.ACTION_CLASS_IDS["ATTACK"])
        self.assertEqual(label["target_slot"], 1)

    def test_attack_hidden_target_dropped(self):
        raw = {"type": "action", "action": "ATTACK", "object_addresses": [3],
               "target_address": 999, "error": None}
        self.assertIsNone(S.action_label(raw, self.obs, self.type_ids,
                                        S.SLOTS_ENEMY))

    def test_move_continuous_coordinates(self):
        near = S.action_label({"action": "MOVE",
                               "coordinates": [93 * 256, 60 * 256]},
                              self.obs, self.type_ids, S.SLOTS_ENEMY)
        far = S.action_label({"action": "MOVE",
                              "coordinates": [103 * 256, 60 * 256]},
                             self.obs, self.type_ids, S.SLOTS_ENEMY)
        self.assertEqual(near["class_id"], S.ACTION_CLASS_IDS["MOVE"])
        self.assertGreater(far["move_dx"], near["move_dx"])
        self.assertGreater(far["move_dx"], 0.05)  # not just a compass class

    def test_produce_type_preserved(self):
        b = S.action_label({"action": "PRODUCE",
                            "production_type": "Allied Barracks"},
                           self.obs, self.type_ids, S.SLOTS_ENEMY)
        g = S.action_label({"action": "PRODUCE", "production_type": "GI"},
                           self.obs, self.type_ids, S.SLOTS_ENEMY)
        self.assertEqual(b["class_id"], S.ACTION_CLASS_IDS["PRODUCE"])
        self.assertEqual(g["class_id"], S.ACTION_CLASS_IDS["PRODUCE"])
        self.assertEqual(b["produce_type_id"], 5)
        self.assertEqual(g["produce_type_id"], 6)

    def test_place_offset_relative_to_base(self):
        label = S.action_label({"action": "PLACE",
                                "coordinates": [96 * 256, 58 * 256]},
                               self.obs, self.type_ids, S.SLOTS_ENEMY)
        self.assertGreater(label["place_dx"], 0.0)
        self.assertEqual(label["place_dy"], 0.0)


def write_episode(path, episode_id, rows):
    meta = {"schema_version": "episode-v2", "episode_id": episode_id,
            "started_at": "2026-10-29T00:00:00", "policy": {"name": "test"},
            "environment": {}}
    result = {"episode_id": episode_id, "kind": "timeout", "is_win": False,
              "is_crash": False, "rows": len(rows)}
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "meta", "meta": meta}) + "\n")
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
        f.write(json.dumps({"type": "result", "result": result}) + "\n")


class TestDatasetV2Pipeline(unittest.TestCase):
    def test_build_dataset_end_to_end(self):
        with tempfile.TemporaryDirectory() as td:
            eps = pathlib.Path(td) / "episodes"
            eps.mkdir()
            visible_enemy = obj(900, "GI", "ABSTRACT_TYPE_INFANTRY", (94, 60),
                                hp=100, owner="Computer")
            hidden_enemy = dict(obj(901, "Pill Box", "ABSTRACT_TYPE_BUILDING",
                                    (120, 90), hp=400, owner="Computer"),
                                cell_shrouded=True, cell_mapped=False)
            for ep_i in range(3):
                rows = []
                for step in range(6):
                    own = base_own()
                    own[2]["health"] = max(10, 125 - step * 10)
                    rows.append(make_obs(frame=600 + step, own=own,
                                         enemies=[visible_enemy],
                                         patch=patch9()))
                    rows.append({"type": "action", "action": "MOVE",
                                 "object_addresses": [3, 4],
                                 "coordinates": [(93 + step) * 256, 60 * 256],
                                 "ack_ms": 15.0, "error": None})
                    rows.append({"type": "action", "action": "ATTACK",
                                 "object_addresses": [3],
                                 "target_address": 900 if step % 2 == 0 else 901,
                                 "ack_ms": 16.0, "error": None})
                write_episode(eps / f"ep-{ep_i}.jsonl", f"ep-{ep_i}", rows)
            info = dataset_v2.build_dataset(
                str(eps), str(pathlib.Path(td) / "ds"),
                val_fraction=0.5, noop_keep=0.5, seed=1)
            self.assertEqual(info["obs_dim"], S.OBS_DIM)
            self.assertGreater(info["samples_train"] + info["samples_val"], 8)
            # actions targeting the hidden pill box (addr 901) must be dropped
            self.assertGreater(info["dropped_hidden_target_samples"], 0)
            npz = pathlib.Path(td) / "ds" / "dataset_v2.npz"
            self.assertTrue(npz.exists())
            import numpy as np
            with np.load(npz) as z:
                self.assertEqual(z["X_train"].shape[1], S.OBS_DIM)
                self.assertEqual(len(z["y_class_train"]), len(z["X_train"]))
                self.assertEqual(len(z["y_target_train"]), len(z["X_train"]))
                self.assertNotIn(999, z["y_target_train"].tolist())


if __name__ == "__main__":
    unittest.main(verbosity=2)
