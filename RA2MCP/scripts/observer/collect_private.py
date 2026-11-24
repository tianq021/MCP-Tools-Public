"""Live observer collector (P1 smoke + later batches) for RA2/YR.

Read-only by construction: the only commands sent are
  * ``GetGameState``      -- full per-frame memory snapshot (all houses / all objects)
  * ``ReadValue``         -- initial_game_state (type catalogue), map_data_soa, event_buffer
No UnitOrder / Produce / Place / AddEvent / any mutating command is ever sent.

The game is launched from an isolated ``test-game*`` copy with an observer-only
``spawn.ini`` (``IsSpectator=Yes`` + AI players).  Requires a previously approved
one-time authorization ledger (``coordination/approvals/*_AUTHORIZATION.json``).

Usage (P1 smoke):
  F:\\conda\\envs\\python_mmx_LLM\\python.exe scripts\\observer\\collect_live.py ^
    --game-dir E:\\RA2MCP\\test-game-observer-01 ^
    --run-id OBSERVER-P1-SMOKE-01 ^
    --ledger coordination\\approvals\\OBSERVER_P1_SMOKE_AUTHORIZATION.json ^
    --ai-count 2 --sim-seconds 90 --max-wall-seconds 180 ^
    --out _runs_observer\\observer-p1-smoke
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import pathlib
import socket
import subprocess
import sys
import time
import traceback
from observer import private_runtime as batch

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "vendor" / "pyra2yr"))

from observer import config, extract, maptool, store  # noqa: E402

EXE_NAME = "gamemd-spawn-ra2yrcpp.exe"
STAGE_INGAME = 2
CELL = 256

PROTECTED_HASHES = {
    "config/spawn.ini": ROOT / "config" / "spawn.ini",
    "test-game/spawn.ini": ROOT / "test-game" / "spawn.ini",
    "test-game/spawnmap.ini": ROOT / "test-game" / "spawnmap.ini",
    "test-game/RA2MD.ini": ROOT / "test-game" / "RA2MD.ini",
    "reports/rl/stage-A-round1-20261030-153225/model.zip":
        ROOT / "reports/rl/stage-A-round1-20261030-153225/model.zip",
    "config/rewards-combat.json": ROOT / "config" / "rewards-combat.json",
}


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def sha256(path: pathlib.Path) -> str | None:
    path = pathlib.Path(path)
    if not path.is_file():
        return None
    return extract.sha256_file(path)


def snapshot_protected() -> dict:
    return {name: sha256(path) for name, path in PROTECTED_HASHES.items()}


def count_game_processes(exe_name: str = EXE_NAME) -> int:
    try:
        out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {exe_name}"],
                             capture_output=True, text=True, timeout=20).stdout
    except Exception:  # noqa: BLE001
        return -1
    return sum(1 for line in out.splitlines() if line.strip().lower().startswith("gamemd"))


def port_free(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def load_ledger(path: pathlib.Path, run_id: str) -> dict:
    ledger = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    runs = ledger.get("runs", [])
    entry = next((r for r in runs if r.get("run") == run_id), None)
    if entry is None:
        raise SystemExit(f"run {run_id!r} not found in authorization ledger {path}")
    if entry.get("status") not in ("APPROVED_NOT_CONSUMED", "APPROVED"):
        raise SystemExit(f"authorization run {run_id!r} status is {entry.get('status')!r}; refusing")
    return ledger


def update_ledger(path: pathlib.Path, ledger: dict, run_id: str, **fields) -> None:
    for entry in ledger.get("runs", []):
        if entry.get("run") == run_id:
            entry.update(fields)
            entry.setdefault("execution_history", []).append(
                {"at": now_iso(), "status": fields.get("status")})
    pathlib.Path(path).write_text(json.dumps(ledger, ensure_ascii=False, indent=2),
                                  encoding="utf-8")


# ------------------------------------------------------------------ bridge I/O ----
async def get_state(client):
    from ra2yrproto import commands_yr

    cmd = commands_yr.GetGameState()
    try:
        reply = await asyncio.wait_for(client.exec_command(cmd, timeout=5.0), 8.0)
    except asyncio.TimeoutError:
        return None, "timeout"
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"
    if getattr(reply, "result_code", 0) != 0:
        return None, f"native_error: {getattr(reply, 'error_message', '')}"
    if not reply.result.Unpack(cmd):
        return None, "unpack_failed"
    return cmd.state, None


async def read_initial(client):
    from ra2yrproto import commands_yr

    q = commands_yr.ReadValue()
    q.data.initial_game_state.SetInParent()
    reply = await asyncio.wait_for(client.exec_command(q, timeout=8.0), 12.0)
    msg = commands_yr.ReadValue()
    if not reply.result.Unpack(msg):
        return None
    return msg.data.initial_game_state


async def read_map_soa(client):
    from ra2yrproto import commands_yr

    q = commands_yr.ReadValue()
    q.data.map_data_soa.SetInParent()
    reply = await asyncio.wait_for(client.exec_command(q, timeout=8.0), 12.0)
    msg = commands_yr.ReadValue()
    if not reply.result.Unpack(msg):
        return None
    return msg.data.map_data_soa


async def read_event_buffer(client):
    from ra2yrproto import commands_yr

    q = commands_yr.ReadValue()
    q.data.event_buffer.SetInParent()
    reply = await asyncio.wait_for(client.exec_command(q, timeout=8.0), 12.0)
    msg = commands_yr.ReadValue()
    if not reply.result.Unpack(msg):
        return None
    return msg.data.event_buffer


async def wait_for_bridge(port: int, timeout_s: float):
    from pyra2yr.network import DualClient

    deadline = time.monotonic() + timeout_s
    last_err = None
    while time.monotonic() < deadline:
        try:
            client = DualClient("127.0.0.1", port, timeout=2.0)
            client.connect()
            return client, None
        except Exception as exc:  # noqa: BLE001
            last_err = f"{type(exc).__name__}: {exc}"
            await asyncio.sleep(1.0)
    return None, last_err


def _state_sample(state, type_names):
    self_to_index = {int(h.self): int(h.array_index) for h in state.houses}
    houses = [
        [int(h.array_index), str(h.name), int(h.money), int(h.power_output),
         int(h.power_drain), bool(h.defeated), bool(h.is_loser), bool(h.is_winner)]
        for h in state.houses
    ]
    objects = []
    for obj in state.objects:
        try:
            x = int(obj.coordinates.x) // CELL
            y = int(obj.coordinates.y) // CELL
        except Exception:  # noqa: BLE001
            x = y = 0
        pointer = int(getattr(obj, "pointer_technotypeclass", 0))
        objects.append([
            self_to_index.get(int(getattr(obj, "pointer_house", 0)), -1),
            type_names.get(pointer, "?"),
            int(obj.health), x, y,
            1 if getattr(obj, "on_map", False) else 0,
            1 if getattr(obj, "in_limbo", False) else 0,
            int(getattr(obj, "current_mission", -1)),
            # extra columns (extract.py only requires the first 8):
            int(getattr(obj, "pointer_self", 0)),
            pointer,
        ])
    return {"frame": int(state.current_frame), "wall": round(time.time(), 3),
            "houses": houses, "objects": objects}


def _patch_aiohttp_message_size(limit_bytes: int = 256 * 1024 * 1024) -> None:
    """Raise aiohttp's incoming websocket message limit.

    The runtime ``map_data_soa`` reply for large maps can exceed aiohttp's default
    4 MiB limit; the bridge then closes the socket with code 1009 ("message too big")
    and the pyra2yr poll loop dies.  We only widen the limit for our own client.
    """
    try:
        import aiohttp

        original = aiohttp.ClientSession.ws_connect
        if getattr(original, "_dsh_large_msg_patch", False):
            return

        def ws_connect(self, *args, **kwargs):
            # aiohttp's ws_connect is a *sync* factory that returns an async context
            # manager; only the max_msg_size default needs to be widened here.
            kwargs.setdefault("max_msg_size", limit_bytes)
            return original(self, *args, **kwargs)

        ws_connect._dsh_large_msg_patch = True
        aiohttp.ClientSession.ws_connect = ws_connect
    except Exception:  # noqa: BLE001
        pass


def _observer_flags(state):
    """Return observer candidate (array_index, self) list for the host/observer seat."""
    out = []
    for h in state.houses:
        if bool(getattr(h, "current_player", False)):
            out.append((int(h.array_index), int(h.self), bool(h.is_human_player)))
    return out


def _save_map_soa(soa, path: pathlib.Path) -> dict:
    """Persist the full runtime map SoA (gzip JSON) so connectivity can be re-checked offline."""
    fields = ("land_type", "height", "level", "overlay_data", "tiberium_value",
              "shrouded", "passability", "mapped", "alt_flags", "is_fogged",
              "visibility", "shroud_state")
    data = {"map_width": int(soa.map_width), "map_height": int(soa.map_height)}
    for name in fields:
        if not hasattr(soa, name):
            continue
        try:
            sequence = list(getattr(soa, name))
            data[name] = [int(value) for value in sequence]
        except Exception:  # noqa: BLE001
            continue
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(data, fh)
    return {"map_width": data["map_width"], "map_height": data["map_height"],
            "fields": sorted(k for k in data if k not in ("map_width", "map_height")),
            "water_cells": sum(1 for v in data.get("land_type", []) if v == 2),
            "tiberium_cells": sum(1 for v in data.get("tiberium_value", []) if v > 0),
            "cells": len(data.get("land_type", []))}


def _start_cells(state, type_names: dict[int, str]) -> list[dict]:
    """Find each AI participant's starting cell from its first object (MCV preferred)."""
    self_to_index = {int(h.self): int(h.array_index) for h in state.houses}
    participants = {int(h.array_index) for h in state.houses
                    if not bool(getattr(h, "current_player", False))
                    and str(h.name) == "Computer"}
    starts: dict[int, dict] = {}
    for obj in state.objects:
        owner = self_to_index.get(int(getattr(obj, "pointer_house", 0)), -1)
        if owner not in participants:
            continue
        try:
            x = int(obj.coordinates.x) // CELL
            y = int(obj.coordinates.y) // CELL
        except Exception:  # noqa: BLE001
            continue
        type_name = type_names.get(int(getattr(obj, "pointer_technotypeclass", 0)), "?")
        preference = 0 if "Construction Vehicle" in type_name else 1
        current = starts.get(owner)
        if current is None or preference < current["preference"]:
            starts[owner] = {"house": owner, "cell": [x, y], "type": type_name,
                             "preference": preference}
    return [starts[k] for k in sorted(starts)]


