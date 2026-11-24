"""LLM-authored conditional orders for the DSH takeover driver (offline-testable).

The DSH LLM writes rules; this module only *evaluates* conditions over the same
legal-visibility snapshot the LLM sees and resolves selectors into explicit
command dicts. It never invents a decision: every rule and every command comes
from an LLM decision file, and the driver still validates each resolved command
against the latest legal state before sending it through the existing bridge.

Rule schema (inside a decision JSON, key "rules"; the list replaces the active set;
key "rules_clear": true clears it):

  {"id": "defend-close",
   "when": {"enemy_visible_within": {"radius_cells": 9, "from": "own_base"}},
   "then": [{"op": "ATTACK", "units": "all_combat",
             "target": "nearest_visible_enemy"}],
   "cooldown_frames": 90, "max_fires": 20}

Conditions (one object; combine with "all": [...], "any": [...], "not": {...}):
  - {"always": true}
  - {"enemy_visible_within": {"radius_cells": N, "from": "own_base" | [x,y]}}
  - {"enemy_visible_count_at_least": N}
  - {"own_combat_count_below": N}
  - {"money_above": N}
  - {"money_below": N}
  - {"own_limbo_building": "Allied Barracks"}
  - {"own_hp_below_pct": {"selector": "all_combat" | "type:<name>" | [addr,...], "pct": 30}}

Command selectors:
  units: "all_combat" | "type:<TypeName>" | [addr, ...]
  target: "nearest_visible_enemy" | <enemy addr> | {"cell": [x, y]}
  PLACE building: "own_limbo:<TypeName>" | <own limbo addr>

The module has no pyra2yr / game dependency; it is unit tested offline.
"""
from __future__ import annotations

import math

NON_COMBAT_HINTS = ("Construction Vehicle", "Miner", "Harvester", "Chrono Miner")


# ------------------------------------------------------------------ helpers ----
def _own_objects(legal):
    return legal.get("own_objects") or []


def _enemies(legal):
    return legal.get("visible_enemies") or []


def _is_building(obj):
    return obj.get("abstract") == "ABSTRACT_TYPE_BUILDING"


def _is_mobile_combat(obj):
    if _is_building(obj):
        return False
    if obj.get("in_limbo") or not obj.get("on_map"):
        return False
    if int(obj.get("health", 0)) <= 0:
        return False
    name = obj.get("type_name", "")
    if any(hint in name for hint in NON_COMBAT_HINTS):
        return False
    return True


def own_combat_units(legal):
    return [o for o in _own_objects(legal) if _is_mobile_combat(o)]


def own_limbo_buildings(legal):
    return [o for o in _own_objects(legal) if _is_building(o) and o.get("in_limbo")]


def hero_house(legal):
    return next((h for h in legal.get("houses") or []
                 if h.get("current_player") and h.get("is_human")), None)


def money(legal):
    hero = hero_house(legal)
    return int(hero.get("money", 0)) if hero else 0


def own_base_center(legal):
    buildings = [o for o in _own_objects(legal)
                 if _is_building(o) and o.get("on_map") and not o.get("in_limbo")]
    if buildings:
        return [sum(o["cell"][0] for o in buildings) / len(buildings),
                sum(o["cell"][1] for o in buildings) / len(buildings)]
    units = [o for o in _own_objects(legal) if o.get("on_map") and not o.get("in_limbo")]
    if units:
        return [sum(o["cell"][0] for o in units) / len(units),
                sum(o["cell"][1] for o in units) / len(units)]
    return [0.0, 0.0]


def _center(point):
    if point == "own_base":
        return None  # resolved by caller
    return [float(point[0]), float(point[1])]


# --------------------------------------------------------------- selectors ----
def select_units(selector, legal):
    """Resolve a units selector into a list of own mobile-combat addresses."""
    combat = own_combat_units(legal)
    if selector in (None, "all_combat"):
        return [o["addr"] for o in combat]
    if isinstance(selector, str) and selector.startswith("type:"):
        name = selector.split(":", 1)[1].strip().lower()
        return [o["addr"] for o in combat
                if name in o.get("type_name", "").lower()]
    if isinstance(selector, (list, tuple)):
        valid = {o["addr"] for o in combat}
        return [int(a) for a in selector if int(a) in valid]
    return []


