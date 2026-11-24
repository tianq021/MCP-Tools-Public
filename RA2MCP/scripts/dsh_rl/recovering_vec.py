"""P0-5: Worker-independent recovery vector env (full SB3 MaskablePPO compatibility).

P0-4 根因（已定位）：
- train_shared 用 Monitor 包裹真实 env；Monitor 不通过 getattr 暴露内部属性/方法；
- 旧 worker 的 env_method 用 getattr(env, name)，对 game_pid/action_masks 抛 AttributeError，
  且 worker 把任何异常都当作“退出 worker”，父进程 _update_game_pid 又静默吞掉异常；
- RecoveringSubprocVecEnv 未覆盖 has_attr，继承 VecEnv 默认实现（SB3 自己也注明对不可 pickle
  的 method 不安全），于是 MaskablePPO 的 is_masking_supported → has_attr → VecEnv.get_attr
  撞上已死亡的 worker0，表现为 BrokenPipeError（症状，不是根因）。

本实现：
- wrapper-aware 属性/方法访问（get_wrapper_attr / set_wrapper_attr）；
- 完整覆盖 get_attr/set_attr/env_is_wrapped/has_attr/env_method/reset/step_async/step_wait/close；
- 单 worker IPC/env 异常只恢复该 worker，再重试一次；恢复预算耗尽抛 WorkerRecoveryError；
- 不伪造 observation/reward/done/transition；失败 step 抛 FeedbackBatchInterrupted；
- worker 进程尽早登记 PID；每次 reset/step 后立即回传真实 game PID；父进程保留最后已知 PID；
- 结构化 worker 日志（worker_id/process_pid/game_pid/stage/异常/traceback/最后成功时间）；
- set_attr 恢复后重放一次（记录 reapply 事件，避免静默双副作用）；
- close() 只清理已登记的 game PID（并可从 worker 日志补录），不全局误杀；
- feature flag 仍由 train_shared 控制，默认关闭；旧 SafeSubprocVecEnv 未修改。
"""
import json
import os
import random
import subprocess
import threading
import time
import traceback
from dataclasses import dataclass
from multiprocessing import Pipe, Process
from typing import Any, Callable, Optional, Sequence

import numpy as np
from stable_baselines3.common.vec_env import VecEnv
from stable_baselines3.common.vec_env.subproc_vec_env import CloudpickleWrapper

from dsh_rl.safe_vec import FeedbackBatchInterrupted

try:  # 兼容 gymnasium 包装器
    from gymnasium import Wrapper as GymWrapper
except BaseException:  # pragma: no cover
    GymWrapper = None


@dataclass(frozen=True)
class CallableAttributeRef:
    """跨进程可 pickle 的 callable 属性引用（不是数据值，也不用于假装属性存在）。

    SB3 的 SubprocVecEnv.has_attr 使用逐 worker 的 has_attr 命令，避免搬运 bound method
    （bound method 对本项目 env 不可 pickle）。本类仅为 get_attr 遇到 callable 属性时提供
    可序列化的“引用”语义，绝不用于伪造 observation/reward/transition。
    """

    name: str


class WorkerRecoveryError(RuntimeError):
    """Persistent worker failure (recovery budget exhausted); caller must fail cleanly."""

    def __init__(self, message, worker_index=None, stats=None):
        super().__init__(message)
        self.worker_index = worker_index
        self.stats = stats or {}


class WorkerTimeoutError(TimeoutError):
    pass


class _IpcFault(RuntimeError):
    """IPC-level worker fault (EOF/timeout/broken pipe)."""


class _EnvFault(RuntimeError):
    """Worker reported an environment-level exception."""

    def __init__(self, message, payload=None):
        super().__init__(message)
        self.payload = payload or {}


# --------------------------------------------------------------------------- worker
def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _append_jsonl(path, record):
    if not path:
        return
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:  # noqa: BLE001 - logging must never mask env errors
        pass


def _worker_resolve(env, name):
    """Wrapper-aware attribute lookup（Monitor 不会通过 getattr 透传内部属性）。"""
    getter = getattr(env, "get_wrapper_attr", None)
    if callable(getter):
        return getter(name)
    return getattr(env, name)


def _worker_optional(env, name):
    try:
        return _worker_resolve(env, name)
    except AttributeError:
        return None
    except BaseException:  # noqa: BLE001
        return None


