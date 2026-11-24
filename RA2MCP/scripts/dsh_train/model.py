"""Framework-agnostic model helpers.

v1 model: 2-layer MLP (obs_dim -> hidden -> n_actions), ReLU hidden, softmax out.

Backends:
  * torch  — training (CUDA when available); weights exported to `.npz` + `.json`
  * numpy  — training fallback and runtime inference in the game controller
  * stdlib — dependency-free inference fallback (reads weights.json)

The exported artefact contract is intentionally framework-free:
  weights.npz / weights.json : {w1,b1,w2,b2}
  norm.json                  : {mean,std,feature_version,obs_dim,n_actions}
  meta.json                  : {backend, hidden, trained_at, val_acc, ...}
"""
import json
import math
import pathlib

try:
    import numpy as np
    HAVE_NUMPY = True
except Exception:  # noqa: BLE001
    np = None
    HAVE_NUMPY = False

try:
    import torch
    import torch.nn as nn
    HAVE_TORCH = True
except Exception:  # noqa: BLE001
    torch = None
    nn = None
    HAVE_TORCH = False


def torch_available():
    return HAVE_TORCH


def numpy_available():
    return HAVE_NUMPY


if HAVE_TORCH:
    class TorchMLP(nn.Module):
        def __init__(self, obs_dim, hidden, n_actions):
            super().__init__()
            self.net = nn.Sequential(
                nn.Linear(obs_dim, hidden),
                nn.ReLU(),
                nn.Linear(hidden, n_actions),
            )

        def forward(self, x):
            return self.net(x)


# ---------------- numpy implementation ---------------------------------------

def init_numpy_mlp(obs_dim, hidden, n_actions, seed=0):
    rng = np.random.default_rng(seed)
    w1 = (rng.standard_normal((obs_dim, hidden)) * math.sqrt(2.0 / obs_dim)).astype(np.float32)
    b1 = np.zeros(hidden, dtype=np.float32)
    w2 = (rng.standard_normal((hidden, n_actions)) * math.sqrt(1.0 / hidden)).astype(np.float32)
    b2 = np.zeros(n_actions, dtype=np.float32)
    return {"w1": w1, "b1": b1, "w2": w2, "b2": b2}


def numpy_forward(weights, X):
    h = X @ weights["w1"] + weights["b1"]
    h = np.maximum(h, 0.0)
    logits = h @ weights["w2"] + weights["b2"]
    return logits, h


def numpy_softmax(logits):
    z = logits - logits.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


def numpy_predict(weights, X):
    logits, _ = numpy_forward(weights, X)
    return logits.argmax(axis=1), numpy_softmax(logits)


def numpy_train(weights, Xtr, ytr, Xva, yva, epochs=30, lr=1e-3, batch=64,
                seed=0, log=print):
    """Plain minibatch SGD + momentum for the 2-layer MLP (training fallback)."""
    rng = np.random.default_rng(seed)
    n = len(Xtr)
    vel = {k: np.zeros_like(v) for k, v in weights.items()}
    momentum = 0.9
    best = {"val_acc": -1.0, "weights": {k: v.copy() for k, v in weights.items()},
            "epoch": -1}
    for ep in range(epochs):
        perm = rng.permutation(n)
        total_loss = 0.0
        for i in range(0, n, batch):
            idx = perm[i:i + batch]
            xb, yb = Xtr[idx], ytr[idx]
            logits, h = numpy_forward(weights, xb)
            probs = numpy_softmax(logits)
            m = len(xb)
            total_loss += float(-np.log(probs[np.arange(m), yb] + 1e-9).mean()) * m
            dlogits = probs
            dlogits[np.arange(m), yb] -= 1.0
            dlogits /= m
            grad = {
                "w2": h.T @ dlogits,
                "b2": dlogits.sum(axis=0),
            }
            dh = (dlogits @ weights["w2"].T) * (h > 0)
            grad["w1"] = xb.T @ dh
            grad["b1"] = dh.sum(axis=0)
            for k in weights:
                vel[k] = momentum * vel[k] - lr * grad[k]
                weights[k] += vel[k]
        pred, _ = numpy_predict(weights, Xva)
        val_acc = float((pred == yva).mean())
        if val_acc > best["val_acc"]:
            best = {"val_acc": val_acc,
                    "weights": {k: v.copy() for k, v in weights.items()},
                    "epoch": ep + 1}
        if log is not None:
            log(f"  epoch {ep + 1:3d}/{epochs} loss={total_loss / n:.4f} val_acc={val_acc:.4f}")
    return best["weights"], {"val_acc": best["val_acc"], "best_epoch": best["epoch"]}


# ---------------- stdlib implementation (inference only) ---------------------

def stdlib_forward(weights_json, obs):
    """Dependency-free forward pass; weights_json = {'w1': [[...]], ...}."""
    w1, b1 = weights_json["w1"], weights_json["b1"]
    w2, b2 = weights_json["w2"], weights_json["b2"]
    hidden = []
    for j in range(len(b1)):
        s = b1[j]
        for i in range(len(obs)):
            s += w1[i][j] * obs[i]
        hidden.append(s if s > 0 else 0.0)
    logits = []
    for k in range(len(b2)):
        s = b2[k]
        for j in range(len(hidden)):
            s += w2[j][k] * hidden[j]
        logits.append(s)
    m = max(logits)
    ex = [math.exp(v - m) for v in logits]
    z = sum(ex)
    return logits, [v / z for v in ex]


# ---------------- persistence -------------------------------------------------

def save_weights_npz(path, weights):
    np.savez_compressed(path, **weights)


def load_weights_npz(path):
    with np.load(path) as z:
        return {k: z[k].astype(np.float32) for k in ("w1", "b1", "w2", "b2")}


def save_weights_json(path, weights):
    payload = {k: (v.tolist() if hasattr(v, "tolist") else v) for k, v in weights.items()}
    pathlib.Path(path).write_text(json.dumps(payload), encoding="utf-8")


def load_weights_json(path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def torch_to_numpy(model):
    sd = model.state_dict()
    return {
        "w1": sd["net.0.weight"].detach().cpu().numpy().T.copy(),
        "b1": sd["net.0.bias"].detach().cpu().numpy().copy(),
        "w2": sd["net.2.weight"].detach().cpu().numpy().T.copy(),
        "b2": sd["net.2.bias"].detach().cpu().numpy().copy(),
    }
