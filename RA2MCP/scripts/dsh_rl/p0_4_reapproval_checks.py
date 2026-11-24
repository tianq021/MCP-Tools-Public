"""P0-4 Phase 1 重新申请前的专项检查（离线；不启动真实游戏、不训练、不产令牌）。

覆盖：
1. Monitor 包装后 game_pid 正确获取；
2. has_attr("action_masks") 安全；
3. action_masks 通过正确接口获取（get_action_masks/env_method）；
4. 首次 reset 后 Worker 保持存活；
5. MaskablePPO 能进入 collect_rollouts；
6. 512 是全局 timesteps（n_steps=32 × n_envs=2=64/rollout）；
7. 8 个 rollout 完整执行 → 3520 + 512 = 4032。

说明：collect_rollouts dry-run 只做 forward/rollout 收集，**不调用 model.learn / 不做梯度更新 / 不保存模型**；
起始 num_timesteps=3520 是显式设置的计数器语义验证（不是已训练检查点）。
"""
import json
import pathlib
import subprocess
import sys
import tempfile
import time
from functools import partial

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import numpy as np  # noqa: E402
from stable_baselines3.common.callbacks import BaseCallback  # noqa: E402
from sb3_contrib import MaskablePPO  # noqa: E402
from sb3_contrib.common.maskable.utils import (  # noqa: E402
    get_action_masks,
    is_masking_supported,
)

from dsh_rl.recovering_vec import (  # noqa: E402
    CallableAttributeRef,
    RecoveringSubprocVecEnv,
)
from dsh_rl.recovery_test_envs import make_dummy_env  # noqa: E402


class DryRunCallback(BaseCallback):
    def _on_step(self):
        return True


class Checks:
    def __init__(self):
        self.rows = []

    def check(self, name, ok, detail=None):
        ok = bool(ok)
        self.rows.append({"name": name, "ok": ok, "detail": detail})
        print(("[PASS] " if ok else "[FAIL] ") + name
              + ((" | " + json.dumps(detail, ensure_ascii=False))
                 if detail is not None else ""), flush=True)
        return ok


def no_games():
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq gamemd-spawn-ra2yrcpp.exe"],
            capture_output=True, text=True, timeout=15)
        return sum(1 for line in result.stdout.splitlines()
                   if "gamemd-spawn-ra2yrcpp" in line)
    except BaseException:  # noqa: BLE001
        return -1


