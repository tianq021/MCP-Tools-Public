"""Export exact teacher labels as review candidates, never auto-authorize training.

Preserves the existing 1801 feature contract plus raw observation references.
Tokenized V2 training and its type-map contract remain a separate gated reader.
No row-random split: one source episode stays one source episode.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from dual_channel.data import verify, sha, write_json


def export_candidates(run, out):
    run, out = pathlib.Path(run), pathlib.Path(out)
    if out.exists():
        raise FileExistsError("dataset export output must be new")
    source = run / "dual-channel"
    checked = verify(source)
    if not checked["pass"]:
        raise ValueError("source integrity failed")
    summary = json.loads((source / "summary.json").read_text(encoding="utf-8"))
    if summary["failure"] or summary["terminal"]["conflict"]:
        raise ValueError("environment failure or contradictory outcome")
    records = [json.loads(l) for l in (source / "transitions.jsonl").read_text(encoding="utf-8").splitlines()]
    candidates = [r for r in records if r["admission"]["bc_label_eligible"]]
    if not candidates:
        raise ValueError("no admissible exact action labels")
    x = np.asarray([r["features"] for r in candidates], dtype=np.float32)
    y = np.asarray([r["action_idx"] for r in candidates], dtype=np.int64)
    masks = np.asarray([r["action_mask"] for r in candidates], dtype=np.bool_)
    if x.ndim != 2 or x.shape[1] != 1801 or not np.isfinite(x).all():
        raise ValueError("frozen 1801 feature contract mismatch")
    if not masks[np.arange(len(y)), y].all():
        raise ValueError("masked label")
    out.mkdir(parents=True)
    np.savez_compressed(out / "teacher_candidates.npz", observations=x, actions=y, action_masks=masks,
                        frames=np.asarray([r["observation_frame"] for r in candidates], np.int64))
    write_json(out / "labels.json", [{"episode_id": r["episode_id"], "decision_id": r["decision_id"],
                                      "skill_instance_id": r["skill_instance_id"], "skill_id": r["skill_id"],
                                      "source": r["source"], "teacher_sha256": r["teacher_sha256"]} for r in candidates])
    manifest = {"schema": "ra2.teacher-dataset-candidates/1", "status": "CANDIDATE_REVIEW_REQUIRED",
                "source_summary_sha256": sha(source / "summary.json"), "source_trace_sha256": sha(source / "transitions.jsonl"),
                "source_episode": summary["episode_id"], "source_feature_contract": "frozen_full_ground_1801",
                "samples": len(y), "features": x.shape[1], "action_n": 301,
                "episode_split": {"unassigned": [summary["episode_id"]], "train": [], "validation": [], "test": []},
                "formal_training_eligible": False, "ppo_eligible": False,
                "reasons": ["teacher quality not independently certified", "single episode: no independent validation/test split"],
                "files": {p.name: {"sha256": sha(p), "size": p.stat().st_size} for p in out.iterdir() if p.is_file()}}
    write_json(out / "manifest.json", manifest)
    return manifest


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    print(json.dumps(export_candidates(a.run, a.out), ensure_ascii=True))
