"""Diagnostic: launch the isolated headless copy, verify window discovery,
bridge INGAME, and a short ShowWindow hide/restore cycle.

Not the experiment itself; used to validate the harness before longer runs.
Writes a JSON summary to stdout and to reports/headless/diagnostics/.
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
sys.path.insert(0, str(ROOT / "scripts" / "headless"))

import winapi  # noqa: E402
from pyra2yr.network import DualClient  # noqa: E402
from ra2yrproto import commands_yr, ra2yr  # noqa: E402

DEFAULT_EXE = "gamemd-spawn-ra2yrcpp.exe"


async def read_state(client, timeout=5.0):
    try:
        cmd = commands_yr.GetGameState()
        r = await asyncio.wait_for(client.exec_command(cmd, timeout=3), timeout)
        if getattr(r, "result_code", 0) != 0:
            return None, f"result_code={r.result_code}"
        if not r.result.Unpack(cmd):
            return None, "unpack_failed"
        return cmd.state, None
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


async def main(opts):
    game_dir = pathlib.Path(opts.game_dir)
    exe = game_dir / DEFAULT_EXE
    for p in (game_dir / "ra2yrcpp.log", game_dir / "stats.dmp"):
        try:
            p.unlink()
        except OSError:
            pass
    t_launch = time.monotonic()
    proc = subprocess.Popen([str(exe), "-SPAWN", "-CD"], cwd=str(game_dir),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    out = {"game_dir": str(game_dir), "pid": proc.pid, "port": opts.port,
           "launch_to_window_s": None, "launch_to_ingame_s": None,
           "windows_pre": [], "windows_ingame": [], "hide": {},
           "frames": {}, "errors": []}
    win = winapi.wait_for_window(proc.pid, timeout_s=90, poll_s=0.25)
    out["launch_to_window_s"] = round(time.monotonic() - t_launch, 2)
    out["windows_pre"] = winapi.snapshot_windows(proc.pid)
    if win is None:
        out["errors"].append("no window appeared")
        proc.kill()
        return out

    client = DualClient("127.0.0.1", opts.port, timeout=2)
    client.connect()
    st = None
    deadline = time.monotonic() + 120
    tries = 0
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            out["errors"].append(f"game exited rc={proc.returncode}")
            break
        tries += 1
        st, err = await read_state(client)
        if st is not None and st.stage == 2 and len(st.objects) > 0:
            break
        st = None
        await asyncio.sleep(1.0)
    if st is None:
        out["errors"].append(f"INGAME timeout after {tries} reads")
    else:
        out["launch_to_ingame_s"] = round(time.monotonic() - t_launch, 2)
        out["ingame_frame"] = int(st.current_frame)
        out["ingame_objects"] = len(st.objects)
        out["windows_ingame"] = winapi.snapshot_windows(proc.pid)
        win = winapi.pick_game_window(proc.pid) or win
        out["chosen_window"] = {k: win[k] for k in ("hwnd", "pid", "title", "class",
                                                    "visible", "rect")}

        async def frames(n, gap):
            result, last = [], None
            for _ in range(n):
                s, err = await read_state(client)
                if s is None:
                    result.append({"error": err})
                else:
                    result.append({"frame": int(s.current_frame),
                                   "objects": len(s.objects),
                                   "stage": ra2yr.LoadStage.Name(s.stage)})
                await asyncio.sleep(gap)
            return result

        out["frames"]["before_hide"] = await frames(4, 1.0)
        t_hide = time.monotonic()
        ok = winapi.hide_window(win["hwnd"])
        await asyncio.sleep(2.0)
        out["hide"] = {
            "sw_hide_returned_hidden": ok,
            "is_window_visible_after": winapi.is_window_visible(win["hwnd"]),
            "process_alive_after": winapi.is_process_alive(proc.pid),
            "process_resources_hidden": winapi.process_cpu_memory(proc.pid),
        }
        out["frames"]["hidden"] = await frames(5, 1.0)
        ok2 = winapi.show_window(win["hwnd"])
        await asyncio.sleep(1.0)
        out["show"] = {"is_window_visible_after_show": winapi.is_window_visible(win["hwnd"]),
                       "show_returned_visible": ok2,
                       "process_alive": winapi.is_process_alive(proc.pid)}
        out["frames"]["after_show"] = await frames(3, 1.0)
        out["hide_duration_s"] = round(time.monotonic() - t_hide, 2)
    try:
        await client.stop()
    except Exception:  # noqa: BLE001
        pass
    # only terminate the process we launched
    if proc.poll() is None:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/F"],
                       capture_output=True, text=True, timeout=20)
    out["exit_code"] = proc.poll()
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-dir", default=str(ROOT / "test-game-headless"))
    ap.add_argument("--port", type=int, default=14531)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    result = asyncio.run(main(args))
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    out_path = pathlib.Path(args.out) if args.out else (
        ROOT / "reports" / "headless" / "diagnostics" /
        f"inspect-session-{time.strftime('%Y%m%d-%H%M%S')}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    print(f"\nwritten: {out_path}")
