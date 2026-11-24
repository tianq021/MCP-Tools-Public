"""Multi-head behavioral-cloning trainer for the v2 interface.

Heads (all derived from the same trunk):
  class   : 8 action classes (NOOP/MOVE/ATTACK/STOP/RETREAT/DEPLOY/PRODUCE/PLACE)
  target  : SLOTS_ENEMY+1 (0 = no target, i+1 = visible-enemy slot i)
  move    : 2 regression values (continuous dx/dy in 30-cell units)
  produce : n_types+1 (0 = none, i+1 = type catalog id i)
  place   : 2 regression values (cell offsets from the construction yard)

Usage (training env, torch 2.5.1+cu121):
  F:\\conda\\envs\\python_mmx_LLM\\python.exe scripts/dsh_train/train_bc_v2.py \
      --data reports/dsh-learning/dataset-v2 --out reports/dsh-learning/models/bc-v2-demo
"""
import argparse
import json
import pathlib
import sys
import time
import hashlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import numpy as np  # noqa: E402

import spec_v2 as S  # noqa: E402


def load_dataset(data_dir):
    data_dir = pathlib.Path(data_dir)
    with np.load(data_dir / "dataset_v2.npz") as z:
        d = {k: z[k] for k in z.files}
    info = json.loads((data_dir / "info.json").read_text(encoding="utf-8"))
    return d, info


def metrics(y_class, pred_class, y_target, pred_target, y_type, pred_type,
            y_mdx, pred_mdx, y_mdy, pred_mdy):
    class_acc = float((pred_class == y_class).mean()) if len(y_class) else 0.0
    f1s = []
    for c in range(S.N_ACTION_CLASSES):
        tp = int(((y_class == c) & (pred_class == c)).sum())
        fp = int(((y_class != c) & (pred_class == c)).sum())
        fn = int(((y_class == c) & (pred_class != c)).sum())
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1s.append(2 * prec * rec / (prec + rec) if prec + rec else 0.0)
    atk = y_class == S.ACTION_CLASS_IDS["ATTACK"]
    prod = y_class == S.ACTION_CLASS_IDS["PRODUCE"]
    move = y_class == S.ACTION_CLASS_IDS["MOVE"]
    return {
        "class_accuracy": round(class_acc, 4),
        "class_macro_f1": round(float(np.mean(f1s)), 4),
        "target_accuracy_on_attack": round(
            float((pred_target[atk] == y_target[atk]).mean()), 4) if atk.any() else None,
        "produce_type_accuracy": round(
            float((pred_type[prod] == y_type[prod]).mean()), 4) if prod.any() else None,
        "move_mae": round(float(np.abs(pred_mdx[move] - y_mdx[move]).mean()
                             + np.abs(pred_mdy[move] - y_mdy[move]).mean()) / 2.0, 4)
        if move.any() else None,
        "n": int(len(y_class)),
        "class_hist": np.bincount(y_class, minlength=S.N_ACTION_CLASSES).tolist(),
    }


def build_torch_model(obs_dim, hidden, n_types):
    import torch
    import torch.nn as nn

    class Net(nn.Module):
        def __init__(self):
            super().__init__()
            self.trunk = nn.Sequential(
                nn.Linear(obs_dim, hidden), nn.ReLU(),
                nn.Linear(hidden, hidden), nn.ReLU(),
            )
            self.class_head = nn.Linear(hidden, S.N_ACTION_CLASSES)
            self.target_head = nn.Linear(hidden, S.SLOTS_ENEMY + 1)
            self.move_head = nn.Linear(hidden, 2)
            self.produce_head = nn.Linear(hidden, n_types + 1)
            self.place_head = nn.Linear(hidden, 2)

        def forward(self, x):
            h = self.trunk(x)
            return (self.class_head(h), self.target_head(h), self.move_head(h),
                    self.produce_head(h), self.place_head(h))

    return Net()


