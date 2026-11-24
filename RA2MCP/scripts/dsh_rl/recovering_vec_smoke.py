"""P0-5 真实游戏接口验证（最多 2 实例，不运行 PPO，不训练）。

覆盖：
1. train_shared 同款 Monitor(RA2CombatEnv) 包装 + RecoveringSubprocVecEnv；
2. P0-4 失败路径：is_masking_supported(vec) → has_attr("action_masks") 以及
   get_attr("action_masks")（旧实现会 BrokenPipe/不可 pickle）；
3. 人为终止 worker0 → 仅 worker0 恢复，worker1 保持运行；
4. 人为终止 worker1 的游戏进程 → env 错误被捕获/恢复，worker traceback 落盘；
5. PID 生命周期记录、无重复游戏 PID、close 后无残留游戏；
6. 全程真实执行 MaskablePPO 所需接口，但不创建/训练 PPO 模型。
"""
import argparse
import json
import pathlib
import subprocess
import sys
import time
import traceback

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import os
os.environ.setdefault("DSH_COMBAT_ENV", "1")

import numpy as np  # noqa: E402
from stable_baselines3.common.monitor import Monitor  # noqa: E402
from sb3_contrib.common.maskable.utils import (  # noqa: E402
    get_action_masks,
    is_masking_supported,
)

from dsh_rl.combat_env import RA2CombatEnv  # noqa: E402
from dsh_rl import port_config  # noqa: E402
from dsh_rl.recovering_vec import (  # noqa: E402
    CallableAttributeRef,
    RecoveringSubprocVecEnv,
    WorkerRecoveryError,
)
from dsh_rl.safe_vec import FeedbackBatchInterrupted, resume_after_feedback  # noqa: E402


class Checks:
    def __init__(self):
        self.rows = []

    def check(self, name, ok, detail=None):
        self.rows.append({"name": name, "ok": bool(ok), "detail": detail})
        print(("[PASS] " if ok else "[FAIL] ") + name + (
            (" | " + json.dumps(detail, ensure_ascii=False)) if detail is not None else ""),
            flush=True)
        return bool(ok)

    def summary(self):
        return {"checks": self.rows,
                "total": len(self.rows),
                "passed": sum(1 for row in self.rows if row["ok"]),
                "failed": sum(1 for row in self.rows if not row["ok"])}


def kill_pid(pid):
    try:
        subprocess.run(["taskkill", "/PID", str(int(pid)), "/T", "/F"],
                       capture_output=True, timeout=15)
        return True
    except BaseException:  # noqa: BLE001
        return False


def pid_alive(pid):
    try:
        result = subprocess.run(["tasklist", "/FI", "PID eq %d" % int(pid)],
                                capture_output=True, text=True, timeout=10)
        return str(int(pid)) in result.stdout
    except BaseException:  # noqa: BLE001
        return False


def game_processes():
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq gamemd-spawn-ra2yrcpp.exe"],
            capture_output=True, text=True, timeout=15)
        return [line for line in result.stdout.splitlines()
                if "gamemd-spawn-ra2yrcpp" in line]
    except BaseException:  # noqa: BLE001
        return []


def read_log_records(stats_dir, index):
    path = pathlib.Path(stats_dir) / ("worker-%d.log" % index)
    records = []
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip():
                try:
                    records.append(json.loads(line))
                except BaseException:  # noqa: BLE001
                    pass
    return records


