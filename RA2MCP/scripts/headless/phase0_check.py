"""Phase 0 evidence: instance patch/config audit + two-instance INGAME liveness.

Read-mostly checks of the configured game copies (test-game, test-game-1), then
a short read-only dual-instance run that proves both real games reach STAGE_INGAME
and keep producing valid state changes (frame / object / money / unit positions).

No training is started or stopped, no config is modified.

Usage:
  .venv\\Scripts\\python.exe scripts\\headless\\phase0_check.py
"""
import argparse
import asyncio
import hashlib
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "vendor" / "pyra2yr"))
sys.path.insert(0, str(SCRIPTS / "headless"))

import winapi  # noqa: E402
from dsh_episode.runner import read_state as read_state_fn  # noqa: E402
from ra2yrproto import ra2yr as ra2yr_proto  # noqa: E402
from session import GameSession  # noqa: E402

HEADLESS_ROOT = ROOT / "reports" / "headless"
DEFAULT_INSTANCES = [
    {"index": 0, "game_dir": str(ROOT / "test-game"), "port": 14521},
    {"index": 1, "game_dir": str(ROOT / "test-game-1"), "port": 14522},
]
FILES = ("gamemd-spawn-ra2yrcpp.exe", "libra2yrcpp.dll", "ra2yrcpp.json",
         "spawn.ini", "spawnmap.ini", "ddraw.ini", "RA2MD.ini")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def audit_instance(inst):
    game_dir = pathlib.Path(inst["game_dir"])
    out = {"index": inst["index"], "game_dir": str(game_dir),
           "port_config": inst["port"], "exists": game_dir.is_dir(),
           "files": {}, "bridge_config": None, "mutex_marker": None,
           "unique_mutex": None, "running_processes": [],
           "last_logs": {}}
    if not out["exists"]:
        return out
    for name in FILES:
        path = game_dir / name
        out["files"][name] = ({"sha256": sha256_file(path),
                               "size": path.stat().st_size,
                               "mtime": time.strftime(
                                   "%Y-%m-%dT%H:%M:%S",
                                   time.localtime(path.stat().st_mtime))}
                              if path.exists() else None)
    cfg_path = game_dir / "ra2yrcpp.json"
    if cfg_path.exists():
        cfg = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
        out["bridge_config"] = cfg
    marker = game_dir / "ra2mcp-instance.json"
    if marker.exists():
        out["mutex_marker"] = json.loads(marker.read_text(encoding="utf-8-sig"))
    log = game_dir / "ra2yrcpp.log"
    if log.exists():
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
        out["last_logs"]["ra2yrcpp.log"] = {
            "size": log.stat().st_size,
            "mtime": time.strftime("%Y-%m-%dT%H:%M:%S",
                                   time.localtime(log.stat().st_mtime)),
            "lines": len(lines),
            "head": lines[:6], "tail": lines[-6:]}
    dmp = game_dir / "stats.dmp"
    out["last_logs"]["stats.dmp_present"] = dmp.exists()
    return out


def cross_check(instances):
    """Mutually exclusive mutex names + distinct bridge ports."""
    result = {"ports_unique": True, "mutex_names_unique": True, "details": []}
    ports = [i["port_config"] for i in instances]
    result["ports_unique"] = len(set(ports)) == len(ports)
    names = []
    for inst in instances:
        marker = inst.get("mutex_marker")
        if marker:
            names.extend(marker.get("mutex_names") or [])
        else:
            # instance 0 keeps the vanilla GUIDs
            names.extend(["29e3bb2a-2f36-11d3-a72c-0090272fa661",
                          "01AF9993-3492-11d3-8F6F-0060089C05B1"])
    result["mutex_names_unique"] = len(set(names)) == len(names)
    result["mutex_names"] = names
    result["ports"] = ports
    return result


