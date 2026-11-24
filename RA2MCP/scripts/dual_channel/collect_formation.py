"""One bounded raw formation-teacher game, using existing Bridge commands only."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import sys
import time
import traceback

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from ai_teacher.formation_teacher import FormationTeacher, FormationConfig, VERSION
from dual_channel import run_teacher as common
from dual_channel.data import write_json, sha, sanitized
from dual_channel.formation_data import FormationWriter, SCHEMA, verify

LEDGER = ROOT / "coordination/approvals/V3_FORMATION_RUN_AUTHORIZATION.json"


def rejection_class(error, skipped=False):
    if skipped:
        return "STALE_REJECTED"
    if not error:
        return "ACCEPTED"
    error = str(error).lower()
    if any(x in error for x in ("timeout", "timed out", "connection", "transport", "websocket")):
        return "TRANSPORT_FAILURE"
    if "native_error" in error and ("object not found" in error or "no longer exists" in error):
        return "STALE_REJECTED"
    return "OTHER_REJECTED"


async def execute_plan(env, plan, decision_id):
    from dsh_m1 import command_capture
    ext = command_capture.extractor()
    if ext is None:
        raise RuntimeError("native command capture not installed")
    started = time.monotonic()
    first_record = len(ext.records)
    acks, invalid, outcomes = [], 0, []
    for i, command in enumerate(plan["commands"]):
        if not command_capture.set_context(action_idx=command["mapping_301"]["action_idx"],
                decision_id=decision_id, issued_frame=plan["observation_frame"],
                action_id=f"{decision_id}:command:{i:02d}"):
            raise RuntimeError("command provenance context unavailable")
        env._dec_acks = []
        invalid_one = await env._commands([command["intent"]], "formation_teacher")
        invalid += invalid_one
        acks.extend(env._dec_acks)
        errors = [r.get("error") for r in env._dec_acks if r.get("error")]
        skipped = any(r.get("event") == "order_skipped" for r in env._dec_acks)
        classification = rejection_class(errors[0] if errors else None, skipped)
        outcomes.append({"action_id": f"{decision_id}:command:{i:02d}",
                         "role": command["role"], "classification": classification,
                         "native_error": errors[0] if errors else None, "invalid": invalid_one,
                         "applied_status": "UNKNOWN"})
    await asyncio.sleep(max(.01, .5 - (time.monotonic() - started)))
    await env._capture()
    status = command_capture.status()
    if status.get("errors"):
        raise RuntimeError(f"command capture failed: {status['errors']}")
    return {"ack_records": acks, "invalid": invalid,
            "command_outcomes": outcomes,
            "native_commands": [dict(r) for r in ext.records[first_record:]]}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", required=True)
    p.add_argument("--authorization", required=True)
    p.add_argument("--game-dir", default=str(ROOT / "test-game-v3-teacher"))
    p.add_argument("--port", type=int, default=14621)
    p.add_argument("--seconds", type=float, default=300)
    p.add_argument("--config")
    p.add_argument("--execute", action="store_true")
    a = p.parse_args()
    out, game = pathlib.Path(a.out).resolve(), pathlib.Path(a.game_dir).resolve()
    out.relative_to(ROOT)
    out.mkdir(parents=True, exist_ok=True)
    common.LEDGER = LEDGER  # Process-local reuse of the same checked ledger API.
    pre = common.preflight(out, game, a.port, a.seconds, a.authorization)
    pre["checks"]["new_formation_trace"] = not (out / "formation").exists()
    pre["pass"] = all(pre["checks"].values())
    write_json(out / "PREFLIGHT.json", pre)
    if not pre["pass"]:
        print(json.dumps(pre)); return 3
    os.environ.update(DSH_V3_CAPTURE="1", DSH_EVENT_CAPTURE="1", DSH_COMMAND_CAPTURE="1",
        DSH_COMMAND_CAPTURE_DIR=str(out / "command-capture"), DSH_MAP_DIAG_DIR=str(out / "map-diag"),
        DSH_LOCK_DIR=str(ROOT / "build"))
    from dsh_rl.full_env import RA2FullEnv
    from dsh_rl.env import RA2MicroEnv
    from dsh_episode import lockfile
    from dsh_m1 import command_capture
    RA2MicroEnv.RESET_RETRY_DELAYS = ()
    held = lockfile.acquire(ROOT / "build/dsh-game.lock")
    lockfile.release(held)
    pre["checks"]["shared_lock_writable"] = True
    config = FormationConfig(**(json.loads(pathlib.Path(a.config).read_text(encoding="utf-8"))["config"] if a.config else {}))
    teacher = FormationTeacher(config)

    class FormationEnv(RA2FullEnv):
        def _control_metadata(self):
            return VERSION, {"control_mode": "raw_explicit_formation", "teacher": teacher.metadata(),
                             "learning_eligible": False, "student_action_contract_unchanged": True}

    env = FormationEnv(out / "episodes", max_seconds=a.seconds, decision_seconds=.5,
        game_dir=game, port=a.port, kill_stale=False,
        protection={"enabled": True, "stall_timeout_s": 5, "hard_step_timeout_s": 30, "screenshot_interval_s": 30})
    pre["checks"]["env_constructed"] = True
    write_json(out / "PREFLIGHT.json", pre)
    if not a.execute:
        env.loop.close()
        print("PREFLIGHT PASS: formation env constructed; game launches=0")
        return 0
    common.consume(a.authorization, out)
    result = {"authorization": a.authorization, "status": "STARTING", "teacher": teacher.metadata(),
              "collector_source_sha256": sha(pathlib.Path(__file__)),
              "game_launches": 0, "training_started": False, "student_model_loaded": False}
    write_json(out / "RESULT.json", result)
    writer, failure = None, None
    try:
        env.reset()
        result.update(status="RUNNING", game_launches=1, game_pid=env.game_pid, episode_id=env.writer.episode_id)
        write_json(out / "RESULT.json", result)
        if not command_capture.installed():
            raise RuntimeError("native command capture missing after reset")
        writer = FormationWriter(out / "formation", {**teacher.metadata(), "episode_id": env.writer.episode_id,
            "authorization": a.authorization, "game": pre["game_fingerprints"],
            "action_contract_sha256": sha(out / "action-contract.json"),
            "catalog_sha256": sha(out / "public-catalog.json"),
            "command_capture": "command-capture/action_command_records.jsonl",
            "collector_source_sha256": sha(pathlib.Path(__file__)),
            "native_outcome_semantics": "accepted RPC / stale rejection / transport failure are distinct; no blind resend",
            "command_level_301_encoding_is_not_single_step_label": True})
        step, invalid_total, serious_rejections, stale_rejections = 0, 0, 0, 0
        while env.kind == "running":
            before = sanitized(env.current)
            plan = teacher.plan(before, env.full, env.starts, env.map_soa)
            did = f"{env.writer.episode_id}:formation:{step:05d}"
            execution = env._run(execute_plan(env, plan, did), name="formation_commands_and_observation")
            after = sanitized(env.current)
            invalid_total += execution["invalid"]
            for outcome in execution["command_outcomes"]:
                if outcome["classification"] == "ACCEPTED":
                    serious_rejections = 0
                elif outcome["classification"] == "STALE_REJECTED":
                    stale_rejections += 1
                else:
                    serious_rejections += 1
            candidate_301 = bool(plan["decision_301_eligible"] and execution["invalid"] == 0
                                 and len(execution["native_commands"]) == 1
                                 and execution["native_commands"][0].get("transport_result", {}).get("ok"))
            row = {"schema": SCHEMA, "source": "formation_teacher", "teacher_id": VERSION,
                "teacher_sha256": teacher.metadata()["source_sha256"], "decision_id": did,
                "observation_frame": before["frame"], "next_frame": after["frame"],
                "observation": before, "next_observation": after, "plan": plan,
                "native_commands": execution["native_commands"], "ack_records": execution["ack_records"],
                "command_outcomes": execution["command_outcomes"],
                "invalid_commands": execution["invalid"], "decision_301_eligible": candidate_301,
                "actor_eligible": False, "effect_label_eligible": False, "formal_training_eligible": False,
                "reward": None, "reward_source": "NOT_COMPUTED_RAW_TEACHER_NO_PPO_STEP",
                "applied_status": "UNKNOWN", "effect_status": "UNKNOWN"}
            writer.append(row)
            step += 1
            if step % 50 == 0:
                print(json.dumps({"step": step, "t": after.get("t"), "phase": plan["phase"],
                    "army": plan["combat_units"], "assigned": plan["assigned_units"],
                    "outside8": len(plan["outside_legacy_slots"]), "commands": len(plan["commands"])}), flush=True)
            if any(o["classification"] == "TRANSPORT_FAILURE" for o in execution["command_outcomes"]):
                raise RuntimeError("transport failed; do not resend")
            if serious_rejections >= 3:
                raise RuntimeError("three non-stale native rejections without a successful command")
        result.update(status="FINISHED", invalid_commands=invalid_total, stale_rejections=stale_rejections)
    except BaseException as exc:
        failure = f"{type(exc).__name__}: {exc}"
        result.update(status="FAILED", failure=failure, traceback=traceback.format_exc())
        write_json(out / "RESULT.json", result)
    finally:
        try:
            if writer:
                result["summary"] = writer.finish(sanitized(getattr(env, "current", {}) or {}),
                    getattr(env, "kind", "launch_failed"), getattr(env, "_last_outcome_flags", {}), failure)
                result["formation_verification"] = verify(writer.out)
                if not result["formation_verification"]["pass"]:
                    failure = failure or "formation verification failed"
                    result["status"] = "FAILED"
        except BaseException as exc:
            failure = failure or f"{type(exc).__name__}: {exc}"
            result.update(status="FAILED", finalization_error=str(exc))
        failure = common.finish_attempt(env, out, result, failure=failure)
    print(json.dumps({"status": result["status"], "end_kind": result["end_kind"],
        "formation_verification": result.get("formation_verification"), "failure": failure}), flush=True)
    return 1 if failure else 0


if __name__ == "__main__":
    if sys.version_info[:2] != (3, 10):
        raise SystemExit("Use existing F:/conda/envs/python_mmx_LLM/python.exe (Python 3.10)")
    sys.path.append(str(ROOT / ".venv-rl/Lib/site-packages"))
    sys.exit(main())