def main():
    checks = Checks()
    out = ROOT / "reports/autonomous_learning"
    result = {"started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "checks": checks.rows, "ppo_training": False,
              "gradient_updates": False, "real_games": 0}
    vec = None
    try:
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="p0_4_reapproval_",
                                            dir=str(ROOT / "reports/rl")))
        try:
            tmp = pathlib.Path(tmp)
            env_fns = []
            for index in range(2):
                spec = {"_attempt_counter_file": str(tmp / ("attempt-%d.txt" % index))}
                monitor = str(tmp / ("monitor-%d.csv" % index))
                env_fns.append(partial(make_dummy_env, index, spec, monitor))
            vec = RecoveringSubprocVecEnv(
                env_fns, start_method="spawn", max_launches=2,
                launch_jitter=(1.0, 3.0), recv_timeout_s=60.0,
                startup_timeout_s=60.0, max_restarts_per_worker=5,
                stats_dir=tmp / "stats", seed=11, verbose=0)
            observations = vec.reset()
            checks.check("首次 reset 返回 2 个观测",
                         observations.shape == (2, 2),
                         {"shape": list(observations.shape)})
            worker_pids = [process.pid for process in vec.processes]
            checks.check("首次 reset 后 2 个 Worker 均存活",
                         len(worker_pids) == 2
                         and all(process.is_alive() for process in vec.processes),
                         {"worker_pids": worker_pids})

            game_pids = vec.env_method("game_pid")
            checks.check("Monitor 包装后 game_pid 可获取（2 个唯一整数）",
                         len(game_pids) == 2
                         and all(isinstance(pid, int) for pid in game_pids)
                         and len(set(game_pids)) == 2,
                         {"game_pids": game_pids})

            checks.check("has_attr('action_masks') 安全返回 True",
                         vec.has_attr("action_masks") is True)
            checks.check("is_masking_supported(vec) 为 True",
                         is_masking_supported(vec) is True)
            masks = get_action_masks(vec)
            checks.check("action_masks 通过 env_method 正确获取",
                         masks.shape == (2, 3) and masks.dtype == np.bool_,
                         {"shape": list(masks.shape), "dtype": str(masks.dtype)})
            refs = vec.get_attr("action_masks")
            checks.check("get_attr('action_masks') 返回 CallableAttributeRef（不 BrokenPipe）",
                         all(isinstance(ref, CallableAttributeRef) for ref in refs),
                         {"types": [type(ref).__name__ for ref in refs]})
            checks.check("get_attr('render_mode') 可读",
                         vec.get_attr("render_mode") == [None, None])

            # 离线 collect_rollouts dry-run：只收集 rollout，不训练/不更新参数
            callback = DryRunCallback()
            model = MaskablePPO(
                "MlpPolicy", vec, n_steps=32, batch_size=64, n_epochs=1,
                verbose=0, device="cpu", seed=11)
            callback.init_callback(model)
            from collections import deque
            model.ep_info_buffer = deque(maxlen=100)
            model.ep_success_buffer = deque(maxlen=100)
            model.num_timesteps = 3520
            model._last_obs = observations
            model._last_episode_starts = np.ones((2,), dtype=bool)
            model.rollout_buffer.reset()
            checks.check("MaskablePPO 构造成功且 num_envs=2",
                         model.env.num_envs == 2,
                         {"num_timesteps_start": int(model.num_timesteps),
                          "n_steps": int(model.n_steps),
                          "n_updates": int(model._n_updates)})
            checkpoints = [int(model.num_timesteps)]
            rollout_steps = []
            for rollout in range(8):
                before = int(model.num_timesteps)
                continue_rollout = model.collect_rollouts(
                    model.env, callback, model.rollout_buffer,
                    model.n_steps, use_masking=True)
                after = int(model.num_timesteps)
                rollout_steps.append(after - before)
                checkpoints.append(after)
                if not continue_rollout:
                    raise RuntimeError("collect_rollouts returned False")
            checks.check("PPO 成功进入 collect_rollouts（8 次）",
                         len(rollout_steps) == 8)
            checks.check("每次 rollout 为 32×2=64 全局 timesteps",
                         rollout_steps == [64] * 8,
                         {"rollout_steps": rollout_steps})
            checks.check("512 为全局 timesteps（8×64）",
                         sum(rollout_steps) == 512,
                         {"total": sum(rollout_steps)})
            checks.check("8 个 rollout 完整执行，起点 3520 → 终点 4032",
                         checkpoints[0] == 3520 and checkpoints[-1] == 4032,
                         {"checkpoints": checkpoints})
            checks.check("dry-run 无梯度更新（_n_updates 不变）",
                         int(model._n_updates) == 0,
                         {"n_updates": int(model._n_updates)})
            result["model"] = {
                "num_timesteps_start": 3520,
                "num_timesteps_end": int(model.num_timesteps),
                "rollout_steps": rollout_steps,
                "n_updates": int(model._n_updates),
                "n_steps": int(model.n_steps),
                "n_envs": int(model.env.num_envs),
                "collect_rollouts_calls": 8,
                "training": False,
            }
            result["worker_pids"] = worker_pids
            result["game_pids"] = [int(pid) for pid in game_pids]
            vec.close()
            vec = None
            time.sleep(0.5)
            checks.check("关闭后无残留游戏进程（离线 dummy 环境）",
                         no_games() == 0, {"games": no_games()})
        finally:
            import shutil
            time.sleep(2.0)
            shutil.rmtree(str(tmp), ignore_errors=True)
        result["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        result["checks"] = checks.rows
        result["summary"] = {
            "total": len(checks.rows),
            "passed": sum(1 for row in checks.rows if row["ok"]),
            "failed": sum(1 for row in checks.rows if not row["ok"]),
        }
        result["success"] = result["summary"]["failed"] == 0
        (out / "p0_4_reapproval_checks.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result["summary"], ensure_ascii=False))
        return 0 if result["success"] else 1
    except BaseException as error:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        checks.check("专项检查过程未抛未知异常", False, {"error": repr(error)})
        result["checks"] = checks.rows
        result["summary"] = {
            "total": len(checks.rows),
            "passed": sum(1 for row in checks.rows if row["ok"]),
            "failed": sum(1 for row in checks.rows if not row["ok"]),
        }
        result["success"] = False
        (out / "p0_4_reapproval_checks.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return 1
    finally:
        if vec is not None:
            try:
                vec.close()
            except BaseException:  # noqa: BLE001
                pass


if __name__ == "__main__":
    sys.exit(main())