def _nearest(items, point):
    if not items:
        return None
    return min(items, key=lambda o: math.dist(o["cell"], point))


def resolve_target(target, legal, units):
    """Return (target_addr or None, cell or None) for an ATTACK command."""
    if isinstance(target, (list, tuple)) and len(target) == 2 and not isinstance(target[0], dict):
        return None, [int(target[0]), int(target[1])]
    if isinstance(target, dict) and "cell" in target:
        cell = target["cell"]
        return None, [int(cell[0]), int(cell[1])]
    enemies = _enemies(legal)
    if target in ("nearest_visible_enemy", "nearest_enemy"):
        if not enemies:
            return None, None
        ref = None
        if units:
            ref = [sum(u["cell"][0] for u in units) / len(units),
                   sum(u["cell"][1] for u in units) / len(units)]
        enemy = _nearest(enemies, ref or own_base_center(legal))
        return int(enemy["addr"]), list(enemy["cell"])
    try:
        addr = int(target)
    except (TypeError, ValueError):
        return None, None
    enemy = next((e for e in enemies if int(e["addr"]) == addr), None)
    if enemy is None:
        return None, None
    return addr, list(enemy["cell"])


# -------------------------------------------------------------- conditions ----
def _condition_center(cond, legal):
    if isinstance(cond, str):
        return own_base_center(legal) if cond == "own_base" else _center(cond)
    return own_base_center(legal) if cond is None else _center(cond)


def evaluate_condition(cond, legal):
    """Evaluate one condition dict over a legal-visibility snapshot."""
    if not isinstance(cond, dict):
        return False
    if "all" in cond:
        return all(evaluate_condition(c, legal) for c in cond["all"])
    if "any" in cond:
        return any(evaluate_condition(c, legal) for c in cond["any"])
    if "not" in cond:
        return not evaluate_condition(cond["not"], legal)
    if cond.get("always"):
        return True
    if "enemy_visible_within" in cond:
        spec = cond["enemy_visible_within"] or {}
        radius = float(spec.get("radius_cells", 10))
        center = _condition_center(spec.get("from"), legal)
        return any(math.dist(e["cell"], center) <= radius for e in _enemies(legal))
    if "enemy_visible_count_at_least" in cond:
        return len(_enemies(legal)) >= int(cond["enemy_visible_count_at_least"])
    if "own_combat_count_below" in cond:
        return len(own_combat_units(legal)) < int(cond["own_combat_count_below"])
    if "money_above" in cond:
        return money(legal) > int(cond["money_above"])
    if "money_below" in cond:
        return money(legal) < int(cond["money_below"])
    if "own_limbo_building" in cond:
        name = str(cond["own_limbo_building"]).lower()
        return any(name in b.get("type_name", "").lower() for b in own_limbo_buildings(legal))
    if "own_completed_building" in cond:
        # stronger than own_limbo_building: the engine factory must report the
        # building as completed, otherwise PlaceBuilding is rejected with
        # "completed object ... not found from any factory".
        name = str(cond["own_completed_building"]).lower()
        done = {int(f.get("object_ptr") or 0) for f in legal.get("factories") or []
                if f.get("completed")}
        return any(name in b.get("type_name", "").lower() and int(b["addr"]) in done
                   for b in own_limbo_buildings(legal))
    if "own_hp_below_pct" in cond:
        spec = cond["own_hp_below_pct"] or {}
        pct = float(spec.get("pct", 30))
        addrs = select_units(spec.get("selector", "all_combat"), legal)
        own = {o["addr"]: o for o in own_combat_units(legal)}
        for addr in addrs:
            rec = own.get(addr)
            if rec is None:
                continue
            max_hp = int(rec.get("max_hp") or 0)
            if max_hp <= 0:
                # fall back to a per-type typical maximum when the bridge does
                # not expose max HP: Grizzly/Prism class units are 300/150 etc.
                continue
            if 100.0 * int(rec["health"]) / max_hp < pct:
                return True
        return False
    return False


