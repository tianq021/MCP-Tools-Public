"""Read-only verification probe for the local ra2yrcpp bridge.

Performs several consecutive GetGameState reads and checks that the native
snapshot actually advances while the game is running.  It never submits a
mutating command: the only command sent is commands_yr.GetGameState.

Usage:
  .venv\\Scripts\\python.exe scripts\\probe_verify.py [--reads N] [--interval S] [-o out.json]
Exit code 0 only when every read succeeded and the collected state proves a live match.
"""
import argparse
import asyncio
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vendor" / "pyra2yr"))

STAGE_INGAME = 2


def _house_dict(h):
    return {
        "array_index": h.array_index,
        "name": h.name,
        "faction": h.faction,
        "current_player": h.current_player,
        "is_human_player": h.is_human_player,
        "money": h.money,
        "defeated": h.defeated,
        "type_array_index": h.type_array_index,
        "self": h.self,
    }


def _summarize_state(state):
    from ra2yrproto import ra2yr

    houses = [_house_dict(h) for h in state.houses]
    # Object.pointer_house is the HouseClass address, i.e. House.self in the same snapshot.
    by_pointer = {h["self"]: h for h in houses if h["self"]}

    type_names = {}
    for t in state.object_types:
        type_names[t.pointer_self] = t.name

    owners = {}
    samples = []
    for o in state.objects:
        owner = by_pointer.get(o.pointer_house)
        owner_key = owner["array_index"] if owner else "unknown"
        owners[owner_key] = owners.get(owner_key, 0) + 1
        if len(samples) < 10:
            type_name = type_names.get(o.pointer_technotypeclass)
            if not type_name:
                try:
                    type_name = ra2yr.AbstractType.Name(o.object_type)
                except Exception:  # noqa: BLE001
                    type_name = "?"
            samples.append(
                {
                    "object_array_index": o.array_index,
                    "type": type_name,
                    "owner_house_index": owner_key,
                    "owner_name": owner["name"] if owner else None,
                    "health": o.health,
                    "on_map": o.on_map,
                    "coordinates": {"x": o.coordinates.x, "y": o.coordinates.y},
                }
            )

    return {
        "current_frame": state.current_frame,
        "stage": state.stage,
        "crc": state.crc,
        "tech_level": state.tech_level,
        "house_count": len(houses),
        "houses": houses,
        "object_count": len(state.objects),
        "object_type_count": len(state.object_types),
        "object_count_by_owner": owners,
        "object_sample": samples,
        "map": {
            "width": state.map_data.width if state.map_data else None,
            "height": state.map_data.height if state.map_data else None,
        },
    }


async def collect(reads, interval, endpoint, port, timeout):
    from pyra2yr.network import DualClient
    from ra2yrproto import commands_yr

    client = DualClient(endpoint, port, timeout=timeout)
    client.connect()
    results = []
    try:
        for i in range(reads):
            cmd = commands_yr.GetGameState()
            result = await asyncio.wait_for(
                client.exec_command(cmd, timeout=timeout + 3.0), timeout + 8.0
            )
            if not result.result.Unpack(cmd):
                raise RuntimeError("unexpected native response (GetGameState not echoed)")
            state = cmd.state
            if state is None or (not state.houses and not state.objects):
                raise RuntimeError("empty state; match readiness not established")
            snap = _summarize_state(state)
            snap["read_index"] = i
            snap["read_wall_clock"] = time.time()
            results.append(snap)
            if i + 1 < reads:
                await asyncio.sleep(interval)
    finally:
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return results


def verify(results):
    checks = {}

    frames = [r["current_frame"] for r in results]
    last = results[-1]
    checks["multiple_reads"] = len(results) >= 2
    checks["frame_strictly_increasing"] = all(
        b > a for a, b in zip(frames, frames[1:])
    )
    checks["stage_ingame"] = last["stage"] == STAGE_INGAME
    checks["has_two_or_more_houses"] = last["house_count"] >= 2
    checks["has_objects"] = last["object_count"] > 0

    human_owner_keys = {
        h["array_index"]
        for h in last["houses"]
        if h["current_player"] or h["is_human_player"]
    }
    owned = last["object_count_by_owner"]
    checks["objects_owned_by_human"] = any(k in owned for k in human_owner_keys)
    checks["objects_owned_by_non_human_house"] = any(
        k != "unknown" and k not in human_owner_keys for k in owned
    )
    checks["some_human_house"] = bool(human_owner_keys)

    maps = {(r["map"]["width"], r["map"]["height"]) for r in results}
    checks["all_reads_consistent_map"] = len(maps) == 1

    # Not gating: GetGameState in this native build leaves object_types/map_data empty
    # (they are populated through other requests), so record them as observations only.
    observations = {
        "object_type_count": last["object_type_count"],
        "object_type_names_available": last["object_type_count"] > 0,
        "map_data_size_available": last["map"]["width"] not in (None, 0),
        "object_ownership_available": bool(owned),
    }

    return {
        "ok": all(checks.values()),
        "checks": checks,
        "observations": observations,
        "frames": frames,
        "frame_deltas": [b - a for a, b in zip(frames, frames[1:])],
        "owners_observed": owned,
        "human_owner_house_indices": sorted(human_owner_keys),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reads", type=int, default=3)
    ap.add_argument("--interval", type=float, default=1.5)
    ap.add_argument("--endpoint", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=14521)
    ap.add_argument("--timeout", type=float, default=2.0)
    ap.add_argument("-o", "--output", type=pathlib.Path, default=None)
    args = ap.parse_args()

    out = {
        "probe": "probe_verify.py",
        "mode": "read-only (GetGameState only, no mutating command)",
        "endpoint": args.endpoint,
        "port": args.port,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    try:
        results = asyncio.run(
            collect(args.reads, args.interval, args.endpoint, args.port, args.timeout)
        )
        out["reads"] = results
        out["verification"] = verify(results)
        out["native_state_received"] = True
    except Exception as exc:  # noqa: BLE001 - report any failure, never fake data
        out["native_state_received"] = False
        out["error"] = f"{type(exc).__name__}: {exc}"
        out["verification"] = {"ok": False}

    text = json.dumps(out, ensure_ascii=False, indent=2, default=str)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    return 0 if out.get("verification", {}).get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