def export_numpy(model, path):
    sd = {k: v.detach().cpu().numpy() for k, v in model.state_dict().items()}
    out = {
        "w1": sd["trunk.0.weight"].T, "b1": sd["trunk.0.bias"],
        "w2": sd["trunk.2.weight"].T, "b2": sd["trunk.2.bias"],
        "class_w": sd["class_head.weight"].T, "class_b": sd["class_head.bias"],
        "target_w": sd["target_head.weight"].T, "target_b": sd["target_head.bias"],
        "move_w": sd["move_head.weight"].T, "move_b": sd["move_head.bias"],
        "produce_w": sd["produce_head.weight"].T, "produce_b": sd["produce_head.bias"],
        "place_w": sd["place_head.weight"].T, "place_b": sd["place_head.bias"],
    }
    np.savez_compressed(path, **out)
    json_out = {k: np.asarray(v).tolist() for k, v in out.items()}
    pathlib.Path(str(path).replace(".npz", ".json")).write_text(
        json.dumps(json_out), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="reports/dsh-learning/dataset-v2")
    ap.add_argument("--out", default="reports/dsh-learning/models/bc-v2-demo")
    ap.add_argument("--hidden", type=int, default=192)
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--lambda-move", type=float, default=1.0)
    ap.add_argument("--lambda-target", type=float, default=1.0)
    ap.add_argument("--lambda-type", type=float, default=1.0)
    ap.add_argument("--lambda-place", type=float, default=1.0)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    import torch
    import torch.nn as nn

    torch.manual_seed(args.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(args.seed)

    d, info = load_dataset(args.data)
    n_types = int(info["produce_type_count"])
    obs_dim = int(info["obs_dim"])
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    Xtr = torch.from_numpy(d["X_train"]).float()
    Xva = torch.from_numpy(d["X_val"]).float()

    def tgt(key, split, dtype):
        return torch.from_numpy(d[f"{key}_{split}"]).to(dtype)

    y = {split: {k: tgt(k, split, torch.long if k in
                        ("y_class", "y_target", "y_type") else torch.float32)
                 for k in ("y_class", "y_target", "y_mdx", "y_mdy",
                           "y_type", "y_pdx", "y_pdy")}
         for split in ("train", "val")}

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = build_torch_model(obs_dim, args.hidden, n_types).to(device)
    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    ce = nn.CrossEntropyLoss()
    mse = nn.MSELoss()

    bs = args.batch
    n = len(Xtr)
    rng = np.random.default_rng(args.seed)
    history = []
    best = {"val": -1.0, "state": None, "epoch": -1}

    def train_iter(split):
        order = rng.permutation(n)
        for i in range(0, n, bs):
            idx = order[i:i + bs]
            xb = Xtr[idx].to(device)
            yb = {k: y["train"][k][idx].to(device) for k in y["train"]}
            opt.zero_grad()
            c, t, m, p, pl = model(xb)
            # target label: -1 -> class 0, slot i -> i+1
            tgt_idx = torch.where(yb["y_target"] < 0,
                                  torch.zeros_like(yb["y_target"]),
                                  yb["y_target"] + 1)
            type_idx = torch.where(yb["y_type"] < 0,
                                   torch.zeros_like(yb["y_type"]),
                                   yb["y_type"] + 1)
            move_target = torch.stack([yb["y_mdx"], yb["y_mdy"]], dim=1)
            place_target = torch.stack([yb["y_pdx"], yb["y_pdy"]], dim=1)
            loss = (ce(c, yb["y_class"])
                    + args.lambda_target * ce(t, tgt_idx)
                    + args.lambda_move * mse(torch.tanh(m), move_target)
                    + args.lambda_type * ce(p, type_idx)
                    + args.lambda_place * mse(torch.tanh(pl), place_target))
            loss.backward()
            opt.step()

    def predict(split):
        model.eval()
        with torch.no_grad():
            c, t, m, p, pl = model((Xtr if split == "train" else Xva).to(device))
        return {
            "class": c.argmax(1).cpu().numpy(),
            "target": t.argmax(1).cpu().numpy() - 1,   # back to -1 based
            "type": p.argmax(1).cpu().numpy() - 1,
            "mdx": torch.tanh(m)[:, 0].cpu().numpy(),
            "mdy": torch.tanh(m)[:, 1].cpu().numpy(),
            "pdx": np.zeros(len(c.class_hist) if hasattr(c, "class_hist") else 0),
        }

    for ep in range(args.epochs):
        model.train()
        train_iter("train")
        model.eval()
        with torch.no_grad():
            c, t, m, p, pl = model(Xva.to(device))
        val_class = c.argmax(1).cpu().numpy()
        acc = float((val_class == y["val"]["y_class"].numpy()).mean())
        history.append({"epoch": ep + 1, "val_class_acc": acc})
        if args.verbose:
            print(f"  epoch {ep + 1}/{args.epochs} val_class_acc={acc:.4f}")
        if acc > best["val"]:
            best = {"val": acc, "epoch": ep + 1,
                    "state": {k: v.detach().clone() for k, v in model.state_dict().items()}}
    if best["state"] is not None:
        model.load_state_dict(best["state"])

    model.eval()
    with torch.no_grad():
        preds = {}
        for split, Xs in (("train", Xtr), ("val", Xva)):
            c, t, m, p, pl = model(Xs.to(device))
            preds[split] = {
                "class": c.argmax(1).cpu().numpy(),
                "target": t.argmax(1).cpu().numpy() - 1,
                "type": p.argmax(1).cpu().numpy() - 1,
                "mdx": torch.tanh(m)[:, 0].cpu().numpy(),
                "mdy": torch.tanh(m)[:, 1].cpu().numpy(),
                "pdx": torch.tanh(pl)[:, 0].cpu().numpy(),
                "pdy": torch.tanh(pl)[:, 1].cpu().numpy(),
            }
    train_m = metrics(d["y_class_train"], preds["train"]["class"],
                      d["y_target_train"], preds["train"]["target"],
                      d["y_type_train"], preds["train"]["type"],
                      d["y_mdx_train"].astype("float32"), preds["train"]["mdx"],
                      d["y_mdy_train"].astype("float32"), preds["train"]["mdy"])
    val_m = metrics(d["y_class_val"], preds["val"]["class"],
                    d["y_target_val"], preds["val"]["target"],
                    d["y_type_val"], preds["val"]["type"],
                    d["y_mdx_val"].astype("float32"), preds["val"]["mdx"],
                    d["y_mdy_val"].astype("float32"), preds["val"]["mdy"])

    torch.save(model.state_dict(), out / "model.pt")
    export_numpy(model, out / "weights.npz")
    norm = {"mean": d["mean"].tolist(), "std": d["std"].tolist(),
            "feature_version": S.FEATURE_VERSION,
            "action_space_version": S.ACTION_SPACE_VERSION,
            "obs_dim": obs_dim, "n_action_classes": S.N_ACTION_CLASSES,
            "slots_enemy": S.SLOTS_ENEMY, "n_types": n_types}
    (out / "norm.json").write_text(json.dumps(norm), encoding="utf-8")
    meta = {
        "model_format_version": 2,
        "feature_version": S.FEATURE_VERSION,
        "action_space_version": S.ACTION_SPACE_VERSION,
        "backend": "torch", "device": device,
        "torch_version": torch.__version__,
        "hidden": args.hidden, "obs_dim": obs_dim, "n_types": n_types,
        "best_epoch": best["epoch"], "best_val_class_acc": best["val"],
        "train_samples": int(len(Xtr)), "val_samples": int(len(Xva)),
        "train_metrics": train_m, "val_metrics": val_m,
        "history": history[-5:],
        "data_info": {k: info[k] for k in
                      ("episodes_train", "episodes_val", "samples_train",
                       "samples_val", "dropped_hidden_target_samples",
                       "noop_samples_added", "type_catalog")},
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "seed": args.seed,
        "training_parameters": vars(args),
        "dataset_info": info,
        "dataset_sha256": hashlib.sha256((pathlib.Path(args.data) / "dataset_v2.npz").read_bytes()).hexdigest(),
        "trainer_sha256": hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),
    }
    (out / "metrics.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    print(json.dumps({"model_dir": str(out), "backend": "torch", "device": device,
                      "obs_dim": obs_dim, "n_types": n_types,
                      "train_samples": meta["train_samples"],
                      "val_samples": meta["val_samples"],
                      "val_class_acc": val_m["class_accuracy"],
                      "val_macro_f1": val_m["class_macro_f1"],
                      "target_acc": val_m["target_accuracy_on_attack"],
                      "produce_acc": val_m["produce_type_accuracy"],
                      "move_mae": val_m["move_mae"]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
