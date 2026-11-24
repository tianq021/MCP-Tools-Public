"""Full ground-control action contract. Legality filters never choose an action.

Production order, timing, placement sector, controlled units, movement direction
and visible attack target are policy decisions. The native game is the final
authority for buildability/placement; this conservative mask covers exposed
vanilla prerequisites and excludes unsupported naval/air production.

P2.0 (2026-10-30) adds a *new action-space version* with identical dimensions:

* ``full_ground_v2p1``: attack target slots are ordered by proximity to the
  actual fighting force (not to the base centre), candidates are limited to a
  configurable engagement radius (default 30 cells, NOT a weapon range and NOT
  a path-reachability test), and individual-unit slots are stable across frames
  (sticky identity slots instead of re-sorting every capture).
* ``full_ground_v2``: the legacy mapping is kept for evaluation of old
  checkpoints and for A/B comparison. The action/observation dimensions are
  identical between versions, but the *meaning* of target slot ``i`` and unit
  slot ``i`` changed, so old checkpoints must not be resumed without an
  explicit opt-in (see train_shared.py ``--allow-action-space-change``).
"""
import hashlib
import json
import math
from collections import Counter

import numpy as np
from dsh_episode.hostility import is_opponent

# Legacy version string kept for the base contract fields / old manifests.
VERSION = "full_ground_v2"
ACTION_SPACE_VERSION = "full_ground_v2p1"
LEGACY_ACTION_SPACE_VERSION = "full_ground_v2"
ACTION_SPACE_VERSIONS = (LEGACY_ACTION_SPACE_VERSION, ACTION_SPACE_VERSION)
DEFAULT_TARGET_CANDIDATE_RANGE = 30.0
DEFAULT_TARGET_NEAREST_K = 3
MAX_TYPES, UNIT_SLOTS, TARGET_SLOTS, PENDING_SLOTS = 96, 8, 8, 4
DIRECTIONS = ((1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1))
# One decision step covers a longer leg than 4 cells: with a memoryless policy a
# 4-cell goal is re-rolled almost every step and the squad jitters in place.
MOVE_CELLS = 10
# The env appends the previous action (category + move direction) so the policy
# can hold a heading instead of re-deciding it from scratch every second.
HISTORY_DIM = 13
PRODUCE_START = 2
PLACE_START = PRODUCE_START + MAX_TYPES
UNIT_START = PLACE_START + PENDING_SLOTS * len(DIRECTIONS)
UNIT_ACTIONS = 1 + len(DIRECTIONS) + TARGET_SLOTS + 2
ACTION_N = UNIT_START + (UNIT_SLOTS + 1) * UNIT_ACTIONS
# Each controlled-unit slot also carries its distance/direction to the public
# enemy spawn so the policy knows where to attack without memorising the map.
UNIT_FEATURES = 8 + 3
EXTRA_DIM = MAX_TYPES * 10 + UNIT_SLOTS * UNIT_FEATURES + TARGET_SLOTS * 6 + PENDING_SLOTS * 4 + ACTION_N
GROUP_IDS = {-1: "power", -2: "factory", -3: "barracks", -4: "radar", -5: "tech", -6: "proc"}
KINDS = ("ABSTRACT_TYPE_BUILDINGTYPE", "ABSTRACT_TYPE_INFANTRYTYPE", "ABSTRACT_TYPE_UNITTYPE")
# Non-fighting mobile objects that must not anchor target sorting (they would
# farm proximity by driving to ore fields or the enemy base on their own).
_NON_COMBAT_HINTS = ("Construction Vehicle", "Miner", "Harvester", "Chrono Miner")


def country_allowed(t, bit):
    # Native RequiredHouses/ForbiddenHouses use -1 for an unspecified list.
    # Do not interpret that sentinel as "every country is forbidden".
    unspecified = (None, 0, -1, 0xFFFFFFFF)
    required, forbidden = t.get("required_houses"), t.get("forbidden_houses")
    return ((required in unspecified or bool(required & bit))
            and (forbidden in unspecified or not forbidden & bit))


def is_building(obj):
    return obj.get("abstract") == "ABSTRACT_TYPE_BUILDING"


