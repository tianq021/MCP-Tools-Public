"""Offline unit tests for LLM-authored conditional orders (no game, no bridge)."""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

from llm_takeover import rules  # noqa: E402


def own(addr, name, cell, hp=300, abstract="ABSTRACT_TYPE_UNIT", in_limbo=False,
        max_hp=300):
    return {"addr": addr, "type_name": name, "health": hp, "max_hp": max_hp,
            "cell": list(cell), "abstract": abstract, "in_limbo": in_limbo,
            "on_map": not in_limbo, "mission": "Mission_Guard"}


def enemy(addr, name, cell, hp=125):
    return {"addr": addr, "type_name": name, "health": hp, "cell": list(cell)}


def legal(own_objects=None, visible_enemies=None, money=10000):
    return {
        "own_objects": own_objects or [],
        "visible_enemies": visible_enemies or [],
        "houses": [{"ptr": 1, "name": "<human player>", "money": money,
                    "current_player": True, "is_human": True}],
    }


class SelectorTests(unittest.TestCase):
    def test_selectors_exclude_economy_and_buildings(self):
        view = legal([
            own(1, "Grizzly Battle Tank", (10, 10)),
            own(2, "GI", (11, 10)),
            own(3, "Allied Construction Vehicle", (9, 10)),
            own(4, "Chrono Miner", (8, 10)),
            own(5, "Allied Power Plant", (9, 9),
                abstract="ABSTRACT_TYPE_BUILDING"),
            own(6, "Grizzly Battle Tank", (12, 12), in_limbo=True),
        ])
        self.assertEqual(rules.select_units("all_combat", view), [1, 2])
        self.assertEqual(rules.select_units("type:Grizzly", view), [1])
        self.assertEqual(rules.select_units([2, 4, 999], view), [2])

    def test_nearest_visible_enemy(self):
        view = legal([own(1, "Grizzly Battle Tank", (10, 10))],
                     [enemy(100, "GI", (12, 10)), enemy(101, "GI", (10, 30))])
        addr, cell = rules.resolve_target("nearest_visible_enemy", view,
                                          [o for o in view["own_objects"]])
        self.assertEqual(addr, 100)
        self.assertEqual(cell, [12, 10])

    def test_hidden_target_is_rejected(self):
        view = legal([own(1, "Grizzly Battle Tank", (10, 10))], [])
        self.assertEqual(rules.resolve_target("nearest_visible_enemy", view, []), (None, None))
        self.assertEqual(rules.resolve_target(999, view, []), (None, None))


class ConditionTests(unittest.TestCase):
    def test_enemy_visible_within_base(self):
        base = own(1, "Allied Construction Yard", (50, 50),
                   abstract="ABSTRACT_TYPE_BUILDING")
        view = legal([base], [enemy(100, "GI", (55, 50))])
        self.assertTrue(rules.evaluate_condition(
            {"enemy_visible_within": {"radius_cells": 6, "from": "own_base"}}, view))
        self.assertFalse(rules.evaluate_condition(
            {"enemy_visible_within": {"radius_cells": 2, "from": "own_base"}}, view))

    def test_count_money_and_limbo(self):
        view = legal([own(1, "Allied Barracks", (10, 10),
                          abstract="ABSTRACT_TYPE_BUILDING", in_limbo=True)],
                     money=2500)
        self.assertTrue(rules.evaluate_condition({"money_above": 2000}, view))
        self.assertFalse(rules.evaluate_condition({"money_below": 2000}, view))
        self.assertTrue(rules.evaluate_condition({"own_limbo_building": "Barracks"}, view))
        self.assertFalse(rules.evaluate_condition({"own_limbo_building": "Battle Lab"}, view))
        self.assertTrue(rules.evaluate_condition({"own_combat_count_below": 3}, view))

    def test_completed_building_requires_factory_flag(self):
        view = legal([own(1, "Allied Barracks", (10, 10),
                          abstract="ABSTRACT_TYPE_BUILDING", in_limbo=True)])
        view["factories"] = [{"owner_ptr": 1, "object_ptr": 1, "completed": False}]
        self.assertFalse(rules.evaluate_condition(
            {"own_completed_building": "Barracks"}, view))
        view["factories"] = [{"owner_ptr": 1, "object_ptr": 1, "completed": True}]
        self.assertTrue(rules.evaluate_condition(
            {"own_completed_building": "Barracks"}, view))

    def test_hp_below_and_boolean_combinators(self):
        view = legal([own(1, "Grizzly Battle Tank", (10, 10), hp=60)])
        self.assertTrue(rules.evaluate_condition(
            {"own_hp_below_pct": {"selector": "all_combat", "pct": 30}}, view))
        view2 = legal([own(1, "Grizzly Battle Tank", (10, 10), hp=250)])
        self.assertFalse(rules.evaluate_condition(
            {"own_hp_below_pct": {"selector": "all_combat", "pct": 30}}, view2))
        self.assertTrue(rules.evaluate_condition(
            {"all": [{"always": True}, {"not": {"money_below": 0}}]}, view))
        self.assertFalse(rules.evaluate_condition(
            {"any": [{"money_below": 0}, {"enemy_visible_count_at_least": 1}]}, view))


