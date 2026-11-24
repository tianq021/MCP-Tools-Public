"""Batch episode collector: run N real episodes sequentially and aggregate results.

Each episode runs in its own subprocess (`dsh_episode_runner.py`) so a crash of
one episode cannot poison the next one. The runner itself launches/kills the
isolated test-copy game.

Usage:
  .venv\\Scripts\\python.exe scripts\\dsh_batch_collect.py --count 6 --max-seconds 240
"""
import argparse
import json
import pathlib
import subprocess
import sys
import time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from dsh_episode import quality, lockfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
LEARN = ROOT / "reports" / "dsh-learning"
LOGS = ROOT / "build" / "collector-logs"
RUNNER = ROOT / "scripts" / "dsh_episode_runner.py"
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")


def read_result(episodes_dir, episode_id):
    candidates = [episodes_dir / f"{episode_id}.result.json",
                  episodes_dir / f"{episode_id}.jsonl"]
    for p in candidates:
        if not p.exists():
            continue
        try:
            if p.suffix == ".jsonl":
                import sys as _s
                _s.path.insert(0, str(ROOT / "scripts"))
                from dsh_episode import schema
                ep = schema.read_episode(p)
                return ep.get("result") or {}
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=6)
    ap.add_argument("--max-seconds", type=float, default=240.0)
    ap.add_argument("--episodes-dir", default=str(LEARN / "episodes"))
    ap.add_argument("--map-patch", action="store_true", default=True)
    ap.add_argument("--prefix", default="ep-batch")
    ap.add_argument("--stop-after-failures", type=int, default=3)
    ap.add_argument("--sleep-between", type=float, default=3.0)
    ap.add_argument("--policy-params", default=None)
    args = ap.parse_args()

    episodes_dir = pathlib.Path(args.episodes_dir)
    episodes_dir.mkdir(parents=True, exist_ok=True)
    LEARN.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    batch_id = time.strftime("%Y%m%d-%H%M%S")
    results = []
    failures = 0
    for i in range(args.count):
        episode_id = f"{args.prefix}-{batch_id}-{i:02d}"
        cmd = [PY, str(RUNNER), "--episode-id", episode_id,
               "--max-seconds", str(args.max_seconds)]
        cmd += ["--episodes-dir", str(episodes_dir)]
        if args.map_patch:
            cmd.append("--map-patch")
        if args.policy_params:
            cmd += ["--policy-params", args.policy_params]
        log_path = LOGS / f"{episode_id}.log"
        print(f"[{time.strftime('%H:%M:%S')}] episode {i + 1}/{args.count}: {episode_id}",
              flush=True)
        t0 = time.monotonic()
        with open(log_path, "w", encoding="utf-8") as log:
            try:
                proc = subprocess.run(cmd, cwd=str(ROOT), stdout=log,
                                      stderr=subprocess.STDOUT, timeout=args.max_seconds + 240)
                runner_exit = proc.returncode
            except subprocess.TimeoutExpired:
                runner_exit = 124
        elapsed = round(time.monotonic() - t0, 1)
        res = read_result(episodes_dir, episode_id)
        kind = res.get("kind", "missing")
        record = {"episode_id": episode_id, "kind": kind,
                  "seconds": elapsed,
                  "rows": res.get("rows"),
                  "is_crash": res.get("is_crash"),
                  "is_win": res.get("is_win"),
                  "metrics": res.get("metrics"),
                  "stats": res.get("stats"),
                  "final": res.get("final"),
                  "log": str(log_path),
                  "runner_exit": runner_exit,
                  "quality": quality.inspect_episode(episodes_dir / f"{episode_id}.jsonl")}
        results.append(record)
        # Save after every game, even if the collector itself is interrupted.
        (LEARN / f"batch-{batch_id}.json").write_text(json.dumps({
            "batch_id": batch_id, "state": "running", "requested": args.count,
            "finished": len(results), "results": results}, indent=2), encoding="utf-8")
        print(f"  -> kind={kind} runner_rc={runner_exit} rows={record['rows']} "
              f"damage={(res.get('metrics') or {}).get('damage_dealt')} "
              f"violations={(res.get('stats') or {}).get('visibility_violations')} "
              f"in {elapsed}s", flush=True)
        if runner_exit != 0 or not record["quality"]["all_pass"]:
            print("stopping: episode failed evidence/quality gates; inspect before expanding", flush=True)
            break
        if kind in ("missing", "crash") or res.get("is_crash"):
            failures += 1
            if failures >= args.stop_after_failures:
                print(f"stopping: {failures} consecutive crash/missing episodes", flush=True)
                break
        else:
            failures = 0
        time.sleep(args.sleep_between)

    good = [r for r in results if r["runner_exit"] == 0 and r["quality"]["all_pass"]]
    summary = {
        "batch_id": batch_id,
        "requested": args.count,
        "finished": len(results),
        "good": len(good),
        "crash_or_missing": sum(r["kind"] in ("missing", "crash") for r in results),
        "quality_failed": len(results) - len(good),
        "max_seconds": args.max_seconds,
        "state": "finished" if len(good) == args.count else "stopped_quality_gate",
        "results": results,
        "totals": {
            "rows": sum(r.get("rows") or 0 for r in results),
            "damage_dealt": sum((r.get("metrics") or {}).get("damage_dealt") or 0
                                for r in results),
            "own_hp_lost": sum((r.get("metrics") or {}).get("own_hp_lost") or 0
                               for r in results),
            "first_damage_episodes": sum(
                1 for r in results if (r.get("stats") or {}).get("first_damage_at_s")),
            "visibility_violations": sum(
                (r.get("stats") or {}).get("visibility_violations") or 0
                for r in results),
        },
    }
    out = LEARN / f"batch-{batch_id}.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"batch report: {out}", flush=True)
    print(json.dumps(summary["totals"], ensure_ascii=False), flush=True)
    return 0 if len(good) == args.count else 2


if __name__ == "__main__":
    held = lockfile.acquire(ROOT / "build/dsh-experiment.lock")
    try:
        code = main()
    finally:
        lockfile.release(held)
    sys.exit(code)
