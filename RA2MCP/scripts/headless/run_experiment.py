"""Headless-window feasibility experiment runner (phases 1-3).

Launches one or two isolated real RA2 games, drives a deterministic rule policy
at a fixed decision cadence, and toggles the game window with
ShowWindow(SW_HIDE)/ShowWindow(SW_SHOW).  Records per-step JSONL evidence and a
machine-readable result JSON per run.

Usage examples:
  # phase 1 + 2 single instance: visible baseline -> hidden -> restored
  .venv\\Scripts\\python.exe scripts\\headless\\run_experiment.py \
      --run-id h1-aba --instances 1 --plan "settle:5,visible:90,hidden:120,show:45"

  # phase 2: a full episode with the window visible (run until game over)
  .venv\\Scripts\\python.exe scripts\\headless\\run_experiment.py \
      --run-id h2-visible --instances 1 --plan "settle:5,visible:0" --max-phase-seconds 420

  # phase 3: two hidden instances in parallel
  .venv\\Scripts\\python.exe scripts\\headless\\run_experiment.py \
      --run-id h3-dual --instances 2 --plan "settle:5,hidden:0" --max-phase-seconds 420 \
      --kill-one-test
"""
import argparse
import asyncio
import json
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "vendor" / "pyra2yr"))
sys.path.insert(0, str(SCRIPTS / "headless"))

import winapi  # noqa: E402
from session import GameSession  # noqa: E402

HEADLESS_ROOT = ROOT / "reports" / "headless"


def shell_exe():
    """Windows PowerShell is always present; prefer pwsh when installed."""
    import shutil
    for name in ("pwsh", "powershell"):
        path = shutil.which(name)
        if path:
            return path
    fallback = pathlib.Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
    return str(fallback)


def parse_plan(text):
    phases = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        name, _, secs = part.partition(":")
        name = name.strip().lower()
        if name not in ("settle", "visible", "hidden", "show"):
            raise ValueError(f"unknown phase {name!r}")
        phases.append({"name": name, "seconds": float(secs or 0)})
    if not phases:
        raise ValueError("empty plan")
    return phases


def phase_window_mode(name):
    return "hidden" if name == "hidden" else "visible"


def write_phase_marker(csv_path, phase):
    marker = pathlib.Path(str(csv_path) + ".phase")
    try:
        marker.write_text(phase, encoding="ascii")
    except OSError:
        pass


def capture_window_region(session, out_png):
    if session.window is None:
        return None
    rect = winapi.window_rect(session.window["hwnd"])
    if not rect:
        return None
    try:
        subprocess.run([shell_exe(), "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", str(SCRIPTS / "headless" / "capture.ps1"),
                        "-Left", str(rect[0]), "-Top", str(rect[1]),
                        "-Right", str(rect[2]), "-Bottom", str(rect[3]),
                        "-OutPng", str(out_png)],
                       capture_output=True, text=True, timeout=30)
        return str(out_png) if pathlib.Path(out_png).exists() else None
    except Exception:  # noqa: BLE001
        return None