def is_mcv(obj):
    return "Construction Vehicle" in obj.get("type_name", "")


def placed(obj):
    return obj.get("on_map") and not obj.get("in_limbo") and obj.get("health", 0) > 0


def all_units(row):
    return sorted([o for o in row.get("own_objects", []) if placed(o) and o.get("abstract") in
                   ("ABSTRACT_TYPE_INFANTRY", "ABSTRACT_TYPE_UNIT")],
                  key=lambda o: (o.get("type_name", ""), o["addr"]))


def combat_units(row):
    """Placed mobile units that fight: no buildings, no MCV, no miners."""
    result = []
    for o in row.get("own_objects", []):
        if not placed(o) or is_building(o) or is_mcv(o):
            continue
        name = o.get("type_name", "")
        if any(hint in name for hint in _NON_COMBAT_HINTS):
            continue
        result.append(o)
    return result


def unit_identity(obj):
    """Temporary identity for the stable unit slots (bridge has no stable ID)."""
    return (int(obj.get("addr") or 0), str(obj.get("type_name") or "?"),
            str(obj.get("owner") or ""))


def units(row):
    """Legacy unit ordering: deterministic sort by (type, addr), first 8."""
    return all_units(row)[:UNIT_SLOTS]


def _unit_proximity(row):
    """Positions used to judge target proximity.

    Primary anchors are the actually fighting units.  Only when no combat unit
    exists (e.g. opening MCV moment) fall back to all placed non-building units
    so the action space does not become silently unusable.
    """
    anchors = [o["cell"] for o in combat_units(row)]
    if anchors:
        return anchors
    return [o["cell"] for o in row.get("own_objects", []) if placed(o) and not is_building(o)]


def _valid_enemy_targets(row):
    """Visible, non-cloaked ground enemies: the only legal attack candidates."""
    return [o for o in row.get("visible_enemies", [])
            if is_opponent(o)
            and o.get("cell_shrouded") is False
            and o.get("cloak_state", 0) not in (1, 2)
            and o.get("abstract") != "ABSTRACT_TYPE_AIRCRAFT"]


def legacy_targets(row):
    """V2 ordering: distance to the centre of all own objects (base centre)."""
    own = row.get("own_objects", [])
    center = tuple(sum(o["cell"][i] for o in own) / max(1, len(own)) for i in (0, 1))
    return sorted(_valid_enemy_targets(row),
                  key=lambda o: (math.dist(center, o["cell"]), o["addr"]))[:TARGET_SLOTS]


def _target_proximity(row, nearest_k=DEFAULT_TARGET_NEAREST_K):
    """Per-target proximity score from the fighting force.

    Returns ``(target, score, nearest_distance)`` where ``score`` is the mean
    distance to the ``nearest_k`` closest combat units and ``nearest_distance``
    is the distance to the single closest combat unit.  The nearest-k mean is
    used for ordering because it measures "how much of the army can engage
    this target" and is not dominated by one outlier unit, unlike a raw army
    centroid (which a single distant scout can drag around).
    """
    anchors = _unit_proximity(row)
    if not anchors:
        return []
    scored = []
    for target in _valid_enemy_targets(row):
        distances = sorted(math.dist(anchor, target["cell"]) for anchor in anchors)
        k = max(1, min(int(nearest_k), len(distances)))
        score = sum(distances[:k]) / k
        scored.append((target, score, distances[0]))
    return scored


def p20_targets(row, candidate_range=DEFAULT_TARGET_CANDIDATE_RANGE,
                nearest_k=DEFAULT_TARGET_NEAREST_K):
    """V2.1 ordering: candidates near the fighting force, ordered by nearest-k mean."""
    scored = _target_proximity(row, nearest_k)
    limit = float(candidate_range)
    candidates = [(target, score, nearest) for target, score, nearest in scored
                  if nearest <= limit]
    candidates.sort(key=lambda item: (round(item[1], 6), round(item[2], 6),
                                      int(item[0]["addr"])))
    return [target for target, _score, _nearest in candidates[:TARGET_SLOTS]]


