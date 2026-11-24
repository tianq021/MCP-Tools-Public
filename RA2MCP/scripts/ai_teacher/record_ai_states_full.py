"""Full-fidelity read-only recorder: every object of every house per sample.

Unlike record_ai_states.py (compact aggregate), this dump stores each object's
owner, abstract type name, cell, health, mission and limbo flag. Type names are
resolved through the bridge's initial_game_state catalogue.

  python scripts/ai_teacher/record_ai_states_full.py --port 14521 --duration 900
"""
import argparse
import asyncio
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "vendor" / "pyra2yr"))


async def read_initial(client, commands_yr):
    q = commands_yr.ReadValue()
    q.data.initial_game_state.SetInParent()
    r = await asyncio.wait_for(client.exec_command(q, timeout=8), 12)
    msg = commands_yr.ReadValue()
    if not r.result.Unpack(msg):
        return None
    return msg.data.initial_game_state


async def record(port, endpoint, interval, duration, out):
    from pyra2yr.network import DualClient
    from ra2yrproto import commands_yr, ra2yr

    out.mkdir(parents=True, exist_ok=True)
    client = DualClient(endpoint, port, timeout=3.0)
    client.connect()

    type_names = {}
    types_path = out / "object_types.json"
    try:
        initial = await read_initial(client, commands_yr)
        if initial is not None:
            TYPE = {int(t.pointer_self): t.name for t in initial.object_types}
            type_names = TYPE
            types_path.write_text(
                json.dumps([[str(k), v, int(t.type)] for k, v in TYPE.items()
                            for t in initial.object_types if int(t.pointer_self) == k],
                           ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"[full] object type catalogue: {len(TYPE)} entries", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[full] initial state unavailable: {exc}", flush=True)

    states_path = out / "states_full.jsonl"
    started = time.time()
    samples = 0
    with states_path.open("w", encoding="utf-8") as fh:
        while time.time() - started < duration:
            try:
                cmd = commands_yr.GetGameState()
                r = await asyncio.wait_for(client.exec_command(cmd, timeout=5.0), 8.0)
                if not r.result.Unpack(cmd):
                    await asyncio.sleep(interval)
                    continue
                state = cmd.state
                if state is None or not state.houses:
                    await asyncio.sleep(interval)
                    continue
            except Exception as exc:  # noqa: BLE001
                print(f"[full] read failed: {exc}", flush=True)
                await asyncio.sleep(interval)
                continue

            houses = []
            self_to_index = {}
            if not type_names and int(state.current_frame) > 0:
                # The catalogue may not be ready at the main menu; retry in-game.
                try:
                    initial = await read_initial(client, commands_yr)
                    if initial is not None and len(initial.object_types) > 0:
                        type_names = {int(t.pointer_self): t.name for t in initial.object_types}
                        print(f"[full] object type catalogue (retry): {len(type_names)} entries",
                              flush=True)
                except Exception:  # noqa: BLE001
                    pass
            for h in state.houses:
                self_to_index[int(h.self)] = int(h.array_index)
                houses.append([int(h.array_index), h.name, int(h.money),
                               int(h.power_output), int(h.power_drain),
                               bool(h.defeated), bool(h.is_loser), bool(h.is_winner)])
            objects = []
            for o in state.objects:
                owner = self_to_index.get(int(o.pointer_house), -1)
                kind = type_names.get(int(o.pointer_technotypeclass))
                if kind is None:
                    try:
                        kind = "?:" + ra2yr.AbstractType.Name(o.object_type)
                    except Exception:  # noqa: BLE001
                        kind = "?"
                objects.append([
                    owner, kind, int(o.health),
                    int(o.coordinates.x // 256), int(o.coordinates.y // 256),
                    1 if o.on_map else 0, 1 if o.in_limbo else 0,
                    int(o.current_mission),
                ])
            sample = {"frame": int(state.current_frame), "wall": round(time.time(), 3),
                      "houses": houses, "objects": objects}
            fh.write(json.dumps(sample, ensure_ascii=False) + "\n")
            fh.flush()
            samples += 1
            if samples % 20 == 0:
                print(f"[full] frame={sample['frame']} samples={samples} objects={len(objects)}",
                      flush=True)
            await asyncio.sleep(interval)

    meta = {"port": port, "interval": interval, "duration": duration,
            "samples": samples, "type_names": len(type_names),
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    (out / "meta_full.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False),
                                       encoding="utf-8")
    print(f"[full] done: {samples} samples", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=14521)
    ap.add_argument("--interval", type=float, default=0.5)
    ap.add_argument("--duration", type=float, default=900)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = pathlib.Path(a.out or (ROOT / "reports/ai-teacher/wdzs-godstoy/live-full" / stamp))
    try:
        asyncio.run(record(a.port, a.endpoint, a.interval, a.duration, out))
    except KeyboardInterrupt:
        print("[full] interrupted", flush=True)


if __name__ == "__main__":
    main()
