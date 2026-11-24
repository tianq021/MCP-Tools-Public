"""Run the CollectorTeacher to collect demonstrations (REAL RUN GATED).

Default mode is a pure-offline self-check (no environment construction, no game).
``--preflight-only`` constructs the real environment object but never calls
``reset()`` (same proven pattern as ``run_wdzs_teacher.py``); still no game.
``--execute`` is fail-closed: it refuses unless
``coordination/approvals/RUN_AUTHORIZATION_LEDGER.json`` contains an unused
``COLLECTOR-TEACHER-01`` APPROVED_ONE_TIME entry. This script never creates or
modifies the ledger/token and never retries a failed run.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
import traceback

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "ai_teacher"))

from ai_teacher.collector_teacher import (  # noqa: E402
    POLICY_VERSION, CollectorConfig, CollectorTeacher)
from dsh_rl.full_env import RA2FullEnv  # noqa: E402
from dsh_rl.scripted_policy_v2 import HostAdapter  # noqa: E402

AUTH_NAME = "COLLECTOR-TEACHER-01"
LEDGER = ROOT / "coordination" / "approvals" / "RUN_AUTHORIZATION_LEDGER.json"


def _run_authorized(name: str = AUTH_NAME) -> tuple[bool, str]:
    if not LEDGER.is_file():
        return False, f"ledger missing: {LEDGER}"
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    runs = (ledger.get("authorization") or {}).get("runs") or []
    entry = next((r for r in runs if r.get("run") == name), None)
    if entry is None:
        return False, f"no {name} entry in ledger"
    if entry.get("status") != "APPROVED_ONE_TIME":
        return False, f"{name} status={entry.get('status')}"
    if int(entry.get("consumed_games") or 0) != 0:
        return False, f"{name} already consumed"
    return True, f"authorized one-time (unused): {name}"


def _collector_preflight() -> dict:
    meta = CollectorTeacher().policy_metadata()
    return {"policy_version": POLICY_VERSION, "metadata": meta,
            "has_choose": callable(getattr(CollectorTeacher, "choose", None)),
            "action_n": meta["action_n"]}


def _construct_env(out: pathlib.Path, game_dir: str, seconds: float,
                   decision_seconds: float, port: int) -> RA2FullEnv:
    return RA2FullEnv(out / "episodes", max_seconds=seconds,
                      decision_seconds=decision_seconds, game_dir=game_dir,
                      port=port, kill_stale=False,
                      protection={"enabled": True, "stall_timeout_s": 5,
                                  "hard_step_timeout_s": 30,
                                  "screenshot_interval_s": 30})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(ROOT / "reports/teacher-collector-skeleton-2026-11-02/preflight"))
    ap.add_argument("--game-dir", default=str(ROOT / "test-game-capture-01"))
    ap.add_argument("--seconds", type=float, default=300.0)
    ap.add_argument("--decision-seconds", type=float, default=0.5)
    ap.add_argument("--port", type=int, default=13361)
    ap.add_argument("--preflight-only", action="store_true",
                    help="construct the env object (no reset, no game) and exit")
    ap.add_argument("--execute", action="store_true",
                    help="REAL RUN: requires unused one-time ledger authorization")
    ap.add_argument("--authorization-name", default=AUTH_NAME,
                    help="ledger run name to consume (default: %(default)s)")
    ap.add_argument("--check-auth", action="store_true",
                    help="only check the ledger authorization for --authorization-name; no env/game")
    a = ap.parse_args()
    if a.check_auth:
        ok, why = _run_authorized(a.authorization_name)
        print(json.dumps({"authorization": a.authorization_name, "ok": ok,
                          "detail": why}, ensure_ascii=False))
        return 0 if ok else 3
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    pre = _collector_preflight()
    result = {"policy_version": POLICY_VERSION, "preflight": pre,
              "executed": False, "game_launches": 0,
              "training_started": False, "learning_eligible": True}
    (out / "COLLECT_PREFLIGHT.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if not a.execute:
        if a.preflight_only:
            env = _construct_env(out, a.game_dir, a.seconds, a.decision_seconds, a.port)
            try:
                result["env_constructed"] = True
                result["env_class"] = type(env).__name__
                print("CollectorTeacher/environment construction PASS; game launches=0")
            finally:
                env.loop.close()
            (out / "COLLECT_PREFLIGHT.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        else:
            print("offline self-check PASS (no env constructed, no game); use --preflight-only "
                  "for env-construction check, --execute for a gated real run")
        return 0

    ok, why = _run_authorized(a.authorization_name)
    if not ok:
        print(f"REFUSING real run: {why}", file=sys.stderr)
        return 3

    result["status"] = "STARTING"
    result["game_dir"] = a.game_dir
    (out / "COLLECT_RESULT.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    class CollectorEnv(RA2FullEnv):
        def _control_metadata(self):
            return (POLICY_VERSION,
                    {"control_mode": "external_adapter_301_collector",
                     "student_301_contract_unchanged": True,
                     "learning_eligible": True})

    env = None
    teacher = CollectorTeacher(config=CollectorConfig(), spec=None, seed=0)
    trace = (out / "collector-decisions.jsonl").open("x", encoding="utf-8")
    try:
        env = CollectorEnv(out / "episodes", max_seconds=a.seconds,
                           decision_seconds=a.decision_seconds, game_dir=a.game_dir,
                           port=a.port, kill_stale=False,
                           protection={"enabled": True, "stall_timeout_s": 5,
                                       "hard_step_timeout_s": 30,
                                       "screenshot_interval_s": 30})
        obs, info = env.reset()
        env.action_source = "collector_teacher"
        env.action_version = POLICY_VERSION
        host = HostAdapter(env)
        result["episode_id"] = env.writer.episode_id
        result["game_pid"] = env.game_pid
        result["status"] = "RUNNING"
        steps = 0
        while env.kind == "running":
            host.refresh()
            try:
                action, label, rationale = teacher.choose(host)
            except Exception as exc:  # noqa: BLE001
                # Fail-closed policy must not abort a one-shot data run: fall
                # back to being idle and record the anomaly (never retried).
                masks = env.action_masks()
                action = 0 if bool(masks[0]) else int(np.flatnonzero(masks)[0])
                label = "noop"
                rationale = {"policy_version": POLICY_VERSION, "task": "NOOP",
                             "action": action, "label": label,
                             "fallback_error": f"{type(exc).__name__}: {exc}"}
                result.setdefault("teacher_fallbacks", 0)
                result["teacher_fallbacks"] += 1
            host.refresh()
            obs, reward, done, truncated, info = env.step(action)
            steps += 1
            trace.write(json.dumps({"step": steps, "action": action, "label": label,
                                    "rationale": rationale,
                                    "frame": (env.current or {}).get("frame"),
                                    "reward": reward}, ensure_ascii=False) + "\n")
            trace.flush()
            if steps >= int(a.seconds / a.decision_seconds) + 60:
                break
        result.update(status="FINISHED", steps=steps, end_kind=env.kind,
                      elapsed_s=(env.current or {}).get("t"))
        env._finish(env.kind)
    except BaseException as exc:  # noqa: BLE001
        result.update(status="FAILED", error=f"{type(exc).__name__}: {exc}",
                      traceback=traceback.format_exc())
        raise
    finally:
        trace.close()
        if env is not None:
            env.close()
        result["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
        (out / "COLLECT_RESULT.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
