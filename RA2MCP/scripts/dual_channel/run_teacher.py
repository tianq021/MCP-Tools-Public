"""One authorized real game per invocation, no retries or neural training.

The new dual-channel sidecar is authoritative for this teacher's source and
labels; legacy outputs remain present with their original schema and limitations.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import time
import traceback

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from ai_teacher.strategic_teacher import StrategicTeacher, StrategyConfig, VERSION
from dual_channel.data import SidecarWriter, SCHEMA, sanitized, verify, write_json, sha
from dual_channel.skills import SkillRuntime

LEDGER = ROOT / "coordination/approvals/V3_TEACHER_RUN_AUTHORIZATION.json"


def preflight(out, game, port, seconds, authorization):
    checks = {}
    checks["isolated_game"] = game.parent == ROOT and game.name.startswith("test-game")
    checks["bounded_seconds"] = 0 < seconds <= 300
    checks["new_run"] = (not any((out / x).exists() for x in ("one-time-launch.claim", "dual-channel"))
                         and not any((out / "episodes").rglob("*")))
    checks["disk_space"] = shutil.disk_usage(ROOT).free > 3 * 1024**3
    for name in ("gamemd-spawn-ra2yrcpp.exe", "libra2yrcpp.dll", "spawn.ini", "spawnmap.ini", "ra2yrcpp.json"):
        checks[name] = (game / name).is_file()
    try:
        config = json.loads((game / "ra2yrcpp.json").read_text(encoding="utf-8"))
        checks["bridge_port"] = config.get("port") == port
    except Exception:
        checks["bridge_port"] = False
    with socket.socket() as sock:
        sock.settimeout(1)
        try:
            sock.bind(("127.0.0.1", port))
            checks["port_bindable"] = True
        except OSError as exc:
            checks["port_bindable"] = False
            checks["port_error"] = False
    proc = subprocess.run(["powershell", "-NoProfile", "-Command",
        "@(Get-Process -Name 'gamemd-spawn-ra2yrcpp','gamemd','yuri','Syringe' -ErrorAction SilentlyContinue).Count"],
        capture_output=True, text=True, timeout=15)
    checks["no_existing_game"] = proc.returncode == 0 and proc.stdout.strip() == "0"
    entry = None
    if LEDGER.exists():
        ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
        entry = next((r for r in ledger["runs"] if r["run"] == authorization), None)
    checks["authorization_unused"] = bool(entry and entry["status"] == "APPROVED_ONE_TIME"
        and entry["consumed_attempts"] == 0 and pathlib.Path(entry["run_dir"]).resolve() == out
        and pathlib.Path(entry["game_dir"]).resolve() == game and seconds <= entry["max_seconds"])
    return {"checks": checks, "pass": all(checks.values()), "game_dir": str(game),
            "game_fingerprints": {n: sha(game / n) for n in ("spawn.ini", "spawnmap.ini", "libra2yrcpp.dll") if (game / n).is_file()},
            "game_launches": 0, "training_started": False}


def consume(authorization, out):
    sys.path.insert(0, str(ROOT / "scripts/coordination"))
    import context_store as store
    import mcp_context_server as api
    with store.Lock():
        version = store.current_context_version()
        store.check_base_version(version)
        doc = json.loads(LEDGER.read_text(encoding="utf-8"))
        entry = next(r for r in doc["runs"] if r["run"] == authorization)
        if entry["status"] != "APPROVED_ONE_TIME" or entry["consumed_attempts"]:
            raise RuntimeError("authorization already consumed")
        with (out / "one-time-launch.claim").open("x", encoding="utf-8") as f:
            f.write(authorization)
        entry.update(status="CONSUMED_ATTEMPT", consumed_attempts=1, consumed_at=store.now_iso())
        store.atomic_write_json(LEDGER, doc)
    result = api.events_append("v3_teacher_attempt_consumed", authorization,
                              base_context_version=version, actor="Codex",
                              payload={"run_dir": str(out), "authorization": authorization})
    if not result.get("ok"):
        raise RuntimeError(result)


def finish_attempt(env, out, result, writer=None, runtime=None, failure=None):
    """Even a reset failure without kind/current must close the environment."""
    try:
        observation = sanitized(getattr(env, "current", {}) or {})
        kind = getattr(env, "kind", "launch_failed")
        if writer:
            runtime.observe(observation, terminal=True)
            writer.skills(runtime.drain())
            result["summary"] = writer.finish(observation, kind, failure)
            result["sidecar_verification"] = verify(writer.out)
            if not result["sidecar_verification"]["pass"]:
                raise RuntimeError("new sidecar verification failed")
        result.update(end_kind=kind, elapsed_s=observation.get("t"))
    except BaseException as exc:
        failure = failure or f"{type(exc).__name__}: {exc}"
        result.update(status="FAILED", finalization_error=f"{type(exc).__name__}: {exc}")
    finally:
        try:
            env.close()
        except BaseException as exc:
            failure = failure or f"cleanup: {exc}"
            result.update(status="FAILED", cleanup_error=str(exc))
        result.setdefault("end_kind", getattr(env, "kind", "launch_failed"))
        if not result.get("game_pid"):
            for path in sorted((out / "episodes").glob("failure-*.json")):
                evidence = json.loads(path.read_text(encoding="utf-8"))
                if evidence.get("pid"):
                    result.update(game_pid=evidence["pid"], game_launches=1)
        write_json(out / "RESULT.json", result)
    return failure


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", required=True)
    p.add_argument("--game-dir", default=str(ROOT / "test-game-capture-01"))
    p.add_argument("--authorization", required=True)
    p.add_argument("--seconds", type=float, default=300)
    p.add_argument("--port", type=int, default=13361)
    p.add_argument("--config")
    p.add_argument("--execute", action="store_true")
    args = p.parse_args()
    out, game = pathlib.Path(args.out).resolve(), pathlib.Path(args.game_dir).resolve()
    out.relative_to(ROOT)
    out.mkdir(parents=True, exist_ok=True)
    cfg = StrategyConfig(**(json.loads(pathlib.Path(args.config).read_text(encoding="utf-8"))["config"] if args.config else {}))
    teacher = StrategicTeacher(cfg)
    checks = preflight(out, game, args.port, args.seconds, args.authorization)
    write_json(out / "PREFLIGHT.json", checks)
    if not checks["pass"]:
        print(json.dumps(checks)); return 3
    # All trace features explicitly enabled before importing the environment.
    os.environ.update(DSH_V3_CAPTURE="1", DSH_EVENT_CAPTURE="1", DSH_COMMAND_CAPTURE="1",
                      DSH_COMMAND_CAPTURE_DIR=str(out / "command-capture"),
                      DSH_MAP_DIAG_DIR=str(out / "map-diag"), DSH_LOCK_DIR=str(ROOT / "build"),
                      DSH_RPC_TIMEOUT_S="15")
    from dsh_rl.full_env import RA2FullEnv
    from dsh_rl.env import RA2MicroEnv
    from dsh_episode import lockfile
    RA2MicroEnv.RESET_RETRY_DELAYS = ()
    # Check the actual shared lock path with this interpreter before consuming
    # an attempt; env construction alone did not expose Low-IL permission errors.
    try:
        held = lockfile.acquire(ROOT / "build/dsh-game.lock")
        lockfile.release(held)
        checks["checks"]["shared_lock_writable"] = True
    except Exception as exc:
        checks["checks"]["shared_lock_writable"] = False
        checks["lock_error"] = f"{type(exc).__name__}: {exc}"
    checks["pass"] = all(checks["checks"].values())
    write_json(out / "PREFLIGHT.json", checks)
    if not checks["pass"]:
        print(json.dumps(checks)); return 3

    class TeacherEnv(RA2FullEnv):
        def _control_metadata(self):
            return VERSION, {"control_mode": "external_teacher_301", "teacher": teacher.metadata(),
                             "student_model_loaded": False, "training_started": False}

        async def _commands(self, intents, source):
            # Correct source at the actual command recorder, without changing the core environment.
            return await super()._commands(intents, "collector_teacher" if source == "ppo" else source)

    env = TeacherEnv(out / "episodes", max_seconds=args.seconds, decision_seconds=0.5,
                     game_dir=game, port=args.port, kill_stale=False,
                     protection={"enabled": True, "stall_timeout_s": 5, "hard_step_timeout_s": 30,
                                 "screenshot_interval_s": 30})
    if not args.execute:
        env.loop.close()
        print("PREFLIGHT PASS: env constructed, no game, no training")
        return 0
    consume(args.authorization, out)
    writer = None
    runtime = None
    failure = None
    result = {"authorization": args.authorization, "teacher": teacher.metadata(), "status": "STARTING",
              "training_started": False, "game_launch_attempts": 1, "game_launches": 0}
    write_json(out / "RESULT.json", result)
    try:
        features, _ = env.reset()
        env.action_source = "collector_teacher"
        env.action_version = VERSION
        result.update(status="RUNNING", episode_id=env.writer.episode_id, game_pid=env.game_pid, game_launches=1)
        write_json(out / "RESULT.json", result)
        writer = SidecarWriter(out / "dual-channel", env.writer.episode_id,
            {**teacher.metadata(), "authorization": args.authorization,
             "action_contract_sha256": sha(out / "action-contract.json"),
             "catalog_sha256": sha(out / "public-catalog.json"),
             "game": checks["game_fingerprints"], "legacy_capture_not_training_authority": True})
        runtime = SkillRuntime(env.writer.episode_id)
        step = 0
        while env.kind == "running":
            before = sanitized(env.current)
            proposal = teacher.choose(before, env.full, env.starts, env.map_soa)
            # A 301 action must mean the same thing at selection and at send.
            intent = env.full.intent(proposal["action"], before, env.starts, env.map_soa) if proposal["action"] else None
            if intent != proposal["intent"]:
                raise RuntimeError("action interpretation changed between proposal and send")
            did = f"{env.writer.episode_id}:decision:{step:05d}"
            sid = runtime.start(proposal, before, did)
            after_features, reward, done, truncated, info = env.step(proposal["action"])
            after = sanitized(env.current)
            acks = list(env._dec_acks)
            success = (not proposal["action"] or bool(acks) and all(not a.get("error") for a in acks))
            record = {"schema": SCHEMA, "episode_id": env.writer.episode_id, "decision_id": did,
                "source": "collector_teacher", "teacher_id": VERSION, "teacher_sha256": teacher.metadata()["source_sha256"],
                "config_sha256": teacher.metadata()["config_sha256"], "skill_instance_id": sid,
                "skill_id": proposal["skill_id"], "phase": proposal["phase"], "reason": proposal["reason"],
                "observation_frame": before["frame"], "next_frame": after["frame"],
                "observation": before, "next_observation": after,
                "features": features.tolist(), "next_features": after_features.tolist(),
                "action_idx": proposal["action"], "action_mask": proposal["action_mask"], "intent": intent,
                "slot_bindings": proposal["slot_bindings"], "target_bindings": proposal["target_bindings"],
                "feedback": {"transport_ok": success, "ack_records": acks, "applied": "UNKNOWN", "effect": "UNKNOWN"},
                "reward": reward, "reward_parts": info.get("reward_parts", {}),
                "reward_source": "unchanged_V4_environment_return; not a per-action causal label",
                "terminated": done, "truncated": truncated,
                "admission": {"bc_label_eligible": bool(proposal["action"] and success and info.get("action_executed")),
                              "actor_eligible": False, "critic_eligible": False, "effect_label_eligible": False,
                              "reason": "teacher trace, no student behavior probability; effect attribution unknown"}}
            writer.append(record)
            runtime.observe(after, terminal=env.kind != "running")
            writer.skills(runtime.drain())
            features = after_features
            step += 1
            if step % 50 == 0:
                print(json.dumps({"step": step, "t": after["t"], "phase": proposal["phase"],
                                  "skill": proposal["skill_id"], "buildings": proposal["own_building_types"],
                                  "army": proposal["army_units"]}), flush=True)
        result["status"] = "FINISHED"
    except BaseException as exc:
        failure = f"{type(exc).__name__}: {exc}"
        result.update(status="FAILED", failure=failure, traceback=traceback.format_exc())
        write_json(out / "RESULT.json", result)
    finally:
        failure = finish_attempt(env, out, result, writer, runtime, failure)
    print(json.dumps({"status": result["status"], "end_kind": result["end_kind"],
                      "sidecar_verification": result.get("sidecar_verification"), "failure": failure}), flush=True)
    return 1 if failure else 0


if __name__ == "__main__":
    sys.exit(main())
