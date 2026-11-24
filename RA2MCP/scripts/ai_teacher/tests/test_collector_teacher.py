"""Offline unit tests for CollectorTeacher (no game / no Bridge / no training).

Run with the control venv:
  .\\.venv\\Scripts\\python.exe -m unittest scripts.ai_teacher.tests.test_collector_teacher
"""
from __future__ import annotations

import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[3]
for p in (ROOT / "scripts", ROOT / "scripts" / "ai_teacher"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import numpy as np  # noqa: E402

from dsh_rl.full_spec import FullControlSpec, ACTION_N, UNIT_START, PLACE_START, PRODUCE_START  # noqa: E402
from ai_teacher.collector_teacher import (  # noqa: E402
    CollectorTeacher, CollectorConfig, CollectorView, POLICY_VERSION, dir_bucket)


# ---------------------------------------------------------------------------
# synthetic catalog / rows
# ---------------------------------------------------------------------------
def _catalog():
    def t(name, kind, array_index, cost, ptr, prereq=(), build_limit=0):
        return {"name": name, "type": kind, "array_index": array_index,
                "cost": cost, "prerequisites": list(prereq), "tech_level": 0,
                "pointer_self": ptr, "owner_flags": 1, "required_houses": -1,
                "forbidden_houses": -1, "naval": False, "build_limit": build_limit,
                "power_bonus": 100 if "Power" in name else 0,
                "power_drain": 0, "is_base_defense": "Pillbox" in name,
                "requires_stolen_allied_tech": False,
                "requires_stolen_soviet_tech": False,
                "requires_stolen_third_tech": False, "strength": 1000}
    types = [
        t("Allied Construction Yard", "ABSTRACT_TYPE_BUILDINGTYPE", 100, 1, 900),
        t("Allied Power Plant", "ABSTRACT_TYPE_BUILDINGTYPE", 101, 800, 901),
        t("Allied Ore Refinery", "ABSTRACT_TYPE_BUILDINGTYPE", 102, 2000, 902),
        t("Allied Barracks", "ABSTRACT_TYPE_BUILDINGTYPE", 103, 500, 903),
        t("Allied War Factory", "ABSTRACT_TYPE_BUILDINGTYPE", 104, 2000, 904),
        t("Pillbox", "ABSTRACT_TYPE_BUILDINGTYPE", 105, 500, 905),
        t("Grizzly Battle Tank", "ABSTRACT_TYPE_UNITTYPE", 0, 700, 910),
        t("GI", "ABSTRACT_TYPE_INFANTRYTYPE", 0, 200, 911),
        t("Allied Construction Vehicle", "ABSTRACT_TYPE_UNITTYPE", 1, 1000, 912),
    ]
    groups = {"power": {101}, "proc": {102}, "barracks": {103},
              "factory": {104}, "radar": set(), "tech": set()}
    return types, groups


def make_spec(country_index=0):
    cat, groups = _catalog()
    return FullControlSpec(catalog=cat, groups=groups, country_index=country_index,
                           tech_level=10)


def obj(addr, name, abstract, cell=(90, 127), health=500, ptr=None,
        on_map=True, in_limbo=False, deployed=False):
    return {"addr": addr, "type_ptr": ptr or (hash(name) & 0xFFFFF), "type_name": name,
            "abstract": abstract, "owner": "<human player>", "owner_ptr": 1,
            "health": health, "cell": list(cell), "on_map": on_map,
            "in_limbo": in_limbo, "selected": False, "cell_shrouded": False,
            "cell_mapped": True, "discovered_by_current_player": True,
            "is_visible": True, "cloak_state": 0, "deployed": deployed}


def row(own=(), enemies=(), factories=(), money=5000, t=10.0,
        map_w=182, map_h=182, hidden=False):
    r = {"own_objects": list(own), "visible_enemies": list(enemies),
         "factories": list(factories), "houses": [{"money": money, "is_human": True}],
         "money": money, "t": t, "frame": int(t * 60),
         "map": {"width": map_w, "height": map_h}}
    if hidden:
        r["hidden_enemy_count"] = 99
    return r


def cy(addr=1001, cell=(90, 127)):
    return obj(addr, "Allied Construction Yard", "ABSTRACT_TYPE_BUILDING", cell=cell)


def power(addr=1002, cell=(92, 127)):
    return obj(addr, "Allied Power Plant", "ABSTRACT_TYPE_BUILDING", cell=cell)


def refinery(addr=1003, cell=(94, 127)):
    return obj(addr, "Allied Ore Refinery", "ABSTRACT_TYPE_BUILDING", cell=cell)


def barracks(addr=1004, cell=(96, 127)):
    return obj(addr, "Allied Barracks", "ABSTRACT_TYPE_BUILDING", cell=cell)


def mcv(addr=2001, cell=(88, 127)):
    return obj(addr, "Allied Construction Vehicle", "ABSTRACT_TYPE_UNIT", cell=cell,
               ptr=912)


def tank(addr, cell):
    return obj(addr, "Grizzly Battle Tank", "ABSTRACT_TYPE_UNIT", cell=cell, ptr=910)


def enemy(addr=3001, cell=(100, 127)):
    return obj(addr, "Grizzly Battle Tank", "ABSTRACT_TYPE_UNIT", cell=cell,
               health=300, ptr=910) | {"owner": "Computer"}


class MockHost:
    """Minimal host matching dsh_rl.scripted_policy_v2.HostAdapter's interface."""

    def __init__(self, observation, spec, starts=(), map_soa=None):
        self.current = observation
        self.starts = list(starts)
        self.full = spec
        self.map_soa = map_soa or types.SimpleNamespace(map_width=182, map_height=182)
        self.catalog = []

    def available_production(self):
        out = [bool(self.full.can_produce(i, self.current))
               for i in range(len(self.full.types))]
        return out + [False] * (96 - len(out))

    def production_types(self):
        return [str(t.get("name")) for t in self.full.types]

    def legal_actions(self):
        mask = self.full.masks(self.current, self.starts, self.map_soa)
        return np.flatnonzero(mask).tolist()


class BrokenMaskHost(MockHost):
    def legal_actions(self):
        return []


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------
class CollectorTeacherTests(unittest.TestCase):
    def setUp(self):
        self.spec = make_spec()

    def teacher(self, **cfg):
        return CollectorTeacher(config=CollectorConfig(**cfg), spec=self.spec, seed=7)

    def decide(self, teacher, obs, starts=()):
        return teacher.decide(MockHost(obs, self.spec, starts=starts))

    # -- contract guarantees -------------------------------------------------
    def assertLegalMapped(self, teacher, obs, starts=(), allow_noop=True):
        host = MockHost(obs, self.spec, starts=starts)
        dec = teacher.decide(host)
        legal = host.legal_actions()
        self.assertIn(dec["action"], legal, dec)
        self.assertTrue(dec["action_in_legal_mask"])
        if dec["action"] == 0:
            self.assertTrue(allow_noop)
            self.assertEqual(dec["mapping"], "NOOP")
        else:
            self.assertEqual(dec["mapping"], "MAPPED", dec)
            self.assertIsNotNone(dec["intent"], dec)
        return dec

    def test_empty_observation_returns_noop(self):
        dec = self.assertLegalMapped(self.teacher(), row())
        self.assertEqual(dec["action"], 0)
        self.assertEqual(dec["task"], "NOOP")
        self.assertEqual(dec["label"], "noop")

    def test_deploy_mcv(self):
        dec = self.assertLegalMapped(self.teacher(), row(own=[mcv()]))
        self.assertEqual(dec["task"], "DEPLOY")
        self.assertEqual(dec["action"], 1)
        self.assertEqual(dec["label"], "deploy")

    def test_produce_building_with_base(self):
        dec = self.assertLegalMapped(self.teacher(), row(own=[cy()]))
        self.assertEqual(dec["task"], "PRODUCE_BUILDING")
        self.assertTrue(PRODUCE_START <= dec["action"] < PLACE_START)
        self.assertEqual(dec["label"], "produce")

    def test_place_pending_building(self):
        pending = obj(1100, "Allied Power Plant", "ABSTRACT_TYPE_BUILDING",
                      cell=(0, 0), on_map=False, in_limbo=True, ptr=901)
        factories = [{"owner_ptr": 1, "object_ptr": 1100, "completed": True,
                      "on_hold": False, "queued_objects": [], "progress_timer": 0}]
        obs = row(own=[cy(), pending], factories=factories)
        dec = self.assertLegalMapped(self.teacher(), obs)
        self.assertEqual(dec["task"], "PLACE")
        self.assertTrue(PLACE_START <= dec["action"] < UNIT_START)
        self.assertEqual(dec["label"], "place")

    def test_attack_visible_target_in_assault_phase(self):
        own = [power(), refinery(), barracks(),
               tank(2101, (90, 120)), tank(2102, (91, 120)),
               tank(2103, (92, 120)), tank(2104, (93, 120))]
        obs = row(own=own, enemies=[enemy(cell=(100, 120))])
        dec = self.assertLegalMapped(self.teacher(), obs)
        self.assertEqual(dec["task"], "ATTACK")
        # action offset must be in the ATTACK command window of the selection
        command = (dec["action"] - UNIT_START) % 19
        self.assertTrue(9 <= command <= 16, dec)
        self.assertEqual(dec["label"], "attack")

    def test_move_when_no_enemy(self):
        own = [power(), refinery(), barracks(),
               tank(2101, (90, 120)), tank(2102, (91, 120)),
               tank(2103, (92, 120)), tank(2104, (93, 120))]
        obs = row(own=own)
        dec = self.assertLegalMapped(self.teacher(), obs)
        self.assertIn(dec["task"], ("MOVE", "PRODUCE_UNIT", "STOP"))
        if dec["task"] == "MOVE":
            command = (dec["action"] - UNIT_START) % 19
            self.assertTrue(1 <= command <= 8)

    def test_scout_with_public_starts(self):
        teacher = self.teacher(scout_after_s=0.0)
        own = [cy(), power(), refinery(), barracks(), tank(2101, (90, 120))]
        obs = row(own=own, t=60.0)
        host = MockHost(obs, self.spec, starts=[(20, 20), (160, 160)])
        view = CollectorView(obs)
        cand = teacher._scout_candidate(host, view)
        self.assertIsNotNone(cand)
        self.assertEqual(cand["task"], "SCOUT")
        self.assertIn(cand["action"], host.legal_actions())
        self.assertEqual((cand["action"] - UNIT_START) % 19, 17)
        # The scheduler must still emit a legal, mapped action in the same state.
        dec = teacher.decide(host)
        self.assertIn(dec["action"], host.legal_actions())
        self.assertIn(dec["mapping"], ("MAPPED", "NOOP"))

    # -- anti-cheat / robustness -------------------------------------------
    def test_hidden_info_is_dropped(self):
        obs = row(own=[cy()], hidden=True)
        view = CollectorView(obs)
        self.assertNotIn("hidden_enemy_count", view.row)
        self.assertTrue(view.reject_hidden)
        dec = self.assertLegalMapped(self.teacher(), obs)
        self.assertFalse(dec["hidden_info_used"])

    def test_deterministic_same_state(self):
        obs = row(own=[power(), refinery(), barracks(),
                       tank(2101, (90, 120)), tank(2102, (91, 120)),
                       tank(2103, (92, 120)), tank(2104, (93, 120))],
                  enemies=[enemy(cell=(100, 120))])
        a = self.decide(self.teacher(), obs)
        b = self.decide(self.teacher(), obs)
        self.assertEqual(a["action"], b["action"])
        self.assertEqual(a["digest"], b["digest"])
        self.assertEqual(a["config_hash"], b["config_hash"])

    def test_fail_closed_when_mask_has_no_decodable_action(self):
        teacher = self.teacher()
        host = BrokenMaskHost(row(own=[cy()]), self.spec)
        with self.assertRaises(RuntimeError):
            teacher.decide(host)

    def test_feint_hook_is_off_by_default_and_legal_when_enabled(self):
        own = [power(), refinery(), barracks(),
               tank(2101, (90, 120)), tank(2102, (91, 120)),
               tank(2103, (92, 120)), tank(2104, (93, 120))]
        obs = row(own=own, enemies=[enemy(cell=(100, 120))])
        host = MockHost(obs, self.spec)
        view = CollectorView(obs)
        off = self.teacher()._feint_candidate(host, view)
        self.assertIsNone(off)
        teacher = self.teacher(feint_enabled=True)
        cand = teacher._feint_candidate(host, view)
        self.assertIsNotNone(cand)
        self.assertEqual(cand["task"], "FEINT_LURE")
        self.assertIn(cand["action"], host.legal_actions())
        self.assertIsNotNone(self.spec.intent(cand["action"], host.current, [],
                                              host.map_soa))

    def test_dir_bucket_matches_contract_directions(self):
        from dsh_rl.full_spec import DIRECTIONS
        for index, (dx, dy) in enumerate(DIRECTIONS):
            # perpendicular step only; 90-degree ambiguity is excluded by using
            # an exact unit vector scaled by 10 cells
            self.assertEqual(dir_bucket(dx, dy), index,
                             (dx, dy, DIRECTIONS))

    def test_policy_metadata(self):
        meta = self.teacher().policy_metadata()
        self.assertEqual(meta["action_n"], ACTION_N)
        self.assertTrue(meta["deterministic"])
        self.assertEqual(meta["policy_version"], POLICY_VERSION)
        self.assertTrue(meta["config_hash"])

    def test_decision_schema_accepts_collector_teacher_source(self):
        """Regression for RUN01 failure: capture schema must accept our source."""
        from dsh_m1 import decision_schema as DS
        self.assertIn("collector_teacher", DS.ACTION_SOURCES)
        d = DS.make_decision(
            "ep", 0, 0, DS.obs_id("ep", 0, "before"), 1,
            "collector_teacher", POLICY_VERSION, 0.5,
            obs_after_id=DS.obs_id("ep", 0, "after"), status="RUNNING")
        self.assertEqual(DS.validate_decision(d), [])
        self.assertEqual(d["action_source"], "collector_teacher")
        self.assertEqual(d["action_version"], POLICY_VERSION)
        # historical sources stay valid
        for source in ("scripted_teacher", "scripted_teacher_v2", "model", "human", "rule"):
            self.assertEqual(DS.validate_decision(
                DS.make_decision("ep", 0, 0, DS.obs_id("ep", 0, "before"), 0,
                                 source, "v", 0.0,
                                 obs_after_id=DS.obs_id("ep", 0, "after"))), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
