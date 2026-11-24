"""P0-3：从真实训练日志定位 Worker 级联崩溃（只读解析，不启动游戏）。

用法：
  python scripts/dsh_rl/worker_cascade_diagnose.py \
      --log reports/dsh-learning/experiment-a/auto-train-20261031-003750.log \
      --log reports/dsh-learning/experiment-a/auto-train-20261031-070006.log \
      --log reports/dsh-learning/experiment-a/auto-train-20261031-015140.log \
      --out reports/autonomous_learning/p0_worker_cascade_diagnosis.json

检测模式：
- SIMULTANEOUS_RELAUNCH_BURST：短窗口内 >=4 个 PPO launched（全量重建/同时重开）
- FULL_VEC_REBUILD_ON_SINGLE_WORKER_FAILURE：单 worker 失败（relaunch fail / IPC EOF）
  后紧跟全量重启
- RENDER_OFF_WINDOW_RACE：relaunch 失败原因含 RenderOffError / no game window
- RECOVERY_ABORT：learner IPC failure 次数超过恢复上限
"""
import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]

TS = re.compile(r"^\[(\d{2}):(\d{2}):(\d{2})\]\s?(.*)$")
LAUNCH = re.compile(r"PPO launched pid=(\d+) episode=([\w.-]+)")
RENDER_OFF = re.compile(r"render-off enabled: pid=(\d+).*workers\\(\d+)\\render-off")
RELAUNCH_FAIL = re.compile(
    r"real game failed to relaunch after (\d+) attempts: (\w+)\('?([^')]*)'?\)?")
IPC_FAIL = re.compile(r"learner IPC failure (\d+): (\w+)\(?([^)]*)\)?;?")
WATCHDOG = re.compile(r"render-off watchdog: (.+)")
FEEDBACK_UNAVAILABLE = re.compile(r"feedback unavailable: excluded (\d+) samples")
GAME_EXITED = re.compile(r"process already exited rc=(\d+)")


def _seconds(hh, mm, ss):
    return int(hh) * 3600 + int(mm) * 60 + int(ss)


def _read_log_lines(path):
    raw = path.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = raw.decode("utf-16", errors="replace")
    else:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("utf-16", errors="replace")
    return text.splitlines()


