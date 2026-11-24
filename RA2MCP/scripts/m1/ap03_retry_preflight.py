"""AP-03 retry preflight (plan A): Medium-IL conda python + venv site-packages.

Run with: F:\\conda\\...\\python.exe (NOT .venv-rl python). Non-game only:
integrity check, native module paths, write probes, lock acquire/release,
game fingerprint, protection hashes. No ACL/registry changes; no game launch.
"""
import json
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(r"E:\RA2MCP")
OUT = ROOT / "reports" / "m1-2026-11-01" / "p4a-retry"
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / ".venv-rl" / "Lib" / "site-packages"))

EXPECTED = {
    "reports/rl/stage-A-round1-20261030-153225/model.zip": "f650f4fd1a21f236116426bfe9a38b71d1c63334836e2303337f0ef7127cea8a",
    "reports/rl/stage-A-round2-20261030-155824/model.zip": "c340d9ccead7c9a1b9add2a37fee990e2caee5c3c7c2c316dc79d2010c2ea542",
    "reports/rl/stage-A-round3-20261030-162536/model.zip": "0c9c75d7ecc4c360b6c165b1b224f10491bc13dad9aeea4b3b84abd8d6511ce8",
    "reports/rl/ppo-shared-20261030-102633/model.zip": "f8380e8bd55d2454179ca1955682f1d869356f03d51fa19c11a2f6a4dc6f1b56",
    "config/rewards-combat.json": "2aced7dd305716737711da10febb16e33093467284569abaa7f52912ae021e8b",
}


def sha(p):
    import hashlib
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


res = {"timestamp": time.strftime("%F %T"), "python": sys.executable, "preflight": {}}
# 1) integrity (actual launch chain: this interpreter under DSH pwsh)
g = subprocess.run(["whoami", "/groups"], capture_output=True, text=True)
res["preflight"]["integrity_medium"] = "S-1-16-8192" in g.stdout
res["preflight"]["integrity_low"] = "S-1-16-4096" in g.stdout
# 2) imports + native module paths
mods = {}
for name in ("gymnasium", "sb3_contrib", "ra2yrproto", "numpy", "torch"):
    try:
        m = __import__(name)
        mods[name] = {"version": getattr(m, "__version__", "?"), "file": getattr(m, "__file__", "?")}
    except Exception as e:  # noqa: BLE001
        mods[name] = {"error": repr(e)}
res["preflight"]["modules"] = mods
try:
    from dsh_rl import full_env, port_config  # noqa: F401
    from dsh_episode import runner, lockfile
    res["preflight"]["dsh_imports"] = "OK"
    res["preflight"]["fingerprint"] = runner.env_fingerprint()
except Exception as e:  # noqa: BLE001
    res["preflight"]["dsh_imports"] = "FAIL " + repr(e)
# native extension files under venv site-packages
sp = ROOT / ".venv-rl" / "Lib" / "site-packages"
res["preflight"]["native_ext_sample"] = [str(p.relative_to(sp)) for p in list(sp.glob("**/*.pyd"))[:20]]
# 3) write probes (create+delete; never overwrite)
probes = {}
for rel in ("build", "reports", "test-game"):
    d = ROOT / rel
    p = d / f"_ap03_probe_{int(time.time()*1000)}.tmp"
    try:
        p.write_text("probe"); p.unlink()
        probes[rel] = "OK"
    except Exception as e:  # noqa: BLE001
        probes[rel] = repr(e)
res["preflight"]["write_probes"] = probes
# 4) lock acquire/release using project lockfile (custom name; do not touch unknown locks)
try:
    lock_path = ROOT / "build" / "dsh-ap03-preflight.lock"
    if lock_path.exists():
        res["preflight"]["lock"] = "EXISTING_UNKNOWN_LOCK_NOT_DELETED"
    else:
        lockfile.acquire(lock_path, 30)
        try:
            ok_held = lock_path.exists()
        finally:
            lockfile.release(lock_path)
        res["preflight"]["lock"] = "ACQUIRE_RELEASE_OK" if ok_held and not lock_path.exists() else "LOCK_LEFTOVER"
except Exception as e:  # noqa: BLE001
    res["preflight"]["lock"] = "FAIL " + repr(e)
# 5) protection hashes
prot = {}
for rel, exp in EXPECTED.items():
    p = ROOT / rel
    prot[rel] = {"exists": p.exists(), "sha_ok": p.exists() and sha(p) == exp}
res["preflight"]["protection"] = prot
# 6) no game processes / ports
try:
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq gamemd-spawn-ra2yrcpp.exe"],
                         capture_output=True, text=True).stdout
    res["preflight"]["gamemd_running"] = "gamemd-spawn-ra2yrcpp.exe" in out
except Exception:  # noqa: BLE001
    res["preflight"]["gamemd_running"] = "UNKNOWN"

res["ok"] = (res["preflight"]["integrity_medium"] and not res["preflight"]["integrity_low"]
             and res["preflight"]["dsh_imports"] == "OK"
             and all(v == "OK" for v in probes.values())
             and res["preflight"]["lock"] == "ACQUIRE_RELEASE_OK"
             and all(v["sha_ok"] for v in prot.values())
             and res["preflight"]["gamemd_running"] is False)
(OUT / "preflight.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(res, ensure_ascii=False, indent=1)[:3500])
sys.exit(0 if res["ok"] else 2)
