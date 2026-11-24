"""Offline validation of a v2 multi-head model (no game, no training).

Answers the phase-4 questions with evidence:
  * class distribution / confusion matrix / per-class recall
  * confidence distribution and how often the runtime would fall back to rules
  * invalid target slots (would be dropped) and production-type validity
  * move/place regression errors

Usage (training env, numpy):
  F:\\conda\\envs\\python_mmx_LLM\\python.exe scripts/dsh_validate_model.py \
      --data reports/dsh-learning/dataset-v2 \
      --model reports/dsh-learning/models/bc-v2-demo \
      --threshold 0.35 -o reports/dsh-model-validation.json
"""
import argparse
import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "dsh_train"))

import numpy as np  # noqa: E402

import spec_v2 as S  # noqa: E402


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def softmax(x):
    z = x - x.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


def forward(w, X):
    h = np.maximum(X @ w["w1"] + w["b1"], 0.0)
    h = np.maximum(h @ w["w2"] + w["b2"], 0.0)
    return {
        "class_logits": h @ w["class_w"] + w["class_b"],
        "target_logits": h @ w["target_w"] + w["target_b"],
        "move": np.tanh(h @ w["move_w"] + w["move_b"]),
        "type_logits": h @ w["produce_w"] + w["produce_b"],
        "place": np.tanh(h @ w["place_w"] + w["place_b"]),
    }


def classification_report(y_true, y_pred, n_classes, names):
    cm = [[0] * n_classes for _ in range(n_classes)]
    for t, p in zip(y_true.tolist(), y_pred.tolist()):
        cm[t][p] += 1
    per = []
    for c in range(n_classes):
        tp = cm[c][c]
        fp = sum(cm[r][c] for r in range(n_classes)) - tp
        fn = sum(cm[c]) - tp
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        per.append({"class": names[c] if c < len(names) else str(c),
                    "support": sum(cm[c]), "precision": round(prec, 4),
                    "recall": round(rec, 4), "f1": round(f1, 4)})
    macro = float(np.mean([p["f1"] for p in per]))
    acc = float((y_true == y_pred).mean()) if len(y_true) else 0.0
    return {"accuracy": round(acc, 4), "macro_f1": round(macro, 4),
            "confusion": cm, "per_class": per}


