"""Dependency-light runtime inference plugin for the RA2 controller.

This is the "允许时调用" (call-when-enabled) local model: it is NOT imported by
default, refuses to act unless `enabled=True`, and never touches the game itself —
the controller decides when to ask it for an action.

Backends (auto-detected):
  * numpy  — fast path, requires numpy only
  * stdlib — dependency-free fallback (slower, fine for <=20 Hz)

Model directory contract (written by train_bc.py):
  weights.npz / weights.json : {w1,b1,w2,b2}
  norm.json                  : {mean,std,feature_version,obs_dim,n_actions}
  meta.json (optional)       : {trained_at, val_metrics, ...}

CLI:
  python scripts/dsh_train/infer.py --model <dir> --bench 1000 --enabled
  python scripts/dsh_train/infer.py --model <dir>            # refuses (not enabled)
"""
import argparse
import json
import math
import pathlib
import time

sys_path_ok = True


def _try_import_numpy():
    try:
        import numpy as np
        return np
    except Exception:  # noqa: BLE001
        return None


class LocalModel:
    def __init__(self, model_dir, enabled=False, backend="auto"):
        self.model_dir = pathlib.Path(model_dir)
        self.enabled = bool(enabled)
        self.norm = json.loads((self.model_dir / "norm.json").read_text(encoding="utf-8"))
        self.meta = {}
        meta_path = self.model_dir / "meta.json"
        if meta_path.exists():
            self.meta = json.loads(meta_path.read_text(encoding="utf-8"))
        self.obs_dim = int(self.norm["obs_dim"])
        self.n_actions = int(self.norm["n_actions"])
        self.feature_version = self.norm["feature_version"]
        self.np = _try_import_numpy() if backend in ("auto", "numpy") else None
        self.weights_np = None
        self.weights_json = None
        if self.np is not None and (self.model_dir / "weights.npz").exists():
            with self.np.load(self.model_dir / "weights.npz") as z:
                self.weights_np = {k: z[k].astype(self.np.float32)
                                   for k in ("w1", "b1", "w2", "b2")}
            self.backend = "numpy"
        else:
            self.weights_json = json.loads(
                (self.model_dir / "weights.json").read_text(encoding="utf-8"))
            self.backend = "stdlib"
        self._mean = self.norm["mean"]
        self._std = self.norm["std"]

    # ---- normalization ----
    def _normalize(self, obs):
        if len(obs) != self.obs_dim:
            raise ValueError(f"obs dim {len(obs)} != model obs_dim {self.obs_dim}")
        return [(float(v) - m) / s for v, m, s in zip(obs, self._mean, self._std)]

    # ---- predict ----
    def _predict(self, obs_norm):
        if self.backend == "numpy":
            x = self.np.asarray([obs_norm], dtype=self.np.float32)
            h = x @ self.weights_np["w1"] + self.weights_np["b1"]
            h = self.np.maximum(h, 0.0)
            logits = h @ self.weights_np["w2"] + self.weights_np["b2"]
            z = logits - logits.max(axis=1, keepdims=True)
            e = self.np.exp(z)
            probs = (e / e.sum(axis=1, keepdims=True))[0]
            probs = [float(v) for v in probs]
        else:
            w = self.weights_json
            hidden = []
            for j in range(len(w["b1"])):
                s = w["b1"][j]
                for i in range(len(obs_norm)):
                    s += w["w1"][i][j] * obs_norm[i]
                hidden.append(s if s > 0 else 0.0)
            logits = []
            for k in range(len(w["b2"])):
                s = w["b2"][k]
                for j in range(len(hidden)):
                    s += w["w2"][j][k] * hidden[j]
                logits.append(s)
            m = max(logits)
            ex = [math.exp(v - m) for v in logits]
            z = sum(ex)
            probs = [v / z for v in ex]
        best = max(range(len(probs)), key=lambda i: probs[i])
        return best, probs[best], probs

    def act(self, obs):
        """Return {'enabled', 'action', 'confidence', 'probs', 'latency_ms', ...}."""
        if not self.enabled:
            return {"enabled": False, "error": "local model not enabled"}
        t0 = time.perf_counter()
        obs_norm = self._normalize(obs)
        action, conf, probs = self._predict(obs_norm)
        ms = (time.perf_counter() - t0) * 1000.0
        return {
            "enabled": True, "action": int(action), "confidence": round(conf, 4),
            "probs": [round(p, 4) for p in probs],
            "latency_ms": round(ms, 4), "backend": self.backend,
            "feature_version": self.feature_version,
            "model_dir": str(self.model_dir),
            "val_accuracy": self.meta.get("val_metrics", {}).get("accuracy"),
        }

    def info(self):
        return {"model_dir": str(self.model_dir), "backend": self.backend,
                "obs_dim": self.obs_dim, "n_actions": self.n_actions,
                "feature_version": self.feature_version,
                "val_metrics": self.meta.get("val_metrics"),
                "trained_at": self.meta.get("trained_at")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="reports/dsh-learning/models/bc-demo")
    ap.add_argument("--enabled", action="store_true",
                    help="actually allow acting; without it the model refuses")
    ap.add_argument("--bench", type=int, default=0, help="benchmark N random obs")
    ap.add_argument("--backend", default="auto", choices=["auto", "numpy", "stdlib"],
                    help="auto: numpy if importable, else stdlib")
    a = ap.parse_args()

    lm = LocalModel(a.model, enabled=a.enabled, backend=a.backend)
    print(json.dumps({"info": lm.info()}, ensure_ascii=False, indent=2))

    if a.bench:
        import random
        rnd = random.Random(0)
        obs = [rnd.random() for _ in range(lm.obs_dim)]
        lat = []
        for _ in range(a.bench):
            t0 = time.perf_counter()
            obs_n = lm._normalize(obs)
            lm._predict(obs_n)
            lat.append((time.perf_counter() - t0) * 1000.0)
        lat.sort()
        print(json.dumps({
            "bench": lm.backend, "n": a.bench,
            "p50_ms": round(lat[len(lat) // 2], 4),
            "p95_ms": round(lat[int(len(lat) * 0.95)], 4),
            "max_ms": round(lat[-1], 4),
        }, ensure_ascii=False, indent=2))

    # smoke act
    import random
    rnd = random.Random(1)
    res = lm.act([rnd.random() for _ in range(lm.obs_dim)])
    print(json.dumps({"act": res}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
