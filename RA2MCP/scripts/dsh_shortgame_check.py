"""ShortGame / no-buildings defeat check.

Samples GameState only (no commands) for a freshly spawned offline skirmish and
records whether the human house is declared defeated while it owns no buildings
and still owns its MCV.  Used for the A/B question:
  * spawn.ini ShortGame=No  -> expected: no defeat while MCV alive
  * spawn.ini ShortGame=Yes -> does "no buildings" alone trigger a loss?

Usage:
  .venv\\Scripts\\python.exe scripts\\dsh_shortgame_check.py --label No --seconds 90 -o reports/dsh-shortgame-No.json
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

TYPE_BUILDING = "ABSTRACT_TYPE_BUILDING"
TYPE_UNIT = "ABSTRACT_TYPE_UNIT"


async def collect(label, seconds, interval, port):
    c = DualClient("127.0.0.1", port, timeout=2)
    c.connect()
    out = {"label": label, "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "samples": [], "defeat_observed": False, "notes": []}

    async def state():
        q = commands_yr.GetGameState()
        try:
            r = await asyncio.wait_for(c.exec_command(q, timeout=3), 6)
        except asyncio.TimeoutError:
            return None
        if getattr(r, "result_code", 0) != 0:
            return None
        if not r.result.Unpack(q):
            return None
        return q.state

    try:
        t0 = time.monotonic()
        while time.monotonic() - t0 < seconds:
            st = await state()
            if st is None:
                out["samples"].append({"wall": time.strftime("%H:%M:%S"),
                                       "error": "no state/timeout"})
                await asyncio.sleep(interval)
                continue
            human = next((h for h in st.houses if h.current_player and h.is_human_player), None)
            blds, units = 0, 0
            if human is not None:
                for o in st.objects:
                    if o.pointer_house != human.self or o.health <= 0:
                        continue
                    k = ra2yr.AbstractType.Name(o.object_type)
                    if k == TYPE_BUILDING:
                        blds += 1
                    elif k == TYPE_UNIT:
                        units += 1
            rec = {
                "wall": time.strftime("%H:%M:%S"),
                "frame": st.current_frame,
                "stage": ra2yr.LoadStage.Name(st.stage),
                "human": None if human is None else {
                    "money": human.money,
                    "defeated": human.defeated,
                    "is_game_over": human.is_game_over,
                    "is_loser": human.is_loser,
                    "is_winner": human.is_winner,
                },
                "human_buildings": blds,
                "human_units": units,
            }
            out["samples"].append(rec)
            defeated = human is not None and (human.defeated or human.is_loser
                                              or human.is_game_over)
            if st.stage != 2 or defeated:
                out["defeat_observed"] = bool(defeated)
                out["end_sample"] = rec
                break
            await asyncio.sleep(interval)
        out["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        out["no_buildings_throughout"] = all(s.get("human_buildings", 0) == 0
                                             for s in out["samples"] if "human" in s)
        out["mcv_alive_throughout"] = all(s.get("human_units", 0) >= 1
                                          for s in out["samples"] if "human" in s)
        out["frames"] = [s.get("frame") for s in out["samples"] if "frame" in s]
    finally:
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="unknown")
    ap.add_argument("--seconds", type=float, default=90)
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--port", type=int, default=14521)
    ap.add_argument("-o", "--output", type=pathlib.Path, required=True)
    a = ap.parse_args()
    res = asyncio.run(collect(a.label, a.seconds, a.interval, a.port))
    text = json.dumps(res, ensure_ascii=False, indent=2, default=str)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(text, encoding="utf-8")
    print(f"written: {a.output}")
    print(f"label={res['label']} samples={len(res['samples'])} "
          f"defeat_observed={res['defeat_observed']} "
          f"no_buildings={res.get('no_buildings_throughout')} "
          f"mcv_alive={res.get('mcv_alive_throughout')}")
