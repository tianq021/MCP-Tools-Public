"""Behavior-cloning trainer (v1 MLP) — framework-agnostic entry point.

Usage (run with a training environment that has numpy; torch is optional):
  F:\\conda\\envs\\python_mmx_LLM\\python.exe scripts/dsh_train/train_bc.py \
      --data reports/dsh-learning/dataset --out reports/dsh-learning/models/bc-v1 \
      --backend auto --epochs 40

Backend selection:
  auto  -> torch if importable, else numpy
  torch -> CUDA MLP training (exports .pt + .npz + .json)
  numpy -> pure-numpy MLP training (exports .npz + .json)
"""
import argparse
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import numpy as np  # noqa: E402

import model as M  # noqa: E402
from spec import FEATURE_VERSION, MODEL_FORMAT_VERSION  # noqa: E402


def load_dataset(data_dir):
    data_dir = pathlib.Path(data_dir)
    with np.load(data_dir / "dataset.npz") as z:
        d = {k: z[k] for k in z.files}
    info = json.loads((data_dir / "info.json").read_text(encoding="utf-8"))
    return d, info


def metrics(y_true, y_pred, n_actions):
    acc = float((y_pred == y_true).mean()) if len(y_true) else 0.0
    f1s = []
    confusion = [[0] * n_actions for _ in range(n_actions)]
    for t, p in zip(y_true.tolist(), y_pred.tolist()):
        confusion[t][p] += 1
    for c in range(n_actions):
        tp = sum(1 for t, p in zip(y_true.tolist(), y_pred.tolist()) if t == c and p == c)
        fp = sum(1 for t, p in zip(y_true.tolist(), y_pred.tolist()) if t != c and p == c)
        fn = sum(1 for t, p in zip(y_true.tolist(), y_pred.tolist()) if t == c and p != c)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1s.append(2 * prec * rec / (prec + rec) if prec + rec else 0.0)
    return {"accuracy": acc, "macro_f1": float(np.mean(f1s)), "confusion": confusion}


def train_torch(d, info, args):
    import torch
    import torch.nn as nn

    device = "cuda" if torch.cuda.is_available() else "cpu"
    obs_dim = d["X_train"].shape[1]
    n_actions = info["n_actions"]
    model = M.TorchMLP(obs_dim, args.hidden, n_actions).to(device)
    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    loss_fn = nn.CrossEntropyLoss()

    Xtr = torch.from_numpy(d["X_train"]).float()
    ytr = torch.from_numpy(d["y_train"]).long()
    Xva = torch.from_numpy(d["X_val"]).float()
    yva = torch.from_numpy(d["y_val"]).long()

    bs = args.batch
    n = len(Xtr)
    history = []
    best = {"val_acc": -1.0, "state": None, "epoch": -1}
    for ep in range(args.epochs):
        model.train()
        perm = torch.randperm(n)
        total = 0.0
        for i in range(0, n, bs):
            idx = perm[i:i + bs]
            xb, yb = Xtr[idx].to(device), ytr[idx].to(device)
            opt.zero_grad()
            out = model(xb)
            loss = loss_fn(out, yb)
            loss.backward()
            opt.step()
            total += float(loss.detach()) * len(xb)
        model.eval()
        with torch.no_grad():
            pred = model(Xva.to(device)).argmax(dim=1).cpu().numpy()
        acc = float((pred == yva.numpy()).mean())
        history.append({"epoch": ep + 1, "loss": total / n, "val_acc": acc})
        if args.verbose:
            print(f"  epoch {ep + 1:3d}/{args.epochs} loss={total / n:.4f} val_acc={acc:.4f}")
        if acc > best["val_acc"]:
            best = {"val_acc": acc, "epoch": ep + 1,
                    "state": {k: v.detach().clone() for k, v in model.state_dict().items()}}
    if best["state"] is not None:
        model.load_state_dict(best["state"])
    model.eval()
    with torch.no_grad():
        pred_tr = model(Xtr.to(device)).argmax(dim=1).cpu().numpy()
    weights_np = M.torch_to_numpy(model)
    return model, weights_np, {
        "backend": "torch", "device": device,
        "torch_version": torch.__version__,
        "best_epoch": best["epoch"], "best_val_acc": best["val_acc"],
        "train_metrics": metrics(d["y_train"], pred_tr, n_actions),
        "history": history,
    }


