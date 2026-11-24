"""One real RA2 game session for the headless-window feasibility experiments.

Reuses the verified project bridge helpers (`dsh_episode.runner`) for launch,
state reads and native order execution, but does *not* train, does not write
episodes into reports/rl and does not touch the training copies.  The rule
policy only builds a normal economy and issues real orders so that building,
production, movement and combat keep happening while we toggle the window.

Everything is measured per decision step and written as JSONL by the caller.
"""
import asyncio
import math
import pathlib
import sys
import time
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "dsh_train"))
sys.path.insert(0, str(ROOT / "vendor" / "pyra2yr"))
sys.path.insert(0, str(ROOT / "scripts" / "headless"))

from dsh_episode import actions as actions_mod  # noqa: E402
from dsh_episode import obs, schema  # noqa: E402
from dsh_episode.hostility import is_opponent  # noqa: E402
from dsh_episode.policy import RulePolicy  # noqa: E402
from dsh_episode.runner import (EpisodeRunner, env_fingerprint, launch_game,  # noqa: E402
                                public_start_cells, read_initial, read_map_soa,
                                read_state, send_cmd)
from dsh_rl.reward import progress_reward, reward_parts  # noqa: E402
from dsh_rl.reward_config import (RewardLedger, load as load_rewards,  # noqa: E402
                                  revision as reward_revision)
from ra2yrproto import ra2yr  # noqa: E402

import winapi  # noqa: E402

CELL = 256
EXE_NAME = "gamemd-spawn-ra2yrcpp.exe"
BENIGN = "already exists"


# --------------------------------------------------------------- helpers ----
def is_building(o):
    return o.get("abstract") == "ABSTRACT_TYPE_BUILDING"


def placed(o):
    return bool(o.get("on_map")) and not o.get("in_limbo") and o.get("health", 0) > 0


def is_mcv(o):
    return "Construction Vehicle" in o.get("type_name", "")


def base_cell(row):
    return next((o["cell"] for o in row.get("own_objects", [])
                 if placed(o) and "Construction Yard" in o.get("type_name", "")), None)


def enemy_start(row, starts):
    starts = [s for s in (starts or [])]
    if not starts:
        return None
    anchor = base_cell(row)
    if anchor is None:
        buildings = [o for o in row.get("own_objects", [])
                     if placed(o) and is_building(o)]
        if not buildings:
            return None
        anchor = [sum(o["cell"][i] for o in buildings) / len(buildings) for i in (0, 1)]
    return max(starts, key=lambda s: math.dist(anchor, s))


_NON_COMBAT = ("Construction Vehicle", "Miner", "Harvester", "Chrono Miner")


def combat_units(row):
    out = []
    for o in row.get("own_objects", []):
        if not placed(o) or is_building(o) or is_mcv(o):
            continue
        if any(hint in o.get("type_name", "") for hint in _NON_COMBAT):
            continue
        out.append(o)
    return out


def advance_delta(before, after, starts):
    if not starts:
        return 0.0
    def potential(row):
        units = combat_units(row)
        goal = enemy_start(row, starts)
        if not units or goal is None:
            return None
        center = [sum(o["cell"][i] for o in units) / len(units) for i in (0, 1)]
        return -min(200.0, math.dist(center, goal))
    old, new = potential(before), potential(after)
    if old is None or new is None:
        return 0.0
    return new - old


def enemy_base_contact(row, starts, radius=25.0, min_units=2):
    units = combat_units(row)
    goal = enemy_start(row, starts)
    if len(units) < min_units or goal is None:
        return False
    center = [sum(o["cell"][i] for o in units) / len(units) for i in (0, 1)]
    return math.dist(center, goal) <= radius


def enemy_building_sight(row):
    return any(is_building(o) for o in row.get("visible_enemies", [])
               if is_opponent(o))


def deployment_safe_reward(before, after, kind="running", invalid=0):
    old_cy = sum(is_building(o) and "Construction Yard" in o.get("type_name", "")
                 for o in before.get("own_objects", []))
    new_cy = [o for o in after.get("own_objects", [])
              if is_building(o) and "Construction Yard" in o.get("type_name", "")]
    current = {o["addr"] for o in after.get("own_objects", [])}
    if len(new_cy) > old_cy:
        own = [o for o in before.get("own_objects", []) if not
               (is_mcv(o) and o["addr"] not in current
                and any(math.dist(o["cell"], b["cell"]) < 4 for b in new_cy))]
        before = dict(before, own_objects=own)
    return reward_parts(before, after, kind, invalid)


