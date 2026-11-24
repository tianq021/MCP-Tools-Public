"""P0-5 offline fault-injection dummy envs (no real game).

支持：
- Gymnasium 5-tuple API；
- Monitor 包装（复现 P0-4：train_shared 用 Monitor 包裹真实 env）；
- get_attr/has_attr/set_attr/env_is_wrapped/env_method 命令级故障注入；
- spawn 计数（首次 spawn 故障、恢复后正常）；
- 真实语义的 game_pid 属性（不是方法）与可 set/get 的数据属性。
"""
import os
import time

import numpy as np

try:
    import gymnasium as gym
    from gymnasium import spaces
except BaseException:  # pragma: no cover
    gym = None
    spaces = None


def _bump_counter(path):
    if not path:
        return 1
    try:
        current = 0
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as stream:
                current = int((stream.read().strip() or "0"))
        current += 1
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as stream:
            stream.write(str(current))
        return current
    except BaseException:  # noqa: BLE001
        return 1


class DummyWorkerEnv(gym.Env):
    """Gymnasium env with scriptable, per-spawn command faults."""

    metadata = {"render_modes": []}

    def __init__(self, spec):
        super().__init__()
        self.test_spec = dict(spec or {})
        self.spec = None  # 避免覆盖 gymnasium.Env.spec（Monitor 需要 env.spec.id）
        self.render_mode = None
        self.index = int(self.test_spec.get("index", 0))
        self.spawn_attempt = int(self.test_spec.get("spawn_attempt", 1))
        self.steps = 0
        self.dummy_attr = self.test_spec.get("dummy_attr", "dummy-value")
        self.dummy_setting = self.test_spec.get("dummy_setting", "initial")
        self.fake_pid = int(self.test_spec.get("pid_base", 1000)) \
            + (os.getpid() % 100000) * 10 + self.index
        self._faulted = set()
        self.observation_space = spaces.Box(low=-10.0, high=10.0, shape=(2,),
                                            dtype=np.float32)
        self.action_space = spaces.Discrete(3)

    # ------------------------------------------------------------ fault hooks
    def _maybe_fault(self, command, extra=None):
        faults = self.test_spec.get("fault_commands") or []
        if command not in faults:
            return
        fault_attrs = self.test_spec.get("fault_attrs")
        if fault_attrs is not None:
            target = extra[0] if isinstance(extra, tuple) and extra else extra
            if target not in fault_attrs:
                return
        fault_spawns = [int(value) for value in
                        (self.test_spec.get("fault_spawns") or [1])]
        if self.spawn_attempt not in fault_spawns:
            return
        key = (command, self.spawn_attempt)
        if key in self._faulted:
            return
        self._faulted.add(key)
        fault_type = self.test_spec.get("fault_type", "exit")
        if fault_type == "exit":
            os._exit(7)
        if fault_type == "sleep":
            time.sleep(float(self.test_spec.get("sleep_s", 2.0)))
        raise RuntimeError("dummy %s fault on spawn %d"
                           % (command, self.spawn_attempt))

    def _before_get_attr(self, name):
        self._maybe_fault("get_attr", name)

    def _before_has_attr(self, name):
        self._maybe_fault("has_attr", name)

    def _before_set_attr(self, name, value):
        self._maybe_fault("set_attr", (name, value))

    def _before_env_method(self, name):
        self._maybe_fault("env_method", name)

    def _before_env_is_wrapped(self, wrapper_class):
        self._maybe_fault("env_is_wrapped", wrapper_class)

    # ------------------------------------------------------------ gymnasium API
    def reset(self, *, seed=None, options=None):
        self.steps = 0
        return self._obs(), {}

    def step(self, action):
        self.steps += 1
        fault_on = self.test_spec.get("crash_on_step")
        if fault_on is not None and self.steps >= int(fault_on):
            crash_spawns = [int(value) for value in
                            (self.test_spec.get("crash_spawns") or [1])]
            if self.spawn_attempt in crash_spawns:
                crash_type = self.test_spec.get("crash_type", "exit")
                if crash_type == "exit":
                    os._exit(3)
                if crash_type == "sleep":
                    time.sleep(float(self.test_spec.get("sleep_s", 2.0)))
                if crash_type == "raise":
                    raise RuntimeError("dummy worker crash")
        episode_length = int(self.test_spec.get("episode_length", 10_000))
        done = self.steps >= episode_length
        return self._obs(), 0.1, done, False, {"worker_index": self.index}

    def _obs(self):
        return np.asarray([float(self.index), self.steps / 100.0], dtype=np.float32)

    # ------------------------------------------------------------ normal methods/attrs
    def action_masks(self):
        return np.ones(3, dtype=bool)

    @property
    def game_pid(self):
        return self.fake_pid

    def is_episode_active(self):
        return True

    def close(self):
        pass


def make_dummy_env(index, spec, monitor_path=None):
    """Factory used by tests. 若给 monitor_path，则用 SB3 Monitor 包装（复现 P0-4）。"""
    spec = dict(spec or {})
    spec["index"] = index
    attempt = _bump_counter(spec.get("_attempt_counter_file"))
    spec["spawn_attempt"] = attempt
    fail_attempts = [int(value) for value in spec.get("factory_fail_attempts", [])]
    if attempt in fail_attempts:
        raise RuntimeError("dummy factory fail at attempt %d" % attempt)
    env = DummyWorkerEnv(spec)
    if monitor_path:
        from stable_baselines3.common.monitor import Monitor
        parent = os.path.dirname(str(monitor_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        env = Monitor(env, str(monitor_path))
    return env