def _ground_connectivity(soa, starts: list[dict]) -> dict:
    """BFS over land cells between the participant start cells (runtime map truth)."""
    from collections import deque

    width = int(soa.map_width)
    height = int(soa.map_height)
    land = list(soa.land_type)
    if len(land) != width * height:
        return {"all_connected": False, "error": "land_type length != w*h"}
    # LandType: 0 Clear, 1 Road, 2 Water, 3 Rock, 4 Wall, 5 Tiberium, 6 Beach,
    #           7 Rough, 8 Ice, 9 Railroad, 10 Tunnel, 11 Weeds
    impassable = {2, 3, 4}

    def passable(x: int, y: int) -> bool:
        if x < 0 or y < 0 or x >= width or y >= height:
            return False
        return land[y * width + x] not in impassable

    valid_starts = []
    for start in starts:
        x, y = start["cell"]
        if passable(x, y):
            valid_starts.append((start["house"], x, y))
        else:
            start["passable"] = False
    if len(valid_starts) < 2:
        return {"all_connected": False, "error": "fewer than 2 passable start cells",
                "valid_starts": len(valid_starts)}
    seen = set()
    queue = deque()
    house0, x0, y0 = valid_starts[0]
    queue.append((x0, y0))
    seen.add((x0, y0))
    while queue:
        x, y = queue.popleft()
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if (nx, ny) in seen or not passable(nx, ny):
                continue
            seen.add((nx, ny))
            queue.append((nx, ny))
    reachable = [house for house, x, y in valid_starts if (x, y) in seen]
    return {
        "all_connected": len(reachable) == len(valid_starts) == len(starts),
        "start_count": len(starts),
        "valid_starts": len(valid_starts),
        "reachable_starts": len(reachable),
        "reachable_houses": reachable,
        "passable_cells": sum(1 for v in land if v not in impassable),
        "water_cells": sum(1 for v in land if v == 2),
    }


