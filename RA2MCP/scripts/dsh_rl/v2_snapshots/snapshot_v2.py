"""V2 snapshot tool: archive source+config+tests+parent+hashes."""
import hashlib, json, pathlib, shutil, sys, time
ROOT = pathlib.Path(__file__).resolve().parents[3]
SRC = ROOT / "scripts/dsh_rl/scripted_policy_v2.py"
SNAP = ROOT / "scripts/dsh_rl/v2_snapshots"
def sha(p): return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()
def snapshot(version, parent, test_logs):
    SNAP.mkdir(parents=True, exist_ok=True)
    dst = SNAP / (version + ".py"); shutil.copyfile(SRC, dst)
    meta = {"version": version, "parent": parent, "source_sha256": sha(dst),
            "source_bytes": dst.stat().st_size, "action_n": 301,
            "test_logs": test_logs, "deps": {"python": sys.version.split()[0]},
            "captured_at": time.strftime("%F %T")}
    (SNAP / (version + ".json")).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta
if __name__ == "__main__":
    logs = sys.argv[1:] or []
    m = snapshot("v2_" + time.strftime("%Y%m%d-%H%M%S"), "failed_866a69e2", logs)
    print(json.dumps(m, ensure_ascii=False))