def target_stats(row, version=ACTION_SPACE_VERSION,
                 candidate_range=DEFAULT_TARGET_CANDIDATE_RANGE,
                 nearest_k=DEFAULT_TARGET_NEAREST_K):
    """Diagnostics for coverage reports (never used by the observation)."""
    valid = _valid_enemy_targets(row)
    scored = _target_proximity(row, nearest_k)
    anchors = len(_unit_proximity(row))
    if not scored:
        return {"action_space_version": version, "visible_valid": len(valid),
                "candidates": 0, "anchors": anchors, "nearest_enemy_dist": None,
                "candidate_nearest_dist": None,
                "candidate_range": float(candidate_range)}
    nearest_enemy = min(nearest for _t, _s, nearest in scored)
    if version == LEGACY_ACTION_SPACE_VERSION:
        candidates = legacy_targets(row)
        candidate_min = min((min(math.dist(anchor, target["cell"])
                                 for anchor in _unit_proximity(row))
                             for target in candidates), default=None)
    else:
        near = [nearest for _t, _s, nearest in scored if nearest <= float(candidate_range)]
        candidates = p20_targets(row, candidate_range, nearest_k)
        candidate_min = min(near) if near else None
    return {"action_space_version": version, "visible_valid": len(valid),
            "candidates": len(candidates), "anchors": anchors,
            "nearest_enemy_dist": round(float(nearest_enemy), 2),
            "candidate_nearest_dist": None if candidate_min is None else round(float(candidate_min), 2),
            "candidate_range": float(candidate_range)}


def pending(row):
    completed = {f.get("object_ptr") for f in row.get("factories", []) if f.get("completed")}
    return sorted([o for o in row.get("own_objects", []) if is_building(o) and o["addr"] in completed
                   and (o.get("in_limbo") or not o.get("on_map"))], key=lambda o: o["addr"])[:PENDING_SLOTS]


def base_cell(row):
    return next((o["cell"] for o in row.get("own_objects", [])
                 if placed(o) and "Construction Yard" in o.get("type_name", "")), None)


def enemy_start(row, starts):
    """Public spawn farthest from our base: on these skirmish maps that is the
    opponent's spawn. The reference is the base (construction yard, else the
    building centroid) so an advancing army never moves the target itself."""
    starts = [s for s in (starts or [])]
    if not starts:
        return None
    anchor = base_cell(row)
    if anchor is None:
        buildings = [o for o in row.get("own_objects", []) if placed(o) and is_building(o)]
        if not buildings:
            return None
        anchor = [sum(o["cell"][i] for o in buildings) / len(buildings) for i in (0, 1)]
    return max(starts, key=lambda s: math.dist(anchor, s))