# ---------------------------------------------------------------------- main ----
async def collect(opts) -> dict:
    run_dir = pathlib.Path(opts.out)
    run_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(exist_ok=True)
    result: dict = {
        "schema": "observer.live.v1",
        "run_id": opts.run_id,
        "task_id": opts.task_id,
        "mode": "P1_SMOKE" if opts.ai_count <= 2 else "OBSERVER_BATCH",
        "started_at": now_iso(),
        "game_dir": str(pathlib.Path(opts.game_dir).resolve()),
        "authorization": opts.ledger,
        "ai_count": opts.ai_count,
        "sim_seconds_limit": opts.sim_seconds,
        "max_wall_seconds": opts.max_wall_seconds,
        "status": "RUNNING",
    }
    result_path = run_dir / "RESULT.json"

    def save_result():
        batch.write_json(result_path,result)

    # ---------------- preflight ----------------
    game_dir = pathlib.Path(opts.game_dir).resolve()
    ledger_path = pathlib.Path(opts.ledger)
    map_path = pathlib.Path(opts.map_file)
    checks = {
        "game_dir_isolated": game_dir.name.lower().startswith("test-game")
        and "hj" not in game_dir.name.lower(),
        "game_dir_exists": game_dir.is_dir(),
        "exe_exists": (game_dir / EXE_NAME).is_file(),
        "bridge_dll_exists": (game_dir / "libra2yrcpp.dll").is_file(),
        "map_source_exists": map_path.is_file(),
        "no_existing_game_in_this_copy": not batch.copy_processes(game_dir),
        "bridge_port_free": port_free(opts.port),
        "bridge_port_config_matches": batch.validate_port(game_dir,opts.port),
        "ledger_exists": ledger_path.is_file(),
        "disk_free_gt_5gb": True,
    }
    try:
        free_gb = shutil_disk_free("E:")
        checks["disk_free_gt_5gb"] = free_gb > 5.0
        result["disk_free_gb"] = free_gb
    except Exception:  # noqa: BLE001
        pass
    result["preflight"] = checks
    result["protected_before"] = snapshot_protected()
    if not all(checks.values()):
        result.update(status="FAILED_PREFLIGHT", finished_at=now_iso(),
                      failed_checks=[k for k, v in checks.items() if not v])
        save_result()
        return result
    ledger = load_ledger(ledger_path, opts.run_id)
    result["ledger_run_before"] = next(r for r in ledger["runs"] if r["run"] == opts.run_id)

    # ---------------- install config + launch ----------------
    seed = opts.seed if opts.seed is not None else int(time.time()) % 100000
    result["seed"] = seed
    result["map_file"] = str(map_path)
    result["ui_map_name"] = opts.ui_map_name
    fp = config.install_observer_config(
        game_dir, map_source=map_path, seed=seed, ai_count=opts.ai_count,
        difficulty=0, credits=10000, unit_count=0, game_speed=1, superweapons=False,
        ui_map_name=opts.ui_map_name)
    result["config"] = {k: v for k, v in fp.items() if k != "spawn_ini_text"}
    (run_dir / "config").mkdir(exist_ok=True)
    (run_dir / "config" / "spawn.ini").write_text(fp["spawn_ini_text"], encoding="utf-8")
    (run_dir / "config" / "spawnmap.ini").write_bytes(map_path.read_bytes())

    # full static map data (raw map file + complete section index + declared metadata)
    try:
        raw_dir.mkdir(exist_ok=True)
        result["map_static"] = {
            "raw": maptool.copy_raw_map(map_path, raw_dir),
            "meta": maptool.static_map_meta(map_path),
        }
        (raw_dir / "map_static_meta.json").write_text(
            json.dumps(result["map_static"]["meta"], ensure_ascii=False, indent=2),
            encoding="utf-8")
        result["map_static"]["meta_file"] = str(raw_dir / "map_static_meta.json")
    except Exception as exc:  # noqa: BLE001
        result["map_static_error"] = f"{type(exc).__name__}: {exc}"
    _patch_aiohttp_message_size()

    proc = batch.launch_hidden(game_dir,run_dir)
    launch_t = time.time()
    result["game_pid"] = proc.pid
    result["launch_at"] = now_iso()
    print(f"[observer] launched pid={proc.pid} game_dir={game_dir}", flush=True)

    from ra2yrproto import ra2yr

    client = None
    controller = None
    try:
        save_result()
        controller = batch.enable_headless(proc,run_dir)
        result["render_off"] = True
        client, err = await wait_for_bridge(opts.port, opts.launch_timeout_seconds)
        if client is None:
            result.update(status="FAILED_BRIDGE_CONNECT", error=err, finished_at=now_iso())
            return result
        result["bridge_connected_at"] = now_iso()

        # wait for INGAME
        state = None
        deadline = time.monotonic() + opts.launch_timeout_seconds
        while time.monotonic() < deadline:
            state, err = await get_state(client)
            if state is not None and int(state.stage) == STAGE_INGAME and int(state.current_frame) > 0:
                break
            state = None
            await asyncio.sleep(1.0)
        if state is None:
            result.update(status="FAILED_NO_INGAME",
                          error=f"stage never INGAME within {opts.launch_timeout_seconds}s",
                          finished_at=now_iso())
            return result
        result["ingame_at"] = now_iso()
        result["ingame_wait_seconds"] = round(time.time() - launch_t, 2)

        # initial catalogue + map + observer detection
        type_names: dict[int, str] = {}
        try:
            initial = await read_initial(client)
            if initial is not None:
                type_names = {int(t.pointer_self): str(t.name) for t in initial.object_types}
                catalogue = []
                for t in initial.object_types:
                    try:
                        catalogue.append({
                            "pointer": int(t.pointer_self), "name": str(t.name),
                            "cost": int(getattr(t, "cost", 0)),
                            "strength": int(getattr(t, "strength", 0)),
                            "armor_type": int(getattr(t, "armor_type", 0)),
                            "type": int(getattr(t, "type", 0)),
                            "array_index": int(getattr(t, "array_index", 0)),
                        })
                    except Exception:  # noqa: BLE001
                        continue
                (raw_dir / "object_types.json").write_text(
                    json.dumps(catalogue, ensure_ascii=False), encoding="utf-8")
                result["object_type_catalogue"] = len(catalogue)
        except Exception as exc:  # noqa: BLE001
            result["initial_state_error"] = f"{type(exc).__name__}: {exc}"
        result["object_type_count"] = len(type_names)

        soa = None
        try:
            soa = await read_map_soa(client)
            if soa is not None:
                meta = _save_map_soa(soa, raw_dir / "map_soa.json.gz")
                (raw_dir / "map_soa_meta.json").write_text(
                    json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
                result["map"] = meta
        except Exception as exc:  # noqa: BLE001
            result["map_soa_error"] = f"{type(exc).__name__}: {exc}"

        if soa is None or not type_names:
            raise RuntimeError("Required map/type data unavailable")
        # runtime land-connectivity gate: reject sea/island maps BEFORE collecting
        if soa is not None:
            try:
                starts = _start_cells(state, type_names)
                gate = _ground_connectivity(soa, starts)
                map_gate = {"checked": True, "starts": starts, **gate}
            except Exception as exc:  # noqa: BLE001
                map_gate = {"checked": False, "error": f"{type(exc).__name__}: {exc}"}
            result["map_gate"] = map_gate
            if map_gate.get("checked") and not map_gate.get("all_connected", False):
                result.update(status="FAILED_MAP_NOT_CONNECTED",
                              end_reason="MAP_STARTS_NOT_CONNECTED")
                print(f"[observer] MAP GATE FAILED: {json.dumps(map_gate, ensure_ascii=False)}",
                      flush=True)
                save_result()
                return result
            print(f"[observer] map gate: starts={len(map_gate.get('starts', []))} "
                  f"connected={map_gate.get('all_connected')} "
                  f"water={map_gate.get('water_cells')}", flush=True)

        observers = _observer_flags(state)
        result["observer_candidates"] = observers
        observer_indices = tuple(idx for idx, _self, _human in observers)
        houses0 = [{"array_index": int(h.array_index), "name": str(h.name),
                    "current_player": bool(h.current_player),
                    "is_human_player": bool(h.is_human_player),
                    "defeated": bool(h.defeated), "money": int(h.money)}
                   for h in state.houses]
        result["houses_initial"] = houses0
        ai_houses=[h for h in houses0 if h['name']=='Computer' and not h['current_player']]
        if len(ai_houses)!=7 or len(observers)!=1 or not all(h['money']>2000000 for h in ai_houses):
            raise RuntimeError('Observer/7AI/enhanced-economy validation failed')
        save_result()
        print(f"[observer] INGAME frame={int(state.current_frame)} houses={len(houses0)} "
              f"observers={observers} types={len(type_names)}", flush=True)

        # ---------------- sampling loop ----------------
        states_path = raw_dir / "states.jsonl"
        events_path = raw_dir / "events.jsonl"
        frame0 = int(state.current_frame)
        wall0 = time.time()
        sample_idx = 0
        consecutive_fail = 0
        max_objects = 0
        last_event_buffer_frame = -1
        event_frames_with_events = 0
        event_total = 0
        end_reason = "UNKNOWN"
        with open(states_path, "w", encoding="utf-8") as states_fh, \
                open(events_path, "w", encoding="utf-8") as events_fh:
            while True:
                if pathlib.Path(opts.stop_file).exists():
                    end_reason = 'BATCH_DEADLINE_OR_USER_STOP'
                    break
                state, err = await get_state(client)
                if state is None:
                    if proc.poll() is not None:
                        # the game process itself died (crash/exit), not a bridge glitch
                        end_reason = (f"GAME_PROCESS_EXITED: exit_code={proc.returncode}; "
                                      f"last_err={err}")
                        break
                    consecutive_fail += 1
                    if consecutive_fail >= 5:
                        end_reason = f"STATE_READ_FAILURES: {err}"
                        break
                    await asyncio.sleep(opts.interval)
                    continue
                consecutive_fail = 0
                if int(state.stage) != STAGE_INGAME:
                    end_reason = "GAME_LEFT_INGAME"
                    break
                violations=controller.check_health(int(state.current_frame),True)
                if violations:raise RuntimeError('Headless watchdog: '+str(violations))
                if sample_idx % 20 == 0:controller.assert_patch_intact()
                sample = _state_sample(state, type_names)
                states_fh.write(json.dumps(sample, ensure_ascii=False) + "\n")
                states_fh.flush()
                sample_idx += 1
                max_objects = max(max_objects, len(sample["objects"]))

                # event buffer (600-frame ring; 1 Hz polling cannot lose events)
                try:
                    eb = await read_event_buffer(client)
                    if eb is not None:
                        frames = list(eb.frame)
                        new_frames = [f for f in frames if int(f) > last_event_buffer_frame]
                        if new_frames:
                            last_event_buffer_frame = max(int(f) for f in new_frames)
                            for frame, lists in zip(frames, eb.lists):
                                if int(frame) not in new_frames:
                                    continue
                                record = {
                                    "buffer_frame": int(frame),
                                    "out_count": len(lists.out_list),
                                    "do_count": len(lists.do_list),
                                    "megamission_count": len(lists.megamission_list),
                                    "events": [],
                                }
                                for event in lists.out_list:
                                    record["events"].append(_event_dict(event))
                                for event in lists.megamission_list:
                                    record["events"].append(_event_dict(event))
                                if record["events"]:
                                    event_frames_with_events += 1
                                    event_total += len(record["events"])
                                    events_fh.write(
                                        json.dumps(record, ensure_ascii=False) + "\n")
                            events_fh.flush()
                except Exception:  # noqa: BLE001
                    pass

                wall_elapsed = time.time() - wall0
                fps = (sample["frame"] - frame0) / wall_elapsed if wall_elapsed > 0 else None
                sim_s = (sample["frame"] - frame0) / 62.5
                if sample_idx % 10 == 0:
                    result.update(samples=sample_idx,frame_end=sample['frame'],max_objects=max_objects,
                                  heartbeat=time.time(),fps_estimate=fps,collection_seconds=round(wall_elapsed,2))
                    save_result()
                    print(f"[observer] sample={sample_idx} frame={sample['frame']} "
                          f"objects={len(sample['objects'])} fps={fps:.1f} sim={sim_s:.1f}s",
                          flush=True)
                if opts.sim_seconds and sim_s >= opts.sim_seconds:
                    end_reason = "AGENT_STOP_SIM_LIMIT"
                    break
                if wall_elapsed >= opts.max_wall_seconds:
                    end_reason = "AGENT_STOP_WALL_LIMIT"
                    break
                await asyncio.sleep(opts.interval)

        collected_status='COLLECTED' if end_reason=='AGENT_STOP_SIM_LIMIT' else 'PARTIAL'
        result.update(status=collected_status, end_reason=end_reason, samples=sample_idx,
                      max_objects=max_objects, frame_start=frame0,
                      frame_end=(sample["frame"] if state is not None else None),
                      collection_seconds=round(time.time() - wall0, 2),
                      event_stats={"frames_with_events": event_frames_with_events,
                                   "events_total": event_total,
                                   "note": "AI players do not enqueue network events; "
                                           "an empty event stream in an AI-only game is "
                                           "expected (object missions are the command proxy)"})
    except Exception as exc:  # noqa: BLE001
        result.update(status="FAILED_EXCEPTION", error=f"{type(exc).__name__}: {exc}",
                      traceback=traceback.format_exc()[-3000:])
    finally:
        # ---------------- cleanup ----------------
        try:
            for task in asyncio.all_tasks():
                if task is not asyncio.current_task():
                    task.cancel()
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(0.2)
        try:
            cleanup = batch.cleanup_owned(proc,controller)
            remaining=batch.copy_processes(game_dir)
            cleanup.update(remaining_game_processes=len(remaining),cleanup_ok=not remaining)
        except Exception as exc:
            cleanup={'cleanup_ok':False,'remaining_game_processes':None,'error':str(exc)}
        result["cleanup"] = cleanup
        result["protected_after"] = snapshot_protected()
        result["protected_unchanged"] = (
            result.get("protected_before") == result.get("protected_after"))
        result["finished_at"] = now_iso()

        # ---------------- extract + store ----------------
        states_path = run_dir / "raw" / "states.jsonl"
        try:
            if states_path.is_file() and states_path.stat().st_size > 0 and result.get("samples", 0) >= 2:
                observer_indices = tuple(idx for idx, _s, _h in result.get("observer_candidates", []))
                extracted = extract.extract_recording(
                    states_path, match_id=opts.run_id, map_name=opts.ui_map_name,
                    seed=result.get("seed"), observer_indices=observer_indices)
                artifacts = extract.write_extract_artifacts(extracted, run_dir / "extract")
                db = store.connect(run_dir / "observer.sqlite")
                try:
                    store.insert_match(db, extracted)
                    csv_files = store.export_csv(db, run_dir / "csv")
                finally:
                    db.close()
                (run_dir / "MATCH_REPORT.md").write_text(
                    store.match_report(extracted), encoding="utf-8")
                result["extract"] = {
                    "artifacts": artifacts,
                    "csv": csv_files,
                    "participants": extracted["participants"],
                    "winner": extracted["winner_name"],
                    "end_reason_extracted": extracted["end_reason"],
                    "first_defeated": extracted["first_defeated_name"],
                    "first_defeat_frame": extracted["first_defeat_frame"],
                    "sample_count": extracted["sample_count"],
                    "fps_estimate": extracted["fps_estimate"],
                }
                # combat sanity proxy (a data-collection smoke must show fighting, not a
                # stalemate between unreachable islands)
                result["combat_proxy"] = {
                    "eliminations": len(extracted.get("elimination_order", [])),
                    "first_defeat_frame": extracted.get("first_defeat_frame"),
                    "offensive_samples": {str(row["house_index"]): row.get("offensive_samples")
                                          for row in extracted.get("houses", [])},
                    "peak_units": {str(row["house_index"]): row.get("peak_units")
                                   for row in extracted.get("houses", [])},
                    "fps_estimate": extracted.get("fps_estimate"),
                }
        except Exception as exc:  # noqa: BLE001
            result["extract_error"] = f"{type(exc).__name__}: {exc}"

        # ---------------- ledger + result ----------------
        try:
            ledger = load_ledger(ledger_path, opts.run_id) if ledger_path.is_file() else {}
            if ledger:
                final_status = ("CONSUMED_FINISHED"
                                if result.get("status") in ("COLLECTED",)
                                else "CONSUMED_FAILED")
                update_ledger(ledger_path, ledger, opts.run_id, status=final_status,
                              consumed_at=now_iso(), result=str(result_path),
                              actual_game_processes=1,
                              end_reason=result.get("end_reason"),
                              samples=result.get("samples"))
        except Exception as exc:  # noqa: BLE001
            result["ledger_update_error"] = f"{type(exc).__name__}: {exc}"
        save_result()
    return result


def shutil_disk_free(drive: str) -> float:
    import shutil

    return shutil.disk_usage(drive + "\\").free / (1 << 30)


def _event_dict(event) -> dict:
    try:
        from google.protobuf.json_format import MessageToDict

        data = MessageToDict(event, preserving_proto_field_name=True)
        return data
    except Exception:  # noqa: BLE001
        return {"event_type": int(getattr(event, "event_type", -1)),
                "frame": int(getattr(event, "frame", -1)),
                "house_index": int(getattr(event, "house_index", -1))}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-dir", required=True)
    parser.add_argument("--port",type=int,required=True)
    parser.add_argument("--stop-file",required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--task-id", default="TASK-20261103-006")
    parser.add_argument("--ai-count", type=int, default=2)
    parser.add_argument("--map-file", default=str(config.DEFAULT_MAP))
    parser.add_argument("--ui-map-name", default="8_valley_of_the_exiles")
    parser.add_argument("--sim-seconds", type=float, default=120.0)
    parser.add_argument("--max-wall-seconds", type=float, default=180.0)
    parser.add_argument("--launch-timeout-seconds", type=float, default=120.0)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=None)
    opts = parser.parse_args(argv)
    result = asyncio.run(collect(opts))
    print(json.dumps({k: result.get(k) for k in (
        "run_id", "status", "end_reason", "samples", "max_objects", "game_pid",
        "game_dir", "seed", "cleanup", "protected_unchanged", "extract")},
        ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("status") in ("COLLECTED",) else 2


if __name__ == "__main__":
    raise SystemExit(main())
