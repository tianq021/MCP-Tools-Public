"""Wait for one realtime/slowmo takeover decision cycle and print a compact summary.

Usage: python wait_cycle.py --run-dir DIR --n N [--timeout 300]
Waits for io/result-<N>.json and io/state-<N+1>.json, then prints compact JSON.
"""
import argparse
import json
import pathlib
import time


def summarize_state(st):
    return {
        "decision": st.get("decision"),
        "frame": st.get("frame"),
        "elapsed_sim_s": st.get("elapsed_sim_s"),
        "elapsed_wall_s": st.get("elapsed_wall_s"),
        "money": st.get("money"),
        "power": st.get("power"),
        "own_counts": st.get("own_counts"),
        "build_state": st.get("build_state"),
        "done_buildings": st.get("done_buildings"),
        "factories": st.get("factories"),
        "enemy_counts": st.get("enemy_counts"),
        "enemies_visible": [
            {"addr": e["addr"], "type": e["type"], "hp": e["hp"], "cell": e["cell"]}
            for e in st.get("enemies_visible", [])][:25],
        "enemy_houses": st.get("enemy_houses"),
        "public_start_cells": st.get("public_start_cells"),
        "own": [
            {"addr": o["addr"], "type": o["type"], "hp": o["hp"], "cell": o["cell"],
             "mission": o["mission"], "in_limbo": o["in_limbo"]}
            for o in st.get("own", [])],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--n", type=int, required=True)
    ap.add_argument("--timeout", type=float, default=300.0)
    a = ap.parse_args()
    io = pathlib.Path(a.run_dir) / "io"
    result_path = io / f"result-{a.n}.json"
    state_path = io / f"state-{a.n + 1}.json"
    t0 = time.monotonic()
    while time.monotonic() - t0 < a.timeout:
        if result_path.exists() and state_path.exists():
            break
        time.sleep(0.3)
    out = {"n": a.n, "elapsed_wait_s": round(time.monotonic() - t0, 1)}
    try:
        out["result"] = json.loads(result_path.read_text(encoding="utf-8-sig"))
    except Exception as exc:  # noqa: BLE001
        out["result"] = {"status": "MISSING", "error": str(exc)}
    try:
        st = json.loads(state_path.read_text(encoding="utf-8-sig"))
        out["state"] = summarize_state(st)
    except Exception as exc:  # noqa: BLE001
        out["state"] = {"status": "MISSING", "error": str(exc)}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
