"""DSH fog-of-war reveal experiment (cell level, read + one MOVE order).

Question: what do these fields actually mean?
  * map_data.cells[].shrouded          (native: CellClass::IsShrouded -> MapClass shroud buffer)
  * GetGameState.cells_difference[].shrouded (native: !(AltCellFlags & Mapped))

Method: snapshot full map_data, order the human MCV (or any human unit) to move
to a still-shrouded area, then poll every 0.5 s:
  - full map_data.shrouded (authoritative carried state)
  - cells_difference (per-frame delta with its own shrouded bit)
Record exactly which cells flipped and whether the two bits agree.
Never sends SELL; only UNIT_ACTION_MOVE / UNIT_ACTION_STOP.

Usage: .venv\\Scripts\\python.exe scripts\\dsh_visibility_reveal_test.py -o reports/dsh-visibility-reveal.json
"""
import argparse
import asyncio
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vendor" / "pyra2yr"))

from pyra2yr.network import DualClient  # noqa: E402
from ra2yrproto import commands_game, commands_yr, ra2yr  # noqa: E402

CELL = 256


async def main(opts):
    out = {"script": "dsh_visibility_reveal_test.py",
           "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "samples": [],
           "orders": [], "map": {}}
    c = DualClient("127.0.0.1", opts.port, timeout=2)
    c.connect()

    async def send(cmd, into, timeout=5.0):
        r = await asyncio.wait_for(c.exec_command(cmd, timeout=timeout), timeout + 4)
        if getattr(r, "result_code", 0) != 0:
            return None, f"native_error: {getattr(r, 'error_message', '')}"
        msg = into()
        if not r.result.Unpack(msg):
            return None, "unpack_failed"
        return msg, None

    async def state():
        msg, err = await send(commands_yr.GetGameState(), commands_yr.GetGameState)
        if err:
            raise RuntimeError(f"GetGameState: {err}")
        return msg.state

    async def map_data():
        q = commands_yr.ReadValue()
        q.data.map_data.SetInParent()
        msg, err = await send(q, commands_yr.ReadValue, timeout=10)
        if err:
            raise RuntimeError(f"ReadValue: {err}")
        return msg.data.map_data

    def w_of(md):
        return int(md.width)

    def flags(md):
        return [bool(cell.shrouded) for cell in md.cells]

    try:
        md0 = await map_data()
        w = w_of(md0)
        base = flags(md0)
        out["map"]["width"] = w
        out["map"]["height"] = int(md0.height)
        out["map"]["cells"] = len(md0.cells)
        out["map"]["shrouded_true"] = sum(base)
        out["map"]["shrouded_false"] = len(base) - sum(base)

        st = await state()
        human = next(h for h in st.houses if h.current_player and h.is_human_player)
        movable = [o for o in st.objects
                   if o.pointer_house == human.self and o.health > 0 and o.on_map
                   and ra2yr.AbstractType.Name(o.object_type) in
                   ("ABSTRACT_TYPE_UNIT", "ABSTRACT_TYPE_INFANTRY")]
        if not movable:
            out["error"] = "no human movable object"
            return out
        mover = min(movable, key=lambda o: (o.coordinates.x ** 2 + o.coordinates.y ** 2))
        start_xyz = [mover.coordinates.x, mover.coordinates.y]
        start_cell = [start_xyz[0] // CELL, start_xyz[1] // CELL]
        out["mover"] = {"addr": mover.pointer_self,
                        "type": ra2yr.AbstractType.Name(mover.object_type),
                        "start_xyz": start_xyz, "start_cell": start_cell,
                        "start_cell_shrouded": base[start_cell[1] * w + start_cell[0]]}

        # pick destination: 10 cells towards map centre from the mover
        cx, cy = start_cell
        tx, ty = w // 2, (int(md0.height) // 2)
        dx = 0 if abs(tx - cx) < 4 else (1 if tx > cx else -1)
        dy = 10 if ty > cy else -10
        dest_cell = (cx + dx * 0, cy + dy)
        dest_xyz = [dest_cell[0] * CELL + 128, dest_cell[1] * CELL + 128]
        out["destination"] = {"cell": list(dest_cell), "xyz": dest_xyz,
                              "dest_cell_shrouded": base[dest_cell[1] * w + dest_cell[0]]}

        q = commands_game.UnitOrder(object_addresses=[mover.pointer_self],
                                    action=ra2yr.UNIT_ACTION_MOVE)
        q.coordinates.x, q.coordinates.y = dest_xyz
        t0 = time.monotonic()
        msg, err = await send(q, commands_game.UnitOrder)
        out["orders"].append({"action": "MOVE", "ack_ms": round((time.monotonic() - t0) * 1000, 2),
                              "error": err, "dest_xyz": dest_xyz})

        prev = base
        total_diff_seen = 0
        diff_shroud_agree = 0
        diff_shroud_disagree = 0
        diff_examples = []
        revealed_ever = set()
        last_md = md0
        t_end = time.monotonic() + opts.watch_seconds
        while time.monotonic() < t_end:
            await asyncio.sleep(opts.interval)
            st = await state()
            cur_obj = next((o for o in st.objects if o.pointer_self == mover.pointer_self), None)
            last_md = await map_data()
            cur = flags(last_md)
            flipped = [i for i in range(len(cur)) if cur[i] != prev[i]]
            for i in flipped:
                revealed_ever.add(i)
            # cells_difference uses its own bit (!AltFlags.Mapped)
            diffs = list(st.cells_difference)
            total_diff_seen += len(diffs)
            for d in diffs:
                i = d.index
                if 0 <= i < len(cur):
                    if bool(d.shrouded) == bool(cur[i]):
                        diff_shroud_agree += 1
                    else:
                        diff_shroud_disagree += 1
                        if len(diff_examples) < 20:
                            diff_examples.append({"index": i,
                                                  "diff_shrouded": bool(d.shrouded),
                                                  "map_shrouded": bool(cur[i])})
            out["samples"].append({
                "wall": time.strftime("%H:%M:%S"),
                "frame": st.current_frame,
                "mover_xyz": None if cur_obj is None else [cur_obj.coordinates.x,
                                                           cur_obj.coordinates.y],
                "shrouded_true": sum(cur),
                "flipped_vs_prev": len(flipped),
                "cells_difference": len(diffs),
                "diff_vs_map_agree": diff_shroud_agree,
                "diff_vs_map_disagree": diff_shroud_disagree,
            })
            prev = cur
            if not opts.wait_full and cur_obj is not None and \
                    cur_obj.current_mission not in (ra2yr.Mission.Mission_Move,):
                # mover stopped
                if len(out["samples"]) >= 3:
                    break

        final = prev
        out["result"] = {
            "frame_end": (await state()).current_frame,
            "mover_final": None if cur_obj is None else [cur_obj.coordinates.x,
                                                         cur_obj.coordinates.y],
            "shrouded_true_start": sum(base),
            "shrouded_true_end": sum(final),
            "newly_revealed_cells": len(revealed_ever),
            "newly_revealed_sample": [{"index": i, "cell": [i % w, i // w]} for i in
                                      sorted(revealed_ever)[:20]],
            "cells_difference_entries_seen": total_diff_seen,
            "diff_vs_map_shrouded_agree": diff_shroud_agree,
            "diff_vs_map_shrouded_disagree": diff_shroud_disagree,
            "diff_mismatch_examples": diff_examples,
        }
        # was the mover's own cell unshrouded throughout?
        return out
    finally:
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=14521)
    ap.add_argument("--watch-seconds", type=float, default=12.0)
    ap.add_argument("--interval", type=float, default=0.5)
    ap.add_argument("--wait-full", action="store_true")
    ap.add_argument("-o", "--output", type=pathlib.Path,
                    default=ROOT / "reports" / "dsh-visibility-reveal.json")
    a = ap.parse_args()
    res = asyncio.run(main(a))
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(res, ensure_ascii=False, indent=2, default=str),
                        encoding="utf-8")
    print("written:", a.output)
    print(json.dumps({k: v for k, v in res.items()
                      if k in ("error", "mover", "destination", "result", "orders")},
                     ensure_ascii=False, indent=2, default=str)[:3000])