def summarize_phase(phase, sessions, records_by_session, wall_start_ms, wall_end_ms,
                    duration_s=None):
    """Aggregate per-session step records into comparable phase metrics."""
    duration_s = duration_s if duration_s else (wall_end_ms - wall_start_ms) / 1000.0
    out = {"name": phase["name"], "window": phase_window_mode(phase["name"]),
           "planned_seconds": phase["seconds"],
           "wall_start_ms": wall_start_ms, "wall_end_ms": wall_end_ms,
           "wall_seconds": round(duration_s, 3),
           "instances": {}}
    total_steps = 0
    for sess in sessions:
        recs = records_by_session.get(sess.index, [])
        step_recs = [r for r in recs if r.get("type") == "step"]
        benchmarks = [r for r in recs if r.get("type") == "rpc_benchmark"]
        ok = [r for r in step_recs if r.get("read_ok")]
        read_ms = sorted(r["read_ms"] for r in ok)
        frames = [r["frame"] for r in ok]
        ts = [r["t"] for r in ok]
        fps = None
        if len(frames) >= 2 and ts[-1] > ts[0]:
            fps = round((frames[-1] - frames[0]) / (ts[-1] - ts[0]), 3)
        step_window_s = (ts[-1] - ts[0]) if len(ts) >= 2 else None
        def pct(values, p):
            return round(values[min(len(values) - 1, int(len(values) * p))], 3) if values else None
        agg = {
            "steps": len(step_recs), "read_ok": len(ok),
            "read_fail": len(step_recs) - len(ok),
            "achieved_steps_per_s": round(len(step_recs) / max(0.001, duration_s), 3),
            "steps_per_s_step_window": (round((len(ok) - 1) / step_window_s, 3)
                                        if step_window_s and len(ok) >= 2 else None),
            "step_window_s": round(step_window_s, 3) if step_window_s else None,
            "read_latency_ms_p50": pct(read_ms, 0.5),
            "read_latency_ms_p95": pct(read_ms, 0.95),
            "frame_first": frames[0] if frames else None,
            "frame_last": frames[-1] if frames else None,
            "game_fps": fps,
            "orders_ok": sum(r.get("orders_ok", 0) for r in ok),
            "invalid_orders": sum(r.get("invalid", 0) for r in ok),
            "actions": sum(len(r.get("actions") or []) for r in ok),
            "damage_to_enemy": sum((r.get("delta") or {}).get("damage_to_enemy", 0) for r in ok),
            "damage_to_own": sum((r.get("delta") or {}).get("damage_to_own", 0) for r in ok),
            "enemy_destroyed": sum((r.get("delta") or {}).get("enemy_destroyed", 0) for r in ok),
            "own_lost": sum((r.get("delta") or {}).get("own_lost", 0) for r in ok),
            "moved_objects_samples": sum((r.get("delta") or {}).get("moved_objects", 0) for r in ok),
            "moved_cells": round(sum((r.get("delta") or {}).get("moved_cells", 0) for r in ok), 2),
            "stall_steps": sum(1 for r in ok if r.get("stall")),
            "window_visible_samples": sum(1 for r in ok if r.get("window_visible")),
            "window_hidden_samples": sum(1 for r in ok if r.get("window") == "hidden"),
            "rehide_count": sess.rehide_count,
            "own_placed_buildings_first": ok[0]["own"]["placed_buildings"] if ok else None,
            "own_placed_buildings_last": ok[-1]["own"]["placed_buildings"] if ok else None,
            "own_placed_buildings_max": max((r["own"]["placed_buildings"] for r in ok), default=None),
            "own_units_first": ok[0]["own"]["units"] if ok else None,
            "own_units_last": ok[-1]["own"]["units"] if ok else None,
            "enemy_buildings_first": ok[0]["enemy"]["buildings"] if ok else None,
            "enemy_buildings_last": ok[-1]["enemy"]["buildings"] if ok else None,
            "enemy_buildings_max": max((r["enemy"]["buildings"] for r in ok), default=None),
            "enemy_units_first": ok[0]["enemy"]["units"] if ok else None,
            "enemy_units_last": ok[-1]["enemy"]["units"] if ok else None,
            "enemy_units_max": max((r["enemy"]["units"] for r in ok), default=None),
            "outcome": ok[-1]["outcome"] if ok else sess.outcome,
            "reward_total_phase": round(sum(r.get("reward_total_step", 0) for r in ok), 6),
            "reward_parts_phase": _sum_reward_parts(ok),
            "rpc_benchmarks": benchmarks,
        }
        total_steps += len(recs)
        out["instances"][str(sess.index)] = agg
    out["total_steps"] = total_steps
    out["total_achieved_steps_per_s"] = round(total_steps / max(0.001, duration_s), 3)
    return out


def _sum_reward_parts(records):
    total = {}
    for r in records:
        for key, value in (r.get("reward") or {}).items():
            total[key] = round(total.get(key, 0.0) + value, 6)
    return total


