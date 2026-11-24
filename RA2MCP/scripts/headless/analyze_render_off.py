"""Analyze render-off stability runs (phase B/C evidence).

Reads a `render_off_runtime.py` run directory (result.json + monitor.csv +
gpu_counters.csv) and produces analysis JSON + Markdown:

  * per worker / per match: steps, steps/s, fps, state-read success, action
    success, settlement, patch restore/cleanup, CPU cores, GPU %, VRAM, RSS;
  * run totals: total transitions, sampling wall, aggregate and per-instance
    throughput, restart count, failures;
  * comparison against the previous "dual hidden window, normal rendering"
    baseline (reports/headless/analysis-phase3-dual-01.json) and the single
    hidden+render-off run when available.

Usage:
  .venv\\Scripts\\python.exe scripts/headless/analyze_render_off.py render-off-dual-10min
"""
import argparse
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "headless"))

from analyze import _in_window, read_gpu_samples, read_process_samples  # noqa: E402

HEADLESS_ROOT = ROOT / "reports" / "headless"
BASELINE = HEADLESS_ROOT / "analysis-phase3-dual-01.json"


def read_gpu_by_pid(csv_path):
    """pid -> [{ts,gpu_util,gpu_3d,vram_mb}] from monitor_dynamic's per-pid csv."""
    import csv as _csv
    out = {}
    path = pathlib.Path(csv_path) if csv_path else None
    if path is None or not path.exists():
        return out
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        for row in _csv.DictReader(f):
            try:
                pid = int(row["pid"])
                ts = int(row["ts_ms"])
            except (KeyError, TypeError, ValueError):
                continue

            def num(key):
                try:
                    v = float(row.get(key))
                    return v if v == v else None
                except (TypeError, ValueError):
                    return None
            out.setdefault(pid, []).append({"ts": ts, "gpu_util": num("gpu_util"),
                                            "gpu_3d": num("gpu_3d"),
                                            "vram_mb": num("vram_mb")})
    for pid in out:
        out[pid].sort(key=lambda x: x["ts"])
    return out


def match_resources(result, match):
    """CPU cores / GPU / VRAM / RSS for one match window (by pid)."""
    procs = read_process_samples(result.get("monitor_csv"))
    gpus = read_gpu_samples(result.get("gpu_csv"))
    gpu_by_pid_path = result.get("gpu_by_pid_csv")
    if not gpu_by_pid_path and result.get("monitor_csv"):
        gpu_by_pid_path = str(pathlib.Path(result["monitor_csv"]).parent /
                              "monitor_gpu_by_pid.csv")
    gpu_by_pid = read_gpu_by_pid(gpu_by_pid_path)
    pid = match.get("pid")
    start = match.get("decision_start_ms")
    end = match.get("decision_end_ms")
    out = {"pid": pid, "window_ms": [start, end]}
    if not pid or not start or not end:
        return out
    rows = [r for r in procs.get(pid, []) if _in_window(r["ts"], start, end)]
    if len(rows) >= 2:
        t0, t1 = rows[0]["ts"], rows[-1]["ts"]
        if t1 > t0 and rows[0]["cpu"] is not None and rows[-1]["cpu"] is not None:
            out["cpu_cores"] = round(
                (rows[-1]["cpu"] - rows[0]["cpu"]) / ((t1 - t0) / 1000.0), 4)
    ws = [r["ws"] for r in rows if r["ws"] is not None]
    priv = [r["private"] for r in rows if r["private"] is not None]
    out["process_samples"] = len(rows)
    out["working_set_mb_avg"] = round(sum(ws) / len(ws), 2) if ws else None
    out["private_mb_avg"] = round(sum(priv) / len(priv), 2) if priv else None
    # prefer the per-pid GPU samples (they also cover restarted matches)
    pid_gpu = [r for r in gpu_by_pid.get(pid, [])
               if _in_window(r["ts"], start, end)]
    if pid_gpu:
        util = [r["gpu_util"] for r in pid_gpu if r["gpu_util"] is not None]
        util3d = [r["gpu_3d"] for r in pid_gpu if r["gpu_3d"] is not None]
        vram = [r["vram_mb"] for r in pid_gpu if r["vram_mb"] is not None]
        out["gpu_source"] = "gpu_by_pid"
        out["gpu_samples"] = len(pid_gpu)
        if util:
            out["gpu_util_avg"] = round(sum(util) / len(util), 3)
        if util3d:
            out["gpu_3d_avg"] = round(sum(util3d) / len(util3d), 3)
        if vram:
            out["vram_mb_avg"] = round(sum(vram) / len(vram), 2)
        return out
    gsel = [s["per_pid"].get(pid) for s in gpus
            if _in_window(s["ts"], start, end)]
    gsel = [g for g in gsel if g]
    if gsel:
        out["gpu_source"] = "typeperf"
        out["gpu_samples"] = len(gsel)
        out["gpu_util_avg"] = round(sum(g["util"] for g in gsel) / len(gsel), 3)
        out["gpu_3d_avg"] = round(sum(g["util3d"] for g in gsel) / len(gsel), 3)
        out["vram_mb_avg"] = round(sum(g["vram_mb"] for g in gsel) / len(gsel), 2)
    return out


