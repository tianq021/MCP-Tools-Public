"""Offline tests for the episode schema/observation/policy (no game required).

Run:
  E:\\RA2MCP\\.venv\\Scripts\\python.exe -m unittest discover -s scripts/dsh_episode/tests -v
"""
import json
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace

SCRIPTS = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SCRIPTS))

from dsh_episode import actions, lockfile, obs, policy, schema  # noqa: E402
from ra2yrproto import ra2yr  # noqa: E402


def fake_obj(addr, type_ptr, type_name, abstract, owner, cell, hp=100,
             cell_shrouded=False, cloak_state=0, mission=ra2yr.Mission.Mission_Guard):
    return SimpleNamespace(
        pointer_self=addr, pointer_technotypeclass=type_ptr, object_type=abstract,
        pointer_house=owner, health=hp, in_limbo=False, on_map=True, selected=False,
        cell_shrouded=cell_shrouded, cell_mapped=not cell_shrouded,
        discovered_by_current_player=True, is_visible=True, cloak_state=cloak_state,
        current_mission=mission,
        coordinates=SimpleNamespace(x=cell[0] * 256 + 128, y=cell[1] * 256 + 128, z=0),
        deployed=False)


def fake_state(objs, hero_self=1, frame=100):
    return SimpleNamespace(
        current_frame=frame, stage=ra2yr.LoadStage.STAGE_INGAME, objects=objs,
        factories=[], houses=[
            SimpleNamespace(self=hero_self, name="<human player>", faction="Americans",
                            array_index=0, current_player=True, is_human_player=True,
                            money=10000, power_output=200, power_drain=10,
                            defeated=False, is_loser=False, is_winner=False,
                            is_game_over=False, start_credits=10000),
            SimpleNamespace(self=2, name="Computer", faction="Americans",
                            array_index=1, current_player=False, is_human_player=False,
                            money=10000, power_output=0, power_drain=0,
                            defeated=False, is_loser=False, is_winner=False,
                            is_game_over=False, start_credits=0),
        ])


def fake_map(cells=4):
    return SimpleNamespace(map_width=4, map_height=1,
                           mapped=[True] * cells, shrouded=[False] * cells,
                           passability=[1] * cells)


def make_meta(ep_id="test-1"):
    return schema.make_meta(ep_id, "rule-v2", {"infantry_cap": 8},
                            {"spawn_ini_sha256": "x"})


def obs_rec(frame=1, wall="2026-10-29T00:00:00", own=None, enemies=None):
    return schema.make_observation(
        frame=frame, wall=wall, stage="STAGE_INGAME", money=5000,
        houses=[], own_objects=own or [], visible_enemies=enemies or [],
        factories=[], hidden_enemy_count=0,
        map_summary={"width": 4, "height": 1, "mapped": 4, "shrouded": 0, "cells": 4})


class TestWriterEncodingAndSchema(unittest.TestCase):
    def test_utf8_no_bom_and_validation(self):
        with tempfile.TemporaryDirectory() as td:
            w = schema.EpisodeWriter(td, "ep-1", make_meta())
            w.add_row(obs_rec())
            w.add_row(schema.make_action("MOVE", [11], ack_ms=15.0,
                                         coordinates=[1024, 2048]))
            w.add_row({"type": "note", "wall": "2026-10-29T00:00:01",
                       "message": "中文备注：编码测试"})
            res = {"kind": "timeout", "detail": "test", "is_win": False,
                   "is_crash": False, "rows": 3}
            path = w.finalize(res)
            raw = path.read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), "JSONL must not have BOM")
            text = raw.decode("utf-8")  # must not raise
            self.assertIn("中文备注", text)
            self.assertEqual(schema.validate_episode_file(path), [])
            ep = schema.read_episode(path)
            self.assertEqual(ep["meta"]["schema_version"], schema.SCHEMA_VERSION)
            self.assertEqual(ep["result"]["kind"], "timeout")
            self.assertEqual(len([r for r in ep["rows"] if r.get("type") == "observation"]), 1)