async def run_plan(sessions, plan, max_phase_seconds, out_dir, decision_seconds,
                   rpc_bench_seconds, monitor_csv):
    all_records = {s.index: [] for s in sessions}
    phase_summaries = []
    for phase in plan:
        duration = phase["seconds"]
        if duration <= 0:
            if max_phase_seconds <= 0:
                raise SystemExit("phase with seconds=0 needs --max-phase-seconds")
            duration = max_phase_seconds
        mode = phase_window_mode(phase["name"])
        phase_records = {s.index: [] for s in sessions}
        # apply window state to every session before the phase starts
        for sess in sessions:
            sess.ensure_window(mode)
        write_phase_marker(monitor_csv, phase["name"])
        start_ms = int(time.time() * 1000)
        t_start = time.perf_counter()
        print(f"=== phase {phase['name']} window={mode} duration={duration}s ===", flush=True)

        async def loop(sess, phase=phase, duration=duration):
            idx = 0
            # Bridge read-throughput micro benchmark first, while the phase is in
            # steady state (running it after a defeat would measure the exit path).
            if rpc_bench_seconds > 0 and sess.client is not None and \
                    sess.proc is not None and sess.proc.poll() is None:
                bench = await sess.rpc_benchmark(rpc_bench_seconds)
                bench["type"] = "rpc_benchmark"
                bench["phase"] = phase["name"]
                bench["instance"] = sess.index
                phase_records[sess.index].append(bench)
                all_records[sess.index].append(bench)
                print(f"rpc benchmark {sess.label} {phase['name']}: {bench}", flush=True)
            end = time.perf_counter() + duration
            while time.perf_counter() < end:
                idx += 1
                rec = await sess.decision_step(phase, idx)
                rec["phase_wall_ms"] = int(time.time() * 1000)
                phase_records[sess.index].append(rec)
                all_records[sess.index].append(rec)
                if rec.get("fatal"):
                    print(f"!! {sess.label}: {rec['fatal']}", flush=True)
                    break
                if rec.get("outcome") in ("win", "defeat", "game_end", "crash"):
                    break
                if sess.proc is not None and sess.proc.poll() is not None:
                    break
                # pace at decision_seconds between decision-step starts
                step_cost_ms = rec.get("cycle_ms", 0)
                wait = decision_seconds - step_cost_ms / 1000.0
                if wait > 0:
                    await asyncio.sleep(wait)

        results = await asyncio.gather(*[loop(s) for s in sessions],
                                       return_exceptions=True)
        for sess, res in zip(sessions, results):
            if isinstance(res, Exception):
                print(f"!! phase loop {sess.label} raised {type(res).__name__}: {res}")
                sess.crash_detail = f"phase loop exception: {type(res).__name__}: {res}"
        end_ms = int(time.time() * 1000)
        wall_duration = time.perf_counter() - t_start
        summary = summarize_phase(phase, sessions, phase_records,
                                  start_ms, end_ms, duration_s=wall_duration)
        summary["wall_seconds"] = round(wall_duration, 3)
        phase_summaries.append(summary)
        for key, agg in summary["instances"].items():
            print(f"  [{key}] steps={agg['steps']} read_ok={agg['read_ok']}/"
                  f"{agg['read_fail']} fps={agg['game_fps']} "
                  f"bld={agg['own_placed_buildings_first']}->{agg['own_placed_buildings_last']} "
                  f"units={agg['own_units_first']}->{agg['own_units_last']} "
                  f"dmg_e={agg['damage_to_enemy']} dmg_own={agg['damage_to_own']} "
                  f"moved={agg['moved_cells']} orders={agg['orders_ok']} "
                  f"invalid={agg['invalid_orders']} stalls={agg['stall_steps']} "
                  f"visible_samples={agg['window_visible_samples']}", flush=True)
        # stop the plan early when every game ended
        if all(s.outcome in ("win", "defeat", "game_end", "crash") for s in sessions):
            print("all games ended; stopping plan early", flush=True)
            break
    return all_records, phase_summaries


