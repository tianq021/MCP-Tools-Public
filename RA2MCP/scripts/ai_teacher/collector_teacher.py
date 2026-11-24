"""CollectorTeacher: a modular, 301-native data-collection teacher skeleton.

Purpose
-------
This is the *skeleton* for the teacher that will generate student-usable
demonstrations. It is deliberately constrained to the frozen action contract
``full_ground_v2p1`` (``ACTION_N=301``): every emitted action is decoded by
``dsh_rl.full_spec`` and, whenever a spec is available, the teacher refuses to
emit actions whose ``intent()`` is undefined. That guarantees the collected
dataset is 100% expressible by the current student contract (no spy / garrison /
remote-base / feint actions that the contract cannot represent).

It is NOT a native AI script and must never be named as one. It is an external
adapter: it reads only the local player's legal, visible observation through the
environment adapter and sends 301 actions back to the environment.

Modules (all 301-native)
------------------------
* ``DeployModule``   - deploy an undeployed MCV (action 1)
* ``BuildModule``    - produce buildings / place ready buildings (PRODUCE/PLACE)
* ``ArmyModule``     - produce combat units (PRODUCE)
* ``AttackModule``   - attack a visible target from the legal top-8 target slots
* ``MoveModule``     - move a single unit toward the public enemy spawn / map centre
* ``ScoutModule``    - 301 SCOUT (move to public spawn) for one fast unit
* ``FeintModule``    - SCAFFOLD ONLY (default OFF): lure move for feint tactics
* ``Scheduler``      - deterministic priority selection; never emits a masked action

The class exposes ``choose(host) -> (action, label, rationale)`` compatible with
the existing ``dsh_rl.scripted_policy_v2.HostAdapter`` interface, plus
``decide(host) -> dict`` with rich provenance for the data pipeline
(action / task / label / intent / mapping / phase / config hash).
"""
from __future__ import annotations

import hashlib
import json
import math

from dsh_rl import full_spec as S

POLICY_VERSION = "collector_teacher:0.1.0"
ACTION_N = S.ACTION_N
PRODUCE_START = S.PRODUCE_START
PLACE_START = S.PLACE_START
UNIT_START = S.UNIT_START
UNIT_ACTIONS = S.UNIT_ACTIONS
DIRECTIONS = S.DIRECTIONS
CMD_STOP = 0
CMD_MOVE_BASE = 1          # directions are 1..8
CMD_ATTACK_BASE = 9        # target slots are 9..16
CMD_SCOUT = 17
CMD_RETREAT = 18

LABELS = ("noop", "deploy", "produce", "place", "move", "attack", "stop", "scout")

TASK_LABEL = {
    "DEPLOY": "deploy", "PLACE": "place", "PRODUCE_BUILDING": "produce",
    "PRODUCE_UNIT": "produce", "ATTACK": "attack", "MOVE": "move",
    "FEINT_LURE": "move", "SCOUT": "scout", "STOP": "stop", "NOOP": "noop",
}

# Economy targets are intentionally generic vanilla-name matchers; the actual
# production slots come from the real catalog / legal mask, never hard-coded.
BUILD_TARGETS = (
    ("power", ("Power Plant", "Nuclear Reactor"), 2),
    ("refinery", ("Ore Refinery",), 2),
    ("barracks", ("Barracks",), 1),
    ("war_factory", ("War Factory",), 1),
    ("radar", ("Radar", "Airforce Command", "Air Force Command"), 1),
    ("battle_lab", ("Battle Lab",), 1),
    ("defense", ("Pillbox", "Sentry Gun", "Patriot", "Prism Tower",
                 "Flak Cannon", "Tesla Coil"), 2),
)
UNIT_PRIORITY = (
    "Grizzly Battle Tank", "GI", "Guardian GI", "IFV", "Prism Tank",
    "Mirage Tank", "Rhino Heavy Tank", "Conscript", "Tesla Trooper",
    "Apocalypse Tank", "Lasher Tank", "Flak Track",
)
DEFENSE_MIN_UNITS = 4
ARMY_CAP = 12
ASSAULT_MIN_UNITS = 4


def _hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:12]


def dir_bucket(dx: float, dy: float) -> int:
    """Map a cell delta to the 8-direction index of S.DIRECTIONS.

    S.DIRECTIONS = ((1,0),(1,1),(0,1),(-1,1),(-1,0),(-1,-1),(0,-1),(1,-1)) with
    y growing downward, which is exactly ``round(atan2(dy, dx) / 45deg) mod 8``.
    """
    if abs(dx) < 0.5 and abs(dy) < 0.5:
        return 0
    return int(round(math.degrees(math.atan2(dy, dx)) / 45.0)) % 8


