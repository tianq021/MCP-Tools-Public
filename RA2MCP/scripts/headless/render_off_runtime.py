"""Dual-instance render-off stability harness (phase B).

Each worker owns one isolated game copy, bridge port and process lifecycle.  Per
match it:

  1. launches its own game process and waits for a real INGAME state,
  2. hides the game window with ShowWindow(SW_HIDE),
  3. enables the validated, reversible render-off patch (RenderOffController),
  4. drives a rule policy at a fixed decision cadence until the match settles,
  5. restores the patch and verifies the original bytes, kills only its own PID,
  6. launches the next match until the requested duration is reached.

Watchdogs (frame stall / consecutive read failures / consecutive action
failures / patch loss) terminate the affected worker only and write diagnostics;
the other workers keep running.  No training and no model files are involved.

Usage:
  .venv\\Scripts\\python.exe scripts\\headless\\render_off_runtime.py \
      --run-id render-off-dual-10min --duration-seconds 600
  .venv\\Scripts\\python\\... --run-id render-off-dual-1h --duration-seconds 3600
"""
import argparse
import asyncio
import json
import pathlib
import shutil
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "vendor" / "pyra2yr"))
sys.path.insert(0, str(SCRIPTS / "headless"))

import winapi  # noqa: E402
from dsh_episode.render_off import (RenderOffController, RenderOffError,  # noqa: E402
                                    RenderOffRestoreError)
from dsh_episode.runner import read_state as read_state_fn  # noqa: E402
from run_experiment import shell_exe  # noqa: E402
from session import GameSession  # noqa: E402

HEADLESS_ROOT = ROOT / "reports" / "headless"
TERMINAL = ("win", "defeat", "game_end", "crash")
DEFAULT_GAME_DIRS = "E:\\RA2MCP\\test-game-headless,E:\\RA2MCP\\test-game-headless-2"
DEFAULT_PORTS = "14651,14652"


class WorkerFailure(RuntimeError):
    pass


def write_phase_marker(csv_path, phase):
    try:
        pathlib.Path(str(csv_path) + ".phase").write_text(phase, encoding="ascii")
    except OSError:
        pass


