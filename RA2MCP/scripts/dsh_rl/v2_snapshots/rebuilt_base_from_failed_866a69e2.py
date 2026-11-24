"""ScriptedTeacher V2: modular strategy teacher over ACTION_N=301 (does NOT replace V1).

Modules: Perception(ObservationView) / EconomyPlanner / Scouting(Frontier+EnemyMemory) /
SquadManager+CombatEvaluator+EngageFSM / ActionScheduler / Evaluation. Deterministic, seeded.
Only observed data is used (no hidden_enemy_count / no future info). ACK/APPLIED/EFFECT are
never read by the policy (they belong to the capture chain).
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict

ACTION_N = 301
UNIT_START = 130
UNIT_ACTIONS = 19
SEL_SLOTS = 9
CMD_MOVE = 1      # 1..8 directions
CMD_ATTACK = 9    # 9..16 target slots
CMD_STOP = 0
CMD_SCOUT = 17
CMD_RETREAT = 18
POLICY_VERSION = "scripted_teacher_v2:0.1.0"


def _hash(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:12]


class ObservationView:
    """Sanitized read-only view. Drops hidden_enemy_count and any non-visible fields."""
    ALLOWED = ("own_objects", "visible_enemies", "factories", "houses", "money", "map", "t", "frame")

    def __init__(self, row, starts=None, catalog=None):
        self.row = {k: row.get(k) for k in self.ALLOWED if k in row}
        self.starts = starts or []
        self.catalog = catalog or []
        self.reject_hidden = "hidden_enemy_count" not in self.row

    @property
    def t(self): return float(self.row.get("t") or 0.0)
    @property
    def money(self): return int(self.row.get("money") or 0)
    def own(self): return [o for o in (self.row.get("own_objects") or []) if o.get("on_map", True) and not o.get("in_limbo")]
    def buildings(self): return [o for o in self.own() if o.get("abstract") == "ABSTRACT_TYPE_BUILDING" or "Construction Yard" in (o.get("type_name") or "")]
    def units(self): return [o for o in self.own() if o not in self.buildings()]
    def enemies(self): return list(self.row.get("visible_enemies") or [])
    def base_cell(self):
        b = self.buildings()
        if b:
            cells = [o.get("cell") or [0, 0] for o in b]
            return (sum(c[0] for c in cells) / len(cells), sum(c[1] for c in cells) / len(cells))
        u = self.units()
        if u:
            cells = [o.get("cell") or [0, 0] for o in u]
            return (sum(c[0] for c in cells) / len(cells), sum(c[1] for c in cells) / len(cells))
        return (0.0, 0.0)
    def digest(self):
        return _hash({k: self.row.get(k) for k in ("money", "visible_enemies", "frame")} |
                     {"own": [(o.get("addr"), o.get("health"), tuple(o.get("cell") or [])) for o in self.own()]})


# ---------------------------------------------------------------- economy
class EconomyPlanner:
    """Dynamic build/production targets; keeps queue busy; power/ore balance."""
    TARGETS = {"refinery": 3, "power": 3, "barracks": 2, "warfatory": 2, "defense": 3, "tech": 1}

    def __init__(self):
        self.targets = dict(self.TARGETS)
        self.last_hit = None

    def update(self, view):
        b = view.buildings()
        n = Counter()
        for o in b:
            nm = (o.get("type_name") or "").lower()
            for k in self.targets:
                if k.replace("warfatory", "war factory").replace("tech", "battle lab") in nm or (
                        k == "power" and "power plant" in nm) or (k == "refinery" and "refinery" in nm):
                    n[k] += 1
            if "barracks" in nm: n["barracks"] += 1
            if "war factory" in nm: n["warfatory"] += 1
            if "battle lab" in nm: n["tech"] += 1
            if "patriot" in nm or "prism" in nm or "flak" in nm: n["defense"] += 1
        money = view.money
        drains = sum(int(h.get("power_drain") or 0) for h in (view.row.get("houses") or []))
        out = sum(int(h.get("power_output") or 0) for h in (view.row.get("houses") or []))
        if out < drains + 20:
            self.targets["power"] = max(self.targets["power"], n["power"] + 1)
        if money > 4000:
            self.targets["refinery"] = max(3, self.targets["refinery"])
            self.targets["defense"] = max(3, self.targets["defense"])
        return {"counts": dict(n), "targets": dict(self.targets), "money": money, "power": (out, drains)}


# ---------------------------------------------------------------- scouting
class EnemyMemory:
    """Last-known enemy positions from visible enemies only; decays over time."""
    def __init__(self, ttl=60.0):
        self.ttl = ttl; self.mem = {}; self.last_seen_t = None

    def update(self, view):
        t = view.t
        for e in view.enemies():
            addr = int(e.get("addr", -1))
            self.mem[addr] = {"cell": e.get("cell"), "type": e.get("type_name"),
                              "abstract": e.get("abstract"), "t": t}
            self.last_seen_t = t
        self.mem = {k: v for k, v in self.mem.items() if t - v["t"] <= self.ttl}
        return len(self.mem)

    def positions(self):
        return [v["cell"] for v in self.mem.values() if v.get("cell")]


class FrontierExplorer:
    """Goal-based scouting: last-known enemy > public start (legal public map info) > spiral.
    Tracks visited goals to detect repeated paths; never reads hidden enemy data."""
    def __init__(self, threshold_s=20.0):
        self.threshold = threshold_s; self.visited = []; self.turns = 0; self.repeats = 0

    def should_scout(self, view, memory):
        if memory.last_seen_t is None: return view.t > 5
        return view.t - memory.last_seen_t > self.threshold

    def goal(self, view, memory, starts=None, last_goal=None):
        pos = memory.positions()
        if pos:
            cell = pos[-1]; self._visit(cell); return tuple(cell)
        if starts:
            s0 = starts[0]
            if isinstance(s0, (list, tuple)) and len(s0) >= 2:
                cell = (s0[0], s0[1]); self._visit(cell); return cell
        bx, by = view.base_cell()
        dirs = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]
        dx, dy = dirs[self.turns % 8]; self.turns += 1
        cell = (bx + dx * 20, by + dy * 20); self._visit(cell); return cell

    def _visit(self, cell):
        if cell in self.visited[-3:]:
            self.repeats += 1
        self.visited.append(cell)

    def next_direction(self):
        d = self.turns % 8; self.turns += 1; return d


# ---------------------------------------------------------------- squad/combat
class SquadManager:
    """Group combat units, track rally point and cohesion."""
    def __init__(self, rally_offset=6):
        self.rally_offset = rally_offset
        self.group = []  # list of addrs
        self.rally = None

    def update(self, view, enemy_dir=(1, 0)):
        units = view.units()
        combat = [u for u in units if "Miner" not in (u.get("type_name") or "")]
        self.group = [int(u.get("addr", -1)) for u in combat]
        bx, by = view.base_cell()
        norm = math.hypot(enemy_dir[0], enemy_dir[1]) or 1.0
        self.rally = (bx + enemy_dir[0] / norm * self.rally_offset,
                      by + enemy_dir[1] / norm * self.rally_offset)
        return {"group_size": len(combat), "rally": self.rally}


class CombatEvaluator:
    """HP-weighted force comparison; decides engage/retreat thresholds."""
    RANGE = {"Grizzly Battle Tank": 6, "GI": 5, "Allied Attack Dog": 1, "Engineer": 0, "Chrono Miner": 0,
             "Allied Construction Vehicle": 0}
    ENGAGE_DIST = 16.0  # default fallback only; primary check is per-unit effective range

    def _range(self, u):
        return self.RANGE.get(u.get("type_name"), 4)

    def evaluate(self, view, squad, memory):
        own = [u for u in view.units() if "Miner" not in (u.get("type_name") or "")]
        own_hp = sum(float(u.get("health") or 0) for u in own)
        enemies = view.enemies()
        enemy_hp = sum(float(e.get("health") or 0) for e in enemies)
        eligible = []  # (enemy, min_distance)
        for e in enemies:
            c = e.get("cell") or [0, 0]
            dists = []
            for u in own:
                uc = u.get("cell") or [0, 0]
                d = abs(c[0] - uc[0]) + abs(c[1] - uc[1])
                if d <= self._range(u): dists.append((d, u.get("type_name"), int(u.get("addr", -1))))
            if dists: eligible.append((e, min(x[0] for x in dists), dists))
        eligible_addrs = {int(e.get("addr", -1)) for e, _, _ in eligible}
        state = "REGROUP"
        if len(own) == 0:
            state = "REGROUP"
        elif enemy_hp > 0 and own_hp < enemy_hp * 0.6:
            state = "RETREAT"
        elif eligible:
            state = "ENGAGE"
        elif enemies or (memory.last_seen_t is not None and view.t - memory.last_seen_t < 30):
            state = "ADVANCE"
        near_targets = [e.get("addr") for e, _, _ in eligible]
        return {"state": state, "own_hp": own_hp, "enemy_hp": enemy_hp,
                "own_units": len(own), "near_targets": near_targets,
                "eligible_attack_units": sum(len(x[2]) for x in eligible),
                "focus_fire_candidate_count": len(near_targets),
                "selected_target_id": near_targets[0] if near_targets else None,
                "target_distance": eligible[0][1] if eligible else None,
                "enemy_visible_count": len(enemies),
                "target_slot_map": {int(e.get("addr", -1)): i for i, (e, _, _) in enumerate(eligible[:8])},
                "eligible_addrs": sorted(eligible_addrs)}


class BattleReport:
    """Post-hoc evaluation from observations/truth (never fed back into policy decisions)."""
    def __init__(self):
        self.dmg_out = 0.0; self.hp_lost = 0.0; self.attacks = 0; self.effective = 0
        self.own_units_start = None; self.losses = Counter(); self.kills = Counter()

    def update(self, view):
        if self.own_units_start is None: self.own_units_start = len(view.units())
        if view.enemies(): self.attacks += 0
        return self

    def finalize(self, truth_metrics=None):
        m = truth_metrics or {}
        return {"policy": POLICY_VERSION, "module": "BattleReport",
                "attacks_issued": m.get("attacks_issued"), "attacks_effective": m.get("attacks_effective"),
                "damage_dealt": m.get("damage_dealt"), "own_hp_lost": m.get("own_hp_lost"),
                "efficiency": (m.get("attacks_effective") or 0) / max(1, m.get("attacks_issued") or 0),
                "survival_ratio": None if not self.own_units_start else round(
                    (self.own_units_start - sum(self.losses.values())) / self.own_units_start, 3)}


# ---------------------------------------------------------------- action scheduler
class ActionScheduler:
    """One 301 action per decision with age-based fairness (prevents starvation)."""
    TASKS = ("PLACE", "PRODUCE", "DEPLOY", "RETREAT", "FOCUS_FIRE", "RALLY", "SCOUT", "STOP_NOOP", "NOOP")

    def __init__(self):
        self.age = {k: 0 for k in self.TASKS}; self.history = []
        self.fairness_boost = 5

    def _pick(self, candidates):
        # candidates: list of (task, action, meta); age boost ensures low-priority tasks eventually run
        best = None; best_score = -1
        for task, action, meta in candidates:
            if action is None:
                continue
            score = 100 - self.TASKS.index(task) * 10 + self.fairness_boost * self.age.get(task, 0)
            if score > best_score:
                best = (task, action, meta); best_score = score
        if best is None:
            best = ("NOOP", 0, {"reason": "no_legal_action"})
        for k in self.age:
            self.age[k] = self.age[k] + 1 if k != best[0] else 0
        self.history.append(best[0])
        return best

    def schedule(self, view, info, mask_fn):
        c = []
        # 1) place pending direction
        for which in range(4):
            a = 98 + which * 8 + (self.age["PLACE"] % 8)
            if mask_fn(a): c.append(("PLACE", a, {"which": which})); break
        # 2) production (catalog slots via env adapter)
        prod = info.get("prod_slots", []); types = info.get("prod_types", [])
        wants = ["Grizzly", "GI", "Attack Dog", "Engineer"]
        order = []
        for w in wants:
            for i, ok in enumerate(prod):
                nm = types[i] if i < len(types) else ""
                if ok and w in nm: order.append(i)
        if not order:
            order = [i for i, ok in enumerate(prod) if ok]
        for slot in order[:1]:
            if mask_fn(2 + slot): c.append(("PRODUCE", 2 + slot, {"slot": slot})); break
        # 3) deploy
        for sel, ok in enumerate(info.get("deploy_units", [])[:1]):
            if ok and mask_fn(1): c.append(("DEPLOY", 1, {"sel": sel}))
        # 4) retreat
        if info["combat"]["state"] == "RETREAT":
            c.append(("RETREAT", UNIT_START + CMD_RETREAT, {"group": 0}))
        # 5) focus fire: group attack selection 0 to nearest eligible target
        info["focus_fire_candidate_count"] = len(info["combat"].get("near_targets") or [])
        if info["combat"]["state"] == "ENGAGE" and info["combat"]["near_targets"]:
            for addr, slot in list(info["combat"]["target_slot_map"].items())[:8]:
                a = UNIT_START + CMD_ATTACK + slot
                if mask_fn(a): c.append(("FOCUS_FIRE", a, {"target": addr, "slot": slot})); break
        # 6) rally/advance: group move 8 directions toward enemy/start
        if info["combat"]["state"] in ("REGROUP", "ADVANCE"):
            d = self._dir_to(view.base_cell(), view, info)
            a = UNIT_START + CMD_MOVE + d
            if mask_fn(a): c.append(("RALLY", a, {"dir": d}))
        # 7) scout: send scout unit with SCOUT command
        if info["scout_now"]:
            sel = info.get("scout_sel")
            if sel is not None:
                a = UNIT_START + sel * UNIT_ACTIONS + CMD_SCOUT
                if mask_fn(a): c.append(("SCOUT", a, {"sel": sel}))
        # 8) stop if idle
        if mask_fn(UNIT_START + CMD_STOP): c.append(("STOP_NOOP", UNIT_START, {"reason": "idle"}))
        return self._pick(c)

    def _dir_to(self, src, view, info):
        target = info.get("enemy_goal") or view.base_cell()
        dx, dy = target[0] - src[0], target[1] - src[1]
        if abs(dx) < 0.5 and abs(dy) < 0.5: return 0
        ang = (math.degrees(math.atan2(dy, dx)) + 360) % 360
        return int(round(ang / 45.0)) % 8


# ---------------------------------------------------------------- teacher
class ScriptedTeacherV2:
    def __init__(self, variant="extreme_v2", seed=0):
        self.variant = variant; self.seed = seed
        self.economy = EconomyPlanner(); self.memory = EnemyMemory()
        self.explorer = FrontierExplorer(); self.squad = SquadManager()
        self.combat = CombatEvaluator(); self.report = BattleReport()
        self.sched = ActionScheduler(); self.decisions = 0
        self.reward_meta = {"policy_version": POLICY_VERSION, "modules": []}

    def reset(self):
        """Per-episode reset (no cross-episode leakage)."""
        self.__init__(self.variant, self.seed)

    def policy_metadata(self):
        return {"policy_version": POLICY_VERSION, "variant": self.variant,
                "action_n": ACTION_N, "deterministic": True,
                "module_hashes": {"teacher": _hash(self.__class__.__name__)}}

    def _make_mask_fn(self, info):
        legal = info.get("legal_actions")
        if legal is None:
            return lambda a: 0 <= int(a) < ACTION_N
        return lambda a: int(a) in set(legal)

    def choose(self, env, view=None):
        v = view or ObservationView(getattr(env, "current", {}) or {}, getattr(env, "starts", []) or [],
                                    getattr(env, "catalog", []) or [])
        self.decisions += 1
        self.memory.update(v); self.squad.update(v, self._enemy_dir(v))
        econ = self.economy.update(v)
        scout_now = self.explorer.should_scout(v, self.memory)
        units = v.units()
        fast = [u for u in units if "Engineer" not in (u.get("type_name") or "") and "Miner" not in (u.get("type_name") or "")]
        scout_sel = None
        if scout_now and fast:
            idx = units.index(fast[0]) + 1  # selection slots 1..8
            scout_sel = min(8, idx)
        combat = self.combat.evaluate(v, self.squad, self.memory)
        scout_goal = self.explorer.goal(v, self.memory, v.starts) if scout_now else None
        info = {"combat": combat, "econ": econ, "scout_now": scout_now, "scout_sel": scout_sel,
                "scout_goal": scout_goal,
                "prod_slots": [bool(x) for x in (getattr(env, "available_production", lambda: [])() or [])][:96],
                "prod_types": list(getattr(env, "production_types", lambda: [])() or []),
                "deploy_units": [True], "enemy_goal": self._enemy_goal(v),
                "legal_actions": getattr(env, "legal_actions", None)() if callable(getattr(env, "legal_actions", None)) else None}
        mask_fn = self._make_mask_fn(info)
        task, action, meta = self.sched.schedule(v, info, mask_fn)
        action = int(action)
        assert 0 <= action < ACTION_N
        rationale = {"policy_version": POLICY_VERSION, "task": task, "action": action, "meta": meta,
                     "state": combat["state"], "money": v.money, "own_units": len(units),
                     "enemies_visible": len(v.enemies()), "digest": v.digest(),
                     "scout": scout_now, "squad": self.squad.group, "t": v.t}
        self.reward_meta["modules"].append(task)
        label = {"FOCUS_FIRE": "attack", "RETREAT": "retreat", "SCOUT": "scout",
                 "RALLY": "move", "PRODUCE": "produce", "PLACE": "place", "DEPLOY": "deploy",
                 "STOP_NOOP": "noop", "NOOP": "noop"}.get(task, "noop")
        return action, label, rationale

    def _enemy_dir(self, v):
        e = v.enemies()
        if e:
            c = e[0].get("cell") or [0, 0]
            b = v.base_cell()
            return (c[0] - b[0], c[1] - b[1])
        return (1, 0)

    def _enemy_goal(self, v):
        en = v.enemies()
        if en:
            cells = [e.get("cell") or [0, 0] for e in en]
            return (sum(c[0] for c in cells) / len(cells), sum(c[1] for c in cells) / len(cells))
        pos = self.memory.positions()
        if pos:
            return (sum(p[0] for p in pos) / len(pos), sum(p[1] for p in pos) / len(pos))
        if v.starts:
            s = v.starts[0]
            return (s[0] if isinstance(s, (list, tuple)) else 0, s[1] if isinstance(s, (list, tuple)) else 0)
        return v.base_cell()


class HostAdapter:
    """Adapter from real RA2FullEnv to V2's read-only env interface (real catalog + legal mask)."""
    def __init__(self, env):
        self.env = env; self.refresh()
    def refresh(self):
        self.current = self.env.current
        self.starts = list(getattr(self.env, "starts", []) or [])
        self.catalog = list(getattr(self.env, "catalog", []) or [])
    def available_production(self):
        full = getattr(self.env, "full", None)
        if full is None: return [False] * 96
        n = min(96, len(full.types))
        return [bool(full.can_produce(i, self.current)) for i in range(n)] + [False] * (96 - n)
    def production_types(self):
        full = getattr(self.env, "full", None)
        if full is None: return []
        return [getattr(t, "get", lambda k, d=None: getattr(t, k, d))("name") for t in full.types]

    def legal_actions(self):
        try:
            import numpy as np
            return np.flatnonzero(self.env.action_masks()).tolist()
        except Exception:  # noqa: BLE001
            return None
