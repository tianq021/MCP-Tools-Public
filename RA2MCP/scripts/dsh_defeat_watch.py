"""Watch an offline skirmish with no commands: detect defeat and record why.

Samples GetGameState once per second and records, for every read: frame, stage,
per-house flags (defeated/is_loser/is_winner), and per-owner object counts.  When
the human house becomes defeated (or the game leaves INGAME) the full final state
is stored and the loop stops.  No game commands are sent (read-only).

Usage:
  .venv\\Scripts\\python.exe scripts\\dsh_defeat_watch.py --label shortgame-No --seconds 120 \
      -o reports/dsh-defeat-watch-No.json
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

report = {"script": "dsh_defeat_watch.py", "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
          "reads": [], "verdict": {}}


async def main(opts):
    client = DualClient("127.0.0.1", opts.port, timeout=2)
    client.connect()

    async def state():
        q = commands_yr.GetGameState()
        try:
            r = await asyncio.wait_for(client.exec_command(q, timeout=3), 6)
        except asyncio.TimeoutError:
            return None, "timeout"
        if getattr(r, "result_code", 0) != 0:
            return None, f"native_error: {getattr(r, 'error_message', '')}"
        if not r.result.Unpack(q):
            return None, "unpack_failed"
        return q.state, None

    st, err = await state()
    if st is None:
        report["fatal_error"] = f"initial state failed: {err}"
        return
    names = {h.self: h.name for h in st.houses}
    human = next((h for h in st.houses if h.current_player and h.is_human_player), None)
    if human is None:
        report["fatal_error"] = "no human player"
        return
    report["start"] = {"frame": st.current_frame, "stage": ra2yr.LoadStage.Name(st.stage),
                       "houses": [{"name": h.name, "self": h.self} for h in st.houses],
                       "objects_by_owner": {names.get(o.pointer_house, "?"):
                                            sum(1 for x in st.objects
                                                if x.pointer_house == o.pointer_house)
                                            for o in st.objects}}

    t0 = time.monotonic()
    sample = 0
    while time.monotonic() - t0 < opts.seconds:
        st, err = await state()
        sample += 1
        rec = {"wall": time.strftime("%H:%M:%S"), "sample": sample}
        if st is None:
            rec["error"] = err
            report["reads"].append(rec)
            await asyncio.sleep(opts.interval)
            continue
        by_owner = {}
        for o in st.objects:
            by_owner[o.pointer_house] = by_owner.get(o.pointer_house, 0) + 1
        rec.update({
            "frame": st.current_frame,
            "stage": ra2yr.LoadStage.Name(st.stage),
            "houses": [{"name": h.name, "defeated": h.defeated,
                        "is_loser": h.is_loser, "is_winner": h.is_winner,
                        "is_game_over": h.is_game_over, "money": h.money}
                       for h in st.houses],
            "objects_by_owner": {names.get(k, str(k)): v for k, v in by_owner.items()},
        })
        hum = next((h for h in st.houses if h.self == human.self), None)
        if hum is not None and (hum.defeated or hum.is_loser or hum.is_game_over):
            rec["human_objects"] = [
                {"addr": o.pointer_self,
                 "abstract": ra2yr.AbstractType.Name(o.object_type),
                 "health": o.health,
                 "mission": ra2yr.Mission.Name(o.current_mission),
                 "xyz": [o.coordinates.x, o.coordinates.y]}
                for o in st.objects if o.pointer_house == human.self]
            report["reads"].append(rec)
            report["end"] = rec
            report["verdict"] = {"defeat_observed": True, "wall": rec["wall"],
                                 "frame": rec["frame"], "stage": rec["stage"],
                                 "human_objects_left": len(rec["human_objects"])}
            break
        if st.stage != 2:
            report["reads"].append(rec)
            report["end"] = rec
            report["verdict"] = {"defeat_observed": False,
                                 "game_left_ingame": True, "stage": rec["stage"],
                                 "wall": rec["wall"]}
            break
        report["reads"].append(rec)
        await asyncio.sleep(opts.interval)
    if not report["verdict"]:
        last = report["reads"][-1] if report["reads"] else {}
        report["verdict"] = {"defeat_observed": False,
                             "game_left_ingame": last.get("stage") != "STAGE_INGAME",
                             "observed_seconds": opts.seconds,
                             "final_stage": last.get("stage"),
                             "final_frame": last.get("frame")}
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="watch")
    ap.add_argument("--seconds", type=float, default=120)
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--port", type=int, default=14521)
    ap.add_argument("-o", "--output", type=pathlib.Path, required=True)
    opts = ap.parse_args()
    report["label"] = opts.label
    try:
        asyncio.run(main(opts))
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        report["fatal_error"] = f"{type(e).__name__}: {e}"
    report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    opts.output.parent.mkdir(parents=True, exist_ok=True)
    opts.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                           encoding="utf-8")
    print(f"label={report['label']} verdict={json.dumps(report['verdict'], ensure_ascii=False)}")
    print(f"written: {opts.output}")
