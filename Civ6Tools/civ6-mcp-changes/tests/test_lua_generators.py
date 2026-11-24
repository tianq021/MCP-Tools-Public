"""Regression tests for the Lua generators fixed during the T57-T66 siege.

These assert on the *generated* Lua text, because the defects they cover were
only observable in-game (undefended-city attacks, enemy-city move diagnostics,
city HP disappearing once walls were breached, the Great Person class lookup,
and the ranged-vs-melee estimate mismatch).
"""

from civ_mcp.game_state import _extract_city_defense
from civ_mcp.lua.great_people import build_gp_advisor_query
from civ_mcp.lua.units import (
    build_attack_followup_query,
    build_attack_unit,
    build_combat_estimate_query,
    build_move_unit,
    build_unit_position_query,
)


class TestGreatPersonAdvisor:
    def test_uses_great_person_classes_table(self):
        lua = build_gp_advisor_query(1703950)
        # Units has no GreatPersonClass column, so the class must come from
        # GreatPersonClasses (class -> UnitType -> DistrictType).
        assert "GameInfo.GreatPersonClasses()" in lua
        assert ".GreatPersonClassType" in lua
        assert ".DistrictType" in lua

    def test_no_hardcoded_class_to_district_table(self):
        lua = build_gp_advisor_query(1703950)
        assert "classToDistrict" not in lua


class TestEnemyCityMoveDiagnostic:
    def test_move_diagnostic_names_enemy_city(self):
        lua = build_unit_position_query(123, 6, 13)
        assert "ENEMY_CITY" in lua
        assert "plot:IsCity()" in lua


class TestCityDefenseReporting:
    def test_followup_always_emits_city_def(self):
        lua = build_attack_followup_query(6, 13)
        # The old generator wrapped the print in `if wMax > 0 or gMax > 0`,
        # which suppressed city HP entirely once the walls were breached.
        assert "if wMax > 0 or gMax > 0" not in lua
        assert 'print("CITY_DEF|' in lua

    def test_parse_city_def_with_zero_walls(self):
        assert _extract_city_defense(["CITY_DEF|wall:0/0|garrison:180/200"]) == (
            0,
            0,
            180,
            200,
        )

    def test_parse_city_def_with_walls(self):
        assert _extract_city_defense(["CITY_DEF|wall:59/100|garrison:183/200"]) == (
            59,
            100,
            183,
            200,
        )


class TestCombatEstimateRangedDetection:
    def test_estimate_mirrors_attack_ranged_decision(self):
        lua = build_combat_estimate_query(1, 2, 3)
        # Previously: `isRanged = attRS > 0 and dist > 1`, which made an archer
        # firing at an adjacent enemy look like a melee attacker.
        assert "attRS > 0 and dist > 1" not in lua
        assert "UnitOperationTypes.RANGE_ATTACK" in lua


class TestMeleeAttackCheckOrder:
    def test_zoc_checked_before_movement(self):
        lua = build_attack_unit(1, 2, 3)
        zoc = lua.find("HasMovedIntoZOC")
        moves = lua.find("no movement points for melee attack")
        assert zoc != -1 and moves != -1
        assert zoc < moves


class TestUndefendedCityAttack:
    """Characterises the remaining gap so a future fix has a failing marker."""

    def test_attack_targets_only_units(self):
        lua = build_attack_unit(1, 6, 13)
        # Still unit-only today: report it explicitly instead of silently
        # assuming the city case is handled.
        assert "Map.GetUnitsAt(6, 13)" in lua


class TestEndTurnWaitBudget:
    """The MCP client cancels a tool call at toolCallTimeoutMs (180 s here).

    A server-side poll budget longer than that guarantees a client-side cancel,
    which skips end_turn's flag cleanup and strands _pending_end_turn.
    """

    CLIENT_TIMEOUT_S = 180.0

    def test_wait_cap_stays_under_client_timeout(self):
        from civ_mcp.end_turn import _MAX_TURN_WAIT_S

        # Leave room for Phase 3 diagnostics (~15-20 s of round trips).
        assert _MAX_TURN_WAIT_S + 20 <= self.CLIENT_TIMEOUT_S

    def test_wait_cap_is_not_too_short(self):
        from civ_mcp.end_turn import _MAX_TURN_WAIT_S

        # A normal end_turn takes ~15 s; the cap must not fire on healthy turns.
        assert _MAX_TURN_WAIT_S >= 30