class RenderOffWorker:
    def __init__(self, index, game_dir, port, cfg, out_dir):
        self.index = int(index)
        self.game_dir = pathlib.Path(game_dir)
        self.port = int(port)
        self.cfg = cfg
        self.out_dir = pathlib.Path(out_dir)
        self.evidence_dir = self.out_dir / f"worker{self.index}"
        self.session = None
        self.controller = None
        self.events = []
        self.matches = []
        self.failure = None
        self.status = "pending"
        self.label = f"worker{self.index}"

    # ---- validation ----
    def validate_environment(self):
        if not self.game_dir.is_dir():
            raise WorkerFailure(f"game dir missing: {self.game_dir}")
        try:
            self.game_dir.resolve().relative_to(ROOT.resolve())
        except ValueError:
            raise WorkerFailure(f"game dir outside workspace: {self.game_dir}") from None
        if not self.game_dir.name.lower().startswith("test-game"):
            raise WorkerFailure(f"not a test-game copy: {self.game_dir.name}")
        cfg_path = self.game_dir / "ra2yrcpp.json"
        if not cfg_path.exists():
            raise WorkerFailure(f"bridge config missing: {cfg_path}")
        bridge = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
        if int(bridge.get("port", -1)) != self.port:
            raise WorkerFailure(
                f"port mismatch for {self.game_dir.name}: bridge config "
                f"{bridge.get('port')} vs worker port {self.port}")
        if not (self.game_dir / "spawn.ini").exists():
            raise WorkerFailure(f"spawn.ini missing in {self.game_dir}")
        return {"game_dir": str(self.game_dir), "port": self.port,
                "bridge": bridge}

    def log(self, msg):
        line = f"[{time.strftime('%H:%M:%S')}][{self.label}] {msg}"
        print(line, flush=True)
        self.events.append({"kind": "log", "t": round(time.time(), 3),
                            "message": msg})
        try:
            self.evidence_dir.mkdir(parents=True, exist_ok=True)
            with open(self.evidence_dir / "worker-log.jsonl", "a",
                      encoding="utf-8") as f:
                f.write(json.dumps({"t": round(time.time(), 3), "message": msg},
                                   ensure_ascii=False) + "\n")
        except OSError:
            pass

    # ---- match lifecycle ----
    async def run_match(self, match_index, deadline, stop_after_deadline):
        cfg = self.cfg
        match = {"match_index": match_index, "status": "starting",
                 "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                 "decision_seconds": cfg["decision_seconds"]}
        self.log(f"match {match_index}: launching {self.game_dir.name} port {self.port}")
        sess = GameSession(self.index, self.game_dir, self.port,
                           run_dir=self.out_dir, label=f"{self.label}-m{match_index}")
        self.session = sess
        session_closed = False
        restore_ok = None
        try:
            sess.launch()
            match["pid"] = sess.pid
            st = await sess.wait_ingame(cfg["launch_timeout"])
            sess.window = winapi.pick_game_window(sess.pid)
            await sess.setup(st, warmup_s=cfg["warmup_seconds"])
            win = sess.ensure_window("hidden")
            match["window"] = {"hwnd": sess.window["hwnd"] if sess.window else None,
                               "title": sess.window["title"] if sess.window else None,
                               "state_after_hide": win}
            if win != "hidden":
                raise WorkerFailure(f"window not hidden after SW_HIDE: {win}")
            controller = RenderOffController(
                sess.pid, evidence_dir=self.evidence_dir,
                stall_seconds=cfg["stall_seconds"],
                read_fail_limit=cfg["read_fail_limit"],
                action_fail_limit=cfg["action_fail_limit"])
            self.controller = controller
            patch = controller.enable()
            match["patch"] = patch
            match["status"] = "running"
            match["ingame_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            # confirm the game keeps advancing after the patch
            verify = await self._wait_advancing(cfg["post_patch_advance_seconds"])
            match["post_patch_advance"] = verify
            if not verify["advancing"]:
                match["status"] = "watchdog_frame_stall_after_patch"
                raise WorkerFailure(
                    f"game frame did not advance after render-off patch: {verify}")
            write_phase_marker(cfg["monitor_csv"], "match")
            match["decision_start_ms"] = int(time.time() * 1000)
            steps = read_ok = read_fail = orders_ok = invalid = 0
            frame_first = frame_last = None
            violations = []
            outcome = "running"
            step_records = []
            idx = 0
            match_start = time.perf_counter()
            patch_checked_at = 0.0
            while True:
                idx += 1
                rec = await sess.decision_step({"name": "hidden"}, idx)
                rec["match_index"] = match_index
                rec["phase_wall_ms"] = int(time.time() * 1000)
                step_records.append(rec)
                steps += 1
                frame = rec.get("frame")
                ok = bool(rec.get("read_ok"))
                read_ok += 1 if ok else 0
                read_fail += 0 if ok else 1
                if frame is not None:
                    frame_first = frame if frame_first is None else frame_first
                    frame_last = frame
                orders_ok += int(rec.get("orders_ok") or 0)
                invalid += int(rec.get("invalid") or 0)
                outcome = rec.get("outcome", outcome)
                h = controller.check_health(frame if frame is not None else -1,
                                            ok,
                                            ok_count=int(rec.get("orders_ok") or 0),
                                            invalid_count=int(rec.get("invalid") or 0))
                if h:
                    violations = h
                    match["status"] = "watchdog_" + h[0][0]
                    break
                now = time.perf_counter()
                if now - patch_checked_at >= 5.0:
                    patch_checked_at = now
                    try:
                        controller.assert_patch_intact()
                    except RenderOffError as exc:
                        violations = [("patch_lost", str(exc))]
                        match["status"] = "watchdog_patch_lost"
                        break
                if outcome in TERMINAL:
                    match["status"] = "settled"
                    break
                if sess.proc is not None and sess.proc.poll() is not None:
                    match["status"] = "process_exited"
                    match["exit_code_during_match"] = sess.proc.returncode
                    break
                if now - match_start > cfg["match_max_seconds"]:
                    match["status"] = "match_timeout"
                    break
                if stop_after_deadline and now >= deadline:
                    match["status"] = "stopped_by_duration"
                    break
                wait = cfg["decision_seconds"] - rec.get("cycle_ms", 0) / 1000.0
                if wait > 0:
                    await asyncio.sleep(wait)
            match["decision_end_ms"] = int(time.time() * 1000)
            match.update(steps=steps, read_ok=read_ok, read_fail=read_fail,
                         orders_ok=orders_ok, invalid_orders=invalid,
                         frames_first=frame_first, frames_last=frame_last,
                         outcome=outcome, violations=violations,
                         decision_wall_seconds=round(
                             (match["decision_end_ms"] - match["decision_start_ms"]) / 1000.0, 3))
            if frame_first is not None and frame_last is not None and \
                    match["decision_wall_seconds"] > 0:
                match["game_fps"] = round(
                    (frame_last - frame_first) / match["decision_wall_seconds"], 3)
            # store decision records before cleanup
            ev_path = self.out_dir / f"{self.label}-match{match_index}-events.jsonl"
            with open(ev_path, "w", encoding="utf-8") as f:
                for rec in step_records:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            match["events_file"] = str(ev_path)
            # ---- cleanup: restore patch first (while alive), verify, then kill ----
            if winapi.is_process_alive(sess.pid):
                try:
                    restore = controller.disable()
                    restore_ok = bool(restore.get("restored"))
                    match["restore"] = restore
                except RenderOffRestoreError as exc:
                    restore_ok = False
                    match["restore"] = {"restored": False, "error": str(exc)}
                    self.log(f"match {match_index}: RESTORE FAILED: {exc}")
            else:
                match["restore"] = {"restored": None,
                                    "note": "process already exited before restore"}
            match["patch_restored"] = restore_ok
            match["process_alive_before_close"] = winapi.is_process_alive(sess.pid)
            await sess.close()
            session_closed = True
            match["process_alive_after_close"] = winapi.is_process_alive(sess.pid)
            match["exit_code_after_close"] = (sess.proc.poll()
                                              if sess.proc is not None else None)
            match["cleanup_ok"] = bool(match.get("process_alive_after_close") is False)
            # hard failure cases
            if restore_ok is False:
                raise WorkerFailure(
                    f"render-off patch restore failed (match {match_index}); "
                    "process terminated, worker stops")
            if match["status"].startswith("watchdog"):
                raise WorkerFailure(
                    f"watchdog triggered in match {match_index}: "
                    f"{match['status']} {violations}")
            if not match["cleanup_ok"]:
                raise WorkerFailure(
                    f"process {match.get('pid')} still alive after close")
        finally:
            if not session_closed:
                try:
                    if self.controller is not None and sess.pid and \
                            winapi.is_process_alive(sess.pid):
                        try:
                            self.controller.disable()
                        except Exception:  # noqa: BLE001
                            pass
                    await asyncio.wait_for(sess.close(), 25)
                except Exception:  # noqa: BLE001
                    pass
            self.session = None
            self.controller = None
            match["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            self.matches.append(match)
            try:
                with open(self.evidence_dir / "matches.jsonl", "a",
                          encoding="utf-8") as f:
                    f.write(json.dumps(match, ensure_ascii=False, default=str) + "\n")
            except OSError:
                pass
            self.log(f"match {match_index} finished: status={match['status']} "
                     f"steps={match.get('steps')} read_fail={match.get('read_fail')} "
                     f"restored={match.get('patch_restored')} "
                     f"cleanup_ok={match.get('cleanup_ok')}")
        return match

    async def _wait_advancing(self, seconds=5.0):
        frames = []
        deadline = time.perf_counter() + seconds
        while time.perf_counter() < deadline and len(frames) < 8:
            st = await read_state_fn(self.session.client)
            if st is not None:
                frames.append(int(st.current_frame))
            await asyncio.sleep(0.5)
        advancing = len(frames) >= 2 and frames[-1] > frames[0] + 5
        return {"advancing": bool(advancing), "frames": frames}

    # ---- worker loop ----
    async def run(self, deadline, stop_after_deadline=True):
        self.status = "running"
        try:
            env = self.validate_environment()
            self.events.append({"kind": "env_validated", **env})
            match_index = 0
            while time.perf_counter() < deadline:
                match_index += 1
                await self.run_match(match_index, deadline,
                                     stop_after_deadline=stop_after_deadline)
                # if this match already consumed the remaining time, stop
                if time.perf_counter() >= deadline:
                    break
                if match_index >= self.cfg["max_matches"]:
                    self.events.append({"kind": "max_matches_reached",
                                        "matches": match_index})
                    break
                await asyncio.sleep(self.cfg["between_matches_seconds"])
            self.status = "ok"
        except WorkerFailure as exc:
            self.failure = {"type": "WorkerFailure", "error": str(exc)}
            self.status = "failed"
            self.log(f"worker failed: {exc}")
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            self.failure = {"type": type(exc).__name__, "error": str(exc)}
            self.status = "failed"
            self.log(f"worker crashed: {type(exc).__name__}: {exc}")
        finally:
            if self.session is not None:
                try:
                    await asyncio.wait_for(self.session.close(), 20)
                except Exception:  # noqa: BLE001
                    pass
                self.session = None
            for target in (self.out_dir / f"{self.label}-worker-result.json",
                           self.evidence_dir / "worker-result.json"):
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(json.dumps(self.summary(),
                                                 ensure_ascii=False, indent=2,
                                                 default=str), encoding="utf-8")
                except OSError:
                    pass
        return self.summary()

    def summary(self):
        steps = sum(m.get("steps") or 0 for m in self.matches)
        read_ok = sum(m.get("read_ok") or 0 for m in self.matches)
        read_fail = sum(m.get("read_fail") or 0 for m in self.matches)
        invalid = sum(m.get("invalid_orders") or 0 for m in self.matches)
        orders = sum(m.get("orders_ok") or 0 for m in self.matches)
        settled = sum(1 for m in self.matches if m.get("status") in
                      ("settled", "process_exited", "stopped_by_duration"))
        decision_wall = sum(m.get("decision_wall_seconds") or 0 for m in self.matches)
        return {
            "index": self.index, "label": self.label,
            "game_dir": str(self.game_dir), "port": self.port,
            "status": self.status, "failure": self.failure,
            "matches": self.matches,
            "match_count": len(self.matches),
            "settled_matches": settled,
            "steps": steps, "read_ok": read_ok, "read_fail": read_fail,
            "orders_ok": orders, "invalid_orders": invalid,
            "decision_wall_seconds": round(decision_wall, 3),
            "steps_per_s_decision_wall": round(steps / decision_wall, 4)
            if decision_wall else None,
            "events": self.events,
        }


def _monitor(monitor_csv, gpu_csv, duration_s):
    return subprocess.Popen(
        [shell_exe(), "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", str(SCRIPTS / "headless" / "monitor_dynamic.ps1"),
         "-OutCsv", str(monitor_csv), "-GpuCsv", str(gpu_csv),
         "-DurationSec", str(int(duration_s)), "-IntervalMs", "1000"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _kill_monitor(proc):
    try:
        if proc is not None and proc.poll() is None:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, text=True, timeout=20)
            proc.wait(timeout=10)
    except Exception:  # noqa: BLE001
        pass


async def main_async(opts):
    run_id = opts.run_id or time.strftime("render-off-%Y%m%d-%H%M%S")
    out_dir = (pathlib.Path(opts.out_dir) if opts.out_dir
               else HEADLESS_ROOT / "runs" / run_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    game_dirs = [d.strip() for d in opts.game_dirs.split(",") if d.strip()]
    ports = [int(p) for p in opts.ports.split(",") if p.strip()]
    if len(game_dirs) < opts.instances or len(ports) < opts.instances:
        raise SystemExit("need one game dir and one port per instance")
    cfg = {
        "decision_seconds": opts.decision_seconds,
        "match_max_seconds": opts.match_max_seconds,
        "launch_timeout": opts.launch_timeout,
        "warmup_seconds": opts.warmup_seconds,
        "between_matches_seconds": opts.between_matches_seconds,
        "stall_seconds": opts.stall_seconds,
        "read_fail_limit": opts.read_fail_limit,
        "action_fail_limit": opts.action_fail_limit,
        "post_patch_advance_seconds": opts.post_patch_advance_seconds,
        "max_matches": opts.max_matches,
        "monitor_csv": str(out_dir / "monitor.csv"),
    }
    result = {
        "run_id": run_id,
        "kind": "render-off-stability",
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "argv": sys.argv,
        "config": {**cfg, "instances": opts.instances,
                   "duration_seconds": opts.duration_seconds,
                   "game_dirs": game_dirs[:opts.instances],
                   "ports": ports[:opts.instances]},
        "workers": [], "monitor_csv": cfg["monitor_csv"],
        "gpu_csv": str(out_dir / "gpu_counters.csv"),
        "gpu_by_pid_csv": str(out_dir / "monitor_gpu_by_pid.csv"),
        "errors": [],
    }
    workers = [RenderOffWorker(i, game_dirs[i], ports[i], cfg, out_dir)
               for i in range(opts.instances)]
    monitor_duration = opts.duration_seconds + opts.match_max_seconds + 180
    monitor_proc = _monitor(cfg["monitor_csv"], result["gpu_csv"], monitor_duration)
    deadline = time.perf_counter() + opts.duration_seconds
    result["deadline_perf"] = deadline
    try:
        # launch all workers together; each waits for its own INGAME
        summaries = await asyncio.gather(
            *[w.run(deadline) for w in workers], return_exceptions=True)
        for worker, s in zip(workers, summaries):
            if isinstance(s, Exception):
                result["errors"].append(
                    f"{worker.label}: {type(s).__name__}: {s}")
    finally:
        _kill_monitor(monitor_proc)
        result["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        result["workers"] = [w.summary() for w in workers]
        starts = [m.get("decision_start_ms") for w in workers for m in w.matches
                  if m.get("decision_start_ms")]
        ends = [m.get("decision_end_ms") for w in workers for m in w.matches
                if m.get("decision_end_ms")]
        total_steps = sum(w.summary()["steps"] for w in workers)
        result["totals"] = {
            "instances": opts.instances,
            "matches": sum(len(w.matches) for w in workers),
            "settled_matches": sum(w.summary()["settled_matches"] for w in workers),
            "steps": total_steps,
            "read_ok": sum(w.summary()["read_ok"] for w in workers),
            "read_fail": sum(w.summary()["read_fail"] for w in workers),
            "orders_ok": sum(w.summary()["orders_ok"] for w in workers),
            "invalid_orders": sum(w.summary()["invalid_orders"] for w in workers),
            "decision_wall_seconds": round(sum(
                w.summary()["decision_wall_seconds"] for w in workers), 3),
            "sampling_wall_seconds": round((max(ends) - min(starts)) / 1000.0, 3)
            if starts and ends else None,
            "workers_failed": sum(1 for w in workers if w.status == "failed"),
            "workers_ok": sum(1 for w in workers if w.status == "ok"),
        }
        if starts and ends:
            result["totals"]["aggregate_steps_per_s"] = round(
                total_steps / max(0.001, (max(ends) - min(starts)) / 1000.0), 4)
            per_instance = [w.summary()["steps_per_s_decision_wall"] for w in workers]
            result["totals"]["per_instance_steps_per_s"] = per_instance
            result["totals"]["mean_per_instance_steps_per_s"] = round(
                sum(x for x in per_instance if x) /
                max(1, len([x for x in per_instance if x])), 4)
        result["monitor_duration_s"] = monitor_duration
        (out_dir / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8")
        print(f"\nresult written: {out_dir / 'result.json'}")
        print(json.dumps(result["totals"], ensure_ascii=False, indent=2))
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--instances", type=int, default=2, choices=[1, 2])
    ap.add_argument("--game-dirs", default=DEFAULT_GAME_DIRS)
    ap.add_argument("--ports", default=DEFAULT_PORTS)
    ap.add_argument("--duration-seconds", type=float, default=600,
                    help="total test duration; 600 = 10 min, 3600 = 1 h")
    ap.add_argument("--decision-seconds", type=float, default=0.5)
    ap.add_argument("--match-max-seconds", type=float, default=600)
    ap.add_argument("--max-matches", type=int, default=99)
    ap.add_argument("--between-matches-seconds", type=float, default=3)
    ap.add_argument("--launch-timeout", type=float, default=120)
    ap.add_argument("--warmup-seconds", type=float, default=1.5)
    ap.add_argument("--stall-seconds", type=float, default=8)
    ap.add_argument("--read-fail-limit", type=int, default=5)
    ap.add_argument("--action-fail-limit", type=int, default=10)
    ap.add_argument("--post-patch-advance-seconds", type=float, default=5)
    args = ap.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(0 if main() else 0)
