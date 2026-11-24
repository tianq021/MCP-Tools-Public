"""P2.0 action-space tests: target proximity ordering, 30-cell candidate range,
stable unit slots, legacy/new mapping compatibility.  All offline."""

import math
import unittest
from types import SimpleNamespace

from dsh_rl import full_spec as S
from dsh_rl.test_full_control import CATALOG, GROUPS, obj, row


def spec(version=S.ACTION_SPACE_VERSION, candidate_range=S.DEFAULT_TARGET_CANDIDATE_RANGE,
         nearest_k=S.DEFAULT_TARGET_NEAREST_K):
    return S.FullControlSpec(CATALOG, GROUPS, action_space_version=version,
                             target_candidate_range=candidate_range,
                             target_nearest_k=nearest_k)


def map_soa(w=200, h=200):
    return SimpleNamespace(map_width=w, map_height=h)


def unit(addr, cell, type_name="GI", kind="ABSTRACT_TYPE_INFANTRY", owner="<human player>"):
    rec = obj(addr, 200, type_name, kind, cell)
    rec["owner"] = owner
    return rec


def enemy(addr, cell, type_name="GI", kind="ABSTRACT_TYPE_INFANTRY", shrouded=False,
          cloak=0, owner="Computer"):
    rec = obj(addr, 900, type_name, kind, cell)
    rec.update({"owner": owner, "cell_shrouded": shrouded, "cloak_state": cloak})
    return rec


def combat_row(unit_cells, enemy_cells, buildings=((91, 58), (92, 58), (93, 58), (94, 58))):
    own = [obj(i + 1, 100, "Power Plant", "ABSTRACT_TYPE_BUILDING", cell)
           for i, cell in enumerate(buildings)]
    own += [unit(100 + i, cell) for i, cell in enumerate(unit_cells)]
    r = row(own)
    r["visible_enemies"] = [enemy(500 + i, cell) for i, cell in enumerate(enemy_cells)]
    return r


class TestTargetOrderingP20(unittest.TestCase):
    def test_targets_ordered_by_army_not_base_center(self):
        # Base near (91,58); army far east at (150,150). One enemy near the army,
        # one near the base.  The V2.1 mapping must prefer the army target.
        r = combat_row(unit_cells=[(150, 150), (151, 150)],
                       enemy_cells=[(155, 152), (93, 60)])
        v2 = spec(S.LEGACY_ACTION_SPACE_VERSION)
        v21 = spec(S.ACTION_SPACE_VERSION)
        legacy = v2.targets(r)
        modern = v21.targets(r)
        self.assertEqual(legacy[0]["addr"], 501)      # near base wins in legacy
        self.assertEqual(modern[0]["addr"], 500)      # near army wins in V2.1
        # action index mapping: slot 0 targets differ (semantic change!)
        slot0 = S.UNIT_START + 9
        self.assertEqual(v2.intent(slot0, r, [])["target_address"], 501)
        self.assertEqual(v21.intent(slot0, r, [])["target_address"], 500)

    def test_nearest_k_mean_is_used_for_ordering(self):
        # Three units cluster at x=90..92, one outlier at x=200.  Target A sits
        # right next to the outlier (k=1 loves it, k=3 rejects it); target B is
        # close to the whole cluster.
        r = combat_row(unit_cells=[(90, 100), (91, 100), (92, 100), (200, 100)],
                       enemy_cells=[(201, 100), (95, 100)])
        v21_k3 = spec(S.ACTION_SPACE_VERSION, nearest_k=3)
        order = [t["addr"] for t in v21_k3.targets(r)]
        self.assertEqual(order[0], 501)             # cluster target wins with k=3
        v21_k1 = spec(S.ACTION_SPACE_VERSION, nearest_k=1)
        self.assertEqual(v21_k1.targets(r)[0]["addr"], 500)  # outlier wins with k=1

    def test_ordering_is_deterministic_and_tie_broken_by_addr(self):
        r = combat_row(unit_cells=[(100, 100)], enemy_cells=[(105, 100), (105, 100)])
        v21 = spec(S.ACTION_SPACE_VERSION)
        first = [t["addr"] for t in v21.targets(r)]
        shuffled = dict(r)
        shuffled["visible_enemies"] = list(reversed(r["visible_enemies"]))
        second = [t["addr"] for t in v21.targets(shuffled)]
        self.assertEqual(first, second)
        self.assertEqual(first, sorted(first)[:len(first)])  # equal scores -> addr order


