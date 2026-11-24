"""Smoke-test behaviour cloning on the scripted-teacher dataset.

Trains a small MLP to predict the teacher's macro intent from the observation
and reports validation accuracy vs majority/random baselines. This is the
"can the model learn" gate, not a production trainer.

  .venv\\Scripts\\python.exe scripts\\dsh_rl\\bc_smoke.py --data <dataset.npz>
"""
import argparse
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from dsh_rl.scripted_policy import LABELS  # noqa: E402

LABEL_NAMES = LABELS


def load(data):
    d = np.load(data)
    obs = d["obs"].astype(np.float32)
    labels = d["labels"].astype(np.int64)
    episodes = d["episodes"].astype(np.int64)
    return obs, labels, episodes


def split(obs, labels, episodes):
    unique = sorted(set(episodes.tolist()))
    if len(unique) >= 2:
        val_eps = {unique[-1]}
        train_mask = ~np.isin(episodes, list(val_eps))
        val_mask = np.isin(episodes, list(val_eps))
        return (obs[train_mask], labels[train_mask]), (obs[val_mask], labels[val_mask]), "by-episode"
    rng = np.random.default_rng(0)
    order = rng.permutation(len(obs))
    cut = int(len(obs) * 0.8)
    return (obs[order[:cut]], labels[order[:cut]]), (obs[order[cut:]], labels[order[cut:]]), "by-sample"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--epochs", type=int, default=200)
    a = ap.parse_args()
    data = pathlib.Path(a.data)
    out = pathlib.Path(a.out or data.parent)
    out.mkdir(parents=True, exist_ok=True)

    obs, labels, episodes = load(data)
    (train_x, train_y), (val_x, val_y), mode = split(obs, labels, episodes)
    print(f"[bc] samples={len(labels)} train={len(train_y)} val={len(val_y)} split={mode} "
          f"obs_dim={obs.shape[1]}")

    counts = np.bincount(labels, minlength=len(LABEL_NAMES))
    print("[bc] label distribution:", {LABEL_NAMES[i]: int(c) for i, c in enumerate(counts) if c})
    majority = int(np.argmax(np.bincount(train_y, minlength=len(LABEL_NAMES))))
    majority_acc = float((val_y == majority).mean()) if len(val_y) else 0.0
    random_acc = 1.0 / float(len(set(labels.tolist())))
    print(f"[bc] baselines: majority={majority_acc:.3f} random={random_acc:.3f}")

    try:
        import torch
        import torch.nn as nn
    except ImportError:
        print("[bc] torch not available; install torch in .venv to run the smoke test")
        return

    torch.manual_seed(0)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = nn.Sequential(
        nn.Linear(obs.shape[1], 256), nn.ReLU(), nn.Dropout(0.1),
        nn.Linear(256, 128), nn.ReLU(),
        nn.Linear(128, len(LABEL_NAMES)),
    ).to(device)
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    loss_fn = nn.CrossEntropyLoss()
    tx = torch.tensor(train_x, device=device)
    ty = torch.tensor(train_y, device=device)
    vx = torch.tensor(val_x, device=device)
    vy = torch.tensor(val_y, device=device)
    batch = 128
    best = {"epoch": 0, "val_acc": 0.0}
    history = []
    for epoch in range(1, a.epochs + 1):
        model.train()
        perm = torch.randperm(len(tx), device=device)
        total = 0.0
        for i in range(0, len(tx), batch):
            idx = perm[i:i + batch]
            opt.zero_grad()
            loss = loss_fn(model(tx[idx]), ty[idx])
            loss.backward()
            opt.step()
            total += float(loss.item()) * len(idx)
        model.eval()
        with torch.no_grad():
            train_acc = float((model(tx).argmax(1) == ty).float().mean().item())
            val_acc = float((model(vx).argmax(1) == vy).float().mean().item()) if len(vx) else 0.0
        if val_acc > best["val_acc"]:
            best = {"epoch": epoch, "val_acc": val_acc, "train_acc": train_acc}
        if epoch % 20 == 0 or epoch == 1:
            history.append({"epoch": epoch, "loss": round(total / max(1, len(tx)), 4),
                            "train_acc": round(train_acc, 4), "val_acc": round(val_acc, 4)})
            print(f"[bc] epoch {epoch:3d} loss={total / max(1, len(tx)):.4f} "
                  f"train_acc={train_acc:.3f} val_acc={val_acc:.3f}", flush=True)

    # per-class validation accuracy for the top classes
    model.eval()
    with torch.no_grad():
        pred = model(vx).argmax(1).cpu().numpy() if len(vx) else np.asarray([])
    per_class = {}
    for cls in sorted(set(val_y.tolist())):
        mask = val_y == cls
        per_class[LABEL_NAMES[cls]] = round(float((pred[mask] == cls).mean()), 3)

    report = {"data": str(data), "samples": int(len(labels)), "split": mode,
              "train": int(len(train_y)), "val": int(len(val_y)),
              "obs_dim": int(obs.shape[1]), "epochs": a.epochs,
              "label_distribution": {LABEL_NAMES[i]: int(c) for i, c in enumerate(counts) if c},
              "baselines": {"majority": majority_acc, "random": random_acc},
              "best": best, "final_history": history, "per_class_val_acc": per_class,
              "device": device}
    (out / "bc-smoke.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    verdict = "LEARNABLE" if best["val_acc"] > max(0.5, majority_acc + 0.15) else "WEAK"
    print(f"[bc] best epoch={best['epoch']} val_acc={best['val_acc']:.3f} "
          f"(majority={majority_acc:.3f}, random={random_acc:.3f}) -> {verdict}")
    print(f"[bc] wrote {out / 'bc-smoke.json'}")


if __name__ == "__main__":
    main()