class TestWriterResume(unittest.TestCase):
    def test_abort_then_resume_appends(self):
        with tempfile.TemporaryDirectory() as td:
            w = schema.EpisodeWriter(td, "ep-2", make_meta("ep-2"),
                                     checkpoint_every=1)
            for i in range(5):
                w.add_row(obs_rec(frame=i))
            w.abort()  # simulate hard kill: no result line, only .part + ckpt
            self.assertTrue((pathlib.Path(td) / "ep-2.jsonl.part").exists())
            self.assertFalse((pathlib.Path(td) / "ep-2.jsonl").exists())

            w2 = schema.EpisodeWriter(td, "ep-2", make_meta("ep-2"), resume=True,
                                      checkpoint_every=1)
            self.assertEqual(w2.row_count, 5)
            w2.add_row(obs_rec(frame=5))
            w2.add_row(schema.make_action("DEPLOY", [9], ack_ms=16.0))
            path = w2.finalize({"kind": "interrupted", "detail": "resumed",
                                "is_win": False, "is_crash": False})
            ep = schema.read_episode(path)
            self.assertEqual(len(ep["rows"]), 7)
            self.assertEqual(ep["result"]["kind"], "interrupted")


class TestSchemaValidationCatchesErrors(unittest.TestCase):
    def test_missing_fields(self):
        bad_obs = {"type": "observation", "frame": 1}
        self.assertTrue(schema.validate_observation(bad_obs))
        bad_action = {"type": "action", "action": "MOVE",
                      "object_addresses": [1], "error": None}
        self.assertIn("MOVE missing coordinates", schema.validate_action(bad_action))
        bad_target = {"type": "action", "action": "ATTACK",
                      "object_addresses": [1], "error": None}
        self.assertIn("ATTACK missing target_address",
                      schema.validate_action(bad_target))

    def test_visibility_contract_violation_detected(self):
        hidden = {"addr": 5, "type_name": "GI", "owner": "Computer", "health": 100,
                  "cell": [1, 1], "cell_shrouded": True, "cloak_state": 0}
        rec = obs_rec(enemies=[hidden])
        errs = schema.validate_observation(rec)
        self.assertTrue(any("not visible" in e for e in errs))

    def test_final_required(self):
        with tempfile.TemporaryDirectory() as td:
            w = schema.EpisodeWriter(td, "ep-3", make_meta("ep-3"), checkpoint_every=1)
            w.add_row(obs_rec())
            w.abort()
            part = pathlib.Path(td) / "ep-3.jsonl.part"
            errs = schema.validate_episode_file(part, require_final=True)
            self.assertTrue(any("missing result" in e for e in errs))


class TestActionRecordsKeepFullParams(unittest.TestCase):
    def test_actions(self):
        cases = [
            schema.make_action("MOVE", [1, 2], ack_ms=15.5, coordinates=[1024, 2048]),
            schema.make_action("ATTACK", [1], ack_ms=16.0, target_address=777,
                               target_type="GI", coordinates=[2560, 3072]),
            schema.make_action("PRODUCE", [], ack_ms=15.0,
                               production_type="Allied Barracks",
                               production_action="BEGIN"),
            schema.make_action("PLACE", [88], ack_ms=17.0, building_address=88,
                               coordinates=[2304, 1536],
                               production_type="Allied Barracks"),
        ]
        for rec in cases:
            self.assertEqual(schema.validate_action(rec), [])
        self.assertEqual(cases[0]["coordinates"], [1024, 2048])
        self.assertEqual(cases[1]["target_address"], 777)
        self.assertEqual(cases[2]["production_type"], "Allied Barracks")
        self.assertEqual(cases[3]["coordinates"], [2304, 1536])