def analyze_run(run_dir):
    run_dir = pathlib.Path(run_dir)
    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8-sig"))
    analysis = {"run_id": result.get("run_id", run_dir.name),
                "run_dir": str(run_dir), "config": result.get("config"),
                "workers": [], "totals": {}, "comparison": {}}
    matches_all = []
    for worker in result.get("workers", []):
        winfo = {"index": worker.get("index"), "game_dir": worker.get("game_dir"),
                 "port": worker.get("port"), "status": worker.get("status"),
                 "failure": worker.get("failure"),
                 "matches": [], "restarts": max(0, (worker.get("match_count") or 0) - 1)}
        for match in worker.get("matches", []):
            m = {k: match.get(k) for k in
                 ("match_index", "pid", "status", "outcome", "steps", "read_ok",
                  "read_fail", "orders_ok", "invalid_orders", "game_fps",
                  "decision_wall_seconds", "patch_restored", "cleanup_ok",
                  "process_alive_after_close", "exit_code_after_close",
                  "decision_start_ms", "decision_end_ms")}
            m.update(match_resources(result, match))
            winfo["matches"].append(m)
            matches_all.append(m)
        steps = sum(m["steps"] or 0 for m in winfo["matches"])
        wall = sum(m["decision_wall_seconds"] or 0 for m in winfo["matches"])
        winfo.update({
            "steps": steps,
            "decision_wall_seconds": round(wall, 3),
            "steps_per_s": round(steps / wall, 4) if wall else None,
            "read_ok": sum(m["read_ok"] or 0 for m in winfo["matches"]),
            "read_fail": sum(m["read_fail"] or 0 for m in winfo["matches"]),
            "orders_ok": sum(m["orders_ok"] or 0 for m in winfo["matches"]),
            "invalid_orders": sum(m["invalid_orders"] or 0 for m in winfo["matches"]),
            "settled_matches": sum(1 for m in winfo["matches"]
                                   if m["status"] in ("settled", "process_exited",
                                                      "stopped_by_duration")),
            "patch_restored_all": all(m["patch_restored"] for m in winfo["matches"]),
            "cleanup_ok_all": all(m["cleanup_ok"] for m in winfo["matches"]),
        })
        analysis["workers"].append(winfo)
    # totals from result.json (authoritative) plus resource aggregation
    analysis["totals"] = dict(result.get("totals") or {})
    total_steps = sum(m["steps"] or 0 for m in matches_all)
    total_wall = sum(m["decision_wall_seconds"] or 0 for m in matches_all)
    starts = [m["decision_start_ms"] for m in matches_all if m["decision_start_ms"]]
    ends = [m["decision_end_ms"] for m in matches_all if m["decision_end_ms"]]
    if starts and ends:
        span = (max(ends) - min(starts)) / 1000.0
        analysis["totals"]["sampling_wall_seconds"] = round(span, 3)
        analysis["totals"]["aggregate_steps_per_s"] = round(
            total_steps / span, 4) if span else None
        analysis["totals"]["aggregate_steps_per_instance_s"] = round(
            total_steps / span / max(1, len(analysis["workers"])), 4) if span else None
    analysis["totals"]["per_instance_step_wall_steps_per_s"] = [
        w["steps_per_s"] for w in analysis["workers"]]
    analysis["totals"]["steps"] = total_steps
    analysis["totals"]["decision_wall_seconds"] = round(total_wall, 3)
    # weighted resource averages across all match windows
    cpu = [m["cpu_cores"] for m in matches_all if m.get("cpu_cores") is not None]
    gpu = [m["gpu_util_avg"] for m in matches_all if m.get("gpu_util_avg") is not None]
    vram = [m["vram_mb_avg"] for m in matches_all if m.get("vram_mb_avg") is not None]
    ws = [m["working_set_mb_avg"] for m in matches_all
          if m.get("working_set_mb_avg") is not None]
    analysis["totals"]["resources"] = {
        "cpu_cores_per_instance_avg": round(sum(cpu) / len(cpu), 4) if cpu else None,
        "gpu_util_per_instance_avg": round(sum(gpu) / len(gpu), 3) if gpu else None,
        "vram_mb_per_instance_avg": round(sum(vram) / len(vram), 2) if vram else None,
        "working_set_mb_per_instance_avg": round(sum(ws) / len(ws), 2) if ws else None,
        "process_samples": len(cpu) + len(ws), "gpu_windows": len(gpu),
    }
    # failures / errors
    analysis["failures"] = [w["failure"] for w in analysis["workers"]
                            if w.get("failure")]
    analysis["errors"] = result.get("errors")
    return analysis


