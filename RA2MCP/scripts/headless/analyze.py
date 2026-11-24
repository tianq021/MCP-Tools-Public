"""Analyze headless-window experiment runs: phase metrics, resources, comparison.

Reads result.json + monitor.csv (Get-Process samples) + gpu_counters.csv
(typeperf GPU Engine / GPU Process Memory samples) from one or more run
directories and produces analysis.json / analysis.md.

No game interaction.
"""
import argparse
import csv
import datetime
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
HEADLESS_ROOT = ROOT / "reports" / "headless"


def _to_float(value):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v == v else None  # NaN guard


def parse_timestamp_ms(text):
    text = text.strip()
    if not text:
        return None
    # typeperf CSV: "10/29/2026 18:54:13.342" (local time)
    for fmt in ("%m/%d/%Y %H:%M:%S.%f", "%m/%d/%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            dt = datetime.datetime.strptime(text, fmt)
            return int(dt.timestamp() * 1000)
        except ValueError:
            continue
    return None


def read_process_samples(csv_path):
    """pid -> list of {ts, cpu, ws, private, phase, nv_util}."""
    out = {}
    path = pathlib.Path(csv_path)
    if not path.exists():
        return out
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f):
            try:
                pid = int(row["pid"])
                ts = int(row["ts_ms"])
            except (KeyError, TypeError, ValueError):
                continue
            out.setdefault(pid, []).append({
                "ts": ts,
                "cpu": _to_float(row.get("cpu_seconds")),
                "ws": _to_float(row.get("ws_mb")),
                "private": _to_float(row.get("private_mb")),
                "phase": (row.get("phase") or "").strip(),
                "nv_util": _to_float(row.get("nv_util")),
            })
    for pid in out:
        out[pid].sort(key=lambda x: x["ts"])
    return out


GPU_ENGINE_RE = re.compile(r"GPU Engine\((.*?)\)\\Utilization Percentage")
GPU_MEM_RE = re.compile(r"GPU Process Memory\((.*?)\)\\Dedicated Usage")


def read_gpu_samples(csv_path):
    """[{ts, per_pid: {pid: {util, util3d, vram_mb}}}] from typeperf CSV."""
    path = pathlib.Path(csv_path)
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            return []
        columns = []  # (kind, pid, engtype)
        for col in header[1:]:
            m = GPU_ENGINE_RE.search(col)
            if m:
                instance = m.group(1)
                pm = re.match(r"pid_(\d+)_", instance)
                if not pm:
                    columns.append(None)
                    continue
                engtype = "3d" if instance.lower().endswith("_engtype_3d") else "other"
                columns.append(("engine", int(pm.group(1)), engtype))
                continue
            m = GPU_MEM_RE.search(col)
            if m:
                instance = m.group(1)
                pm = re.match(r"pid_(\d+)_", instance)
                columns.append(("mem", int(pm.group(1)), "vram") if pm else None)
                continue
            columns.append(None)
        samples = []
        for row in reader:
            if not row:
                continue
            ts = parse_timestamp_ms(row[0])
            if ts is None:
                continue
            per_pid = {}
            for i, value in enumerate(row[1:]):
                col = columns[i] if i < len(columns) else None
                if col is None:
                    continue
                v = _to_float(value)
                if v is None:
                    continue
                kind, pid, engtype = col
                entry = per_pid.setdefault(pid, {"util": 0.0, "util3d": 0.0,
                                                 "vram_mb": 0.0})
                if kind == "engine":
                    entry["util"] += v
                    if engtype == "3d":
                        entry["util3d"] += v
                else:
                    entry["vram_mb"] += v / (1024 * 1024)
            samples.append({"ts": ts, "per_pid": per_pid})
    samples.sort(key=lambda x: x["ts"])
    return samples


def _in_window(ts, start, end):
    return start - 1000 <= ts <= end + 1000


