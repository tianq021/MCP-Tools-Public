"""Offline source, identity, masks and advisory safety tests. No game processes."""
import copy
import pathlib
import json
import os
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zlib

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/ai_teacher"))
from wdzs_source import compile_library, compile_rules, parse_ini, read_mix_entry
from wdzs_advisor import WdzsAdvisor
sys.path.insert(0, str(ROOT / "scripts"))
from ai_teacher.wdzs_advice_hook import capture_advice


def fixture():
    ai = b'''[TaskForces]
0=F
1=S
[ScriptTypes]
0=A
[TeamTypes]
0=T
1=SPYTEAM
[F]
0=3,E1
1=3,GGI
[S]
0=1,SPY
[A]
0=0,1
1=49,0
[T]
Name=Mixed infantry
TaskForce=F
Script=A
[SPYTEAM]
TaskForce=S
Script=A
[AITriggerTypes]
X=Example,T,<all>,0,-1,<none>,0,1,1,1,1,0,1,0,<none>,0,1,1
'''
    rules = b'''[BuildingTypes]
0=GACNST
1=GAPOWR
2=GAREFN
3=GAPILE
4=GAWEAP
[InfantryTypes]
0=E1
1=GGI
2=SPY
[AI]
BuildConst=GACNST
BuildPower=GAPOWR
BuildRefinery=GAREFN
BuildBarracks=GAPILE
BuildWeapons=GAWEAP
[GACNST]
Name=Allied Construction Yard
[GAPOWR]
Name=Allied Power Plant
[GAREFN]
Name=Allied Ore Refinery
Cost=2000
[GAPILE]
Name=Allied Barracks
[GAWEAP]
Name=Allied War Factory
[E1]
Name=GI
Primary=M60
Armor=none
[GGI]
Name=Guardian GI
Secondary=Missile
[SPY]
Name=Spy
Agent=yes
Primary=Disguise
[M60]
Damage=15
Range=4
ROF=20
Warhead=SA
[SA]
Verses=100%,100%,100%,1%,1%,1%,1%,1%,1%,1%,1%
'''
    library, rules = compile_library(parse_ini(ai)), compile_rules(parse_ini(rules))
    catalog = [{"name": rules["units"][ident]["name"], "cost": 200} for ident in rules["units"]]
    return library, rules, catalog


def observation(rules, ids):
    return {"frame": 100, "money": 5000, "own_objects": [
        {"addr": i+1, "type_name": rules["units"][ident]["name"], "health": 100,
         "on_map": True, "in_limbo": False} for i, ident in enumerate(ids)]}


class SourceTests(unittest.TestCase):
    def test_exact_archive_entry_bytes(self):
        data = b"[TaskForces]\n0=X\n"
        member = struct.pack("<III", zlib.crc32(b"AIMD.INI"), 0, len(data))
        archive = struct.pack("<IHI", 0, 1, len(data)) + member + data
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "test.mix"
            path.write_bytes(archive)
            result = read_mix_entry(path)
        self.assertEqual(result["data"], data)
        self.assertEqual(result["offset"], 22)

    def test_archive_bounds_are_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "bad.mix"
            path.write_bytes(struct.pack("<IHI", 0, 1, 1000))
            with self.assertRaises(ValueError):
                read_mix_entry(path)

    def test_encrypted_archive_not_guessed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "encrypted.mix"
            path.write_bytes(struct.pack("<IHI", 0x20000, 0, 0))
            self.assertEqual(read_mix_entry(path)["status"], "ENCRYPTED_UNSUPPORTED")

    def test_ini_repeated_section_merges_and_tracks_override(self):
        parsed = parse_ini(b"[AI]\nBuildPower=A ; comment\n[AI]\nBuildPower=B\nBuildRefinery=R\n")
        self.assertEqual(parsed["sections"]["AI"], {"buildpower": "B", "buildrefinery": "R"})
        self.assertEqual(len(parsed["duplicates"]), 1)

    def test_compiled_graph_preserves_real_references(self):
        library, _, _ = fixture()
        self.assertEqual(library["issues"], [])
        self.assertEqual(library["teams"]["T"]["taskforce_id"], "F")
        self.assertEqual(library["triggers"][0]["primary_team"], "T")

    def test_missing_references_are_reported(self):
        parsed = parse_ini(b"[TeamTypes]\n0=T\n[T]\nTaskForce=missing\nScript=missing\n")
        self.assertEqual(compile_library(parsed)["issues"][0]["kind"], "BROKEN_TEAM_REFERENCE")

    def test_actual_package_is_different_from_old_external_library(self):
        entry = read_mix_entry(ROOT / "test-game-wdzs/ExpandMD02.mix")
        self.assertEqual(entry["sha256"], "4100cf7ae514e7df1705cfaee6e24072166964e20b0e45ab607bf399fb1513ad")
        library = compile_library(parse_ini(entry["data"]))
        self.assertEqual(library["counts"], {"taskforces": 138, "scripts": 132, "teams": 278,
                                             "triggers": 287, "action_lines": 704})
        self.assertEqual(library["issues"], [])


