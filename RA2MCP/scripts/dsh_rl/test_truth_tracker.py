"""Unit tests for the V4 reward-only truth tracker.

These fixtures are plain dictionaries; no game/bridge is involved.
"""
import unittest

from dsh_rl.truth_tracker import (TruthTracker, KIND_BUILDING,
                                  KIND_INFANTRY, KIND_VEHICLE)

HOUSES = {1: "<human player>", 2: "Computer", 3: "Neutral"}
MAX_HP = {
    "GI": 125, "Guardian GI": 100, "Grizzly Battle Tank": 300,
    "Allied Ore Refinery": 1000, "Power Plant": 750,
    "Allied Construction Yard": 1000, "Construction Vehicle": 1000,
}


def rec(addr, type_name, owner, health, abstract, in_limbo=False, on_map=True):
    return {"addr": addr, "type": type_name, "owner": owner, "health": health,
            "abstract": abstract, "in_limbo": in_limbo, "on_map": on_map,
            "cell": [20, 20]}


def unit(addr, type_name="GI", owner=2, health=125, in_limbo=False):
    abstract = ("ABSTRACT_TYPE_INFANTRY" if "GI" in type_name
                else "ABSTRACT_TYPE_UNIT")
    return rec(addr, type_name, owner, health, abstract, in_limbo=in_limbo)


def building(addr, type_name="Power Plant", owner=2, health=750):
    return rec(addr, type_name, owner, health, "ABSTRACT_TYPE_BUILDING")


def tracker():
    return TruthTracker(max_hp=MAX_HP, hero_owner=1, confirm_frames=2)


