"""RA2 episode runner v2: launch/attach, record raw observations + raw actions.

Usage (launch a fresh offline skirmish in the isolated test copy):
  .venv\\Scripts\\python.exe scripts\\dsh_episode_runner.py

Attach to an already running game:
  .venv\\Scripts\\python.exe scripts\\dsh_episode_runner.py --attach --episode-id ep-0001

Resume an interrupted (crash/hard-kill) recording of a still-running game:
  .venv\\Scripts\\python.exe scripts\\dsh_episode_runner.py --attach --resume --episode-id ep-0001

Safety:
  * only the isolated copy E:\\RA2MCP\\test-game is launched
  * never sells buildings, never removes/teleports enemies, never edits game state
  * hidden enemies are filtered by the visibility contract before any policy sees them
  * a crashed/exited game is recorded as `crash`, never as `win`
"""
import argparse
import asyncio
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time
from dsh_m1 import capture_v1  # P3 decision/map capture
from dsh_m1 import command_capture as command_capture_mod  # TASK-105 (default off)
import traceback
import uuid
import configparser

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
TEST_GAME = ROOT / "test-game"
EXE = TEST_GAME / "gamemd-spawn-ra2yrcpp.exe"
DEFAULT_EXE_NAME = "gamemd-spawn-ra2yrcpp.exe"
LEARN = ROOT / "reports" / "dsh-learning"
EPISODES = LEARN / "episodes"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "dsh_train"))
sys.path.insert(0, str(ROOT / "vendor" / "pyra2yr"))
# RUN02 run-protection: central RPC timeout table + TimingLog for every critical await.
sys.path.insert(0, str(ROOT / "v2_candidate" / "src"))
try:
    from ra2v2 import run_protection as _protect
except Exception:  # noqa: BLE001 - offline tools/tests may run without v2_candidate/src
    _protect = None

# stall protection for the legacy episode runner loop as well (RUN01 class of hang)
DEFAULT_STALL_SECONDS = 8.0

from dsh_episode import actions as actions_mod  # noqa: E402
from dsh_episode import lockfile as lockfile_mod  # noqa: E402
from dsh_episode import model_policy as model_policy_mod  # noqa: E402
from dsh_episode.process_output import run_captured, archive_launch_logs
from dsh_episode import obs, policy as policy_mod, schema  # noqa: E402

from pyra2yr.network import DualClient  # noqa: E402
from ra2yrproto import commands_game, commands_yr, ra2yr  # noqa: E402

CELL = 256
ACTION_NAMES = {
    "UNIT_ACTION_DEPLOY": "UNIT_ACTION_DEPLOY",
    "UNIT_ACTION_MOVE": "UNIT_ACTION_MOVE",
    "UNIT_ACTION_ATTACK": "UNIT_ACTION_ATTACK",
    "UNIT_ACTION_STOP": "UNIT_ACTION_STOP",
}
BENIGN_DUP = "already exists"


# ---------------------------------------------------------------- utilities ----
def sha256(path):
    if not pathlib.Path(path).exists():
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def kill_games(exe_name=DEFAULT_EXE_NAME):
    """Kill every game behind this bridge image.

    Used by the single-instance launcher to clear leftovers. Parallel training
    instances must NOT call this (it would kill the other instances); they pass
    kill_existing=False and only terminate the process they launched themselves.
    """
    try:
        out = run_captured(["tasklist", "/FI",
                              f"IMAGENAME eq {exe_name}"],
                             timeout=20).stdout.decode('ascii', errors='replace')
    except Exception:  # noqa: BLE001
        return
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].lower().startswith("gamemd"):
            run_captured(["taskkill", "/PID", parts[1], "/F"], timeout=20)


def kill_game(pid):
    try:
        run_captured(["taskkill", "/PID", str(int(pid)), "/F"], timeout=20)
    except Exception:  # noqa: BLE001
        pass


def launch_game(game_dir=None):
    game_dir = pathlib.Path(game_dir) if game_dir else TEST_GAME
    exe = game_dir / DEFAULT_EXE_NAME
    # Keep the game's C runtime stderr: an uncaught bridge exception prints the
    # terminate message there, which is the only evidence left after a crash.
    # Archive a previous non-empty log instead of deleting it: the next launch
    # must not erase the crash message from the game that just died.
    stdout_path = game_dir / "dsh-game-stdout.log"
    stderr_path = game_dir / "dsh-game-stderr.log"
    archive_launch_logs(game_dir)
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        return subprocess.Popen(
            [str(exe), "-SPAWN", "-CD"], cwd=str(game_dir),
            stdout=stdout, stderr=stderr,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))


def env_fingerprint(extra=None, game_dir=None):
    game_dir = pathlib.Path(game_dir) if game_dir else TEST_GAME
    fp = {
        "game_dir": str(game_dir),
        "game_exe": f"{game_dir.name}/{DEFAULT_EXE_NAME}",
        "game_exe_sha256": sha256(game_dir / DEFAULT_EXE_NAME),
        "bridge_dll_sha256": sha256(game_dir / "libra2yrcpp.dll"),
        "spawn_ini_sha256": sha256(game_dir / "spawn.ini"),
        "map_sha256": sha256(game_dir / "spawnmap.ini"),
        "rules_sha256": sha256(game_dir / "rulesmd.ini"),
        "collector_code_sha256": {name: sha256(SCRIPTS / "dsh_episode" / name)
                                   for name in ("runner.py", "policy.py", "obs.py", "schema.py",
                                                "model_policy.py", "hostility.py")},
        "spawn_ini": (game_dir / "spawn.ini").read_text(encoding="utf-8", errors="replace")
        if (game_dir / "spawn.ini").exists() else None,
        "ml_env": "F:\\conda\\envs\\python_mmx_LLM (torch 2.5.1+cu121)",
        "controller_env": sys.executable,
    }
    if extra:
        fp.update(extra)
    return fp