class AdvisorTests(unittest.TestCase):
    def setUp(self):
        self.library, self.rules, self.catalog = fixture()
        self.advisor = WdzsAdvisor(self.library, self.rules)
        self.mask = [False] * 301
        for i in range(len(self.catalog)):
            self.mask[2+i] = True

    def test_refinery_goal_prevents_more_barracks(self):
        obs = observation(self.rules, ["GACNST", "GAPOWR"] + ["GAPILE"] * 13)
        advice = self.advisor.suggest(obs, self.catalog, self.mask)
        self.assertEqual(advice["goal"], "BUILD_REFINERY")
        self.assertEqual(self.catalog[advice["candidate_301"]-2]["name"], "Allied Ore Refinery")

    def test_unaffordable_refinery_does_not_fall_back_to_barracks(self):
        obs = observation(self.rules, ["GACNST", "GAPOWR", "GAPILE"])
        mask = [False] * 301
        mask[2+3] = True
        advice = self.advisor.suggest(obs, self.catalog, mask)
        self.assertEqual(advice["goal"], "BUILD_REFINERY")
        self.assertIsNone(advice["candidate_301"])

    def test_placement_precedes_new_production(self):
        mask = list(self.mask); mask[100] = True
        advice = self.advisor.suggest(observation(self.rules, ["GACNST"]), self.catalog, mask)
        self.assertEqual(advice["goal"], "PLACE_FINISHED_BUILDING")
        self.assertEqual(advice["candidate_301"], 100)

    def test_spy_attack_script_is_not_a_combat_template(self):
        team = self.advisor._combat_template(self.catalog)
        self.assertEqual(team[2]["id"], "T")
        spy_catalog = [item for item in self.catalog if item["name"] == "Spy"]
        self.assertIsNone(self.advisor._combat_template(spy_catalog))

    def test_engineer_is_excluded_even_with_attack_script_codes(self):
        rules = copy.deepcopy(self.rules)
        rules["units"]["SPY"]["fields"] = {"engineer": "yes"}
        self.assertIsNone(WdzsAdvisor(self.library, rules)._combat_template(
            [item for item in self.catalog if item["name"] == "Spy"]))

    def test_host_adapter_never_executes_or_mutates(self):
        obs = observation(self.rules, ["GACNST", "GAPOWR"])
        before = copy.deepcopy(obs)
        def forbidden(*args):
            raise AssertionError("No control operation allowed")
        env = SimpleNamespace(current=obs, full=SimpleNamespace(types=self.catalog),
                              action_masks=lambda: self.mask, step=forbidden, reset=forbidden,
                              execute=forbidden, client=SimpleNamespace(exec_command=forbidden))
        advice = self.advisor.suggest_from_env(env)
        self.assertEqual(obs, before)
        self.assertFalse(advice["training_label_eligible"])
        self.assertEqual(advice["role"], "ADVISORY_ONLY")

    def test_hidden_or_future_fields_cannot_change_advice(self):
        obs = observation(self.rules, ["GACNST", "GAPOWR"])
        other = copy.deepcopy(obs)
        other.update(hidden_enemy_count=999, enemy_true_locations=[[1, 2]], future_action=300)
        self.assertEqual(self.advisor.suggest(obs, self.catalog, self.mask),
                         self.advisor.suggest(other, self.catalog, self.mask))

    def test_property_cards_keep_unknown_and_known_fields_separate(self):
        card = self.advisor.property_card("E1")
        self.assertEqual(card["weapons"][0]["range"], "4")
        self.assertIsNone(card["speed"])
        self.assertEqual(card["armor"], "none")
        self.assertEqual(self.advisor.property_card("missing")["status"], "UNKNOWN")

    def test_persistent_type_vocabulary_not_python_hash(self):
        rules = copy.deepcopy(self.rules)
        rules["units"] = dict(reversed(list(rules["units"].items())))
        other = WdzsAdvisor(self.library, rules)
        self.assertEqual(self.advisor.property_card("E1")["type_index"], other.property_card("E1")["type_index"])

    def test_action_contract_is_not_silently_changed(self):
        with self.assertRaises(ValueError):
            self.advisor.suggest({}, self.catalog, [False] * 300)

    def test_ambiguous_unit_names_do_not_guess_identity(self):
        rules = copy.deepcopy(self.rules)
        rules["units"]["OTHER"] = {"name": "GI", "fields": {}, "kind": "infantry"}
        self.assertIsNone(WdzsAdvisor(self.library, rules).type_id("GI"))


