"""Learn concrete (301) actions from the previously collected teacher dataset.

Unlike bc_smoke.py (8 macro intents) this trains directly on the full action
contract with the recorded legality mask, i.e. "learn from the data" instead of
hand-crafting more policy code.

  .venv-rl python:  F:\\conda\\envs\\python_mmx_LLM\\python.exe  (+ PYTHONPATH=.venv-rl site-packages)
"""
import argparse
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from dsh_rl.scripted_policy import intent_label, LABELS  # noqa: E402


def load(data):
    d = np.load(data)
    return (d["obs"].astype(np.float32), d["actions"].astype(np.int64),
            d["masks"].astype(bool), d["episodes"].astype(np.int64))


def split_by_episode(episodes):
    unique = sorted(set(episodes.tolist()))
    if len(unique) >= 2:
        val = {unique[-1]}
        tr = ~np.isin(episodes, list(val))
        va = np.isin(episodes, list(val))
        return tr, va, "by-episode"
    rng = np.random.default_rng(0)
    order = rng.permutation(len(episodes))
    cut = int(len(episodes) * 0.8)
    tr = np.zeros(len(episodes), bool)
    tr[order[:cut]] = True
    return tr, ~tr, "by-sample"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--epochs", type=int, default=300)
    a = ap.parse_args()
    data = pathlib.Path(a.data)
    out = pathlib.Path(a.out or data.parent)
    out.mkdir(parents=True, exist_ok=True)

    obs, actions, masks, episodes = load(data)
    tr, va, mode = split_by_episode(episodes)
    tx, ty, tm = obs[tr], actions[tr], masks[tr]
    vx, vy, vm = obs[va], actions[va], masks[va]
    print(f"[bc-actions] samples={len(actions)} train={len(ty)} val={len(vy)} split={mode} "
          f"obs_dim={obs.shape[1]} action_n={masks.shape[1]}")

    # baselines
    valid_counts = masks.sum(1)
    random_valid = float((1.0 / np.maximum(valid_counts, 1)).mean())
    noop_share = float((actions == 0).mean())
    legal_action_rate = float(masks[np.arange(len(actions)), actions].mean())
    print(f"[bc-actions] baselines: masked-random={random_valid:.3f} "
          f"noop-share={noop_share:.3f} legal-rate={legal_action_rate:.3f}")

    try:
        import torch
        import torch.nn as nn
        import torch.nn.functional as F
    except ImportError:
        print("[bc-actions] torch unavailable")
        return

    torch.manual_seed(0)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = nn.Sequential(
        nn.Linear(obs.shape[1], 512), nn.ReLU(), nn.Dropout(0.1),
        nn.Linear(512, 256), nn.ReLU(),
        nn.Linear(256, 301),
    ).to(device)
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)

    TX = torch.tensor(tx, device=device)
    TY = torch.tensor(ty, device=device)
    TM = torch.tensor(tm, device=device)
    VX = torch.tensor(vx, device=device)
    VY = torch.tensor(vy, device=device)
    VM = torch.tensor(vm, device=device)
    NEG = torch.finfo(torch.float32).min / 2

    def masked_logits(x, mask):
        logits = model(x)
        return logits.masked_fill(~mask, NEG)

    batch = 128
    history = []
    best = {"epoch": 0, "val_acc": 0.0}
    for epoch in range(1, a.epochs + 1):
        model.train()
        perm = torch.randperm(len(TX), device=device)
        total = 0.0
        for i in range(0, len(TX), batch):
            idx = perm[i:i + batch]
            opt.zero_grad()
            logits = masked_logits(TX[idx], TM[idx])
            loss = F.cross_entropy(logits, TY[idx])
            loss.backward()
            opt.step()
            total += float(loss.item()) * len(idx)
        model.eval()
        with torch.no_grad():
            tpred = masked_logits(TX, TM).argmax(1)
            vpred = masked_logits(VX, VM).argmax(1)
            train_acc = float((tpred == TY).float().mean().item())
            val_acc = float((vpred == VY).float().mean().item())
        if val_acc > best["val_acc"]:
            best = {"epoch": epoch, "val_acc": val_acc, "train_acc": train_acc}
        if epoch == 1 or epoch % 30 == 0:
            history.append({"epoch": epoch, "loss": round(total / max(1, len(TX)), 4),
                            "train_acc": round(train_acc, 4), "val_acc": round(val_acc, 4)})
            print(f"[bc-actions] epoch {epoch:3d} loss={total / max(1, len(TX)):.4f} "
                  f"train_acc={train_acc:.3f} val_acc={val_acc:.3f}", flush=True)

    # intent-level confusion on validation
    with torch.no_grad():
        pred = masked_logits(VX, VM).argmax(1).cpu().numpy()
        pred_intent = np.array([LABELS.index(intent_label(int(p))) for p in pred])
        true_intent = np.array([LABELS.index(intent_label(int(t))) for t in vy])
    intent_acc = float((pred_intent == true_intent).mean())
    per_true_intent = {}
    for cls in sorted(set(true_intent.tolist())):
        m = true_intent == cls
        per_true_intent[LABELS[cls]] = round(float((pred_intent[m] == cls).mean()), 3)

    report = {"data": str(data), "samples": int(len(actions)), "split": mode,
              "train": int(len(ty)), "val": int(len(vy)), "obs_dim": int(obs.shape[1]),
              "baselines": {"masked_random": random_valid, "noop_share": noop_share,
                            "legal_action_rate": legal_action_rate},
              "best": best, "history": history,
              "val_intent_acc": intent_acc, "per_intent_recall": per_true_intent,
              "device": device}
    (out / "bc-actions.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    print(f"[bc-actions] best epoch={best['epoch']} val_acc={best['val_acc']:.3f} "
          f"(masked_rand={random_valid:.3f}, noop_share={noop_share:.3f})")
    print(f"[bc-actions] intent-level val acc={intent_acc:.3f}, recalls={per_true_intent}")
    print(f"[bc-actions] wrote {out / 'bc-actions.json'}")


if __name__ == "__main__":
    main()
