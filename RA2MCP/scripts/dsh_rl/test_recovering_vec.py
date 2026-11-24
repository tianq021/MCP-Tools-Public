"""P0-5 RecoveringSubprocVecEnv 完整接口故障注入测试（离线，不启动红警）。

运行：
  PYTHONPATH=E:\\RA2MCP\\.venv-rl\\Lib\\site-packages;E:\\RA2MCP\\scripts
  python scripts/dsh_rl/run_recovering_tests.py
"""
import json
import pathlib
import sys
import tempfile
import time
import unittest
from functools import partial

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from gymnasium import Wrapper as GymWrapper  # noqa: E402
from sb3_contrib.common.maskable.utils import (  # noqa: E402
    get_action_masks,
    is_masking_supported,
)

from dsh_rl.recovering_vec import (  # noqa: E402
    CallableAttributeRef,
    RecoveringSubprocVecEnv,
    WorkerRecoveryError,
)
from dsh_rl.safe_vec import FeedbackBatchInterrupted, resume_after_feedback  # noqa: E402
from dsh_rl.recovery_test_envs import make_dummy_env  # noqa: E402


def _make_vec(tmp, specs, wrap_monitor=False, **kwargs):
    tmp = pathlib.Path(tmp)
    env_fns = []
    for index, spec in enumerate(specs):
        spec = dict(spec or {})
        spec.setdefault("_attempt_counter_file", str(tmp / ("attempt-%d.txt" % index)))
        monitor_path = str(tmp / ("monitor-%d.csv" % index)) if wrap_monitor else None
        env_fns.append(partial(make_dummy_env, index, spec, monitor_path))
    options = {
        "max_launches": 2,
        "launch_jitter": (0.0, 0.0),
        "recv_timeout_s": 5.0,
        "startup_timeout_s": 30.0,
        "max_restarts_per_worker": 3,
        "stats_dir": tmp / "stats",
        "kill_pid_hook": lambda pid: True,
        "verbose": 0,
    }
    options.update(kwargs)
    return RecoveringSubprocVecEnv(env_fns, **options)


def _events(tmp):
    path = pathlib.Path(tmp) / "stats" / "worker-stats.jsonl"
    events = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    events.append(json.loads(line))
                except Exception:  # noqa: BLE001
                    pass
    return events


def _worker_log_records(tmp, index=0):
    path = pathlib.Path(tmp) / "stats" / ("worker-%d.log" % index)
    records = []
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip():
                try:
                    records.append(json.loads(line))
                except Exception:  # noqa: BLE001
                    pass
    return records