class LiveHookOfflineTests(unittest.TestCase):
    def test_disabled_hook_has_no_effect(self):
        env = SimpleNamespace()
        with patch.dict(os.environ, {"DSH_WDZS_ADVICE": "0"}):
            self.assertIsNone(capture_advice(env))
        self.assertEqual(vars(env), {})

    def test_enabled_hook_logs_only_advice_without_control_calls(self):
        library, rules, catalog = fixture()
        mask = [False] * 301
        for i in range(len(catalog)):
            mask[2+i] = True
        def forbidden(*args):
            raise AssertionError("Control operation forbidden")
        with tempfile.TemporaryDirectory() as directory:
            bundle = pathlib.Path(directory) / "bundle"; bundle.mkdir()
            for name, payload in (("compiled_ai.json", library), ("rules_reference.json", rules),
                                  ("SOURCE_PROVENANCE.json", {"candidate": {"sha256": library["source_sha256"]}})):
                (bundle / name).write_text(json.dumps(payload), encoding="utf-8")
            env = SimpleNamespace(current=observation(rules, ["GACNST", "GAPOWR"]),
                                  full=SimpleNamespace(types=catalog), action_masks=lambda: mask,
                                  writer=SimpleNamespace(episode_id="offline-test"), out_dir=pathlib.Path(directory),
                                  step=forbidden, reset=forbidden, execute=forbidden)
            with patch.dict(os.environ, {"DSH_WDZS_ADVICE": "1", "DSH_WDZS_ADVICE_BUNDLE": str(bundle)}):
                result = capture_advice(env)
            self.assertEqual(result["goal"], "BUILD_REFINERY")
            self.assertFalse(result["training_label_eligible"])
            logged = json.loads((pathlib.Path(directory) / "offline-test.wdzs-advice.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(logged, result)

    def test_missing_bundle_does_not_break_the_loop(self):
        env = SimpleNamespace()
        with patch.dict(os.environ, {"DSH_WDZS_ADVICE": "1", "DSH_WDZS_ADVICE_BUNDLE": "Z:/no-such-advisor-bundle"}):
            self.assertIsNone(capture_advice(env))
        self.assertIn("FileNotFoundError", env._wdzs_advice_error)


if __name__ == "__main__":
    unittest.main()
