"""Ingest one existing observer recording into SQLite + CSV + Markdown (offline, no game).

Usage:
  python scripts/observer/ingest_recording.py \
      --recording reports/ai-teacher/wdzs-godstoy/live-full/20261030-183814-godstoy \
      --out-dir reports/observer-collect-2026-11-03/poc-existing-godstoy \
      --match-id godstoy-20261030-183814 --map "God's Toy / 咫尺天涯" \
      --exclude-names Neutral,Special

The recording directory must contain ``states_full.jsonl`` (or ``states_full.jsonl.gz``).
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from observer import extract, store  # noqa: E402


def find_recording(recording: pathlib.Path) -> pathlib.Path:
    recording = pathlib.Path(recording)
    if recording.is_file():
        return recording
    for name in ("states_full.jsonl.gz", "states_full.jsonl", "states.jsonl.gz",
                 "states.jsonl", "observations.jsonl.gz", "observations.jsonl"):
        candidate = recording / name
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"no recording jsonl found in {recording}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recording", required=True,
                        help="recording file or directory containing states_full.jsonl")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--match-id", default=None)
    parser.add_argument("--map", dest="map_name", default="UNKNOWN")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--observer-indices", default="",
                        help="comma separated house indices acting as observer (excluded)")
    parser.add_argument("--exclude-names", default="Neutral,Special")
    parser.add_argument("--db", default=None, help="SQLite path (default <out-dir>/observer.sqlite)")
    args = parser.parse_args(argv)

    recording = pathlib.Path(args.recording)
    source = find_recording(recording)
    out_dir = pathlib.Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    match_id = args.match_id or f"{source.parent.name}-{source.stem}"
    observer_indices = tuple(int(x) for x in args.observer_indices.split(",") if x.strip())
    exclude = frozenset(x.strip() for x in args.exclude_names.split(",") if x.strip())

    result = extract.extract_recording(
        source, match_id=match_id, map_name=args.map_name, seed=args.seed,
        observer_indices=observer_indices, exclude_names=exclude,
        source_label=source.parent.name,
    )
    artifacts = extract.write_extract_artifacts(result, out_dir)

    db_path = pathlib.Path(args.db) if args.db else out_dir / "observer.sqlite"
    conn = store.connect(db_path)
    try:
        store.insert_match(conn, result)
        csv_files = store.export_csv(conn, out_dir / "csv")
    finally:
        conn.close()

    (out_dir / "MATCH_REPORT.md").write_text(store.match_report(result), encoding="utf-8")
    summary = {
        "match_id": match_id,
        "source": str(source),
        "out_dir": str(out_dir),
        "db": str(db_path),
        "csv": csv_files,
        "participants": result["participants"],
        "winner": result["winner_name"],
        "end_reason": result["end_reason"],
        "first_defeated": result["first_defeated_name"],
        "first_defeat_frame": result["first_defeat_frame"],
        "first_defeat_sim_s": result["first_defeat_sim_s"],
        "sample_count": result["sample_count"],
        "fps_estimate": result["fps_estimate"],
        "elimination_order": result["elimination_order"],
        "artifacts": artifacts,
    }
    (out_dir / "INGEST_SUMMARY.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