class TestTruthTracker(unittest.TestCase):
    def setUp(self):
        self.t = tracker()

    def feed(self, *records):
        return self.t.observe(list(records), HOUSES)

    # -- kills ---------------------------------------------------------
    def test_damaged_enemy_needs_consecutive_confirmations_then_pays_once(self):
        self.feed(unit(7, health=125))
        hit = self.feed(unit(7, health=60))
        self.assertEqual(hit["enemy_damage_hp"], 65)
        first_miss = self.feed()
        self.assertEqual(first_miss["kills_infantry"], 0)   # still pending
        kill = self.feed()
        self.assertEqual(kill["kills_infantry"], 1)
        self.assertEqual(kill["killed_hp_credit"], 60)
        self.assertEqual(len(kill["kill_events"]), 1)
        # no double payment on later frames
        self.assertEqual(self.feed()["kills_infantry"], 0)

    def test_full_health_disappearance_is_not_a_kill(self):
        self.feed(unit(7, health=125))
        self.feed()
        out = self.feed()
        self.assertEqual(out["kills_infantry"], 0)
        self.assertEqual(out["sold_like_removals"], 1)
        self.assertEqual(out["killed_hp_credit"], 0)

    def test_reappear_cancels_removal(self):
        self.feed(unit(7, health=125))
        self.feed()                      # one missing frame -> pending
        back = self.feed(unit(7, health=125))
        self.assertEqual(back["reappear_cancels"], 1)
        self.feed()                      # missing again -> pending
        out = self.feed()                # confirmed removal, but never damaged
        self.assertEqual(out["kills_infantry"], 0)
        self.assertEqual(out["sold_like_removals"], 1)

    def test_vehicle_and_building_kind_classification(self):
        self.feed(unit(7, "Grizzly Battle Tank", health=300))
        self.feed(unit(7, "Grizzly Battle Tank", health=50))
        self.feed()
        out = self.feed()
        self.assertEqual(out["kills_vehicle"], 1)
        self.assertEqual(out["killed_hp_credit"], 50)

        self.feed(building(11, health=750))
        self.feed(building(11, health=300))
        self.feed()
        out = self.feed()
        self.assertEqual(out["building_destroyed"], 1)
        self.assertEqual(out["killed_hp_credit"], 300)

    # -- identity/reuse/limbo -----------------------------------------
    def test_address_reuse_with_new_type_never_pays_kill(self):
        self.feed(unit(21, "GI", health=100))
        out = self.feed(unit(21, "Grizzly Battle Tank", health=300))
        self.assertEqual(out["identity_changes"], 1)
        self.assertEqual(out["kills_infantry"], 0)
        self.assertEqual(out["kills_vehicle"], 0)
        # The replacement is a new object: its later disappearance is not a
        # kill either unless it is observed taking damage.
        self.feed()
        out = self.feed()
        self.assertEqual(out["sold_like_removals"], 1)

    def test_ownership_change_is_not_a_kill(self):
        self.feed(building(31, owner=2))
        out = self.feed(building(31, owner=1))
        self.assertEqual(out["identity_changes"], 1)
        self.assertEqual(out["building_destroyed"], 0)
        self.assertEqual(out["own_removed_hp"], 0)

    def test_limbo_transition_is_not_a_kill(self):
        self.feed(unit(41, health=125))
        self.feed(unit(41, health=90))
        out = self.feed(unit(41, health=90, in_limbo=True))
        self.assertEqual(out["limbo_transitions"], 1)
        self.assertEqual(out["kills_infantry"], 0)
        self.feed()
        self.assertEqual(self.feed()["kills_infantry"], 0)

    # -- own losses ----------------------------------------------------
    def test_own_damage_and_removal_are_counted_but_mcv_is_excluded(self):
        self.feed(unit(51, owner=1, health=125))
        out = self.feed(unit(51, owner=1, health=100))
        self.assertEqual(out["own_damage_hp"], 25)
        self.feed()
        out = self.feed()
        self.assertEqual(out["own_removed_hp"], 100)

        self.feed(unit(52, "Construction Vehicle", owner=1, health=1000))
        self.feed(unit(52, "Construction Vehicle", owner=1, health=1000))
        self.feed()                      # missing frame 1
        out = self.feed()                # missing frame 2 -> MCV conversion
        self.assertEqual(out["own_removed_hp"], 0)
        self.assertEqual(out["mcv_removals"], 1)

    def test_neutral_objects_never_pay(self):
        self.feed(unit(61, owner=3, health=125))
        out = self.feed(unit(61, owner=3, health=10))
        self.assertEqual(out["enemy_damage_hp"], 0)
        self.assertEqual(out["own_damage_hp"], 0)
        self.feed()
        out = self.feed()
        self.assertEqual(out["kills_infantry"], 0)

    def test_repair_is_not_penalised_and_redamage_is_credited(self):
        self.feed(unit(71, health=125))
        self.feed(unit(71, health=80))
        repaired = self.feed(unit(71, health=120))
        self.assertEqual(repaired["enemy_damage_hp"], 0)
        again = self.feed(unit(71, health=100))
        self.assertEqual(again["enemy_damage_hp"], 20)

    def test_snapshot_from_state_reads_full_object_array(self):
        from ra2yrproto import ra2yr
        from dsh_rl.truth_tracker import snapshot_from_state
        state = ra2yr.GameState(stage=ra2yr.STAGE_INGAME, current_frame=10)
        hero = state.houses.add()
        hero.self = 1
        hero.name = "<human player>"
        hero.is_human_player = True
        enemy = state.houses.add()
        enemy.self = 2
        enemy.name = "Computer"

        obj = state.objects.add()
        obj.pointer_self = 500
        obj.pointer_technotypeclass = 900
        obj.pointer_house = 2
        obj.health = 125
        obj.object_type = ra2yr.ABSTRACT_TYPE_INFANTRY
        obj.in_limbo = False
        obj.on_map = True
        obj.coordinates.x = 20 * 256
        obj.coordinates.y = 30 * 256

        records = snapshot_from_state(state, {900: "GI"})
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["addr"], 500)
        self.assertEqual(record["type"], "GI")
        self.assertEqual(record["owner"], 2)
        self.assertEqual(record["health"], 125)
        self.assertEqual(record["abstract"], "ABSTRACT_TYPE_INFANTRY")
        self.assertEqual(record["cell"], [20, 30])
        # The reward truth has no visibility/shroud fields by construction.
        self.assertNotIn("cell_shrouded", record)
        self.assertNotIn("is_visible", record)


if __name__ == "__main__":
    unittest.main()
