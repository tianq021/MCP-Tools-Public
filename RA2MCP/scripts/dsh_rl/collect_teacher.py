"""Collect (state, action) demonstrations from the scripted teacher.

Runs the scripted macro policy (built from the parsed AI scripts) inside the real
game environment and records observations + actions + masks as a BC dataset.

  .venv\\Scripts\\python.exe scripts\\dsh_rl\\collect_teacher.py --episodes 1 --episode-seconds 100
"""
import argparse
import os
import json
import pathlib
import sys
import time
import uuid

import hashlib
import numpy as np
from dsh_m1 import decision_schema as DS

BASE_PORT = int(os.environ.get("RA2YR_BASE_PORT", "14621"))
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from dsh_episode import lockfile  # noqa: E402
from dsh_rl.full_env import RA2FullEnv  # noqa: E402
from dsh_rl.env import RunStalled  # noqa: E402  (RUN02 run-protection abort)
from dsh_rl import full_spec as S  # noqa: E402
from dsh_rl.scripted_policy import (ScriptedTeacher, TEACHER_VARIANTS,  # noqa: E402
    intent_label, LABELS, LABEL_TO_INDEX)
from dsh_rl.scripted_policy_v2 import ScriptedTeacherV2, HostAdapter as V2HostAdapter, POLICY_VERSION as V2_POLICY_VERSION  # noqa: E402
from dsh_rl.scripted_policy_v2 import ScriptedTeacherV2, HostAdapter as V2HostAdapter, POLICY_VERSION as V2_POLICY_VERSION  # noqa: E402
from dsh_rl.scripted_policy_v2 import ScriptedTeacherV2, HostAdapter as V2HostAdapter, POLICY_VERSION as V2_POLICY_VERSION  # noqa: E402
from dsh_rl.scripted_policy_v2 import ScriptedTeacherV2, HostAdapter as V2HostAdapter, POLICY_VERSION as V2_POLICY_VERSION  # noqa: E402


def episode_stats(env):
    row = env.current
    own = [o for o in row.get("own_objects", []) if S.placed(o)]
    buildings = [o for o in own if S.is_building(o)]
    combat = [o for o in own if not S.is_building(o) and not S.is_mcv(o)]
    return {"t": round(float(row.get("t", 0)), 1), "money": int(row.get("money", 0)),
            "buildings": len(buildings), "units": len(combat),
            "building_types": sorted({o.get("type_name", "") for o in buildings}),
            "unit_types": sorted({o.get("type_name", "") for o in combat})}


