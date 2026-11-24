"""A/B evaluation: rule-v2 policy vs a v2 model policy on the same map/env.

Runs episodes alternately (rule, model, rule, model, ...) through the episode
runner, then compares outcome/behavior metrics and registers a checkpoint.

Usage:
  .venv\\Scripts\\python.exe scripts\\dsh_ab_eval.py \
      --model-dir reports/dsh-learning/models/bc-v2-demo \
      --episodes-per-side 2 --max-seconds 240
"""
import argparse
import json
import pathlib
import subprocess
import sys
import time
import uuid
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
LEARN = ROOT / "reports" / "dsh-learning"
LOGS = ROOT / "build" / "ab-logs"
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
RUNNER = ROOT / "scripts" / "dsh_episode_runner.py"
sys.path.insert(0, str(ROOT / "scripts"))
import dsh_model_registry as registry  # noqa: E402
from dsh_episode import quality, lockfile, schema  # noqa: E402


def read_result(episodes_dir, episode_id):
    p = pathlib.Path(episodes_dir) / f"{episode_id}.result.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return {}
    p2 = pathlib.Path(episodes_dir) / f"{episode_id}.jsonl"
    if not p2.exists():
        return {}
    sys.path.insert(0, str(ROOT / "scripts"))
    from dsh_episode import schema
    try:
        return schema.read_episode(p2).get("result") or {}
    except (ValueError, OSError):
        return {}


def run_episode(side, model_dir, threshold, max_seconds, episodes_dir, idx):
    episode_id = f"ab-{side}-{time.strftime('%Y%m%d-%H%M%S')}-{idx:02d}-{uuid.uuid4().hex[:6]}"
    cmd = [PY, str(RUNNER), "--episode-id", episode_id,
           "--episodes-dir", str(episodes_dir),
           "--max-seconds", str(max_seconds), "--map-patch", "--policy", side]
    if side == "model":
        cmd += ["--model-dir", str(model_dir),
                "--model-threshold", str(threshold)]
    LOGS.mkdir(parents=True, exist_ok=True)
    log_path = LOGS / f"{episode_id}.log"
    print(f"[{time.strftime('%H:%M:%S')}] {side}: {episode_id}", flush=True)
    t0 = time.monotonic()
    with open(log_path, "w", encoding="utf-8") as log:
        try:
            proc = subprocess.run(cmd, cwd=str(ROOT), stdout=log,
                                  stderr=subprocess.STDOUT,
                                  timeout=max_seconds + 240)
            returncode = proc.returncode
        except subprocess.TimeoutExpired:
            returncode = 124
    res = read_result(episodes_dir, episode_id)
    episode_path = pathlib.Path(episodes_dir) / f"{episode_id}.jsonl"
    evidence = quality.inspect_episode(episode_path)
    initial_cell = None
    if episode_path.exists() and not evidence.get("errors"):
        ep = schema.read_episode(episode_path)
        if ep["result"] != res:
            evidence["errors"].append("result sidecar does not match raw recording")
        initial_obs = next((r for r in ep["rows"] if r.get("type") == "observation"), {})
        initial_cell = next((o.get("cell") for o in initial_obs.get("own_objects", [])
                             if "Construction" in o.get("type_name", "")), None)
    return {"side": side, "episode_id": episode_id,
            "returncode": returncode, "quality": evidence, "initial_cell": initial_cell,
            "wall_s": round(time.monotonic() - t0, 1),
            "result": res, "log": str(log_path)}