class TestTurnReportHeader:
    def test_equal_turns_do_not_look_like_advance(self):
        from civ_mcp.game_state import GameState

        report = GameState._build_turn_report(60, 60, [], [])
        assert "no advance" in report
        assert "60 -> 60" not in report

    def test_real_advance_keeps_arrow_form(self):
        from civ_mcp.game_state import GameState

        report = GameState._build_turn_report(60, 61, [], [])
        assert "60 -> 61" in report


class TestStackingRule:
    """Civ 6 rule: units of DIFFERENT FormationClass may share a tile.

    Only units of the SAME FormationClass cannot stack (e.g. land combat +
    support + civilian/great person is legal). The old move pre-check compared
    a boolean "isCivilian", which wrongly blocked combat+support.
    """

    def test_move_precheck_compares_formation_class(self):
        lua = build_move_unit(1, 30, 15)
        assert "FormationClass" in lua
        assert "myClass" in lua and "otherClass" in lua
        # The coarse binary heuristic must be gone.
        assert "isCivilian" not in lua

    def test_move_precheck_allows_different_classes(self):
        lua = build_move_unit(1, 30, 15)
        assert "myClass == otherClass" in lua
        assert "Same FormationClass cannot stack" in lua

    def test_purchase_precheck_still_compares_class(self):
        from civ_mcp.lua.cities import build_purchase_item

        lua = build_purchase_item(1, "UNIT", "UNIT_ARCHER")
        assert "FormationClass == targetClass" in lua


class TestUnitFormationClass:
    def test_units_query_emits_formation_class(self):
        from civ_mcp.lua.units import build_units_query

        lua = build_units_query()
        assert "entry.FormationClass" in lua

    def test_parse_units_response_reads_class_field(self):
        from civ_mcp.lua.units import parse_units_response

        line = (
            "131073|1|勇士|UNIT_WARRIOR|39,16|3/3|100/100|20|0|0||0|0||0||"
            "|FORMATION_CLASS_LAND_COMBAT"
        )
        units = parse_units_response([line])
        assert len(units) == 1
        assert units[0].formation_class == "FORMATION_CLASS_LAND_COMBAT"

    def test_narration_tags_class(self):
        from civ_mcp import narrate as nr
        from civ_mcp.lua.models import UnitInfo

        u = UnitInfo(
            unit_id=1, unit_index=1, name="破城槌", unit_type="UNIT_BATTERING_RAM",
            x=1, y=2, moves_remaining=1, max_moves=2, health=100, max_health=100,
            formation_class="FORMATION_CLASS_SUPPORT",
        )
        text = nr.narrate_units([u])
        assert "[支援]" in text


class TestStackingIssuesTool:
    def test_query_emits_conflicts_and_multi_class_tiles(self):
        from civ_mcp.lua.units import build_stacking_issues_query

        lua = build_stacking_issues_query()
        assert "STACK|" in lua
        assert "MULTI|" in lua
        assert "FormationClass" in lua

    def test_parse_stacking_issues(self):
        from civ_mcp.lua.units import parse_stacking_issues

        conflicts, tiles = parse_stacking_issues(
            [
                "STACK|30,15|FORMATION_CLASS_LAND_COMBAT|UNIT_WARRIOR#65537,UNIT_ARCHER#65538",
                "MULTI|31,16|3|FORMATION_CLASS_LAND_COMBAT,FORMATION_CLASS_SUPPORT,FORMATION_CLASS_CIVILIAN",
                "STACKCOUNT|1|7",
            ]
        )
        assert len(conflicts) == 1
        assert conflicts[0].x == 30 and conflicts[0].y == 15
        assert conflicts[0].formation_class == "FORMATION_CLASS_LAND_COMBAT"
        assert conflicts[0].units == ["UNIT_WARRIOR#65537", "UNIT_ARCHER#65538"]
        assert len(tiles) == 1
        assert tiles[0].count == 3
        assert tiles[0].formation_classes[1] == "FORMATION_CLASS_SUPPORT"

    def test_narration_reports_clean_and_dirty(self):
        from civ_mcp import narrate as nr
        from civ_mcp.lua.models import StackingConflict

        clean = nr.narrate_stacking_issues([], [])
        assert "No illegal stacks" in clean
        dirty = nr.narrate_stacking_issues(
            [StackingConflict(x=3, y=4, formation_class="FORMATION_CLASS_LAND_COMBAT",
                              units=["UNIT_WARRIOR#1", "UNIT_ARCHER#2"])],
            [],
        )
        assert "(3,4)" in dirty and "陆战" in dirty
