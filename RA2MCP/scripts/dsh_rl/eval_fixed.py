"""Frozen-policy fixed evaluation: N real games, no optimizer updates.

Every worker plays exactly one episode with the frozen policy. Results are read
from the recorded episode files, so a crash or defeat is never hidden.

Usage (same environment as play.py):
  python scripts/dsh_rl/eval_fixed.py --model <run>/model.zip \
      --games 6 --workers 3 --episode-seconds 300 --render-off
"""
import argparse
import hashlib
import json
import os
import pathlib
import statistics
import sys
import time
import uuid
from functools import partial

import numpy as np

BASE_PORT = int(os.environ.get("RA2YR_BASE_PORT", "14621"))
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from dsh_episode import lockfile  # noqa: E402
from sb3_contrib import MaskablePPO  # noqa: E402
from stable_baselines3.common.vec_env import SubprocVecEnv  # noqa: E402
from dsh_rl.full_env import RA2FullEnv  # noqa: E402

if os.environ.get("DSH_COMBAT_ENV") == "1":
    # 与训练一致的 combat env（脚本经济 + 军事掩码）
    from dsh_rl.combat_env import RA2CombatEnv as RA2FullEnv  # noqa: F811
from dsh_learning import fixed_eval_protocol  # noqa: E402


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def make_env(index, out, seconds, render_off):
    folder = pathlib.Path(out) / "workers" / str(index)
    return RA2FullEnv(folder / "episodes", seconds, 0.5,
                      game_dir=ROOT / ("test-game" if index == 0 else f"test-game-{index}"),
                      port=BASE_PORT + index, instance=index,
                      kill_stale=False, render_off=render_off)


def latest_results(out, workers):
    results = {}
    for index in range(workers):
        eps = sorted((pathlib.Path(out) / "workers" / str(index) / "episodes").glob("*.result.json"),
                     key=lambda p: p.stat().st_mtime)
        if eps:
            results[index] = json.loads(eps[-1].read_text(encoding="utf-8"))
    return results


def summarize(results):
    kinds = {}
    for r in results.values():
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    elapsed = [float(r.get("elapsed_s") or 0) for r in results.values()]
    dmg = sum(float((r.get("metrics") or {}).get("damage_dealt") or 0) for r in results.values())
    hp = sum(float((r.get("metrics") or {}).get("own_hp_lost") or 0) for r in results.values())
    wins = kinds.get("win", 0)
    return {"games": len(results), "kinds": kinds, "win_rate": wins / max(1, len(results)),
            "defeat_rate": kinds.get("defeat", 0) / max(1, len(results)),
            "crash_rate": kinds.get("crash", 0) / max(1, len(results)),
            "mean_elapsed_s": round(statistics.mean(elapsed), 1) if elapsed else 0,
            "damage_dealt": round(dmg, 0), "own_hp_lost": round(hp, 0),
            "damage_exchange": round(dmg / max(1.0, hp), 2)}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", required=True)
    ap.add_argument("--games", type=int, default=6)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--episode-seconds", type=float, default=300)
    ap.add_argument("--render-off", action="store_true")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    workers = min(a.workers, a.games)
    out = pathlib.Path(a.out or (ROOT / "reports/rl" /
                                 f"eval-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"))
    out.mkdir(parents=True, exist_ok=True)
    held = lockfile.acquire(ROOT / "build/dsh-experiment.lock")
    env = None
    model = None
    updates_before = None
    model_sha256 = _sha256_file(a.model)
    try:
        model = MaskablePPO.load(a.model, device="cpu")
        updates_before = int(getattr(model, "_n_updates", 0))
        env = SubprocVecEnv([partial(make_env, i, str(out), a.episode_seconds, a.render_off)
                             for i in range(workers)], start_method="spawn")
        obs = env.reset()
        steps = 0
        rounds = (a.games + workers - 1) // workers
        collected = []
        max_steps = int(a.episode_seconds / 0.5) * 3 + 1600
        for rnd in range(rounds):
            pending = set(range(workers))
            while pending and steps < max_steps:
                masks = env.env_method("action_masks")
                actions = []
                for i in range(workers):
                    if i in pending:
                        action, _ = model.predict(obs[i], deterministic=True,
                                                  action_masks=masks[i])
                    else:
                        action = 0
                    actions.append(int(action))
                obs, rewards, dones, infos = env.step(actions)
                steps += 1
                for i in list(pending):
                    if dones[i]:
                        pending.discard(i)
                        print(f"round {rnd + 1}: worker {i} finished after {steps} steps",
                              flush=True)
            files = sorted((pathlib.Path(out) / "workers").glob("*/episodes/*.result.json"),
                           key=lambda p: p.stat().st_mtime)
            fresh = [p for p in files if p not in collected]
            collected.extend(fresh[:max(0, a.games - len(collected))])
            if len(collected) >= a.games:
                break
        results = {i: json.loads(p.read_text(encoding="utf-8"))
                   for i, p in enumerate(collected)}
        updates_after = int(getattr(model, "_n_updates", 0))
        frozen_verified = updates_after == updates_before
        if not frozen_verified:
            raise RuntimeError("POLICY_CHANGED_DURING_EVAL: optimizer updates %s -> %s"
                               % (updates_before, updates_after))
        if _sha256_file(a.model) != model_sha256:
            raise RuntimeError("MODEL_FILE_CHANGED_DURING_EVAL")
        summary = {"model": a.model,
                   "model_sha256": model_sha256,
                   "optimizer_updates_before": updates_before,
                   "optimizer_updates_after": updates_after,
                   "frozen_assertion": "optimizer_updates == 0",
                   "frozen_verified": frozen_verified,
                   "protocol_version": fixed_eval_protocol.PROTOCOL_VERSION,
                   "protocol_hash": fixed_eval_protocol.PROTOCOL_HASH,
                   "protocol_compliant": float(a.episode_seconds) == 300.0,
                   "eval_protocol": dict(fixed_eval_protocol.EVAL_CONFIG,
                                         reward_version=RA2FullEnv.reward_version),
                   "out": str(out), "steps": steps,
                   "episode_seconds": a.episode_seconds,
                   "render_off": bool(a.render_off),
                   "training": False,
                   "summary": summarize(results),
                   "episodes": results}
        (out / "evaluation.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(json.dumps({k: summary[k] for k in
                          ("model_sha256", "frozen_verified", "protocol_hash",
                           "protocol_compliant", "training")}, indent=2))
        print(json.dumps(summary["summary"], indent=2))
    finally:
        try:
            if env:
                env.close()
        finally:
            lockfile.release(held)


if __name__ == "__main__":
    main()