def summarize(records):
    out = {}
    for side in ("rule", "model"):
        rows = [r for r in records if r["side"] == side and r.get("result")]
        kinds = {}
        for r in rows:
            k = r["result"].get("kind", "missing")
            kinds[k] = kinds.get(k, 0) + 1
        metrics = [(r.get("quality") or {}).get("metrics") or r["result"].get("metrics") or {} for r in rows]
        stats = [r["result"].get("stats") or {} for r in rows]
        out[side] = {
            "episodes": len(rows),
            "kinds": kinds,
            "mean_survive_s": round(sum(r["result"].get("elapsed_s", 0)
                                        for r in rows) / max(1, len(rows)), 1),
            "total_damage_dealt": sum(m.get("damage_dealt", 0) for m in metrics),
            "total_own_hp_lost": sum(m.get("own_hp_lost", 0) for m in metrics),
            "total_visibility_violations": sum(
                s.get("visibility_violations", 0) for s in stats),
            "total_invalid_orders": sum(s.get("invalid_orders", 0) for s in stats),
            "model_stats": [s.get("policy") for s in stats if s.get("policy")],
            "missing_results": sum(r["side"] == side and not r.get("result") for r in records),
            "model_orders_ok": sum((r.get("quality") or {}).get("model_orders_ok", 0) for r in rows),
            "orders_ok": sum((r.get("quality") or {}).get("orders_ok", 0) for r in rows),
        }
        out[side]["model_order_ratio"] = out[side]["model_orders_ok"] / max(1, out[side]["orders_ok"])
    return out


def fatal_record(record):
    q = record.get("quality") or {}
    gates = q.get("gates") or {}
    return (record.get("returncode") != 0 or not record.get("result")
            or bool(q.get("errors"))
            or not all(gates.get(k, False) for k in
                       ("schema_result_checkpoint", "real_complete", "visibility", "opponents_only",
                        "metrics_match_raw", "action_errors")))


