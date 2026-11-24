"""Offline integration test: LLM rules -> driver validation, no game / no bridge.

Builds a synthetic ra2yrproto GameState (the same proto the bridge returns) and
checks that the LLM-authored conditional-command pipeline resolves selectors and
passes the driver's own legality validation. This never launches a game.
"""
import argparse
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "vendor" / "pyra2yr"))
sys.path.append(str(ROOT / ".venv-rl" / "Lib" / "site-packages"))

from ra2yrproto import ra2yr  # noqa: E402

from llm_takeover import rules  # noqa: E402
from llm_takeover import run_llm_takeover as drv  # noqa: E402

CELL = 256


def make_state():
    st = ra2yr.GameState()
    st.current_frame = 1000
    st.stage = ra2yr.LoadStage.STAGE_INGAME
    hero = st.houses.add()
    hero.self = 1
    hero.name = "<human player>"
    hero.faction = "Americans"
    hero.current_player = True
    hero.is_human_player = True
    hero.money = 5000
    enemy = st.houses.add()
    enemy.self = 2
    enemy.name = "Computer"
    enemy.faction = "Americans"
    enemy.is_human_player = False

    def add(addr, house, cell, abstract, on_map=True, in_limbo=False):
        o = st.objects.add()
        o.pointer_self = addr
        o.pointer_house = house
        o.health = 300
        o.pointer_technotypeclass = 77
        o.object_type = abstract
        o.coordinates.x = cell[0] * CELL + CELL // 2
        o.coordinates.y = cell[1] * CELL + CELL // 2
        o.on_map = on_map
        o.in_limbo = in_limbo
        o.cell_shrouded = False
        o.discovered_by_current_player = True
        o.current_mission = ra2yr.Mission.Mission_Guard
        return o

    add(42, 1, (10, 10), ra2yr.ABSTRACT_TYPE_UNIT)            # own tank
    add(43, 1, (11, 10), ra2yr.ABSTRACT_TYPE_INFANTRY)        # own GI
    add(44, 1, (10, 9), ra2yr.ABSTRACT_TYPE_BUILDING)         # own building
    add(90, 2, (14, 10), ra2yr.ABSTRACT_TYPE_INFANTRY)        # visible enemy
    return st


def make_takeover(tmpdir):
    opts = argparse.Namespace(
        out=str(tmpdir), game_dir=str(ROOT / "test-game-brutal"), port=14621,
        authorization="OFFLINE-TEST", runtime_mode="slowmo", slowmo_fps=10.0,
        decision_timeout=60.0, max_sim_seconds=60.0, max_wall_seconds=60.0,
        attach=False, game_pid=None, start_decision=0, episode_id="offline-test",
    )
    tk = drv.Takeover(opts)
    st = make_state()
    tk.latest_state = st
    tk.hero_self = 1
    tk.type_names = {77: "Grizzly Battle Tank"}
    tk.house_names = {1: "<human player>", 2: "Computer"}
    tk.catalog_by_name = {"Grizzly Battle Tank": {"strength": 300}}
    return tk


class DriverRuleIntegrationTests(unittest.TestCase):
    def test_legal_view_enriches_max_hp_and_filters_hidden(self):
        with tempfile.TemporaryDirectory() as tmp:
            tk = make_takeover(tmp)
            legal = tk.legal_view(tk.latest_state)
            self.assertIsNotNone(legal)
            self.assertEqual(len(legal["own_objects"]), 3)
            tank = next(o for o in legal["own_objects"] if o["addr"] == 42)
            self.assertEqual(tank["max_hp"], 300)
            self.assertEqual([e["addr"] for e in legal["visible_enemies"]], [90])

    def test_rule_commands_pass_driver_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            tk = make_takeover(tmp)
            legal = tk.legal_view(tk.latest_state)
            cmds, errors = rules.compile_rule_commands(
                {"id": "atk", "then": [
                    {"op": "ATTACK", "units": "all_combat",
                     "target": "nearest_visible_enemy"},
                    {"op": "MOVE", "units": "type:Grizzly", "cell": [5, 5]},
                    {"op": "STOP", "units": [42]},
                    {"op": "PRODUCE", "type": "Grizzly Battle Tank"},
                ]}, legal)
            self.assertEqual(errors, [])
            self.assertEqual(len(cmds), 4)
            for raw in cmds:
                op, payload, errs = tk._validate(raw)
                self.assertEqual(errs, [], f"{op} unexpectedly invalid: {errs}")
            self.assertEqual(cmds[0]["target"], 90)
            self.assertEqual(cmds[0]["units"], [42, 43])  # tank + GI are both combat

    def test_hidden_enemy_attack_is_rejected_by_driver(self):
        with tempfile.TemporaryDirectory() as tmp:
            tk = make_takeover(tmp)
            # a rule that somehow targets an address that is not legally visible
            op, payload, errors = tk._validate(
                {"op": "ATTACK", "units": [42], "target": 12345})
            self.assertEqual(op, "ATTACK")
            self.assertTrue(errors)

    def test_ruleset_roundtrip_from_decision_dict(self):
        decision = {
            "decision": 0, "advance_frames": 60, "reason": "offline",
            "rules": [{"id": "r1", "when": {"always": True},
                       "then": [{"op": "STOP", "units": "all_combat"}],
                       "cooldown_frames": 30, "max_fires": 2}],
        }
        rs = rules.RuleSet(decision["rules"])
        self.assertEqual(len(rs.rules), 1)
        rs2 = rules.RuleSet([])
        self.assertEqual(rs2.rules, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