class TestOutcomeClassification(unittest.TestCase):
    def test_no_win_on_crash(self):
        for code in (1, -1073741819, 3221225477):
            r = schema.classify_outcome(proc_exited=True, exit_code=code,
                                        human_is_winner=True,
                                        enemies_all_defeated=True)
            self.assertEqual(r["kind"], "crash")
            self.assertFalse(r["is_win"])

    def test_defeat(self):
        r = schema.classify_outcome(human_defeated=True)
        self.assertEqual(r["kind"], "defeat")
        self.assertFalse(r["is_win"])

    def test_clean_win_exit(self):
        r = schema.classify_outcome(proc_exited=True, exit_code=0,
                                    stage_name="STAGE_EXIT_GAME",
                                    human_is_winner=True)
        self.assertEqual(r["kind"], "win")
        self.assertTrue(r["is_win"])

    def test_clean_exit_without_result_is_game_end(self):
        r = schema.classify_outcome(proc_exited=True, exit_code=0,
                                    stage_name="STAGE_EXIT_GAME")
        self.assertEqual(r["kind"], "game_end")
        self.assertFalse(r["is_win"])

    def test_crash_without_clean_stage(self):
        r = schema.classify_outcome(proc_exited=True, exit_code=0, stage_name=None)
        self.assertEqual(r["kind"], "crash")

    def test_win_ingame(self):
        r = schema.classify_outcome(stage_name="STAGE_INGAME",
                                    enemies_all_defeated=True)
        self.assertEqual(r["kind"], "win")

    def test_timeout(self):
        r = schema.classify_outcome(stage_name="STAGE_INGAME", elapsed_s=301,
                                    max_seconds=300)
        self.assertEqual(r["kind"], "timeout")

    def test_interrupted(self):
        self.assertEqual(schema.classify_outcome(interrupted=True)["kind"],
                         "interrupted")

    def test_win_beats_timeout_at_budget_boundary(self):
        # V4 regression: victory inside the final budget second must be a win,
        # never a timeout (old ordering checked timeout first).
        r = schema.classify_outcome(stage_name="STAGE_INGAME", elapsed_s=299.5,
                                    max_seconds=300, human_is_winner=True)
        self.assertEqual(r["kind"], "win")
        self.assertTrue(r["is_win"])
        r = schema.classify_outcome(stage_name="STAGE_INGAME", elapsed_s=299.5,
                                    max_seconds=300, enemies_all_defeated=True)
        self.assertEqual(r["kind"], "win")

    def test_win_at_budget_without_any_flag_is_timeout(self):
        r = schema.classify_outcome(stage_name="STAGE_INGAME", elapsed_s=299.5,
                                    max_seconds=300)
        self.assertEqual(r["kind"], "timeout")

    def test_clean_exit_win_is_checked_before_timeout(self):
        r = schema.classify_outcome(proc_exited=True, exit_code=0,
                                    stage_name="STAGE_EXIT_GAME",
                                    elapsed_s=150.0, max_seconds=300,
                                    human_is_winner=True)
        self.assertEqual(r["kind"], "win")
        # Own teardown at the budget (clean exit, no winner) stays a timeout.
        r = schema.classify_outcome(proc_exited=True, exit_code=0,
                                    stage_name="STAGE_EXIT_GAME",
                                    elapsed_s=299.5, max_seconds=300)
        self.assertEqual(r["kind"], "timeout")

    def test_game_over_without_winner_is_game_end_not_win(self):
        r = schema.classify_outcome(stage_name="STAGE_INGAME",
                                    human_is_game_over=True)
        self.assertEqual(r["kind"], "game_end")
        self.assertFalse(r["is_win"])

    def test_enemies_all_defeated_wins_even_without_is_winner_flag(self):
        # Long games may not set HouseClass::IsWinner; all enemy houses defeated
        # is the reliable fallback used by the RL feedback path.
        r = schema.classify_outcome(stage_name="STAGE_INGAME",
                                    elapsed_s=100, max_seconds=300,
                                    enemies_all_defeated=True)
        self.assertEqual(r["kind"], "win")
        self.assertTrue(r["is_win"])


