"""Build a train/val dataset (npz) from episode JSONL files.

Requires numpy (training environment only; the game controller never imports this).

Usage:
  python scripts/dsh_train/dataset.py --episodes reports/dsh-learning/episodes \
      --out reports/dsh-learning/dataset --val-fraction 0.25
"""
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import numpy as np  # noqa: E402

from episode_io import iter_episodes  # noqa: E402
from features import build_obs, label_from_row  # noqa: E402
from spec import FEATURE_VERSION, N_ACTIONS  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--episodes", default="reports/dsh-learning/episodes")
    ap.add_argument("--out", default="reports/dsh-learning/dataset")
    ap.add_argument("--val-fraction", type=float, default=0.25)
    ap.add_argument("--noop-keep", type=float, default=0.2,
                    help="keep this fraction of NOOP samples (class imbalance)")
    ap.add_argument("--map-width", type=int, default=182)
    ap.add_argument("--map-height", type=int, default=182)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()

    rng = np.random.default_rng(a.seed)
    eps = list(iter_episodes(a.episodes))
    if not eps:
        raise SystemExit(f"no episodes under {a.episodes}")

    ids = sorted(ep["meta"].get("episode_id", ep["path"]) for ep in eps)
    val_ids = {i for i in ids if rng.random() < a.val_fraction}
    if ids and not val_ids:                     # ensure at least one val episode
        val_ids = {ids[-1]}

    def collect(ep_list):
        X, y, meta = [], [], []
        dropped_hidden = 0
        for ep in ep_list:
            eid = ep["meta"].get("episode_id", ep["path"])
            for row in ep["rows"]:
                aid, ok = label_from_row(row)
                if not ok:
                    dropped_hidden += 1
                    continue
                if aid == 0 and rng.random() > a.noop_keep:
                    continue
                X.append(build_obs(row, a.map_width, a.map_height))
                y.append(aid)
                meta.append(eid)
        return (np.asarray(X, dtype=np.float32), np.asarray(y, dtype=np.int64),
                meta, dropped_hidden)

    train_eps = [e for e in eps if e["meta"].get("episode_id", e["path"]) not in val_ids]
    val_eps = [e for e in eps if e["meta"].get("episode_id", e["path"]) in val_ids]
    if not train_eps:                            # tiny dataset guard
        train_eps, val_eps = eps, eps

    Xtr, ytr, _, drop_tr = collect(train_eps)
    Xva, yva, _, drop_va = collect(val_eps)
    if len(Xtr) < 4 or len(Xva) < 2:
        raise SystemExit(f"not enough samples: train={len(Xtr)} val={len(Xva)}")

    mean = Xtr.mean(axis=0)
    std = Xtr.std(axis=0)
    std[std < 1e-6] = 1.0
    Xtr_n = (Xtr - mean) / std
    Xva_n = (Xva - mean) / std

    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out / "dataset.npz",
                        X_train=Xtr_n, y_train=ytr, X_val=Xva_n, y_val=yva,
                        mean=mean, std=std)
    info = {
        "feature_version": FEATURE_VERSION,
        "n_actions": N_ACTIONS,
        "episodes_total": len(eps), "episodes_train": len(train_eps),
        "episodes_val": len(val_eps),
        "samples_train": int(len(Xtr)), "samples_val": int(len(Xva)),
        "dropped_hidden_target_orders": drop_tr + drop_va,
        "map": {"width": a.map_width, "height": a.map_height},
        "class_hist_train": np.bincount(ytr, minlength=N_ACTIONS).tolist(),
        "class_hist_val": np.bincount(yva, minlength=N_ACTIONS).tolist(),
    }
    (out / "info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    print(json.dumps(info, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
