"""Phase 4: DirectDraw render/present controllability experiment.

Runs a real game and toggles the reversible runtime patches discovered by
disasm_analysis.py.  Each mode is tested independently because an unsafe patch
can hang the game main thread (the previous mode's restore does not unhang it):

  blit     -> GScreenClass::DoBlit   (0x4F4780) final presentation/blit path
  tactical -> TacticalClass::Render  (0x6D3D10) battlefield drawing only
  render   -> GScreenClass::Render   (0x4F4480) whole per-frame draw dispatch

For each mode: baseline -> patch -> phase with screenshots -> restore -> verify
the game is responsive and the picture updates again.  If the game main thread
hangs (bridge reads time out / frame counter frozen), the mode is recorded as
unsafe and the remaining modes are skipped (the game can only be killed).

The patch is always restored in `finally`; original bytes are saved to JSON.
Nothing is written to disk in the game directory.

Example:
  .venv\\Scripts\\python.exe scripts\\headless\\phase4_norender.py \
      --run-id phase4-render-01 --port 14651 --modes render
"""
import argparse
import asyncio
import hashlib
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

import render_patch  # noqa: E402
import winapi  # noqa: E402
from dsh_episode.runner import read_state as read_state_fn  # noqa: E402
from run_experiment import (capture_window_region, shell_exe, summarize_phase,  # noqa: E402
                            write_phase_marker)
from session import GameSession  # noqa: E402

HEADLESS_ROOT = ROOT / "reports" / "headless"
TERMINAL = ("win", "defeat", "game_end", "crash")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


async def wait_responsive(session, timeout_s=8.0, min_frame_advance=5):
    """Wait until the bridge answers and the game frame counter advances."""
    deadline = time.perf_counter() + timeout_s
    frames = []
    while time.perf_counter() < deadline:
        st = await read_state_fn(session.client)
        if st is not None:
            frames.append(int(st.current_frame))
            if len(frames) >= 2 and frames[-1] - frames[0] >= min_frame_advance:
                return {"responsive": True, "frames": frames[:2] + frames[-2:]}
        await asyncio.sleep(0.7)
    return {"responsive": False, "frames": frames[-5:]}


async def run_phase(session, name, seconds, out_dir, monitor_csv):
    write_phase_marker(monitor_csv, name)
    start_ms = int(time.time() * 1000)
    t_start = time.perf_counter()
    records = []
    idx = 0
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        idx += 1
        rec = await session.decision_step({"name": name}, idx)
        rec["phase"] = name
        rec["phase_wall_ms"] = int(time.time() * 1000)
        records.append(rec)
        if rec.get("fatal") or rec.get("outcome") in TERMINAL:
            break
        if session.proc is not None and session.proc.poll() is not None:
            break
        wait = 0.5 - rec.get("cycle_ms", 0) / 1000.0
        if wait > 0:
            await asyncio.sleep(wait)
    end_ms = int(time.time() * 1000)
    duration = time.perf_counter() - t_start
    summary = summarize_phase({"name": name, "window": "visible", "seconds": seconds},
                              [session], {session.index: records},
                              start_ms, end_ms, duration_s=duration)
    summary["wall_seconds"] = round(duration, 3)
    inst = summary["instances"]["0"]
    print(f"  phase {name}: steps={summary['total_steps']} fps={inst['game_fps']} "
          f"read_ok={inst['read_ok']}/{inst['read_fail']} "
          f"outcome={inst['outcome']}", flush=True)
    return summary, records


async def screenshot_pair(session, out_dir, tag, gap=1.6):
    a = out_dir / f"window_{tag}_a.png"
    b = out_dir / f"window_{tag}_b.png"
    if not capture_window_region(session, a):
        return {"tag": tag, "captured": False}
    await asyncio.sleep(gap)
    if not capture_window_region(session, b):
        return {"tag": tag, "captured": False, "a": str(a), "sha_a": sha256_file(a)}
    ha, hb = sha256_file(a), sha256_file(b)
    return {"tag": tag, "captured": True, "a": str(a), "b": str(b),
            "sha_a": ha, "sha_b": hb, "identical": ha == hb,
            "changed": ha != hb}


