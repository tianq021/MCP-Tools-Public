"""Tiny helper for the realtime LLM takeover loop.

Usage:
  python decide.py --run-dir DIR --n N [--wait-seconds 180] < decision.json

Writes io/decision-<N>.json atomically, waits for io/result-<N>.json and
io/state-<N+1>.json, then prints a compact result + next-state summary.
The decision JSON is passed through unchanged (the driver validates it).
"""
import argparse
import json
import pathlib
import sys
import time


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--n", type=int, required=True)
    ap.add_argument("--wait-seconds", type=float, default=180.0)
    a = ap.parse_args()
    run = pathlib.Path(a.run_dir)
    io = run / "io"
    io.mkdir(parents=True, exist_ok=True)
    raw = sys.stdin.read()
    decision = json.loads(raw)
    decision["decision"] = a.n
    path = io / f"decision-{a.n}.json"
    tmp = io / f"decision-{a.n}.json.tmp"
    tmp.write_text(json.dumps(decision, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    print(f"[wrote] {path}", flush=True)
    result_path = io / f"result-{a.n}.json"
    state_path = io / f"state-{a.n + 1}.json"
    t0 = time.monotonic()
    while time.monotonic() - t0 < a.wait_seconds:
        if result_path.exists() and state_path.exists():
            break
        time.sleep(0.4)
    out = {}
    if result_path.exists():
        out["result"] = json.loads(result_path.read_text(encoding="utf-8"))
    else:
        out["result"] = {"status": "WAIT_TIMEOUT"}
    if state_path.exists():
        st = json.loads(state_path.read_text(encoding="utf-8"))
        out["state"] = {
            "decision": st.get("decision"),
            "frame": st.get("frame"),
            "elapsed_sim_s": st.get("elapsed_sim_s"),
            "money": st.get("money"),
            "power": st.get("power"),
            "own_counts": st.get("own_counts"),
            "build_state": st.get("build_state"),
            "done_buildings": st.get("done_buildings"),
            "factories": st.get("factories"),
            "enemy_counts": st.get("enemy_counts"),
            "enemies_visible": [
                {"addr": e["addr"], "type": e["type"], "hp": e["hp"], "cell": e["cell"]}
                for e in st.get("enemies_visible", [])][:20],
            "enemy_houses": st.get("enemy_houses"),
            "public_start_cells": st.get("public_start_cells"),
            "own": [
                {"addr": o["addr"], "type": o["type"], "hp": o["hp"],
                 "cell": o["cell"], "mission": o["mission"],
                 "in_limbo": o["in_limbo"]}
                for o in st.get("own", [])],
        }
    else:
        out["state"] = {"status": "STATE_MISSING"}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