def replay_policy_on_episodes(episodes_dir, episode_ids, model_dir, threshold):
    """Replay ModelPolicy (model + rule fallback) over real recorded observations.

    This validates runtime behaviour on real data without a game: how often the
    model actually acts vs falls back, whether any ATTACK intent targets an
    enemy that is not visible in the same observation, and the intent mix.
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    sys.path.insert(0, str(ROOT / "scripts" / "dsh_train"))
    from dsh_episode import model_policy
    from dsh_episode.schema import read_episode
    from infer_v2 import LocalModelV2

    lm = LocalModelV2(model_dir, enabled=True)
    catalog_names = lm.meta.get("data_info", {}).get("type_catalog") or []
    type_catalog = [{"pointer_self": i + 1000, "name": n, "array_index": i,
                     "type": "UNKNOWN"} for i, n in enumerate(catalog_names)]
    stats = {"observations": 0, "intents_total": 0, "model_actions": 0,
             "fallback_actions": 0, "low_confidence": 0, "unsupported": 0,
             "attack_intents": 0, "attack_target_invisible": 0,
             "intent_actions": {}}
    per_episode = {}
    for eid in episode_ids:
        p = pathlib.Path(episodes_dir) / f"{eid}.jsonl"
        if not p.exists():
            continue
        ep = read_episode(p)
        policy = model_policy.ModelPolicy(lm, type_catalog,
                                          {"build_order": ["Allied Power Plant",
                                                           "Allied Barracks"]},
                                          confidence_threshold=threshold)
        n_obs = 0
        for row in ep["rows"]:
            if row.get("type") != "observation":
                continue
            n_obs += 1
            ctx = {
                "t": float(row.get("t") or 0.0),
                "frame": row.get("frame"),
                "own_objects": row.get("own_objects") or [],
                "visible_enemies": row.get("visible_enemies") or [],
                "factories": row.get("factories") or [],
                "hero": {"self": 1, "name": "<human player>",
                         "money": row.get("money") or 0},
                "map": row.get("map") or {},
                "observation": row,
                "type_catalog": type_catalog,
                "last_acks": [],
            }
            intents = policy.step(ctx)
            stats["intents_total"] += len(intents)
            for it in intents:
                a = it["action"]
                stats["intent_actions"][a] = stats["intent_actions"].get(a, 0) + 1
                if a == "ATTACK":
                    stats["attack_intents"] += 1
                    visible = {e["addr"] for e in row.get("visible_enemies") or []
                               if e.get("cell_shrouded") is False}
                    if int(it.get("target_address") or 0) not in visible:
                        stats["attack_target_invisible"] += 1
        stats["observations"] += n_obs
        per_episode[eid] = {"observations": n_obs,
                            "policy_stats": dict(getattr(policy, "stats", {}))}
        for k in ("model_actions", "fallback_actions", "low_confidence",
                  "unsupported"):
            stats[k] += int(getattr(policy, "stats", {}).get(k, 0))
    stats["per_episode"] = per_episode
    stats["model_action_ratio"] = round(
        stats["model_actions"] / max(1, stats["model_actions"]
                                     + stats["fallback_actions"]), 4)
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="reports/dsh-learning/dataset-v2")
    ap.add_argument("--model", default="reports/dsh-learning/models/bc-v2-demo")
    ap.add_argument("--threshold", type=float, default=0.35)
    ap.add_argument("--episodes-dir", default="reports/dsh-learning/episodes")
    ap.add_argument("--simulate", action="store_true",
                    help="replay ModelPolicy over the val episodes (needs episodes dir)")
    ap.add_argument("-o", "--out", default="reports/dsh-model-validation.json")
    a = ap.parse_args()

    data_dir = pathlib.Path(a.data)
    model_dir = pathlib.Path(a.model)
    with np.load(data_dir / "dataset_v2.npz") as z:
        d = {k: z[k] for k in z.files}
    info = json.loads((data_dir / "info.json").read_text(encoding="utf-8"))
    norm = json.loads((model_dir / "norm.json").read_text(encoding="utf-8"))
    meta = json.loads((model_dir / "metrics.json").read_text(encoding="utf-8")) \
        if (model_dir / "metrics.json").exists() else {}
    with np.load(model_dir / "weights.npz") as z:
        w = {k: z[k].astype(np.float32) for k in z.files}

    split = "val"
    X = d[f"X_{split}"]
    y_class = d[f"y_class_{split}"]
    y_target = d[f"y_target_{split}"]
    y_type = d[f"y_type_{split}"]
    y_mdx = d[f"y_mdx_{split}"]
    y_mdy = d[f"y_mdy_{split}"]
    n_enemies = d[f"n_enemies_{split}"]
    pred = forward(w, X)
    probs = softmax(pred["class_logits"])
    pred_class = probs.argmax(axis=1)
    conf = probs.max(axis=1)
    pred_target = pred["target_logits"].argmax(axis=1) - 1
    pred_type = pred["type_logits"].argmax(axis=1) - 1
    pred_mdx, pred_mdy = pred["move"][:, 0], pred["move"][:, 1]
    pred_pdx, pred_pdy = pred["place"][:, 0], pred["place"][:, 1]

    cls = classification_report(y_class, pred_class, S.N_ACTION_CLASSES,
                                S.ACTION_CLASSES)
    true_attack = y_class == S.ACTION_CLASS_IDS["ATTACK"]
    true_produce = y_class == S.ACTION_CLASS_IDS["PRODUCE"]
    valid_target = (pred_target >= 0) & (pred_target < np.maximum(n_enemies, 0))
    valid_type = (pred_type >= 0) & (pred_type < int(info["produce_type_count"]))
    fallback = (conf < a.threshold)
    fallback |= (pred_class == S.ACTION_CLASS_IDS["ATTACK"]) & (~valid_target)
    fallback |= (pred_class == S.ACTION_CLASS_IDS["PRODUCE"]) & (~valid_type)
    would_act = ~fallback

    out = {
        "model_dir": str(model_dir),
        "weights_sha256": sha256(model_dir / "weights.npz"),
        "feature_version": norm.get("feature_version"),
        "split": split,
        "samples": int(len(X)),
        "episodes_val": info.get("val_ids"),
        "threshold": a.threshold,
        "training_meta": {
            "best_epoch": meta.get("best_epoch"),
            "val_class_acc_at_training": meta.get("val_metrics", {}).get(
                "class_accuracy"),
            "note": "training accuracy is NOT game win rate",
        },
        "class_metrics": cls,
        "class_hist_true": np.bincount(y_class,
                                       minlength=S.N_ACTION_CLASSES).tolist(),
        "class_hist_pred": np.bincount(pred_class,
                                        minlength=S.N_ACTION_CLASSES).tolist(),
        "confidence": {
            "mean": round(float(conf.mean()), 4),
            "p10": round(float(np.percentile(conf, 10)), 4),
            "p50": round(float(np.percentile(conf, 50)), 4),
            "p90": round(float(np.percentile(conf, 90)), 4),
            "below_threshold_ratio": round(float((conf < a.threshold).mean()), 4),
        },
        "attack_head": {
            "true_attack_samples": int(true_attack.sum()),
            "pred_attack_samples": int((pred_class
                                        == S.ACTION_CLASS_IDS["ATTACK"]).sum()),
            "target_valid_ratio_on_pred_attack": round(
                float(valid_target[pred_class == S.ACTION_CLASS_IDS["ATTACK"]].mean()),
                4) if (pred_class == S.ACTION_CLASS_IDS["ATTACK"]).any() else None,
            "target_slot_accuracy_on_true_attack": round(
                float((pred_target[true_attack] == y_target[true_attack]).mean()), 4)
            if true_attack.any() else None,
        },
        "produce_head": {
            "true_produce_samples": int(true_produce.sum()),
            "type_valid_ratio_on_pred_produce": round(
                float(valid_type[pred_class == S.ACTION_CLASS_IDS["PRODUCE"]].mean()),
                4) if (pred_class == S.ACTION_CLASS_IDS["PRODUCE"]).any() else None,
            "type_accuracy_on_true_produce": round(
                float((pred_type[true_produce] == y_type[true_produce]).mean()), 4)
            if true_produce.any() else None,
        },
        "regression": {
            "move_mae": round(float(np.abs(pred_mdx - y_mdx).mean()
                                    + np.abs(pred_mdy - y_mdy).mean()) / 2.0, 4),
            "place_mae": round(float(np.abs(pred_pdx - d["y_pdx_val"]).mean()
                                     + np.abs(pred_pdy - d["y_pdy_val"]).mean()) / 2.0, 4),
        },
        "runtime_fallback_simulation": {
            "would_act_ratio": round(float(would_act.mean()), 4),
            "fallback_ratio": round(float(fallback.mean()), 4),
            "fallback_reasons": {
                "low_confidence": int((conf < a.threshold).sum()),
                "invalid_attack_target": int(((pred_class
                    == S.ACTION_CLASS_IDS["ATTACK"]) & (~valid_target)).sum()),
                "invalid_produce_type": int(((pred_class
                    == S.ACTION_CLASS_IDS["PRODUCE"]) & (~valid_type)).sum()),
            },
        },
    }
    if a.simulate:
        out["runtime_policy_replay"] = replay_policy_on_episodes(
            a.episodes_dir, info.get("val_ids") or [], model_dir, a.threshold)
    pathlib.Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    print(json.dumps({k: out[k] for k in
                      ("model_dir", "samples", "class_hist_true",
                       "class_hist_pred", "confidence",
                       "runtime_fallback_simulation")}, ensure_ascii=False, indent=2))
    print("per-class:")
    for p in out["class_metrics"]["per_class"]:
        print(f"  {p['class']:<14} support={p['support']:>3} "
              f"precision={p['precision']} recall={p['recall']} f1={p['f1']}")
    print("attack_head:", json.dumps(out["attack_head"], ensure_ascii=False))
    print("produce_head:", json.dumps(out["produce_head"], ensure_ascii=False))
    print("regression:", json.dumps(out["regression"], ensure_ascii=False))
    print(f"written: {a.out}")


if __name__ == "__main__":
    main()