class CollectorConfig:
    """Tunable, hashable configuration (part of the policy provenance)."""

    def __init__(self, army_cap=ARMY_CAP, assault_min_units=ASSAULT_MIN_UNITS,
                 defense_min_units=DEFENSE_MIN_UNITS, scout_enabled=True,
                 feint_enabled=False, scout_after_s=45.0):
        self.army_cap = int(army_cap)
        self.assault_min_units = int(assault_min_units)
        self.defense_min_units = int(defense_min_units)
        self.scout_enabled = bool(scout_enabled)
        self.feint_enabled = bool(feint_enabled)
        self.scout_after_s = float(scout_after_s)

    def to_dict(self) -> dict:
        return {"army_cap": self.army_cap, "assault_min_units": self.assault_min_units,
                "defense_min_units": self.defense_min_units,
                "scout_enabled": self.scout_enabled,
                "feint_enabled": self.feint_enabled,
                "scout_after_s": self.scout_after_s}

    def digest(self) -> str:
        return _hash(self.to_dict())


class CollectorView:
    """Sanitized read-only view: only local observation fields, no hidden info."""

    ALLOWED = ("own_objects", "visible_enemies", "factories", "houses",
               "money", "map", "t", "frame")

    def __init__(self, row):
        self.row = {k: row.get(k) for k in self.ALLOWED if k in row}
        self.reject_hidden = "hidden_enemy_count" not in self.row

    # -- basic accessors ---------------------------------------------------
    @property
    def t(self):
        return float(self.row.get("t") or 0.0)

    @property
    def money(self):
        return int(self.row.get("money") or 0)

    @property
    def own(self):
        return [o for o in (self.row.get("own_objects") or [])
                if o.get("on_map", True) and not o.get("in_limbo")]

    def buildings(self):
        return [o for o in self.own if o.get("abstract") == "ABSTRACT_TYPE_BUILDING"]

    def pending(self):
        return [o for o in (self.row.get("own_objects") or [])
                if o.get("abstract") == "ABSTRACT_TYPE_BUILDING"
                and (o.get("in_limbo") or not o.get("on_map"))]

    def mobile_units(self):
        return [o for o in self.own
                if o.get("abstract") in ("ABSTRACT_TYPE_UNIT", "ABSTRACT_TYPE_INFANTRY")]

    def combat_units(self):
        return [o for o in self.mobile_units()
                if not S.is_mcv(o)
                and not any(h in str(o.get("type_name") or "")
                            for h in ("Miner", "Harvester", "Construction Vehicle"))]

    def mcv(self):
        return next((o for o in self.own if S.is_mcv(o)), None)

    def enemies(self):
        return list(self.row.get("visible_enemies") or [])

    def base_cell(self):
        b = self.buildings()
        if b:
            cells = [o.get("cell") or [0, 0] for o in b]
            return (sum(c[0] for c in cells) / len(cells),
                    sum(c[1] for c in cells) / len(cells))
        u = self.mobile_units()
        if u:
            cells = [o.get("cell") or [0, 0] for o in u]
            return (sum(c[0] for c in cells) / len(cells),
                    sum(c[1] for c in cells) / len(cells))
        return (0.0, 0.0)

    def force_center(self):
        u = self.combat_units() or self.mobile_units()
        if not u:
            return self.base_cell()
        cells = [o.get("cell") or [0, 0] for o in u]
        return (sum(c[0] for c in cells) / len(cells),
                sum(c[1] for c in cells) / len(cells))

    def digest(self) -> str:
        return _hash({
            "money": self.money, "t": round(self.t, 2),
            "own": [(o.get("addr"), o.get("health"), tuple(o.get("cell") or []))
                    for o in self.own],
            "enemies": [(e.get("addr"), tuple(e.get("cell") or []))
                        for e in self.enemies()],
        })


