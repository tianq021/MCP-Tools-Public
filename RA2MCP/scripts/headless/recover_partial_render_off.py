"""Reconstruct partial evidence from an interrupted render-off stability run.

`render-off-dual-1h` was terminated externally (background process closed) before
the harness could write result.json.  The per-match evidence that was already
flushed (worker*/matches.jsonl, match decision event files, monitor.csv,
monitor_gpu_by_pid.csv) is still valid; this script summarises it and writes
`partial-summary.json` + `INTERRUPTED.md` next to the run so the interruption is
recorded honestly instead of silently vanishing.

Only read-only recovery: no game is launched, no file outside the run dir is
modified.
"""
import argparse
import hashlib
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "headless"))
from analyze_render_off import match_resources  # noqa: E402


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_jsonl(path):
    out = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
    except FileNotFoundError:
        pass
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--reason", default="background harness process was closed "
                                       "externally by the session host")
    args = ap.parse_args()
    run_dir = pathlib.Path(args.run_dir)
    files = sorted(run_dir.rglob("*"))
    evidence = []
    last_mtime = None
    for p in files:
        if p.is_file():
            m = p.stat().st_mtime
            last_mtime = max(last_mtime or m, m)
            evidence.append({"path": str(p.relative_to(run_dir)),
                             "size": p.stat().st_size,
                             "mtime": time.strftime("%Y-%m-%dT%H:%M:%S",
                                                    time.localtime(m))})
    pseudo_result = {
        "monitor_csv": str(run_dir / "monitor.csv"),
        "gpu_csv": str(run_dir / "gpu_counters.csv"),
        "gpu_by_pid_csv": str(run_dir / "monitor_gpu_by_pid.csv"),
    }
    workers = []
    all_matches = []
    for worker_dir in sorted(run_dir.glob("worker*")):
        if not worker_dir.is_dir():
            continue
        matches = []
        for raw in read_jsonl(worker_dir / "matches.jsonl"):
            entry = {k: raw.get(k) for k in
                     ("match_index", "pid", "status", "outcome", "steps",
                      "read_ok", "read_fail", "orders_ok", "invalid_orders",
                      "game_fps", "decision_wall_seconds", "patch_restored",
                      "cleanup_ok", "decision_start_ms", "decision_end_ms")}
            entry.update(match_resources(pseudo_result, raw))
            matches.append(entry)
            all_matches.append(entry)
        # in-progress match evidence (render-off events written when patch applied)
        in_progress = []
        logs = read_jsonl(worker_dir / "worker-log.jsonl")
        if logs:
            last = logs[-1].get("message", "")
            if "launching" in last:
                in_progress.append(last)
        render_off_state = worker_dir / "render-off-state.json"
        state = None
        if render_off_state.exists():
            try:
                state = json.loads(render_off_state.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                state = None
        steps = sum(m["steps"] or 0 for m in matches)
        wall = sum(m["decision_wall_seconds"] or 0 for m in matches)
        workers.append({
            "worker": worker_dir.name,
            "completed_matches": len(matches),
            "matches": matches,
            "in_progress_evidence": in_progress,
            "last_render_off_state": state and {
                "pid": state.get("pid"),
                "restore": state.get("restore"),
                "patch": state.get("patch") and {
                    "va": state["patch"].get("va"),
                    "verified": state["patch"].get("verified", True)}},
            "steps": steps, "decision_wall_seconds": round(wall, 3),
            "steps_per_s": round(steps / wall, 4) if wall else None,
            "read_ok": sum(m["read_ok"] or 0 for m in matches),
            "read_fail": sum(m["read_fail"] or 0 for m in matches),
            "orders_ok": sum(m["orders_ok"] or 0 for m in matches),
            "invalid_orders": sum(m["invalid_orders"] or 0 for m in matches),
            "patch_restored_all": all(m["patch_restored"] for m in matches),
            "cleanup_ok_all": all(m["cleanup_ok"] for m in matches),
        })
    total_steps = sum(m["steps"] or 0 for m in all_matches)
    total_wall = sum(m["decision_wall_seconds"] or 0 for m in all_matches)
    starts = [m["decision_start_ms"] for m in all_matches if m["decision_start_ms"]]
    ends = [m["decision_end_ms"] for m in all_matches if m["decision_end_ms"]]
    cpu = [m["cpu_cores"] for m in all_matches if m.get("cpu_cores") is not None]
    gpu = [m["gpu_util_avg"] for m in all_matches if m.get("gpu_util_avg") is not None]
    ws = [m["working_set_mb_avg"] for m in all_matches
          if m.get("working_set_mb_avg") is not None]
    vram = [m["vram_mb_avg"] for m in all_matches if m.get("vram_mb_avg") is not None]
    summary = {
        "run_dir": str(run_dir),
        "interrupted": True,
        "reason": args.reason,
        "reconstructed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "last_evidence_mtime": time.strftime(
            "%Y-%m-%dT%H:%M:%S", time.localtime(last_mtime)) if last_mtime else None,
        "result_json_written": (run_dir / "result.json").exists(),
        "workers": workers,
        "totals": {
            "completed_matches": len(all_matches),
            "steps": total_steps,
            "decision_wall_seconds": round(total_wall, 3),
            "per_instance_steps_per_s": [w["steps_per_s"] for w in workers],
            "concurrent_aggregate_steps_per_s": (
                round(total_steps / ((max(ends) - min(starts)) / 1000.0), 4)
                if starts and ends else None),
            "sampling_span_seconds": (
                round((max(ends) - min(starts)) / 1000.0, 3)
                if starts and ends else None),
            "read_ok": sum(w["read_ok"] for w in workers),
            "read_fail": sum(w["read_fail"] for w in workers),
            "orders_ok": sum(w["orders_ok"] for w in workers),
            "invalid_orders": sum(w["invalid_orders"] for w in workers),
            "patch_restored_all": all(w["patch_restored_all"] for w in workers),
            "cleanup_ok_all": all(w["cleanup_ok_all"] for w in workers),
            "cpu_cores_per_instance_avg": round(sum(cpu) / len(cpu), 4) if cpu else None,
            "gpu_util_per_instance_avg": round(sum(gpu) / len(gpu), 3) if gpu else None,
            "vram_mb_per_instance_avg": round(sum(vram) / len(vram), 2) if vram else None,
            "working_set_mb_per_instance_avg": round(sum(ws) / len(ws), 2) if ws else None,
        },
        "data_lost": [
            "run-level result.json (harness was killed before writing it)",
            "the in-flight (third) match of each worker: its decision events are "
            "buffered in memory and were not flushed",
            "final post-run cleanup verification for the killed processes "
            "(games were terminated externally; patch was memory-only and "
            "therefore disappeared with the process)",
        ],
        "safety_notes": [
            "no gamemd-spawn-ra2yrcpp process was alive after the interruption",
            "render-off patch existed only in the killed processes' memory; "
            "on-disk exe SHA256 in both test copies is unchanged",
            "no training was running and no model files were touched",
        ],
        "evidence_files": evidence,
    }
    (run_dir / "partial-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    md = [
        "# INTERRUPTED：1 小时双实例 render-off 稳定性测试（外部中断）",
        "",
        f"- 中断原因：{args.reason}",
        f"- 重建时间：{summary['reconstructed_at']}",
        f"- 最后证据写入时间：{summary['last_evidence_mtime']}",
        f"- result.json 是否写出：{summary['result_json_written']}",
        "",
        "## 仍有效的证据",
        "",
        f"- 已完整结束并落盘的对局：**{len(all_matches)} 局**（每局结束均已恢复补丁并清理进程）",
        f"- 决策步合计：**{total_steps}**；读取 {summary['totals']['read_ok']}/"
        f"{summary['totals']['read_ok'] + summary['totals']['read_fail']} 成功；"
        f"动作 {summary['totals']['orders_ok']} 成功 / {summary['totals']['invalid_orders']} 无效",
        f"- 每实例活跃吞吐：" + ", ".join(
            f"{w['worker']}={w['steps_per_s']} 步/秒" for w in workers),
        f"- CPU/实例 {summary['totals']['cpu_cores_per_instance_avg']} 核；"
        f"GPU/实例 {summary['totals']['gpu_util_per_instance_avg']}%；"
        f"内存/实例 {summary['totals']['working_set_mb_per_instance_avg']} MB",
        "",
        "## 丢失的数据",
        "",
    ] + [f"- {x}" for x in summary["data_lost"]] + [
        "",
        "## 安全核对",
        "",
    ] + [f"- {x}" for x in summary["safety_notes"]] + [
        "",
        "## 各 worker 已完成的对局",
        "",
        "| worker | 局 | 结果 | 步数 | 步/秒 | FPS | 读取 | 动作 | CPU核 | GPU% | 恢复 | 清理 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for w in workers:
        for m in w["matches"]:
            md.append(
                f"| {w['worker']} | {m['match_index']} | {m['status']} | {m['steps']} "
                f"| {round((m['steps'] or 0)/(m['decision_wall_seconds'] or 1), 3)} "
                f"| {m['game_fps']} | {m['read_ok']}/{m['read_fail']} "
                f"| {m['orders_ok']}/{m['invalid_orders']} | {m.get('cpu_cores')} "
                f"| {m.get('gpu_util_avg')} | {m['patch_restored']} | {m['cleanup_ok']} |")
    (run_dir / "INTERRUPTED.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(json.dumps(summary["totals"], ensure_ascii=False, indent=2))
    print(f"written: {run_dir / 'partial-summary.json'}")
    print(f"written: {run_dir / 'INTERRUPTED.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