class TestCandidateRangeP20(unittest.TestCase):
    def test_thirty_cell_boundary_inclusive(self):
        # army at (100,100): offsets 29/30 are candidates, 31 is not.
        r = combat_row(unit_cells=[(100, 100)],
                       enemy_cells=[(100, 129), (100, 130), (100, 131)])
        v21 = spec(S.ACTION_SPACE_VERSION, candidate_range=30)
        addrs = [t["addr"] for t in v21.targets(r)]
        self.assertIn(500, addrs)
        self.assertIn(501, addrs)
        self.assertNotIn(502, addrs)
        stats = v21.target_stats(r)
        self.assertEqual(stats["visible_valid"], 3)
        self.assertEqual(stats["candidates"], 2)

    def test_configurable_range_20_30_40(self):
        r = combat_row(unit_cells=[(100, 100)],
                       enemy_cells=[(100, 125), (100, 135), (100, 139)])
        counts = {}
        for radius in (20, 30, 40):
            counts[radius] = len(spec(S.ACTION_SPACE_VERSION, candidate_range=radius).targets(r))
        self.assertEqual(counts[20], 0)
        self.assertEqual(counts[30], 1)
        self.assertEqual(counts[40], 3)

    def test_no_candidate_does_not_fabricate_targets_and_keeps_movement(self):
        r = combat_row(unit_cells=[(100, 100)], enemy_cells=[(20, 20)])
        v21 = spec(S.ACTION_SPACE_VERSION, candidate_range=30)
        self.assertEqual(v21.targets(r), [])
        mask = v21.masks(r, [[92, 59]], map_soa())
        # all attack slots disabled, but movement/scouting remains available
        for slot in range(S.TARGET_SLOTS):
            self.assertFalse(mask[S.UNIT_START + 9 + slot], slot)
        move_slot = S.UNIT_START + 1
        self.assertTrue(mask[move_slot])
        self.assertTrue(mask[S.UNIT_START])            # group STOP/move base leg
        # no candidates in observation target features
        stats = v21.target_stats(r)
        self.assertEqual(stats["candidates"], 0)
        self.assertIsNotNone(stats["nearest_enemy_dist"])

    def test_no_army_uses_non_building_fallback_not_fabricated(self):
        r = row([obj(1, 100, "Power Plant", "ABSTRACT_TYPE_BUILDING", (90, 90))])
        r["visible_enemies"] = [enemy(500, (92, 92)), enemy(501, (200, 200))]
        v21 = spec(S.ACTION_SPACE_VERSION, candidate_range=30)
        # fallback anchors = placed non-building own objects: none -> no candidates
        self.assertEqual(v21.targets(r), [])
        r2 = row([obj(1, 100, "Power Plant", "ABSTRACT_TYPE_BUILDING", (90, 90)),
                  unit(100, (91, 91))])
        r2["visible_enemies"] = [enemy(500, (92, 92)), enemy(501, (200, 200))]
        self.assertEqual([t["addr"] for t in v21.targets(r2)], [500])


