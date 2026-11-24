"""M0-A 固定评测执行器（2026-11-01 新建）。

用法（在 E:\\RA2MCP 下，使用带 sb3 的 Python）：
  E:\\RA2MCP\\.venv-rl\\Scripts\\python.exe scripts\\eval_m0a\\run_eval.py ^
      --model reports/rl/stage-A-round1-20261030-153225/model.zip ^
      --games 10 --workers 6 --out reports/m0a-2026-11-01/eval-R1

默认模式 full：RA2FullEnv（模型控制经济/建造/生产/单位，无 RulePolicy 代做战略）。
--mode combat 仅用于“脚本经济参考口径”（RA2CombatEnv），报告中必须标注脚本托管。

本脚本不训练、不改奖励、不改模型；冻结校验（SHA + optimizer updates）失败即中止。
"""
import argparse
import json
import pathlib
import statistics
import sys
import time
import uuid
from functools import partial

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "dsh_train"))
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
from sb3_contrib import MaskablePPO  # noqa: E402
from stable_baselines3.common.vec_env import SubprocVecEnv  # noqa: E402

from dsh_episode import lockfile  # noqa: E402
from dsh_rl.full_env import RA2FullEnv  # noqa: E402
from eval_m0a import analyze as analyze_mod  # noqa: E402
from eval_m0a import protocol  # noqa: E402


def _parse_slots_env(workers):
    slots = protocol.resolve_game_slots(workers)
    if len(slots) < workers:
        raise SystemExit(
            f"only {len(slots)} compliant game slots found, need {workers}: {slots}")
    return slots


def make_env(index, out, seconds, decision_seconds, render_off, mode, slot):
    folder = pathlib.Path(out) / "workers" / str(index)
    env_cls = RA2FullEnv
    if mode == "combat":
        from dsh_rl.combat_env import RA2CombatEnv
        env_cls = RA2CombatEnv
    return env_cls(folder / "episodes", seconds, decision_seconds,
                   game_dir=slot["game_dir"], port=int(slot["port"]),
                   instance=index, kill_stale=False, render_off=render_off,
                   launch_delay_seconds=index * 2.0)


def wilson_ci(successes, n, z=1.96):
    if n <= 0:
        return [0.0, 1.0]
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / denom
    return [round(max(0.0, center - half), 4), round(min(1.0, center + half), 4)]


def collect_worker_results(out, workers, seen):
    """每轮结束后，每个 worker 取“最新且未收集”的一个结果（避免 auto-reset 的额外对局混入）。"""
    fresh = []
    for index in range(workers):
        eps = sorted((pathlib.Path(out) / "workers" / str(index) / "episodes").glob("*.result.json"),
                     key=lambda p: p.stat().st_mtime)
        for path in reversed(eps):
            if str(path) in seen:
                continue
            try:
                result = json.loads(path.read_text(encoding="utf-8-sig"))
            except Exception:  # noqa: BLE001
                continue
            jsonl = path.with_name(path.name.replace(".result.json", ".jsonl"))
            fresh.append((path, jsonl, result))
            break
    return fresh


def collect_results(out, workers, already):
    """兼容旧接口（保留）：返回全部未收集结果。"""
    fresh = []
    for path in sorted((pathlib.Path(out) / "workers").glob("*/episodes/*.result.json"),
                       key=lambda p: p.stat().st_mtime):
        if str(path) in already:
            continue
        try:
            result = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:  # noqa: BLE001
            continue
        jsonl = path.with_name(path.name.replace(".result.json", ".jsonl"))
        fresh.append((path, jsonl, result))
    return fresh