class RecoveringVecTests(unittest.TestCase):
    # ------------------------------------------------------------------ basic/SB3 接口
    def test_basic_protocol_and_launch_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            vec = _make_vec(tmp, [{}, {}, {}, {}], max_launches=2,
                            launch_jitter=(0.0, 0.02))
            try:
                obs = vec.reset()
                self.assertEqual(obs.shape, (4, 2))
                self.assertLessEqual(vec.max_concurrent_launches, 2)
                self.assertEqual(len(vec._launch_log), 4)
                masks = vec.env_method("action_masks")
                self.assertEqual([mask.shape for mask in masks], [(3,)] * 4)
                pids = vec.env_method("game_pid")
                self.assertEqual(len(set(pids)), 4)
                vec.step_async(np.zeros(4, dtype=np.int64))
                observations, rewards, dones, infos = vec.step_wait()
                self.assertEqual(observations.shape, (4, 2))
                self.assertEqual(rewards.shape, (4,))
                self.assertFalse(dones.any())
                self.assertEqual(len(infos), 4)
            finally:
                vec.close()

    def test_monitor_wrapped_maskable_interface(self):
        """覆盖 P0-4 真实失败场景：Monitor 包装 + MaskablePPO 启动接口。"""
        with tempfile.TemporaryDirectory() as tmp:
            vec = _make_vec(tmp, [{}, {}], wrap_monitor=True)
            try:
                vec.reset()
                pids = vec.env_method("game_pid")
                self.assertEqual(len(set(pids)), 2)
                self.assertTrue(vec.has_attr("action_masks"))
                self.assertTrue(is_masking_supported(vec))
                masks = get_action_masks(vec)
                self.assertEqual(masks.shape, (2, 3))
                refs = vec.get_attr("action_masks")
                self.assertTrue(all(isinstance(ref, CallableAttributeRef) for ref in refs))
                self.assertEqual(vec.get_attr("dummy_attr"),
                                 ["dummy-value", "dummy-value"])
                self.assertEqual(vec.get_attr("render_mode"), [None, None])
                vec.set_attr("dummy_setting", "changed")
                self.assertEqual(vec.get_attr("dummy_setting"), ["changed", "changed"])
                vec.step_async(np.zeros(2, dtype=np.int64))
                vec.step_wait()
            finally:
                vec.close()

    def test_action_masks_startup_check_without_monitor(self):
        with tempfile.TemporaryDirectory() as tmp:
            vec = _make_vec(tmp, [{}, {}])
            try:
                vec.reset()
                self.assertTrue(vec.has_attr("action_masks"))
                self.assertTrue(is_masking_supported(vec))
                self.assertEqual(get_action_masks(vec).shape, (2, 3))
                self.assertTrue(all(isinstance(ref, CallableAttributeRef)
                                    for ref in vec.get_attr("action_masks")))
            finally:
                vec.close()

    # ------------------------------------------------------------------ 逐接口 Worker 死亡
    def test_get_attr_worker_death_recovers(self):
        with tempfile.TemporaryDirectory() as tmp:
            vec = _make_vec(tmp, [{"fault_commands": ["get_attr"],
                                   "fault_attrs": ["dummy_attr"],
                                   "fault_type": "exit", "fault_spawns": [1],
                                   "dummy_attr": "real-value"}, {}])
            try:
                vec.reset()
                old_processes = [process for process in vec.processes]
                values = vec.get_attr("dummy_attr")
                self.assertEqual(values, ["real-value", "dummy-value"])
                self.assertIsNot(vec.processes[0], old_processes[0])
                self.assertIs(vec.processes[1], old_processes[1])
                stats = vec.stats_summary()
                self.assertGreaterEqual(stats["workers"][0]["restarts"], 1)
                self.assertEqual(stats["workers"][0]["crashed"], 1)
                self.assertGreaterEqual(stats["workers"][0]["spawns"], 2)
            finally:
                vec.close()

    def test_has_attr_worker_death_recovers(self):
        with tempfile.TemporaryDirectory() as tmp:
            vec = _make_vec(tmp, [{"fault_commands": ["has_attr"],
                                   "fault_type": "exit", "fault_spawns": [1]}, {}])
            try:
                vec.reset()
                procs = [process.pid for process in vec.processes]
                self.assertTrue(vec.has_attr("dummy_attr"))
                self.assertFalse(vec.has_attr("definitely_missing_attr"))
                self.assertNotEqual(vec.processes[0].pid, procs[0])
                self.assertEqual(vec.processes[1].pid, procs[1])
            finally:
                vec.close()

    def test_set_attr_worker_death_reapplies(self):
        with tempfile.TemporaryDirectory() as tmp:
            vec = _make_vec(tmp, [{"fault_commands": ["set_attr"],
                                   "fault_type": "exit", "fault_spawns": [1]}, {}])
            try:
                vec.reset()
                vec.set_attr("dummy_setting", "new-value")
                self.assertEqual(vec.get_attr("dummy_setting"),
                                 ["new-value", "new-value"])
                self.assertTrue(any(event["event"] == "set_attr_reapplied"
                                    for event in _events(tmp)))
            finally:
                vec.close()

    def test_env_is_wrapped_worker_death_recovers(self):
        with tempfile.TemporaryDirectory() as tmp:
            vec = _make_vec(tmp, [{"fault_commands": ["env_is_wrapped"],
                                   "fault_type": "exit", "fault_spawns": [1]}, {}])
            try:
                vec.reset()
                procs = [process.pid for process in vec.processes]
                wrapped = vec.env_is_wrapped(GymWrapper)
                self.assertEqual(wrapped, [False, False])
                self.assertNotEqual(vec.processes[0].pid, procs[0])
                self.assertEqual(vec.processes[1].pid, procs[1])
            finally:
                vec.close()

    # ------------------------------------------------------------------ 生命周期 / PID
    def test_worker_death_before_first_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            vec = _make_vec(tmp, [{}, {}])
            try:
                procs = [process.pid for process in vec.processes]
                self.assertIsNone(vec.worker_stats[0]["last_game_pid"])
                vec.processes[0].terminate()
                time.sleep(0.2)
                obs = vec.reset()
                self.assertEqual(obs.shape, (2, 2))
                self.assertNotEqual(vec.processes[0].pid, procs[0])
                self.assertEqual(vec.processes[1].pid, procs[1])
                pids = vec.env_method("game_pid")
                self.assertNotEqual(pids[0], pids[1])
                self.assertIsNotNone(vec.worker_stats[0]["last_game_pid"])
            finally:
                vec.close()

    def test_worker_death_after_reset_before_rollout(self):
        with tempfile.TemporaryDirectory() as tmp:
            vec = _make_vec(tmp, [{}, {}])
            try:
                vec.reset()
                procs = [process.pid for process in vec.processes]
                vec.processes[1].terminate()
                time.sleep(0.2)
                self.assertTrue(is_masking_supported(vec))
                self.assertNotEqual(vec.processes[1].pid, procs[1])
                self.assertEqual(vec.processes[0].pid, procs[0])
                vec.step_async(np.zeros(2, dtype=np.int64))
                vec.step_wait()
            finally:
                vec.close()

    def test_multiple_workers_die_recover_independently(self):
        with tempfile.TemporaryDirectory() as tmp:
            vec = _make_vec(tmp, [{}, {}])
            try:
                vec.reset()
                procs = [process.pid for process in vec.processes]
                pids = vec.env_method("game_pid")
                for process in vec.processes:
                    process.terminate()
                time.sleep(0.2)
                self.assertTrue(vec.has_attr("action_masks"))
                self.assertNotEqual(vec.processes[0].pid, procs[0])
                self.assertNotEqual(vec.processes[1].pid, procs[1])
                new_pids = vec.env_method("game_pid")
                self.assertEqual(len(set(new_pids)), 2)
                self.assertLessEqual(vec.max_concurrent_launches, 2)
                self.assertNotEqual(pids, new_pids)
            finally:
                vec.close()

    def test_close_kills_registered_game_pids_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            killed = []

            def hook(pid):
                killed.append(int(pid))
                return True

            vec = _make_vec(tmp, [{}, {}], kill_pid_hook=hook)
            vec.reset()
            pids = [int(pid) for pid in vec.env_method("game_pid")]
            vec.close()
            self.assertEqual(set(killed), set(pids))
            self.assertNotIn(999999, killed)
            self.assertNotIn(None, killed)

    def test_worker_log_records_game_pid_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            vec = _make_vec(tmp, [{}, {}])
            try:
                vec.reset()
                pids = [int(pid) for pid in vec.env_method("game_pid")]
                records = _worker_log_records(tmp, 0)
                self.assertTrue(records)
                self.assertTrue(any(record.get("game_pid") == pids[0]
                                    for record in records))
                self.assertTrue(any(record.get("event") == "worker_ready"
                                    for record in records))
            finally:
                vec.close()

    def test_structured_worker_traceback_logging(self):
        with tempfile.TemporaryDirectory() as tmp:
            vec = _make_vec(tmp, [{"fault_commands": ["get_attr"],
                                   "fault_attrs": ["dummy_attr"],
                                   "fault_type": "raise", "fault_spawns": [1]}, {}])
            try:
                vec.reset()
                values = vec.get_attr("dummy_attr")
                self.assertEqual(values, ["dummy-value", "dummy-value"])
                records = _worker_log_records(tmp, 0)
                failures = [record for record in records
                            if record.get("event") == "worker_exception"]
                self.assertTrue(failures)
                failure = failures[0]
                for field in ("worker_id", "process_pid", "game_pid", "stage",
                              "exception_type", "exception_message", "traceback",
                              "last_success_ipc", "last_reset", "last_step"):
                    self.assertIn(field, failure)
                self.assertIn("Traceback", failure["traceback"])
            finally:
                vec.close()

    # ------------------------------------------------------------------ 恢复预算 / 数据安全
    def test_recovery_budget_exhausted_no_infinite_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            counter = pathlib.Path(tmp) / "attempt-0.txt"
            spec = {"crash_on_step": 1, "crash_type": "exit", "crash_spawns": [1],
                    "factory_fail_attempts": list(range(2, 50))}
            vec = _make_vec(tmp, [spec, {}], max_restarts_per_worker=2)
            try:
                vec.reset()
                vec.step_async(np.asarray([0, 0]))
                with self.assertRaises(WorkerRecoveryError):
                    vec.step_wait()
                attempts = int(counter.read_text(encoding="utf-8").strip())
                self.assertGreaterEqual(attempts, 3)
                self.assertLessEqual(attempts, 1 + (2 + 1))
            finally:
                vec.close()

    def test_no_fake_transition_on_worker_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            vec = _make_vec(tmp, [{"crash_on_step": 1, "crash_type": "exit",
                                   "crash_spawns": [1]}, {}])
            try:
                vec.reset()
                vec.step_async(np.asarray([0, 0]))
                with self.assertRaises(FeedbackBatchInterrupted) as context:
                    vec.step_wait()
                interruption = context.exception
                np.testing.assert_allclose(interruption.observations[1], [1.0, 0.01])
                np.testing.assert_allclose(interruption.observations[0], [0.0, 0.0])
                self.assertTrue(bool(interruption.dones[0]))
                self.assertFalse(bool(interruption.dones[1]))
                self.assertFalse(hasattr(interruption, "rewards"))
                self.assertIs(interruption.infos[0]["feedback_valid"], False)
            finally:
                vec.close()

    def test_startup_factory_failure_retries_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec = {"factory_fail_attempts": [1], "fault_commands": [],
                    "crash_on_step": None}
            vec = _make_vec(tmp, [spec, {}])
            try:
                self.assertEqual(len(vec.processes), 2)
                obs = vec.reset()
                self.assertEqual(obs.shape, (2, 2))
                self.assertTrue(any(event["event"] == "worker_start_failed"
                                    for event in _events(tmp)))
            finally:
                vec.close()

    def test_maskable_ppo_integration_and_interruption_resume(self):
        from sb3_contrib import MaskablePPO

        with tempfile.TemporaryDirectory() as tmp:
            vec = _make_vec(tmp, [{}, {"crash_on_step": 3, "crash_type": "exit",
                                        "crash_spawns": [1]}])
            model = None
            try:
                model = MaskablePPO("MlpPolicy", vec, n_steps=8, batch_size=8,
                                    n_epochs=1, verbose=0, device="cpu", seed=0)
                interrupted = False
                learned = 0
                for _ in range(8):
                    try:
                        model.learn(total_timesteps=8, reset_num_timesteps=False)
                        learned += 8
                    except FeedbackBatchInterrupted as interruption:
                        interrupted = True
                        resume_after_feedback(model, interruption)
                        continue
                    if learned > 0:
                        break
                self.assertTrue(interrupted)
                self.assertGreaterEqual(model.num_timesteps, 8)
                self.assertGreaterEqual(vec.stats_summary()["restarts"], 1)
            finally:
                vec.close()

    def test_train_shared_feature_flag_wiring(self):
        from types import SimpleNamespace

        import train_shared as ts

        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)

            def build_args(recovering):
                return SimpleNamespace(
                    recovering_vec=recovering,
                    recovering_max_launches=2,
                    recovering_max_restarts=2,
                    recovering_jitter_seconds=(0.0, 0.0),
                    episode_seconds=60.0,
                )

            specs = [{}, {}]
            env_fns = []
            for index, spec in enumerate(specs):
                spec = dict(spec)
                spec.setdefault("_attempt_counter_file",
                                str(tmp / ("attempt-%d.txt" % index)))
                env_fns.append(partial(make_dummy_env, index, spec, None))
            old_env = ts._build_vector_env(env_fns, build_args(False), tmp)
            self.assertNotIsInstance(old_env, RecoveringSubprocVecEnv)
            old_env.reset()
            old_env.close()
            new_env = ts._build_vector_env(env_fns, build_args(True),
                                           tmp / "recovering")
            self.assertIsInstance(new_env, RecoveringSubprocVecEnv)
            new_env.reset()
            new_env.step_async(np.asarray([0, 0]))
            new_env.step_wait()
            new_env.close()

    def test_env_recreation_for_checkpoint_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            vec = _make_vec(tmp, [{}, {}])
            vec.reset()
            vec.step_async(np.asarray([0, 0]))
            vec.step_wait()
            vec.close()
            vec2 = _make_vec(tmp, [{}, {}])
            try:
                obs = vec2.reset()
                self.assertEqual(obs.shape, (2, 2))
                vec2.step_async(np.asarray([0, 0]))
                vec2.step_wait()
            finally:
                vec2.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