class RewardTracker:
    """Replicates the full_configurable_v3 accounting used by training."""

    def __init__(self):
        self.config = load_rewards()
        self.revision = reward_revision(self.config)
        self.ledger = RewardLedger(self.config)
        self.seen_buildings = set()
        self.seen_units = set()
        self.prev_unit_count = 0
        self.attack_issued = 0
        self.last_attack_issued = 0
        self.first_damage_done = False
        self.advance_seen = False
        self.enemy_sight_seen = False
        self.max_mapped = 0
        self.total = 0.0
        self.parts_total = {}

    def reset_episode(self, row):
        self.seen_units = {o["type_name"] for o in row["own_objects"]
                           if not is_building(o)}
        self.seen_buildings = {o["type_name"] for o in row["own_objects"]
                               if is_building(o) and placed(o)}
        self.prev_unit_count = sum(1 for o in row["own_objects"]
                                   if not is_building(o) and placed(o) and not is_mcv(o))
        self.max_mapped = int((row.get("map") or {}).get("mapped", 0))

    def step(self, before, after, kind, invalid=0, attack_delta=0):
        parts = deployment_safe_reward(before, after, kind, invalid)
        self.attack_issued += int(attack_delta)
        building_types = {o["type_name"] for o in after["own_objects"]
                          if is_building(o) and placed(o)}
        unit_types = {o["type_name"] for o in after["own_objects"]
                      if not is_building(o) and placed(o)}
        first_buildings = building_types - self.seen_buildings
        first_units = unit_types - self.seen_units
        yards = sum("Construction Yard" in name for name in first_buildings)
        self.seen_buildings.update(building_types)
        self.seen_units.update(unit_types)
        mapped = int((after.get("map") or {}).get("mapped", 0))
        placed_units = [o for o in after["own_objects"]
                        if not is_building(o) and placed(o) and not is_mcv(o)]
        unit_count = len(placed_units)
        unit_delta = max(0, unit_count - self.prev_unit_count)
        self.prev_unit_count = unit_count
        if attack_delta == 0 and self.attack_issued > self.last_attack_issued:
            attack_delta = self.attack_issued - self.last_attack_issued
        self.last_attack_issued = self.attack_issued
        first_damage = 0
        if parts["opponent_hp"] > 0 and not self.first_damage_done:
            self.first_damage_done = True
            first_damage = 1
        advance = 0.0 if kind != "running" else advance_delta(before, after, self.starts)
        contact = kind == "running" and enemy_base_contact(after, self.starts)
        first_advance = 1 if contact and not self.advance_seen else 0
        self.advance_seen = self.advance_seen or contact
        sight = kind == "running" and enemy_building_sight(after)
        first_sight = 1 if sight and not self.enemy_sight_seen else 0
        self.enemy_sight_seen = self.enemy_sight_seen or sight
        quantities = {
            "opponent_hp": parts["opponent_hp"] * 100,
            "own_hp": -parts["own_hp"] * 100,
            "invalid": -parts["invalid"] * 10,
            "victory": int(kind == "win"), "defeat": int(kind == "defeat"),
            "time": max(0.0, after.get("t", 0) - before.get("t", 0)),
            "progress": progress_reward(before, after, self.starts, kind) / 0.1,
            "exploration": max(0, mapped - self.max_mapped),
            "construction_yard": yards,
            "first_building_type": len(first_buildings) - yards,
            "first_unit_type": len(first_units),
            "unit_produced": unit_delta,
            "attack": max(0, attack_delta), "first_damage": first_damage,
            "income": max(0, after["money"] - before["money"]),
            "advance": advance, "first_advance": first_advance,
            "first_enemy_sight": first_sight,
        }
        self.max_mapped = max(self.max_mapped, mapped)
        applied = self.ledger.apply(quantities)
        total = float(sum(applied.values()))
        self.total += total
        for key, value in applied.items():
            self.parts_total[key] = self.parts_total.get(key, 0.0) + value
        return total, applied

    def set_starts(self, starts):
        self.starts = starts