def train_numpy(d, info, args):
    obs_dim = d["X_train"].shape[1]
    n_actions = info["n_actions"]
    weights = M.init_numpy_mlp(obs_dim, args.hidden, n_actions, seed=args.seed)
    weights, hist = M.numpy_train(weights, d["X_train"], d["y_train"],
                                  d["X_val"], d["y_val"], epochs=args.epochs,
                                  lr=args.lr, batch=args.batch, seed=args.seed)
    pred_tr, _ = M.numpy_predict(weights, d["X_train"])
    pred_va, _ = M.numpy_predict(weights, d["X_val"])
    return weights, {
        "backend": "numpy", "best_epoch": hist["best_epoch"],
        "best_val_acc": hist["val_acc"],
        "train_metrics": metrics(d["y_train"], pred_tr, n_actions),
        "val_metrics": metrics(d["y_val"], pred_va, n_actions),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="reports/dsh-learning/dataset")
    ap.add_argument("--out", default="reports/dsh-learning/models/bc-v1")
    ap.add_argument("--backend", default="auto", choices=["auto", "torch", "numpy"])
    ap.add_argument("--hidden", type=int, default=128)
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    d, info = load_dataset(args.data)
    n_actions = info["n_actions"]
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    backend = args.backend
    if backend == "auto":
        backend = "torch" if M.torch_available() else "numpy"
    if backend == "torch" and not M.torch_available():
        print("torch unavailable, falling back to numpy")
        backend = "numpy"

    if backend == "torch":
        net, weights_np, train_info = train_torch(d, info, args)
        import torch
        torch.save(net.state_dict(), out / "model.pt")
    else:
        weights_np, train_info = train_numpy(d, info, args)

    # evaluate on validation with the exported weights (both backends agree)
    pred_va, probs = M.numpy_predict(weights_np, d["X_val"])
    val_metrics = metrics(d["y_val"], pred_va, n_actions)

    M.save_weights_npz(out / "weights.npz", weights_np)
    M.save_weights_json(out / "weights.json", weights_np)
    norm = {"mean": d["mean"].tolist(), "std": d["std"].tolist(),
            "feature_version": FEATURE_VERSION,
            "obs_dim": int(d["X_train"].shape[1]), "n_actions": n_actions}
    (out / "norm.json").write_text(json.dumps(norm), encoding="utf-8")
    meta = {
        "model_format_version": MODEL_FORMAT_VERSION,
        "feature_version": FEATURE_VERSION,
        "obs_dim": int(d["X_train"].shape[1]),
        "hidden": args.hidden, "n_actions": n_actions,
        "backend": train_info.get("backend"),
        "device": train_info.get("device"),
        "torch_version": train_info.get("torch_version"),
        "best_epoch": train_info.get("best_epoch"),
        "train_samples": int(len(d["y_train"])),
        "val_samples": int(len(d["y_val"])),
        "train_seconds": round(time.time() - t0, 2),
        "train_metrics": train_info.get("train_metrics"),
        "val_metrics": val_metrics,
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "data_info": info,
    }
    (out / "metrics.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    summary = {
        "model_dir": str(out), "backend": meta["backend"],
        "obs_dim": meta["obs_dim"], "hidden": meta["hidden"],
        "train_samples": meta["train_samples"], "val_samples": meta["val_samples"],
        "val_accuracy": round(val_metrics["accuracy"], 4),
        "val_macro_f1": round(val_metrics["macro_f1"], 4),
        "train_seconds": meta["train_seconds"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