class FullControlSpec:
    def __init__(self, catalog, groups, country_index=0, tech_level=10,
                 action_space_version=LEGACY_ACTION_SPACE_VERSION,
                 target_candidate_range=DEFAULT_TARGET_CANDIDATE_RANGE,
                 target_nearest_k=DEFAULT_TARGET_NEAREST_K):
        if action_space_version not in ACTION_SPACE_VERSIONS:
            raise ValueError(f"unknown action_space_version: {action_space_version!r}")
        try:
            candidate_range = float(target_candidate_range)
        except (TypeError, ValueError) as exc:
            raise ValueError("target_candidate_range must be numeric") from exc
        if not math.isfinite(candidate_range) or candidate_range <= 0:
            raise ValueError("target_candidate_range must be a positive finite number")
        self.action_space_version = action_space_version
        self.target_candidate_range = candidate_range
        self.target_nearest_k = max(1, int(target_nearest_k))
        self.all_types = {int(t["pointer_self"]): dict(t) for t in catalog}
        bit = 1 << country_index
        self.types = sorted([dict(t) for t in catalog if t.get("type") in KINDS
                             and t.get("cost", 0) > 0 and 0 <= t.get("tech_level", -1) <= tech_level
                             and not t.get("naval") and t.get("owner_flags", 0) & bit
                             and country_allowed(t, bit)],
                            key=lambda t: (t["type"], t["name"], t["array_index"]))
        if len(self.types) > MAX_TYPES:
            raise ValueError("production catalog exceeds action contract; revise schema instead of truncating")
        self.groups = {k: set(v) for k, v in groups.items()}
        self.slot_by_ptr = {int(t["pointer_self"]): i for i, t in enumerate(self.types)}
        self.failed = {}
        self.sent_until = {}
        # Sticky unit slots for the V2.1 action space.  Slot index is what the
        # policy sees/selects, so surviving units must keep their slot instead
        # of being re-sorted every frame (legacy ordering swaps slots whenever
        # the sorted list changes, which confuses both observation and action).
        self.unit_slot_identities = [None] * UNIT_SLOTS
        self._unit_slot_prev = {}
        self._unit_slot_frames = 0
        self._unit_slot_swaps = 0
        self._synced_row = None

    # ------------------------------------------------------------------ slots
    def _sync_unit_slots(self, row):
        if self._synced_row is row:
            return
        self._synced_row = row
        candidates = sorted({unit_identity(o) for o in all_units(row)})
        current = set(candidates)
        slots = self.unit_slot_identities
        for index, identity in enumerate(slots):
            if identity is not None and identity not in current:
                slots[index] = None
        present = {identity for identity in slots if identity is not None}
        for identity in candidates:
            if identity in present:
                continue
            free = next((i for i, value in enumerate(slots) if value is None), None)
            if free is None:
                break  # unit beyond UNIT_SLOTS: still controllable by the group action
            slots[free] = identity
            present.add(identity)
        # Diagnostics: a surviving identity must never change its slot in V2.1.
        now_slots = {identity: i for i, identity in enumerate(slots) if identity is not None}
        if self._unit_slot_frames:
            for identity, index in now_slots.items():
                previous = self._unit_slot_prev.get(identity)
                if previous is not None and previous != index:
                    self._unit_slot_swaps += 1
        self._unit_slot_prev = now_slots
        self._unit_slot_frames += 1

    def unit_slot_diagnostics(self):
        frames = max(1, self._unit_slot_frames)
        return {"unit_slot_frames": self._unit_slot_frames,
                "unit_slot_swaps": self._unit_slot_swaps,
                "unit_slot_swaps_per_frame": round(self._unit_slot_swaps / frames, 4)}

    def ordered_units(self, row):
        """V2.1: units in sticky slot order (empty slots skipped)."""
        self._sync_unit_slots(row)
        by_identity = {unit_identity(o): o for o in all_units(row)}
        return [by_identity[identity] for identity in self.unit_slot_identities
                if identity is not None and identity in by_identity]

    def units(self, row):
        if self.action_space_version == LEGACY_ACTION_SPACE_VERSION:
            return units(row)
        return self.ordered_units(row)

    def select_units(self, row, selection):
        if not selection:
            return all_units(row)
        if self.action_space_version == LEGACY_ACTION_SPACE_VERSION:
            return all_units(row)[selection - 1:selection]
        ordered = self.ordered_units(row)
        return [ordered[selection - 1]] if selection - 1 < len(ordered) else []

    def targets(self, row):
        if self.action_space_version == LEGACY_ACTION_SPACE_VERSION:
            return legacy_targets(row)
        return p20_targets(row, self.target_candidate_range, self.target_nearest_k)

    # -------------------------------------------------------------- contract
    def manifest(self):
        # Native pointers change between launches and are never part of model semantics.
        rows = [{k: t.get(k) for k in ("name", "type", "array_index", "cost", "prerequisites")}
                for t in self.types]
        return {"version": VERSION,
                "action_space_version": self.action_space_version,
                "target_candidate_range": self.target_candidate_range,
                "target_nearest_k": self.target_nearest_k,
                "unit_slots_stable": self.action_space_version != LEGACY_ACTION_SPACE_VERSION,
                "action_n": ACTION_N, "production_types": rows,
                "catalog_sha256": hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest(),
                "mask_authority": "conservative exposed metadata; native game validates final order"}

    def _signature(self, row):
        return tuple(sorted(o["type_ptr"] for o in row.get("own_objects", []) if is_building(o) and placed(o)))

    def can_produce(self, slot, row):
        if slot >= len(self.types):
            return False
        t = self.types[slot]
        if self.failed.get(slot) == self._signature(row):
            return False
        if row.get("t", 0) < self.sent_until.get(slot, -1) or row.get("money", 0) < t["cost"]:
            return False
        buildings = [o for o in row.get("own_objects", []) if is_building(o) and placed(o)]
        indices = {self.all_types.get(o.get("type_ptr"), {}).get("array_index") for o in buildings}
        group = {name: bool(indices & members) for name, members in self.groups.items()}
        if t["type"] == KINDS[0] and base_cell(row) is None:
            return False
        if t["type"] == KINDS[1] and not group.get("barracks"):
            return False
        if t["type"] == KINDS[2] and not group.get("factory"):
            return False
        for req in t.get("prerequisites", []):
            if req >= 0 and req not in indices:
                return False
            if req < 0 and not group.get(GROUP_IDS.get(req, "unknown")):
                return False
        if any(t.get(k) for k in ("requires_stolen_allied_tech", "requires_stolen_soviet_tech", "requires_stolen_third_tech")):
            return False  # Full stolen-tech rules are not part of the exposed contract yet.
        limit = t.get("build_limit", 0)
        if limit > 0 and sum(o.get("type_ptr") == t["pointer_self"] for o in row.get("own_objects", [])) >= limit:
            return False
        own_by_addr = {o["addr"]: o for o in row.get("own_objects", [])}
        family = {KINDS[0]: "ABSTRACT_TYPE_BUILDING", KINDS[1]: "ABSTRACT_TYPE_INFANTRY", KINDS[2]: "ABSTRACT_TYPE_UNIT"}[t["type"]]
        for f in row.get("factories", []):
            current = own_by_addr.get(f.get("object_ptr"), {})
            if current.get("abstract") == family and (f.get("object_ptr") or f.get("queued_objects")):
                return False
        return True

    def intent(self, action, row, starts, map_soa=None):
        if action == 0:
            return None
        if action == 1:
            mcv = next((o for o in all_units(row) if is_mcv(o)), None)
            return {"action": "DEPLOY", "object_addresses": [mcv["addr"]]} if mcv else None
        if PRODUCE_START <= action < PLACE_START:
            slot = action - PRODUCE_START
            if slot >= len(self.types):
                return None
            t = self.types[slot]
            return {"action": "PRODUCE", "object_addresses": [], "production_type": t["name"],
                    "production_type_ptr": t["pointer_self"], "production_action": "BEGIN"}
        if PLACE_START <= action < UNIT_START:
            which, direction = divmod(action - PLACE_START, len(DIRECTIONS))
            ready, base = pending(row), base_cell(row)
            if which >= len(ready) or base is None:
                return None
            building = ready[which]
            dx, dy = DIRECTIONS[direction]
            return {"action": "PLACE", "object_addresses": [], "building_address": building["addr"],
                    "production_type": building["type_name"], "production_type_ptr": building["type_ptr"],
                    "anchor": [base[0] + 5 * dx, base[1] + 5 * dy]}
        if not UNIT_START <= action < ACTION_N:
            return None
        selection, command = divmod(action - UNIT_START, UNIT_ACTIONS)
        controlled = self.select_units(row, selection)
        if not controlled:
            return None
        addrs = [o["addr"] for o in controlled]
        if command == 0:
            return {"action": "STOP", "object_addresses": addrs}
        center = [sum(o["cell"][i] for o in controlled) / len(controlled) for i in (0, 1)]
        if 1 <= command <= 8:
            dx, dy = DIRECTIONS[command - 1]
            goal = [round(center[0] + dx * MOVE_CELLS), round(center[1] + dy * MOVE_CELLS)]
            if map_soa is None:
                return None
            x, y = goal
            w, h = int(map_soa.map_width), int(map_soa.map_height)
            # Only refuse off-map legs. Requiring the goal cell to be already
            # explored used to mask every outward move, which made scouting the
            # single 1/19-wide SCOUT action; the native game resolves blocking
            # terrain and the unit still discovers cells on the way.
            if not (0 <= x < w and 0 <= y < h):
                return None
        elif 9 <= command < 9 + TARGET_SLOTS:
            visible = self.targets(row)
            slot = command - 9
            if slot >= len(visible):
                return None
            target = visible[slot]
            return {"action": "ATTACK", "object_addresses": addrs, "target_address": target["addr"],
                    "target_type": target["type_name"], "coordinates": [int(c * 256 + 128) for c in target["cell"]]}
        else:
            base = base_cell(row)
            if base is None:
                return None
            if command == 9 + TARGET_SLOTS:
                choices = [s for s in starts if math.dist(s, base) > 12]
                if not choices:
                    return None
                goal = min(choices, key=lambda s: math.dist(s, center))
            else:
                goal = base
        return {"action": "MOVE", "object_addresses": addrs, "coordinates": [int(c * 256 + 128) for c in goal]}

    def masks(self, row, starts, map_soa=None):
        mask = np.zeros(ACTION_N, dtype=bool)
        mask[0] = True
        mask[1] = any(is_mcv(o) for o in all_units(row))
        for slot in range(len(self.types)):
            mask[PRODUCE_START + slot] = self.can_produce(slot, row)
        for action in range(PLACE_START, ACTION_N):
            mask[action] = self.intent(action, row, starts, map_soa) is not None
        return mask

    def target_stats(self, row):
        return target_stats(row, self.action_space_version,
                            self.target_candidate_range, self.target_nearest_k)

    def features(self, row, starts, map_soa=None):
        by_type = Counter(o.get("type_ptr") for o in row.get("own_objects", []) if placed(o))
        unplaced = Counter(o.get("type_ptr") for o in row.get("own_objects", []) if not placed(o))
        out = []
        for slot in range(MAX_TYPES):
            if slot >= len(self.types):
                out += [0.0] * 10
                continue
            t = self.types[slot]
            out += [float(self.can_produce(slot, row)), by_type[t["pointer_self"]] / 10,
                    unplaced[t["pointer_self"]] / 5, t["cost"] / 5000, t.get("tech_level", 0) / 10,
                    t.get("power_bonus", 0) / 1000, t.get("power_drain", 0) / 1000,
                    *[float(t["type"] == k) for k in KINDS]]
        controlled = self.units(row)
        goal = enemy_start(row, starts)
        for slot in range(UNIT_SLOTS):
            if slot >= len(controlled):
                out += [0.0] * UNIT_FEATURES
                continue
            o = controlled[slot]
            if goal is None:
                approach = [0.0, 0.0, 0.0]
            else:
                dx, dy = goal[0] - o["cell"][0], goal[1] - o["cell"][1]
                approach = [min(1.0, math.hypot(dx, dy) / 182.0), dx / 182.0, dy / 182.0]
            out += [1.0, self.slot_by_ptr.get(o.get("type_ptr"), -1) / MAX_TYPES,
                    o["health"] / 1000, o["cell"][0] / 182, o["cell"][1] / 182,
                    float(is_mcv(o)), float(o["abstract"] == "ABSTRACT_TYPE_INFANTRY"),
                    float(o["abstract"] == "ABSTRACT_TYPE_UNIT"), *approach]
        visible = self.targets(row)
        for slot in range(TARGET_SLOTS):
            if slot >= len(visible):
                out += [0.0] * 6
            else:
                o = visible[slot]
                out += [1.0, o["health"] / 1000, o["cell"][0] / 182, o["cell"][1] / 182,
                        float(is_building(o)), float(o.get("abstract") == "ABSTRACT_TYPE_INFANTRY")]
        ready = pending(row)
        for slot in range(PENDING_SLOTS):
            if slot >= len(ready):
                out += [0.0] * 4
            else:
                o = ready[slot]
                out += [1.0, self.slot_by_ptr.get(o.get("type_ptr"), -1) / MAX_TYPES,
                        o.get("health", 0) / 1000, float(base_cell(row) is not None)]
        out += self.masks(row, starts, map_soa).astype(np.float32).tolist()
        return np.asarray(out, dtype=np.float32)