class TestObservationVisibility(unittest.TestCase):
    def test_hidden_and_cloaked_not_in_visible_enemies(self):
        hero = ra2yr.AbstractType.ABSTRACT_TYPE_BUILDING
        objs = [
            fake_obj(1, 10, "Allied Construction Yard", hero, 1, (91, 58)),
            fake_obj(201, 11, "GI", ra2yr.AbstractType.ABSTRACT_TYPE_INFANTRY, 2,
                     (92, 59), cell_shrouded=False),
            fake_obj(202, 12, "Pill Box", hero, 2, (100, 80), cell_shrouded=True),
            fake_obj(203, 13, "GI", ra2yr.AbstractType.ABSTRACT_TYPE_INFANTRY, 2,
                     (93, 60), cell_shrouded=False, cloak_state=2),
        ]
        st = fake_state(objs)
        types = {10: "Allied Construction Yard", 11: "GI", 12: "Pill Box", 13: "GI"}
        houses = {1: "<human player>", 2: "Computer"}
        rec = obs.capture_observation(st, 1, types, houses, fake_map(),
                                      "2026-10-29T00:00:00")
        errs = schema.validate_observation(rec)
        self.assertEqual(errs, [])
        addrs = {e["addr"] for e in rec["visible_enemies"]}
        self.assertEqual(addrs, {201})              # hidden + cloaked filtered
        self.assertEqual(rec["hidden_enemy_count"], 2)
        for e in rec["visible_enemies"]:
            self.assertIs(e["cell_shrouded"], False)
        self.assertNotIn(202, addrs)
        self.assertNotIn(203, addrs)

    def test_map_patch(self):
        m = fake_map(cells=9)
        patch = obs.map_patch_from_soa(m, (1, 0), size=3)
        self.assertEqual(len(patch["mapped"]), 9)
        self.assertEqual(len(patch["passability"]), 9)


class TestPolicyVisibilityHygiene(unittest.TestCase):
    def make_ctx(self, enemies):
        own = [
            {"addr": 1, "type_name": "Allied Construction Yard",
             "abstract": "ABSTRACT_TYPE_BUILDING", "health": 1000,
             "cell": [91, 58], "xyz": [23360, 14976]},
            {"addr": 2, "type_name": "GI", "abstract": "ABSTRACT_TYPE_INFANTRY",
             "health": 125, "cell": [92, 59], "xyz": [23680, 15232]},
            {"addr": 3, "type_name": "GI", "abstract": "ABSTRACT_TYPE_INFANTRY",
             "health": 125, "cell": [92, 60], "xyz": [23680, 15488]},
        ]
        return {"t": 10.0, "frame": 600, "own_objects": own,
                "visible_enemies": enemies, "factories": [],
                "hero": {"self": 1, "name": "<human player>", "money": 5000},
                "map": {"mapped": 300}}

    def test_never_attacks_hidden_target(self):
        hidden_enemy = {"addr": 900, "type_name": "Pill Box", "owner": "Computer",
                        "health": 400, "cell": [93, 61], "xyz": [23808, 15616],
                        "cell_shrouded": True, "cloak_state": 0}
        p = policy.RulePolicy({"build_order": []}, [
            {"pointer_self": 11, "name": "GI", "array_index": 0, "type": "INFANTRYTYPE"}])
        intents = p.step(self.make_ctx([hidden_enemy]))
        self.assertFalse(any(i["action"] == "ATTACK" for i in intents))
        self.assertEqual(p.violations, 0)

    def test_attacks_visible_target_in_range(self):
        visible_enemy = {"addr": 901, "type_name": "GI", "owner": "Computer",
                         "health": 125, "cell": [93, 61], "xyz": [23808, 15616],
                         "cell_shrouded": False, "cloak_state": 0}
        p = policy.RulePolicy({"build_order": []}, [])
        intents = p.step(self.make_ctx([visible_enemy]))
        self.assertTrue(any(i["action"] == "ATTACK" and i["target_address"] == 901
                            for i in intents))