def phase_resources(run, pid):
    """Per-phase resource averages for one game pid, aligned with phases[index].

    Duplicate phase names are allowed (ABAB plans repeat `visible`/`hidden`), so
    the result is a list indexed like run["phases"], never a dict keyed by name.
    """
    procs = read_process_samples(run.get("monitor_csv"))
    gpus = read_gpu_samples(run.get("gpu_csv"))
    rows = procs.get(pid, [])
    out = []
    for phase in run.get("phases", []):
        start, end = phase["wall_start_ms"], phase["wall_end_ms"]
        sel = [r for r in rows if _in_window(r["ts"], start, end)]
        cpu = ws = private = nv = None
        if len(sel) >= 2:
            t0, t1 = sel[0]["ts"], sel[-1]["ts"]
            if t1 > t0 and sel[0]["cpu"] is not None and sel[-1]["cpu"] is not None:
                cpu = (sel[-1]["cpu"] - sel[0]["cpu"]) / ((t1 - t0) / 1000.0)
        ws_vals = [r["ws"] for r in sel if r["ws"] is not None]
        priv_vals = [r["private"] for r in sel if r["private"] is not None]
        nv_vals = [r["nv_util"] for r in sel if r["nv_util"] is not None]
        gsel = [s["per_pid"].get(pid) for s in gpus if _in_window(s["ts"], start, end)]
        gsel = [g for g in gsel if g]
        gpu_utils = [g["util"] for g in gsel]
        gpu_3d = [g["util3d"] for g in gsel]
        vram = [g["vram_mb"] for g in gsel]
        out.append({
            "phase_name": phase["name"],
            "phase_window_s": round((end - start) / 1000.0, 2),
            "process_samples": len(sel), "gpu_samples": len(gsel),
            "cpu_cores": round(cpu, 4) if cpu is not None else None,
            "cpu_cores_avg_window": (round(sum(
                (sel[i + 1]["cpu"] - sel[i]["cpu"]) /
                ((sel[i + 1]["ts"] - sel[i]["ts"]) / 1000.0)
                for i in range(len(sel) - 1)
                if sel[i]["cpu"] is not None and sel[i + 1]["cpu"] is not None
                and sel[i + 1]["ts"] > sel[i]["ts"]) /
                max(1, len(sel) - 1), 4)
                if len(sel) >= 2 else None),
            "working_set_mb_avg": round(sum(ws_vals) / len(ws_vals), 2) if ws_vals else None,
            "private_mb_avg": round(sum(priv_vals) / len(priv_vals), 2) if priv_vals else None,
            "private_mb_max": round(max(priv_vals), 2) if priv_vals else None,
            "gpu_util_avg": round(sum(gpu_utils) / len(gpu_utils), 3) if gpu_utils else None,
            "gpu_util_max": round(max(gpu_utils), 3) if gpu_utils else None,
            "gpu_3d_avg": round(sum(gpu_3d) / len(gpu_3d), 3) if gpu_3d else None,
            "vram_mb_avg": round(sum(vram) / len(vram), 2) if vram else None,
            "ambient_nv_util_avg": round(sum(nv_vals) / len(nv_vals), 2) if nv_vals else None,
        })
    return out


def analyze_run(run_dir):
    run_dir = pathlib.Path(run_dir)
    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    analysis = {"run_id": result.get("run_id", run_dir.name),
                "run_dir": str(run_dir),
                "config": result.get("config"),
                "phases": [], "sessions": [], "checks": {}}
    sessions_by_index = {str(s["index"]): s for s in result.get("sessions", [])}
    resources_by_pid = {}
    for index, sess in sessions_by_index.items():
        pid = sess.get("pid")
        if pid:
            resources_by_pid[pid] = phase_resources(result, pid)
    for phase_index, phase in enumerate(result.get("phases", [])):
        entry = {"name": phase["name"], "window": phase["window"],
                 "wall_seconds": phase["wall_seconds"],
                 "total_steps": phase.get("total_steps"),
                 "instances": {}}
        for index, agg in (phase.get("instances") or {}).items():
            sess = sessions_by_index.get(index, {})
            pid = sess.get("pid")
            res_list = resources_by_pid.get(pid) or []
            res = res_list[phase_index] if phase_index < len(res_list) else {}
            item = dict(agg)
            item.update(res)
            entry["instances"][index] = item
        analysis["phases"].append(entry)
    analysis["sessions"] = result.get("sessions", [])
    analysis["screenshots"] = result.get("screenshots", {})
    analysis["restore"] = result.get("restore")
    analysis["monitor"] = {"monitor_csv": result.get("monitor_csv"),
                           "gpu_csv": result.get("gpu_csv"),
                           "duration_s": result.get("monitor_duration_s")}
    analysis["checks"] = build_checks(result, analysis)
    return analysis


def _phase_by_name(analysis, name):
    return next((p for p in analysis["phases"] if p["name"] == name), None)


def _instance_metric(phase, key, index="0"):
    if not phase:
        return None
    return (phase.get("instances") or {}).get(index, {}).get(key)