class CollectorTeacher:
    """Deterministic 301-native teacher skeleton (one action per decision)."""

    def __init__(self, config=None, spec=None, seed=0):
        self.config = config or CollectorConfig()
        self.spec = spec
        self.seed = int(seed)
        self.decisions = 0
        self.reset_episode()

    # -- lifecycle ---------------------------------------------------------
    def reset_episode(self):
        self.decisions = 0
        self.phase = "OPENING"
        self.last_task = None
        self.task_counts = {}
        self.digests = []

    def policy_metadata(self) -> dict:
        doc = {"policy_version": POLICY_VERSION, "action_n": ACTION_N,
               "deterministic": True, "config": self.config.to_dict(),
               "config_hash": self.config.digest(),
               "label_set": list(LABELS)}
        doc["module_hash"] = _hash(doc)
        return doc

    # -- host/spec helpers -------------------------------------------------
    def _spec_of(self, host):
        spec = self.spec or getattr(host, "full", None)
        if spec is None:
            spec = getattr(getattr(host, "env", None), "full", None)
        return spec

    def _map_soa(self, host):
        for cand in (host, getattr(host, "env", None)):
            if cand is not None and getattr(cand, "map_soa", None) is not None:
                return getattr(cand, "map_soa")
        return None

    def _starts(self, host):
        return list(getattr(host, "starts", []) or [])

    def _row(self, host):
        return getattr(host, "current", {}) or {}

    def _legal(self, host) -> set:
        legal = getattr(host, "legal_actions", None)
        if callable(legal):
            out = legal()
            if out is not None:
                return {int(a) for a in out}
        return set(range(ACTION_N))

    def _type_names(self, host):
        fn = getattr(host, "production_types", None)
        if callable(fn):
            return list(fn() or [])
        spec = self._spec_of(host)
        return [] if spec is None else [str(t.get("name")) for t in spec.types]

    def _available(self, host):
        fn = getattr(host, "available_production", None)
        if callable(fn):
            return [bool(x) for x in (fn() or [])]
        spec = self._spec_of(host)
        if spec is None:
            return [False] * 96
        out = [bool(spec.can_produce(i, self._row(host))) for i in range(len(spec.types))]
        return out + [False] * (96 - len(out))

    def _slot_for(self, host, names) -> int | None:
        """First production slot whose type name matches one of ``names``."""
        if isinstance(names, str):
            names = (names,)
        avail = self._available(host)
        for slot, name in enumerate(self._type_names(host)):
            if slot >= len(avail) or not avail[slot]:
                continue
            if any(n.casefold() in str(name).casefold() for n in names):
                return slot
        return None

    # -- intent guarantee --------------------------------------------------
    def _intent(self, host, action: int):
        spec = self._spec_of(host)
        if spec is None or action == 0:
            return None
        return spec.intent(action, self._row(host), self._starts(host),
                            self._map_soa(host))

    def _intent_ok(self, host, action: int) -> bool:
        """Action 0 is legal by contract; every other action must decode."""
        if action == 0:
            return True
        spec = self._spec_of(host)
        if spec is None:
            return True  # legality comes from the real mask; mapping checked in decide()
        return self._intent(host, action) is not None

    # -- module candidates -------------------------------------------------
    def _deploy_candidate(self, host, view):
        if view.mcv() is None or view.mcv().get("deployed"):
            return None
        return {"task": "DEPLOY", "action": 1, "priority": 100,
                "meta": {"mcv": view.mcv().get("addr")}}

    def _build_candidates(self, host, view):
        spec = self._spec_of(host)
        if spec is None:
            return []
        out = []
        # In the assault phase economy production is de-prioritised so the
        # teacher actually spends decisions on combat instead of never leaving
        # the build phase.
        base_priority = 90 if self.phase in ("DEPLOY", "OPENING") else 40
        names = [str(o.get("type_name") or "") for o in view.buildings()]
        for key, matchers, target in BUILD_TARGETS:
            count = sum(1 for n in names if any(m.casefold() in n.casefold()
                                                for m in matchers))
            if count >= target:
                continue
            slot = self._slot_for(host, matchers)
            if slot is None:
                continue
            action = PRODUCE_START + slot
            if not self._intent_ok(host, action):
                continue
            out.append({"task": "PRODUCE_BUILDING", "action": action,
                        "priority": base_priority - len(out),
                        "meta": {"building": key, "have": count, "want": target,
                                 "type": self._type_names(host)[slot]}})
            break
        return out

    def _army_candidate(self, host, view):
        units = view.combat_units()
        if len(units) >= self.config.army_cap:
            return None
        prior = 80 if self.phase in ("OPENING", "BUILD_ARMY") else 50
        for name in UNIT_PRIORITY:
            slot = self._slot_for(host, name)
            if slot is None:
                continue
            action = PRODUCE_START + slot
            if not self._intent_ok(host, action):
                continue
            return {"task": "PRODUCE_UNIT", "action": action, "priority": prior,
                    "meta": {"unit": self._type_names(host)[slot],
                             "combat_units": len(units),
                             "army_cap": self.config.army_cap}}
        return None

    def _place_candidate(self, host, view):
        spec = self._spec_of(host)
        if spec is None:
            return None
        ready = S.pending(self._row(host))
        if not ready:
            return None
        base = S.base_cell(self._row(host))
        if base is None:
            return None
        goal = self._goal(view)
        direction = dir_bucket(goal[0] - base[0], goal[1] - base[1])
        for index in range(min(len(ready), S.PENDING_SLOTS)):
            action = PLACE_START + index * len(DIRECTIONS) + direction
            if not self._intent_ok(host, action):
                continue
            return {"task": "PLACE", "action": action, "priority": 95,
                    "meta": {"pending": index, "direction": direction,
                             "building": ready[index].get("type_name")}}
        return None

    def _selection_for_combat_unit(self, host, view):
        """1-based selection index (S.select_units) of the first combat unit."""
        spec = self._spec_of(host)
        if spec is None:
            return None
        combat_addrs = {int(u.get("addr") or 0) for u in view.combat_units()}
        for index, obj in enumerate(spec.units(self._row(host))):
            if int(obj.get("addr") or 0) in combat_addrs:
                return index + 1
        return None

    def _attack_candidate(self, host, view):
        spec = self._spec_of(host)
        if spec is None or not view.enemies():
            return None
        if len(view.combat_units()) < 1:
            return None
        selection = self._selection_for_combat_unit(host, view)
        if selection is None:
            return None
        targets = spec.targets(self._row(host))
        if not targets:
            return None
        slot = 0
        action = UNIT_START + selection * UNIT_ACTIONS + CMD_ATTACK_BASE + slot
        if not self._intent_ok(host, action):
            return None
        target = targets[slot]
        priority = 85 if self.phase == "ASSAULT" else 75
        return {"task": "ATTACK", "action": action, "priority": priority,
                "meta": {"selection": selection, "target_slot": slot,
                         "target_addr": target.get("addr"),
                         "target_type": target.get("type_name"),
                         "combat_units": len(view.combat_units())}}

    def _move_candidate(self, host, view):
        spec = self._spec_of(host)
        if spec is None:
            return None
        selection = self._selection_for_combat_unit(host, view)
        if selection is None:
            return None
        goal = self._goal(view)
        center = view.force_center()
        direction = dir_bucket(goal[0] - center[0], goal[1] - center[1])
        action = UNIT_START + selection * UNIT_ACTIONS + CMD_MOVE_BASE + direction
        if not self._intent_ok(host, action):
            return None
        return {"task": "MOVE", "action": action, "priority": 60,
                "meta": {"selection": selection, "direction": direction,
                         "goal": list(goal)}}

    def _scout_candidate(self, host, view):
        if not self.config.scout_enabled or view.t < self.config.scout_after_s:
            return None
        if view.enemies() or not self._starts(host):
            return None
        spec = self._spec_of(host)
        if spec is None:
            return None
        units = view.mobile_units()
        if not units:
            return None
        fast = next((u for u in units
                     if "Engineer" not in str(u.get("type_name") or "")), None)
        if fast is None:
            return None
        ordered = spec.units(self._row(host))
        try:
            selection = ordered.index(fast) + 1
        except ValueError:
            return None
        action = UNIT_START + selection * UNIT_ACTIONS + CMD_SCOUT
        if not self._intent_ok(host, action):
            return None
        return {"task": "SCOUT", "action": action, "priority": 50,
                "meta": {"selection": selection}}

    def _feint_candidate(self, host, view):
        """SCAFFOLD: lure move. Disabled by default; 301-native MOVE only."""
        if not self.config.feint_enabled or not view.enemies():
            return None
        if len(view.combat_units()) < self.config.defense_min_units:
            return None
        selection = self._selection_for_combat_unit(host, view)
        if selection is None:
            return None
        base = view.base_cell()
        enemy = view.enemies()[0].get("cell") or [base[0] + 1, base[1]]
        dx, dy = enemy[0] - base[0], enemy[1] - base[1]
        # move perpendicular to the threat axis as a lure
        direction = dir_bucket(-dy, dx)
        action = UNIT_START + selection * UNIT_ACTIONS + CMD_MOVE_BASE + direction
        if not self._intent_ok(host, action):
            return None
        return {"task": "FEINT_LURE", "action": action, "priority": 65,
                "meta": {"selection": selection, "direction": direction,
                         "enemy_cell": enemy, "note": "scaffold; no tactic claim"}}

    def _stop_candidate(self, host, view):
        if not view.mobile_units():
            return None
        action = UNIT_START + CMD_STOP
        if not self._intent_ok(host, action):
            return None
        return {"task": "STOP", "action": action, "priority": 20, "meta": {}}

    # -- scheduler ---------------------------------------------------------
    def _goal(self, view):
        """Public enemy spawn farthest from our base (no hidden information)."""
        base = view.base_cell()
        starts = getattr(self, "_goal_starts", [])
        if starts:
            return max(starts, key=lambda s: math.dist(base, s))
        map_info = view.row.get("map") or {}
        w = float(map_info.get("width") or 182)
        h = float(map_info.get("height") or 182)
        return (w / 2.0, h / 2.0)

    def _phase(self, view):
        if view.mcv() is not None and not view.mcv().get("deployed"):
            return "DEPLOY"
        if len(view.buildings()) < 3:
            return "OPENING"
        if len(view.combat_units()) < self.config.assault_min_units:
            return "BUILD_ARMY"
        return "ASSAULT"

    def decide(self, host) -> dict:
        self.decisions += 1
        view = CollectorView(self._row(host))
        self._goal_starts = self._starts(host)
        self.phase = self._phase(view)
        legal = self._legal(host)
        candidates = []
        for fn in (self._deploy_candidate, self._place_candidate,
                   self._build_candidates, self._army_candidate,
                   self._attack_candidate, self._move_candidate,
                   self._scout_candidate, self._feint_candidate,
                   self._stop_candidate):
            result = fn(host, view)
            if result is None:
                continue
            if isinstance(result, list):
                candidates.extend(result)
            else:
                candidates.append(result)
        candidates = [c for c in candidates
                      if int(c["action"]) in legal and self._intent_ok(host, int(c["action"]))]
        candidates.sort(key=lambda c: (-int(c["priority"]), int(c["action"])))
        if candidates:
            chosen = candidates[0]
        else:
            chosen = {"task": "NOOP", "action": 0, "priority": 0, "meta": {}}
            fallback = sorted(legal)
            if 0 not in legal:
                # Mask says NOOP illegal; pick the smallest legal action that
                # decodes under the contract. If none decodes, fail closed
                # instead of emitting an action the dataset cannot label.
                for action in fallback:
                    if self._intent_ok(host, action):
                        chosen = {"task": "NOOP", "action": int(action),
                                  "priority": 0, "meta": {"fallback_from_mask": True}}
                        break
                else:
                    raise RuntimeError(
                        f"no legal 301 action decodes (spec action_n={ACTION_N})")
        action = int(chosen["action"])
        task = chosen["task"]
        label = TASK_LABEL.get(task, "noop")
        intent = self._intent(host, action) if action != 0 else None
        spec = self._spec_of(host)
        if spec is None:
            mapping = "NO_SPEC_AVAILABLE"
        elif action == 0:
            mapping = "NOOP"
        elif intent is not None:
            mapping = "MAPPED"
        else:
            mapping = "UNMAPPED"
        rationale = {
            "policy_version": POLICY_VERSION,
            "config_hash": self.config.digest(),
            "task": task,
            "label": label,
            "action": action,
            "action_in_legal_mask": action in legal,
            "mapping": mapping,
            "intent": intent,
            "phase": self.phase,
            "decision_index": self.decisions,
            "t": round(view.t, 3),
            "money": view.money,
            "own_buildings": len(view.buildings()),
            "combat_units": len(view.combat_units()),
            "visible_enemies": len(view.enemies()),
            "pending_buildings": len(view.pending()),
            "digest": view.digest(),
            "hidden_info_used": False,
            "meta": chosen.get("meta") or {},
        }
        self.last_task = task
        self.task_counts[task] = self.task_counts.get(task, 0) + 1
        self.digests.append(rationale["digest"])
        return rationale

    def choose(self, host):
        """Compatibility wrapper: (action, label, rationale)."""
        rationale = self.decide(host)
        return rationale["action"], rationale["label"], rationale
