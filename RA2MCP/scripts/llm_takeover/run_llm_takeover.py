"""DSH LLM direct takeover driver for one isolated RA2YR game (TASK-20261102-032).

The driver NEVER chooses actions by itself. It only:

1. runs an offline preflight (one game process, Hard0 config, isolated copy, no
   existing game, authorization ledger unused, protected files unchanged);
2. launches ONE isolated test-game copy and connects to the existing bridge;
3. enables the bridge `singleStep` mode and probes on the real DLL that frames
   advance only when the driver explicitly advances them (no background advancer);
4. writes legal-visibility observation snapshots as JSON files and waits for the
   DSH LLM to write a decision file `io/decision-<n>.json`;
5. validates that decision against the latest legal state, sends the raw native
   commands through the existing bridge, and records ACK/native feedback plus the
   true next observation;
6. stops on engine win/defeat or the sim/wall budget and keeps all raw evidence.

It does not load any model, compute reward, train, or change the bridge / action /
reward contracts. It never launches the original E:/hj game.

Decision file schema (written by the DSH LLM):

{
  "decision": <n>,                  // must match the snapshot number
  "advance_frames": 300,            // sim frames to advance this decision (30..1800)
  "reason": "one-line plan",
  "commands": [
    {"op": "DEPLOY",  "units": [addr]},
    {"op": "MOVE",    "units": [addr, ...], "cell": [x, y]},
    {"op": "ATTACK",  "units": [addr, ...], "target": <visible enemy addr>},
    {"op": "ATTACK",  "units": [addr, ...], "cell": [x, y]},
    {"op": "STOP",    "units": [addr, ...]},
    {"op": "PRODUCE", "type": "Allied Power Plant", "count": 1},
    {"op": "PLACE",   "type": "Allied Power Plant", "building": <own limbo addr>,
                      "cell": [anchor_x, anchor_y]},
    {"op": "NOOP"}
  ]
}
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import hashlib
import json
import os
import pathlib
import socket
import subprocess
import sys
import time
import traceback

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "vendor" / "pyra2yr"))
sys.path.append(str(ROOT / ".venv-rl" / "Lib" / "site-packages"))

from pyra2yr.network import DualClient  # noqa: E402
from ra2yrproto import commands_game, commands_yr, ra2yr  # noqa: E402
from dsh_episode.runner import (  # noqa: E402
    launch_game, kill_game, kill_games, read_state, read_initial,
    public_start_cells, GameStateReadError,
)
from dsh_episode import lockfile  # noqa: E402
from dsh_episode import obs  # noqa: E402
from llm_takeover import rules as rules_mod  # noqa: E402

CELL = 256
DEFAULT_PORT = 14621
LEDGER = ROOT / "coordination/approvals/LLM_TAKEOVER_RUN_AUTHORIZATION.json"
PROTECTED = ROOT / "reports/brutal-teacher-2026-11-02/PROTECTED_BEFORE.json"
EXE_NAME = "gamemd-spawn-ra2yrcpp.exe"
STAGE_INGAME = 2
NON_COMBAT_HINTS = ("Construction Vehicle", "Miner", "Harvester", "Chrono Miner")


# ------------------------------------------------------------------ helpers ----
def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def sha256(path):
    path = pathlib.Path(path)
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path, obj):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def read_json(path):
    # utf-8-sig tolerates a BOM: a decision file written by PowerShell
    # Set-Content can carry one, which previously made every decision unreadable.
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8-sig"))


def jsonable(msg):
    """Small JSON-safe echo for evidence (never includes hidden data)."""
    try:
        from google.protobuf.json_format import MessageToDict
        return MessageToDict(msg, preserving_proto_field_name=True)
    except Exception:  # noqa: BLE001
        return None


def classify_error(error, skipped=False):
    if skipped:
        return "STALE_REJECTED"
    if not error:
        return "ACCEPTED"
    text = str(error).lower()
    if any(x in text for x in ("timeout", "timed out", "transport", "connection")):
        return "TRANSPORT_UNKNOWN"
    if "object not found" in text or "no longer" in text:
        return "STALE_REJECTED"
    return "OTHER_REJECTED"


# --------------------------------------------------------------- preflight ----
def preflight(out, game, port, authorization, max_sim_seconds):
    checks = {}
    checks["isolated_game"] = bool(game.parent == ROOT and game.name.startswith("test-game"))
    checks["bounded_sim"] = 0 < max_sim_seconds <= 1200
    checks["new_run"] = not (out / "one-time-launch.claim").exists() and \
        not (out / "episode/decisions.jsonl").exists()
    checks["disk_space"] = bool(__import__("shutil").disk_usage(ROOT).free > 3 * 1024 ** 3)
    for name in (EXE_NAME, "libra2yrcpp.dll", "spawn.ini", "spawnmap.ini", "ra2yrcpp.json"):
        checks[f"file:{name}"] = (game / name).is_file()
    try:
        cfg = read_json(game / "ra2yrcpp.json")
        checks["bridge_port"] = cfg.get("port") == port
        checks["bridge_single_step_config_field"] = "singleStep" in cfg
    except Exception:  # noqa: BLE001
        checks["bridge_port"] = False
        checks["bridge_single_step_config_field"] = False
    try:
        with socket.socket() as sock:
            sock.settimeout(1)
            sock.bind(("127.0.0.1", port))
        checks["port_bindable"] = True
    except OSError:
        checks["port_bindable"] = False
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "@(Get-Process -Name 'gamemd-spawn-ra2yrcpp','gamemd','yuri','Syringe'"
             " -ErrorAction SilentlyContinue).Count"],
            capture_output=True, text=True, timeout=20)
        checks["no_existing_game"] = proc.returncode == 0 and proc.stdout.strip() == "0"
    except Exception:  # noqa: BLE001
        checks["no_existing_game"] = False
    try:
        ini = __import__("configparser").ConfigParser(interpolation=None, strict=False)
        ini.read(game / "spawn.ini", encoding="utf-8")
        checks["config_Hard0"] = ini.getint("Settings", "AIDifficulty") == 0 and \
            ini.getint("HouseHandicaps", "Multi2") == 0
        checks["config_1v1"] = ini.getint("Settings", "PlayerCount") == 2 and \
            ini.getint("Settings", "AIPlayers") == 1
    except Exception:  # noqa: BLE001
        checks["config_Hard0"] = False
        checks["config_1v1"] = False
    entry = None
    try:
        ledger = read_json(LEDGER)
        entry = next((r for r in ledger["runs"] if r["run"] == authorization), None)
    except Exception:  # noqa: BLE001
        entry = None
    checks["authorization_unused"] = bool(
        entry and entry["status"] == "APPROVED_ONE_TIME" and entry["consumed_attempts"] == 0
        and pathlib.Path(entry["run_dir"]).resolve() == out
        and pathlib.Path(entry["game_dir"]).resolve() == game
        and max_sim_seconds <= entry["max_seconds"])
    try:
        held = lockfile.acquire(ROOT / "build/dsh-game.lock")
        lockfile.release(held)
        checks["shared_lock_writable"] = True
    except Exception as exc:  # noqa: BLE001
        checks["shared_lock_writable"] = False
        checks["lock_error"] = f"{type(exc).__name__}: {exc}"
    # bridge protocol symbols needed for raw control
    checks["protocol_symbols"] = all([
        hasattr(commands_game, "UnitOrder"), hasattr(commands_game, "ProduceOrder"),
        hasattr(commands_game, "PlaceBuilding"), hasattr(commands_yr, "PlaceQuery"),
        hasattr(commands_yr, "GetGameState"), hasattr(commands_yr, "InspectConfiguration"),
        hasattr(ra2yr, "UNIT_ACTION_MOVE"), hasattr(ra2yr, "UNIT_ACTION_ATTACK"),
        hasattr(ra2yr, "UNIT_ACTION_STOP"), hasattr(ra2yr, "UNIT_ACTION_DEPLOY"),
        hasattr(ra2yr, "PRODUCE_ACTION_BEGIN")])
    # protected files (178 in the Codex manifest) must be byte-identical
    protected_mismatch, protected_missing = [], []
    try:
        manifest = read_json(PROTECTED)
        for rel, expect in manifest.items():
            got = sha256(ROOT / rel.replace("\\", "/"))
            if got is None:
                protected_missing.append(rel)
            elif got != expect:
                protected_mismatch.append(rel)
    except Exception as exc:  # noqa: BLE001
        protected_mismatch.append(f"manifest_error: {type(exc).__name__}: {exc}")
    checks["protected_unchanged"] = not protected_mismatch and not protected_missing
    return {
        "checks": checks,
        "pass": all(checks.values()),
        "game_dir": str(game),
        "game_fingerprints": {
            "game_exe_sha256": sha256(game / EXE_NAME),
            "bridge_dll_sha256": sha256(game / "libra2yrcpp.dll"),
            "spawn_ini_sha256": sha256(game / "spawn.ini"),
            "spawnmap_sha256": sha256(game / "spawnmap.ini"),
        },
        "protected_files_checked": len(manifest) if 'manifest' in dir() else None,
        "protected_missing": protected_missing[:20],
        "protected_mismatch": protected_mismatch[:20],
        "game_launches": 0,
        "training_started": False,
    }


def consume_authorization(authorization, out):
    claim = out / "one-time-launch.claim"
    with claim.open("x", encoding="utf-8") as f:
        f.write(authorization)
    ledger = read_json(LEDGER)
    entry = next(r for r in ledger["runs"] if r["run"] == authorization)
    if entry["status"] != "APPROVED_ONE_TIME" or entry["consumed_attempts"]:
        raise RuntimeError("authorization already consumed")
    entry.update(status="CONSUMED_ATTEMPT", consumed_attempts=1, consumed_at=now_iso())
    write_json(LEDGER, ledger)


# ------------------------------------------------------------------ takeover ----
class Takeover:
    CONTROL_MODE = "dsh_llm_direct"

    def __init__(self, opts):
        self.opts = opts
        self.out = pathlib.Path(opts.out).resolve()
        self.game = pathlib.Path(opts.game_dir).resolve()
        self.port = int(opts.port)
        self.io = self.out / "io"
        self.episodes = self.out / "episode"
        self.episodes.mkdir(parents=True, exist_ok=True)
        self.io.mkdir(parents=True, exist_ok=True)
        self.result_path = self.out / "RESULT.json"
        self.progress_path = self.out / "PROGRESS.json"
        self.log_path = self.out / "driver.log"
        self.records = self.out / "episode" / "decisions.jsonl"
        self.rule_records = self.out / "episode" / "rule_firings.jsonl"
        self.rule_set = rules_mod.RuleSet([])
        self.rule_fires_total = 0
        self._rule_eval_tick = 0
        self.proc = None
        self.attach = bool(getattr(opts, "attach", False))
        self.external_pid = getattr(opts, "game_pid", None)
        self.start_decision = int(getattr(opts, "start_decision", 0) or 0)
        self.preset_episode_id = getattr(opts, "episode_id", None)
        self.runtime_mode = getattr(opts, "runtime_mode", "auto") or "auto"
        self.slowmo_fps = max(2.0, float(getattr(opts, "slowmo_fps", 10.0) or 10.0))
        self.client = None
        self.lock = None
        self.started_wall = None
        self.started_mono = None
        self.latest_state = None
        self.initial = None
        self.catalog = []
        self.catalog_by_name = {}
        self.type_names = {}
        self.house_names = {}
        self.hero_self = None
        self.public_starts = []
        self.mode = "unset"
        self.probe = {}
        self.gs_calls = 0
        self.gs_errors = 0
        self.gs_times = []
        self.frames_start = 0
        self.decision_index = 0
        self.native_outcomes = collections.Counter()
        self.end_kind = "RUNNING"
        self.end_detail = ""
        self.failure = None
        self._logf = None

    # ---- logging ----
    def log(self, msg):
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        if self._logf:
            self._logf.write(line + "\n")
            self._logf.flush()

    # ---- result/progress ----
    def write_result(self, **updates):
        doc = {}
        if self.result_path.exists():
            try:
                doc = read_json(self.result_path)
            except Exception:  # noqa: BLE001
                doc = {}
        doc.update(updates)
        doc.setdefault("authorization", self.opts.authorization)
        doc.setdefault("task_id", "TASK-20261102-032")
        doc.setdefault("control_mode", self.CONTROL_MODE)
        doc.setdefault("runtime_mode", self.runtime_mode)
        doc.setdefault("game_dir", str(self.game))
        doc.setdefault("training_started", False)
        doc.setdefault("student_model_loaded", False)
        doc.setdefault("no_model_no_reward_no_training", True)
        doc["updated_at"] = now_iso()
        write_json(self.result_path, doc)

    def write_progress(self):
        st = self.latest_state
        doc = {
            "decision_index": self.decision_index,
            "mode": self.mode,
            "frame": int(st.current_frame) if st is not None else None,
            "sim_seconds": round((int(st.current_frame) - self.frames_start) / 60.0, 1)
            if st is not None else None,
            "wall_seconds": round(time.monotonic() - self.started_mono, 1)
            if self.started_mono else 0,
            "remote_end_kind": self.end_kind,
            "native_outcomes": dict(self.native_outcomes),
            "gs_calls": self.gs_calls,
            "gs_stats": self.gs_stats(),
            "money": self._hero(st).money if st is not None and self._hero(st) else None,
            "updated_at": now_iso(),
        }
        write_json(self.progress_path, doc)

    # ---- bridge basics ----
    def _hero(self, st):
        if st is None:
            return None
        return next((h for h in st.houses if h.current_player and h.is_human_player), None)

    async def gs(self, strict=False):
        """Advance one frame in single-step mode and return the state snapshot."""
        t0 = time.monotonic()
        st = await read_state(self.client, strict=strict)
        if st is None:
            self.gs_errors += 1
            return None
        self.gs_errors = 0
        self.latest_state = st
        self.gs_calls += 1
        self.gs_times.append(round((time.monotonic() - t0) * 1000.0, 2))
        if len(self.gs_times) > 400:
            del self.gs_times[:200]
        return st

    def gs_stats(self):
        if not self.gs_times:
            return {"n": 0}
        xs = sorted(self.gs_times)
        return {"n": len(xs), "median_ms": xs[len(xs) // 2],
                "max_ms": xs[-1], "mean_ms": round(sum(xs) / len(xs), 2)}

    async def pump_until(self, task, timeout=8.0):
        """Release game frames until an RPC task completes (single-step safe)."""
        t0 = time.monotonic()
        idle = 0
        while not task.done():
            if time.monotonic() - t0 > timeout:
                return False
            st = self.latest_state
            f0 = int(st.current_frame) if st is not None else -1
            await self.gs()
            st2 = self.latest_state
            f1 = int(st2.current_frame) if st2 is not None else -1
            idle = 0 if f1 > f0 else idle + 1
            await asyncio.sleep(0.002 if idle < 6 else 0.008)
        return True

    async def exec_native(self, cmd, unpack_cls=None, max_frames=8, timeout=6.0):
        """Send one native command; in single-step mode release frames until ACK."""
        t0 = time.monotonic()
        task = asyncio.ensure_future(self.client.exec_command(cmd, timeout=timeout))
        done = await self.pump_until(task, timeout=8.0)
        if not done:
            task.cancel()
            return {"status": "TRANSPORT_TIMEOUT", "ack_ms": None,
                    "error": "exec queue timeout; delivery UNKNOWN, not resent"}
        try:
            r = task.result()
        except Exception as exc:  # noqa: BLE001
            return {"status": "TRANSPORT_ERROR", "ack_ms": None,
                    "error": f"{type(exc).__name__}: {exc}"}
        code = int(getattr(r, "result_code", 0) or 0)
        err = getattr(r, "error_message", "") or ""
        if code != 0:
            return {"status": "NATIVE_REJECTED", "ack_ms": round((time.monotonic() - t0) * 1000, 2),
                    "error": f"native_error: {err}"}
        echo = None
        if unpack_cls is not None:
            try:
                msg = unpack_cls()
                if r.result.Unpack(msg):
                    echo = jsonable(msg)
            except Exception:  # noqa: BLE001
                echo = None
        return {"status": "ACCEPTED", "ack_ms": round((time.monotonic() - t0) * 1000, 2),
                "error": None, "native_echo": echo}

    async def read_config(self):
        r = await self._plain_cmd(commands_yr.InspectConfiguration(), commands_yr.InspectConfiguration)
        return r

    async def _plain_cmd(self, cmd, unpack_cls):
        task = asyncio.ensure_future(self.client.exec_command(cmd, timeout=6.0))
        done = await self.pump_until(task, timeout=10.0)
        if not done:
            task.cancel()
            raise RuntimeError(f"{unpack_cls.__name__} timeout")
        r = task.result()
        if getattr(r, "result_code", 0):
            raise RuntimeError(f"{unpack_cls.__name__} native error: {getattr(r, 'error_message', '')}")
        msg = unpack_cls()
        if not r.result.Unpack(msg):
            raise RuntimeError(f"{unpack_cls.__name__} unpack failed")
        return msg

    async def set_single_step(self, value):
        cfg = await self.read_config()
        cfg.config.single_step = bool(value)
        await self._plain_cmd(
            commands_yr.InspectConfiguration(config=cfg.config, update=True),
            commands_yr.InspectConfiguration)
        verify = await self.read_config()
        return bool(verify.config.single_step)

    # ---- startup ----
    async def connect_ingame(self, launch_timeout=90):
        deadline = time.monotonic() + launch_timeout
        client = None
        while time.monotonic() < deadline:
            if self.proc is not None and self.proc.poll() is not None:
                raise GameStateReadError(f"game exited during launch rc={self.proc.returncode}")
            if client is None:
                client = DualClient("127.0.0.1", self.port, timeout=2)
                client.connect()
                await asyncio.sleep(2.0)
            st = await read_state(client)
            if st is not None and int(st.stage) == STAGE_INGAME and len(st.objects) > 0:
                self.client = client
                self.log(f"INGAME frame={st.current_frame} objects={len(st.objects)}")
                return st
            await asyncio.sleep(2.0)
        raise GameStateReadError("bridge/INGAME timeout")

    async def connect_existing(self, timeout=60):
        """Attach to the already-running single game (controller restart, no launch)."""
        deadline = time.monotonic() + timeout
        client = None
        while time.monotonic() < deadline:
            if self.external_pid and not lockfile._pid_alive(self.external_pid):
                raise GameStateReadError(f"attached game pid {self.external_pid} is not alive")
            if client is None:
                client = DualClient("127.0.0.1", self.port, timeout=2)
                try:
                    client.connect()
                except Exception:  # noqa: BLE001
                    client = None
                    await asyncio.sleep(1.0)
                    continue
                await asyncio.sleep(1.0)
            st = await read_state(client)
            if st is not None and int(st.stage) == STAGE_INGAME and len(st.objects) > 0:
                self.client = client
                self.log(f"attach INGAME frame={st.current_frame} objects={len(st.objects)} "
                         f"pid={self.external_pid}")
                return st
            await asyncio.sleep(1.0)
        raise GameStateReadError("attach to existing game failed")

    def build_catalog(self, initial):
        cols = collections.Counter()
        catalog = []

        def enum_name(enum_cls, value):
            try:
                return enum_cls.Name(value)
            except Exception:  # noqa: BLE001
                return str(int(value))

        for t in initial.object_types:
            item = {
                "name": t.name,
                "ptr": int(t.pointer_self),
                "cost": int(t.cost),
                "abstract": enum_name(ra2yr.AbstractType, t.type),
                "build_category": enum_name(ra2yr.BuildCategory, t.build_category),
                "tech_level": int(t.tech_level),
                "strength": int(t.strength),
                "power_bonus": int(t.power_bonus),
                "power_drain": int(t.power_drain),
                "is_base_defense": bool(t.is_base_defense),
                "prerequisite_ptrs": [int(p) for p in t.prerequisites],
            }
            catalog.append(item)
            cols[item["abstract"]] += 1
        by_ptr = {t["ptr"]: t["name"] for t in catalog}
        for item in catalog:
            item["prerequisites"] = [by_ptr.get(p, str(p)) for p in item.pop("prerequisite_ptrs")]
        order = {"ABSTRACT_TYPE_BUILDING": 0, "ABSTRACT_TYPE_INFANTRY": 1,
                 "ABSTRACT_TYPE_UNIT": 2, "ABSTRACT_TYPE_AIRCRAFT": 3}
        catalog.sort(key=lambda x: (order.get(x["abstract"], 9), x["cost"], x["name"]))
        self.catalog = catalog
        self.catalog_by_name = {t["name"]: t for t in catalog}
        write_json(self.io / "catalog.json", {
            "tech_level": int(initial.tech_level),
            "counts": dict(cols),
            "types": catalog,
        })
        self.log(f"catalog: {len(catalog)} types {dict(cols)}")

    # ---- single-step probe (real DLL) ----
    async def probe_single_step(self):
        evidence = {"started_at": now_iso()}
        try:
            before = await self.read_config()
            evidence["config_before"] = {"single_step": bool(before.config.single_step)}
            enabled = await self.set_single_step(True)
            evidence["single_step_enabled"] = enabled
            if not enabled:
                self.mode = "wall_clock"
                evidence["verdict"] = "SINGLE_STEP_NOT_ENABLED"
                return evidence
            s1 = await self.gs(strict=True)
            f1 = int(s1.current_frame)
            quiet_s = 2.0
            await asyncio.sleep(quiet_s)
            s2 = await self.gs(strict=True)
            f2 = int(s2.current_frame)
            await asyncio.sleep(quiet_s)
            s3 = await self.gs(strict=True)
            f3 = int(s3.current_frame)
            evidence.update({
                "quiet_seconds": quiet_s,
                "frames": [f1, f2, f3],
                "delta_quiet_1": f2 - f1,
                "delta_quiet_2": f3 - f2,
                "gs_timing": self.gs_stats(),
                "note": "in single-step mode one GetGameState call releases exactly one frame; "
                        "no other frame advance is allowed while the driver waits",
            })
            ok = 1 <= (f2 - f1) <= 2 and 1 <= (f3 - f2) <= 2
            evidence["verdict"] = "SINGLE_STEP_VERIFIED" if ok else "BACKGROUND_FRAME_ADVANCE_DETECTED"
            self.mode = "single_step" if ok else "wall_clock"
            if not ok:
                await self.set_single_step(False)
                evidence["fallback"] = "wall_clock"
            return evidence
        except Exception as exc:  # noqa: BLE001
            evidence["verdict"] = "PROBE_FAILED"
            evidence["error"] = f"{type(exc).__name__}: {exc}"
            self.mode = "wall_clock"
            try:
                await self.set_single_step(False)
            except Exception:  # noqa: BLE001
                pass
            return evidence
        finally:
            evidence["finished_at"] = now_iso()
            write_json(self.out / "evidence" / "single_step_probe.json", evidence)
            self.log(f"single-step probe: {evidence.get('verdict')} mode={self.mode}")

    # ---- legal-state views ----
    def legal_view(self, st):
        if st is None:
            return None
        hero = self._hero(st)
        if hero is None:
            return None
        houses = {int(h.self): h.name for h in st.houses}
        legal = obs.capture_observation(st, int(hero.self), self.type_names, houses, None,
                                        time.strftime("%H:%M:%S"))
        # Rules need per-type max HP for hp-threshold conditions; the catalog was
        # read from the engine's own type table (no hidden information involved).
        for o in legal.get("own_objects") or []:
            t = self.catalog_by_name.get(o.get("type_name"))
            if t is not None:
                o["max_hp"] = int(t.get("strength") or 0)
        return legal

    @staticmethod
    def is_mobile(rec):
        if rec.get("abstract") == "ABSTRACT_TYPE_BUILDING":
            return False
        if rec.get("in_limbo") or not rec.get("on_map") or rec.get("health", 0) <= 0:
            return False
        return True

    def build_snapshot(self, st, n):
        legal = self.legal_view(st)
        if legal is None:
            raise GameStateReadError("legal view unavailable")
        own, done_buildings = [], []
        for o in legal["own_objects"]:
            compact = {
                "addr": o["addr"], "type": o["type_name"], "hp": o["health"],
                "cell": o["cell"], "xyz": o["xyz"], "mission": o["mission"],
                "in_limbo": o["in_limbo"], "on_map": o["on_map"],
            }
            own.append(compact)
            if o["in_limbo"] and o["abstract"] == "ABSTRACT_TYPE_BUILDING":
                done_buildings.append({"addr": o["addr"], "type": o["type_name"],
                                       "cell": o["cell"]})
        enemies = [{
            "addr": o["addr"], "type": o["type_name"], "hp": o["health"],
            "cell": o["cell"], "mission": o["mission"], "abstract": o["abstract"],
        } for o in legal["visible_enemies"]]
        hero = self._hero(st)
        own_counts = collections.Counter(o["type"] for o in own)
        enemy_counts = collections.Counter(o["type"] for o in enemies)
        build_state = [{"name": b.name, "percent": int(b.percent_done)}
                       for b in getattr(hero, "build_state", [])]
        factories = []
        for f in st.factories:
            if int(f.owner) != int(hero.self):
                continue
            factories.append({
                "object_addr": int(f.object), "progress_timer": int(f.progress_timer),
                "completed": bool(f.completed), "on_hold": bool(f.on_hold),
                "queued": len(f.queued_objects),
            })
        enemy_houses = [{
            "name": h.name, "faction": h.faction, "defeated": bool(h.defeated),
            "is_loser": bool(h.is_loser), "is_winner": bool(h.is_winner),
        } for h in st.houses if not h.is_human_player and h.name == "Computer"]
        return {
            "decision": n,
            "frame": int(st.current_frame),
            "elapsed_sim_s": round((int(st.current_frame) - self.frames_start) / 60.0, 1),
            "elapsed_wall_s": round(time.monotonic() - self.started_mono, 1) if self.started_mono else 0,
            "mode": self.mode,
            "money": int(hero.money),
            "power": {"output": int(getattr(hero, "power_output", 0)),
                      "drain": int(getattr(hero, "power_drain", 0))},
            "own": own,
            "own_counts": dict(own_counts),
            "done_buildings": done_buildings,
            "build_state": build_state,
            "factories": factories,
            "enemies_visible": enemies,
            "enemy_counts": dict(enemy_counts),
            "hidden_enemy_count": legal.get("hidden_enemy_count"),
            "enemy_houses": enemy_houses,
            "public_start_cells": self.public_starts,
            "rules_active": self.rule_set.describe(),
            "rules_fired_total": self.rule_fires_total,
            "protocol": {
                "snapshot": f"io/state-{n}.json",
                "write_decision_to": f"io/decision-{n}.json",
                "fields": "decision=?, advance_frames(30..1800), reason, commands[]",
                "ops": ["DEPLOY", "MOVE", "ATTACK", "STOP", "PRODUCE", "PLACE", "NOOP"],
                "coordinates": "cell = [x, y] in map cells",
                "abort": "write file io/ABORT to stop the run",
            },
        }

    # ---- decision validation/build ----
    def _own_mobile(self, st):
        legal = self.legal_view(st)
        return {o["addr"]: o for o in legal["own_objects"] if self.is_mobile(o)}

    def _own_limbo_buildings(self, st):
        legal = self.legal_view(st)
        return {o["addr"]: o for o in legal["own_objects"]
                if o["abstract"] == "ABSTRACT_TYPE_BUILDING" and o["in_limbo"]}

    def _visible_enemy(self, st):
        legal = self.legal_view(st)
        return {o["addr"]: o for o in legal["visible_enemies"]}

    @staticmethod
    def _cell_to_xyz(cell):
        return int(cell[0]) * CELL + CELL // 2, int(cell[1]) * CELL + CELL // 2

    def _resolve_units(self, raw_units, mobile, errors, op):
        addrs = [int(a) for a in (raw_units or [])]
        if not addrs:
            errors.append("no units")
        good = []
        for addr in addrs:
            rec = mobile.get(addr)
            if rec is None:
                errors.append(f"unit {addr} not an own mobile object in latest legal state")
            else:
                good.append(addr)
        return good

    def _validate(self, raw):
        """Return (kind, payload, errors) for one raw command dict."""
        op = str(raw.get("op", "")).upper()
        errors = []
        if op == "NOOP":
            return "NOOP", {}, errors
        st = self.latest_state
        mobile = self._own_mobile(st)
        if op in ("DEPLOY", "MOVE", "ATTACK", "STOP"):
            units = self._resolve_units(raw.get("units"), mobile, errors, op)
            if errors:
                return op, {"units": units}, errors
            if op == "DEPLOY":
                return op, {"units": units}, errors
            if op == "MOVE":
                cell = raw.get("cell")
                if not (isinstance(cell, (list, tuple)) and len(cell) == 2):
                    errors.append("MOVE needs cell=[x,y]")
                    return op, {"units": units}, errors
                return op, {"units": units, "cell": [int(cell[0]), int(cell[1])]}, errors
            if op == "STOP":
                return op, {"units": units}, errors
            visible = self._visible_enemy(st)
            target = raw.get("target")
            cell = raw.get("cell")
            if target is not None:
                trec = visible.get(int(target))
                if trec is None:
                    errors.append(f"ATTACK target {target} is not in the legal visible-enemy list")
                    return op, {"units": units}, errors
                return op, {"units": units, "target": int(target),
                            "cell": trec["cell"]}, errors
            if isinstance(cell, (list, tuple)) and len(cell) == 2:
                return op, {"units": units, "cell": [int(cell[0]), int(cell[1])]}, errors
            errors.append("ATTACK needs visible target addr or cell=[x,y]")
            return op, {"units": units}, errors
        if op == "PRODUCE":
            name = str(raw.get("type", ""))
            t = self.catalog_by_name.get(name)
            if t is None:
                errors.append(f"unknown production type {name!r}")
                return op, {}, errors
            count = max(1, min(int(raw.get("count", 1)), 5))
            return op, {"type": t, "count": count}, errors
        if op == "PLACE":
            name = str(raw.get("type", ""))
            addr = raw.get("building")
            cell = raw.get("cell")
            if not (isinstance(cell, (list, tuple)) and len(cell) == 2):
                errors.append("PLACE needs cell=[anchor_x,anchor_y]")
                return op, {}, errors
            limbo = self._own_limbo_buildings(st)
            obj = limbo.get(int(addr)) if addr is not None else None
            if obj is None:
                errors.append(f"PLACE building {addr} is not an own finished building in limbo")
                return op, {}, errors
            if name and obj["type_name"] != name:
                errors.append(f"PLACE building type {obj['type_name']} != {name}")
                return op, {}, errors
            return op, {"building": int(addr), "cell": [int(cell[0]), int(cell[1])],
                        "type_name": obj["type_name"]}, errors
        errors.append(f"unknown op {op!r}")
        return op, {}, errors

    # ---- native command senders ----
    async def send_unit_order(self, op, payload):
        action = {
            "DEPLOY": ra2yr.UNIT_ACTION_DEPLOY,
            "MOVE": ra2yr.UNIT_ACTION_MOVE,
            "ATTACK": ra2yr.UNIT_ACTION_ATTACK,
            "STOP": ra2yr.UNIT_ACTION_STOP,
        }[op]
        q = commands_game.UnitOrder(object_addresses=list(payload["units"]), action=action)
        if op in ("MOVE", "ATTACK") and payload.get("cell"):
            x, y = self._cell_to_xyz(payload["cell"])
            q.coordinates.x, q.coordinates.y = x, y
        if op == "ATTACK" and payload.get("target"):
            q.target_object = int(payload["target"])
        return await self.exec_native(q, commands_game.UnitOrder)

    async def send_produce(self, payload):
        t = payload["type"]
        tc = next((x for x in self.initial.object_types if int(x.pointer_self) == int(t["ptr"])), None)
        if tc is None:
            return {"status": "SKIPPED_INVALID", "error": "ObjectTypeClass not in initial catalog"}
        results = []
        for _ in range(payload["count"]):
            results.append(await self.exec_native(
                commands_game.ProduceOrder(object_type=tc, action=ra2yr.PRODUCE_ACTION_BEGIN),
                commands_game.ProduceOrder))
        return results[-1] if results else {"status": "SKIPPED_INVALID", "error": "no produce"}

    async def place_building(self, payload):
        st = self.latest_state
        addr = int(payload["building"])
        obj = next((o for o in st.objects
                    if int(o.pointer_self) == addr and int(o.pointer_house) == int(self.hero_self)
                    and bool(o.in_limbo) and int(o.health) > 0), None)
        if obj is None:
            return {"status": "SKIPPED_INVALID", "error": "building object disappeared"}
        anchor = payload["cell"]
        candidates = [anchor]
        for r in (2, 3, 4, 5, 6, 8, 10):
            for dx, dy in ((r, 0), (-r, 0), (0, r), (0, -r),
                           (r, r), (-r, r), (r, -r), (-r, -r)):
                candidates.append([anchor[0] + dx, anchor[1] + dy])
        pq = commands_yr.PlaceQuery(type_class=int(obj.pointer_technotypeclass),
                                    house_class=int(self.hero_self))
        for cell in candidates:
            x, y = self._cell_to_xyz(cell)
            c = pq.coordinates.add()
            c.x, c.y = x, y
        r = await self.exec_native(pq, commands_yr.PlaceQuery)
        if r["status"] != "ACCEPTED":
            return {"status": r["status"], "error": f"place_query: {r.get('error')}",
                    "candidates": len(candidates)}
        valid = []
        echo = r.get("native_echo") or {}
        for c in echo.get("coordinates") or []:
            valid.append((int(c.get("x", 0)), int(c.get("y", 0))))
        if not valid:
            return {"status": "REJECTED_NO_VALID_CELL", "candidates": len(candidates)}
        ax, ay = self._cell_to_xyz(anchor)
        best = min(valid, key=lambda xy: (xy[0] - ax) ** 2 + (xy[1] - ay) ** 2)
        pb = commands_game.PlaceBuilding()
        pb.building.CopyFrom(obj)
        pb.coordinates.x, pb.coordinates.y = int(best[0]), int(best[1])
        out = await self.exec_native(pb, commands_game.PlaceBuilding)
        out["valid_cells"] = len(valid)
        out["placed_xyz"] = [int(best[0]), int(best[1])]
        out["placed_cell"] = [best[0] // CELL, best[1] // CELL]
        return out

    # ---- decision execution ----
    async def advance_one_frame(self):
        """Release exactly one frame in single-step/slowmo mode and return the new state."""
        last = int(self.latest_state.current_frame) if self.latest_state is not None else -1
        for _ in range(10):
            st = await self.gs()
            if st is not None and int(st.current_frame) > last:
                return st
            await asyncio.sleep(0.003)
        return self.latest_state

    async def paced_tick(self):
        """Release one frame and sleep so the game runs at slowmo_fps."""
        t0 = time.monotonic()
        await self.advance_one_frame()
        self._rule_eval_tick += 1
        if self._rule_eval_tick % 2 == 0 and self.rule_set.rules:
            try:
                await self.evaluate_and_fire_rules(max_fires=1)
            except GameStateReadError:
                raise
            except Exception as exc:  # noqa: BLE001 - a rule bug must not kill the run
                self.log(f"rule evaluation error: {type(exc).__name__}: {exc}")
        rest = (1.0 / self.slowmo_fps) - (time.monotonic() - t0)
        if rest > 0:
            await asyncio.sleep(rest)
        return self.latest_state

    async def _execute_command_raw(self, raw):
        """Validate and execute one raw command dict; returns (record, fatal)."""
        op, payload, errors = self._validate(raw)
        rec = {"raw": raw, "op": op, "validated": payload, "errors": errors}
        if errors:
            rec.update(status="SKIPPED_INVALID")
            self.native_outcomes["SKIPPED_INVALID"] += 1
            return rec, False
        if op == "NOOP":
            rec.update(status="NOOP")
            return rec, False
        if op in ("DEPLOY", "MOVE", "ATTACK", "STOP"):
            res = await self.send_unit_order(op, payload)
        elif op == "PRODUCE":
            res = await self.send_produce(payload)
        elif op == "PLACE":
            res = await self.place_building(payload)
        else:
            res = {"status": "SKIPPED_INVALID", "error": "unsupported"}
        rec.update(res)
        self.native_outcomes[res["status"]] += 1
        fatal = res["status"] in ("TRANSPORT_TIMEOUT", "TRANSPORT_ERROR")
        if fatal:
            rec["fatal"] = True
        return rec, fatal

    async def evaluate_and_fire_rules(self, max_fires=2):
        """Evaluate the LLM-authored rules and execute the ones that trigger.

        The rules came from the LLM's decision files; the driver only evaluates
        the conditions every frame-scale tick, resolves selectors and sends the
        already-authorized commands through the same validated path.
        """
        st = self.latest_state
        if st is None or not self.rule_set.rules:
            return 0
        legal = self.legal_view(st)
        if legal is None:
            return 0
        frame = int(st.current_frame)
        due = self.rule_set.due(legal, frame, max_fires=max_fires)
        fired = 0
        for rule, cmds, errors in due:
            outcomes, fatal = [], False
            for raw in cmds:
                rec, fatal_one = await self._execute_command_raw(raw)
                outcomes.append(rec)
                fatal = fatal or fatal_one
                if fatal:
                    break
            fired += 1
            self.rule_fires_total += 1
            row = {
                "schema": "ra2.llm-takeover-rule-firing/1",
                "episode_id": self.episode_id,
                "rule_id": rule.get("id"),
                "author": "llm",
                "executor": "driver_trigger",
                "decision_number": self.decision_index,
                "frame": frame,
                "when": rule.get("when"),
                "then": rule.get("then"),
                "resolved_commands": cmds,
                "compile_errors": errors,
                "command_outcomes": outcomes,
                "fatal": fatal,
            }
            try:
                with self.rule_records.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
            except Exception:  # noqa: BLE001
                pass
            self.log(f"RULE FIRED {rule.get('id')!r} frame={frame} cmds={len(cmds)}")
            if fatal:
                self.failure = f"transport failure in rule {rule.get('id')!r}"
                return fired
        return fired

    async def advance(self, frames):
        """Advance `frames` sim frames.

        In single-step mode a GetGameState call only advances a frame when the
        game is currently paused at a loop boundary; rapid calls while a released
        frame is still executing return the same snapshot. So we poll adaptively
        until the frame counter reaches the target.
        """
        st = self.latest_state
        start = int(st.current_frame)
        target = start + int(frames)
        gs0 = self.gs_calls
        last = start
        t_last = time.monotonic()
        interval = getattr(self, "_frame_interval", 0.0167)
        idle = 0
        deadline = time.monotonic() + 20.0 + frames / 20.0
        if self.mode == "slowmo":
            while True:
                cur = int(self.latest_state.current_frame) if self.latest_state is not None else -1
                if cur >= target:
                    break
                if time.monotonic() > deadline:
                    self.log(f"advance deadline: frame {cur} of target {target}")
                    break
                # fast-forward: release frames back-to-back (window stays alive
                # because the game thread keeps executing frames), then go back to
                # paced slow motion while waiting for the next LLM decision.
                await self.advance_one_frame()
                self._rule_eval_tick += 1
                if self._rule_eval_tick % 3 == 0 and self.rule_set.rules:
                    try:
                        await self.evaluate_and_fire_rules(max_fires=1)
                    except GameStateReadError:
                        raise
                    except Exception as exc:  # noqa: BLE001
                        self.log(f"rule evaluation error: {type(exc).__name__}: {exc}")
        elif self.mode == "single_step":
            while True:
                cur = int(self.latest_state.current_frame) if self.latest_state is not None else -1
                if cur >= target:
                    break
                if time.monotonic() > deadline:
                    self.log(f"advance deadline: frame {cur} of target {target}")
                    break
                st2 = await self.gs()
                if st2 is None:
                    if self.gs_errors >= 3:
                        raise GameStateReadError("3 consecutive state read failures")
                    await asyncio.sleep(0.01)
                    continue
                f = int(st2.current_frame)
                if f > last:
                    now = time.monotonic()
                    interval = max(0.002, min(0.5, (now - t_last) / max(1, f - last)))
                    self._frame_interval = interval
                    t_last, last, idle = now, f, 0
                    self._rule_eval_tick += 1
                    if self._rule_eval_tick % 3 == 0 and self.rule_set.rules:
                        try:
                            await self.evaluate_and_fire_rules(max_fires=1)
                        except GameStateReadError:
                            raise
                        except Exception as exc:  # noqa: BLE001
                            self.log(f"rule evaluation error: {type(exc).__name__}: {exc}")
                else:
                    idle += 1
                    await asyncio.sleep(max(0.001, min(interval * 0.6, 0.02)))
        else:
            deadline = time.monotonic() + 180.0
            while time.monotonic() < deadline:
                nxt = await self.gs()
                if nxt is None:
                    if self.gs_errors >= 6:
                        raise GameStateReadError("6 consecutive state read failures")
                elif int(nxt.current_frame) >= target:
                    break
                elif self.resolve_terminal(nxt) not in ("RUNNING", "STATE_UNAVAILABLE"):
                    break
                if self.rule_set.rules:
                    try:
                        await self.evaluate_and_fire_rules(max_fires=1)
                    except GameStateReadError:
                        raise
                    except Exception as exc:  # noqa: BLE001
                        self.log(f"rule evaluation error: {type(exc).__name__}: {exc}")
                await asyncio.sleep(0.5)
        return self.gs_calls - gs0

    async def execute_decision(self, n, dec):
        fresh = await self.gs()
        if fresh is not None:
            self.latest_state = fresh
        # LLM-authored conditional orders: the list replaces the active set and
        # persists across decisions until replaced or cleared.
        if dec.get("rules_clear"):
            self.rule_set = rules_mod.RuleSet([])
            self.log("rules cleared by decision")
        if isinstance(dec.get("rules"), list):
            self.rule_set = rules_mod.RuleSet(dec["rules"])
            self.log(f"rules set: {len(self.rule_set.rules)}")
        st_before = self.latest_state
        frames_before = int(st_before.current_frame)
        snap_before = self.build_snapshot(st_before, n)
        advance_frames = max(30, min(int(dec.get("advance_frames", 300) or 300), 1800))
        outcomes, fatal = [], False
        for raw in (dec.get("commands") or []):
            rec, fatal_one = await self._execute_command_raw(raw)
            outcomes.append(rec)
            if fatal_one:
                fatal = True
                self.failure = f"transport failure at decision {n}: {rec.get('error')}"
                break
        calls = 0
        adv_t0 = time.monotonic()
        try:
            calls = await self.advance(advance_frames)
        except GameStateReadError as exc:
            self.failure = f"state read failure while advancing: {exc}"
            fatal = True
        adv_ms = round((time.monotonic() - adv_t0) * 1000.0, 2)
        st_after = self.latest_state
        frames_after = int(st_after.current_frame) if st_after is not None else frames_before
        terminal = self.resolve_terminal(st_after)
        if terminal != "RUNNING":
            self.end_kind = terminal
            self.end_detail = f"engine flags at decision {n}"
        snap_after = self.build_snapshot(st_after, n + 1) if st_after is not None else None
        record = {
            "schema": "ra2.llm-takeover-decision/1",
            "episode_id": self.episode_id,
            "decision_id": f"{self.episode_id}:llm:{n:05d}",
            "source": "dsh_llm_direct",
            "control_mode": self.CONTROL_MODE,
            "llm": {"identity": "DSH session LLM (DeepSeek Harness); exact model version UNKNOWN",
                    "input": "legal-visibility snapshot only; no hidden enemy data",
                    "decision_file": f"io/decision-{n}.json"},
            "decision_number": n,
            "reason": dec.get("reason"),
            "observation_frame": frames_before,
            "next_frame": frames_after,
            "observation": snap_before,
            "next_observation": snap_after,
            "advance_frames_requested": advance_frames,
            "frames_advanced": frames_after - frames_before,
            "advance_wall_ms": adv_ms,
            "gs_calls": calls,
            "commands": outcomes,
            "rules_active": self.rule_set.describe(),
            "rules_fired_total": self.rule_fires_total,
            "fatal": fatal,
            "terminal": terminal,
            "formal_training_eligible": False,
            "reward": None,
            "reward_source": "NOT_COMPUTED_LLM_RAW_DEMONSTRATION",
            "applied_status": "UNKNOWN",
            "effect_status": "UNKNOWN",
        }
        with self.records.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        write_json(self.io / f"result-{n}.json", {
            "decision": n, "status": "FATAL" if fatal else "OK",
            "frame_before": frames_before, "frame_after": frames_after,
            "frames_advanced": frames_after - frames_before,
            "gs_calls": calls, "terminal": terminal,
            "commands": [{k: v for k, v in c.items() if k != "raw"} for c in outcomes],
            "fatal": fatal, "failure": self.failure,
        })
        self.decision_index = n + 1
        self.write_progress()
        return record

    # ---- terminal ----
    def resolve_terminal(self, st):
        if st is None:
            return "STATE_UNAVAILABLE"
        if self.proc is not None and self.proc.poll() is not None:
            return "PROCESS_EXITED"
        if self.proc is None and self.external_pid:
            if not lockfile._pid_alive(self.external_pid):
                return "PROCESS_EXITED"
        hero = self._hero(st)
        if hero is None:
            return "NO_HERO"
        enemies = [h for h in st.houses if not h.is_human_player and h.name == "Computer"]
        enemies_all_defeated = bool(enemies) and all(bool(h.defeated) for h in enemies)
        if bool(getattr(hero, "is_winner", False)) or enemies_all_defeated:
            return "WIN"
        if bool(hero.defeated) or bool(getattr(hero, "is_loser", False)):
            return "DEFEAT"
        if int(st.stage) != STAGE_INGAME:
            name = ra2yr.LoadStage.Name(st.stage)
            return "EXIT_" + name
        return "RUNNING"

    # ---- file protocol ----
    async def wait_decision(self, n, per_decision_timeout=None):
        if per_decision_timeout is None:
            per_decision_timeout = float(getattr(self.opts, "decision_timeout", 900.0) or 900.0)
        path = self.io / f"decision-{n}.json"
        abort = self.io / "ABORT"
        t0 = time.monotonic()
        last_refresh = 0.0
        while True:
            if abort.exists():
                return {"abort": True, "reason": "IO_ABORT_FILE"}
            if path.exists():
                try:
                    dec = read_json(path)
                    if int(dec.get("decision", n)) != n:
                        self.log(f"decision {n}: wrong decision number in file; waiting for overwrite")
                    else:
                        return dec
                except Exception as exc:  # noqa: BLE001
                    self.log(f"decision {n}: unreadable JSON ({exc}); waiting for overwrite")
            if self.proc is not None and self.proc.poll() is not None:
                return {"abort": True, "reason": "PROCESS_EXITED"}
            if self.mode in ("realtime", "slowmo") and time.monotonic() - last_refresh >= 1.0:
                last_refresh = time.monotonic()
                st_now = self.latest_state if self.mode == "slowmo" else await self.gs()
                if st_now is not None:
                    term_now = self.resolve_terminal(st_now)
                    if term_now not in ("RUNNING", "STATE_UNAVAILABLE"):
                        return {"abort": True, "reason": "ENDED_" + term_now}
                    sim_now = (int(st_now.current_frame) - self.frames_start) / 60.0
                    if sim_now >= self.opts.max_sim_seconds:
                        return {"abort": True, "reason": "TIMEOUT_SIMULATION_BUDGET"}
                    snap = self.build_snapshot(st_now, n)
                    write_json(self.io / f"state-{n}.json", snap)
                    write_json(self.io / "LATEST.json", {
                        "decision": n, "state": str(self.io / f"state-{n}.json"),
                        "frame": snap["frame"], "written_at": now_iso(),
                        "note": f"{self.mode} mode: snapshot refreshed about once per second"})
                    if self.mode == "realtime" and self.rule_set.rules:
                        try:
                            await self.evaluate_and_fire_rules(max_fires=1)
                        except GameStateReadError:
                            raise
                        except Exception as exc:  # noqa: BLE001
                            self.log(f"rule evaluation error: {type(exc).__name__}: {exc}")
            if time.monotonic() - t0 > per_decision_timeout:
                return {"abort": True, "reason": "DECISION_TIMEOUT"}
            wall = time.monotonic() - self.started_mono
            if wall > self.opts.max_wall_seconds:
                return {"abort": True, "reason": "WALL_BUDGET"}
            if self.mode == "slowmo":
                await self.paced_tick()
            else:
                await asyncio.sleep(0.2)

    async def run_loop(self, start=0):
        st = self.latest_state
        n = int(start)
        while True:
            self.write_progress()
            if st is None:
                st = await self.gs()
                if st is None:
                    raise GameStateReadError("no state available")
            terminal = self.resolve_terminal(st)
            if terminal != "RUNNING":
                self.end_kind = terminal
                self.end_detail = "engine terminal before next decision"
                break
            sim_s = (int(st.current_frame) - self.frames_start) / 60.0
            wall = time.monotonic() - self.started_mono
            if sim_s >= self.opts.max_sim_seconds:
                self.end_kind = "TIMEOUT_SIMULATION_BUDGET"
                break
            if wall >= self.opts.max_wall_seconds:
                self.end_kind = "TIMEOUT_WALL_BUDGET"
                break
            snap = self.build_snapshot(st, n)
            write_json(self.io / f"state-{n}.json", snap)
            marker = {"decision": n, "state": str(self.io / f"state-{n}.json"),
                      "frame": snap["frame"], "written_at": now_iso()}
            write_json(self.io / "LATEST.json", marker)
            self.log(f"WAITING decision {n} frame={snap['frame']} "
                     f"sim={snap['elapsed_sim_s']}s money={snap['money']}")
            dec = await self.wait_decision(n)
            if dec.get("abort"):
                self.end_kind = str(dec.get("reason") or "ABORT")
                self.end_detail = "driver stopped waiting for decision"
                break
            record = await self.execute_decision(n, dec)
            st = self.latest_state
            n += 1
            if record.get("fatal"):
                self.end_kind = "FAILED_DRIVER"
                break
            if record.get("terminal") not in (None, "RUNNING"):
                self.end_kind = record["terminal"]
                break
        return n

    # ---- top level ----
    async def run(self):
        self.out.mkdir(parents=True, exist_ok=True)
        self._logf = (self.out / "driver.log").open("a", encoding="utf-8")
        self.started_mono = time.monotonic()
        self.episode_id = self.preset_episode_id or (time.strftime("%Y%m%d-%H%M%S") + "-llm")
        self.write_result(status="STARTING", episode_id=self.episode_id, game_launches=0,
                          attach=self.attach)
        try:
            self.lock = lockfile.acquire(ROOT / "build/dsh-game.lock")
            self.log(f"game lock acquired: {self.lock}")
            if self.attach:
                if not self.external_pid:
                    raise RuntimeError("--attach requires --game-pid")
                self.log(f"ATTACH mode: reusing existing game pid={self.external_pid}; "
                         f"no new launch, no retry")
                st = await self.connect_existing()
                self.write_result(status="ATTACHED", attach=True,
                                  game_pid=self.external_pid, game_launches=0)
            else:
                kill_games(EXE_NAME)
                time.sleep(1.0)
                consume_authorization(self.opts.authorization, self.out)
                self.log(f"authorization consumed: {self.opts.authorization}")
                self.proc = launch_game(self.game)
                self.log(f"launched game pid={self.proc.pid} dir={self.game}")
                self.write_result(status="LAUNCHED", game_pid=self.proc.pid, game_launches=1)
                st = await self.connect_ingame()
            self.initial = await read_initial(self.client)
            self.type_names = {int(t.pointer_self): t.name for t in self.initial.object_types}
            self.house_names = {int(h.self): h.name for h in st.houses}
            hero = self._hero(st)
            self.hero_self = int(hero.self) if hero is not None else None
            self.hero_country = int(getattr(hero, "type_array_index", -1)) if hero is not None else None
            self.public_starts = public_start_cells(self.game / "spawnmap.ini")
            self.build_catalog(self.initial)
            self.frames_start = int(st.current_frame)
            baseline = await self.read_config()
            self.log(f"bridge config before mode setup: single_step={baseline.config.single_step}")
            if self.runtime_mode == "realtime":
                self.mode = "realtime"
                if bool(baseline.config.single_step):
                    await self.set_single_step(False)
                self.probe = {
                    "verdict": "REALTIME_MODE_REQUESTED",
                    "config_single_step": bool(baseline.config.single_step),
                    "single_step_evidence_in_previous_run": True,
                    "note": "single-step capability was verified on this DLL in LLM-TAKEOVER-01; "
                            "this run keeps the game live so the window stays responsive",
                }
                write_json(self.out / "evidence" / "runtime_mode.json", self.probe)
            elif self.runtime_mode == "slowmo":
                enabled = await self.set_single_step(True)
                if not enabled:
                    self.mode = "realtime"
                    self.probe = {"verdict": "SLOWMO_SETUP_FAILED_REALTIME_FALLBACK"}
                else:
                    s1 = await self.gs(strict=True)
                    f1 = int(s1.current_frame)
                    await asyncio.sleep(1.0)
                    s2 = await self.gs(strict=True)
                    f2 = int(s2.current_frame)
                    ok = 1 <= (f2 - f1) <= 2
                    self.mode = "slowmo" if ok else "single_step"
                    self.probe = {
                        "verdict": "SLOWMO_VERIFIED" if ok else "SLOWMO_UNEXPECTED_FRAME_ADVANCE",
                        "single_step_enabled": True,
                        "quiet_seconds": 1.0,
                        "frames": [f1, f2],
                        "delta": f2 - f1,
                        "slowmo_fps": self.slowmo_fps,
                        "note": "single-step frame release paced by the driver; the game window "
                                "receives frames continuously so it stays responsive",
                    }
                write_json(self.out / "evidence" / "runtime_mode.json", self.probe)
            else:
                self.probe = await self.probe_single_step()
            self.log(f"control mode: {self.mode}")
            st = await self.gs()
            self.latest_state = st
            self.write_result(
                status="RUNNING", mode=self.mode,
                game_pid=(self.proc.pid if self.proc is not None else self.external_pid),
                initial_house={"name": hero.name, "faction": hero.faction,
                               "money": int(hero.money),
                               "start_credits": int(getattr(hero, "start_credits", 0))},
                single_step_probe=self.probe,
                game_fingerprints={
                    "game_exe": sha256(self.game / EXE_NAME),
                    "bridge_dll": sha256(self.game / "libra2yrcpp.dll"),
                    "spawn_ini": sha256(self.game / "spawn.ini"),
                    "spawnmap": sha256(self.game / "spawnmap.ini"),
                },
                public_start_cells=self.public_starts,
                decisions=0)
            n = await self.run_loop(self.start_decision)
            self.write_result(status="FINISHED", decisions=n, end_kind=self.end_kind,
                              end_detail=self.end_detail,
                              native_outcomes=dict(self.native_outcomes),
                              final_frame=int(self.latest_state.current_frame)
                              if self.latest_state is not None else None)
        except BaseException as exc:  # noqa: BLE001
            self.failure = f"{type(exc).__name__}: {exc}"
            self.log(f"FAILED: {self.failure}")
            self.write_result(status="FAILED", failure=self.failure,
                              traceback=traceback.format_exc()[-4000:],
                              end_kind=self.end_kind,
                              native_outcomes=dict(self.native_outcomes))
        finally:
            await self.finalize()

    async def finalize(self):
        # keep the final frame evidence, then free the one game process
        try:
            self.write_progress()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self.client is not None:
                try:
                    await asyncio.wait_for(self.client.stop(), 5)
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass
        pid = getattr(self.proc, "pid", None) if self.proc is not None else self.external_pid
        try:
            if pid and lockfile._pid_alive(pid):
                kill_game(pid)
                await asyncio.sleep(1.0)
        except Exception:  # noqa: BLE001
            pass
        try:
            if self.lock is not None:
                lockfile.release(self.lock)
        except Exception:  # noqa: BLE001
            pass
        remaining = 0
        try:
            out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {EXE_NAME}"],
                                 capture_output=True, text=True, timeout=20).stdout
            remaining = sum(1 for line in out.splitlines()
                            if line.split() and line.split()[0].lower().startswith("gamemd"))
        except Exception:  # noqa: BLE001
            remaining = -1
        self.write_result(
            status="FINISHED" if self.failure is None else "FAILED",
            failure=self.failure, end_kind=self.end_kind,
            decisions=self.decision_index,
            rule_fires_total=self.rule_fires_total,
            rules_active=self.rule_set.describe(),
            final_frame=int(self.latest_state.current_frame) if self.latest_state is not None else None,
            remaining_game_processes=remaining,
            finished_at=now_iso(),
            episode="episode/decisions.jsonl",
            note="DSH LLM direct control; raw demonstration only; no BC/PPO training")
        self.log(f"finalize: end_kind={self.end_kind} decisions={self.decision_index} "
                 f"remaining_game_processes={remaining} failure={self.failure}")
        if self._logf:
            self._logf.close()
            self._logf = None


async def amain(opts):
    tk = Takeover(opts)
    if opts.attach:
        out = pathlib.Path(opts.out).resolve()
        out.mkdir(parents=True, exist_ok=True)
        alive_pid = bool(opts.game_pid) and lockfile._pid_alive(int(opts.game_pid))
        port_ok = False
        try:
            with socket.create_connection(("127.0.0.1", int(opts.port)), timeout=2):
                port_ok = True
        except OSError:
            port_ok = False
        write_json(out / "PREFLIGHT_ATTACH.json", {
            "attach": True, "game_pid": opts.game_pid, "game_pid_alive": alive_pid,
            "bridge_port_connectable": port_ok, "checked_at": now_iso(),
            "note": "controller restart on the same single game process; no new launch",
        })
        if not alive_pid or not port_ok:
            print(json.dumps({"attach": False, "game_pid_alive": alive_pid,
                              "bridge_port_connectable": port_ok}))
            return 3
        await tk.run()
        return 0 if tk.failure is None else 1
    if not opts.execute:
        # offline preflight only: env/import checks, no game
        pre = tk_run_preflight(opts)
        print(json.dumps({"preflight": pre["pass"],
                          "failed": [k for k, v in pre["checks"].items() if not v]}, ensure_ascii=False))
        return 0 if pre["pass"] else 3
    pre = tk_run_preflight(opts)
    if not pre["pass"]:
        print(json.dumps({"preflight": False,
                          "failed": [k for k, v in pre["checks"].items() if not v]}))
        return 3
    await tk.run()
    return 0 if tk.failure is None else 1


def tk_run_preflight(opts):
    out = pathlib.Path(opts.out).resolve()
    game = pathlib.Path(opts.game_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    pre = preflight(out, game, int(opts.port), opts.authorization, opts.max_sim_seconds)
    write_json(out / "PREFLIGHT.json", pre)
    return pre


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True)
    ap.add_argument("--game-dir", default=str(ROOT / "test-game-brutal"))
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--authorization", default="LLM-TAKEOVER-01")
    ap.add_argument("--max-sim-seconds", type=float, default=1200)
    ap.add_argument("--max-wall-seconds", type=float, default=3600)
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--attach", action="store_true",
                    help="attach to the already-running single game instead of launching")
    ap.add_argument("--game-pid", type=int, default=None,
                    help="existing game pid for --attach (for honest cleanup/terminal checks)")
    ap.add_argument("--start-decision", type=int, default=0)
    ap.add_argument("--episode-id", default=None)
    ap.add_argument("--decision-timeout", type=float, default=900.0,
                    help="max wall seconds to wait for one LLM decision file")
    ap.add_argument("--runtime-mode", choices=("auto", "single_step", "realtime", "slowmo"),
                    default="auto",
                    help="auto=probe/enable single-step; realtime=keep the game live; "
                         "slowmo=single-step with paced frame release so the window stays "
                         "responsive while the LLM thinks (recommended)")
    ap.add_argument("--slowmo-fps", type=float, default=10.0,
                    help="simulation frames released per second while waiting in slowmo mode")
    opts = ap.parse_args()
    if sys.version_info[:2] != (3, 10):
        raise SystemExit("Use F:/conda/envs/python_mmx_LLM/python.exe (3.10)")
    return asyncio.run(amain(opts))


if __name__ == "__main__":
    sys.exit(main())