def _worker_has_attr(env, name):
    try:
        _worker_resolve(env, name)
        return True
    except AttributeError:
        return False
    except BaseException:  # noqa: BLE001
        return False


def _worker_set_attr(env, name, value):
    setter = getattr(env, "set_wrapper_attr", None)
    if callable(setter):
        setter(name, value)
    else:
        setattr(env, name, value)


def _worker_game_pid(env):
    """尽量返回真实 game PID；拿不到返回 None（绝不伪造）。"""
    for name in ("game_pid",):
        try:
            value = _worker_resolve(env, name)
        except BaseException:  # noqa: BLE001
            continue
        try:
            return int(value)
        except BaseException:  # noqa: BLE001
            continue
    return None


def _worker_impl(remote, parent_remote, env_fn_wrapper, worker_id, log_path):
    parent_remote.close()
    state = {
        "worker_id": int(worker_id),
        "process_pid": os.getpid(),
        "game_pid": None,
        "stage": "env_create",
        "command": None,
        "commands": 0,
        "last_success_ipc": None,
        "last_reset": None,
        "last_step": None,
    }

    def log_event(event, **extra):
        record = {"ts": _now(), "event": event, **state, **extra}
        _append_jsonl(log_path, record)

    def log_exception(error, stage, command, extra=None):
        payload = {
            "kind": "internal",
            "worker_id": int(worker_id),
            "process_pid": os.getpid(),
            "game_pid": state.get("game_pid"),
            "stage": stage,
            "command": command,
            "exception_type": type(error).__name__,
            "exception_message": str(error),
            "traceback": traceback.format_exc(),
            "last_success_ipc": state.get("last_success_ipc"),
            "last_reset": state.get("last_reset"),
            "last_step": state.get("last_step"),
            "commands": state.get("commands"),
            **state,
            **(extra or {}),
        }
        _append_jsonl(log_path, {"ts": _now(), "event": "worker_exception", **payload})
        return payload

    env = None
    try:
        env = env_fn_wrapper.var()
        state["stage"] = "ready"
        remote.send(("ready", env.observation_space, env.action_space))
        log_event("worker_ready")
        while True:
            try:
                command, data = remote.recv()
            except EOFError:
                log_event("worker_exit", reason="eof_from_parent")
                break
            state["command"] = command
            state["commands"] += 1
            try:
                if command == "reset":
                    state["stage"] = "reset"
                    result = env.reset()
                    if isinstance(result, tuple) and len(result) == 2:
                        observation, info = result
                    else:
                        observation, info = result, {}
                    state["game_pid"] = _worker_game_pid(env)
                    state["last_reset"] = _now()
                    state["last_success_ipc"] = state["last_reset"]
                    state["stage"] = "ready"
                    log_event("reset_ok", game_pid=state["game_pid"])
                    remote.send(("ok", {"obs": observation, "info": info,
                                        "game_pid": state["game_pid"]}))
                elif command == "step":
                    state["stage"] = "step"
                    result = env.step(data)
                    if isinstance(result, tuple) and len(result) == 5:
                        observation, reward, terminated, truncated, info = result
                        done = bool(terminated or truncated)
                        info = dict(info or {})
                        info["TimeLimit.truncated"] = bool(truncated and not terminated)
                    else:
                        observation, reward, done, info = result
                        info = dict(info or {})
                    if done:
                        info["terminal_observation"] = observation
                        reset_result = env.reset()
                        observation = reset_result[0] if (
                            isinstance(reset_result, tuple) and len(reset_result) == 2) \
                            else reset_result
                        state["game_pid"] = _worker_game_pid(env)
                        state["last_reset"] = _now()
                        log_event("step_autoreset_ok", game_pid=state["game_pid"])
                    state["last_step"] = _now()
                    state["last_success_ipc"] = state["last_step"]
                    state["stage"] = "ready"
                    remote.send(("ok", {"obs": observation, "reward": reward,
                                        "done": done, "info": info,
                                        "game_pid": state["game_pid"]}))
                elif command == "env_method":
                    state["stage"] = "env_method"
                    name, args, kwargs = data
                    hook = _worker_optional(env, "_before_env_method")
                    if callable(hook):
                        hook(name)
                    attribute = _worker_resolve(env, name)
                    result = attribute(*args, **kwargs) if callable(attribute) else attribute
                    state["last_success_ipc"] = _now()
                    state["stage"] = "ready"
                    remote.send(("ok", result))
                elif command == "get_attr":
                    state["stage"] = "get_attr"
                    hook = _worker_optional(env, "_before_get_attr")
                    if callable(hook):
                        hook(data)
                    try:
                        value = _worker_resolve(env, data)
                    except AttributeError:
                        state["stage"] = "ready"
                        remote.send(("missing", data))
                        continue
                    if callable(value):
                        value = CallableAttributeRef(data)
                    state["last_success_ipc"] = _now()
                    state["stage"] = "ready"
                    remote.send(("ok", value))
                elif command == "has_attr":
                    state["stage"] = "has_attr"
                    hook = _worker_optional(env, "_before_has_attr")
                    if callable(hook):
                        hook(data)
                    exists = _worker_has_attr(env, data)
                    state["last_success_ipc"] = _now()
                    state["stage"] = "ready"
                    remote.send(("ok", exists))
                elif command == "set_attr":
                    state["stage"] = "set_attr"
                    name, value = data
                    hook = _worker_optional(env, "_before_set_attr")
                    if callable(hook):
                        hook(name, value)
                    _worker_set_attr(env, name, value)
                    state["last_success_ipc"] = _now()
                    state["stage"] = "ready"
                    remote.send(("ok", True))
                elif command == "env_is_wrapped":
                    state["stage"] = "env_is_wrapped"
                    hook = _worker_optional(env, "_before_env_is_wrapped")
                    if callable(hook):
                        hook(data)
                    wrapped = isinstance(env, data)
                    state["last_success_ipc"] = _now()
                    state["stage"] = "ready"
                    remote.send(("ok", wrapped))
                elif command == "game_pid":
                    state["stage"] = "game_pid"
                    state["game_pid"] = _worker_game_pid(env)
                    state["last_success_ipc"] = _now()
                    state["stage"] = "ready"
                    remote.send(("ok", state["game_pid"]))
                elif command == "seed":
                    state["stage"] = "seed"
                    try:
                        seed_fn = _worker_resolve(env, "seed")
                    except AttributeError:
                        seed_fn = None
                    if callable(seed_fn):
                        seed_fn(data)
                    state["last_success_ipc"] = _now()
                    state["stage"] = "ready"
                    remote.send(("ok", None))
                elif command == "close":
                    log_event("worker_close_requested")
                    remote.send(("ok", None))
                    break
                else:
                    raise RuntimeError("unknown worker command %r" % (command,))
            except BaseException as error:  # noqa: BLE001
                payload = log_exception(error, state["stage"], command)
                try:
                    remote.send(("error", payload))
                except BaseException:  # noqa: BLE001 - pipe may already be broken
                    pass
                break
    except BaseException as error:  # noqa: BLE001 - env create/ready failure
        payload = log_exception(error, state.get("stage", "env_create"),
                               state.get("command"))
        try:
            remote.send(("error", payload))
        except BaseException:  # noqa: BLE001
            pass
    finally:
        try:
            if env is not None:
                env.close()
        except BaseException:  # noqa: BLE001
            pass
        log_event("worker_exit", reason="worker returned", exit_code=0,
                  game_pid=state.get("game_pid"))
        try:
            remote.close()
        except BaseException:  # noqa: BLE001
            pass