class TestPolicyConstructionPhases(unittest.TestCase):
    """Regression tests for the phase-2 bug: an unplaced building was counted as done."""

    TYPES = [
        {"pointer_self": 101, "name": "Allied Power Plant", "array_index": 0,
         "type": "BUILDINGTYPE"},
        {"pointer_self": 102, "name": "Allied Barracks", "array_index": 3,
         "type": "BUILDINGTYPE"},
        {"pointer_self": 103, "name": "GI", "array_index": 0, "type": "INFANTRYTYPE"},
    ]

    def ctx(self, buildings, factories=None, acks=None):
        return {"t": 20.0, "frame": 1200, "own_objects": buildings,
                "visible_enemies": [], "factories": factories or [],
                "hero": {"self": 1, "name": "<human player>", "money": 8000},
                "map": {"mapped": 300}, "last_acks": acks or []}

    def cy(self):
        return {"addr": 1, "type_name": "Allied Construction Yard",
                "type_ptr": 100, "abstract": "ABSTRACT_TYPE_BUILDING",
                "health": 1000, "cell": [91, 58], "xyz": [23360, 14976],
                "on_map": True, "in_limbo": False}

    def pending_pp(self):
        return {"addr": 2, "type_name": "Allied Power Plant",
                "type_ptr": 101, "abstract": "ABSTRACT_TYPE_BUILDING",
                "health": 1000, "cell": [0, 0], "xyz": [0, 0],
                "on_map": False, "in_limbo": True}

    def test_unplaced_building_gets_placed_before_next_one(self):
        p = policy.RulePolicy({}, self.TYPES)
        buildings = [self.cy(), self.pending_pp()]
        factories = [{"owner_ptr": 1, "object_ptr": 2, "progress_timer": 100,
                      "completed": True, "on_hold": False, "queued_objects": []}]
        intents = p.step(self.ctx(buildings, factories))
        self.assertTrue(any(i["action"] == "PLACE" and i["building_address"] == 2
                            for i in intents), intents)
        self.assertFalse(any(i["action"] == "PRODUCE" for i in intents))

    def test_produces_next_building_when_none_pending(self):
        p = policy.RulePolicy({}, self.TYPES)
        intents = p.step(self.ctx([self.cy()]))
        self.assertTrue(any(i["action"] == "PRODUCE" and
                            i["production_type"] == "Allied Power Plant"
                            for i in intents), intents)

    def test_waits_after_successful_produce_ack(self):
        p = policy.RulePolicy({}, self.TYPES)
        acks = [{"action": "PRODUCE", "production_type": "Allied Power Plant",
                 "error": None, "t": 19.0}]
        intents = p.step(self.ctx([self.cy()], acks=acks))
        self.assertFalse(any(i["action"] == "PRODUCE" for i in intents), intents)

    def test_placed_power_plant_moves_to_barracks(self):
        pp_placed = dict(self.pending_pp(), on_map=True, in_limbo=False,
                         cell=[93, 58], xyz=[23808, 14976])
        p = policy.RulePolicy({}, self.TYPES)
        intents = p.step(self.ctx([self.cy(), pp_placed]))
        self.assertTrue(any(i["action"] == "PRODUCE" and
                            i["production_type"] == "Allied Barracks"
                            for i in intents), intents)


class TestLivenessGuard(unittest.TestCase):
    """Regression tests for `native_error: object not found` on stale addresses."""

    def build_state(self):
        alive = fake_obj(11, 11, "GI", ra2yr.AbstractType.ABSTRACT_TYPE_INFANTRY, 1,
                         (92, 59))
        dead = fake_obj(12, 11, "GI", ra2yr.AbstractType.ABSTRACT_TYPE_INFANTRY, 1,
                        (92, 59), hp=0)
        pending = fake_obj(13, 12, "Allied Power Plant",
                           ra2yr.AbstractType.ABSTRACT_TYPE_BUILDING, 1, (0, 0),
                           hp=800)
        pending.in_limbo = True
        return SimpleNamespace(objects=[alive, dead, pending])

    def test_live_addresses(self):
        live = actions.live_addresses(self.build_state())
        self.assertEqual(live["techno"], {11})
        self.assertEqual(live["all"], {11, 13})

    def test_stale_deploy_is_fatal(self):
        live = actions.live_addresses(self.build_state())
        intent = {"action": "DEPLOY", "object_addresses": [12]}
        out, stat = actions.guard_intent(intent, live)
        self.assertTrue(stat["fatal"])
        self.assertEqual(stat["reason"], "no_live_actors")

    def test_mixed_move_filters_dropped(self):
        live = actions.live_addresses(self.build_state())
        out, stat = actions.guard_intent(
            {"action": "MOVE", "object_addresses": [11, 12],
             "coordinates": [1000, 1000]}, live)
        self.assertEqual(out["object_addresses"], [11])
        self.assertEqual(stat["dropped"], [12])
        self.assertFalse(stat["fatal"])

    def test_stale_attack_target_is_fatal(self):
        live = actions.live_addresses(self.build_state())
        out, stat = actions.guard_intent(
            {"action": "ATTACK", "object_addresses": [11], "target_address": 999},
            live)
        self.assertTrue(stat["fatal"])
        self.assertEqual(stat["reason"], "stale_target")

    def test_place_limbo_building_is_allowed(self):
        live = actions.live_addresses(self.build_state())
        out, stat = actions.guard_intent(
            {"action": "PLACE", "building_address": 13,
             "coordinates": [1000, 1000]}, live)
        self.assertFalse(stat["fatal"])

    def test_produce_needs_no_liveness(self):
        live = actions.live_addresses(self.build_state())
        out, stat = actions.guard_intent(
            {"action": "PRODUCE", "object_addresses": []}, live)
        self.assertFalse(stat["fatal"])


