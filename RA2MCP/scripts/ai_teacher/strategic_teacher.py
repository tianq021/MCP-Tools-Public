"""A 301-native strategic teacher candidate; strength requires live evaluation.

Reuses the project's real FullControlSpec, never emits raw squad RPCs, and never
uses selection zero (which includes miners/MCVs). Economic service is guaranteed
before discretionary moves. Every proposal is decoded against the current slots.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import pathlib
from collections import Counter

from dsh_rl import full_spec as S
from ai_teacher.collector_teacher import dir_bucket

VERSION = "strategic_teacher:1.0.0"


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def identity(o):
    return (int(o.get("addr", 0)), o.get("type_name"), o.get("owner"))


@dataclasses.dataclass(frozen=True)
class StrategyConfig:
    attack_after_seconds: float = 55.0
    attack_min_units: int = 6
    move_cooldown_seconds: float = 7.0
    attack_cooldown_seconds: float = 10.0
    infantry_cap: int = 8
    army_cap: int = 32
    desired_miners: int = 2
    max_economy_gap_ticks: int = 4
    defense_radius: float = 23.0

    def __post_init__(self):
        for f in dataclasses.fields(self):
            value = getattr(self, f.name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"invalid numeric parameter {f.name}")
        if not (15 <= self.attack_after_seconds <= 180 and 2 <= self.attack_min_units <= 16
                and 2 <= self.move_cooldown_seconds <= 20 and 3 <= self.attack_cooldown_seconds <= 25
                and 0 <= self.infantry_cap <= 20 and 8 <= self.army_cap <= 60
                and 1 <= self.desired_miners <= 4 and 1 <= self.max_economy_gap_ticks <= 8
                and 10 <= self.defense_radius <= 40):
            raise ValueError("strategy parameter outside allowed range")
        for field in ("attack_min_units", "infantry_cap", "army_cap", "desired_miners", "max_economy_gap_ticks"):
            if not isinstance(getattr(self, field), int):
                raise ValueError(f"{field} must be integer")

    def as_dict(self):
        return dataclasses.asdict(self)


BUILD_CHAIN = (
    (("Power Plant", "Tesla Reactor", "Bio Reactor"), 1),
    (("Ore Refinery", "Slave Miner"), 1),
    (("Barracks",), 1),
    (("War Factory",), 1),
    (("Airforce Command", "Air Force Command", "Radar", "Psychic Radar"), 1),
    (("Battle Lab",), 1),
    (("Ore Refinery",), 2),
)
NON_COMBAT = ("Miner", "Harvester", "Construction Vehicle", "Engineer", "Spy", "Dog")


class StrategicTeacher:
    def __init__(self, config=None):
        self.config = config or StrategyConfig()
        self.tick = 0
        self.last_economy = -100
        self.last_queue = {"building": -100, "vehicle": -100, "infantry": -100}
        self.commands = {}
        self.launched = False
        self.last = None

    def metadata(self):
        return {"teacher_id": VERSION, "source": "collector_teacher", "action_n": S.ACTION_N,
                "config": self.config.as_dict(), "config_sha256": digest(self.config.as_dict()),
                "source_sha256": hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),
                "role": "candidate_external_301_teacher", "strength": "UNKNOWN"}

    @staticmethod
    def visible(row):
        # Defence in depth; hidden fields are never carried into policy state.
        out = {k: row.get(k) for k in ("own_objects", "visible_enemies", "factories", "houses", "money", "map", "t", "frame")}
        out["visible_enemies"] = [e for e in out["visible_enemies"] or []
                                  if not e.get("cell_shrouded") and int(e.get("cloak_state") or 0) not in (1, 2)]
        return out

    def choose(self, row, spec, starts, map_soa):
        if spec is None:
            raise ValueError("real FullControlSpec required")
        self.tick += 1
        row = self.visible(row)
        t = float(row.get("t") or 0)
        mask = [bool(x) for x in spec.masks(row, starts, map_soa)]
        if len(mask) != 301 or not any(mask):
            raise RuntimeError("no valid 301 mask")
        own = [o for o in row["own_objects"] or [] if S.placed(o)]
        buildings = [o for o in own if S.is_building(o)]
        army = [o for o in own if o.get("abstract") in ("ABSTRACT_TYPE_UNIT", "ABSTRACT_TYPE_INFANTRY")
                and not any(w.lower() in o.get("type_name", "").lower() for w in NON_COMBAT)]
        counts = Counter(o.get("type_name", "") for o in own)
        enemies = row["visible_enemies"] or []
        base = S.base_cell(row)
        names = list(counts)

        def matches(name, alternatives):
            return any((name.lower() == x.lower() or name.lower().endswith(" " + x.lower()))
                       if len(x) <= 3 else x.lower() in name.lower() for x in alternatives)

        def count(alternatives):
            return sum(n for name, n in counts.items() if matches(name, alternatives))

        def proposal(action, skill, queue, reason, **extra):
            if not (0 <= action < 301 and mask[action]):
                return None
            intent = spec.intent(action, row, starts, map_soa) if action else None
            if action and intent is None:
                return None
            return {"action": action, "skill_id": skill, "queue": queue, "reason": reason,
                    "intent": intent, **extra}

        def production(alternatives, queue, reason):
            for i, item in enumerate(spec.types):
                if matches(item["name"], alternatives):
                    p = proposal(2 + i, "economy.bootstrap" if queue == "building" else "production.army", queue, reason)
                    if p:
                        return p
            return None

        # Deploy/placement never wait behind a combat movement loop.
        urgent = None
        if base is None and mask[1]:
            urgent = proposal(1, "economy.deploy", "building", "deploy observed MCV")
        pending = S.pending(row)
        if pending and base is not None:
            # Try alternate sectors over successive commands, retaining native placement validation.
            offset = self.tick % 8
            for pidx in range(min(4, len(pending))):
                for d in range(8):
                    urgent = proposal(98 + pidx * 8 + (offset + d) % 8,
                                      "economy.place", "building", "place completed building")
                    if urgent:
                        break
                if urgent:
                    break

        economic = []
        if base is not None:
            house = next(iter(row["houses"] or []), {})
            power_names = ("Power Plant", "Tesla Reactor", "Bio Reactor", "Nuclear Reactor")
            power_low = (float(house.get("power_output") or 0) < float(house.get("power_drain") or 0) + 25)
            # Output fields can be absent/zero; cap fallback rather than producing endless reactors.
            target_power = 1 if len(buildings) < 4 else 2 if len(buildings) < 7 else 3
            if power_low and count(power_names) < target_power:
                p = production(power_names, "building", "power reserve")
                if p:
                    economic.append(p)
            if not economic:
                for alternatives, number in BUILD_CHAIN:
                    if count(alternatives) < number:
                        p = production(alternatives, "building", "ordered economy/technology bootstrap")
                        if p:
                            economic.append(p)
                        break
            miners = sum(n for name, n in counts.items() if "Miner" in name or "Harvester" in name)
            if count(("War Factory",)):
                if miners < self.config.desired_miners:
                    p = production(("Chrono Miner", "War Miner"), "vehicle", "sustain mining economy")
                    if p:
                        economic.append(p)
                elif len(army) < self.config.army_cap:
                    # Use high-tech ranged support and armour when available, with base tank fallback.
                    priorities = []
                    if count(("Battle Lab",)):
                        if count(("Prism Tank",)) < max(2, count(("Mirage Tank",)) // 2):
                            priorities.append(("Prism Tank",))
                        priorities.append(("Mirage Tank", "Apocalypse Tank", "Master Mind"))
                    if count(("IFV", "Flak Track")) < 2:
                        priorities.append(("IFV", "Flak Track"))
                    priorities.append(("Grizzly Battle Tank", "Rhino Heavy Tank", "Lasher Tank"))
                    for alternatives in priorities:
                        p = production(alternatives, "vehicle", "balanced ground army")
                        if p:
                            economic.append(p)
                            break
            infantry = sum(o.get("abstract") == "ABSTRACT_TYPE_INFANTRY" for o in army)
            if infantry < self.config.infantry_cap and len(army) < self.config.army_cap:
                p = production(("Guardian GI", "GI", "Conscript", "Initiate"), "infantry", "infantry support")
                if p:
                    economic.append(p)

        if not self.launched and len(army) >= self.config.attack_min_units and t >= self.config.attack_after_seconds:
            self.launched = True
        goal = S.enemy_start(row, starts) if starts and base is not None else None
        threats = [e for e in enemies if base is not None and math.dist(e.get("cell", base), base) < self.config.defense_radius]
        # Parse the real selection mapping each decision, never cached action indices.
        slots = [(i + 1, o) for i, o in enumerate(spec.units(row)) if identity(o) in {identity(u) for u in army}]
        slots.sort(key=lambda pair: (self.commands.get(identity(pair[1]), {}).get("t", -100), pair[1]["addr"]))
        targets = spec.targets(row)
        tactical = []
        for selection, unit in slots:
            key = identity(unit)
            previous = self.commands.get(key, {})
            cell = unit.get("cell") or base
            if cell is None:
                continue
            elapsed = t - previous.get("t", -100)
            if elapsed < 1.5:
                continue
            hp = float(unit.get("health") or 0)
            # The local type strength is public catalog data.
            max_hp = float(spec.all_types.get(unit.get("type_ptr"), {}).get("strength") or hp or 1)
            near_enemy = any(math.dist(e.get("cell", cell), cell) < 10 for e in enemies)
            if hp / max_hp < 0.28 and near_enemy and base is not None and elapsed >= 5:
                p = proposal(S.UNIT_START + selection * S.UNIT_ACTIONS + 18,
                             "combat.retreat", "combat", "withdraw damaged unit", actor_key=key)
                if p:
                    tactical.append(p)
                    continue
            pool = [(i, e) for i, e in enumerate(targets) if (self.launched or e in threats)
                    and math.dist(e.get("cell", cell), cell) <= (35 if self.launched else 23)]
            if pool:
                # Prioritize threats in reach over far economy targets; focus target is shared by distance score.
                i, target = min(pool, key=lambda pair: (0 if pair[1] in threats else 1,
                    0 if pair[1].get("abstract") != "ABSTRACT_TYPE_BUILDING" else 1,
                    math.dist(pair[1].get("cell", cell), base or cell), pair[1]["addr"]))
                objective = ("ATTACK", identity(target))
                if previous.get("objective") != objective or elapsed >= self.config.attack_cooldown_seconds:
                    p = proposal(S.UNIT_START + selection * S.UNIT_ACTIONS + 9 + i,
                                 "combat.defend" if target in threats else "combat.engage", "combat",
                                 "focus visible legal target", actor_key=key, objective=objective)
                    if p:
                        tactical.append(p)
                continue
            if previous.get("objective", (None,))[0] == "ATTACK" and elapsed < self.config.attack_cooldown_seconds:
                continue
            if base is None or elapsed < self.config.move_cooldown_seconds:
                continue
            destination = goal if self.launched and goal else base
            # Before launch, gather close to the base without repeatedly interrupting units.
            if math.dist(cell, destination) <= (5 if not self.launched else 8):
                continue
            direction = dir_bucket(destination[0] - cell[0], destination[1] - cell[1])
            p = proposal(S.UNIT_START + selection * S.UNIT_ACTIONS + 1 + direction,
                         "combat.advance" if self.launched else "combat.rally", "combat",
                         "serial single-slot advance" if self.launched else "regroup near base",
                         actor_key=key, objective=("MOVE", tuple(destination)))
            if p:
                tactical.append(p)

        economic.sort(key=lambda p: (self.last_queue[p["queue"]], 0 if p["queue"] == "building" else 1))
        if urgent:
            chosen = urgent
        elif economic and (not tactical or self.tick - self.last_economy >= self.config.max_economy_gap_ticks
                           or self.tick % 2 == 0):
            chosen = economic[0]
        elif tactical:
            chosen = tactical[0]
        elif economic:
            chosen = economic[0]
        else:
            chosen = proposal(0, "wait.progress", "wait", "queues busy or commands still active")
        if chosen is None:
            raise RuntimeError("NOOP unavailable: no safe supported proposal")
        if chosen["queue"] in self.last_queue:
            self.last_economy = self.tick
            self.last_queue[chosen["queue"]] = self.tick
        if chosen.get("actor_key"):
            self.commands[chosen["actor_key"]] = {"t": t, "objective": chosen.get("objective", ("RETREAT",))}
        chosen.update(teacher_id=VERSION, config_sha256=digest(self.config.as_dict()),
                      action_mask=mask, phase="ASSAULT" if self.launched else "BUILDUP",
                      observation_frame=row.get("frame"), tick=self.tick, time_s=t,
                      slot_bindings=[{"selection": i + 1, "identity": identity(o)} for i, o in enumerate(spec.units(row))],
                      target_bindings=[{"slot": i, "identity": identity(o)} for i, o in enumerate(targets)],
                      own_building_types=sorted(o["type_name"] for o in buildings), army_units=len(army),
                      source="collector_teacher", hidden_info_used=False)
        self.last = chosen
        return chosen