class CompileTests(unittest.TestCase):
    def test_attack_rule_compiles(self):
        view = legal([own(1, "Grizzly Battle Tank", (10, 10)),
                      own(2, "Grizzly Battle Tank", (11, 10))],
                     [enemy(100, "GI", (14, 10))])
        cmds, errors = rules.compile_rule_commands(
            {"id": "d", "then": [{"op": "ATTACK", "units": "all_combat",
                                  "target": "nearest_visible_enemy"}]}, view)
        self.assertEqual(errors, [])
        self.assertEqual(cmds[0]["op"], "ATTACK")
        self.assertEqual(cmds[0]["units"], [1, 2])
        self.assertEqual(cmds[0]["target"], 100)

    def test_place_rule_resolves_limbo_building(self):
        view = legal([own(7, "Allied Barracks", (0, 0),
                          abstract="ABSTRACT_TYPE_BUILDING", in_limbo=True)])
        cmds, errors = rules.compile_rule_commands(
            {"id": "p", "then": [{"op": "PLACE", "building": "own_limbo:Barracks",
                                  "cell": [30, 30]}]}, view)
        self.assertEqual(errors, [])
        self.assertEqual(cmds[0]["building"], 7)
        self.assertEqual(cmds[0]["cell"], [30, 30])

    def test_retreat_and_produce(self):
        view = legal([own(1, "Grizzly Battle Tank", (10, 10), hp=60)])
        cmds, _ = rules.compile_rule_commands(
            {"id": "r", "then": [{"op": "MOVE", "units": "all_combat", "cell": [5, 5]},
                                 {"op": "PRODUCE", "type": "IFV", "count": 2}]}, view)
        self.assertEqual(cmds[0], {"op": "MOVE", "units": [1], "cell": [5, 5]})
        self.assertEqual(cmds[1], {"op": "PRODUCE", "type": "IFV", "count": 2})


class RuleSetTests(unittest.TestCase):
    def test_cooldown_and_max_fires(self):
        view = legal([own(1, "Grizzly Battle Tank", (10, 10))],
                     [enemy(100, "GI", (12, 10))])
        rs = rules.RuleSet([{
            "id": "a", "when": {"always": True},
            "then": [{"op": "STOP", "units": "all_combat"}],
            "cooldown_frames": 60, "max_fires": 2}])
        self.assertEqual(len(rs.due(view, 100)), 1)
        self.assertEqual(len(rs.due(view, 130)), 0)   # cooldown
        self.assertEqual(len(rs.due(view, 161)), 1)
        self.assertEqual(len(rs.due(view, 300)), 0)   # max_fires reached
        self.assertEqual(rs.describe()[0]["fires"], 2)

    def test_rule_without_legal_target_does_not_fire(self):
        view = legal([own(1, "Grizzly Battle Tank", (10, 10))], [])
        rs = rules.RuleSet([{"id": "atk", "when": {"always": True},
                             "then": [{"op": "ATTACK", "units": "all_combat",
                                       "target": "nearest_visible_enemy"}],
                             "cooldown_frames": 1}])
        self.assertEqual(rs.due(view, 10), [])
        self.assertEqual(rs.state["atk"]["fires"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