class TestSkippedOrderEvents(unittest.TestCase):
    """Stale orders are recorded as events, not as malformed action rows."""

    def test_order_skipped_event_is_valid_and_finalizes(self):
        with tempfile.TemporaryDirectory() as td:
            w = schema.EpisodeWriter(td, "ep-evt", make_meta("ep-evt"))
            w.add_row(obs_rec())
            w.add_row(schema.make_event("order_skipped", action="MOVE",
                                        reason="no_live_actors",
                                        object_addresses=[123],
                                        target_address=None))
            path = w.finalize({"kind": "timeout", "detail": "t", "is_win": False,
                               "is_crash": False})
            self.assertEqual(schema.validate_episode_file(path), [])
            ep = schema.read_episode(path)
            evs = [r for r in ep["rows"] if r.get("type") == "event"]
            self.assertEqual(evs[0]["event"], "order_skipped")
            self.assertEqual(evs[0]["reason"], "no_live_actors")


def make_damage_obs(frame, own=(), enemies=(), t=None):
    """Minimal observation row accepted by schema.validate_observation."""
    own_recs = [{"addr": a, "type_ptr": 1, "type_name": tn,
                 "abstract": ab, "owner": "<human player>", "health": hp,
                 "cell": [91, 58], "xyz": [91 * 256, 58 * 256],
                 "mission": "Mission_Guard", "in_limbo": limbo, "on_map": True,
                 "selected": False, "cell_shrouded": False, "cell_mapped": True,
                 "discovered_by_current_player": True, "is_visible": True,
                 "cloak_state": 0}
                for (a, tn, ab, hp, limbo) in own]
    enemy_recs = [{"addr": a, "type_ptr": 2, "type_name": tn,
                   "abstract": ab, "owner": "Computer", "health": hp,
                   "cell": [92, 59], "xyz": [92 * 256, 59 * 256],
                   "mission": "Mission_Guard", "in_limbo": False, "on_map": True,
                   "selected": False, "cell_shrouded": False, "cell_mapped": True,
                   "discovered_by_current_player": True, "is_visible": True,
                   "cloak_state": 0}
                  for (a, tn, ab, hp) in enemies]
    rec = schema.make_observation(
        frame=frame, wall="2026-10-29T00:00:00", stage="STAGE_INGAME",
        money=8000, houses=[], own_objects=own_recs,
        visible_enemies=enemy_recs, factories=[], hidden_enemy_count=0,
        map_summary={"width": 182, "height": 182, "mapped": 300,
                     "shrouded": 16000, "cells": 33124})
    if t is not None:
        rec["t"] = t
    return rec


