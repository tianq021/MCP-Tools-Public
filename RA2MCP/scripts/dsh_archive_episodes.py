"""Archive unfinished raw recordings without fabricating a match result.

Default is a dry run. --apply requires exclusive access to the game lock.
Raw bytes/checkpoints are retained in _interrupted/ID with a hash manifest.
Re-running --apply also completes a partially committed archive operation.
"""
import argparse
import hashlib
import json
import os
import pathlib

from dsh_episode import lockfile, schema

ROOT = pathlib.Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_manifest(path, data):
    temporary = path.with_suffix(".json.tmp")
    with temporary.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temporary, path)


def archive(episodes, apply=False, lock_path=ROOT / "build/dsh-game.lock"):
    root = pathlib.Path(episodes).resolve()
    held = lockfile.acquire(lock_path) if apply else None
    records = []
    try:
        ids = {p.name.removesuffix(".jsonl.part") for p in root.glob("*.jsonl.part")}
        archive_root = root / "_interrupted"
        if archive_root.exists():
            for manifest in archive_root.glob("*/archive.json"):
                data = json.loads(manifest.read_text(encoding="utf-8"))
                if data.get("state") == "planned":
                    ids.add(data["episode_id"])
        for episode_id in sorted(ids):
            dest = (archive_root / episode_id).resolve()
            if not dest.is_relative_to(root) or dest == root:
                raise ValueError("archive target outside episode directory")
            manifest = dest / "archive.json"
            if manifest.exists():
                rec = json.loads(manifest.read_text(encoding="utf-8"))
            else:
                sources = [root / f"{episode_id}.jsonl.part", root / f"{episode_id}.ckpt.json"]
                rec = {"episode_id": episode_id, "kind": "interrupted",
                       "trainable": False, "state": "planned", "archived_at": schema.now_wall(),
                       "reason": "unfinished recording; game outcome unknown",
                       "files": [{"name": p.name, "sha256": digest(p), "bytes": p.stat().st_size}
                                 for p in sources if p.exists()]}
            if apply:
                dest.mkdir(parents=True, exist_ok=True)
                save_manifest(manifest, rec)
                for entry in rec["files"]:
                    name = entry["name"]
                    if pathlib.Path(name).name != name:
                        raise ValueError("invalid archive filename")
                    source, target = root / name, dest / name
                    if target.exists():
                        if digest(target) != entry["sha256"]:
                            raise ValueError(f"archive checksum mismatch: {target}")
                        if source.exists():
                            raise FileExistsError(f"source and archive both exist: {source}")
                    else:
                        if digest(source) != entry["sha256"]:
                            raise ValueError(f"source changed: {source}")
                        os.replace(source, target)
                rec["state"] = "archived"
                save_manifest(manifest, rec)
            records.append(rec)
        return records
    finally:
        if held:
            lockfile.release(held)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--episodes", default=str(ROOT / "reports/dsh-learning/episodes"))
    ap.add_argument("--apply", action="store_true")
    opts = ap.parse_args()
    print(json.dumps({"apply": opts.apply, "archives": archive(opts.episodes, opts.apply)}, indent=2))


if __name__ == "__main__":
    main()