async def kill_one_test(sessions):
    """Phase 3: killing instance 0 must not kill instance 1."""
    if len(sessions) < 2:
        return None
    a, b = sessions[0], sessions[1]
    result = {"killed_instance": a.index, "survivor_instance": b.index}
    st = await read_state_async(b)
    result["survivor_frame_before"] = int(st.current_frame) if st is not None else None
    result["survivor_alive_before"] = winapi.is_process_alive(b.pid)
    subprocess.run(["taskkill", "/PID", str(a.pid), "/F"],
                   capture_output=True, text=True, timeout=20)
    a.outcome = "killed_by_test"
    await asyncio.sleep(3.0)
    result["killed_process_alive_after"] = winapi.is_process_alive(a.pid)
    st2 = await read_state_async(b)
    result["survivor_frame_after"] = int(st2.current_frame) if st2 is not None else None
    result["survivor_alive_after"] = winapi.is_process_alive(b.pid)
    result["survivor_advanced"] = (
        result["survivor_frame_before"] is not None
        and result["survivor_frame_after"] is not None
        and result["survivor_frame_after"] > result["survivor_frame_before"])
    return result


async def read_state_async(sess):
    from dsh_episode.runner import read_state
    try:
        return await read_state(sess.client)
    except Exception:  # noqa: BLE001
        return None