def load_baseline():
    """Previous phase: two hidden windows with normal rendering (phase3-dual-01)."""
    path = BASELINE
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    run = data["runs"][0]
    phases = {p["name"]: p for p in run["phases"]}
    hidden = phases.get("hidden")
    if not hidden:
        return None
    instances = {}
    for idx, inst in (hidden.get("instances") or {}).items():
        instances[idx] = {
            "steps": inst.get("steps"),
            "game_fps": inst.get("game_fps"),
            "read_ok": inst.get("read_ok"),
            "read_fail": inst.get("read_fail"),
            "orders_ok": inst.get("orders_ok"),
            "invalid_orders": inst.get("invalid_orders"),
            "cpu_cores": inst.get("cpu_cores"),
            "gpu_util_avg": inst.get("gpu_util_avg"),
            "vram_mb_avg": inst.get("vram_mb_avg"),
            "working_set_mb_avg": inst.get("working_set_mb_avg"),
            "read_latency_ms_p50": inst.get("read_latency_ms_p50"),
            "read_latency_ms_p95": inst.get("read_latency_ms_p95"),
        }
    return {"run_id": run.get("run_id"), "phase": "hidden",
            "wall_seconds": hidden.get("wall_seconds"),
            "instances": instances,
            "aggregate_steps_per_s": 759 / 193.931,
            "note": "two hidden instances, normal rendering"}


def build_comparison(analysis, baseline):
    cur = analysis["totals"].get("resources") or {}
    out = {"baseline": baseline, "current": {
        "aggregate_steps_per_s": analysis["totals"].get("aggregate_steps_per_s"),
        "per_instance_steps_per_s": analysis["totals"].get(
            "per_instance_step_wall_steps_per_s"),
        "cpu_cores_per_instance": cur.get("cpu_cores_per_instance_avg"),
        "gpu_util_per_instance": cur.get("gpu_util_per_instance_avg"),
        "vram_mb_per_instance": cur.get("vram_mb_per_instance_avg"),
        "working_set_mb_per_instance": cur.get("working_set_mb_per_instance_avg"),
    }}
    if baseline:
        b_inst = list(baseline["instances"].values())
        b_cpu = [i.get("cpu_cores") for i in b_inst if i.get("cpu_cores")]
        b_gpu = [i.get("gpu_util_avg") for i in b_inst if i.get("gpu_util_avg")]
        base = {
            "aggregate_steps_per_s": baseline["aggregate_steps_per_s"],
            "cpu_cores_per_instance": round(sum(b_cpu) / len(b_cpu), 4) if b_cpu else None,
            "gpu_util_per_instance": round(sum(b_gpu) / len(b_gpu), 3) if b_gpu else None,
            "vram_mb_per_instance": b_inst[0].get("vram_mb_avg") if b_inst else None,
            "working_set_mb_per_instance": round(
                sum(i.get("working_set_mb_avg") or 0 for i in b_inst) / len(b_inst), 2)
            if b_inst else None,
        }
        out["baseline_metrics"] = base
        out["improvement_pct"] = {}
        for key, base_key in (("aggregate_steps_per_s", "aggregate_steps_per_s"),
                              ("cpu_cores_per_instance", "cpu_cores_per_instance"),
                              ("gpu_util_per_instance", "gpu_util_per_instance"),
                              ("working_set_mb_per_instance", "working_set_mb_per_instance")):
            a, b = out["current"].get(key), base.get(base_key)
            if isinstance(a, (int, float)) and isinstance(b, (int, float)) and b:
                delta = (a - b) / b * 100.0
                # For CPU/GPU/WS lower is better; report raw delta and "saving"
                out["improvement_pct"][key] = round(delta, 2)
        out["interpretation"] = (
            "positive improvement_pct for throughput = faster; for cpu/gpu/mem it "
            "means higher resource use (worse), use the sign accordingly")
    return out