def public_start_cells(path=None):
    """Map-authored spawn waypoints, not live hidden opponent positions."""
    path = pathlib.Path(path) if path else TEST_GAME / "spawnmap.ini"
    ini = configparser.ConfigParser(strict=False, interpolation=None)
    ini.read_string(pathlib.Path(path).read_text(encoding="utf-8", errors="replace"))
    if not ini.has_section("Waypoints"):
        return []
    starts = []
    for key, value in ini.items("Waypoints"):
        if key.isdigit() and int(key) < 8 and value.strip().isdigit():
            encoded = int(value)
            starts.append([encoded % 1000, encoded // 1000])
    return starts


# ------------------------------------------------------------- bridge calls ----
class GameStateReadError(RuntimeError):
    """The bridge did not return a valid real game snapshot."""


def _rpc_timeout(name, fallback):
    return _protect.rpc_timeout(name) if _protect is not None else float(fallback)


def _rpc_record(name, t0, status, error=None, **extra):
    ms = round((time.monotonic() - t0) * 1000.0, 3)
    if _protect is not None:
        try:
            _protect.RPC_TIMING.record(f"runner.rpc.{name}", ms,
                                       {"status": status, "error": error, **extra})
        except Exception:  # noqa: BLE001 - timing must never break control
            pass
    return ms


async def read_state(client, strict=False):
    timeout = _rpc_timeout("GetGameState", 8.0)
    t0 = time.monotonic()
    try:
        r = await asyncio.wait_for(client.exec_command(commands_yr.GetGameState(),
                                                      timeout=min(3.0, timeout)), timeout)
        if getattr(r, "result_code", 0) != 0:
            _rpc_record("GetGameState", t0, "NATIVE_ERROR",
                        error=getattr(r, "error_message", ""))
            raise GameStateReadError(f"GetGameState rejected: {getattr(r, 'error_message', '')}")
        msg = commands_yr.GetGameState()
        if not r.result.Unpack(msg):
            _rpc_record("GetGameState", t0, "UNPACK_ERROR")
            raise GameStateReadError("GetGameState response could not be unpacked")
        _rpc_record("GetGameState", t0, "OK")
        return msg.state
    except asyncio.TimeoutError:
        # A Python-side timeout is NOT proof that the game command was cancelled.
        _rpc_record("GetGameState", t0, "TIMEOUT",
                    delivery=(_protect.DELIVERY_UNKNOWN if _protect is not None
                              else "UNKNOWN"),
                    note="asyncio timeout != game command cancel")
        if strict:
            raise GameStateReadError(
                f"GetGameState timeout after {timeout}s (delivery UNKNOWN)") from None
        return None
    except GameStateReadError:
        raise
    except Exception as exc:  # noqa: BLE001
        _rpc_record("GetGameState", t0, "ERROR", error=f"{type(exc).__name__}: {exc}")
        if strict:
            raise GameStateReadError(f"{type(exc).__name__}: {exc}") from exc
        return None


async def send_cmd(client, cmd, timeout=5.0):
    name = type(cmd).__name__ if not isinstance(cmd, (int, str)) else "cmd"
    timeout = _rpc_timeout(name, timeout)
    t0 = time.monotonic()
    try:
        r = await asyncio.wait_for(client.exec_command(cmd, timeout=min(timeout, 5.0)),
                                   timeout)
    except asyncio.TimeoutError:
        # RUN02: timeout means delivery UNKNOWN; the caller must abort, never re-send.
        _rpc_record(f"command.{name}", t0, "TIMEOUT",
                    delivery=(_protect.DELIVERY_UNKNOWN if _protect is not None
                              else "UNKNOWN"),
                    timeout_s=timeout, command=name,
                    note="not retried: timeout != cancelled, risk of duplicate command")
        return None, "timeout"
    ms = _rpc_record(f"command.{name}", t0, "OK")
    if getattr(r, "result_code", 0) != 0:
        _rpc_record(f"command.{name}", t0, "NATIVE_ERROR",
                    error=getattr(r, "error_message", ""))
        return ms, f"native_error: {getattr(r, 'error_message', '')}"
    return ms, None


async def read_initial(client):
    timeout = _rpc_timeout("initial_game_state", 10.0)
    t0 = time.monotonic()
    try:
        q = commands_yr.ReadValue()
        q.data.initial_game_state.SetInParent()
        r = await asyncio.wait_for(client.exec_command(q, timeout=min(5.0, timeout)),
                                   timeout)
        msg = commands_yr.ReadValue()
        r.result.Unpack(msg)
        _rpc_record("initial_game_state", t0, "OK")
        return msg.data.initial_game_state
    except Exception as exc:  # noqa: BLE001
        _rpc_record("initial_game_state", t0, "ERROR",
                    error=f"{type(exc).__name__}: {exc}")
        raise


async def read_map_soa(client):
    timeout = _rpc_timeout("map_data_soa", 20.0)
    t0 = time.monotonic()
    try:
        q = commands_yr.ReadValue()
        q.data.map_data_soa.SetInParent()
        r = await asyncio.wait_for(client.exec_command(q, timeout=min(15.0, timeout)),
                                   timeout)
        msg = commands_yr.ReadValue()
        r.result.Unpack(msg)
        _rpc_record("map_data_soa", t0, "OK")
        return msg.data.map_data_soa
    except Exception as exc:  # noqa: BLE001
        _rpc_record("map_data_soa", t0, "ERROR",
                    error=f"{type(exc).__name__}: {exc}")
        raise


async def read_event_buffer(client):
    """Read the Bridge ring-buffer of raw game events: ReadValue(event_buffer).

    Returns the serialized snapshot bytes plus the parsed protobuf snapshot so the
    ingester can persist/verify raw evidence. Timeout is centrally configured
    (DSH_RPC_TIMEOUT_READ_EVENT_BUFFER_S, default 15s); never auto-retries.
    """
    timeout = _rpc_timeout("read_event_buffer", 10.0)
    t0 = time.monotonic()
    try:
        q = commands_yr.ReadValue()
        q.data.event_buffer.SetInParent()
        r = await asyncio.wait_for(client.exec_command(q, timeout=min(8.0, timeout)),
                                   timeout)
        msg = commands_yr.ReadValue()
        r.result.Unpack(msg)
        eb = msg.data.event_buffer
        raw = eb.SerializeToString()
        frames = [int(f) for f in eb.frame]
        _rpc_record("read_event_buffer", t0, "OK")
        return {"raw_bytes": raw, "frames": frames, "snapshot": eb,
                "captured_at": time.monotonic(),
                "rpc_ms": round((time.monotonic() - t0) * 1000, 1)}
    except Exception as exc:  # noqa: BLE001
        _rpc_record("read_event_buffer", t0, "ERROR",
                    error=f"{type(exc).__name__}: {exc}")
        raise


async def read_event_buffer(client):
    """Read the Bridge ring-buffer of raw game events: ReadValue(event_buffer).

    Returns the serialized snapshot bytes plus the parsed protobuf snapshot so the
    ingester can persist/verify raw evidence. Timeout is centrally configured
    (DSH_RPC_TIMEOUT_READ_EVENT_BUFFER_S, default 15s); never auto-retries.
    """
    timeout = _rpc_timeout("read_event_buffer", 10.0)
    t0 = time.monotonic()
    try:
        q = commands_yr.ReadValue()
        q.data.event_buffer.SetInParent()
        r = await asyncio.wait_for(client.exec_command(q, timeout=min(8.0, timeout)),
                                   timeout)
        msg = commands_yr.ReadValue()
        r.result.Unpack(msg)
        eb = msg.data.event_buffer
        raw = eb.SerializeToString()
        frames = [int(f) for f in eb.frame]
        _rpc_record("read_event_buffer", t0, "OK")
        return {"raw_bytes": raw, "frames": frames, "snapshot": eb,
                "captured_at": time.monotonic(),
                "rpc_ms": round((time.monotonic() - t0) * 1000, 1)}
    except Exception as exc:  # noqa: BLE001
        _rpc_record("read_event_buffer", t0, "ERROR",
                    error=f"{type(exc).__name__}: {exc}")
        raise


# ------------------------------------------------------------------- runner ----
class EpisodeRunner:
    def __init__(self, opts):
        self.opts = opts
        self.client = None
        self.proc = None
        self.writer = None
        self.episode_id = opts.episode_id or (time.strftime("%Y%m%d-%H%M%S")
                                              + "-" + uuid.uuid4().hex[:6])
        self.started = time.monotonic()
        self.t0 = time.monotonic()
        self.initial = None
        self.type_catalog = []
        self.type_by_ptr = {}
        self.house_names = {}
        self.map_soa = None
        self.capture = None  # P3 decision capture (set by env)
        self.capture_dir = None
        self.map_interval = float(getattr(opts, 'map_interval', 5.0) or 0.0)
        self.map_stats = {k: 0 for k in ('map_refresh_check','map_refresh_skip','map_refresh_enter',
            'map_read_attempt','map_read_success','map_read_fail','sidecar_attempt','sidecar_success',
            'sidecar_fail','sidecar_skip')}
        self.diag_path = None
        self.last_map_read = -99.0
        self.policy = None
        self.stats = {"orders": 0, "invalid_orders": 0, "benign_duplicates": 0,
                      "visibility_violations": 0, "stale_skipped": 0,
                      "stale_filtered": 0, "model_orders_ok": 0,
                      "fallback_orders_ok": 0}
        self.last_hp = {}
        self.first_damage_at = None
        self.policy_acks = []
        self.row_count = 0
        self.outcome = {"kind": "running", "detail": "", "is_win": False, "is_crash": False}
        # RUN02 run-protection: frame-stall watchdog for this legacy loop as well
        self.stall_seconds = float(os.environ.get("DSH_STALL_TIMEOUT_S",
                                                  DEFAULT_STALL_SECONDS) or 0.0)
        self._last_frame_seen = None
        self._last_frame_mono = None
        self.stall_events = []
        self.rpc_timing = _protect.RPC_TIMING if _protect is not None else None
        # TASK-105: default-off command send capture (installed only on explicit opt-in)
        self.cmd_capture_state = None
        self.cmd_capture_failed = False
        self.capture_id = None
        # optional experimental "render-off" mode (validated runtime patch; see
        # dsh_episode.render_off). Disabled unless opts.render_off is set.
        self.render_off = None
        self.render_off_info = None
        self.render_off_evidence_dir = None
        self._ro_last_integrity = 0.0

    # ---- optional render-off mode (validated, reversible, opt-in only) ----
    def enable_render_off(self, evidence_dir=None):
        """Hide the window and enable the validated draw-skip patch.

        Raises if the process is not a test copy, the exe/prologue does not match
        the verified bytes, the window cannot be hidden or the patch write cannot
        be verified.  The caller must treat any failure as "mode unavailable" and
        abort the run instead of continuing with a partial setup.
        """
        from dsh_episode.render_off import RenderOffController  # noqa: PLC0415
        if self.proc is None:
            raise RuntimeError(
                "render-off mode requires the runner to launch the game itself; "
                "attach mode is not supported")
        if evidence_dir is None:
            base = pathlib.Path(getattr(self.opts, "episodes_dir", LEARN / "episodes"))
            evidence_dir = base.parent / "render-off" / self.episode_id
        self.render_off_evidence_dir = str(evidence_dir)
        ctrl = RenderOffController(
            self.proc.pid, evidence_dir=evidence_dir,
            stall_seconds=float(getattr(self.opts, "render_off_stall_seconds", 8.0)),
            read_fail_limit=int(getattr(self.opts, "render_off_read_fail_limit", 5)),
            action_fail_limit=int(getattr(self.opts, "render_off_action_fail_limit", 10)))
        ctrl.find_window()
        ctrl.hide_window()
        info = ctrl.enable()
        self.render_off = ctrl
        self.render_off_info = info
        self._ro_last_integrity = time.monotonic()
        self.log(f"render-off enabled: pid={self.proc.pid} va=0x{info['va']:x} "
                 f"bytes_after={info['bytes_after']} evidence={evidence_dir}")
        return info

    def disable_render_off(self):
        """Restore the original bytes and show the window again.

        Returns the restore evidence.  Raises RenderOffRestoreError when the
        original bytes cannot be verified; the caller must then terminate the
        process instead of handing it back to a human.
        """
        if self.render_off is None:
            return {"restored": True, "note": "render-off not enabled"}
        info = self.render_off.disable()
        try:
            self.render_off.show_window()
        except Exception as exc:  # noqa: BLE001
            self.log(f"render-off: window show failed after restore: {exc}")
        return info

    # ---- logging ----
    def log(self, msg):
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        if self.writer is not None:
            try:
                self.writer.add_row({"type": "note", "wall": schema.now_wall(),
                                     "message": msg})
            except Exception:  # noqa: BLE001
                pass

    # ---- connect ----
    async def connect_ingame(self, proc=None):
        deadline = time.monotonic() + self.opts.launch_timeout
        client = None
        attempts = 0
        while time.monotonic() < deadline:
            if proc is not None and proc.poll() is not None:
                raise GameStateReadError(f"game exited during launch rc={proc.returncode}")
            if client is None:
                attempts += 1
                client = DualClient("127.0.0.1", self.opts.port, timeout=2)
                client.connect()
                await asyncio.sleep(2.0)
            st = await read_state(client)
            if st is not None and st.stage == 2 and len(st.objects) > 0:
                self.client = client
                self.log(f"ingame frame={st.current_frame} objects={len(st.objects)}")
                if _protect is not None:
                    _protect.RPC_TIMING.event("runner.connect_ingame_ok",
                                              attempts=attempts,
                                              elapsed_s=round(
                                                  self.opts.launch_timeout -
                                                  (deadline - time.monotonic()), 2))
                return st
            await asyncio.sleep(2.0)
        if _protect is not None:
            _protect.RPC_TIMING.event("runner.connect_ingame_timeout",
                                      launch_timeout=self.opts.launch_timeout,
                                      attempts=attempts)
        raise GameStateReadError("bridge/INGAME timeout")

    # ---- default-off command capture wiring (TASK-105) ----
    def _ensure_command_capture(self):
        """Install the read-only send_cmd capture (no-op unless explicitly enabled).

        Disabled by default (``DSH_COMMAND_CAPTURE``); when disabled the original
        module-level ``send_cmd`` object is never touched and behavior is
        identical. Installation failures are recorded and never propagate.
        """
        if self.cmd_capture_state is not None or self.cmd_capture_failed:
            return
        try:
            info = command_capture_mod.install_for_module(
                sys.modules[__name__], capture_dir=self.capture_dir,
                episode_id=self.episode_id)
            if info is None:
                self.cmd_capture_failed = True  # disabled/unavailable: never retry
                return
            self.cmd_capture_state = info
            self.capture_id = info.get("capture_id")
        except Exception as exc:  # noqa: BLE001 - capture must never break control
            self.cmd_capture_failed = True
            try:
                self.log(f"command capture install failed: {type(exc).__name__}: {exc}")
            except Exception:  # noqa: BLE001
                pass

    # ---- action execution ----
    async def execute(self, st, hero, intent):
        """Execute one policy intent; return list of recorded action dicts.

        The intent is re-validated against the *current* state snapshot right
        before sending: addresses of moving/dying objects go stale between the
        observation and the order, which previously produced
        `native_error: object not found`. Stale intents are recorded as skipped.
        """
        self._ensure_command_capture()
        live = actions_mod.live_addresses(st)
        intent, gstat = actions_mod.guard_intent(intent, live)
        action = intent["action"]
        addrs = intent.get("object_addresses") or []
        if gstat["fatal"]:
            self.stats["stale_skipped"] += 1
            # a skipped order is *not* an executed action: record it as an event
            # (keeps the action schema strict: MOVE always has coords, etc.)
            return [schema.make_event("order_skipped", action=action,
                                      reason=gstat["reason"],
                                      object_addresses=addrs,
                                      target_address=intent.get("target_address"))]
        if gstat["dropped"]:
            self.stats["stale_filtered"] += 1
        if action == "DEPLOY":
            q = commands_game.UnitOrder(object_addresses=addrs,
                                        action=ra2yr.UNIT_ACTION_DEPLOY)
            ms, err = await send_cmd(self.client, q)
            return [schema.make_action("DEPLOY", addrs, ack_ms=ms, error=err)]
        if action == "MOVE":
            coords = intent["coordinates"]
            q = commands_game.UnitOrder(object_addresses=addrs,
                                        action=ra2yr.UNIT_ACTION_MOVE)
            q.coordinates.x, q.coordinates.y = int(coords[0]), int(coords[1])
            ms, err = await send_cmd(self.client, q)
            return [schema.make_action("MOVE", addrs, ack_ms=ms, error=err,
                                       coordinates=coords)]
        if action == "STOP":
            q = commands_game.UnitOrder(object_addresses=addrs,
                                        action=ra2yr.UNIT_ACTION_STOP)
            ms, err = await send_cmd(self.client, q)
            return [schema.make_action("STOP", addrs, ack_ms=ms, error=err)]
        if action == "ATTACK":
            q = commands_game.UnitOrder(object_addresses=addrs,
                                        action=ra2yr.UNIT_ACTION_ATTACK,
                                        target_object=int(intent["target_address"]))
            coords = intent.get("coordinates")
            if coords:
                q.coordinates.x, q.coordinates.y = int(coords[0]), int(coords[1])
            ms, err = await send_cmd(self.client, q)
            return [schema.make_action("ATTACK", addrs, ack_ms=ms, error=err,
                                       coordinates=coords,
                                       target_address=intent["target_address"],
                                       target_type=intent.get("target_type"))]
        if action == "PRODUCE":
            ptr = int(intent["production_type_ptr"])
            tc = self.type_by_ptr.get(ptr)
            if tc is None:
                return []
            q = commands_game.ProduceOrder(object_type=tc,
                                           action=ra2yr.PRODUCE_ACTION_BEGIN)
            ms, err = await send_cmd(self.client, q)
            return [schema.make_action("PRODUCE", [], ack_ms=ms, error=err,
                                       production_type=intent.get("production_type"),
                                       production_action="BEGIN")]
        if action == "PLACE":
            return await self.execute_place(st, hero, intent)
        return []

    async def execute_place(self, st, hero, intent):
        type_ptr = int(intent["production_type_ptr"])
        anchor = intent.get("anchor") or [91, 58]
        candidates = []
        for r in (2, 3, 4, 5, 6, 8, 10, 12):
            for dx, dy in [(r, 0), (-r, 0), (0, r), (0, -r),
                           (r, r), (-r, r), (r, -r), (-r, -r)]:
                candidates.append({"x": anchor[0] * CELL + dx * CELL + CELL // 2,
                                   "y": anchor[1] * CELL + dy * CELL + CELL // 2})
        pq = commands_yr.PlaceQuery(type_class=type_ptr, house_class=int(hero.self))
        for c in candidates:
            pq.coordinates.add(x=c["x"], y=c["y"])
        baddr = int(intent["building_address"])
        def placement_error(message):
            return [schema.make_action("PLACE", [baddr], building_address=baddr, error=message,
                                       coordinates=[anchor[0] * CELL + CELL // 2,
                                                    anchor[1] * CELL + CELL // 2])]
        q_ms, q_err = None, None
        pq_timeout = _rpc_timeout("PlaceQuery", 13.0)
        pq_t0 = time.monotonic()
        try:
            r = await asyncio.wait_for(self.client.exec_command(pq, timeout=min(10.0, pq_timeout)),
                                       pq_timeout)
            msg = commands_yr.PlaceQuery()
            if getattr(r, "result_code", 0) != 0 or not r.result.Unpack(msg):
                _rpc_record("PlaceQuery", pq_t0, "NATIVE_ERROR",
                            error=getattr(r, "error_message", "place query failed"))
                q_err = getattr(r, "error_message", "place query failed")
                return placement_error(f"place_query: {q_err}")
            valid = [{"x": c.x, "y": c.y} for c in msg.coordinates]
            _rpc_record("PlaceQuery", pq_t0, "OK")
        except Exception as e:  # noqa: BLE001
            _rpc_record("PlaceQuery", pq_t0, "ERROR", error=f"{type(e).__name__}: {e}")
            return placement_error(f"place_query exception: {e}")
        if not valid:
            return placement_error("place_query: no valid cell")
        obj = next((o for o in st.objects
                    if int(o.pointer_self) == baddr), None)
        if obj is None:
            return placement_error("building object not found")
        pb = commands_game.PlaceBuilding()
        pb.building.CopyFrom(obj)
        pb.coordinates.x, pb.coordinates.y = valid[0]["x"], valid[0]["y"]
        ms, err = await send_cmd(self.client, pb, timeout=8)
        return [schema.make_action("PLACE", [baddr], building_address=baddr,
                                   ack_ms=ms, error=err,
                                   coordinates=[valid[0]["x"], valid[0]["y"]],
                                   production_type=intent.get("production_type"))]

    # ---- main loop ----
    async def run(self):
        opts = self.opts
        self.lock_path = lockfile_mod.acquire(opts.lock_path)
        self.log(f"acquired game lock {self.lock_path}")
        if opts.attach:
            self.log(f"attach mode episode={self.episode_id} resume={opts.resume}")
            st = await self.connect_ingame(proc=None)
        else:
            if getattr(opts, 'kill_existing', True):
                kill_games()
                time.sleep(1.0)
            self.proc = launch_game(getattr(opts, 'game_dir', None))
            self.log(f"launched pid={self.proc.pid} episode={self.episode_id}")
            st = await self.connect_ingame(proc=self.proc)

        self.initial = await read_initial(self.client)
        self.type_catalog = [{
            "pointer_self": int(t.pointer_self), "name": t.name,
            "array_index": int(t.array_index),
            "type": ra2yr.AbstractType.Name(t.type),
        } for t in self.initial.object_types]
        self.type_by_ptr = {int(t.pointer_self): t for t in self.initial.object_types}
        self.type_names = {int(t.pointer_self): t.name for t in self.initial.object_types}
        self.house_names = {int(h.self): h.name for h in st.houses}
        hero = next(h for h in st.houses if h.current_player and h.is_human_player)

        policy_name, model_info = "rule-v2", None
        if opts.policy == "model":
            sys.path.insert(0, str(ROOT / "scripts" / "dsh_train"))
            import infer_v2  # noqa: PLC0415
            model_dir = pathlib.Path(opts.model_dir)
            if not model_dir.is_absolute():
                model_dir = ROOT / model_dir
            lm = infer_v2.LocalModelV2(model_dir, enabled=True)
            self.policy = model_policy_mod.ModelPolicy(
                lm, self.type_catalog, opts.policy_params,
                confidence_threshold=opts.model_threshold,
                fallback=True)
            policy_name = f"model-v2:{model_dir.name}"
            model_info = {"model_dir": str(model_dir),
                          "weights_sha256": sha256(model_dir / "weights.npz"),
                          "threshold": opts.model_threshold,
                          "feature_version": lm.norm.get("feature_version")}
            self.log(f"model policy loaded: {model_info}")
        else:
            self.policy = policy_mod.RulePolicy(opts.policy_params, self.type_catalog)
        self.public_start_cells = public_start_cells(
            pathlib.Path(getattr(opts, 'game_dir', None) or TEST_GAME) / 'spawnmap.ini')
        if getattr(opts, "render_off", False):
            try:
                self.enable_render_off()
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(
                    f"render-off mode refused (not falling back silently): {exc}"
                ) from None
        render_off_env = {"enabled": bool(self.render_off is not None),
                          "evidence_dir": self.render_off_evidence_dir}
        if self.render_off_info:
            render_off_env.update({
                "va": self.render_off_info.get("va"),
                "patch_bytes": self.render_off_info.get("bytes_after"),
                "original_bytes": self.render_off_info.get("bytes_before")})
        env = env_fingerprint({"public_start_cells": self.public_start_cells,
                               "opponent_contract": "offline_no_alliances_hostile_v1",
                               "render_off": render_off_env},
                              game_dir=getattr(opts, 'game_dir', None))
        meta = schema.make_meta(self.episode_id, policy_name, self.policy.p,
                                env, model=model_info)
        if opts.resume:
            meta["resumed"] = True
        self.writer = schema.EpisodeWriter(opts.episodes_dir, self.episode_id,
                                          meta, resume=opts.resume)
        if opts.resume:
            self.log(f"resumed episode with {self.writer.row_count} existing rows")
            self.writer.add_row({"type": "note", "wall": schema.now_wall(),
                                 "message": "recording resumed after restart"})

        self.t0 = time.monotonic()
        st = await self.step_loop(st, meta)
        return st

    async def step_loop(self, st, meta):
        opts = self.opts
        hero = next((h for h in st.houses if h.current_player and h.is_human_player),
                    None)
        read_failures = 0
        while True:
            cycle_start = time.monotonic()
            elapsed = cycle_start - self.t0
            if self.proc is not None and self.proc.poll() is not None:
                self.outcome = schema.classify_outcome(
                    proc_exited=True, exit_code=self.proc.returncode,
                    elapsed_s=elapsed, max_seconds=opts.max_seconds)
                self.outcome["exit_code"] = self.proc.returncode
                break
            if elapsed > opts.max_seconds:
                self.outcome = schema.classify_outcome(
                    stage_name="STAGE_INGAME", elapsed_s=elapsed,
                    max_seconds=opts.max_seconds)
                break

            st = await read_state(self.client)
            if st is None:
                read_failures += 1
                if read_failures >= 8:
                    self.outcome = schema.classify_outcome(
                        proc_exited=True, exit_code=None,
                        elapsed_s=time.monotonic() - self.t0,
                        max_seconds=opts.max_seconds)
                    self.outcome["detail"] = f"state read failed {read_failures}x"
                    break
                await asyncio.sleep(1.0)
                continue
            read_failures = 0
            # RUN02 frame-stall watchdog (multiple signals; raises no exception here
            # because this legacy loop finalizes on break instead of aborting).
            now_mono = time.monotonic()
            frame_now = int(st.current_frame)
            if self._last_frame_seen is None or frame_now != self._last_frame_seen:
                self._last_frame_seen = frame_now
                self._last_frame_mono = now_mono
            elif self.stall_seconds > 0 and (now_mono - self._last_frame_mono) >= self.stall_seconds:
                idle = round(now_mono - self._last_frame_mono, 2)
                evidence = {"schema": "run-protection/stall-evidence/1",
                            "classification": "FRAME_STALL",
                            "detected_by": "legacy_runner_loop",
                            "frame": frame_now, "frame_stagnant_s": idle,
                            "game_pid": getattr(getattr(self, "proc", None), "pid", None),
                            "rpc": (self.rpc_timing.summary()
                                    if self.rpc_timing is not None else None)}
                self.stall_events.append(evidence)
                self.log(f"FRAME STALL: frame={frame_now} idle={idle}s; finalizing STALLED")
                self.outcome = {"kind": "stalled",
                                "detail": f"frame stall: frame={frame_now} idle={idle}s",
                                "is_win": False, "is_crash": False}
                break
            hero = next((h for h in st.houses
                         if h.current_player and h.is_human_player), hero)
            stage_name = ra2yr.LoadStage.Name(st.stage)
            enemies = [h for h in st.houses
                       if not h.is_human_player and h.name == "Computer"]
            enemies_all_defeated = bool(enemies) and all(h.defeated for h in enemies)
            self.outcome = schema.classify_outcome(
                stage_name=stage_name,
                human_defeated=bool(hero.defeated) if hero else False,
                human_is_loser=bool(getattr(hero, "is_loser", False)) if hero else False,
                human_is_winner=bool(getattr(hero, "is_winner", False)) if hero else False,
                enemies_all_defeated=enemies_all_defeated,
                proc_exited=False, elapsed_s=time.monotonic() - self.t0,
                max_seconds=opts.max_seconds)
            if self.outcome["kind"] != "running":
                break

            _map = await self._map_refresh(st)
            fresh_map = _map['fresh']
            wall = schema.now_wall()
            self.house_names = {int(h.self): h.name for h in st.houses}
            walk = obs.capture_observation(st, int(hero.self), self.type_names,
                                           self.house_names, self.map_soa, wall)
            walk["t"] = round(now, 2)  # relative seconds (metrics / training join key)
            enemy_hp = {o["addr"]: o["health"] for o in walk["visible_enemies"]}
            own_hp = {o["addr"]: o["health"] for o in walk["own_objects"]}
            events = []
            for addr, hp in own_hp.items():
                prev = self.last_hp.get(addr)
                if prev is not None and hp < prev:
                    events.append(schema.make_event(
                        "own_object_damaged", addr=addr, hp_before=prev, hp_now=hp,
                        frame=st.current_frame))
            for addr, hp in enemy_hp.items():
                prev = self.last_hp.get(addr)
                if prev is not None and hp < prev:
                    events.append(schema.make_event(
                        "enemy_object_damaged", addr=addr, hp_before=prev, hp_now=hp,
                        frame=st.current_frame))
                    if self.first_damage_at is None:
                        self.first_damage_at = round(now, 2)
            for addr in list(self.last_hp):
                if addr not in own_hp and addr not in enemy_hp:
                    prev = self.last_hp[addr]
                    if prev >= 0:
                        events.append(schema.make_event(
                            "own_object_lost", addr=addr, frame=st.current_frame))
            self.last_hp = {**own_hp, **enemy_hp}

            ctx = {
                "t": now,
                "frame": st.current_frame,
                "own_objects": walk["own_objects"],
                "visible_enemies": walk["visible_enemies"],
                "factories": walk["factories"],
                "hero": {"self": int(hero.self), "name": hero.name,
                         "money": int(hero.money)},
                "map": walk["map"],
                "observation": walk,
                "type_catalog": self.type_catalog,
                "last_acks": self.policy_acks,
                "public_start_cells": self.public_start_cells,
            }
            try:
                intents = self.policy.step(ctx)
            except Exception as e:  # noqa: BLE001
                self.log(f"policy error: {e}")
                intents = []
            actions = []
            for intent in intents:
                try:
                    actions.extend(await self.execute(st, hero, intent))
                except Exception as e:  # noqa: BLE001
                    actions.append(schema.make_action(
                        intent["action"], intent.get("object_addresses") or [],
                        error=f"exception: {e}"))
            executed = [a for a in actions if a.get("type") == "action"]
            for act in executed:
                act["decision_source"] = getattr(self.policy, "decision_source", "rule")
            skipped_events = [a for a in actions if a.get("type") == "event"]
            self.policy_acks = [{
                "action": a.get("action"),
                "production_type": a.get("production_type"),
                "error": a.get("error"),
                "t": now,
            } for a in executed]
            for act in executed:
                err = act.get("error")
                if err:
                    if BENIGN_DUP in err:
                        self.stats["benign_duplicates"] += 1
                    elif err.startswith("skipped:"):
                        pass  # already counted as stale_skipped inside execute
                    else:
                        self.stats["invalid_orders"] += 1
                else:
                    self.stats["orders"] += 1
                    key = "model_orders_ok" if act["decision_source"] == "model" else "fallback_orders_ok"
                    self.stats[key] += 1
            violations_before = self.stats["visibility_violations"]
            self.stats["visibility_violations"] = self.policy.violations
            policy_events = []
            while self.policy.events:
                ev = self.policy.events.pop(0)
                rec = {k: v for k, v in ev.items() if k != "event"}
                policy_events.append(schema.make_event(ev["event"], **rec))

            # ---- render-off watchdog: stall / read / action / patch integrity ----
            if self.render_off is not None:
                invalid_step = sum(
                    1 for a in executed
                    if a.get("error") and BENIGN_DUP not in str(a.get("error"))
                    and not str(a.get("error", "")).startswith("skipped:"))
                ro_violations = self.render_off.check_health(
                    int(st.current_frame), True,
                    ok_count=len(executed) - invalid_step,
                    invalid_count=invalid_step)
                if time.monotonic() - self._ro_last_integrity >= 5.0:
                    self._ro_last_integrity = time.monotonic()
                    try:
                        self.render_off.assert_patch_intact()
                    except Exception as exc:  # noqa: BLE001
                        ro_violations.append(("patch_lost", str(exc)))
                if ro_violations:
                    self.log(f"render-off watchdog tripped: {ro_violations}")
                    self.outcome = {
                        "kind": "crash", "is_win": False, "is_crash": True,
                        "detail": f"render-off watchdog: {ro_violations}; "
                                  f"evidence={self.render_off_evidence_dir}"}
                    break

            if fresh_map and opts.map_patch:
                own = walk["own_objects"]
                if own:
                    cx = round(sum(o["cell"][0] for o in own) / len(own))
                    cy = round(sum(o["cell"][1] for o in own) / len(own))
                    walk["map_patch"] = obs.map_patch_from_soa(
                        self.map_soa, (cx, cy), opts.map_patch_size)

            walk["record_kind"] = "periodic"  # P3: periodic obs never enters decision sequence
            self.writer.add_row(walk)
            for act in executed:
                self.writer.add_row(act)
            for ev in events + policy_events + skipped_events:
                self.writer.add_row(ev)
            self.row_count = self.writer.row_count
            if violations_before != self.stats["visibility_violations"]:
                self.log(f"WARNING: visibility violations {self.stats['visibility_violations']}")
            # pace the loop to one cycle per `--tick` seconds (processing included)
            spent = time.monotonic() - cycle_start
            if spent < opts.tick:
                await asyncio.sleep(opts.tick - spent)

        try:
            self._map_diag_summary()
        except Exception:  # noqa: BLE001
            pass
        self._write_rpc_timing()
        # ---- finalize ----
        try:
            st_final = await read_state(self.client)
        except Exception:  # noqa: BLE001
            st_final = None
        final = {"frame": None, "stage": None}
        if st_final is not None:
            final = {"frame": int(st_final.current_frame),
                     "stage": ra2yr.LoadStage.Name(st_final.stage),
                     "houses": [{"name": h.name, "defeated": bool(h.defeated),
                                 "is_loser": bool(getattr(h, "is_loser", False)),
                                 "is_winner": bool(getattr(h, "is_winner", False))}
                                for h in st_final.houses]}
        metrics = schema.derive_metrics(self.writer.rows)
        result = {
            "episode_id": self.episode_id,
            "schema_version": schema.SCHEMA_VERSION,
            "kind": self.outcome["kind"],
            "detail": self.outcome.get("detail", ""),
            "is_win": bool(self.outcome.get("is_win", False)),
            "is_crash": bool(self.outcome.get("is_crash", False)),
            "learning_excluded": self.outcome["kind"] in ("stalled", "crash",
                                                          "interrupted"),
            "stall_events": list(self.stall_events),
            "exit_code": self.outcome.get("exit_code"),
            "final": final,
            "elapsed_s": round(time.monotonic() - self.t0, 2),
            "rows": self.writer.row_count,
            "policy": {"name": meta["policy"]["name"], "params": self.policy.p,
                       "events": len(self.policy.events),
                       "model": meta.get("model")},
            "stats": {
                "orders_ok": self.stats["orders"],
                "model_orders_ok": self.stats["model_orders_ok"],
                "fallback_orders_ok": self.stats["fallback_orders_ok"],
                "invalid_orders": self.stats["invalid_orders"],
                "benign_duplicates": self.stats["benign_duplicates"],
                "stale_skipped": self.stats["stale_skipped"],
                "stale_filtered": self.stats["stale_filtered"],
                "visibility_violations": self.stats["visibility_violations"],
                "first_damage_at_s": self.first_damage_at,
                "policy": getattr(self.policy, "stats", None),
                "policy_events": getattr(self.policy, "events", None) and
                len(self.policy.events),
            },
            "metrics": metrics,
            "environment": meta["environment"],
        }
        path = self.writer.finalize(result)
        (path.parent / f"{self.episode_id}.result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        self.log(f"episode finalized: {path} kind={result['kind']} rows={result['rows']}")
        self.append_ledger(result)
        return st

    def append_ledger(self, result):
        LEARN.mkdir(parents=True, exist_ok=True)
        ledger = LEARN / "ledger.jsonl"
        with open(ledger, "a", encoding="utf-8") as f:
            f.write(json.dumps(result, ensure_ascii=False) + "\n")
        lines = ["# DSH 自主学习账本（自动生成）", "",
                 f"- 更新时间：{schema.now_wall()}", ""]
        rows = []
        if ledger.exists():
            for line in ledger.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        lines.append("| episode | kind | 时长 s | 行数 | 建筑峰值 | 单位峰值 | "
                     "造成伤害 | 自身损失 | 无效命令 | 透视频次 | 首伤 s |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for r in rows[-20:]:
            m = r.get("metrics") or {}
            s = r.get("stats") or {}
            lines.append(
                f"| {r.get('episode_id')} | {r.get('kind')} "
                f"| {r.get('elapsed_s', 0)} | {r.get('rows', 0)} "
                f"| {m.get('buildings_peak', 0)} | {m.get('units_peak', 0)} "
                f"| {m.get('damage_dealt', 0)} | {m.get('own_hp_lost', 0)} "
                f"| {s.get('invalid_orders', 0)} | {s.get('visibility_violations', 0)} "
                f"| {s.get('first_damage_at_s')} |")
        (LEARN / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _write_rpc_timing(self):
        """Persist the RUN02 RPC TimingLog next to the episode (offline analysis)."""
        if self.rpc_timing is None:
            return None
        base = getattr(self, "capture_dir", None) or getattr(self, "diag_path", None)
        if base is None:
            return None
        try:
            p = pathlib.Path(base)
            if p.suffix:  # a file path was configured
                p = p.parent
            target = p / f"{self.episode_id}.rpc-timing.json"
            target.write_text(json.dumps(
                {"summary": self.rpc_timing.summary(),
                 "recent": self.rpc_timing.tail(300),
                 "stall_seconds": self.stall_seconds,
                 "stall_events": self.stall_events},
                ensure_ascii=False, indent=1, default=str), encoding="utf-8")
            return target
        except OSError:
            return None

    def cleanup(self):
        # RUN02: persist the RPC TimingLog before teardown (always, even on failure).
        try:
            self._write_rpc_timing()
        except Exception:  # noqa: BLE001
            pass
        # TASK-105: restore send_cmd and flush command-capture metadata (no-op when disabled)
        try:
            command_capture_mod.uninstall()
        except Exception:  # noqa: BLE001 - cleanup must never fail because of capture
            pass
        # A rejected contender must never kill the holder's game. Keep the lock
        # through cleanup so the next runner cannot launch while we terminate.
        if not getattr(self, "lock_path", None):
            return
        render_off_restore_failed = False
        if self.render_off is not None:
            try:
                code = self.proc.poll() if self.proc is not None else None
                if code is not None:
                    self.render_off.process_exited(code)
                    self.log(f"render-off: process already exited rc={code}; no restore needed")
                else:
                    self.disable_render_off()
            except Exception as exc:  # noqa: BLE001
                code = self.proc.poll() if self.proc is not None else None
                if code is not None:
                    self.render_off.process_exited(code)
                else:
                    render_off_restore_failed = True
                    self.log(f"render-off RESTORE FAILED: {exc}; terminating process "
                             f"pid={self.proc.pid if self.proc else None} and keeping "
                             f"diagnostics at {self.render_off_evidence_dir}")
                    self.cleanup_failure = {"detail": str(exc), "pid": self.proc.pid if self.proc else None}
            finally:
                self.render_off = None
        try:
            if render_off_restore_failed:
                # never hand a still-patched (or unresolvable) process to a human
                if self.proc is not None and self.proc.poll() is None:
                    run_captured(["taskkill", "/PID", str(self.proc.pid), "/F"], timeout=20)
            elif not self.opts.keep_game:
                if self.proc is not None and self.proc.poll() is None:
                    run_captured(["taskkill", "/PID", str(self.proc.pid), "/F"], timeout=20)
                if not self.opts.attach and getattr(self.opts, 'kill_existing', True):
                    kill_games()
        finally:
            lockfile_mod.release(self.lock_path)


async def main_async(opts):
    runner = EpisodeRunner(opts)
    try:
        await runner.run()
    except KeyboardInterrupt:
        runner.outcome = schema.classify_outcome(interrupted=True)
        if runner.writer is not None:
            result = {"episode_id": runner.episode_id,
                      "schema_version": schema.SCHEMA_VERSION,
                      "kind": "interrupted", "detail": "KeyboardInterrupt",
                      "is_win": False, "is_crash": False,
                      "rows": runner.writer.row_count,
                      "elapsed_s": round(time.monotonic() - runner.t0, 2),
                      "metrics": schema.derive_metrics(runner.writer.rows)}
            try:
                runner.writer.finalize(result)
            except Exception:  # noqa: BLE001
                runner.writer.abort()
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        runner.outcome = {"kind": "crash", "detail": f"{type(e).__name__}: {e}",
                          "is_win": False, "is_crash": True}
        if runner.writer is not None:
            result = {"episode_id": runner.episode_id,
                      "schema_version": schema.SCHEMA_VERSION,
                      "kind": "crash", "detail": runner.outcome["detail"],
                      "is_win": False, "is_crash": True,
                      "rows": runner.writer.row_count,
                      "elapsed_s": round(time.monotonic() - runner.t0, 2),
                      "metrics": schema.derive_metrics(runner.writer.rows)}
            try:
                runner.writer.finalize(result)
                runner.append_ledger(result)
            except Exception:  # noqa: BLE001
                runner.writer.abort()
        else:
            runner.writer = None
        return 2
    finally:
        runner.cleanup()
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--episodes-dir", default=str(EPISODES))
    ap.add_argument("--episode-id", default=None)
    ap.add_argument("--port", type=int, default=14521)
    ap.add_argument("--attach", action="store_true",
                    help="attach to a running game instead of launching")
    ap.add_argument("--resume", action="store_true",
                    help="append to an interrupted .part recording (needs --attach)")
    ap.add_argument("--keep-game", action="store_true")
    ap.add_argument("--max-seconds", type=float, default=300.0)
    ap.add_argument("--tick", type=float, default=0.5)
    ap.add_argument("--map-interval", type=float, default=5.0)
    ap.add_argument("--map-patch", action="store_true",
                    help="record a local map patch (mapped/shrouded/passability)")
    ap.add_argument("--map-patch-size", type=int, default=9)
    ap.add_argument("--launch-timeout", type=float, default=90.0)
    ap.add_argument("--policy-params", default=None,
                    help="JSON string with policy param overrides")
    ap.add_argument("--policy", default="rule", choices=["rule", "model"],
                    help="rule-v2 policy or a v2 multi-head model")
    ap.add_argument("--model-dir", default="reports/dsh-learning/models/bc-v2-demo")
    ap.add_argument("--model-threshold", type=float, default=0.35,
                    help="class confidence below this falls back to the rule policy")
    ap.add_argument("--render-off", action="store_true",
                    help="EXPERIMENTAL: hide the game window and reversibly skip "
                         "GScreenClass::Render (validated test copies only); refused "
                         "when validation fails, default is normal rendering")
    ap.add_argument("--render-off-stall-seconds", type=float, default=8.0,
                    help="render-off watchdog: terminate after a frame stall longer than this")
    ap.add_argument("--render-off-read-fail-limit", type=int, default=5,
                    help="render-off watchdog: consecutive state-read failures before abort")
    ap.add_argument("--render-off-action-fail-limit", type=int, default=10,
                    help="render-off watchdog: consecutive invalid actions before abort")
    ap.add_argument("--lock-path", default=str(ROOT / "build" / "dsh-game.lock"),
                    help="exclusive lock file preventing concurrent collectors")
    ap.add_argument("--game-dir", default=None,
                    help="isolated game copy to launch (default: test-game)")
    ap.add_argument("--no-kill-existing", dest="kill_existing", action="store_false",
                    help="never kill other game processes: required for parallel instances")
    ap.set_defaults(kill_existing=True)
    opts = ap.parse_args(argv)
    if opts.resume and not opts.attach:
        raise SystemExit("--resume requires --attach")
    if opts.resume and not opts.episode_id:
        raise SystemExit("--resume requires --episode-id")
    if opts.policy_params:
        opts.policy_params = json.loads(opts.policy_params)
    return asyncio.run(main_async(opts))


if __name__ == "__main__":
    sys.exit(main())


# ---------------- P3 map-refresh production path + diagnostics ----------------
def _diag(self, event, **kw):
    rec = {"event": event, "t": round(time.time(), 3), "episode_id": getattr(self, "episode_id", None),
           "frame_id": kw.pop("frame_id", None), **kw}
    path = getattr(self, "diag_path", None)
    if path is None and getattr(self, "capture_dir", None) is not None:
        path = pathlib.Path(self.capture_dir) / f"{getattr(self, 'episode_id', 'episode')}.map_diag.jsonl"
        self.diag_path = path
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with pathlib.Path(path).open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


async def _map_refresh(self, st):
    """Production map-refresh: condition -> read_map_soa -> validate -> sidecar write.
    All branches observable via independent MAP_DIAG JSONL + map_stats counters."""
    ms = self.map_stats
    frame = getattr(st, "current_frame", None)
    now = time.monotonic() - self.t0
    ms["map_refresh_check"] += 1
    self._diag("MAP_REFRESH_CHECK", frame_id=frame)
    interval = getattr(self, "map_interval", 5.0)
    if self.map_soa is not None and (now - self.last_map_read) < interval:
        ms["map_refresh_skip"] += 1
        self._diag("MAP_REFRESH_SKIPPED", frame_id=frame, age=round(now - self.last_map_read, 3), interval=interval)
        return {"fresh": False}
    ms["map_refresh_enter"] += 1
    ms["map_read_attempt"] += 1
    self._diag("MAP_REFRESH_ENTER", frame_id=frame)
    self._diag("MAP_READ_START", frame_id=frame)
    fresh = False
    try:
        soa = await read_map_soa(self.client)
        if (soa is None or int(getattr(soa, "map_width", 0)) <= 0
                or len(getattr(soa, "mapped", []) or []) == 0):
            raise ValueError(f"SERIALIZE_EMPTY width={getattr(soa,'map_width',None)} "
                             f"mapped={len(getattr(soa,'mapped',[]) or [])}")
        self.map_soa = soa
        fresh = True
        ms["map_read_success"] += 1
        self._diag("MAP_READ_SUCCESS", frame_id=frame, width=int(soa.map_width),
                   height=int(soa.map_height), mapped=len(soa.mapped))
    except Exception as exc:  # noqa: BLE001
        self.map_soa = None
        ms["map_read_fail"] += 1
        self._diag("MAP_READ_FAILED", frame_id=frame, error=f"{type(exc).__name__}: {exc}",
                   traceback=traceback.format_exc()[-800:])
    self.last_map_read = now
    if fresh and getattr(self, "capture", None) is not None and getattr(self, "capture_dir", None) is not None:
        ms["sidecar_attempt"] += 1
        self._diag("SIDECAR_WRITE_START", frame_id=frame)
        try:
            m = capture_v1.write_map_sidecar(
                pathlib.Path(self.capture_dir) / f"{self.capture.episode_id}.map-f{int(frame)}.npz",
                self.map_soa, self.capture.episode_id, frame)
            ms["sidecar_success"] += 1
            self._diag("SIDECAR_WRITE_SUCCESS", frame_id=frame, capture_gap=m.get("capture_gap", []))
            if m.get("capture_gap"):
                self._diag("CAPTURE_GAP", frame_id=frame, fields=m["capture_gap"])
        except Exception as exc:  # noqa: BLE001
            ms["sidecar_fail"] += 1
            self._diag("SIDECAR_WRITE_FAILED", frame_id=frame,
                       error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc()[-800:])
    elif fresh:
        ms["sidecar_skip"] += 1
        self._diag("SIDECAR_SKIPPED", frame_id=frame, reason="capture_or_dir_unbound")
    self._diag("MAP_REFRESH_SUMMARY", frame_id=frame, counters=dict(ms))
    return {"fresh": fresh}


def _map_diag_summary(self):
    path = getattr(self, "diag_path", None)
    base = pathlib.Path(self.capture_dir) if getattr(self, "capture_dir", None) is not None else None
    if base is None:
        return
    out = base / f"{getattr(self, 'episode_id', 'episode')}.map_diag.summary.json"
    out.write_text(json.dumps({"episode_id": getattr(self, "episode_id", None),
                               "counters": dict(self.map_stats),
                               "map_interval": getattr(self, "map_interval", None),
                               "diag_events": path is not None}, ensure_ascii=False, indent=2), encoding="utf-8")


EpisodeRunner._diag = _diag
EpisodeRunner._map_refresh = _map_refresh
EpisodeRunner._map_diag_summary = _map_diag_summary