# --------------------------------------------------------------- compiling ----
def compile_rule_commands(rule, legal):
    """Resolve one rule's `then` commands against the current legal state.

    Returns (commands, errors): commands are raw driver command dicts with all
    selectors resolved to explicit addresses / cells.
    """
    commands, errors = [], []
    for raw in rule.get("then") or []:
        op = str(raw.get("op", "")).upper()
        if op in ("DEPLOY", "MOVE", "ATTACK", "STOP"):
            units = select_units(raw.get("units"), legal)
            if not units:
                errors.append(f"{op}: selector resolved to no legal units")
                continue
            cmd = {"op": op, "units": units}
            if op in ("MOVE", "ATTACK"):
                cell = raw.get("cell")
                if op == "ATTACK":
                    addr, tcell = resolve_target(raw.get("target", "nearest_visible_enemy"),
                                                 legal, [o for o in own_combat_units(legal)
                                                         if o["addr"] in units])
                    if addr is None and tcell is None:
                        errors.append("ATTACK: no legal target resolved")
                        continue
                    if addr is not None:
                        cmd["target"] = addr
                    if tcell is not None:
                        cmd["cell"] = tcell
                if op == "MOVE":
                    if not (isinstance(cell, (list, tuple)) and len(cell) == 2):
                        errors.append("MOVE: cell missing")
                        continue
                    cmd["cell"] = [int(cell[0]), int(cell[1])]
            commands.append(cmd)
        elif op == "PRODUCE":
            cmd = {"op": "PRODUCE", "type": str(raw.get("type", "")),
                   "count": int(raw.get("count", 1))}
            if not cmd["type"]:
                errors.append("PRODUCE: type missing")
                continue
            commands.append(cmd)
        elif op == "PLACE":
            name = str(raw.get("type", ""))
            building = raw.get("building")
            limbo = own_limbo_buildings(legal)
            if isinstance(building, str) and building.startswith("own_limbo:"):
                want = building.split(":", 1)[1].lower()
                cand = [b for b in limbo if want in b.get("type_name", "").lower()]
                if not cand:
                    errors.append(f"PLACE: no own limbo building matching {want!r}")
                    continue
                building = cand[0]["addr"]
                name = name or cand[0]["type_name"]
            cell = raw.get("cell")
            if not (isinstance(cell, (list, tuple)) and len(cell) == 2):
                errors.append("PLACE: cell missing")
                continue
            cmd = {"op": "PLACE", "building": int(building),
                   "cell": [int(cell[0]), int(cell[1])]}
            if name:
                cmd["type"] = name
            commands.append(cmd)
        elif op == "NOOP":
            commands.append({"op": "NOOP"})
        else:
            errors.append(f"unsupported op {op!r}")
    return commands, errors


# ------------------------------------------------------------------ RuleSet ----
class RuleSet:
    """Active LLM-authored rules with per-rule cooldown / max_fires bookkeeping."""

    def __init__(self, rules=None):
        self.rules = [r for r in (rules or []) if isinstance(r, dict) and r.get("id")]
        self.state = {r["id"]: {"last_fire_frame": None, "fires": 0}
                      for r in self.rules}

    def clear(self):
        self.rules = []
        self.state = {}

    def describe(self):
        return [{"id": r["id"], "fires": self.state[r["id"]]["fires"],
                 "max_fires": r.get("max_fires"),
                 "cooldown_frames": r.get("cooldown_frames")}
                for r in self.rules]

    def due(self, legal, frame, max_fires=3):
        """Return [(rule, commands, errors)] for rules that fire on this evaluation."""
        out = []
        for rule in self.rules:
            if len(out) >= max_fires:
                break
            state = self.state[rule["id"]]
            max_fires_allowed = rule.get("max_fires")
            if max_fires_allowed is not None and state["fires"] >= int(max_fires_allowed):
                continue
            cooldown = int(rule.get("cooldown_frames", 0) or 0)
            last = state["last_fire_frame"]
            if last is not None and frame - int(last) < cooldown:
                continue
            if not evaluate_condition(rule.get("when") or {}, legal):
                continue
            commands, errors = compile_rule_commands(rule, legal)
            if not commands:
                continue
            state["last_fire_frame"] = int(frame)
            state["fires"] += 1
            out.append((rule, commands, errors))
        return out