def build_checks(result, analysis):
    """Explicit pass/fail evidence for the hidden-window feasibility questions."""
    checks = {}
    hidden = _phase_by_name(analysis, "hidden")
    visible = _phase_by_name(analysis, "visible")
    sessions = {str(s["index"]): s for s in result.get("sessions", [])}
    idx = "0"
    h = lambda key: _instance_metric(hidden, key, idx)  # noqa: E731
    v = lambda key: _instance_metric(visible, key, idx)  # noqa: E731
    steps = h("steps") or 0
    read_ok = h("read_ok") or 0
    visibility = None
    if hidden:
        total_samples = (h("window_visible_samples") or 0) + (h("window_hidden_samples") or 0)
        if total_samples:
            visibility = round((h("window_hidden_samples") or 0) / total_samples, 4)
    checks["hidden_phase_present"] = hidden is not None
    checks["hidden_steps"] = steps
    checks["hidden_read_success_rate"] = (round(read_ok / steps, 4) if steps else None)
    checks["hidden_window_hidden_fraction"] = visibility
    checks["hidden_rehide_count"] = h("rehide_count")
    checks["hidden_game_fps"] = h("game_fps")
    checks["hidden_stall_steps"] = h("stall_steps")
    checks["hidden_orders_ok"] = h("orders_ok")
    checks["hidden_invalid_orders"] = h("invalid_orders")
    checks["hidden_own_buildings"] = [h("own_placed_buildings_first"),
                                      h("own_placed_buildings_last")]
    checks["hidden_own_units"] = [h("own_units_first"), h("own_units_last")]
    checks["hidden_enemy_buildings"] = [h("enemy_buildings_first"),
                                        h("enemy_buildings_last")]
    checks["hidden_enemy_units"] = [h("enemy_units_first"), h("enemy_units_last")]
    checks["hidden_moved_cells"] = h("moved_cells")
    checks["hidden_damage_to_enemy"] = h("damage_to_enemy")
    checks["hidden_damage_to_own"] = h("damage_to_own")
    checks["hidden_enemy_destroyed"] = h("enemy_destroyed")
    checks["hidden_own_lost"] = h("own_lost")
    checks["hidden_reward_total_phase"] = h("reward_total_phase")
    checks["visible_reward_total_phase"] = v("reward_total_phase")
    checks["hidden_rpc_benchmarks"] = h("rpc_benchmarks")
    checks["visible_rpc_benchmarks"] = v("rpc_benchmarks")
    checks["hidden_cpu_cores"] = h("cpu_cores")
    checks["visible_cpu_cores"] = v("cpu_cores")
    checks["hidden_gpu_util_avg"] = h("gpu_util_avg")
    checks["visible_gpu_util_avg"] = v("gpu_util_avg")
    checks["hidden_vram_mb_avg"] = h("vram_mb_avg")
    checks["visible_vram_mb_avg"] = v("vram_mb_avg")
    checks["game_ended"] = {k: s.get("outcome") for k, s in sessions.items()}
    checks["process_exit_codes"] = {k: s.get("exit_code") for k, s in sessions.items()}
    checks["restore_visible"] = result.get("restore")
    # verdicts
    verdict = {}
    verdict["window_hidden"] = (visibility is not None and visibility >= 0.99)
    verdict["game_logic_continues"] = bool(checks["hidden_game_fps"] and
                                           checks["hidden_game_fps"] > 30)
    verdict["state_reads_work"] = bool(checks["hidden_read_success_rate"] and
                                       checks["hidden_read_success_rate"] >= 0.99)
    prog = checks["hidden_own_buildings"] + checks["hidden_own_units"]
    verdict["building_and_production_continue"] = bool(
        (prog[1] is not None and prog[0] is not None and prog[1] >= prog[0]) and
        (prog[3] is not None and prog[2] is not None and prog[3] > prog[2]))
    verdict["movement_continues"] = bool(checks["hidden_moved_cells"] and
                                         checks["hidden_moved_cells"] > 0)
    verdict["combat_observed"] = bool(
        (checks["hidden_damage_to_enemy"] or 0) + (checks["hidden_damage_to_own"] or 0)
        + (checks["hidden_enemy_destroyed"] or 0) + (checks["hidden_own_lost"] or 0) > 0)
    verdict["restore_works"] = bool(checks["restore_visible"]) and all(
        v.get("window") == "visible" for v in (checks["restore_visible"] or {}).values())
    checks["verdict"] = verdict
    return checks


def find_runs(ids):
    runs = []
    for ident in ids:
        p = pathlib.Path(ident)
        if p.is_dir() and (p / "result.json").exists():
            runs.append(p)
            continue
        candidate = HEADLESS_ROOT / "runs" / ident
        if candidate.is_dir() and (candidate / "result.json").exists():
            runs.append(candidate)
            continue
        raise SystemExit(f"run not found: {ident}")
    return runs