def evaluate(records, expected_per_side):
    reasons = []
    for side in ("rule", "model"):
        rows = [r for r in records if r["side"] == side]
        if len(rows) != expected_per_side:
            reasons.append(f"{side}: incomplete evaluation")
        for r in rows:
            if fatal_record(r):
                reasons.append(f"{r['episode_id']}: evidence/safety gate failed")
            if side == "rule" and not (r.get("quality") or {}).get("all_pass"):
                reasons.append(f"{r['episode_id']}: rule did not establish valid combat")
            if side == "model" and (r.get("quality") or {}).get("model_order_ratio", 0) < 0.5:
                reasons.append(f"{r['episode_id']}: model executed less than 50% of successful orders")
    # Every pair must share initial placement and all recorded game assets.
    for i in range(0, len(records) - 1, 2):
        a, b = records[i:i + 2]
        ea, eb = (a.get("result") or {}).get("environment") or {}, (b.get("result") or {}).get("environment") or {}
        keys = ("game_exe_sha256", "bridge_dll_sha256", "spawn_ini_sha256", "map_sha256", "rules_sha256")
        if any(not ea.get(k) or ea.get(k) != eb.get(k) for k in keys[:4]):
            reasons.append(f"pair {i // 2}: game asset/configuration mismatch or missing fingerprint")
        if ea.get("rules_sha256") != eb.get("rules_sha256"):
            reasons.append(f"pair {i // 2}: rules mismatch")
        if not ea.get("collector_code_sha256") or ea.get("collector_code_sha256") != eb.get("collector_code_sha256"):
            reasons.append(f"pair {i // 2}: controller code changed or missing fingerprint")
        if a.get("initial_cell") is None or a.get("initial_cell") != b.get("initial_cell"):
            reasons.append(f"pair {i // 2}: initial placement mismatch")
    valid = not reasons
    s = summarize(records)
    better_damage = s["model"]["total_damage_dealt"] > s["rule"]["total_damage_dealt"]
    return {"valid": valid, "reasons": reasons,
            "verdict": ("invalid_evaluation" if not valid else
                        "candidate_needs_larger_evaluation" if better_damage else "candidate_not_better"),
            "auto_promote": False,
            "note": "Timeout is truncated evidence, not a win; no champion promotion from this small experiment."}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", default="reports/dsh-learning/models/bc-v2-demo")
    ap.add_argument("--episodes-per-side", type=int, default=2)
    ap.add_argument("--max-seconds", type=float, default=240.0)
    ap.add_argument("--threshold", type=float, default=0.35)
    ap.add_argument("--episodes-dir", default=str(LEARN / "episodes"))
    ap.add_argument("--fixed-spawns", action="store_true",
                    help="temporarily pin Multi1/Multi2 to map start 0/1; restore spawn.ini afterward")
    a = ap.parse_args()
    if a.episodes_per_side < 1 or a.max_seconds <= 0:
        ap.error("episodes-per-side and max-seconds must be positive")
    # Fail before launching a game if the requested model cannot be loaded.
    sys.path.insert(0, str(ROOT / "scripts/dsh_train"))
    from infer_v2 import LocalModelV2
    LocalModelV2(a.model_dir, enabled=True)

    episodes_dir = pathlib.Path(a.episodes_dir)
    records = []
    LEARN.mkdir(parents=True, exist_ok=True)
    out = LEARN / f"ab-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}.json"
    held = lockfile.acquire(ROOT / "build/dsh-experiment.lock")
    original_spawn = None
    spawn_path = ROOT / "test-game/spawn.ini"
    restoration_backup = None
    try:
        if a.fixed_spawns:
            game_held = lockfile.acquire(ROOT / "build/dsh-game.lock")
            try:
                original_spawn = spawn_path.read_bytes()
                restoration_backup = ROOT / "backups" / f"{out.stem}-spawn.ini"
                restoration_backup.parent.mkdir(parents=True, exist_ok=True)
                restoration_backup.write_bytes(original_spawn)
                pinned = re.sub(rb"(?ims)^\[SpawnLocations\].*?(?=^\[|\Z)", b"", original_spawn)
                pinned += b"\n[SpawnLocations]\nMulti1=0\nMulti2=1\n"
                spawn_path.write_bytes(pinned)
            finally:
                lockfile.release(game_held)
        for i in range(a.episodes_per_side):
            for side in ("rule", "model"):
                record = run_episode(side, a.model_dir, a.threshold,
                                     a.max_seconds, episodes_dir, i)
                records.append(record)
                out.write_text(json.dumps({"state": "running", "records": records}, indent=2), encoding="utf-8")
                if fatal_record(record):
                    break
            if fatal_record(records[-1]):
                break
    finally:
        try:
            if original_spawn is not None:
                game_held = lockfile.acquire(ROOT / "build/dsh-game.lock")
                try:
                    spawn_path.write_bytes(original_spawn)
                finally:
                    lockfile.release(game_held)
        finally:
            lockfile.release(held)
    summary = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "model_dir": a.model_dir, "threshold": a.threshold,
               "max_seconds": a.max_seconds,
               "episodes_per_side": a.episodes_per_side,
               "summary": summarize(records), "records": records,
               "evaluation": evaluate(records, a.episodes_per_side), "state": "finished",
               "fixed_spawns": a.fixed_spawns,
               "spawn_restoration_backup": str(restoration_backup) if restoration_backup else None,
               "spawn_restored": original_spawn is None or spawn_path.read_bytes() == original_spawn}
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    state = registry.load()
    reg = registry.add_checkpoint(state, f"ab-{out.stem}", a.model_dir,
                                  summary["summary"])
    md = [f"# A/B 评估 {out.stem}", "",
          f"- model: `{a.model_dir}` (threshold {a.threshold})",
          f"- rule vs model: {a.episodes_per_side} 局/边", "",
          "| side | episodes | kinds | mean survive s | damage | own hp lost | "
          "invalid orders | visibility violations |",
          "|---|---|---|---|---|---|---|---|"]
    for side, s in summary["summary"].items():
        md.append(f"| {side} | {s['episodes']} | {json.dumps(s['kinds'], ensure_ascii=False)} "
                  f"| {s['mean_survive_s']} | {s['total_damage_dealt']} "
                  f"| {s['total_own_hp_lost']} | {s['total_invalid_orders']} "
                  f"| {s['total_visibility_violations']} |")
    md.append("")
    md.append(f"- registry checkpoint: `{reg['episode_id']}`")
    md += [f"- evaluation: `{summary['evaluation']['verdict']}`",
           f"- reasons: {summary['evaluation']['reasons']}", "- auto promotion: disabled"]
    out.with_suffix(".md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(out), "summary": summary["summary"]},
                     ensure_ascii=False, indent=2))
    return 0 if summary["evaluation"]["valid"] else 2


if __name__ == "__main__":
    sys.exit(main())
