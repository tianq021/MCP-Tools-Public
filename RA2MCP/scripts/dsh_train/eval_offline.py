"""Offline evaluation + inference latency benchmark for an exported model dir.

Works without torch (uses weights.npz + norm.json), so it can run in the game
controller environment as a deployment check.

Usage:
  python scripts/dsh_train/eval_offline.py --model reports/dsh-learning/models/bc-v1 \
      --data reports/dsh-learning/dataset
"""
import argparse
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import numpy as np  # noqa: E402

import model as M  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="reports/dsh-learning/models/bc-v1")
    ap.add_argument("--data", default="reports/dsh-learning/dataset")
    ap.add_argument("--bench-n", type=int, default=2000)
    a = ap.parse_args()

    mdir = pathlib.Path(a.model)
    weights = M.load_weights_npz(mdir / "weights.npz")
    norm = json.loads((mdir / "norm.json").read_text(encoding="utf-8"))
    metrics_meta = json.loads((mdir / "metrics.json").read_text(encoding="utf-8"))

    with np.load(pathlib.Path(a.data) / "dataset.npz") as z:
        Xva = z["X_val"]
        yva = z["y_val"]

    pred, probs = M.numpy_predict(weights, Xva)
    acc = float((pred == yva).mean())
    n_actions = int(norm["n_actions"])
    confusion = [[0] * n_actions for _ in range(n_actions)]
    for t, p in zip(yva.tolist(), pred.tolist()):
        confusion[t][p] += 1
    f1s = []
    for c in range(n_actions):
        tp = confusion[c][c]
        fp = sum(confusion[r][c] for r in range(n_actions)) - tp
        fn = sum(confusion[c]) - tp
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1s.append(2 * prec * rec / (prec + rec) if prec + rec else 0.0)

    # single-sample latency (what the game controller actually does)
    lat = []
    for i in range(min(a.bench_n, len(Xva))):
        x = Xva[i:i + 1]
        t0 = time.perf_counter()
        _ = M.numpy_predict(weights, x)
        lat.append((time.perf_counter() - t0) * 1000.0)
    lat.sort()
    p50 = lat[len(lat) // 2] if lat else None
    p95 = lat[int(len(lat) * 0.95)] if lat else None

    out = {
        "model_dir": str(mdir),
        "feature_version": norm["feature_version"],
        "split": "val",
        "samples": int(len(Xva)),
        "accuracy": round(acc, 4),
        "macro_f1": round(float(np.mean(f1s)), 4),
        "per_class_f1": {str(i): round(v, 4) for i, v in enumerate(f1s)},
        "confusion": confusion,
        "latency_ms": {"p50": round(p50, 4) if p50 else None,
                       "p95": round(p95, 4) if p95 else None,
                       "n": len(lat)},
        "trained_metrics_summary": {
            "backend": metrics_meta.get("backend"),
            "train_seconds": metrics_meta.get("train_seconds"),
            "val_acc_at_training": metrics_meta.get("val_metrics", {}).get("accuracy"),
        },
    }
    (mdir / "eval.json").write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