class TestDeriveMetricsStaleAddresses(unittest.TestCase):
    """Regression: MCV(1000hp) address reused by CY(750hp) must not count as loss."""

    def test_mcv_to_cy_address_reuse_not_counted(self):
        rows = [
            make_damage_obs(100, own=[(500, "Allied Construction Vehicle",
                                       "ABSTRACT_TYPE_UNIT", 1000, False)]),
            make_damage_obs(200, own=[(500, "Allied Construction Yard",
                                       "ABSTRACT_TYPE_BUILDING", 750, False)]),
        ]
        m = schema.derive_metrics(rows)
        self.assertEqual(m["own_hp_lost"], 0)
        self.assertEqual(m["damage_dealt"], 0)

    def test_genuine_own_and_enemy_damage_counted(self):
        rows = [
            make_damage_obs(100, own=[(600, "GI", "ABSTRACT_TYPE_INFANTRY", 125, False)],
                            enemies=[(700, "GI", "ABSTRACT_TYPE_INFANTRY", 400)], t=10.0),
            make_damage_obs(200, own=[(600, "GI", "ABSTRACT_TYPE_INFANTRY", 110, False)],
                            enemies=[(700, "GI", "ABSTRACT_TYPE_INFANTRY", 350)], t=11.0),
        ]
        m = schema.derive_metrics(rows)
        self.assertEqual(m["own_hp_lost"], 15)
        self.assertEqual(m["damage_dealt"], 50)
        self.assertEqual(m["first_damage_at_s"], 11.0)

    def test_enemy_address_reused_by_own_object_not_counted(self):
        rows = [
            make_damage_obs(100, enemies=[(800, "GI", "ABSTRACT_TYPE_INFANTRY", 400)]),
            make_damage_obs(200, own=[(800, "GI", "ABSTRACT_TYPE_INFANTRY", 125, False)]),
        ]
        m = schema.derive_metrics(rows)
        self.assertEqual(m["damage_dealt"], 0)
        self.assertEqual(m["own_hp_lost"], 0)


class TestPolicyAdvanceDirectionAndProgress(unittest.TestCase):
    def ctx(self, base, unit_cells, t, map_height=182):
        own = [{"addr": 1, "type_name": "Allied Construction Yard",
                "type_ptr": 100, "abstract": "ABSTRACT_TYPE_BUILDING",
                "health": 1000, "cell": list(base),
                "xyz": [base[0] * 256, base[1] * 256], "on_map": True,
                "in_limbo": False}]
        own += [{"addr": 10 + i, "type_name": "GI", "type_ptr": 103,
                 "abstract": "ABSTRACT_TYPE_INFANTRY", "health": 125,
                 "cell": [c[0], c[1]], "xyz": [c[0] * 256, c[1] * 256],
                 "on_map": True, "in_limbo": False}
                for i, c in enumerate(unit_cells)]
        return {"t": t, "frame": 600, "own_objects": own, "visible_enemies": [],
                "factories": [], "hero": {"self": 1, "name": "<human player>",
                                          "money": 5000},
                "map": {"mapped": 300},
                "observation": {"map": {"height": map_height, "width": 182}},
                "last_acks": []}

    def test_north_spawn_advances_south(self):
        p = policy.RulePolicy({"build_order": []}, [])
        intents = p.step(self.ctx((91, 58), [(91, 66), (92, 66)], t=10.0))
        moves = [i for i in intents if i["action"] == "MOVE"]
        self.assertTrue(moves)
        dest_y_cell = moves[0]["coordinates"][1] / 256.0
        self.assertGreater(dest_y_cell, 58)
        self.assertEqual(p.advance_dir, 1)

    def test_south_spawn_advances_north(self):
        p = policy.RulePolicy({"build_order": []}, [])
        intents = p.step(self.ctx((89, 126), [(89, 132), (90, 133)], t=10.0))
        moves = [i for i in intents if i["action"] == "MOVE"]
        self.assertTrue(moves)
        dest_y_cell = moves[0]["coordinates"][1] / 256.0
        self.assertLess(dest_y_cell, 126)
        self.assertEqual(p.advance_dir, -1)

    def test_waypoint_progresses_even_if_units_stuck(self):
        p = policy.RulePolicy({"build_order": []}, [])
        stuck = [(91, 67), (91, 67), (91, 67)]
        first = p.step(self.ctx((91, 58), stuck, t=10.0))
        y1 = [i for i in first if i["action"] == "MOVE"][0]["coordinates"][1]
        second = p.step(self.ctx((91, 58), stuck, t=13.1))
        moves2 = [i for i in second if i["action"] == "MOVE"]
        self.assertTrue(moves2, "policy must keep advancing instead of deadlocking")
        y2 = moves2[0]["coordinates"][1]
        self.assertGreater(y2, y1, "waypoint must progress on a timer even if stuck")

    def test_advance_clamped_inside_playable_inset(self):
        # regression: ep-trial-03 sent 38x "MOVE: invalid cell" at y=10/14
        p = policy.RulePolicy({"build_order": []}, [])
        stuck = [(89, 132), (90, 133)]
        dests = []
        for i in range(30):
            t = 10.0 + i * 3.1
            intents = p.step(self.ctx((89, 126), stuck, t=t))
            dests += [m["coordinates"][1] / 256.0 for m in intents
                      if m["action"] == "MOVE"]
        self.assertTrue(dests)
        self.assertGreaterEqual(min(dests), 27.0,
                                "advance goal must stay inside the 15% map inset")
        # once clamped at the boundary and units are not near, no more orders
        intents = p.step(self.ctx((89, 126), stuck, t=10.0 + 31 * 3.1))
        self.assertFalse(any(m["action"] == "MOVE" for m in intents))

    def test_invalid_cell_ack_pulls_back_and_pauses(self):
        p = policy.RulePolicy({"build_order": []}, [])
        ctx = self.ctx((89, 126), [(89, 132), (90, 133)], t=10.0)
        p.step(ctx)
        goal_before = p.advance_goal_y
        ctx2 = self.ctx((89, 126), [(89, 132), (90, 133)], t=14.0)
        ctx2["last_acks"] = [{"action": "MOVE", "error": "native_error: invalid cell",
                              "t": 13.0}]
        intents = p.step(ctx2)
        self.assertGreater(p.advance_goal_y, goal_before,
                           "invalid cell must pull the goal back toward map centre")
        self.assertFalse(any(i["action"] == "MOVE" for i in intents),
                         "advance must pause after an invalid-cell rejection")


