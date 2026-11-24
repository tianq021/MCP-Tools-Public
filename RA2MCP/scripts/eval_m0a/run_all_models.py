"""M0-A 批量评测驱动：依次评测 7 个历史模型 + 1 个 combat 参考口径。

每个模型用 6 workers、10 局、300s、确定性策略、full env（无 RulePolicy）。
每次调用 run_eval.py 作为独立子进程，日志落盘；失败/超时记录原因并继续。
"""
import json
import os
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
PYTHON = r"F:\conda\envs\python_mmx_LLM\python.exe"
OUTROOT = ROOT / "reports" / "m0a-2026-11-01"
RUN_EVAL = ROOT / "scripts" / "eval_m0a" / "run_eval.py"

MODELS = [
    ("R1", "reports/rl/stage-A-round1-20261030-153225/model.zip", 10, "full",
     "Stage A R1；受保护"),
    ("R2", "reports/rl/stage-A-round2-20261030-155824/model.zip", 10, "full",
     "Stage A R2；受保护"),
    ("R3", "reports/rl/stage-A-round3-20261030-162536/model.zip", 10, "full",
     "Stage A R3；退化样本"),
    ("203156", "reports/rl/ppo-shared-20261030-203156/model.zip", 10, "full",
     "旧 75 局基线同族（29,014 步）"),
    ("183k", "reports/rl/experimentA-auto-20261031-081809/model.zip", 10, "full",
     "Experiment A 8h（183,106 步）"),
    ("OLD_REF", "reports/rl/ppo-shared-20261030-102633/model.zip", 10, "full",
     "历史基线（受保护）"),
    ("BUILD_V27", "experiments/build_training_v2/build_training_v2_model/model.zip", 10, "full",
     "建设 V2.7（无敌人+预部署条件下训练；本评测为全环境能力测试）"),
    ("R1_COMBAT_REF", "reports/rl/stage-A-round1-20261030-153225/model.zip", 6, "combat",
     "脚本经济参考口径（RulePolicy 托管，仅对照；不得当成模型自主能力）"),
]

ENV = dict(os.environ)
ENV["PYTHONPATH"] = os.pathsep.join([
    str(ROOT / "scripts"), str(ROOT / "scripts" / "dsh_train"),
    r"E:\RA2MCP\.venv-rl\Lib\site-packages"])


def _already_done(outdir):
    ev = pathlib.Path(outdir) / "evaluation.json"
    if not ev.exists():
        return None
    try:
        return json.loads(ev.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def main():
    OUTROOT.mkdir(parents=True, exist_ok=True)
    summary_path = OUTROOT / "batch_summary.json"
    results = {"started_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "models": {}}
    if summary_path.exists():
        try:
            results = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    results.setdefault("models", {})
    for name, model, games, mode, note in MODELS:
        outdir = OUTROOT / f"eval-{name}"
        logpath = OUTROOT / f"eval-{name}.log"
        prev = results["models"].get(name) or {}
        ev = _already_done(outdir)
        if ev and (ev.get("summary") or {}).get("games"):
            summary = ev.get("summary") or {}
            if summary.get("games", 0) >= games:
                results["models"][name] = {
                    "name": name, "model": model, "games": games, "mode": mode, "note": note,
                    "outdir": str(outdir), "log": str(logpath),
                    "started_at": prev.get("started_at"),
                    "finished_at": prev.get("finished_at") or time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "status": "COMPLETED_SKIPPED_ALREADY_DONE",
                    "wall_seconds": prev.get("wall_seconds"),
                    "summary": summary, "frozen_verified": ev.get("frozen_verified"),
                    "model_file_unchanged": ev.get("model_file_unchanged"),
                    "model_sha256": ev.get("model_sha256"),
                }
                summary_path.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
                print(f"[batch] {name}: SKIP (already done, games={summary.get('games')})",
                      flush=True)
                continue
        entry = {"name": name, "model": model, "games": games, "mode": mode,
                 "note": note, "outdir": str(outdir), "log": str(logpath),
                 "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "status": "RUNNING"}
        results["models"][name] = entry
        summary_path.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                                encoding="utf-8")
        cmd = [PYTHON, str(RUN_EVAL), "--model", model, "--games", str(games),
               "--workers", "6" if games >= 6 else str(games), "--mode", mode,
               "--out", str(outdir), "--max-minutes", "55"]
        t0 = time.monotonic()
        try:
            with open(logpath, "w", encoding="utf-8") as log:
                proc = subprocess.run(cmd, cwd=str(ROOT), env=ENV, stdout=log,
                                      stderr=subprocess.STDOUT, timeout=65 * 60)
            entry["returncode"] = proc.returncode
        except subprocess.TimeoutExpired:
            entry["status"] = "TIMEOUT"
            entry["returncode"] = None
        entry["wall_seconds"] = round(time.monotonic() - t0, 1)
        eval_path = outdir / "evaluation.json"
        if eval_path.exists():
            ev = json.loads(eval_path.read_text(encoding="utf-8"))
            entry["status"] = "COMPLETED"
            entry["summary"] = ev.get("summary")
            entry["frozen_verified"] = ev.get("frozen_verified")
            entry["model_file_unchanged"] = ev.get("model_file_unchanged")
            entry["model_sha256"] = ev.get("model_sha256")
        elif entry.get("status") == "RUNNING":
            entry["status"] = f"FAILED(rc={entry.get('returncode')})"
        entry["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        results["models"][name] = entry
        results["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        summary_path.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                                encoding="utf-8")
        print(f"[batch] {name}: {entry['status']} wall={entry.get('wall_seconds')}s "
              f"summary={json.dumps(entry.get('summary', {}).get('kinds') if entry.get('summary') else None)}",
              flush=True)
    results["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    summary_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[batch] ALL_DONE", flush=True)


if __name__ == "__main__":
    main()
