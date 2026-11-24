"""Build the machine-readable summary of the headless-window feasibility tests.

Reads the individual run result.json / analysis-*.json files produced by
run_experiment.py, phase4_norender.py and phase0_check.py and emits one
consolidated JSON + Markdown table set for the final report.

Usage:
  .venv\\Scripts\\python.exe scripts/headless/build_summary.py
"""
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
H = ROOT / "reports" / "headless"
RUNS = H / "runs"


def load(path):
    try:
        return json.loads(pathlib.Path(path).read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return None


def analysis(run_id):
    return load(H / f"analysis-{run_id}.json")


def phase_table(run_id):
    data = analysis(run_id)
    if not data:
        return []
    out = []
    for run in data.get("runs", []):
        for ph in run.get("phases", []):
            for index, inst in (ph.get("instances") or {}).items():
                out.append({
                    "run_id": run.get("run_id"), "phase": ph.get("name"),
                    "window": ph.get("window"), "instance": index,
                    "wall_seconds": ph.get("wall_seconds"),
                    "steps": inst.get("steps"),
                    "steps_per_s_step_window": inst.get("steps_per_s_step_window"),
                    "game_fps": inst.get("game_fps"),
                    "read_ok": inst.get("read_ok"), "read_fail": inst.get("read_fail"),
                    "read_latency_ms_p50": inst.get("read_latency_ms_p50"),
                    "read_latency_ms_p95": inst.get("read_latency_ms_p95"),
                    "cpu_cores": inst.get("cpu_cores"),
                    "gpu_util_avg": inst.get("gpu_util_avg"),
                    "vram_mb_avg": inst.get("vram_mb_avg"),
                    "working_set_mb_avg": inst.get("working_set_mb_avg"),
                    "orders_ok": inst.get("orders_ok"),
                    "invalid_orders": inst.get("invalid_orders"),
                    "stall_steps": inst.get("stall_steps"),
                    "damage_to_enemy": inst.get("damage_to_enemy"),
                    "damage_to_own": inst.get("damage_to_own"),
                    "moved_cells": inst.get("moved_cells"),
                    "own_placed_buildings": [inst.get("own_placed_buildings_first"),
                                             inst.get("own_placed_buildings_last")],
                    "own_units": [inst.get("own_units_first"),
                                  inst.get("own_units_last")],
                    "enemy_buildings_last": inst.get("enemy_buildings_last"),
                    "reward_total_phase": inst.get("reward_total_phase"),
                    "window_hidden_samples": inst.get("window_hidden_samples"),
                    "window_visible_samples": inst.get("window_visible_samples"),
                    "outcome": inst.get("outcome"),
                    "rpc_benchmarks": inst.get("rpc_benchmarks"),
                })
    return out


def run_result(run_id):
    return load(RUNS / run_id / "result.json")


def terminal_facts():
    """Facts that are not derivable from a single phase table."""
    facts = {}
    # phase 0
    p0 = load(H / "phase0-instance-check.json")
    if p0:
        facts["phase0"] = {
            "conclusion": p0.get("conclusion"),
            "cross_check": p0.get("cross_check"),
            "liveness": {k: {kk: v.get(kk) for kk in
                             ("ingame", "frame_first", "frame_last", "object_counts",
                              "state_changed", "read_ok", "read_fail")}
                         for k, v in (p0.get("liveness") or {}).items()},
            "instances": [{kk: inst.get(kk) for kk in
                           ("index", "game_dir", "port_config", "bridge_config",
                            "mutex_marker")} for inst in p0.get("instances", [])],
        }
    # kill isolation
    kill = run_result("phase3-kill-01")
    if kill:
        facts["kill_one_isolation"] = kill.get("kill_one_test")
    # phase 4 patch runs
    p4 = {}
    for run_id in ("phase4-blit-02", "phase4-tactical-02", "phase4-render-01",
                   "phase4-hidden-render-end-01"):
        res = run_result(run_id)
        if not res:
            continue
        p4[run_id] = {
            "mode_results": res.get("mode_results"),
            "final_targets": res.get("final_targets"),
            "terminal": res.get("terminal"),
            "screenshots": {k: {"identical": v.get("identical"),
                                "changed": v.get("changed")}
                            for k, v in (res.get("screenshots") or {}).items()},
            "errors": res.get("errors"),
        }
    # unsafe first attempts (naive ret stub -> game main thread hang)
    unsafe = {}
    for run_id in ("phase4-03", "phase4-tactical-01"):
        res = run_result(run_id)
        if res:
            unsafe[run_id] = {
                "phases": [{kk: (ph.get("instances") or {}).get("0", {}).get(kk)
                            for kk in ("name", "steps", "game_fps", "read_ok")}
                           for ph in res.get("phases", [])],
                "mode_results": res.get("mode_results"),
                "note": "naive `ret` stub at a function with stack arguments left "
                        "the stack unbalanced; the game main thread hung",
            }
    p4["unsafe_first_attempts"] = unsafe
    facts["phase4"] = p4
    # port collision failure
    diag = H / "diagnostics" / "launch-diag.txt"
    if diag.exists():
        facts["port_collision_failure"] = {
            "file": str(diag),
            "text": diag.read_text(encoding="utf-8", errors="replace")[-1200:],
        }
    # disassembly report
    da = load(H / "disasm-analysis.json")
    if da:
        facts["disassembly"] = {
            "exe": da.get("exe"),
            "image_base": da.get("image_base"),
            "instruction_count": da.get("instruction_count"),
            "targets": da.get("targets"),
            "game_loop_hook": {
                "hook_va": (da.get("game_loop_hook") or {}).get("hook_va"),
                "function_start": (da.get("game_loop_hook") or {}).get("function_start"),
                "calls": (da.get("game_loop_hook") or {}).get("calls"),
            },
            "render_call_sites": {
                hex(t): sites for t, sites in
                (da.get("call_sites") or {}).items()
                if t in ("0x4f4480", "0x4f4780", "0x6d3d10")},
        }
    return facts


def main():
    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "scope": "hidden-window / no-render feasibility for original YR skirmish",
        "runs": {},
        "phases": {},
        "facts": terminal_facts(),
    }
    run_ids = [p.name for p in sorted(RUNS.glob("*")) if (p / "result.json").exists()]
    for run_id in run_ids:
        res = run_result(run_id)
        summary["runs"][run_id] = {
            "config": res.get("config"),
            "plan": res.get("plan"),
            "sessions": [{k: s.get(k) for k in
                          ("label", "index", "game_dir", "port", "pid",
                           "launch_to_window_s", "launch_to_ingame_s",
                           "window", "steps", "read_ok", "read_fail",
                           "read_latency_ms_p50", "read_latency_ms_p95",
                           "orders_ok", "invalid_orders", "stall_events",
                           "rehide_count", "show_repair_count", "window_samples",
                           "outcome", "exit_code", "reward_total")}
                         for s in res.get("sessions", [])],
            "restore": res.get("restore"),
            "config_files": res.get("config_files"),
            "errors": res.get("errors"),
            "monitor_csv": res.get("monitor_csv"),
            "gpu_csv": res.get("gpu_csv"),
        }
        table = phase_table(run_id)
        if table:
            summary["phases"][run_id] = table
    out = H / "headless-feasibility-summary.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str),
                   encoding="utf-8")
    print(f"written: {out}")
    print(f"runs: {len(summary['runs'])}, phase rows: "
          f"{sum(len(v) for v in summary['phases'].values())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