class TestGameLock(unittest.TestCase):
    def test_exclusive_lock_and_release(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "game.lock"
            held = lockfile.acquire(path)
            self.assertTrue(held.exists())
            with self.assertRaises(RuntimeError):
                lockfile.acquire(path)
            lockfile.release(held)
            self.assertFalse(path.exists())
            lockfile.acquire(path)
            lockfile.release(path)

    def test_stale_lock_is_reclaimed(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "game.lock"
            path.write_text(json.dumps({"pid": 999999,
                                        "created_at_epoch": 0}), encoding="utf-8")
            lockfile.acquire(path)  # pid not alive -> reclaimed
            lockfile.release(path)

    def test_recycled_pid_lock_is_reclaimed(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "game.lock"
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
            try:
                # The lock is older than the process holding the same PID, so
                # Windows must have recycled that PID; the lock is stale.
                path.write_text(json.dumps({"pid": child.pid,
                                            "created_at_epoch": time.time() - 60}),
                                encoding="utf-8")
                lockfile.acquire(path)
                lockfile.release(path)
                self.assertFalse(path.exists())
            finally:
                child.kill()
                child.wait(10)

    def test_genuine_live_lock_is_not_reclaimed(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "game.lock"
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
            try:
                path.write_text(json.dumps({"pid": child.pid,
                                            "created_at_epoch": time.time()}),
                                encoding="utf-8")
                with self.assertRaises(RuntimeError):
                    lockfile.acquire(path)
                self.assertTrue(path.exists())
            finally:
                child.kill()
                child.wait(10)

    def test_reclaim_stale_keeps_live_and_unreadable(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
            try:
                dead = root / "dead.lock"
                dead.write_text(json.dumps({"pid": 999999, "created_at_epoch": 0}))
                live = root / "live.lock"
                live.write_text(json.dumps({"pid": child.pid,
                                            "created_at_epoch": time.time()}))
                broken = root / "broken.lock"
                broken.write_text("{")
                removed, kept = lockfile.reclaim_stale(root)
                self.assertEqual(removed, [dead])
                self.assertEqual({p.name for p, _ in kept}, {"live.lock", "broken.lock"})
                self.assertTrue(live.exists() and broken.exists())
            finally:
                child.kill()
                child.wait(10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