class TestUnitSlotStabilityP20(unittest.TestCase):
    def test_surviving_units_keep_slots_when_row_order_changes(self):
        v21 = spec(S.ACTION_SPACE_VERSION)
        r1 = row([obj(1), unit(10, (10, 10)), unit(11, (11, 10)), unit(12, (12, 10))])
        identities1 = [S.unit_identity(o) for o in v21.units(r1)]
        self.assertEqual(identities1, [(10, "GI", "<human player>"),
                                       (11, "GI", "<human player>"),
                                       (12, "GI", "<human player>")])
        # Same units but the game returns a different own_objects order.
        r2 = row([unit(12, (12, 10)), obj(1), unit(10, (10, 10)), unit(11, (11, 10))])
        identities2 = [S.unit_identity(o) for o in v21.units(r2)]
        self.assertEqual(identities1, identities2)
        self.assertEqual(v21.unit_slot_diagnostics()["unit_slot_swaps"], 0)

    def test_new_unit_fills_lowest_free_slot_and_dead_unit_frees_it(self):
        v21 = spec(S.ACTION_SPACE_VERSION)
        r1 = row([obj(1), unit(10, (10, 10)), unit(11, (11, 10))])
        v21.units(r1)
        r2 = row([obj(1), unit(10, (10, 10)), unit(11, (11, 10)), unit(12, (12, 10))])
        self.assertEqual([S.unit_identity(o)[0] for o in v21.units(r2)], [10, 11, 12])
        r3 = row([obj(1), unit(11, (11, 10)), unit(12, (12, 10))])  # unit 10 died
        self.assertEqual([S.unit_identity(o)[0] for o in v21.units(r3)], [11, 12])
        r4 = row([obj(1), unit(11, (11, 10)), unit(12, (12, 10)), unit(13, (13, 10))])
        self.assertEqual([S.unit_identity(o)[0] for o in v21.units(r4)], [13, 11, 12])

    def test_legacy_ordering_swaps_slots_where_p20_does_not(self):
        legacy = spec(S.LEGACY_ACTION_SPACE_VERSION)
        v21 = spec(S.ACTION_SPACE_VERSION)
        r1 = row([obj(1), unit(10, (10, 10)), unit(11, (11, 10))])
        first_legacy = [S.unit_identity(o)[0] for o in legacy.units(r1)]
        v21.units(r1)
        # A new unit whose type sorts first arrives: legacy re-sorts everything,
        # V2.1 only fills the free slot.
        r2 = row([obj(1), unit(10, (10, 10)), unit(11, (11, 10)),
                  unit(12, (12, 10), "Attack Dog")])
        second_legacy = [S.unit_identity(o)[0] for o in legacy.units(r2)]
        second_p20 = [S.unit_identity(o)[0] for o in v21.units(r2)]
        self.assertEqual(first_legacy, [10, 11])
        self.assertEqual(second_legacy, [12, 10, 11])   # re-sorted (slot churn)
        self.assertEqual(second_p20, [10, 11, 12])      # sticky slots

    def test_individual_action_uses_stable_slot_identity(self):
        v21 = spec(S.ACTION_SPACE_VERSION)
        r1 = row([obj(1), unit(10, (10, 10)), unit(11, (11, 10))])
        v21.units(r1)
        # Row order changes: action slot 0 (selection=1) must still address the
        # sticky slot identity (unit 10), not "first in the sorted list".
        r2 = row([obj(1), unit(11, (11, 10)), unit(10, (10, 10))])
        command = v21.intent(S.UNIT_START + 1 * S.UNIT_ACTIONS, r2, [])
        self.assertEqual(command["object_addresses"], [10])

    def test_address_reuse_with_new_type_is_a_new_identity(self):
        v21 = spec(S.ACTION_SPACE_VERSION)
        r1 = row([obj(1), unit(10, (10, 10))])
        v21.units(r1)
        # Same pointer, different type -> a different bridge object; it must not
        # silently inherit the old slot identity.
        reused = unit(10, (10, 10), "Grizzly Battle Tank", "ABSTRACT_TYPE_UNIT")
        r2 = row([obj(1), reused])
        ordered = v21.units(r2)
        self.assertEqual([S.unit_identity(o) for o in ordered],
                         [(10, "Grizzly Battle Tank", "<human player>")])
        # Exact same addr+type+owner is indistinguishable without a stable ID and
        # is deliberately kept as the same identity (documented limitation).
        r3 = row([obj(1), unit(10, (10, 10))])
        r4 = row([obj(1), unit(10, (10, 10))])
        v21.units(r3)
        self.assertEqual([S.unit_identity(o) for o in v21.units(r4)],
                         [(10, "GI", "<human player>")])


