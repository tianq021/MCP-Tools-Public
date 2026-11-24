"""Read-only recorder for the 无敌之师 "God's Toy" AI behaviour.

While you play/observe a God's Toy match, this tool continuously reads the full
game state through the loopback bridge and saves a compact timeline for every
house (human or AI): buildings with cells, unit counts, money, power, plus any
game events the bridge exposes. It never sends a mutating command.

Usage (game must already be running with the bridge on the given port):
  python scripts/ai_teacher/record_ai_states.py --port 14521 --duration 900 \
      --out reports/ai-teacher/wdzs-godstoy/live/<stamp>

Typical flow:
  1. start the mod (test-game-wdzs copy) and begin a "God's Toy" match;
  2. run this recorder while the AI builds and attacks;
  3. send me the output folder; I will extract build orders, unit compositions,
     attack timings and key strategy parameters.
"""
import argparse
import asyncio
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "vendor" / "pyra2yr"))

STAGE_INGAME = 2


def _house_dict(h):
    return {
        "array_index": int(h.array_index),
        "self": int(h.self),
        "name": h.name,
        "faction": h.faction,
        "current_player": bool(h.current_player),
        "is_human_player": bool(h.is_human_player),
        "money": int(h.money),
        "power_output": int(h.power_output),
        "power_drain": int(h.power_drain),
        "defeated": bool(h.defeated),
        "is_loser": bool(h.is_loser),
        "is_winner": bool(h.is_winner),
        "type_array_index": int(h.type_array_index),
    }


def _events(state, name):
    """Return [(event_type, frame)] for a bridge event list, if exposed."""
    try:
        entries = getattr(state, name)
    except AttributeError:
        return None
    out = []
    for e in entries:
        out.append([int(getattr(e, "event_type", -1)),
                    int(getattr(e, "frame", -1)),
                    int(getattr(e, "house_index", -1))])
    return out


def _snapshot(state, type_names):
    houses = [_house_dict(h) for h in state.houses]
    by_self = {h["self"]: h for h in houses}
    per_owner = {}
    for o in state.objects:
        owner = by_self.get(int(o.pointer_house))
        key = owner["array_index"] if owner else -1
        slot = per_owner.setdefault(key, {"units": {}, "buildings": [], "total": 0})
        slot["total"] += 1
        name = type_names.get(int(o.pointer_technotypeclass), "?")
        if o.on_map and o.coordinates and (o.coordinates.x or o.coordinates.y):
            # anything with a map cell at a non-zero position and health is a placed object
            if not getattr(o, "in_limbo", False):
                slot["buildings"].append({
                    "type": name,
                    "cell": [int(o.coordinates.x) // 256, int(o.coordinates.y) // 256],
                    "health": int(o.health),
                })
        count = slot["units"].get(name, 0)
        slot["units"][name] = count + 1
    return {
        "frame": int(state.current_frame),
        "stage": int(state.stage),
        "houses": houses,
        "owners": per_owner,
        "events": {
            name: _events(state, name)
            for name in ("out_list", "do_list", "megamission_list")
        },
    }


async def record(port, endpoint, interval, duration, out):
    from pyra2yr.network import DualClient
    from ra2yrproto import commands_yr, ra2yr

    out.mkdir(parents=True, exist_ok=True)
    states_path = out / "states.jsonl"
    client = DualClient(endpoint, port, timeout=2.0)
    client.connect()
    type_names = {}
    started = time.time()
    samples = 0
    meta = {"endpoint": endpoint, "port": port, "interval": interval,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    try:
        with states_path.open("w", encoding="utf-8") as fh:
            while time.time() - started < duration:
                cmd = commands_yr.GetGameState()
                result = await asyncio.wait_for(
                    client.exec_command(cmd, timeout=5.0), 8.0)
                if not result.result.Unpack(cmd):
                    continue
                state = cmd.state
                if state is None or (not state.houses and not state.objects):
                    await asyncio.sleep(interval)
                    continue
                for t in state.object_types:
                    type_names[int(t.pointer_self)] = t.name
                snap = _snapshot(state, type_names)
                snap["wall"] = round(time.time(), 3)
                fh.write(json.dumps(snap, ensure_ascii=False) + "\n")
                fh.flush()
                samples += 1
                if samples % 20 == 0:
                    print(f"[record] frame={snap['frame']} samples={samples}", flush=True)
                await asyncio.sleep(interval)
    finally:
        try:
            tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        except Exception:  # noqa: BLE001
            pass
        meta.update({"finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                     "samples": samples})
        (out / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False),
                                       encoding="utf-8")
        print(f"[record] done: {samples} samples -> {states_path}", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--endpoint", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=14521)
    ap.add_argument("--interval", type=float, default=0.5)
    ap.add_argument("--duration", type=float, default=900.0,
                    help="recording length in seconds (default 15 min)")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = pathlib.Path(a.out or (ROOT / "reports/ai-teacher/wdzs-godstoy/live" / stamp))
    try:
        asyncio.run(record(a.port, a.endpoint, a.interval, a.duration, out))
    except KeyboardInterrupt:
        print("[record] interrupted", flush=True)


if __name__ == "__main__":
    main()