def read_stats(stats_dir):
    path = pathlib.Path(stats_dir) / "worker-stats.jsonl"
    events = []
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip():
                try:
                    events.append(json.loads(line))
                except BaseException:  # noqa: BLE001
                    pass
    return events


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "reports/rl/p0-6-port-validation"))
    parser.add_argument("--episode-seconds", type=float, default=120.0)
    parser.add_argument("--base-port", type=int, default=port_config.DEFAULT_BASE_PORT,
                        help="P0-6：Worker bridge 端口基址（Worker i 使用 base+i）")
    args = parser.parse_args()

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stats_dir = out / "stats"
    # P0-6：真实游戏副本的 ra2yrcpp.json 必须使用同一 base_port（游戏启动时读取）。
    game_dirs = []
    for index in range(2):
        game_dir = ROOT / ("test-game" if index == 0 else "test-game-1")
        port_config.configure_game_port(game_dir, args.base_port + index)
        game_dirs.append(game_dir)
    checks = Checks()
    result = {"started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "out": str(out), "checks": []}
    report = {"result": None, "checks": checks.rows, "stats": [], "events": [],
              "worker_logs": {}}
    vec = None
    try:
        env_fns = []
        for index in range(2):
            folder = out / ("workers/%d" % index)
            folder.mkdir(parents=True, exist_ok=True)

            def make(index=index):
                env = RA2CombatEnv(
                    folder / "episodes",
                    max_seconds=args.episode_seconds,
                    decision_seconds=0.5,
                    game_dir=ROOT / ("test-game" if index == 0 else "test-game-1"),
                    port=args.base_port + index,
                    instance=index,
                    kill_stale=False,
                    render_off=True,
                    launch_delay_seconds=1.0 if index else 0.0,
                )
                env.transport_feedback_failures = True
                return Monitor(env, str(folder / "monitor.csv"))

            env_fns.append(make)

        vec = RecoveringSubprocVecEnv(
            env_fns, start_method="spawn", max_launches=2,
            launch_jitter=(1.0, 2.5), recv_timeout_s=180.0,
            startup_timeout_s=180.0, max_restarts_per_worker=3,
            stats_dir=stats_dir, seed=11, verbose=1)

        observations = vec.reset()
        checks.check("reset 返回 2 个真实观测", observations.shape[0] == 2,
                     {"shape": list(observations.shape)})
        worker_pids = [process.pid for process in vec.processes]
        game_pids = [int(pid) for pid in vec.env_method("game_pid")]
        checks.check("2 个 Worker 进程已登记", len(set(worker_pids)) == 2,
                     {"worker_pids": worker_pids})
        checks.check("2 个真实 game PID 唯一个登记", len(set(game_pids)) == 2,
                     {"game_pids": game_pids})
        checks.check("运行游戏实例数 <= 2", len(game_processes()) <= 2,
                     {"games": len(game_processes())})
        import socket as _socket
        service_ports_ok = []
        for index in range(2):
            try:
                sock = _socket.create_connection(("127.0.0.1", args.base_port + index),
                                                 timeout=3.0)
                sock.close()
                service_ports_ok.append(True)
            except BaseException:  # noqa: BLE001
                service_ports_ok.append(False)
        checks.check("两个游戏服务端口均正常监听/可连接", all(service_ports_ok),
                     {"base_port": args.base_port,
                      "ports": [args.base_port + index for index in range(2)],
                      "connect_ok": service_ports_ok})

        # 覆盖 P0-4 失败路径（Monitor 包装 + MaskablePPO 启动接口）
        checks.check("P0-4 路径 is_masking_supported(vec)", is_masking_supported(vec))
        checks.check("P0-4 路径 has_attr('action_masks')",
                     vec.has_attr("action_masks"))
        masks = get_action_masks(vec)
        checks.check("get_action_masks 返回真实掩码", masks.shape == (2, 301),
                     {"shape": list(masks.shape), "dtype": str(masks.dtype)})
        refs = vec.get_attr("action_masks")
        checks.check("get_attr('action_masks') 不再 BrokenPipe（callable 引用）",
                     all(isinstance(ref, CallableAttributeRef) for ref in refs),
                     {"types": [type(ref).__name__ for ref in refs]})
        checks.check("get_attr('render_mode') 正常", vec.get_attr("render_mode") == [None, None])

        # 1) 人为终止 worker0 → 仅 worker0 恢复
        old_worker1_pid = worker_pids[1]
        old_pids = [int(pid) for pid in vec.env_method("game_pid")]
        kill_pid(worker_pids[0])
        time.sleep(2.0)
        recovered = False
        for attempt in range(3):
            try:
                checks.check("worker0 死亡后 is_masking_supported 恢复", is_masking_supported(vec))
                recovered = True
                break
            except BaseException as error:  # noqa: BLE001
                print("recover attempt %d error: %r" % (attempt, error), flush=True)
                time.sleep(2.0)
        checks.check("worker0 独立恢复成功", recovered)
        new_worker_pids = [process.pid for process in vec.processes]
        checks.check("worker1 未被重启", new_worker_pids[1] == old_worker1_pid,
                     {"old": old_worker1_pid, "new": new_worker_pids[1]})
        new_pids = [int(pid) for pid in vec.env_method("game_pid")]
        checks.check("恢复后 2 个 game PID 唯一且 worker0 变化",
                     len(set(new_pids)) == 2 and new_pids[0] != old_pids[0],
                     {"old": old_pids, "new": new_pids})
        masks_after = get_action_masks(vec)
        checks.check("恢复后 action_masks 仍为真实值", masks_after.shape == (2, 301))

        # 2) 人为终止 worker1 游戏 → env 内部重连/恢复（不伪造 transition）
        old_game_pids2 = [int(pid) for pid in vec.env_method("game_pid")]
        kill_pid(new_pids[1])
        time.sleep(1.5)
        interrupted = False
        try:
            vec.step_async(np.asarray([0, 0], dtype=np.int64))
            vec.step_wait()
        except FeedbackBatchInterrupted as interruption:
            interrupted = True
            checks.check("游戏死亡时抛 FeedbackBatchInterrupted（无虚假 transition）",
                         interruption.observations.shape[0] == 2,
                         {"dones": [bool(value) for value in interruption.dones]})
        except WorkerRecoveryError as error:
            checks.check("游戏死亡时恢复器有限失败（WorkerRecoveryError）", False,
                         {"error": repr(error)})
        except BaseException as error:  # noqa: BLE001
            checks.check("游戏死亡处理未抛未知异常", False,
                         {"error": repr(error), "traceback": traceback.format_exc()})
        game_pids_after_step = [int(pid) for pid in vec.env_method("game_pid")]
        clean_recovery = interrupted or game_pids_after_step[1] != old_game_pids2[1]
        checks.check("游戏死亡被检测并恢复（FeedbackBatchInterrupted 或 env 内部重连）",
                     clean_recovery,
                     {"interrupted": interrupted,
                      "old_pid": old_game_pids2[1],
                      "new_pid": game_pids_after_step[1]})

        # 3) 真实游戏 worker 主动触发结构化 traceback + 独立恢复
        worker0_before_probe = vec.processes[0].pid
        worker1_before_probe = vec.processes[1].pid
        probe_error = None
        try:
            vec.env_method("p0_5_nonexistent_probe", indices=[1])
        except WorkerRecoveryError as error:
            probe_error = error
        except BaseException as error:  # noqa: BLE001
            probe_error = error
        checks.check("worker env 错误被有限恢复（WorkerRecoveryError）",
                     isinstance(probe_error, WorkerRecoveryError),
                     {"error": repr(probe_error)[:300]})
        checks.check("仅 worker1 被重启（probe 后 worker0 不变）",
                     vec.processes[0].pid == worker0_before_probe,
                     {"worker0_before": worker0_before_probe,
                      "worker0_after": vec.processes[0].pid})
        checks.check("worker1 已在 probe 后重启",
                     vec.processes[1].pid != worker1_before_probe,
                     {"worker1_before": worker1_before_probe,
                      "worker1_after": vec.processes[1].pid})
        worker1_log = read_log_records(stats_dir, 1)
        exceptions = [record for record in worker1_log
                      if record.get("event") == "worker_exception"]
        traceback_ok = bool(exceptions) and "Traceback" in exceptions[0].get("traceback", "")
        checks.check("真实游戏 worker traceback 已独立落盘", traceback_ok,
                     {"exception_type": exceptions[0].get("exception_type")
                      if exceptions else None,
                      "stage": exceptions[0].get("stage") if exceptions else None,
                      "game_pid": exceptions[0].get("game_pid") if exceptions else None})
        # worker1 恢复后仍可执行真实 action mask
        try:
            masks_final = get_action_masks(vec)
            checks.check("游戏恢复后 action_masks 仍可用", masks_final.shape == (2, 301))
        except BaseException as error:  # noqa: BLE001
            checks.check("游戏恢复后 action_masks 仍可用", False, {"error": repr(error)})

        final_worker_pids = [process.pid for process in vec.processes]
        final_pids = [int(pid) for pid in vec.env_method("game_pid")]
        checks.check("最终 2 个 worker 存活", len(set(final_worker_pids)) == 2)
        checks.check("最终 2 个 game PID 唯一", len(set(final_pids)) == 2,
                     {"game_pids": final_pids})
        events = read_stats(stats_dir)
        check_events = {name: sum(1 for event in events if event.get("event") == name)
                        for name in ("worker_death", "worker_recovered",
                                     "worker_old_game_pid_cleanup", "worker_started")}
        checks.check("worker-stats 记录死亡/恢复事件",
                     check_events["worker_death"] >= 1
                     and check_events["worker_recovered"] >= 1,
                     check_events)
        checks.check("worker 退出码/死亡阶段已记录",
                     any(event.get("event") == "worker_death"
                         and ("exit_code" in event) for event in events))

        # 3) close 后无残留
        close_game_pids = [int(pid) for pid in vec.env_method("game_pid")]
        vec.close()
        vec = None
        time.sleep(2.0)
        checks.check("close 后无残留游戏进程", len(game_processes()) == 0,
                     {"games": len(game_processes())})
        checks.check("close 后 game PID 均已退出",
                     all(not pid_alive(pid) for pid in close_game_pids),
                     {"pids": close_game_pids})
        checks.check("close 后无残留 worker 进程",
                     all(not pid_alive(pid) for pid in final_worker_pids
                         if pid), {"worker_pids": final_worker_pids})

        # 汇总
        stats_events = read_stats(stats_dir)
        report["stats"] = stats_events
        report["events"] = [event for event in stats_events
                            if event.get("event") in
                            ("worker_death", "worker_recovered", "worker_started",
                             "worker_old_game_pid_cleanup")]
        for index in (0, 1):
            report["worker_logs"]["worker-%d" % index] = read_log_records(
                stats_dir, index)
        result["checks"] = checks.rows
        result["summary"] = checks.summary()
        result["worker_pids"] = {"initial": worker_pids, "final": final_worker_pids}
        result["game_pids"] = {"initial": game_pids, "after_worker_recovery": new_pids,
                               "final": final_pids, "closed": close_game_pids}
        result["finish_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        result["success"] = checks.summary()["failed"] == 0
        report["result"] = result
        (out / "real-game-validation.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        (out / "real-game-validation-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(checks.summary(), ensure_ascii=False))
        return 0 if result["success"] else 1
    except BaseException as error:  # noqa: BLE001
        traceback.print_exc()
        checks.check("验证过程未抛未处理异常", False, {"error": repr(error)})
        result["checks"] = checks.rows
        result["summary"] = checks.summary()
        result["success"] = False
        (out / "real-game-validation.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return 1
    finally:
        if vec is not None:
            try:
                vec.close()
            except BaseException:  # noqa: BLE001
                pass
        time.sleep(0.5)


if __name__ == "__main__":
    sys.exit(main())
