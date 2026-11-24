"""Runtime inference for v2 multi-head models (numpy only, controller-safe).

Model dir contract (written by train_bc_v2.py):
  weights.npz / weights.json : trunk + 5 heads
  norm.json                  : mean/std + feature/action version + type count
  metrics.json               : metadata (val metrics, type catalog lives in dataset info)

Usage:
  python scripts/dsh_train/infer_v2.py --model <dir> --enabled --bench 500
"""
import argparse
import json
import math
import pathlib
import time


def _try_numpy():
    try:
        import numpy as np
        return np
    except Exception:  # noqa: BLE001
        return None


class LocalModelV2:
    """Multi-head model wrapper; cannot act unless `enabled=True`."""

    def __init__(self, model_dir, enabled=False):
        self.dir = pathlib.Path(model_dir)
        self.enabled = bool(enabled)
        self.norm = json.loads((self.dir / "norm.json").read_text(encoding="utf-8"))
        self.meta = {}
        if (self.dir / "metrics.json").exists():
            self.meta = json.loads((self.dir / "metrics.json").read_text(encoding="utf-8"))
        self.obs_dim = int(self.norm["obs_dim"])
        self.n_classes = int(self.norm["n_action_classes"])
        self.n_types = int(self.norm["n_types"])
        self.slots_enemy = int(self.norm.get("slots_enemy", 8))
        self.np = _try_numpy()
        if self.np is None:
            raise RuntimeError("numpy required for LocalModelV2 inference")
        with self.np.load(self.dir / "weights.npz") as z:
            self.w = {k: z[k].astype(self.np.float32) for k in z.files}
        self._mean = self.np.asarray(self.norm["mean"], dtype=self.np.float32)
        self._std = self.np.asarray(self.norm["std"], dtype=self.np.float32)

    def _forward(self, obs):
        x = (self.np.asarray(obs, dtype=self.np.float32) - self._mean) / self._std
        h = x @ self.w["w1"] + self.w["b1"]
        h = self.np.maximum(h, 0.0)
        h = h @ self.w["w2"] + self.w["b2"]
        h = self.np.maximum(h, 0.0)
        class_logits = h @ self.w["class_w"] + self.w["class_b"]
        target_logits = h @ self.w["target_w"] + self.w["target_b"]
        move = self.np.tanh(h @ self.w["move_w"] + self.w["move_b"])
        type_logits = h @ self.w["produce_w"] + self.w["produce_b"]
        place = self.np.tanh(h @ self.w["place_w"] + self.w["place_b"])
        probs = self._softmax(class_logits)
        return {"class_probs": probs, "target_logits": target_logits,
                "move": move, "type_logits": type_logits, "place": place}

    @staticmethod
    def _softmax(x):
        import numpy as np
        z = x - x.max()
        e = np.exp(z)
        return e / e.sum()

    def act(self, obs):
        if not self.enabled:
            return {"enabled": False, "error": "local model not enabled"}
        t0 = time.perf_counter()
        out = self._forward(obs)
        import numpy as np
        class_id = int(np.argmax(out["class_probs"]))
        confidence = float(out["class_probs"][class_id])
        target_slot = int(np.argmax(out["target_logits"])) - 1
        produce_type = int(np.argmax(out["type_logits"])) - 1
        move = [float(out["move"][0]), float(out["move"][1])]
        place = [float(out["place"][0]), float(out["place"][1])]
        ms = (time.perf_counter() - t0) * 1000.0
        return {"enabled": True, "class_id": class_id, "confidence": round(confidence, 4),
                "target_slot": target_slot, "move_dx": move[0], "move_dy": move[1],
                "produce_type_id": produce_type,
                "place_dx": place[0], "place_dy": place[1],
                "latency_ms": round(ms, 4), "feature_version": self.norm["feature_version"],
                "model_dir": str(self.dir)}

    def info(self):
        return {"model_dir": str(self.dir), "obs_dim": self.obs_dim,
                "n_types": self.n_types, "feature_version": self.norm["feature_version"],
                "val_metrics": self.meta.get("val_metrics"),
                "trained_at": self.meta.get("trained_at")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="reports/dsh-learning/models/bc-v2-demo")
    ap.add_argument("--enabled", action="store_true")
    ap.add_argument("--bench", type=int, default=0)
    a = ap.parse_args()

    lm = LocalModelV2(a.model, enabled=a.enabled)
    print(json.dumps({"info": lm.info()}, ensure_ascii=False, indent=2))
    if a.bench:
        import random
        rnd = random.Random(0)
        obs = [rnd.random() for _ in range(lm.obs_dim)]
        lat = []
        for _ in range(a.bench):
            t0 = time.perf_counter()
            lm._forward(obs)
            lat.append((time.perf_counter() - t0) * 1000.0)
        lat.sort()
        print(json.dumps({"backend": "numpy", "n": a.bench,
                          "p50_ms": round(lat[len(lat) // 2], 4),
                          "p95_ms": round(lat[int(len(lat) * 0.95)], 4)},
                         ensure_ascii=False, indent=2))
    import random
    rnd = random.Random(1)
    res = lm.act([rnd.random() for _ in range(lm.obs_dim)])
    print(json.dumps({"act": res}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