def _worker(remote, parent_remote, env_fn_wrapper, worker_id, log_path):
    try:
        _worker_impl(remote, parent_remote, env_fn_wrapper, worker_id, log_path)
    except BaseException:  # noqa: BLE001
        _append_jsonl(log_path, {
            "ts": _now(), "event": "worker_fatal", "worker_id": int(worker_id),
            "process_pid": os.getpid(), "traceback": traceback.format_exc()})
        raise


# --------------------------------------------------------------------------- parent
class RecoveringSubprocVecEnv(VecEnv):
    """SB3-compatible VecEnv with per-worker recovery and full interface coverage."""

    def __init__(
        self,
        env_fns: Sequence[Callable[[], Any]],
        start_method: str = "spawn",
        max_launches: int = 2,
        launch_jitter: tuple = (1.0, 3.0),
        recv_timeout_s: float = 180.0,
        startup_timeout_s: float = 180.0,
        max_restarts_per_worker: int = 5,
        stats_dir=None,
        seed: int = 0,
        kill_pid_hook: Optional[Callable[[int], bool]] = None,
        verbose: int = 0,
    ):
        if not env_fns:
            raise ValueError("env_fns must not be empty")
        self._env_fns = [CloudpickleWrapper(fn) for fn in env_fns]
        self.num_envs = len(env_fns)
        self._ctx = __import__("multiprocessing").get_context(start_method)
        self._max_launches = max(1, int(max_launches))
        self._launch_sem = threading.Semaphore(self._max_launches)
        self._launch_rng = random.Random(seed)
        self._launch_jitter = (float(launch_jitter[0]), float(launch_jitter[1]))
        self._recv_timeout_s = float(recv_timeout_s)
        self._startup_timeout_s = float(startup_timeout_s)
        self._max_restarts = int(max_restarts_per_worker)
        self._stats_dir = stats_dir
        self._kill_pid_hook = kill_pid_hook
        self._verbose = int(verbose)
        self._launch_log = []
        self._active_launches = 0
        self.max_concurrent_launches = 0
        self.processes: list = [None] * self.num_envs
        self.remotes: list = [None] * self.num_envs
        self._stats_file = None
        self._worker_log_paths = [None] * self.num_envs
        if self._stats_dir is not None:
            path = __import__("pathlib").Path(self._stats_dir)
            path.mkdir(parents=True, exist_ok=True)
            self._stats_file = path / "worker-stats.jsonl"
            self._worker_log_paths = [str(path / ("worker-%d.log" % i))
                                      for i in range(self.num_envs)]
        self.worker_stats = [
            {"index": i, "process_pid": None, "process_created_at": None,
             "spawns": 0, "restarts": 0, "crashed": 0, "episodes_completed": 0,
             "crashes": [], "created_at": None, "last_error": None,
             "last_game_pid": None, "last_success_ipc": None,
             "last_reset": None, "last_step": None, "exit_code": None,
             "death_stage": None, "log_path": self._worker_log_paths[i]}
            for i in range(self.num_envs)
        ]
        self._last_game_pid: list = [None] * self.num_envs
        self._death_detected: list = [False] * self.num_envs

        observation_space = action_space = None
        try:
            for index in range(self.num_envs):
                obs_space_i, action_space_i = self._spawn_worker(index, reason="startup")
                if observation_space is None:
                    observation_space, action_space = obs_space_i, action_space_i
            super().__init__(self.num_envs, observation_space, action_space)
            self._write_stats_event("env_created", {"workers": self.num_envs})
        except BaseException:
            self._close_processes()
            raise

    # ------------------------------------------------------------------ logging
    def _write_stats_event(self, event, payload):
        if self._stats_file is None:
            return
        record = {"ts": _now(), "event": event, **payload}
        try:
            self._stats_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._stats_file, "a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ launch/pid
    def _spawn_worker(self, index, reason):
        last_error = None
        attempts = self._max_restarts + 1
        for attempt in range(attempts):
            delay = self._launch_rng.uniform(*self._launch_jitter)
            self._launch_sem.acquire()
            process = parent_conn = child_conn = None
            try:
                self._active_launches += 1
                self.max_concurrent_launches = max(self.max_concurrent_launches,
                                                   self._active_launches)
                started = time.time()
                self._launch_log.append({"t": started, "index": index,
                                         "reason": reason, "attempt": attempt,
                                         "delay": round(delay, 3)})
                if delay > 0:
                    time.sleep(delay)
                parent_conn, child_conn = self._ctx.Pipe()
                log_path = self._worker_log_paths[index]
                process = self._ctx.Process(
                    target=_worker,
                    args=(child_conn, parent_conn, self._env_fns[index], index, log_path),
                    daemon=False)
                process.start()
                child_conn.close()
                child_conn = None
                if not parent_conn.poll(self._startup_timeout_s):
                    raise WorkerTimeoutError(
                        "worker %d handshake timeout (%.1fs)"
                        % (index, self._startup_timeout_s))
                try:
                    message = parent_conn.recv()
                except (EOFError, OSError):
                    raise WorkerTimeoutError("worker %d died during startup" % index)
                if not isinstance(message, tuple) or message[0] != "ready":
                    raise WorkerRecoveryError(
                        "worker %d bad handshake or startup error: %r"
                        % (index, message), worker_index=index,
                        stats=self.worker_stats[index])
                self.processes[index] = process
                self.remotes[index] = parent_conn
                stats = self.worker_stats[index]
                stats["process_pid"] = process.pid
                stats["process_created_at"] = time.strftime(
                    "%Y-%m-%dT%H:%M:%S", time.localtime(started))
                stats["spawns"] += 1
                stats["created_at"] = stats["created_at"] or _now()
                stats["exit_code"] = None
                stats["death_stage"] = None
                self._death_detected[index] = False
                self._write_stats_event("worker_started", {
                    "index": index, "process_pid": process.pid,
                    "reason": reason, "log_path": log_path})
                return message[1], message[2]
            except BaseException as error:  # noqa: BLE001
                last_error = error
                for connection in (parent_conn, child_conn):
                    try:
                        if connection is not None:
                            connection.close()
                    except BaseException:  # noqa: BLE001
                        pass
                self.processes[index] = None
                self.remotes[index] = None
                self._write_stats_event("worker_start_failed", {
                    "index": index, "reason": reason, "attempt": attempt,
                    "error": repr(error)})
                if self._verbose:
                    print("[recovering_vec] spawn %d attempt %d failed: %r"
                          % (index, attempt, error), flush=True)
            finally:
                self._active_launches -= 1
                self._launch_sem.release()
        raise WorkerRecoveryError(
            "worker %d failed to start after %d attempts: %r"
            % (index, attempts, last_error), worker_index=index,
            stats=self.worker_stats[index])

    def _kill_pid(self, pid):
        if pid is None:
            return False
        if self._kill_pid_hook is not None:
            try:
                return bool(self._kill_pid_hook(int(pid)))
            except BaseException:  # noqa: BLE001
                return False
        try:
            result = subprocess.run(["taskkill", "/PID", str(int(pid)), "/T", "/F"],
                                    capture_output=True, timeout=10)
            return result.returncode == 0
        except BaseException:  # noqa: BLE001
            return False

    @staticmethod
    def _kill_process(process, timeout=8.0):
        if process is None:
            return
        try:
            process.terminate()
            process.join(timeout)
        except BaseException:  # noqa: BLE001
            pass
        if process.is_alive():
            try:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               capture_output=True, timeout=10)
            except BaseException:  # noqa: BLE001
                pass
            try:
                process.join(2)
            except BaseException:  # noqa: BLE001
                pass

    def _read_last_pid_from_worker_log(self, index):
        path = self._worker_log_paths[index]
        if not path or not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as stream:
                lines = stream.readlines()[-200:]
            for line in reversed(lines):
                try:
                    record = json.loads(line)
                except BaseException:  # noqa: BLE001
                    continue
                value = record.get("game_pid")
                if value is not None:
                    return int(value)
        except BaseException:  # noqa: BLE001
            return None
        return None

    # ------------------------------------------------------------------ io
    def _send(self, index, command, data=None):
        remote = self.remotes[index]
        if remote is None:
            raise _IpcFault("worker %d has no remote" % index)
        try:
            remote.send((command, data))
        except (EOFError, OSError, BrokenPipeError) as error:
            raise _IpcFault("worker %d send failed: %r" % (index, error))

    def _recv_raw(self, index, timeout=None):
        remote = self.remotes[index]
        if remote is None:
            raise _IpcFault("worker %d has no remote" % index)
        deadline = self._recv_timeout_s if timeout is None else timeout
        if not remote.poll(deadline):
            raise WorkerTimeoutError("worker %d IPC timeout after %.1fs"
                                     % (index, deadline))
        try:
            message = remote.recv()
        except (EOFError, OSError, BrokenPipeError) as error:
            raise _IpcFault("worker %d IPC recv failed: %r" % (index, error))
        if not isinstance(message, tuple):
            raise _IpcFault("worker %d bad message: %r" % (index, message))
        if message[0] == "error":
            raise _EnvFault("worker %d environment error: %s"
                            % (index, message[1]), message[1])
        return message

    def _recv_payload(self, index, timeout=None):
        message = self._recv_raw(index, timeout=timeout)
        if message[0] != "ok":
            raise _IpcFault("worker %d unexpected message %r" % (index, message[0]))
        return message[1]

    def _record_death(self, index, reason, stage=None):
        stats = self.worker_stats[index]
        stats["crashed"] += 1
        exit_code = None
        process = self.processes[index]
        if process is not None:
            try:
                exit_code = process.exitcode
            except BaseException:  # noqa: BLE001
                exit_code = None
        stats["exit_code"] = exit_code
        stats["death_stage"] = stage
        stats["last_error"] = str(reason)
        stats["crashes"].append({"t": _now(), "reason": str(reason),
                                 "stage": stage,
                                 "last_game_pid": self._last_game_pid[index],
                                 "exit_code": exit_code})
        self._death_detected[index] = True
        self._write_stats_event("worker_death", {
            "index": index, "reason": str(reason), "stage": stage,
            "process_pid": stats.get("process_pid"),
            "last_game_pid": self._last_game_pid[index],
            "exit_code": exit_code})

    def _recover_worker(self, index, reason):
        stats = self.worker_stats[index]
        stats["restarts"] += 1
        self._write_stats_event("worker_recovery_started", {
            "index": index, "reason": str(reason),
            "restart": stats["restarts"],
            "last_game_pid": stats.get("last_game_pid")})
        if stats["restarts"] > self._max_restarts:
            raise WorkerRecoveryError(
                "worker %d exceeded restart budget %d (last reason: %s)"
                % (index, self._max_restarts, reason), worker_index=index, stats=stats)
        # 清理旧进程树与已登记游戏 PID（精确清理，不全局 kill）
        old_process = self.processes[index]
        self._kill_process(old_process)
        exit_code = None
        try:
            exit_code = old_process.exitcode if old_process is not None else None
        except BaseException:  # noqa: BLE001
            exit_code = None
        if exit_code is not None:
            stats["exit_code"] = exit_code
            if stats["crashes"]:
                stats["crashes"][-1]["exit_code"] = exit_code
            self._write_stats_event("worker_exit_code_recorded", {
                "index": index, "exit_code": exit_code})
        self.processes[index] = None
        remote = self.remotes[index]
        if remote is not None:
            try:
                remote.close()
            except BaseException:  # noqa: BLE001
                pass
        self.remotes[index] = None
        old_pid = self._last_game_pid[index] or self._read_last_pid_from_worker_log(index)
        if old_pid is not None:
            killed = self._kill_pid(old_pid)
            self._write_stats_event("worker_old_game_pid_cleanup", {
                "index": index, "pid": int(old_pid), "killed": killed})
            time.sleep(0.5)
        # 恢复预算由 _spawn_worker 内部有限尝试保证；恢复中的 reset 不允许递归恢复
        try:
            obs_space, action_space = self._spawn_worker(index, reason="recovery")
            observation = self._reset_single(index, recover=False)
        except WorkerRecoveryError:
            self._kill_process(self.processes[index])
            self.processes[index] = None
            raise
        except BaseException as error:  # noqa: BLE001
            self._kill_process(self.processes[index])
            self.processes[index] = None
            raise WorkerRecoveryError(
                "worker %d failed to reset after recovery: %r" % (index, error),
                worker_index=index, stats=self.worker_stats[index])
        new_pid = self._last_game_pid[index]
        if old_pid is not None and new_pid == old_pid:
            raise WorkerRecoveryError(
                "worker %d duplicate game PID after recovery (%s)"
                % (index, new_pid), worker_index=index, stats=stats)
        self._write_stats_event("worker_recovered", {
            "index": index, "restarts": stats["restarts"],
            "new_game_pid": new_pid, "old_game_pid": old_pid})
        return observation

    def _update_from_payload(self, index, payload):
        if isinstance(payload, dict) and payload.get("game_pid") is not None:
            try:
                pid = int(payload["game_pid"])
            except BaseException:  # noqa: BLE001
                return
            self._last_game_pid[index] = pid
            self.worker_stats[index]["last_game_pid"] = pid
            self.worker_stats[index]["last_success_ipc"] = _now()
        if isinstance(payload, dict) and "info" in payload:
            self.worker_stats[index]["last_reset"] = _now()

    def _call(self, index, command, data=None, stage=None, recover=True,
              expect="ok", timeout=None):
        """Send one command; on IPC/env fault recover this worker and retry once."""
        attempts = 2 if recover else 1
        last_error = None
        for attempt in range(attempts):
            try:
                self._send(index, command, data)
                message = self._recv_raw(index, timeout=timeout)
                if expect is not None and message[0] != expect:
                    if message[0] == "missing":
                        return ("missing", message[1])
                    raise _IpcFault("unexpected message %r" % (message[0],))
                if message[0] == "missing":
                    return ("missing", message[1])
                return ("ok", message[1])
            except (_IpcFault, _EnvFault, WorkerTimeoutError) as error:
                last_error = error
                if not recover:
                    raise
                if attempt == 0:
                    self._record_death(index, repr(error), stage or command)
                    try:
                        self._recover_worker(index, repr(error))
                    except WorkerRecoveryError:
                        raise
                    continue
                raise WorkerRecoveryError(
                    "worker %d failed command %s after recovery: %r"
                    % (index, command, error), worker_index=index,
                    stats=self.worker_stats[index])
        raise WorkerRecoveryError(
            "worker %d command %s failed: %r"
            % (index, command, last_error), worker_index=index,
            stats=self.worker_stats[index])

    def _reset_single(self, index, recover=True):
        try:
            status, payload = self._call(index, "reset", stage="reset",
                                         expect="ok", recover=recover)
        except (_IpcFault, _EnvFault, WorkerTimeoutError) as error:
            raise WorkerRecoveryError(
                "worker %d reset failed: %r" % (index, error),
                worker_index=index, stats=self.worker_stats[index])
        if status != "ok" or not isinstance(payload, dict):
            raise WorkerRecoveryError("worker %d bad reset payload" % index,
                                      worker_index=index,
                                      stats=self.worker_stats[index])
        self._update_from_payload(index, payload)
        return payload["obs"]


    # ------------------------------------------------------------------ VecEnv API
    def step_async(self, actions):
        self.waiting = True
        self._step_errors = {}
        for index in range(self.num_envs):
            try:
                self._send(index, "step", actions[index])
            except (_IpcFault, OSError, BrokenPipeError) as error:
                self._step_errors[index] = error

    def step_wait(self):
        if not self.waiting:
            raise RuntimeError("step_wait called before step_async")
        self.waiting = False
        results = {}
        failed = dict(getattr(self, "_step_errors", {}))
        for index in range(self.num_envs):
            if index in failed:
                continue
            try:
                payload = self._recv_payload(index)
                results[index] = payload
            except _EnvFault as error:
                failed[index] = error
            except (_IpcFault, WorkerTimeoutError) as error:
                failed[index] = error
        if failed:
            observations = []
            dones = np.zeros(self.num_envs, dtype=bool)
            infos = []
            for index in range(self.num_envs):
                if index in failed:
                    self._record_death(index, repr(failed[index]), "step")
                    try:
                        observation = self._recover_worker(index, repr(failed[index]))
                    except WorkerRecoveryError:
                        self.close()
                        raise
                    observations.append(observation)
                    dones[index] = True
                    infos.append({"feedback_valid": False, "learning_excluded": True,
                                  "worker_restarted": True,
                                  "failure": repr(failed[index])})
                else:
                    payload = results[index]
                    self._update_from_payload(index, payload)
                    observations.append(payload["obs"])
                    dones[index] = bool(payload["done"])
                    infos.append(payload["info"])
                    if payload["done"]:
                        self.worker_stats[index]["episodes_completed"] += 1
            self._write_stats_event("batch_interrupted", {
                "failed_workers": [i for i in range(self.num_envs) if i in failed],
                "n_workers": self.num_envs})
            raise FeedbackBatchInterrupted(np.asarray(observations),
                                           np.asarray(dones), infos)
        observations, rewards, dones, infos = [], [], [], []
        for index in range(self.num_envs):
            payload = results[index]
            self._update_from_payload(index, payload)
            observations.append(payload["obs"])
            rewards.append(payload["reward"])
            dones.append(bool(payload["done"]))
            infos.append(payload["info"])
            if payload["done"]:
                self.worker_stats[index]["episodes_completed"] += 1
        return (np.asarray(observations), np.asarray(rewards),
                np.asarray(dones), infos)

    def reset(self):
        observations = []
        for index in range(self.num_envs):
            try:
                observations.append(self._reset_single(index))
            except (_IpcFault, _EnvFault, WorkerTimeoutError) as error:
                self._record_death(index, repr(error), "reset")
                try:
                    observations.append(self._recover_worker(index, repr(error)))
                except WorkerRecoveryError:
                    self.close()
                    raise
        return np.asarray(observations)

    def set_attr(self, attr_name, value, indices=None):
        targets = list(range(self.num_envs)) if indices is None else \
            ([indices] if isinstance(indices, int) else list(indices))
        for index in targets:
            try:
                status, _ = self._call(index, "set_attr", (attr_name, value),
                                       stage="set_attr", expect="ok", recover=False)
            except (_IpcFault, _EnvFault, WorkerTimeoutError) as error:
                # 恢复后必须重新应用一次；记录 reapply，避免静默重复副作用。
                self._record_death(index, repr(error), "set_attr")
                self._recover_worker(index, repr(error))
                try:
                    status, _ = self._call(index, "set_attr", (attr_name, value),
                                           stage="set_attr", expect="ok", recover=False)
                except (_IpcFault, _EnvFault, WorkerTimeoutError) as retry_error:
                    raise WorkerRecoveryError(
                        "worker %d set_attr failed after recovery: %r"
                        % (index, retry_error), worker_index=index,
                        stats=self.worker_stats[index])
                self._write_stats_event("set_attr_reapplied", {
                    "index": index, "attr": attr_name, "value": repr(value)})
        return None

    def has_attr(self, attr_name):
        # 与 SB3 SubprocVecEnv 一致：逐 worker 询问存在性；不搬运 bound method。
        results = []
        for index in range(self.num_envs):
            status, payload = self._call(index, "has_attr", attr_name,
                                         stage="has_attr", expect="ok")
            results.append(bool(payload))
        return all(results)

    def env_is_wrapped(self, wrapper_class, indices=None):
        targets = list(range(self.num_envs)) if indices is None else \
            ([indices] if isinstance(indices, int) else list(indices))
        values = []
        for index in targets:
            status, payload = self._call(index, "env_is_wrapped", wrapper_class,
                                         stage="env_is_wrapped", expect="ok")
            values.append(bool(payload))
        return values

    def env_method(self, method_name, *method_args, indices=None, **method_kwargs):
        targets = list(range(self.num_envs)) if indices is None else \
            ([indices] if isinstance(indices, int) else list(indices))
        results = []
        for index in targets:
            status, payload = self._call(index, "env_method",
                                         (method_name, method_args, method_kwargs),
                                         stage="env_method", expect="ok")
            results.append(payload)
        return results

    def get_attr(self, attr_name, indices=None):  # noqa: F811 - keep interface explicit
        targets = list(range(self.num_envs)) if indices is None else \
            ([indices] if isinstance(indices, int) else list(indices))
        values = []
        for index in targets:
            status, payload = self._call(index, "get_attr", attr_name,
                                         stage="get_attr", expect=None)
            if status == "missing":
                raise AttributeError(attr_name)
            values.append(payload)
        return values

    def seed(self, seed=None):
        seeds = [seed + i if seed is not None else None for i in range(self.num_envs)]
        for index, value in enumerate(seeds):
            try:
                self._send(index, "seed", value)
                self._recv_raw(index)
            except BaseException:  # noqa: BLE001 - seed best effort
                pass
        return seeds

    # ------------------------------------------------------------------ close
    def _close_processes(self):
        for index in range(self.num_envs):
            remote = self.remotes[index]
            if remote is not None:
                try:
                    remote.send(("close", None))
                except BaseException:  # noqa: BLE001
                    pass
        for index in range(self.num_envs):
            process = self.processes[index]
            self._kill_process(process)
            self.processes[index] = None
            remote = self.remotes[index]
            if remote is not None:
                try:
                    remote.close()
                except BaseException:  # noqa: BLE001
                    pass
                self.remotes[index] = None
            pid = self._last_game_pid[index] or self._read_last_pid_from_worker_log(index)
            if pid is not None:
                self._kill_pid(pid)
                self._write_stats_event("game_pid_cleanup_on_close", {
                    "index": index, "pid": int(pid)})
        time.sleep(0.3)

    def close(self):
        if getattr(self, "closed", False):
            return
        self._close_processes()
        self._write_stats_event("env_closed", self.stats_summary())
        self.closed = True

    def stats_summary(self):
        return {
            "workers": self.worker_stats,
            "launches": len(self._launch_log),
            "max_concurrent_launches": self.max_concurrent_launches,
            "restarts": sum(w["restarts"] for w in self.worker_stats),
            "crashes": sum(w["crashed"] for w in self.worker_stats),
            "episodes_completed": sum(w["episodes_completed"] for w in self.worker_stats),
        }