def render_markdown(analysis):
    lines = [f"# render-off 稳定性分析：`{analysis['run_id']}`", ""]
    cfg = analysis.get("config") or {}
    lines.append(f"- 配置：instances={cfg.get('instances')} duration={cfg.get('duration_seconds')}s "
                 f"decision={cfg.get('decision_seconds')}s match_max={cfg.get('match_max_seconds')}s")
    lines.append("")
    lines.append("## 每个 worker / 每局")
    lines.append("")
    lines.append("| worker | match | status | outcome | steps | steps/s | fps | read ok/fail "
                 "| orders ok/invalid | cpu核 | gpu% | vram MB | rss MB | restore | cleanup |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for w in analysis["workers"]:
        for m in w["matches"]:
            lines.append(
                f"| {w['index']} | {m['match_index']} | {m['status']} | {m['outcome']} "
                f"| {m['steps']} | {round((m['steps'] or 0)/(m['decision_wall_seconds'] or 1),3)} "
                f"| {m['game_fps']} | {m['read_ok']}/{m['read_fail']} "
                f"| {m['orders_ok']}/{m['invalid_orders']} | {m.get('cpu_cores')} "
                f"| {m.get('gpu_util_avg')} | {m.get('vram_mb_avg')} "
                f"| {m.get('working_set_mb_avg')} | {m['patch_restored']} "
                f"| {m['cleanup_ok']} |")
    lines.append("")
    lines.append("## 汇总")
    lines.append("")
    for key, value in (analysis.get("totals") or {}).items():
        if key != "resources":
            lines.append(f"- {key}: {value}")
    lines.append("")
    res = (analysis.get("totals") or {}).get("resources") or {}
    lines.append("## 资源（每实例，按对局窗口平均）")
    lines.append("")
    for key, value in res.items():
        lines.append(f"- {key}: {value}")
    lines.append("")
    comp = analysis.get("comparison") or {}
    if comp.get("baseline_metrics"):
        lines.append("## 对照上一阶段“双隐藏窗口、正常渲染”（phase3-dual-01）")
        lines.append("")
        lines.append("| 指标 | 正常渲染(隐藏) | 本次 render-off | 变化% |")
        lines.append("|---|---|---|---|")
        base = comp["baseline_metrics"]
        cur = comp["current"]
        for key in ("aggregate_steps_per_s", "cpu_cores_per_instance",
                    "gpu_util_per_instance", "working_set_mb_per_instance"):
            lines.append(f"| {key} | {base.get(key)} | {cur.get(key)} "
                         f"| {comp.get('improvement_pct', {}).get(key)} |")
        lines.append("")
        if comp.get("improvement_pct"):
            lines.append(f"说明：{comp.get('improvement_pct')} "
                         "（吞吐为正=更快；CPU/GPU/内存为正=占用更高=更差）")
    if analysis.get("failures"):
        lines.append("")
        lines.append("## 失败")
        lines.append("")
        for failure in analysis["failures"]:
            lines.append(f"- {failure}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+", help="run ids under reports/headless/runs")
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args()
    for ident in args.runs:
        run_dir = pathlib.Path(ident)
        if not (run_dir / "result.json").exists():
            run_dir = HEADLESS_ROOT / "runs" / ident
        if not (run_dir / "result.json").exists():
            raise SystemExit(f"run not found: {ident}")
        analysis = analyze_run(run_dir)
        baseline = load_baseline()
        analysis["comparison"] = build_comparison(analysis, baseline)
        out_json = (pathlib.Path(args.out_dir) / f"analysis-{analysis['run_id']}.json"
                    if args.out_dir else
                    HEADLESS_ROOT / f"analysis-{analysis['run_id']}.json")
        out_md = out_json.with_suffix(".md")
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(analysis, ensure_ascii=False, indent=2,
                                       default=str), encoding="utf-8")
        md = render_markdown(analysis)
        out_md.write_text(md, encoding="utf-8")
        print(md)
        print(f"\nwritten: {out_json}\n         {out_md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
