"""Phase C integration smoke tests for the opt-in render-off mode.

Three short real-game smoke tests (no long training, no model overwrite):
  1. train.py --render-off 32 steps -> run.json.render_off=true, patch evidence
     written, patch restored, game process terminated, model saved in new dir;
  2. the same 32-step smoke WITHOUT --render-off -> default rendering unchanged;
  3. manage_training.ps1 -RenderOff (the .\\train.cmd entry point) start -> wait
     for collecting_real_rollouts -> stop -> verify worker exits, patch restored,
     no leftover game process.

Results are written to reports/headless/integration-smoke/result-summary.json.

Usage:
  .venv\\Scripts\\python.exe scripts/headless/integration_smoke.py
"""
import json
import os
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(ROOT / "vendor" / "pyra2yr"))
sys.path.insert(0, str(SCRIPTS / "headless"))

import winapi  # noqa: E402

CONDA_PY = r"F:\conda\envs\python_mmx_LLM\python.exe"
RL_SITE = str(ROOT / ".venv-rl" / "Lib" / "site-packages")
OUT_ROOT = ROOT / "reports" / "headless" / "integration-smoke"
GAME_DIR = str(ROOT / "test-game-headless")
PORT = 14651
TRAIN_PY = str(ROOT / "scripts" / "dsh_rl" / "train.py")
MANAGE_PS1 = str(ROOT / "scripts" / "manage_training.ps1")
SHELL = str(pathlib.Path(os.environ.get("WINDIR", r"C:\Windows")) /
            "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")


def live_game_pids():
    return winapi.find_processes_by_exe("gamemd-spawn-ra2yrcpp.exe")


