"""Local real-game PPO training. Use --resume PATH to continue saved weights/optimizer.

  .venv-rl\Scripts\python.exe scripts\dsh_rl\train.py --steps 64
No remote model calls and no simulated-data fallback.
"""
import argparse
import hashlib
import json
import pathlib
import sys
import time
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from dsh_episode import lockfile
from dsh_rl.env import RA2MicroEnv
from dsh_rl.full_env import RA2FullEnv
from dsh_rl import full_spec
from dsh_rl.reward import ACTION_NAMES, REWARD_VERSION
from stable_baselines3 import PPO
from sb3_contrib import MaskablePPO
from stable_baselines3.common.callbacks import BaseCallback
from stable_baselines3.common.monitor import Monitor
import numpy as np
import torch


def weights(model):
    return np.concatenate([p.detach().cpu().numpy().ravel() for p in model.policy.parameters()])


def save_report(path, data):
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temporary.replace(path)


def save_model(model, path):
    temporary = path.with_name(path.stem + ".next.zip")
    model.save(temporary)
    temporary.replace(path)


class EvidenceCallback(BaseCallback):
    def __init__(self, out):
        super().__init__()
        self.out = out
        self.requested_stop = False

    def _on_step(self):
        if (self.out / "STOP").exists():
            self.requested_stop = True
            save_model(self.model, self.out / "checkpoint.zip")
            return False
        if self.n_calls % 16 == 0:
            print(f"PPO real transitions={self.num_timesteps} reward={self.training_env.envs[0].unwrapped.total_reward:.3f}", flush=True)
        if self.n_calls % 32 == 0:
            save_model(self.model, self.out / "checkpoint.zip")
        if self.n_calls % 16 == 0:
            raw = self.training_env.envs[0].unwrapped
            save_report(self.out / "progress.json", {"timesteps": self.num_timesteps,
                "optimizer_epochs": self.model._n_updates,
                "total_reward": raw.total_reward,
                "episode_reward_parts": getattr(raw, 'episode_reward_parts', {}),
                "reward_revision": getattr(getattr(raw, 'reward_ledger', None), 'revision', None),
                "episode_id": getattr(getattr(raw, 'writer', None), 'episode_id', None),
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        return True

    def _on_rollout_start(self):
        # This event follows the preceding rollout's optimizer update.
        save_model(self.model, self.out / "checkpoint.zip")


def learn_chunks(model, steps, callback, out, continuous=False):
    while True:
        # SB3 retains _last_obs and the live environment across learning chunks.
        model.learn(total_timesteps=steps, callback=callback, reset_num_timesteps=False)
        save_model(model, out / "checkpoint.zip")
        if not continuous or callback.requested_stop or (out / "STOP").exists():
            callback.requested_stop = callback.requested_stop or (out / "STOP").exists()
            return


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--steps", type=int, default=128)
    ap.add_argument("--control", choices=("full", "micro"), default="full",
                    help="full: model chooses economy and units; micro: legacy fixed-economy environment")
    ap.add_argument("--episode-seconds", type=float, default=180, help="0: play to actual game end")
    ap.add_argument("--continuous", action="store_true", help="repeat learning chunks until STOP; do not reset ongoing games")
    ap.add_argument("--decision-seconds", type=float, default=2)
    ap.add_argument("--rollout", type=int, default=32)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--resume", default=None)
    ap.add_argument("--allow-reward-change", action="store_true",
                    help="explicitly allow a new reward version when resuming; scores are not comparable")
    ap.add_argument("--out", default=None)
    ap.add_argument("--instance", type=int, default=0,
                    help="parallel training instance index; 0 is the primary instance")
    ap.add_argument("--game-dir", default=None,
                    help="isolated game copy for this instance (default: test-game)")
    ap.add_argument("--port", type=int, default=14521,
                    help="bridge port of that game copy (must match its ra2yrcpp.json)")
    ap.add_argument("--no-kill-stale-games", dest="kill_stale", action="store_false",
                    default=None,
                    help="never kill other game processes (parallel instances)")
    ap.add_argument("--render-off", action="store_true",
                    help="EXPERIMENTAL opt-in: hide the game window and reversibly "
                         "skip GScreenClass::Render for this worker (validated test "
                         "copies only; default remains normal rendering)")
    a = ap.parse_args()
    if a.steps < a.rollout or a.rollout < 4 or a.rollout % 4:
        ap.error("steps must be >= rollout; rollout must be >=4 and divisible by 4")
    out = pathlib.Path(a.out) if a.out else ROOT / "reports/rl" / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6])
    if (out / "model.zip").exists():
        ap.error("output already contains a model; use a new output directory with --resume")
    out.mkdir(parents=True, exist_ok=True)
    lock_name = ("dsh-experiment.lock" if a.instance == 0
                 else f"dsh-experiment-{a.instance}.lock")
    held = lockfile.acquire(ROOT / "build" / lock_name)
    env = None
    environment_class = RA2FullEnv if a.control == "full" else RA2MicroEnv
    algorithm = MaskablePPO if a.control == "full" else PPO
    reward_version = environment_class.reward_version
    report = {"status": "starting", "parameters": vars(a), "reward_version": reward_version,
              "instance": a.instance, "game_dir": a.game_dir, "bridge_port": a.port,
              "control_mode": a.control, "algorithm": algorithm.__name__,
              "action_names": ACTION_NAMES if a.control == "micro" else "action-contract.json",
              "action_count": full_spec.ACTION_N if a.control == "full" else 4,
              "torch": torch.__version__, "device": "cpu",
              "resume": a.resume, "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "source": "real isolated RA2 game only", "fixed_economy": a.control == "micro",
              "source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in pathlib.Path(__file__).parent.glob("*.py")}}
    save_report(out / "run.json", report)
    try:
        raw = environment_class(out / "episodes", a.episode_seconds, a.decision_seconds,
                                game_dir=a.game_dir, port=a.port, instance=a.instance,
                                kill_stale=a.kill_stale, render_off=a.render_off)
        report["render_off"] = bool(a.render_off)
        report["render_off_evidence"] = getattr(raw, "render_off_evidence", None)
        save_report(out / "run.json", report)
        env = Monitor(raw, str(out / "monitor.csv"))
        if a.resume:
            old_report = pathlib.Path(a.resume).parent / "run.json"
            previous_reward = json.loads(old_report.read_text(encoding="utf-8")).get("reward_version") if old_report.exists() else None
            previous_mode = json.loads(old_report.read_text(encoding="utf-8")).get("control_mode", "micro") if old_report.exists() else "unknown"
            if previous_mode != a.control:
                raise ValueError("checkpoint control mode incompatible; full-control models must start fresh or resume a full checkpoint")
            report["previous_reward_version"] = previous_reward
            if previous_reward != reward_version and not a.allow_reward_change:
                raise ValueError("reward version differs; use --allow-reward-change for a documented new experiment")
            if a.control == "full":
                old_contract = pathlib.Path(a.resume).parent / "action-contract.json"
                raw.expected_catalog_hash = json.loads(old_contract.read_text(encoding="utf-8"))["catalog_sha256"]
            model = algorithm.load(a.resume, env=env, device="cpu")
            if model.n_steps != a.rollout:
                raise ValueError("resume rollout must match the saved model")
        else:
            model = algorithm("MlpPolicy", env, n_steps=a.rollout, batch_size=min(16, a.rollout),
                        n_epochs=4, gamma=0.99, learning_rate=3e-4, ent_coef=0.03,
                        seed=a.seed, device="cpu", policy_kwargs={"net_arch": [64, 64]}, verbose=1)
        before = weights(model).copy()
        steps_before, updates_before = model.num_timesteps, model._n_updates
        report.update(timesteps_before=steps_before, training_epochs_before=updates_before)
        optimizer_before = max((float(s.get("step", 0)) for s in model.policy.optimizer.state.values()), default=0)
        report["status"] = "collecting_real_rollouts"
        save_report(out / "run.json", report)
        callback = EvidenceCallback(out)
        learn_chunks(model, a.steps, callback, out, a.continuous)
        save_model(model, out / "model.zip")
        after = weights(model)
        loaded = algorithm.load(out / "model.zip", device="cpu")
        round_trip = bool(np.array_equal(after, weights(loaded)))
        delta = float(np.linalg.norm(after - before))
        report.update({"status": "stopped_by_request" if callback.requested_stop else "complete", "timesteps_before": steps_before,
                       "timesteps_after": model.num_timesteps, "real_transitions_this_run": raw.transitions,
                       "optimizer_updates_this_run": model._n_updates - updates_before,
                       "optimizer_epochs_this_run": model._n_updates - updates_before,
                       "ppo_rollout_updates_this_run": (model.num_timesteps - steps_before) // model.n_steps,
                       "gradient_steps_this_run": int(max((float(s.get("step", 0)) for s in model.policy.optimizer.state.values()), default=0) - optimizer_before),
                       "optimizer_update_metric": "SB3 _n_updates counts training epochs; rollout and gradient steps are separate",
                       "parameter_l2_change": delta, "parameters_updated": delta > 0,
                       "save_reload_weights_identical": round_trip,
                       "optimizer_state_reloaded": bool(loaded.policy.optimizer.state),
                       "model_sha256": hashlib.sha256((out / "model.zip").read_bytes()).hexdigest(),
                       "total_reward": raw.total_reward,
                       "note": "Small PPO pipeline experiment; reward/weight changes do not establish better gameplay."})
        save_report(out / "progress.json", {"timesteps": model.num_timesteps,
                    "optimizer_epochs": model._n_updates, "total_reward": raw.total_reward,
                    "status": report["status"], "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    except BaseException as error:
        report.update({"status": "failed", "error": f"{type(error).__name__}: {error}"})
        raise
    finally:
        try:
            if env:
                env.close()
                report["episodes"] = env.unwrapped.completed
        finally:
            lockfile.release(held)
            report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            save_report(out / "run.json", report)
            print(json.dumps({k: report.get(k) for k in
                             ("status", "real_transitions_this_run", "optimizer_updates_this_run",
                              "parameter_l2_change", "save_reload_weights_identical", "error")}, indent=2), flush=True)
            print(f"local PPO artifacts: {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