def aggregate(episodes, game_slots):
    n = len(episodes)
    kinds = {}
    for ep in episodes:
        kinds[ep["outcome"]] = kinds.get(ep["outcome"], 0) + 1
    wins = kinds.get("win", 0)

    def mean(key):
        vals = [ep.get(key) for ep in episodes if ep.get(key) is not None]
        return round(statistics.mean(vals), 3) if vals else None

    def rate(flag):
        vals = [1 for ep in episodes if ep.get("capabilities", {}).get(flag)]
        return round(sum(vals) / n, 4) if n else None

    def any_rate(pred):
        vals = [1 for ep in episodes if pred(ep)]
        return round(sum(vals) / n, 4) if n else None

    caps = {
        "script_free": rate("is_script_free_full_env"),
        "deploy_attempted": rate("deploy_attempted"),
        "can_deploy_mcv": rate("can_deploy_mcv"),
        "can_build_any": rate("can_build_any"),
        "can_build_power": rate("can_build_power"),
        "can_build_refinery": rate("can_build_refinery"),
        "can_build_barracks": rate("can_build_barracks"),
        "can_build_factory": rate("can_build_factory"),
        "can_mine": rate("can_mine"),
        "can_produce_units": rate("can_produce_units"),
        "issues_attack": rate("issues_attack"),
        "effective_attack": rate("effective_attack"),
        "organizes_groups": rate("organizes_groups"),
        "multi_unit_attack": any_rate(lambda ep: (ep.get("multi_unit_attack_orders") or 0) > 0),
        "subset_group_attack": any_rate(lambda ep: (ep.get("subset_group_attack_orders") or 0) > 0),
        "subset_selection": any_rate(lambda ep: (ep.get("subset_selection_actions") or 0) >= 3),
        "completes_full_match": rate("completes_full_match"),
        "wins": rate("wins"),
    }
    totals = {
        "attacks_issued": sum(ep.get("attacks_issued_env") or 0 for ep in episodes),
        "attacks_effective": sum(ep.get("attacks_effective_env") or 0 for ep in episodes),
        "attack_orders_action_rows": sum(ep.get("attack_orders") or 0 for ep in episodes),
        "multi_unit_attack_orders": sum(ep.get("multi_unit_attack_orders") or 0 for ep in episodes),
        "subset_group_attack_orders": sum(ep.get("subset_group_attack_orders") or 0 for ep in episodes),
        "all_unit_attack_orders": sum(ep.get("all_unit_attack_orders") or 0 for ep in episodes),
        "deploy_orders": sum(ep.get("deploy_orders") or 0 for ep in episodes),
        "produce_orders": sum(ep.get("produce_orders") or 0 for ep in episodes),
        "place_orders": sum(ep.get("place_orders") or 0 for ep in episodes),
        "move_orders": sum(ep.get("move_orders") or 0 for ep in episodes),
        "model_actions": sum(ep.get("model_actions") or 0 for ep in episodes),
        "non_model_actions": sum(ep.get("non_model_actions") or 0 for ep in episodes),
        "damage_dealt": sum(ep.get("damage_dealt") or 0 for ep in episodes),
        "own_hp_lost": sum(ep.get("own_hp_lost") or 0 for ep in episodes),
        "truth_kills_infantry": sum(ep.get("truth_kills_infantry") or 0 for ep in episodes),
        "truth_kills_vehicle": sum(ep.get("truth_kills_vehicle") or 0 for ep in episodes),
        "truth_building_destroyed": sum(ep.get("truth_building_destroyed") or 0 for ep in episodes),
    }
    return {
        "games": n,
        "kinds": kinds,
        "win_rate": round(wins / n, 4) if n else None,
        "win_rate_wilson95": wilson_ci(wins, n),
        "defeat_rate": round(kinds.get("defeat", 0) / n, 4) if n else None,
        "timeout_rate": round(kinds.get("timeout", 0) / n, 4) if n else None,
        "crash_rate": round(kinds.get("crash", 0) / n, 4) if n else None,
        "mean_elapsed_s": mean("elapsed_s"),
        "mean_building_peak": mean("building_peak"),
        "mean_unit_peak": mean("unit_peak"),
        "mean_attack_orders": mean("attack_orders"),
        "mean_unique_attack_targets": mean("unique_attack_targets"),
        "mean_group_size": mean("mean_group_size"),
        "capabilities": caps,
        "totals": totals,
        "effective_attack_rate_env": (round(totals["attacks_effective"] / totals["attacks_issued"], 4)
                                      if totals["attacks_issued"] else None),
        "damage_exchange": (round(totals["damage_dealt"] / max(1, totals["own_hp_lost"]), 4)
                            if totals["own_hp_lost"] else None),
        "game_slots": game_slots,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", required=True)
    ap.add_argument("--games", type=int, default=10)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--episode-seconds", type=float, default=protocol.EPISODE_SECONDS)
    ap.add_argument("--decision-seconds", type=float, default=protocol.DECISION_SECONDS)
    ap.add_argument("--mode", choices=["full", "combat"], default="full")
    ap.add_argument("--render-off", action="store_true")
    ap.add_argument("--stochastic", action="store_true",
                    help="默认确定性 argmax；此开关仅用于对照实验")
    ap.add_argument("--max-minutes", type=float, default=90.0)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    model_path = (ROOT / a.model).resolve() if not pathlib.Path(a.model).is_absolute() else pathlib.Path(a.model)
    workers = max(1, min(a.workers, a.games))
    slots = _parse_slots_env(workers)
    out = pathlib.Path(a.out)
    if not out.is_absolute():
        out = (ROOT / out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    manifest = protocol.protocol_manifest()
    model_sha_before = protocol.sha256_file(model_path)
    if not manifest["config_compliant"]:
        raise SystemExit("EVAL_CONFIG_DRIFT: spawn.ini/spawnmap hash != protocol constants")
    meta = {
        "experiment": protocol.EXPERIMENT_TAG,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "model": str(model_path),
        "model_sha256": model_sha_before,
        "mode": a.mode,
        "deterministic": not a.stochastic,
        "games": a.games,
        "workers": workers,
        "episode_seconds": a.episode_seconds,
        "decision_seconds": a.decision_seconds,
        "render_off": bool(a.render_off),
        "game_slots": slots,
        "protocol": manifest,
        "env_class": "RA2FullEnv" if a.mode == "full" else "RA2CombatEnv",
        "script_policy": "none (full env _economy() is a no-op)" if a.mode == "full"
                         else "RulePolicy scripted economy (reference only)",
        "host": {"python": sys.executable, "pid": 0},
    }
    (out / "run_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                       encoding="utf-8")

    held = lockfile.acquire(ROOT / "build/dsh-experiment.lock")
    env = None
    started = time.monotonic()
    try:
        model = MaskablePPO.load(str(model_path), device="cpu")
        updates_before = int(getattr(model, "_n_updates", 0))
        env = SubprocVecEnv(
            [partial(make_env, i, str(out), a.episode_seconds, a.decision_seconds,
                     bool(a.render_off), a.mode, slots[i]) for i in range(workers)],
            start_method="spawn")
        obs = env.reset()
        steps = 0
        rounds = (a.games + workers - 1) // workers
        collected = []
        seen = set()
        for rnd in range(rounds):
            pending = set(range(workers))
            max_steps = int(a.episode_seconds / a.decision_seconds) * 3 + 1600
            while pending and steps < max_steps:
                if time.monotonic() - started > a.max_minutes * 60:
                    print("[eval] MAX_RUNTIME_REACHED", flush=True)
                    pending.clear()
                    break
                masks = env.env_method("action_masks")
                actions = []
                for i in range(workers):
                    if i in pending:
                        action, _ = model.predict(obs[i], deterministic=not a.stochastic,
                                                  action_masks=masks[i])
                    else:
                        action = 0
                    actions.append(int(action))
                obs, rewards, dones, infos = env.step(actions)
                steps += 1
                for i in list(pending):
                    if dones[i]:
                        pending.discard(i)
                        print(f"[eval] round {rnd + 1}: worker {i} finished at step {steps}",
                              flush=True)
            fresh = collect_worker_results(out, workers, seen)
            for path, jsonl, result in fresh:
                seen.add(str(path))
                collected.append((path, jsonl, result))
                if len(collected) >= a.games:
                    break
            print(f"[eval] round {rnd + 1}: collected={len(collected)}/{a.games}",
                  flush=True)
            if len(collected) >= a.games:
                break

        updates_after = int(getattr(model, "_n_updates", 0))
        frozen_ok = updates_after == updates_before
        model_sha_after = protocol.sha256_file(model_path)
        file_ok = model_sha_after == model_sha_before
        episodes = []
        for path, jsonl, result in collected[:a.games]:
            try:
                episodes.append(analyze_mod.analyze_episode(jsonl, result))
            except Exception as exc:  # noqa: BLE001
                episodes.append({"episode_id": result.get("episode_id"),
                                 "jsonl": str(jsonl), "analysis_error": repr(exc),
                                 "outcome": result.get("kind")})
        evaluation = {
            "experiment": protocol.EXPERIMENT_TAG,
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "model": str(model_path),
            "model_sha256": model_sha_before,
            "model_sha256_after": model_sha_after,
            "model_file_unchanged": file_ok,
            "optimizer_updates_before": updates_before,
            "optimizer_updates_after": updates_after,
            "frozen_verified": frozen_ok,
            "training": False,
            "mode": a.mode,
            "deterministic": not a.stochastic,
            "protocol": manifest,
            "steps": steps,
            "wall_seconds": round(time.monotonic() - started, 1),
            "out": str(out),
            "summary": aggregate(episodes, slots),
            "episodes": episodes,
        }
        (out / "evaluation.json").write_text(json.dumps(evaluation, ensure_ascii=False, indent=2),
                                            encoding="utf-8")
        print(json.dumps({k: evaluation[k] for k in ("model_sha256", "frozen_verified",
                                                     "model_file_unchanged", "training")},
                         indent=2), flush=True)
        print(json.dumps(evaluation["summary"], ensure_ascii=False, indent=2), flush=True)
        if not (frozen_ok and file_ok):
            raise SystemExit("EVAL_FROZEN_ASSERTION_FAILED")
    finally:
        try:
            if env is not None:
                env.close()
        finally:
            try:
                from dsh_episode.runner import kill_games
                kill_games()
            except Exception:  # noqa: BLE001
                pass
            lockfile.release(held)


if __name__ == "__main__":
    main()