async def main_async(opts):
    run_id = opts.run_id or time.strftime("headless-%Y%m%d-%H%M%S")
    out_dir = pathlib.Path(opts.out_dir) if opts.out_dir else HEADLESS_ROOT / "runs" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    plan = parse_plan(opts.plan)
    game_dirs = [d.strip() for d in (opts.game_dirs or "E:\\RA2MCP\\test-game-headless").split(",")]
    ports = [int(p) for p in (opts.ports or "14531,14532").split(",")]
    if opts.instances > len(game_dirs) or opts.instances > len(ports):
        raise SystemExit("need one game dir and port per instance")
    sessions = [GameSession(i, game_dirs[i], ports[i], run_dir=out_dir)
                for i in range(opts.instances)]
    result = {"run_id": run_id, "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "argv": sys.argv, "plan": plan,
              "config": {"instances": opts.instances, "game_dirs": game_dirs[:opts.instances],
                         "ports": ports[:opts.instances],
                         "decision_seconds": opts.decision_seconds,
                         "max_phase_seconds": opts.max_phase_seconds,
                         "rpc_bench_seconds": opts.rpc_bench_seconds,
                         "kill_one_test": opts.kill_one_test,
                         "notes": opts.notes},
              "config_files": {}, "sessions": [], "phases": [],
              "monitor_csv": None, "screenshots": {}, "kill_one_test": None,
              "errors": []}
    # record game config fingerprints (spawn.ini/map/ddraw/exe/dll + ddraw.ini hash)
    for i, sess in enumerate(sessions):
        gd = pathlib.Path(sess.game_dir)
        entry = {"index": i}
        for name in ("gamemd-spawn-ra2yrcpp.exe", "libra2yrcpp.dll", "ra2yrcpp.json",
                     "spawn.ini", "spawnmap.ini", "ddraw.ini", "RA2MD.ini"):
            path = gd / name
            if path.exists():
                import hashlib
                h = hashlib.sha256()
                with open(path, "rb") as f:
                    for chunk in iter(lambda: f.read(1 << 20), b""):
                        h.update(chunk)
                entry[name] = {"sha256": h.hexdigest(), "size": path.stat().st_size}
            else:
                entry[name] = None
        result["config_files"][str(i)] = entry
    try:
        # launch and wait INGAME
        for sess in sessions:
            sess.launch()
            time.sleep(0.5)
        ingame = await asyncio.gather(*[s.wait_ingame(opts.launch_timeout) for s in sessions])
        for sess, st in zip(sessions, ingame):
            sess.window = winapi.pick_game_window(sess.pid)
        # setup with a short settle; hidden runs hide immediately after setup
        for sess in sessions:
            await sess.setup(st, warmup_s=2.0)
        for sess in sessions:
            sess.ensure_window("visible")
        result["screenshots"]["visible_ingame"] = capture_window_region(
            sessions[0], out_dir / "window_visible_ingame.png")
        # resource monitor (samples until plan + margin)
        total_plan = sum(p["seconds"] for p in plan)
        if total_plan <= 0:
            total_plan = opts.max_phase_seconds
        monitor_duration = int(total_plan + 20)
        monitor_csv = out_dir / "monitor.csv"
        gpu_csv = out_dir / "gpu_counters.csv"
        monitor_proc = subprocess.Popen(
            [shell_exe(), "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(SCRIPTS / "headless" / "monitor.ps1"),
             "-Pids", ",".join(str(s.pid) for s in sessions),
             "-OutCsv", str(monitor_csv), "-GpuCsv", str(gpu_csv),
             "-DurationSec", str(monitor_duration), "-IntervalMs", "1000"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        result["monitor_csv"] = str(monitor_csv)
        result["gpu_csv"] = str(gpu_csv)
        result["monitor_duration_s"] = monitor_duration
        records, phase_summaries = await run_plan(
            sessions, plan, opts.max_phase_seconds, out_dir,
            opts.decision_seconds, opts.rpc_bench_seconds, monitor_csv)
        result["phases"] = phase_summaries
        # partial result immediately, so evidence survives a later cleanup hang
        (out_dir / "result.partial.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        # restore + evidence screenshots (game may already have exited on defeat)
        for sess in sessions:
            try:
                sess.ensure_window("visible")
            except Exception as exc:  # noqa: BLE001
                result["errors"].append(f"restore {sess.label}: {type(exc).__name__}: {exc}")
        await asyncio.sleep(2.0)
        for sess in sessions:
            sess.window_samples["visible"] += 1
        result["restore"] = {
            str(s.index): {"window": s.window_state(),
                           "process_alive": winapi.is_process_alive(s.pid),
                           "process_exit_code": s.proc.poll() if s.proc else None}
            for s in sessions}
        result["screenshots"]["visible_after_restore"] = capture_window_region(
            sessions[0], out_dir / "window_visible_after_restore.png")
        if opts.kill_one_test:
            result["kill_one_test"] = await kill_one_test(sessions)
        # write event logs
        for sess in sessions:
            path = out_dir / f"{sess.label}-events.jsonl"
            with open(path, "w", encoding="utf-8") as f:
                for rec in records.get(sess.index, []):
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            result.setdefault("event_logs", {})[str(sess.index)] = str(path)
        # monitor shutdown (kill the PowerShell tree so no orphan typeperf remains)
        try:
            if monitor_proc.poll() is None:
                subprocess.run(["taskkill", "/PID", str(monitor_proc.pid), "/T", "/F"],
                               capture_output=True, text=True, timeout=20)
                monitor_proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            pass
        result["monitor_exit_code"] = monitor_proc.poll()
        # stop games we launched (bounded: the bridge can hang after game exit)
        close_results = await asyncio.gather(
            *[asyncio.wait_for(s.close(), 20) for s in sessions],
            return_exceptions=True)
        for sess, res in zip(sessions, close_results):
            if isinstance(res, Exception):
                result["errors"].append(
                    f"close {sess.label}: {type(res).__name__}: {res}")
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        result["errors"].append(f"{type(exc).__name__}: {exc}")
        for sess in sessions:
            try:
                await sess.close()
            except Exception:  # noqa: BLE001
                pass
    result["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    result["sessions"] = [s.summary() for s in sessions]
    (out_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nresult written: {out_dir / 'result.json'}")
    for s in result["sessions"]:
        print(f"  {s['label']}: steps={s['steps']} read_ok={s['read_ok']}/{s['read_fail']} "
              f"outcome={s['outcome']} launch_ingame={s['launch_to_ingame_s']}s "
              f"reward={s['reward_total']}")
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--instances", type=int, default=1, choices=[1, 2])
    ap.add_argument("--game-dirs", default=None,
                    help="comma separated game copies, one per instance")
    ap.add_argument("--ports", default="14651,14652", help="comma separated bridge ports")
    ap.add_argument("--plan", default="settle:5,visible:90,hidden:120,show:45")
    ap.add_argument("--decision-seconds", type=float, default=0.5)
    ap.add_argument("--max-phase-seconds", type=float, default=0)
    ap.add_argument("--rpc-bench-seconds", type=float, default=0)
    ap.add_argument("--launch-timeout", type=float, default=120)
    ap.add_argument("--kill-one-test", action="store_true")
    ap.add_argument("--notes", default=None)
    args = ap.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(0 if main() else 0)