class TestTargetFilteringP20(unittest.TestCase):
    def test_hidden_cloaked_neutral_aircraft_are_not_candidates(self):
        r = combat_row(unit_cells=[(100, 100)], enemy_cells=[])
        r["visible_enemies"] = [
            enemy(600, (101, 100)),                              # valid
            enemy(601, (101, 100), shrouded=True),               # hidden
            enemy(602, (101, 100), cloak=2),                     # cloaked
            enemy(603, (101, 100), owner="Neutral"),             # neutral
            enemy(604, (101, 100), kind="ABSTRACT_TYPE_AIRCRAFT"),  # aircraft
        ]
        v21 = spec(S.ACTION_SPACE_VERSION)
        self.assertEqual([t["addr"] for t in v21.targets(r)], [600])
        self.assertEqual(v21.target_stats(r)["visible_valid"], 1)

    def test_hidden_target_never_masks_into_action_space(self):
        r = combat_row(unit_cells=[(100, 100)], enemy_cells=[])
        r["visible_enemies"] = [enemy(600, (101, 100), shrouded=True)]
        v21 = spec(S.ACTION_SPACE_VERSION)
        mask = v21.masks(r, [], map_soa())
        self.assertFalse(mask[S.UNIT_START + 9])


class TestActionSpaceCompatibility(unittest.TestCase):
    def test_action_and_observation_dimensions_unchanged(self):
        self.assertEqual(S.ACTION_N, 301)
        self.assertEqual(S.EXTRA_DIM,
                         S.MAX_TYPES * 10 + S.UNIT_SLOTS * S.UNIT_FEATURES
                         + S.TARGET_SLOTS * 6 + S.PENDING_SLOTS * 4 + S.ACTION_N)
        v2 = spec(S.LEGACY_ACTION_SPACE_VERSION)
        v21 = spec(S.ACTION_SPACE_VERSION)
        r = combat_row(unit_cells=[(100, 100)], enemy_cells=[(105, 100)])
        features2 = v2.features(r, [], map_soa())
        features21 = v21.features(r, [], map_soa())
        self.assertEqual(features2.shape, features21.shape)
        self.assertEqual(len(v2.masks(r, [], map_soa())), len(v21.masks(r, [], map_soa())))

    def test_manifest_records_action_space_version(self):
        v21 = spec(S.ACTION_SPACE_VERSION, candidate_range=25)
        manifest = v21.manifest()
        self.assertEqual(manifest["action_n"], S.ACTION_N)
        self.assertEqual(manifest["action_space_version"], S.ACTION_SPACE_VERSION)
        self.assertEqual(manifest["target_candidate_range"], 25.0)
        self.assertTrue(manifest["unit_slots_stable"])
        legacy = spec(S.LEGACY_ACTION_SPACE_VERSION).manifest()
        self.assertEqual(legacy["action_space_version"], S.LEGACY_ACTION_SPACE_VERSION)
        self.assertFalse(legacy["unit_slots_stable"])
        self.assertEqual(legacy["catalog_sha256"], manifest["catalog_sha256"])

    def test_invalid_action_space_configuration_rejected(self):
        with self.assertRaises(ValueError):
            S.FullControlSpec(CATALOG, GROUPS, action_space_version="nope")
        with self.assertRaises(ValueError):
            S.FullControlSpec(CATALOG, GROUPS, target_candidate_range=0)
        with self.assertRaises(ValueError):
            S.FullControlSpec(CATALOG, GROUPS, target_candidate_range=float("nan"))


if __name__ == "__main__":
    unittest.main()
