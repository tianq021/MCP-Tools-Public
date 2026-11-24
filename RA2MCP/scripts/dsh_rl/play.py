"""Run a saved local PPO policy with frozen weights; no optimizer updates."""
import argparse
import hashlib
import json
import os
import pathlib
import sys
import time
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from dsh_episode import lockfile
from dsh_rl.env import RA2MicroEnv
from dsh_rl.full_env import RA2FullEnv
from stable_baselines3 import PPO
from sb3_contrib import MaskablePPO

if os.environ.get("DSH_COMBAT_ENV") == "1":
    # Evaluate with the same combat-stage environment the checkpoint was trained
    # in (scripted economy, military-only action mask).
    from dsh_rl.combat_env import RA2CombatEnv as RA2FullEnv


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", required=True)
    ap.add_argument("--control", choices=("full", "micro"), default="full")
    ap.add_argument("--episode-seconds", type=float, default=180)
    ap.add_argument("--steps", type=int, default=128,
                    help="max decisions per episode (0.5s each)")
    ap.add_argument("--episodes", type=int, default=1,
                    help="number of games to play; 0 = keep playing until Ctrl+C")
    ap.add_argument("--deterministic", action="store_true",
                    help="always take the argmax action. Off by default: sampling "
                         "matches the behaviour the policy was trained with.")
    ap.add_argument("--instance", type=int, default=0)
    ap.add_argument("--game-dir", default=None)
    ap.add_argument("--port", type=int, default=14521)
    ap.add_argument("--no-kill-stale-games", dest="kill_stale", action="store_false",
                    default=None)
    ap.add_argument("--render-off", action="store_true",
                    help="EXPERIMENTAL opt-in draw-skip mode (validated test copies "
                         "only; default is normal rendering)")
    a = ap.parse_args()
    out = ROOT / "reports/rl" / ("eval-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6])
    lock_name = ("dsh-experiment.lock" if a.instance == 0
                 else f"dsh-experiment-{a.instance}.lock")
    held = lockfile.acquire(ROOT / "build" / lock_name)
    env = None
    try:
        algorithm = MaskablePPO if a.control == "full" else PPO
        model = algorithm.load(a.model, device="cpu")
        updates_before = model._n_updates
        original_hash = hashlib.sha256(pathlib.Path(a.model).read_bytes()).hexdigest()
        env_class = RA2FullEnv if a.control == "full" else RA2MicroEnv
        env = env_class(out / "episodes", max_seconds=a.episode_seconds,
                        game_dir=a.game_dir, port=a.port, instance=a.instance,
                        kill_stale=a.kill_stale, render_off=a.render_off)
        if a.control == "full":
            contract = json.loads((pathlib.Path(a.model).parent / "action-contract.json").read_text(encoding="utf-8"))
            env.expected_catalog_hash = contract["catalog_sha256"]
        x, info = env.reset()
        episodes_played = 0
        while a.episodes == 0 or episodes_played < a.episodes:
            if episodes_played:
                x, info = env.reset()
            for _ in range(a.steps):
                mask = {"action_masks": env.action_masks()} if a.control == "full" else {}
                action, _ = model.predict(x, deterministic=a.deterministic, **mask)
                x, reward, terminated, truncated, info = env.step(int(action))
                if terminated or truncated:
                    break
            episodes_played += 1
            last = env.completed[-1] if env.completed else {}
            print(f"[demo] game {episodes_played}: kind={last.get('kind')} "
                  f"elapsed={last.get('elapsed_s')}s transitions={env.transitions}", flush=True)
            if a.episodes == 0:
                continue
    finally:
        try:
            if env:
                env.close()
                result = {"model": a.model, "model_sha256": original_hash,
                          "optimizer_updates": model._n_updates - updates_before,
                          "model_file_unchanged": original_hash == hashlib.sha256(pathlib.Path(a.model).read_bytes()).hexdigest(),
                          "real_transitions": env.transitions, "episodes": env.completed,
                          "total_reward": env.total_reward, "training": False}
                (out / "evaluation.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
                print(json.dumps(result, indent=2))
        finally:
            lockfile.release(held)


if __name__ == "__main__":
    main()
