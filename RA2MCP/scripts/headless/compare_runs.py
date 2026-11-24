"""Cross-run comparison for the headless-window experiments (phases 2-3).

Use after analyze.py has produced one analysis JSON per run, or directly on run
directories.  Groups runs by label (visible / hidden / dual ...) and emits a
comparison table + JSON for the final report.

Example:
  .venv\\Scripts\\python.exe scripts\\headless\\compare_runs.py \
      --run visible=phase2-visible-01 --run hidden=phase2-hidden-01 \
      --run dual=phase3-dual-01 --out reports/headless/compare-phase23
"""
import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "headless"))

from analyze import analyze_run, find_runs  # noqa: E402

HEADLESS_ROOT = ROOT / "reports" / "headless"

# metrics pulled from the primary phase (the phase carrying the most steps)
METRICS = [
    "game_fps", "steps", "steps_per_s_step_window", "achieved_steps_per_s",
    "read_ok", "read_fail", "read_latency_ms_p50", "read_latency_ms_p95",
    "orders_ok", "invalid_orders", "stall_steps",
    "damage_to_enemy", "damage_to_own", "enemy_destroyed", "own_lost",
    "moved_cells", "own_placed_buildings_first", "own_placed_buildings_last",
    "own_units_first", "own_units_last", "enemy_buildings_last",
    "reward_total_phase", "cpu_cores", "cpu_cores_avg_window",
    "working_set_mb_avg", "private_mb_avg", "gpu_util_avg", "gpu_3d_avg",
    "vram_mb_avg", "ambient_nv_util_avg",
]


def primary_phase(analysis):
    phases = [p for p in analysis["phases"] if p["name"] in ("visible", "hidden", "show")]
    if not phases:
        phases = analysis["phases"]
    return max(phases, key=lambda p: p.get("total_steps") or 0) if phases else None


def primary_phase_last_wins(analysis):
    """For full-episode runs the interesting phase is visible or hidden."""
    for name in ("hidden", "visible"):
        for p in analysis["phases"]:
            if p["name"] == name:
                return p
    return primary_phase(analysis)


def run_features(analysis):
    phase = primary_phase_last_wins(analysis)
    entry = {"run_id": analysis["run_id"], "phase": phase["name"] if phase else None,
             "window": phase["window"] if phase else None}
    idx = "0"
    inst = (phase.get("instances") or {}).get(idx, {}) if phase else {}
    for metric in METRICS:
        entry[metric] = inst.get(metric)
    sess = (analysis.get("sessions") or [{}])[0]
    entry["launch_to_ingame_s"] = sess.get("launch_to_ingame_s")
    entry["outcome"] = sess.get("outcome")
    entry["exit_code"] = sess.get("exit_code")
    entry["reward_total"] = sess.get("reward_total")
    entry["checks"] = analysis.get("checks")
    # elapsed real game duration: from first/last step timestamps if present
    if phase:
        entry["phase_wall_seconds"] = phase.get("wall_seconds")
    entry["total_steps_all_phases"] = sum(
        (p.get("total_steps") or 0) for p in analysis["phases"])
    return entry


def build_comparison(labeled_runs):
    comparison = {"runs": {}, "labels": []}
    for label, run_dir in labeled_runs:
        analysis = analyze_run(run_dir)
        comparison["runs"][label] = run_features(analysis)
        comparison["labels"].append(label)
    # derived comparisons
    labels = comparison["labels"]
    derived = {}
    base = comparison["runs"].get("visible")
    for label in labels:
        run = comparison["runs"][label]
        if label == "visible" or base is None:
            continue
        row = {}
        for metric in METRICS:
            a, b = base.get(metric), run.get(metric)
            if isinstance(a, (int, float)) and isinstance(b, (int, float)) and a:
                row[metric + "_pct_change"] = round((b - a) / a * 100.0, 3)
        # throughput of the whole run (steps/s over the game session)
        if run.get("total_steps_all_phases") and run.get("phase_wall_seconds"):
            row["run_steps_per_s"] = round(
                run["total_steps_all_phases"] /
                max(0.001, sum(p.get("wall_seconds") or 0
                               for p in analyze_run(run_dir)["phases"])), 3)
        derived[label] = row
    comparison["derived_vs_visible"] = derived
    return comparison


def render_markdown(comparison):
    lines = ["# 多 run 对比（自动生成）", ""]
    labels = comparison["labels"]
    header = ["指标"] + labels
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "---|" * len(header))
    for metric in ["phase", "game_fps", "steps", "total_steps_all_phases",
                   "steps_per_s_step_window", "read_ok", "read_fail",
                   "read_latency_ms_p50", "orders_ok", "invalid_orders",
                   "stall_steps", "damage_to_enemy", "damage_to_own",
                   "moved_cells", "enemy_buildings_last", "own_units_last",
                   "reward_total_phase", "cpu_cores", "gpu_util_avg",
                   "gpu_3d_avg", "vram_mb_avg", "working_set_mb_avg",
                   "launch_to_ingame_s", "outcome", "phase_wall_seconds"]:
        row = [metric]
        for label in labels:
            row.append(str(comparison["runs"][label].get(metric)))
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")
    if comparison.get("derived_vs_visible"):
        lines.append("## 相对 visible 的变化 (%)")
        lines.append("")
        lines.append("| run | " + " | ".join(
            m.replace("_pct_change", "") for m in
            next(iter(comparison["derived_vs_visible"].values()), {})) + " |")
        lines.append("|---|" + "---|" * len(
            next(iter(comparison["derived_vs_visible"].values()), {})))
        for label, row in comparison["derived_vs_visible"].items():
            lines.append(f"| {label} | " + " | ".join(
                f"{row.get(k)}" for k in row) + " |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="append", required=True,
                    help="label=run-id-or-path (repeatable)")
    ap.add_argument("--out", default=None, help="output base path (without suffix)")
    args = ap.parse_args()
    labeled = []
    for item in args.run:
        label, _, ident = item.partition("=")
        if not ident:
            raise SystemExit(f"expected label=run, got {item!r}")
        dirs = find_runs([ident])
        labeled.append((label, dirs[0]))
    comparison = build_comparison(labeled)
    out = pathlib.Path(args.out) if args.out else HEADLESS_ROOT / "comparison"
    out.parent.mkdir(parents=True, exist_ok=True)
    (out.with_suffix(".json")).write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")
    md = render_markdown(comparison)
    (out.with_suffix(".md")).write_text(md, encoding="utf-8")
    print(md)
    print(f"\nwritten: {out.with_suffix('.json')}\n         {out.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