def comparison_table(analyses):
    """Visible vs hidden metric comparison for A/B/A runs."""
    rows = []
    for analysis in analyses:
        phases = {p["name"]: p for p in analysis["phases"]}
        for index in (phases.get("hidden") or {}).get("instances", {}):
            def get(phase_name, key):
                return ((phases.get(phase_name) or {}).get("instances") or {}
                        ).get(index, {}).get(key)
            row = {"run_id": analysis["run_id"], "instance": index}
            for metric in ("game_fps", "steps_per_s_step_window",
                           "read_latency_ms_p50", "read_latency_ms_p95",
                           "read_ok", "read_fail", "cpu_cores", "gpu_util_avg",
                           "gpu_3d_avg", "vram_mb_avg", "working_set_mb_avg",
                           "damage_to_enemy", "moved_cells", "orders_ok",
                           "invalid_orders", "stall_steps",
                           "reward_total_phase", "reward_parts_phase"):
                row[f"visible_{metric}"] = get("visible", metric)
                row[f"hidden_{metric}"] = get("hidden", metric)
            row["hidden_window_hidden_fraction"] = (
                get("hidden", "window_hidden_samples") /
                max(1, (get("hidden", "window_hidden_samples") or 0) +
                    (get("hidden", "window_visible_samples") or 0)))
            rows.append(row)
    return rows


def render_markdown(analyses, table):
    lines = ["# 隐藏窗口实验数据分析（自动生成）", ""]
    for analysis in analyses:
        lines.append(f"## run `{analysis['run_id']}`")
        lines.append("")
        lines.append("| 阶段 | 窗口 | 步数 | 实际步/s | 游戏FPS | 读p50ms | 读p95ms "
                     "| CPU核 | GPU% | 3D% | 显存MB | 内存MB |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for phase in analysis["phases"]:
            for index, inst in (phase.get("instances") or {}).items():
                lines.append(
                    f"| {phase['name']} | {phase['window']} | {inst.get('steps')} "
                    f"| {inst.get('steps_per_s_step_window')} | {inst.get('game_fps')} "
                    f"| {inst.get('read_latency_ms_p50')} | {inst.get('read_latency_ms_p95')} "
                    f"| {inst.get('cpu_cores')} | {inst.get('gpu_util_avg')} "
                    f"| {inst.get('gpu_3d_avg')} | {inst.get('vram_mb_avg')} "
                    f"| {inst.get('working_set_mb_avg')} |")
        lines.append("")
        checks = analysis.get("checks", {})
        lines.append("判定：")
        for key, value in (checks.get("verdict") or {}).items():
            lines.append(f"- {key}: **{value}**")
        lines.append("")
        lines.append("隐藏阶段逻辑证据：")
        for key in ("hidden_steps", "hidden_read_success_rate", "hidden_window_hidden_fraction",
                    "hidden_own_buildings", "hidden_own_units", "hidden_moved_cells",
                    "hidden_damage_to_enemy", "hidden_damage_to_own", "hidden_orders_ok",
                    "hidden_invalid_orders", "hidden_stall_steps", "hidden_reward_total_phase",
                    "hidden_game_fps", "hidden_cpu_cores", "visible_cpu_cores",
                    "hidden_gpu_util_avg", "visible_gpu_util_avg"):
            lines.append(f"- {key}: {checks.get(key)}")
        lines.append("")
    if table:
        lines.append("## 对照表（visible vs hidden）")
        lines.append("")
        metrics = [k for k in table[0] if k.startswith("visible_")]
        lines.append("| run | " + " | ".join(m.replace("visible_", "") for m in metrics) + " |")
        lines.append("|---" * (len(metrics) + 1) + "|")
        for row in table:
            vals = []
            for m in metrics:
                vis, hid = row.get(m), row.get("hidden_" + m.split("visible_", 1)[1])
                vals.append(f"{vis} / {hid}")
            lines.append(f"| {row['run_id']}#{row['instance']} | " + " | ".join(vals) + " |")
        lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+", help="run ids under reports/headless/runs or paths")
    ap.add_argument("--json-out", default=None)
    ap.add_argument("--md-out", default=None)
    args = ap.parse_args()
    run_dirs = find_runs(args.runs)
    analyses = [analyze_run(p) for p in run_dirs]
    table = comparison_table(analyses)
    payload = {"generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
               "runs": analyses, "comparison": table}
    json_out = pathlib.Path(args.json_out) if args.json_out else (
        HEADLESS_ROOT / "analysis.json")
    json_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    md = render_markdown(analyses, table)
    md_out = pathlib.Path(args.md_out) if args.md_out else json_out.with_suffix(".md")
    md_out.write_text(md, encoding="utf-8")
    print(md)
    print(f"\nwritten: {json_out}\n         {md_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