def analyze_log(path):
    path = pathlib.Path(path)
    launches = []
    relaunch_failures = []
    ipc_failures = []
    watchdog_stalls = []
    feedback_events = []
    game_exits = 0
    worker_by_pid = {}
    lines = _read_log_lines(path)
    current_second = 0
    for line in lines:
        match = TS.match(line)
        if match:
            current_second = _seconds(*match.groups()[:3])
            body = match.group(4)
        else:
            # 真实日志中 Python 打印的异常/IPC 行没有时间戳前缀，沿用上一条时间。
            body = line.strip()
            if not body:
                continue
        second = current_second
        if LAUNCH.search(body):
            launch = LAUNCH.search(body)
            launches.append({"second": second, "pid": int(launch.group(1)),
                             "episode": launch.group(2)})
        elif RENDER_OFF.search(body):
            found = RENDER_OFF.search(body)
            worker_by_pid[int(found.group(1))] = int(found.group(2))
        elif RELAUNCH_FAIL.search(body):
            found = RELAUNCH_FAIL.search(body)
            relaunch_failures.append({"second": second,
                                      "attempts": int(found.group(1)),
                                      "error_type": found.group(2),
                                      "message": found.group(3),
                                      "line": body})
        elif IPC_FAIL.search(body):
            found = IPC_FAIL.search(body)
            ipc_failures.append({"second": second,
                                 "index": int(found.group(1)),
                                 "error_type": found.group(2),
                                 "message": found.group(3)})
        elif WATCHDOG.search(body):
            watchdog_stalls.append({"second": second, "detail": WATCHDOG.search(body).group(1)})
        elif FEEDBACK_UNAVAILABLE.search(body):
            found = FEEDBACK_UNAVAILABLE.search(body)
            feedback_events.append({"second": second, "excluded": int(found.group(1))})
        elif GAME_EXITED.search(body):
            game_exits += 1

    # 级联重启检测：只统计“失败事件之后”的启动突变（启动期 6 个进程同时拉起是正常现象）
    failure_seconds = sorted([item["second"] for item in relaunch_failures]
                             + [item["second"] for item in ipc_failures])
    first_failure = failure_seconds[0] if failure_seconds else None
    relaunch_launches = [launch for launch in launches
                         if first_failure is not None
                         and -10 <= launch["second"] - first_failure <= 60]
    max_burst, max_burst_second = 0, None
    if relaunch_launches:
        start = 0
        for end in range(len(relaunch_launches)):
            while relaunch_launches[end]["second"] - relaunch_launches[start]["second"] > 10:
                start += 1
            if end - start + 1 > max_burst:
                max_burst = end - start + 1
                max_burst_second = relaunch_launches[end]["second"]
    # 启动期同时拉起（信息项，不视为故障）
    startup_burst = 0
    if launches:
        start = 0
        for end in range(len(launches)):
            while launches[end]["second"] - launches[start]["second"] > 10:
                start += 1
            startup_burst = max(startup_burst, end - start + 1)
    # 单次失败后 30 秒内的全量重启次数
    rebuild_after_failures = []
    for failure in ipc_failures:
        follow = [launch for launch in launches
                  if 0 <= launch["second"] - failure["second"] <= 30]
        rebuild_after_failures.append({"ipc_failure": failure,
                                       "launches_within_30s": len(follow)})

    error_types = {}
    for failure in relaunch_failures:
        error_types[failure["error_type"]] = error_types.get(failure["error_type"], 0) + 1

    findings = []
    if max_burst >= 4:
        findings.append({
            "id": "SIMULTANEOUS_RELAUNCH_BURST",
            "evidence": f"max {max_burst} PPO launches within 10s "
                        f"(at t={max_burst_second}s)",
        })
    if ipc_failures and any(item["launches_within_30s"] >= 4
                            for item in rebuild_after_failures):
        findings.append({
            "id": "FULL_VEC_REBUILD_ON_SINGLE_WORKER_FAILURE",
            "evidence": "IPC failure followed by >=4 simultaneous game launches",
        })
    if any(t in ("RenderOffError",) for t in error_types):
        findings.append({
            "id": "RENDER_OFF_WINDOW_RACE",
            "evidence": f"relaunch error types: {error_types}",
        })
    if len(ipc_failures) > 5:
        findings.append({
            "id": "RECOVERY_ABORT",
            "evidence": f"{len(ipc_failures)} IPC failures (train_shared aborts at >5)",
        })
    if watchdog_stalls:
        findings.append({
            "id": "RENDER_OFF_FRAME_STALL",
            "evidence": f"{len(watchdog_stalls)} render-off watchdog stall events: "
                        f"{watchdog_stalls[0]['detail'][:120]}",
        })
    return {
        "log": str(path),
        "lines": len(lines),
        "ppo_launches": len(launches),
        "relaunch_failures": len(relaunch_failures),
        "relaunch_error_types": error_types,
        "ipc_failures": len(ipc_failures),
        "game_exit_events": game_exits,
        "watchdog_stalls": len(watchdog_stalls),
        "feedback_unavailable_events": len(feedback_events),
        "max_launches_in_10s_window": max_burst,
        "max_launches_window_start_s": max_burst_second,
        "startup_launch_burst": startup_burst,
        "first_failure_s": first_failure,
        "rebuild_after_failure": rebuild_after_failures,
        "worker_index_by_pid_sample": dict(list(worker_by_pid.items())[:10]),
        "relaunch_failure_details": relaunch_failures[:10],
        "findings": findings,
        "status": "FAILED_CASCADE" if findings and (
            "RECOVERY_ABORT" in [f["id"] for f in findings]
            or "FULL_VEC_REBUILD_ON_SINGLE_WORKER_FAILURE" in [f["id"] for f in findings]
        ) else ("HEALTHY_OR_SINGLE_INCIDENT" if not findings else "DEGRADED"),
        "no_game_launched": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", action="append", required=True)
    parser.add_argument("--out", default="reports/autonomous_learning/p0_worker_cascade_diagnosis.json")
    args = parser.parse_args()
    report = {"schema": "p0-worker-cascade-diagnosis-v1",
              "runs": [analyze_log(log) for log in args.log]}
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for run in report["runs"]:
        print(json.dumps({key: run[key] for key in (
            "log", "ppo_launches", "relaunch_failures", "relaunch_error_types",
            "ipc_failures", "max_launches_in_10s_window", "findings", "status")},
            ensure_ascii=False, indent=2))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