async def liveness_probe(session, seconds=8.0):
    """Read state once per second; require INGAME + changing frame/object state."""
    samples = []
    start = time.perf_counter()
    failures = 0
    while time.perf_counter() - start < seconds:
        try:
            st = await read_state_fn(session.client)
        except Exception:  # noqa: BLE001
            st = None
        if st is None:
            failures += 1
            samples.append({"error": "state_read_failed"})
        else:
            own = None
            money = None
            for h in st.houses:
                if h.current_player and h.is_human_player:
                    money = int(h.money)
            # cheap fingerprint of real state change: per-object cell sum
            cell_sum = 0
            for o in st.objects:
                cell_sum += int(o.coordinates.x) + int(o.coordinates.y)
            samples.append({"t": round(time.perf_counter() - start, 3),
                            "stage": ra2yr_proto.LoadStage.Name(st.stage),
                            "frame": int(st.current_frame),
                            "objects": len(st.objects),
                            "cell_sum": cell_sum, "money": money})
        await asyncio.sleep(1.0)
    frames = [s["frame"] for s in samples if "frame" in s]
    ok = [s for s in samples if "frame" in s]
    return {
        "samples": samples,
        "read_ok": len(ok), "read_fail": failures,
        "ingame": all(s.get("stage") == "STAGE_INGAME" for s in ok) if ok else False,
        "frame_advanced": bool(frames and frames[-1] > frames[0]),
        "frame_first": frames[0] if frames else None,
        "frame_last": frames[-1] if frames else None,
        "state_changed": bool(len({(s["frame"], s["objects"], s["cell_sum"], s["money"])
                                   for s in ok}) > 1),
        "object_counts": [s["objects"] for s in ok],
    }


async def main_async(opts):
    out = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "instances": [], "cross_check": {}, "liveness": {},
           "argv": sys.argv, "errors": []}
    audited = [audit_instance(i) for i in DEFAULT_INSTANCES]
    out["instances"] = audited
    out["cross_check"] = cross_check(audited)
    out["running_processes_before"] = [
        {"pid": p, "exe": "gamemd-spawn-ra2yrcpp.exe"}
        for p in winapi.find_processes_by_exe("gamemd-spawn-ra2yrcpp.exe")]
    sessions = []
    if not opts.skip_liveness:
        try:
            for inst in DEFAULT_INSTANCES:
                sess = GameSession(inst["index"], inst["game_dir"], inst["port"],
                                   run_dir=HEADLESS_ROOT, label=f"phase0-{inst['index']}")
                sess.launch()
                time.sleep(0.5)
                sessions.append(sess)
            await asyncio.gather(*[s.wait_ingame(120) for s in sessions])
            for sess in sessions:
                probe = await liveness_probe(sess, opts.probe_seconds)
                out["liveness"][str(sess.index)] = probe
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            out["errors"].append(f"{type(exc).__name__}: {exc}")
        finally:
            results = await asyncio.gather(
                *[asyncio.wait_for(s.close(), 25) for s in sessions],
                return_exceptions=True)
            for sess, res in zip(sessions, results):
                if isinstance(res, Exception):
                    out["errors"].append(f"close {sess.label}: {res}")
    out["running_processes_after"] = [
        p for p in winapi.find_processes_by_exe("gamemd-spawn-ra2yrcpp.exe")]
    out["conclusion"] = {
        "two_instances_ingame": all(
            v.get("ingame") for v in out["liveness"].values()) if out["liveness"] else None,
        "both_states_change": all(
            v.get("state_changed") and v.get("frame_advanced")
            for v in out["liveness"].values()) if out["liveness"] else None,
        "ports_unique": out["cross_check"].get("ports_unique"),
        "mutex_names_unique": out["cross_check"].get("mutex_names_unique"),
    }
    path = pathlib.Path(opts.out) if opts.out else (
        HEADLESS_ROOT / "phase0-instance-check.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out["conclusion"], ensure_ascii=False, indent=2))
    for index, probe in out["liveness"].items():
        print(f"instance {index}: ingame={probe['ingame']} "
              f"frames {probe['frame_first']}->{probe['frame_last']} "
              f"objects={probe['object_counts']} changed={probe['state_changed']} "
              f"read_ok={probe['read_ok']} fail={probe['read_fail']}")
    print(f"written: {path}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-liveness", action="store_true")
    ap.add_argument("--probe-seconds", type=float, default=8.0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(0 if main() else 0)
