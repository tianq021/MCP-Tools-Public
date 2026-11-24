"""DSH visibility recon probe (read-only).

Reads, in one session:
  * GetGameState  (frame, houses, objects with coords/health/owner)
  * ReadValue map_data       (full Cell array; shrouded from CellClass::IsShrouded)
  * ReadValue map_data_soa   (SoA copy of the same array)
  * ReadValue initial_game_state (object type names)

Then cross-checks:
  - map_data.shrouded vs cells_difference.shrouded semantics at the same cells
  - every object's current cell vs shrouded flag (are enemy objects listed in
    still-shrouded cells? that would be a maphack signal)
  - ASCII shroud map of the current player's mapped area

Output: JSON on stdout or -o path.  Never sends a write command.
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
from ra2yrproto import commands_yr, ra2yr  # noqa: E402
from google.protobuf.json_format import MessageToDict  # noqa: E402


def cell_xy(xyz, map_width):
    """Lepton coords -> flat cell index (same order as proto cells)."""
    cx = int(xyz.get("x", 0)) // 256
    cy = int(xyz.get("y", 0)) // 256
    return cx, cy, cy * map_width + cx


def ascii_shroud(cells, w, h, step=2):
    rows = []
    for y in range(0, h, step):
        line = []
        for x in range(0, w, step):
            idx = y * w + x
            line.append("#" if (idx < len(cells) and cells[idx].shrouded) else ".")
        rows.append("".join(line))
    return rows


async def main(opts):
    out = {"script": "dsh_visibility_probe.py",
           "started_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    client = DualClient("127.0.0.1", opts.port, timeout=2)
    client.connect()

    async def send(cmd, unpack_into):
        r = await asyncio.wait_for(client.exec_command(cmd, timeout=3), 6)
        code = getattr(r, "result_code", 0)
        if code != 0:
            raise RuntimeError(f"native_error: {getattr(r, 'error_message', '')}")
        msg = unpack_into()
        if not r.result.Unpack(msg):
            raise RuntimeError("unpack_failed")
        return msg

    try:
        st = (await send(commands_yr.GetGameState(), commands_yr.GetGameState)).state
        out["frame"] = st.current_frame
        out["stage"] = ra2yr.LoadStage.Name(st.stage)
        houses = {h.self: h for h in st.houses}
        out["houses"] = [{"self": h.self, "name": h.name, "faction": h.faction,
                          "current_player": h.current_player,
                          "is_human": h.is_human_player, "money": h.money,
                          "defeated": h.defeated} for h in st.houses]

        initq = commands_yr.ReadValue()
        initq.data.initial_game_state.SetInParent()
        initial = (await send(initq, commands_yr.ReadValue)).data.initial_game_state
        types = {t.pointer_self: t.name for t in initial.object_types}
        out["initial_types"] = len(types)

        mdq = commands_yr.ReadValue()
        mdq.data.map_data.SetInParent()
        md = (await send(mdq, commands_yr.ReadValue)).data.map_data
        soa = commands_yr.ReadValue()
        soa.data.map_data_soa.SetInParent()
        soa = (await send(soa, commands_yr.ReadValue)).data.map_data_soa

        w, h = int(md.width), int(md.height)
        out["map_data"] = {"width": w, "height": h, "cells": len(md.cells)}
        out["map_data_soa"] = {"width": int(soa.map_width), "height": int(soa.map_height),
                               "land_type": len(soa.land_type),
                               "shrouded": len(soa.shrouded)}
        if w == 0 or h == 0:
            out["error"] = "map_data empty"
            return out

        cells = list(md.cells)
        n_sh = sum(1 for c in cells if c.shrouded)
        out["shrouded_true"] = n_sh
        out["shrouded_false"] = len(cells) - n_sh
        out["shrouded_ratio"] = round(n_sh / max(1, len(cells)), 4)
        out["ascii_shroud"] = ascii_shroud(cells, w, h, step=opts.ascii_step)
        out["soa_shrouded_matches_map_data"] = (
            len(soa.shrouded) == len(cells) and
            all(bool(soa.shrouded[i]) == bool(cells[i].shrouded) for i in range(len(cells))))

        # cells_difference merged by index (only changed cells since last frame)
        diff = {c.index: c for c in st.cells_difference}
        out["cells_difference_count"] = len(diff)
        mism = []
        for idx, c in diff.items():
            if 0 <= idx < len(cells) and idx in diff:
                if bool(c.shrouded) != bool(cells[idx].shrouded):
                    mism.append({"index": idx, "diff_shrouded": bool(c.shrouded),
                                 "map_shrouded": bool(cells[idx].shrouded)})
        out["diff_vs_map_shrouded_mismatches"] = mism[:40]
        out["diff_vs_map_mismatch_count"] = len(mism)

        # objects vs shroud
        objs = []
        for o in st.objects:
            if o.health <= 0 or o.in_limbo:
                continue
            owner = houses.get(o.pointer_house)
            xyz = {"x": o.coordinates.x, "y": o.coordinates.y}
            cx, cy, flat = cell_xy(xyz, w)
            in_map = 0 <= cx < w and 0 <= cy < h
            orec = {
                "addr": o.pointer_self,
                "owner": owner.name if owner else "?",
                "is_human": bool(owner.is_human_player) if owner else None,
                "type": types.get(o.pointer_technotypeclass, "?"),
                "abstract": ra2yr.AbstractType.Name(o.object_type),
                "health": o.health,
                "xyz": [xyz["x"], xyz["y"]],
                "cell": [cx, cy],
                "cell_shrouded": bool(cells[flat].shrouded) if in_map else None,
                "mission": ra2yr.Mission.Name(o.current_mission),
            }
            objs.append(orec)
        out["objects"] = objs
        out["objects_by_owner"] = {}
        for o in objs:
            k = o["owner"]
            out["objects_by_owner"][k] = out["objects_by_owner"].get(k, 0) + 1
        enemy_in_shroud = [o for o in objs if not o["is_human"] and o["cell_shrouded"]]
        out["non_human_objects_in_shrouded_cells"] = len(enemy_in_shroud)
        out["non_human_objects_in_shrouded_sample"] = enemy_in_shroud[:15]
        human_in_shroud = [o for o in objs if o["is_human"] and o["cell_shrouded"]]
        out["human_objects_in_shrouded_cells"] = len(human_in_shroud)
        return out
    finally:
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=14521)
    ap.add_argument("--ascii-step", type=int, default=2)
    ap.add_argument("-o", "--output")
    opts = ap.parse_args()
    result = asyncio.run(main(opts))
    text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if opts.output:
        pathlib.Path(opts.output).write_text(text, encoding="utf-8")
        print(f"wrote {opts.output} ({len(text)} bytes)")
    else:
        print(text)