async def main_async(opts):
    run_id = opts.run_id or time.strftime("phase4-%Y%m%d-%H%M%S")
    out_dir = (pathlib.Path(opts.out_dir) if opts.out_dir
               else HEADLESS_ROOT / "runs" / run_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    modes = [m.strip() for m in opts.modes.split(",") if m.strip()]
    for mode in modes:
        if mode not in render_patch.TARGETS:
            raise SystemExit(f"unknown patch mode {mode!r}")
    requested_modes = list(modes)
    if opts.skip_mode_tests:
        modes = []
    session = GameSession(0, opts.game_dir, opts.port, run_dir=out_dir, label="instance0")
    monitor_csv = out_dir / "monitor.csv"
    gpu_csv = out_dir / "gpu_counters.csv"
    result = {"run_id": run_id, "argv": sys.argv,
              "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "config": {"game_dir": str(session.game_dir), "port": session.port,
                         "decision_seconds": opts.decision_seconds, "modes": modes},
              "modes": requested_modes, "patches": [], "phases": [], "screenshots": {},
              "mode_results": {}, "monitor_csv": str(monitor_csv),
              "gpu_csv": str(gpu_csv), "errors": []}
    monitor_proc = None
    patched = {}
    try:
        session.launch()
        st = await session.wait_ingame(opts.launch_timeout)
        session.window = winapi.pick_game_window(session.pid)
        await session.setup(st, warmup_s=2.0)
        session.ensure_window("visible")
        total_s = int((opts.baseline if modes else 0) +
                      sum(opts.off_seconds + opts.restore_seconds for _ in modes) +
                      (opts.max_end_seconds if opts.run_to_terminal else 0) + 90)
        monitor_proc = subprocess.Popen(
            [shell_exe(), "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(SCRIPTS / "headless" / "monitor.ps1"),
             "-Pids", str(session.pid), "-OutCsv", str(monitor_csv),
             "-GpuCsv", str(gpu_csv), "-DurationSec", str(total_s),
             "-IntervalMs", "1000"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        result["monitor_duration_s"] = total_s

        async def phase_with_shots(name, seconds):
            summary, records = await run_phase(session, name, seconds, out_dir,
                                               monitor_csv)
            shots = await screenshot_pair(session, out_dir, name)
            summary["visual"] = shots
            result["phases"].append(summary)
            result["screenshots"][name] = shots
            print(f"  visual {name}: identical={shots.get('identical')}", flush=True)
            return summary, shots

        if modes:
            await phase_with_shots("baseline", opts.baseline)
        for mode in modes:
            spec = render_patch.TARGETS[mode]
            entry = {"mode": mode, "target": {"va": spec["va"], "name": spec["name"],
                                               "expect_hex": spec["expect"].hex(),
                                               "patch_hex": spec["patch"].hex()}}
            pre = await wait_responsive(session, 6.0)
            entry["responsive_before"] = pre
            if not pre["responsive"]:
                entry["result"] = "skipped_game_unresponsive"
                result["mode_results"][mode] = entry
                result["errors"].append(f"{mode}: game unresponsive before patch")
                break
            info = render_patch.apply_patch(session.pid, mode,
                                            state_path=out_dir / f"patch-{mode}.json")
            info["applied_at_phase"] = f"{mode}_off"
            result["patches"].append(info)
            patched[mode] = info["bytes_before"]
            entry["patch"] = info
            print(f"patch {mode}: {info}", flush=True)
            await phase_with_shots(f"{mode}_off", opts.off_seconds)
            restored = render_patch.restore_patch(session.pid, mode,
                                                  patched.pop(mode))
            restored["restored_at_phase"] = f"{mode}_restored"
            result["patches"].append({"restore": restored})
            entry["restore"] = restored
            print(f"restore {mode}: {restored}", flush=True)
            post = await wait_responsive(session, 8.0)
            entry["responsive_after_restore"] = post
            await phase_with_shots(f"{mode}_restored", opts.restore_seconds)
            final = render_patch.read_target(session.pid, mode)
            expected = render_patch.TARGETS[mode]["expect"]
            entry["final_prologue_hex"] = final["bytes"]
            entry["prologue_restored"] = final["bytes"].startswith(expected.hex())
            if not post["responsive"]:
                entry["result"] = "unsafe_game_main_thread_hung"
            elif not entry["prologue_restored"]:
                entry["result"] = "restore_mismatch"
            else:
                entry["result"] = "tested"
            result["mode_results"][mode] = entry
            print(f"  mode {mode}: {entry['result']} "
                  f"responsive_after_restore={post['responsive']}", flush=True)
            if not post["responsive"]:
                break
        # ---- optional: hidden window + draw skipped until the match settles ----
        if opts.run_to_terminal:
            terminal_mode = opts.terminal_mode
            if terminal_mode not in render_patch.TARGETS:
                raise SystemExit(f"unknown --terminal-mode {terminal_mode!r}")
            session.ensure_window("hidden")
            await asyncio.sleep(1.0)
            info = render_patch.apply_patch(
                session.pid, terminal_mode,
                state_path=out_dir / f"patch-terminal-{terminal_mode}.json")
            info["applied_at_phase"] = "terminal"
            result["patches"].append(info)
            patched[terminal_mode] = info["bytes_before"]
            terminal_cfg = {"mode": terminal_mode, "window": "hidden",
                            "max_seconds": opts.max_end_seconds,
                            "patch": info}
            result["terminal"] = terminal_cfg
            print(f"terminal run: mode={terminal_mode} window=hidden "
                  f"max={opts.max_end_seconds}s", flush=True)
            summary, records = await run_phase(session, "render_off_hidden_end",
                                               opts.max_end_seconds, out_dir,
                                               monitor_csv)
            result["phases"].append(summary)
            inst = summary["instances"]["0"]
            terminal_cfg["phase_outcome"] = inst.get("outcome")
            terminal_cfg["steps"] = inst.get("steps")
            terminal_cfg["read_ok"] = inst.get("read_ok")
            terminal_cfg["read_fail"] = inst.get("read_fail")
            terminal_cfg["game_fps"] = inst.get("game_fps")
            terminal_cfg["window_hidden_samples"] = inst.get("window_hidden_samples")
            terminal_cfg["damage_to_enemy"] = inst.get("damage_to_enemy")
            terminal_cfg["damage_to_own"] = inst.get("damage_to_own")
            terminal_cfg["moved_cells"] = inst.get("moved_cells")
            terminal_cfg["orders_ok"] = inst.get("orders_ok")
            terminal_cfg["process_exit_code"] = (session.proc.poll()
                                                 if session.proc else None)
            print(f"terminal outcome={terminal_cfg['phase_outcome']} "
                  f"steps={terminal_cfg['steps']} "
                  f"fps={terminal_cfg['game_fps']} "
                  f"hidden_samples={terminal_cfg['window_hidden_samples']} "
                  f"exit={terminal_cfg['process_exit_code']}", flush=True)
            if winapi.is_process_alive(session.pid):
                restored = render_patch.restore_patch(
                    session.pid, terminal_mode, patched.pop(terminal_mode))
                terminal_cfg["restore_after_terminal"] = restored
                session.ensure_window("visible")
                await asyncio.sleep(1.0)
                terminal_cfg["responsive_after_restore"] = await wait_responsive(
                    session, 6.0)
        result["final_targets"] = {
            mode: render_patch.read_target(session.pid, mode) for mode in modes}
        result["final_state"] = {
            "process_alive": winapi.is_process_alive(session.pid),
            "window": session.window_state(),
            "frame": session.last_frame,
            "outcome": session.outcome,
        }
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        result["errors"].append(f"{type(exc).__name__}: {exc}")
    finally:
        for mode, original in list(patched.items()):
            if session.pid and winapi.is_process_alive(session.pid):
                try:
                    restore = render_patch.restore_patch(session.pid, mode, original)
                    result["patches"].append({"restore_in_finally": restore})
                    print(f"restore in finally {mode}: {restore}", flush=True)
                except Exception as exc:  # noqa: BLE001
                    result["errors"].append(f"restore {mode}: {exc}")
            patched.pop(mode, None)
        try:
            if monitor_proc is not None and monitor_proc.poll() is None:
                subprocess.run(["taskkill", "/PID", str(monitor_proc.pid), "/T", "/F"],
                               capture_output=True, text=True, timeout=20)
        except Exception:  # noqa: BLE001
            pass
        try:
            await asyncio.wait_for(session.close(), 25)
        except Exception as exc:  # noqa: BLE001
            result["errors"].append(f"close: {type(exc).__name__}: {exc}")
        result["sessions"] = [session.summary()]
        result["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        (out_dir / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"\nresult written: {out_dir / 'result.json'}")
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--game-dir", default=str(ROOT / "test-game-headless"))
    ap.add_argument("--port", type=int, default=14651)
    ap.add_argument("--modes", default="blit,tactical,render")
    ap.add_argument("--decision-seconds", type=float, default=0.5)
    ap.add_argument("--launch-timeout", type=float, default=120)
    ap.add_argument("--baseline", type=float, default=12)
    ap.add_argument("--off-seconds", type=float, default=18)
    ap.add_argument("--restore-seconds", type=float, default=10)
    ap.add_argument("--run-to-terminal", action="store_true",
                    help="after the mode tests, hide the window, skip drawing and "
                         "play until the match settles")
    ap.add_argument("--skip-mode-tests", action="store_true",
                    help="only do the terminal (hidden + draw skipped) run")
    ap.add_argument("--terminal-mode", default="render")
    ap.add_argument("--max-end-seconds", type=float, default=360)
    args = ap.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(0 if main() else 0)