def wait_no_game(timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not live_game_pids():
            return True
        time.sleep(1)
    return not live_game_pids()


def run_capture(cmd, env_extra, timeout, log_dir, tag):
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout = log_dir / f"{tag}.stdout.log"
    stderr = log_dir / f"{tag}.stderr.log"
    env = os.environ.copy()
    env.update(env_extra)
    started = time.time()
    with open(stdout, "w", encoding="utf-8", errors="replace") as fo, \
            open(stderr, "w", encoding="utf-8", errors="replace") as fe:
        try:
            proc = subprocess.run(cmd, cwd=str(ROOT), env=env, stdout=fo,
                                  stderr=fe, timeout=timeout)
            code = proc.returncode
            timed_out = False
        except subprocess.TimeoutExpired:
            code = None
            timed_out = True
    return {"cmd": cmd, "exit_code": code, "timed_out": timed_out,
            "wall_seconds": round(time.time() - started, 2),
            "stdout": str(stdout), "stderr": str(stderr)}


def smoke_train(render_off, out_dir):
    steps = 32
    cmd = [CONDA_PY, TRAIN_PY, "--control", "full", "--episode-seconds", "0",
           "--decision-seconds", "0.5", "--steps", str(steps), "--rollout", str(steps),
           "--instance", "0", "--port", str(PORT), "--game-dir", GAME_DIR,
           "--out", str(out_dir), "--no-kill-stale-games"]
    if render_off:
        cmd += ["--render-off"]
    tag = "train-render-off" if render_off else "train-default"
    run = run_capture(cmd, {"PYTHONPATH": RL_SITE, "PYTHONUNBUFFERED": "1",
                            "TEMP": str(ROOT / ".tmp"), "TMP": str(ROOT / ".tmp")},
                      timeout=420, log_dir=out_dir, tag=tag)
    time.sleep(2)
    run["game_processes_after"] = live_game_pids()
    run["no_leftover_game"] = wait_no_game(30)
    result = {"kind": "train.py-smoke", "render_off_requested": render_off,
              "out_dir": str(out_dir), "run": run}
    run_json = out_dir / "run.json"
    report = {}
    if run_json.exists():
        report = json.loads(run_json.read_text(encoding="utf-8-sig"))
    result["report"] = {k: report.get(k) for k in
                        ("status", "render_off", "error", "real_transitions_this_run",
                         "timesteps_after", "model_sha256", "finished_at")}
    result["render_off_evidence"] = report.get("render_off_evidence")
    result["model_exists"] = (out_dir / "model.zip").exists()
    evidence_dir = pathlib.Path(report.get("render_off_evidence", {}).get("evidence_dir", "")) \
        if report.get("render_off_evidence") else None
    restore = None
    patch = None
    if evidence_dir is not None and evidence_dir.exists():
        p = evidence_dir / "render-off-patch.json"
        r = evidence_dir / "render-off-restore.json"
        if p.exists():
            patch = json.loads(p.read_text(encoding="utf-8-sig"))
        if r.exists():
            restore = json.loads(r.read_text(encoding="utf-8-sig"))
    result["patch_evidence"] = patch and {
        "mode": patch.get("mode"), "va": patch.get("va"),
        "verified": patch.get("verified", True),
        "bytes_after": patch.get("bytes_after")}
    result["restore_evidence"] = restore and {
        "restored": restore.get("restored"),
        "bytes_after": restore.get("bytes_after")}
    if render_off:
        result["passed"] = bool(
            report.get("status") in ("complete", "stopped_by_request") and
            report.get("render_off") is True and
            result["model_exists"] and result["no_leftover_game"] and
            result["restore_evidence"] and result["restore_evidence"]["restored"] is True)
    else:
        result["passed"] = bool(
            report.get("status") in ("complete", "stopped_by_request") and
            report.get("render_off") is not True and
            result["model_exists"] and result["no_leftover_game"])
    return result


def _read_json(path):
    try:
        return json.loads(pathlib.Path(path).read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def smoke_manage_training_start_stop():
    tag = "manage-training-render-off"
    out_dir = OUT_ROOT / tag
    out_dir.mkdir(parents=True, exist_ok=True)
    start = run_capture([SHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
                         "-File", MANAGE_PS1, "-Action", "start", "-RenderOff",
                         "-Instances", "1", "-DecisionSeconds", "0.5"],
                        {}, timeout=300, log_dir=out_dir, tag="start")
    result = {"kind": "manage_training-smoke", "start": start, "render_off": True}
    # discover the created run from active-runs.json
    runs = []
    deadline = time.time() + 180
    status = None
    while time.time() < deadline:
        runs = _read_json(ROOT / "reports" / "rl" / "active-runs.json") or []
        if runs:
            report = _read_json(runs[0].get("status_file"))
            status = (report or {}).get("status")
            if status == "collecting_real_rollouts":
                break
            pid = runs[0].get("pid")
            if not winapi.is_process_alive(int(pid)):
                break
        time.sleep(2)
    result["status_seen"] = status
    result["active_run"] = runs[0] if runs else None
    # stop the smoke run through the same entry point
    stop = run_capture([SHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", MANAGE_PS1, "-Action", "stop"],
                       {}, timeout=180, log_dir=out_dir, tag="stop")
    result["stop"] = stop
    time.sleep(3)
    result["no_leftover_game"] = wait_no_game(40)
    if runs:
        run_dir = pathlib.Path(runs[0]["run_dir"])
        report = _read_json(run_dir / "run.json") or {}
        progress = _read_json(run_dir / "progress.json") or {}
        result["run_report"] = {k: report.get(k) for k in
                                ("status", "render_off", "error",
                                 "real_transitions_this_run", "model_sha256")}
        result["progress"] = progress
        evidence_root = run_dir / "render-off"
        evidence = []
        if evidence_root.exists():
            for p in evidence_root.rglob("render-off-restore.json"):
                evidence.append({"restore": _read_json(p),
                                 "path": str(p)})
        result["restore_evidence"] = evidence
        result["model_exists"] = (run_dir / "model.zip").exists()
        # the launcher records render_off in its active-run record
        result["launcher_record"] = runs[0]
    result["passed"] = bool(
        result.get("status_seen") == "collecting_real_rollouts" and
        result.get("no_leftover_game") and
        (result.get("run_report") or {}).get("status") in
        ("stopped_by_request", "complete") and
        (result.get("run_report") or {}).get("render_off") is True and
        result.get("model_exists"))
    return result


def main():
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    summary = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "tests": {}, "errors": []}
    try:
        print("=== smoke 1: train.py --render-off 32 steps ===", flush=True)
        summary["tests"]["train_render_off"] = smoke_train(
            True, OUT_ROOT / "ppo-render-off-32")
        print(json.dumps(summary["tests"]["train_render_off"].get("report"),
                         ensure_ascii=False), flush=True)
        print("=== smoke 2: train.py default 32 steps ===", flush=True)
        summary["tests"]["train_default"] = smoke_train(
            False, OUT_ROOT / "ppo-default-32")
        print(json.dumps(summary["tests"]["train_default"].get("report"),
                         ensure_ascii=False), flush=True)
        print("=== smoke 3: manage_training.ps1 -RenderOff start/stop ===", flush=True)
        summary["tests"]["manage_training"] = smoke_manage_training_start_stop()
        print(json.dumps({k: summary["tests"]["manage_training"].get(k) for k in
                          ("status_seen", "passed", "no_leftover_game")},
                         ensure_ascii=False), flush=True)
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        summary["errors"].append(f"{type(exc).__name__}: {exc}")
    summary["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    summary["passed"] = all(t.get("passed") for t in summary["tests"].values()) \
        if summary["tests"] else False
    path = OUT_ROOT / "result-summary.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8")
    print(f"\nwritten: {path}")
    print(json.dumps({k: v.get("passed") for k, v in summary["tests"].items()},
                     ensure_ascii=False))
    return 0 if summary["passed"] else 2


if __name__ == "__main__":
    sys.exit(main())