# --------------------------------------------------------- metrics block ----
def extract_metrics(state, hero_self, type_names, house_names):
    """Per-state object aggregates plus addr->(cell,hp) maps for deltas."""
    own = {"objects": 0, "buildings": 0, "units": 0, "infantry": 0, "hp": 0,
           "placed_buildings": 0, "placed_units": 0}
    enemy = {"objects": 0, "buildings": 0, "units": 0, "infantry": 0, "hp": 0}
    neutral = {"objects": 0, "buildings": 0, "units": 0}
    pos = {}
    heroes = None
    for o in state.objects:
        hp = int(o.health)
        if hp <= 0:
            continue
        owner = int(getattr(o, "pointer_house", 0))
        abstract = ra2yr.AbstractType.Name(getattr(o, "object_type", 0))
        type_ptr = int(getattr(o, "pointer_technotypeclass", 0))
        name = type_names.get(type_ptr, "?")
        cell = (int(o.coordinates.x) // CELL, int(o.coordinates.y) // CELL)
        on_map = bool(getattr(o, "on_map", False))
        in_limbo = bool(getattr(o, "in_limbo", False))
        entry = {"cell": cell, "hp": hp, "name": name, "abstract": abstract,
                 "on_map": on_map, "in_limbo": in_limbo, "owner": owner}
        if owner == hero_self:
            own["objects"] += 1
            own["hp"] += hp
            if abstract == "ABSTRACT_TYPE_BUILDING":
                own["buildings"] += 1
                if on_map and not in_limbo:
                    own["placed_buildings"] += 1
            elif abstract == "ABSTRACT_TYPE_INFANTRY":
                own["infantry"] += 1
                own["units"] += 1
            else:
                own["units"] += 1
            if on_map and not in_limbo:
                own["placed_units"] += 1
            pos[("own", int(o.pointer_self))] = entry
        elif owner != 0 and is_opponent({"owner": house_names.get(owner, "?")}):
            enemy["objects"] += 1
            enemy["hp"] += hp
            if abstract == "ABSTRACT_TYPE_BUILDING":
                enemy["buildings"] += 1
            elif abstract == "ABSTRACT_TYPE_INFANTRY":
                enemy["infantry"] += 1
                enemy["units"] += 1
            else:
                enemy["units"] += 1
            pos[("enemy", int(o.pointer_self))] = entry
        elif owner == 0:
            neutral["objects"] += 1
            if abstract == "ABSTRACT_TYPE_BUILDING":
                neutral["buildings"] += 1
            else:
                neutral["units"] += 1
    return {"own": own, "enemy": enemy, "neutral": neutral}, pos


def diff_metrics(prev_metrics, prev_pos, metrics, pos):
    """Damage / destruction / movement derived from consecutive snapshots."""
    if prev_metrics is None:
        return {"damage_to_enemy": 0, "damage_to_own": 0, "enemy_destroyed": 0,
                "own_lost": 0, "moved_objects": 0, "moved_cells": 0.0,
                "enemy_buildings_delta": 0, "own_buildings_delta": 0,
                "enemy_units_delta": 0, "own_units_delta": 0}
    moved_objects, moved_cells = 0, 0.0
    for key, cur in pos.items():
        prev = prev_pos.get(key)
        if prev is None:
            continue
        if prev["cell"] != cur["cell"]:
            moved_objects += 1
            moved_cells += math.dist(prev["cell"], cur["cell"])
    dmg_enemy = dmg_own = 0
    enemy_destroyed = own_lost = 0
    for key, cur in pos.items():
        prev = prev_pos.get(key)
        if prev is not None and cur["hp"] < prev["hp"]:
            if key[0] == "own":
                dmg_own += prev["hp"] - cur["hp"]
            else:
                dmg_enemy += prev["hp"] - cur["hp"]
    for key, prev in prev_pos.items():
        if key in pos:
            continue
        if key[0] == "enemy":
            enemy_destroyed += 1
        else:
            own_lost += 1
    return {
        "damage_to_enemy": int(dmg_enemy), "damage_to_own": int(dmg_own),
        "enemy_destroyed": enemy_destroyed, "own_lost": own_lost,
        "moved_objects": moved_objects, "moved_cells": round(moved_cells, 3),
        "enemy_buildings_delta": metrics["enemy"]["buildings"] - prev_metrics["enemy"]["buildings"],
        "own_buildings_delta": metrics["own"]["buildings"] - prev_metrics["own"]["buildings"],
        "enemy_units_delta": metrics["enemy"]["units"] - prev_metrics["enemy"]["units"],
        "own_units_delta": metrics["own"]["units"] - prev_metrics["own"]["units"],
    }


def classify(state, hero):
    enemies = [h for h in state.houses
               if not h.is_human_player and h.name == "Computer"]
    enemies_all_defeated = bool(enemies) and all(h.defeated for h in enemies)
    outcome = schema.classify_outcome(
        stage_name=ra2yr.LoadStage.Name(state.stage),
        human_defeated=bool(hero.defeated) if hero else False,
        human_is_loser=bool(getattr(hero, "is_loser", False)) if hero else False,
        human_is_winner=bool(getattr(hero, "is_winner", False)) if hero else False,
        enemies_all_defeated=enemies_all_defeated,
        proc_exited=False, elapsed_s=0.0, max_seconds=None)
    return outcome["kind"]


# --------------------------------------------------------------- session ----
class GameSession:
    def __init__(self, index, game_dir, port, run_dir=None, label=None):
        self.index = index
        self.game_dir = pathlib.Path(game_dir)
        self.port = int(port)
        self.label = label or f"instance{index}"
        self.run_dir = pathlib.Path(run_dir) if run_dir else None
        self.proc = None
        self.pid = None
        self.client = None
        self.window = None
        self.log_lines = []
        self.t0 = None                 # monotonic INGAME reference
        self.frame0 = None
        self.last_frame = None
        self.last_frame_at = None
        self.stall_events = 0
        self.rehide_count = 0
        self.show_repair_count = 0
        self.window_samples = {"visible": 0, "hidden": 0, "gone": 0}
        self.read_ok = 0
        self.read_fail = 0
        self.read_latencies = []
        self.steps = 0
        self.orders_ok = 0
        self.invalid_orders = 0
        self.attack_issued_step = 0
        self.outcome = "running"
        self.exit_code = None
        self.crash_detail = None
        self.reward = RewardTracker()
        self.reward.set_starts([])
        self.launch_to_window_s = None
        self.launch_to_ingame_s = None
        self.launch_started = None
        self.phase_records = []
        self.prev_walk = None
        self.prev_metrics = None
        self.prev_pos = None
        self.last_actions = []
        self.last_acks = []
        self.exec_shim = None

    # ---- logging ----
    def log(self, msg):
        line = f"[{time.strftime('%H:%M:%S')}][{self.label}] {msg}"
        print(line, flush=True)
        self.log_lines.append(line)

    # ---- launch / connect ----
    def launch(self):
        self.launch_started = time.perf_counter()
        self.proc = launch_game(self.game_dir)
        self.pid = self.proc.pid
        self.log(f"launched pid={self.pid} dir={self.game_dir}")
        return self.pid

    async def wait_ingame(self, timeout=120.0):
        from pyra2yr.network import DualClient
        deadline = time.perf_counter() + timeout
        client = None
        reads = 0
        while time.perf_counter() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"game exited during launch rc={self.proc.returncode}")
            if client is None:
                client = DualClient("127.0.0.1", self.port, timeout=2)
                client.connect()
                await asyncio.sleep(1.0)
            st = await read_state(client)
            reads += 1
            if st is not None and st.stage == 2 and len(st.objects) > 0:
                self.client = client
                if self.window is None:
                    self.window = winapi.pick_game_window(self.pid)
                if self.launch_to_window_s is None and self.window is not None:
                    self.launch_to_window_s = round(time.perf_counter() - self.launch_started, 3)
                self.launch_to_ingame_s = round(time.perf_counter() - self.launch_started, 3)
                self.log(f"INGAME after {self.launch_to_ingame_s}s "
                         f"frame={int(st.current_frame)} objects={len(st.objects)} "
                         f"window={self.window and self.window['hwnd']}")
                return st
            await asyncio.sleep(1.0)
        raise RuntimeError(f"INGAME timeout after {reads} state reads")

    async def setup(self, ingame_state, warmup_s=3.0):
        """Read the type catalog / map, build the rule policy and reward tracker."""
        initial = await read_initial(self.client)
        self.type_by_ptr = {int(t.pointer_self): t for t in initial.object_types}
        self.type_names = {int(t.pointer_self): t.name for t in initial.object_types}
        catalog = [{"pointer_self": int(t.pointer_self), "name": t.name,
                    "array_index": int(t.array_index),
                    "type": ra2yr.AbstractType.Name(t.type)}
                   for t in initial.object_types]
        self.catalog = catalog
        self.starts = public_start_cells(path=self.game_dir / "spawnmap.ini")
        self.policy = RulePolicy(type_catalog=catalog)
        self.reward.set_starts(self.starts)
        self.house_names = {int(h.self): h.name for h in ingame_state.houses}
        self.hero = next(h for h in ingame_state.houses
                         if h.current_player and h.is_human_player)
        opts = SimpleNamespace(episode_id=f"headless-{self.label}", port=self.port,
                               launch_timeout=120, max_seconds=0, attach=True,
                               resume=False, keep_game=True, kill_existing=False,
                               game_dir=str(self.game_dir))
        self.exec_shim = EpisodeRunner(opts)
        self.exec_shim.client = self.client
        self.exec_shim.type_by_ptr = self.type_by_ptr
        self.exec_shim.stats = {"orders": 0, "invalid_orders": 0,
                                "benign_duplicates": 0, "visibility_violations": 0,
                                "stale_skipped": 0, "stale_filtered": 0,
                                "model_orders_ok": 0, "fallback_orders_ok": 0}
        self.exec_shim.log = self.log
        self.map_soa = None
        self.last_map_at = -99.0
        self.env_fingerprint = env_fingerprint(
            {"public_start_cells": self.starts, "instance": self.index,
             "bridge_port": self.port, "headless_harness": True},
            game_dir=self.game_dir)
        # prime previous observation
        self.t0 = time.perf_counter()
        self.t_start_wall_ms = int(time.time() * 1000)
        self.map_soa = await self._maybe_map(force=True)
        self.prev_walk = await self._walk(ingame_state)
        self.reward.reset_episode(self.prev_walk)
        self.prev_metrics, self.prev_pos = extract_metrics(
            ingame_state, int(self.hero.self), self.type_names, self.house_names)
        self.frame0 = int(ingame_state.current_frame)
        self.last_frame = self.frame0
        self.last_frame_at = time.perf_counter()
        if warmup_s > 0:
            await asyncio.sleep(warmup_s)
        self.log(f"setup done: catalog={len(catalog)} starts={self.starts} "
                 f"reward_revision={self.reward.revision}")

    async def _maybe_map(self, force=False):
        now = time.perf_counter() - (self.t0 or time.perf_counter())
        if force or self.map_soa is None or now - self.last_map_at > 5.0:
            try:
                self.map_soa = await read_map_soa(self.client)
            except Exception as exc:  # noqa: BLE001
                self.log(f"map read failed: {type(exc).__name__}")
            self.last_map_at = now
        return self.map_soa

    async def _walk(self, state):
        now = time.perf_counter() - self.t0
        wall = schema.now_wall()
        row = obs.capture_observation(state, int(self.hero.self), self.type_names,
                                      self.house_names, self.map_soa, wall)
        row["t"] = round(now, 3)
        return row

    # ---- window control ----
    def window_state(self):
        if self.window is None or not winapi.is_window_visible(self.window["hwnd"]):
            # window handle may have changed on game restart / recreate
            w = winapi.pick_game_window(self.pid) if self.pid else None
            if w is not None:
                self.window = w
        hwnd = self.window["hwnd"] if self.window else None
        if hwnd is None or not winapi.is_window_visible(hwnd):
            return "hidden" if hwnd is not None else "gone"
        return "visible"

    def ensure_window(self, want):
        """want: 'visible' | 'hidden'. Returns the actual state after the call."""
        if self.window is None:
            self.window = winapi.pick_game_window(self.pid)
        if self.window is None:
            return "gone"
        hwnd = self.window["hwnd"]
        state = self.window_state()
        if want == "hidden":
            if state != "hidden":
                winapi.hide_window(hwnd)
                if winapi.is_window_visible(hwnd):
                    self.log("SW_HIDE did not hide the window")
                else:
                    self.log("window hidden with ShowWindow(SW_HIDE)")
        else:
            if state == "hidden":
                winapi.show_window(hwnd)
                self.show_repair_count += 1
                if winapi.is_window_visible(hwnd):
                    self.log("window restored with ShowWindow(SW_SHOW)")
        return self.window_state()

    # ---- one decision step ----
    async def decision_step(self, phase, idx):
        cycle_start = time.perf_counter()
        t_before = time.perf_counter() - self.t0
        want_hidden = phase["name"] == "hidden"
        # window state bookkeeping / repair
        try:
            wstate = self.window_state()
            if want_hidden and wstate != "hidden":
                self.rehide_count += 1
                self.ensure_window("hidden")
                wstate = self.window_state()
            self.window_samples[wstate if wstate in self.window_samples else "gone"] += 1
        except Exception as exc:  # noqa: BLE001
            self.log(f"window state check failed: {type(exc).__name__}: {exc}")
            wstate = "unknown"
        if self.proc is not None and self.proc.poll() is not None:
            self.exit_code = self.proc.returncode
            self.outcome = "crash"
            self.crash_detail = f"game process exited rc={self.exit_code}"
            return {"type": "step", "phase": phase["name"], "idx": idx,
                    "fatal": self.crash_detail, "read_ok": False}
        t_read = time.perf_counter()
        state = await read_state(self.client)
        read_ms = round((time.perf_counter() - t_read) * 1000.0, 2)
        if state is None:
            self.read_fail += 1
            self.last_frame_at = time.perf_counter()
            return {"type": "step", "phase": phase["name"], "idx": idx,
                    "read_ok": False, "read_ms": read_ms, "window": wstate,
                    "t": round(time.perf_counter() - self.t0, 3)}
        self.read_ok += 1
        self.read_latencies.append(read_ms)
        now = time.perf_counter() - self.t0
        frame = int(state.current_frame)
        self.hero = next((h for h in state.houses
                          if h.current_player and h.is_human_player), self.hero)
        kind = classify(state, self.hero)
        self.outcome = kind
        map_soa = await self._maybe_map()
        walk = await self._walk(state)
        self.map_soa = map_soa
        metrics, pos = extract_metrics(state, int(self.hero.self),
                                       self.type_names, self.house_names)
        delta = diff_metrics(self.prev_metrics, self.prev_pos, metrics, pos)
        # policy decision
        ctx = {"t": walk["t"], "frame": frame,
               "own_objects": walk["own_objects"],
               "visible_enemies": walk["visible_enemies"],
               "factories": walk["factories"],
               "observation": walk, "map": walk["map"],
               "hero": {"self": int(self.hero.self), "money": int(self.hero.money),
                        "name": self.hero.name},
               "type_catalog": self.catalog, "last_acks": self.last_acks,
               "public_start_cells": self.starts}
        try:
            intents = self.policy.step(ctx)
        except Exception as exc:  # noqa: BLE001
            self.log(f"policy error: {type(exc).__name__}: {exc}")
            intents = []
        actions = []
        for intent in intents:
            try:
                actions.extend(await self.exec_shim.execute(state, self.hero, intent))
            except Exception as exc:  # noqa: BLE001
                actions.append(schema.make_action(
                    intent.get("action", "?"), intent.get("object_addresses") or [],
                    error=f"exception: {type(exc).__name__}: {exc}"))
        executed = [a for a in actions if a.get("type") == "action"]
        invalid = 0
        attack_delta = 0
        for rec in actions:
            if rec.get("type") != "action":
                invalid += 1
                continue
            err = rec.get("error")
            if rec.get("action") == "ATTACK" and not err:
                attack_delta += 1
            if err:
                if BENIGN in err:
                    pass
                else:
                    invalid += 1
                    self.invalid_orders += 1
            else:
                self.orders_ok += 1
        self.last_acks = [{"action": r.get("action"),
                           "production_type": r.get("production_type"),
                           "error": r.get("error"), "t": walk["t"]}
                          for r in executed]
        self.last_actions = [{"action": r.get("action"), "error": r.get("error"),
                              "ack_ms": r.get("ack_ms"),
                              "target_type": r.get("target_type"),
                              "production_type": r.get("production_type")}
                             for r in executed]
        # reward (mirrors training semantics; rule policy, not the PPO model)
        reward, reward_parts_dict = self.reward.step(
            self.prev_walk, walk, kind, invalid=invalid, attack_delta=attack_delta)
        # stall detection on the game frame counter
        frame_delta = frame - (self.last_frame or frame)
        stall = frame_delta == 0
        if stall:
            self.stall_events += 1
        self.last_frame = frame
        self.last_frame_at = time.perf_counter()
        self.steps += 1
        record = {
            "type": "step", "phase": phase["name"], "idx": idx,
            "read_ok": True, "read_ms": read_ms, "window": wstate,
            "t": round(walk["t"], 3), "wall_epoch_ms": int(time.time() * 1000),
            "frame": frame, "frame_delta": frame_delta,
            "stage": ra2yr.LoadStage.Name(state.stage),
            "outcome": kind, "money": int(self.hero.money),
            "own": metrics["own"], "enemy": metrics["enemy"],
            "neutral": metrics["neutral"],
            "delta": delta, "priority": 0,
            "actions": self.last_actions, "invalid": invalid,
            "orders_ok": len(executed) - invalid,
            "stall": stall, "window_visible": wstate == "visible",
            "reward": reward_parts_dict, "reward_total_step": round(reward, 6),
            "reward_total": round(self.reward.total, 6),
        }
        self.prev_walk = walk
        self.prev_metrics, self.prev_pos = metrics, pos
        spent = time.perf_counter() - cycle_start
        record["cycle_ms"] = round(spent * 1000.0, 2)
        return record

    # ---- read-throughput micro benchmark ----
    async def rpc_benchmark(self, seconds=5.0):
        start = time.perf_counter()
        ok = fail = 0
        lats = []
        while time.perf_counter() - start < seconds:
            t = time.perf_counter()
            state = await read_state(self.client)
            lats.append((time.perf_counter() - t) * 1000.0)
            if state is None:
                fail += 1
            else:
                ok += 1
        dur = time.perf_counter() - start
        lats.sort()
        return {"seconds": round(dur, 3), "reads": ok + fail, "ok": ok, "fail": fail,
                "reads_per_s": round((ok + fail) / dur, 3),
                "ok_reads_per_s": round(ok / dur, 3),
                "latency_ms_p50": round(lats[len(lats) // 2], 3) if lats else None,
                "latency_ms_p95": round(lats[int(len(lats) * 0.95)], 3) if lats else None}

    # ---- cleanup ----
    async def close(self):
        # The bridge poll loop can block forever if the game already exited
        # (defeat -> spawner closes the process); never let cleanup hang.
        try:
            if self.client is not None:
                await asyncio.wait_for(self.client.stop(), 6)
        except Exception:  # noqa: BLE001
            pass
        if self.proc is not None and self.proc.poll() is None:
            import subprocess
            subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/F"],
                           capture_output=True, text=True, timeout=20)
            try:
                self.proc.wait(timeout=10)
            except Exception:  # noqa: BLE001
                pass
        if self.proc is not None:
            self.exit_code = self.proc.poll()

    def summary(self):
        lats = sorted(self.read_latencies)
        def pct(p):
            if not lats:
                return None
            return round(lats[min(len(lats) - 1, int(len(lats) * p))], 3)
        return {
            "label": self.label, "index": self.index,
            "game_dir": str(self.game_dir), "port": self.port, "pid": self.pid,
            "launch_to_window_s": self.launch_to_window_s,
            "launch_to_ingame_s": self.launch_to_ingame_s,
            "window": self.window and {k: self.window[k] for k in
                                       ("hwnd", "pid", "title", "class", "rect")},
            "frame0": self.frame0, "last_frame": self.last_frame,
            "steps": self.steps, "read_ok": self.read_ok, "read_fail": self.read_fail,
            "read_latency_ms_p50": pct(0.50), "read_latency_ms_p95": pct(0.95),
            "read_latency_ms_max": round(lats[-1], 3) if lats else None,
            "orders_ok": self.orders_ok, "invalid_orders": self.invalid_orders,
            "stall_events": self.stall_events,
            "rehide_count": self.rehide_count,
            "show_repair_count": self.show_repair_count,
            "window_samples": self.window_samples,
            "outcome": self.outcome, "exit_code": self.exit_code,
            "crash_detail": self.crash_detail,
            "reward_total": round(self.reward.total, 6),
            "reward_revision": self.reward.revision,
            "env_fingerprint": getattr(self, "env_fingerprint", None),
        }