def _teacher_version(variant):
    if variant == 'extreme_v2':
        src = pathlib.Path(__file__).with_name('scripted_policy_v2.py')
        return 'scripted_teacher_v2:%s:%s' % (variant, hashlib.sha256(src.read_bytes()).hexdigest()[:12])
    src = pathlib.Path(__file__).with_name('scripted_policy.py')
    return 'scripted_teacher:%s:%s' % (variant, hashlib.sha256(src.read_bytes()).hexdigest()[:12])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--episodes", type=int, default=1)
    ap.add_argument("--episode-seconds", type=float, default=150)
    ap.add_argument("--decision-seconds", type=float, default=0.5)
    ap.add_argument("--out", default=None)
    ap.add_argument("--port", type=int, default=BASE_PORT)
    ap.add_argument("--instance", type=int, default=0)
    ap.add_argument("--game-dir", default=None)
    ap.add_argument("--render-off", action="store_true")
    ap.add_argument("--teacher-variant", choices=tuple(TEACHER_VARIANTS) + ("extreme_v2",), default="extreme")
    ap.add_argument("--no-kill-stale-games", dest="kill_stale", action="store_false", default=None)
    # ---- RUN02 run-protection (real run must keep the game window visible/focused) ----
    ap.add_argument("--no-protection", dest="protection_enabled", action="store_false",
                    default=True, help="disable the RUN02 watchdog/RPC guard (offline only)")
    ap.add_argument("--stall-seconds", type=float, default=None,
                    help="frame-stall timeout (default: DSH_STALL_TIMEOUT_S or 8s)")
    ap.add_argument("--hard-step-timeout", type=float, default=None,
                    help="hard wall-clock cap per step/reset (default: DSH_HARD_STEP_TIMEOUT_S)")
    ap.add_argument("--screenshot-interval", type=float, default=None,
                    help="running screenshot interval seconds (default 60)")
    ap.add_argument("--event-buffer-frames", type=int, default=None,
                    help="light event-buffer capacity in frames (default 120)")
    ap.add_argument("--require-approval", action="store_true",
                    help="refuse to start unless the G0-L1 approval token exists "
                         "(fail-closed real-run gate; never creates the token)")
    ap.add_argument("--v3-capture", action="store_true",
                    help="enable episode-v3 raw event capture (sets DSH_V3_CAPTURE=1)")
    a = ap.parse_args()

    if a.require_approval:
        token = ROOT / "coordination" / "approvals" / "G0L1_THIRD_APPROVED.flag"
        if not token.exists():
            raise SystemExit(f"refusing real run: approval token missing: {token}")
    if a.v3_capture:
        os.environ["DSH_V3_CAPTURE"] = "1"

    protection = {"enabled": bool(a.protection_enabled)}
    for arg_name, cfg_key in (("stall_seconds", "stall_timeout_s"),
                              ("hard_step_timeout", "hard_step_timeout_s"),
                              ("screenshot_interval", "screenshot_interval_s"),
                              ("event_buffer_frames", "event_buffer_max_frames")):
        value = getattr(a, arg_name)
        if value is not None:
            protection[cfg_key] = value

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = pathlib.Path(a.out or ROOT / "reports/teacher-collect" / stamp)
    out.mkdir(parents=True, exist_ok=True)
    lock_name = ("dsh-experiment.lock" if a.instance == 0
                 else f"dsh-experiment-{a.instance}.lock")
    held = lockfile.acquire(lockfile.lock_dir() / lock_name)

    obs_buf, act_buf, label_buf, mask_buf, ep_buf, kind_buf = [], [], [], [], [], []
    episodes_meta = []
    label_counts = {name: 0 for name in LABELS}
    decisions = []
    _ver = _teacher_version(getattr(a, 'teacher_variant', '?'))
    env = None
    meta = {"config": vars(a), "started": stamp, "episodes": episodes_meta,
            "labels": label_counts, "dataset": str(out / "dataset.npz"),
            "data_status": "P3_DECISION_V1", "decision_schema": DS.SCHEMA,
            "teacher_version": _ver, "action_source": "scripted_teacher",
            "action_version": _ver, "game_version": "RA2YR+CNCNet5",
            "map": "UNKNOWN(capture)", "faction": "UNKNOWN(capture)", "seed": None,
            "simulated": False, "learning_eligible": True,
            "protection": {"config": dict(protection)}}
    stalled = None
    crash = None
    try:
        env = RA2FullEnv(out / "episodes", max_seconds=a.episode_seconds,
                         decision_seconds=a.decision_seconds, game_dir=a.game_dir,
                         port=a.port, instance=a.instance, kill_stale=a.kill_stale,
                         render_off=a.render_off, protection=protection)
        if getattr(env, "protection", None) is not None:
            meta["protection"]["active"] = True
            meta["protection"]["dir"] = str(env.protection.dir)
        try:
            for episode in range(a.episodes):
                if a.teacher_variant == "extreme_v2":
                    teacher = ScriptedTeacherV2(variant="extreme_v2")
                else:
                    teacher = ScriptedTeacher(variant=a.teacher_variant)
                v2_host = None
                obs, info = env.reset()
                if a.teacher_variant == "extreme_v2":
                    v2_host = V2HostAdapter(env)
                env.action_source = "scripted_teacher_v2" if a.teacher_variant == "extreme_v2" else "scripted_teacher"
                env.action_version = V2_POLICY_VERSION if a.teacher_variant == "extreme_v2" else _ver
                steps = 0
                cap = int(a.episode_seconds / a.decision_seconds) + 60
                done = False
                while not done and steps < cap:
                    mask = env.action_masks()
                    if v2_host is not None:
                        v2_host.refresh()
                        action, label, _v2_rationale = teacher.choose(v2_host)
                    else:
                        action, label = teacher.choose(env)
                        _v2_rationale = None
                    if not mask[action]:  # defensive: never send a masked action
                        action, label = 0, "noop"
                    label_idx = LABEL_TO_INDEX[label] if label in LABEL_TO_INDEX else (
                        LABEL_TO_INDEX["stop"] if label == "retreat" else LABEL_TO_INDEX["noop"])
                    obs_buf.append(np.asarray(obs, dtype=np.float32))
                    act_buf.append(int(action))
                    label_buf.append(label_idx)
                    mask_buf.append(np.asarray(mask, dtype=bool))
                    ep_buf.append(episode)
                    label_counts[label] += 1
                    obs, reward, terminated, truncated, info = env.step(int(action))
                    _rec = getattr(env, "last_decision", None)
                    if _rec is not None:
                        if a.teacher_variant == "extreme_v2":
                            _rec["action_source"] = "scripted_teacher_v2"
                            _rec["action_version"] = V2_POLICY_VERSION
                            if _v2_rationale is not None: _rec["rationale"] = _v2_rationale
                        decisions.append(_rec)
                    kind_buf.append("running")
                    steps += 1
                    done = terminated or truncated
                    if steps % 25 == 0:
                        st = episode_stats(env)
                        print(f"[collect] ep{episode} step{steps} t={st['t']}s money={st['money']} "
                              f"buildings={st['buildings']} units={st['units']}", flush=True)
                stats = episode_stats(env)
                last = dict(env.completed[-1]) if env.completed else {}
                entry = {"episode": episode, "steps": steps, "end_kind": last.get("kind", "?"),
                         "elapsed_s": last.get("elapsed_s"), "stats": stats,
                         "labels": dict(label_counts)}
                episodes_meta.append(entry)
                print(f"[collect] episode {episode} done steps={steps} "
                      f"kind={entry['end_kind']} buildings={stats['buildings']} "
                      f"units={stats['units']}", flush=True)
        except RunStalled as exc:
            # RUN02: the episode was already finalized as STALLED by the env protection
            # path. Record the truth and make sure this run cannot be used for training.
            evidence = getattr(exc, "evidence", {}) or {}
            stalled = {"error": str(exc), "evidence": evidence,
                       "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
            meta["data_status"] = "STALLED_EXCLUDED_FROM_TRAINING"
            meta["learning_eligible"] = False
            meta["stalled"] = stalled
            print(f"[collect] STALLED: classification="
                  f"{evidence.get('classification')} detected_by="
                  f"{evidence.get('detected_by')} detail={evidence.get('detail')}",
                  flush=True)
    except BaseException as exc:  # noqa: BLE001 - never lose the failure traceback
        # RUN02 regression: the first real run died without a traceback because the
        # controller path swallowed the exception chain. Persist the crash next to the
        # run output and re-raise (the finally block below still cleans up).
        import traceback as _tb
        crash = {"type": type(exc).__name__, "error": str(exc),
                 "traceback": _tb.format_exc(),
                 "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        meta["data_status"] = "CRASHED"
        meta["learning_eligible"] = False
        meta["crash"] = crash
        try:
            (out / "crash.json").write_text(
                json.dumps(crash, ensure_ascii=False, indent=2), encoding="utf-8")
        except BaseException:  # noqa: BLE001
            pass
        print(f"[collect] CRASH {crash['type']}: {crash['error']}", flush=True)
        raise
    finally:
        if env is not None:
            try:
                env.close()
            except BaseException:  # noqa: BLE001
                pass
        if obs_buf:
            # RUN02: a STALLED run is kept as diagnostics only -- never as a normal
            # training dataset (no silent promotion of an incomplete episode).
            suffix = ".stalled" if stalled is not None else ""
            decision_path = out / (f"decisions{suffix}.jsonl")
            dataset_path = out / (f"dataset{suffix}.npz")
            DS.write_decisions(decision_path, decisions)
            np.savez_compressed(
                dataset_path,
                obs=np.stack(obs_buf), actions=np.asarray(act_buf, dtype=np.int64),
                labels=np.asarray(label_buf, dtype=np.int64),
                masks=np.stack(mask_buf), episodes=np.asarray(ep_buf, dtype=np.int64))
            meta["samples"] = len(obs_buf)
            meta["obs_dim"] = int(obs_buf[0].shape[0])
            meta["dataset"] = str(dataset_path)
            print(f"[collect] saved {len(obs_buf)} samples -> {dataset_path}"
                  f" (learning_eligible={meta['learning_eligible']})", flush=True)
        # RUN02: meta.json is written even for failed/stalled runs (it documents status
        # and is required by the post-run acceptance checks).
        try:
            meta["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            meta["samples"] = len(obs_buf)
            if "obs_dim" not in meta and obs_buf:
                meta["obs_dim"] = int(obs_buf[0].shape[0])
            (out / "meta.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        except BaseException:  # noqa: BLE001
            pass
        lockfile.release(held)


if __name__ == "__main__":
    main()



