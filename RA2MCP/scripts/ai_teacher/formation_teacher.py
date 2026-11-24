"""Raw formation teacher, reusing the existing native intent interface.

It may command arbitrary explicit friendly combat sets, not frozen 301 slots.
Exact-301 annotation is computed separately; raw multi-command decisions are
never automatically admitted as one 301 policy action.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import pathlib

from ai_teacher.strategic_teacher import StrategicTeacher, StrategyConfig, digest
from dsh_rl import full_spec as S
from dsh_episode.hostility import is_opponent

VERSION = "formation_teacher:1.0.0"
NON_COMBAT = ("Miner", "Harvester", "Construction Vehicle", "Engineer", "Spy")


def alive(o):
    return bool(o.get("on_map") and not o.get("in_limbo") and o.get("health", 0) > 0)


def center(items):
    return [sum(o["cell"][i] for o in items) / len(items) for i in (0, 1)]


def semantic(intent):
    """Compare executable meanings, ignoring descriptive/provenance fields."""
    action = intent.get("action")
    return {"action": action, "actors": sorted(intent.get("object_addresses") or []),
            "target": intent.get("target_address") if action == "ATTACK" else None,
            "coordinates": list(intent.get("coordinates") or []) if action in ("MOVE", "ATTACK") else None,
            "production_type_ptr": intent.get("production_type_ptr") if action in ("PRODUCE", "PLACE") else None,
            "production_action": intent.get("production_action", "BEGIN") if action == "PRODUCE" else None,
            "building_address": intent.get("building_address") if action == "PLACE" else None,
            "anchor": list(intent.get("anchor") or []) if action == "PLACE" else None}


def exact_301(intent, row, spec, starts, map_soa, mask=None):
    mask = spec.masks(row, starts, map_soa) if mask is None else mask
    desired = semantic(intent)
    for action in range(1, S.ACTION_N):
        if mask[action]:
            candidate = spec.intent(action, row, starts, map_soa)
            if candidate and semantic(candidate) == desired:
                return {"classification": "EXACT", "action_idx": action}
    return {"classification": "UNSUPPORTED_FOR_301", "action_idx": None}


@dataclasses.dataclass(frozen=True)
class FormationConfig:
    launch_after_s: float = 55.0
    min_army: int = 7
    command_refresh_s: float = 10.0
    min_command_gap_s: float = 2.0
    defense_radius: float = 23.0
    engage_radius: float = 26.0
    retreat_hp_ratio: float = 0.25

    def __post_init__(self):
        values = dataclasses.asdict(self)
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values.values()):
            raise ValueError("non-finite configuration")
        if not (15 <= self.launch_after_s <= 180 and isinstance(self.min_army, int) and 2 <= self.min_army <= 32
                and 3 <= self.command_refresh_s <= 30 and 1 <= self.min_command_gap_s <= 5
                and 10 <= self.defense_radius <= 40 and 12 <= self.engage_radius <= 40
                and .1 <= self.retreat_hp_ratio <= .5):
            raise ValueError("formation parameter out of bounds")


class FormationTeacher:
    def __init__(self, config=None):
        self.config = config or FormationConfig()
        self.economy = StrategicTeacher(StrategyConfig())
        self.issued = {}
        self.launched = False
        self.tracks = {}
        self.serial = 0
        self.enemy_memory = []

    def metadata(self):
        return {"teacher_id": VERSION, "source": "formation_teacher", "control_mode": "raw_explicit_groups",
                "source_sha256": hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),
                "economy_source_sha256": self.economy.metadata()["source_sha256"],
                "config": dataclasses.asdict(self.config), "config_sha256": digest(dataclasses.asdict(self.config)),
                "action_contract_301_unchanged": True, "student_model_loaded": False, "strength": "UNKNOWN"}

    def _track(self, own, frame):
        current = {}
        for o in own:
            address = int(o["addr"])
            signature = (o.get("type_name"), o.get("owner"))
            prior = self.tracks.get(address)
            if prior is None or prior["signature"] != signature or not prior["present"]:
                self.serial += 1
                prior = {"track_id": f"own-{self.serial}", "signature": signature,
                         "first_seen_frame": frame, "identity_confidence": "OBSERVATION_HEURISTIC"}
            current[address] = {**prior, "present": True, "last_seen_frame": frame}
        for addr, old in self.tracks.items():
            if addr not in current:
                current[addr] = {**old, "present": False}
        self.tracks = current

    def plan(self, observation, spec, starts, map_soa):
        row = StrategicTeacher.visible(observation)
        t = float(row.get("t") or 0)
        own = [o for o in row.get("own_objects") or [] if alive(o)]
        self._track(own, row.get("frame"))
        army = [o for o in own if o.get("abstract") in ("ABSTRACT_TYPE_UNIT", "ABSTRACT_TYPE_INFANTRY")
                and not any(n in o.get("type_name", "") for n in NON_COMBAT)]
        base = S.base_cell(row)
        macro = self.economy.choose(row, spec, starts, map_soa)
        mask = macro["action_mask"]
        commands = []
        if macro["queue"] in ("building", "vehicle", "infantry") and macro["intent"]:
            commands.append({"role": "economy", "skill_id": macro["skill_id"], "intent": macro["intent"],
                             "mapping_301": {"classification": "EXACT", "action_idx": macro["action"]}})
        if base is None or not army:
            return self._result(row, army, {}, commands, spec, mask)

        enemies = [e for e in row.get("visible_enemies") or [] if alive(e)
                   and e.get("is_visible", True) and is_opponent(e)
                   and e.get("owner_ptr") != own[0].get("owner_ptr")]
        if enemies:
            self.enemy_memory = [(list(e["cell"]), t) for e in enemies]
        self.enemy_memory = [(p, when) for p, when in self.enemy_memory if t - when <= 45]
        public_goal = S.enemy_start(row, starts)
        goal = self.enemy_memory[-1][0] if self.enemy_memory else public_goal
        if not self.launched and len(army) >= self.config.min_army and t >= self.config.launch_after_s:
            self.launched = True
        target_goal = goal or base
        dx, dy = target_goal[0] - base[0], target_goal[1] - base[1]
        norm = max(1, math.hypot(dx, dy))
        vector = (dx / norm, dy / norm)
        threats = [e for e in enemies if math.dist(e["cell"], base) < self.config.defense_radius]
        squads = {"front": [], "fire": [], "support": [], "scout": [], "retreat": []}
        for o in army:
            maximum = float(spec.all_types.get(o.get("type_ptr"), {}).get("strength") or o["health"])
            injured = o["health"] / max(1, maximum) < self.config.retreat_hp_ratio
            near = any(math.dist(e["cell"], o["cell"]) < 12 for e in enemies)
            if injured and near:
                squads["retreat"].append(o)
            elif "Prism Tank" in o.get("type_name", ""):
                squads["fire"].append(o)
            elif o.get("abstract") == "ABSTRACT_TYPE_INFANTRY" or "IFV" in o.get("type_name", ""):
                squads["support"].append(o)
            else:
                squads["front"].append(o)
        if not self.launched and t > 12 and public_goal:
            scout = next((u for u in squads["support"] if u["type_name"] == "GI"), None)
            if scout:
                squads["support"].remove(scout)
                squads["scout"].append(scout)

        w, h = getattr(map_soa, "map_width", 182), getattr(map_soa, "map_height", 182)
        def move(ids, cell):
            cell = [max(0, min(int(w) - 1, cell[0])), max(0, min(int(h) - 1, cell[1]))]
            return {"action": "MOVE", "object_addresses": ids,
                    "coordinates": [int(c * 256 + 128) for c in cell]}

        main = squads["front"] or squads["fire"] or army
        front_center = center(main)
        for role, members in squads.items():
            if not members:
                continue
            ids = sorted(o["addr"] for o in members)
            position = center(members)
            skill = "formation.advance" if self.launched else "formation.rally"
            intent = None
            if role == "retreat":
                intent = move(ids, base); skill = "formation.retreat"
            elif role == "scout":
                intent = move(ids, public_goal); skill = "formation.scout"
            else:
                candidates = threats or [e for e in enemies if math.dist(e["cell"], front_center) < self.config.engage_radius]
                if candidates:
                    # Visible nearby mobile threats take priority. Fire support
                    # prefers buildings only after immediate threats are absent.
                    target = min(candidates, key=lambda e: (
                        0 if e in threats else 1,
                        0 if role == "fire" and e.get("abstract") == "ABSTRACT_TYPE_BUILDING" else
                        0 if role != "fire" and e.get("abstract") != "ABSTRACT_TYPE_BUILDING" else 1,
                        math.dist(e["cell"], position), e["addr"]))
                    if role == "fire" and math.dist(target["cell"], position) < 6:
                        intent = move(ids, [position[i] - vector[i] * 8 for i in (0, 1)])
                        skill = "formation.standoff"
                    else:
                        intent = {"action": "ATTACK", "object_addresses": ids,
                                  "target_address": target["addr"], "target_type": target["type_name"],
                                  "coordinates": [int(c * 256 + 128) for c in target["cell"]]}
                        skill = "formation.defend" if target in threats else "formation.engage"
                else:
                    offset = 7 if role == "fire" else 4 if role == "support" else 0
                    destination = ([target_goal[i] - vector[i] * offset for i in (0, 1)] if self.launched else
                                   [base[i] + vector[i] * (10 - offset) for i in (0, 1)])
                    # New members far from the role objective must receive an
                    # order even if the existing squad centroid is near it.
                    if any(math.dist(o["cell"], destination) > 4 for o in members):
                        intent = move(ids, destination)
            if intent:
                signature = digest({"intent": semantic(intent), "tracks": [self.tracks[o["addr"]]["track_id"] for o in members]})
                old = self.issued.get(role)
                age = t - old["t"] if old else float("inf")
                if age < self.config.min_command_gap_s or old and age < self.config.command_refresh_s and old["signature"] == signature:
                    continue
                self.issued[role] = {"t": t, "signature": signature}
                commands.append({"role": role, "skill_id": skill, "intent": intent,
                    "mapping_301": exact_301(intent, row, spec, starts, map_soa, mask)})
        return self._result(row, army, squads, commands, spec, mask)

    def _result(self, row, army, squads, commands, spec, mask):
        assignments = {role: [{"addr": o["addr"], "type_name": o["type_name"],
                               "track_id": self.tracks[o["addr"]]["track_id"]} for o in members]
                       for role, members in squads.items()}
        slot_ids = {u["addr"] for u in spec.units(row)}
        # A raw decision with multiple commands cannot be relabeled as a single
        # 301 action even if one of its individual commands has an exact encoding.
        one_exact = len(commands) == 1 and commands[0]["mapping_301"]["classification"] == "EXACT"
        return {"schema": "ra2.formation-plan/1", "teacher_id": VERSION, "observation_frame": row.get("frame"),
                "phase": "ASSAULT" if self.launched else "BUILDUP", "assignments": assignments,
                "commands": commands, "action_mask_301": mask,
                "decision_301_eligible": one_exact,
                "combat_units": len(army), "assigned_units": sum(len(x) for x in assignments.values()),
                "outside_legacy_slots": [u["addr"] for u in army if u["addr"] not in slot_ids],
                "all_combat_addresses": sorted(u["addr"] for u in army),
                "hidden_info_used": False, "arbitrary_generated_code_executed": False}
