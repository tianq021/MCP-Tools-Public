"""Dataset builder v2: raw episode-v2 JSONL -> multi-head supervised dataset.

Unlike dataset.py (v1), this keeps:
  * attack target as a slot index (not a single "ATTACK_NEAREST" class)
  * move/place coordinates as continuous offsets (not 4 compass classes)
  * production type as an id from a type catalog (not building/infantry class)
  * richer observation blocks (power/queue/type-known/map passability patch)

Requires numpy (training environment only).

Usage:
  F:\\conda\\envs\\python_mmx_LLM\\python.exe scripts/dsh_train/dataset_v2.py \
      --episodes reports/dsh-learning/episodes \
      --out reports/dsh-learning/dataset-v2
"""
import argparse
import json
import pathlib
import sys
import hashlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import numpy as np  # noqa: E402

import features_v2 as F  # noqa: E402
import spec_v2 as S  # noqa: E402


def load_episodes(episodes_dir):
    """Read finalized episode-v2 files (skips _failed/ and .part)."""
    episodes = []
    root = pathlib.Path(episodes_dir)
    for p in sorted(root.glob("*.jsonl")):
        try:
            meta, rows, result = None, [], None
            with p.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    t = rec.get("type")
                    if t == "meta":
                        meta = rec["meta"]
                    elif t == "result":
                        result = rec["result"]
                    else:
                        rows.append(rec)
            # A finalized filename alone is not proof of a usable episode.
            if (meta and rows and result
                    and meta.get("schema_version") == "episode-v2"
                    and not meta.get("synthetic")
                    and result.get("kind") in ("timeout", "defeat", "win", "game_end")
                    and not result.get("is_crash")
                    and not (result.get("stats") or {}).get("visibility_violations")):
                episodes.append({"path": p, "meta": meta, "rows": rows,
                                 "result": result})
        except Exception as e:  # noqa: BLE001
            print(f"WARN unreadable episode {p}: {e}")
    return episodes


def build_type_catalog(episodes):
    """Type id catalog from own object types and production types."""
    names = set()
    for ep in episodes:
        for row in ep["rows"]:
            if row.get("type") == "observation":
                for o in row.get("own_objects") or []:
                    names.add(str(o.get("type_name")))
                for e in row.get("visible_enemies") or []:
                    names.add(str(e.get("type_name")))
            elif row.get("type") == "action" and row.get("production_type"):
                names.add(str(row["production_type"]))
    catalog = sorted(n for n in names if n and n != "None")
    return {name: i for i, name in enumerate(catalog)}, catalog


def episode_split(episodes, val_fraction, seed=7):
    rng = np.random.default_rng(seed)
    ids = [ep["meta"]["episode_id"] for ep in episodes]
    val = {i for i in ids if rng.random() < val_fraction}
    if ids and not val:
        val = {ids[-1]}
    if ids and len(val) == len(ids) and len(ids) > 1:
        val = {ids[-1]}
    return ([ep for ep in episodes if ep["meta"]["episode_id"] not in val],
            [ep for ep in episodes if ep["meta"]["episode_id"] in val])


def collect_samples(episodes, type_id_map, map_width, map_height, noop_keep, rng):
    X, y_class, y_target = [], [], []
    y_mdx, y_mdy, y_type, y_pdx, y_pdy = [], [], [], [], []
    y_n_enemies = []
    dropped_hidden, dropped_other, dropped_errors = 0, 0, 0
    noop_added = 0

    def append(last_obs, label):
        X.append(F.build_obs_v2(last_obs, type_id_map, map_width, map_height))
        y_class.append(label["class_id"])
        y_target.append(label["target_slot"])
        y_mdx.append(label["move_dx"]); y_mdy.append(label["move_dy"])
        y_type.append(label["produce_type_id"])
        y_pdx.append(label["place_dx"]); y_pdy.append(label["place_dy"])
        y_n_enemies.append(len(last_obs.get("visible_enemies") or []))

    for ep in episodes:
        last_obs = None
        actions_since_obs = 0
        for row in ep["rows"]:
            t = row.get("type")
            if t == "observation":
                if last_obs is not None and actions_since_obs == 0:
                    if rng.random() < noop_keep:
                        F.validate_visibility(last_obs)
                        append(last_obs, {"class_id": S.ACTION_CLASS_IDS["NOOP"],
                                          "target_slot": S.TARGET_NONE,
                                          "move_dx": 0.0, "move_dy": 0.0,
                                          "produce_type_id": S.TYPE_ID_NONE,
                                          "place_dx": 0.0, "place_dy": 0.0})
                        noop_added += 1
                last_obs = row
                actions_since_obs = 0
            elif t == "action":
                actions_since_obs += 1
                if row.get("error"):
                    dropped_errors += 1
                    continue
                if last_obs is None:
                    dropped_other += 1
                    continue
                try:
                    F.validate_visibility(last_obs)
                except ValueError:
                    dropped_hidden += 1
                    continue
                label = S.action_label(row, last_obs, type_id_map, S.SLOTS_ENEMY)
                if label is None:
                    dropped_hidden += 1
                    continue
                append(last_obs, label)
    return {
        "X": np.asarray(X, dtype=np.float32),
        "y_class": np.asarray(y_class, dtype=np.int64),
        "y_target": np.asarray(y_target, dtype=np.int64),
        "y_mdx": np.asarray(y_mdx, dtype=np.float32),
        "y_mdy": np.asarray(y_mdy, dtype=np.float32),
        "y_type": np.asarray(y_type, dtype=np.int64),
        "y_pdx": np.asarray(y_pdx, dtype=np.float32),
        "y_pdy": np.asarray(y_pdy, dtype=np.float32),
        "n_enemies": np.asarray(y_n_enemies, dtype=np.int64),
        "dropped_hidden": dropped_hidden,
        "dropped_other": dropped_other,
        "dropped_errors": dropped_errors,
        "noop_added": noop_added,
    }


def build_dataset(episodes_dir, out_dir, val_fraction=0.25, noop_keep=0.1,
                  map_width=182, map_height=182, seed=7, quality_gates=False):
    episodes = load_episodes(episodes_dir)
    excluded = []
    if quality_gates:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
        from dsh_episode.quality import inspect_episode
        accepted = []
        for ep in episodes:
            evidence = inspect_episode(ep["path"])
            if evidence["all_pass"]:
                accepted.append(ep)
            else:
                excluded.append({"id": ep["meta"]["episode_id"], "quality": evidence})
        episodes = accepted
    if not episodes:
        raise SystemExit(f"no episode-v2 files under {episodes_dir}")
    train_eps, val_eps = episode_split(episodes, val_fraction, seed)
    type_id_map, catalog = build_type_catalog(train_eps)
    rng = np.random.default_rng(seed)
    train = collect_samples(train_eps, type_id_map, map_width, map_height,
                            noop_keep, np.random.default_rng(seed))
    val = collect_samples(val_eps, type_id_map, map_width, map_height,
                          noop_keep, np.random.default_rng(seed + 1))
    if len(train["X"]) < 4 or len(val["X"]) < 2:
        raise SystemExit(f"not enough samples: train={len(train['X'])} val={len(val['X'])}")
    mean = train["X"].mean(axis=0)
    std = train["X"].std(axis=0)
    std[std < 1e-6] = 1.0
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        out / "dataset_v2.npz",
        X_train=(train["X"] - mean) / std, X_val=(val["X"] - mean) / std,
        mean=mean, std=std,
        **{f"{k}_train": train[k] for k in
           ("y_class", "y_target", "y_mdx", "y_mdy", "y_type", "y_pdx", "y_pdy",
            "n_enemies")},
        **{f"{k}_val": val[k] for k in
           ("y_class", "y_target", "y_mdx", "y_mdy", "y_type", "y_pdx", "y_pdy",
            "n_enemies")},
    )
    info = {
        "feature_version": S.FEATURE_VERSION,
        "label_semantics": "distance_sorted_hostile_v1",
        "quality_gates": quality_gates,
        "excluded_episodes": excluded,
        "seed": seed, "val_fraction": val_fraction, "noop_keep": noop_keep,
        "provenance": [{"episode_id": ep["meta"]["episode_id"], "path": str(ep["path"]),
                        "sha256": hashlib.sha256(ep["path"].read_bytes()).hexdigest()}
                       for ep in episodes],
        "action_space_version": S.ACTION_SPACE_VERSION,
        "obs_dim": S.OBS_DIM,
        "n_action_classes": S.N_ACTION_CLASSES,
        "episodes_total": len(episodes),
        "episodes_train": len(train_eps), "episodes_val": len(val_eps),
        "train_ids": [ep["meta"]["episode_id"] for ep in train_eps],
        "val_ids": [ep["meta"]["episode_id"] for ep in val_eps],
        "samples_train": int(len(train["X"])), "samples_val": int(len(val["X"])),
        "dropped_hidden_target_samples": train["dropped_hidden"] + val["dropped_hidden"],
        "dropped_no_observation": train["dropped_other"] + val["dropped_other"],
        "dropped_failed_actions": train["dropped_errors"] + val["dropped_errors"],
        "noop_samples_added": train["noop_added"] + val["noop_added"],
        "class_hist_train": np.bincount(train["y_class"],
                                        minlength=S.N_ACTION_CLASSES).tolist(),
        "class_hist_val": np.bincount(val["y_class"],
                                      minlength=S.N_ACTION_CLASSES).tolist(),
        "target_slot_hist_train": np.bincount(np.maximum(train["y_target"], 0),
                                              minlength=S.SLOTS_ENEMY).tolist(),
        "produce_type_count": len(catalog),
        "type_catalog": catalog,
        "map": {"width": map_width, "height": map_height},
    }
    (out / "info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    return info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--episodes", default="reports/dsh-learning/episodes")
    ap.add_argument("--out", default="reports/dsh-learning/dataset-v2")
    ap.add_argument("--val-fraction", type=float, default=0.25)
    ap.add_argument("--noop-keep", type=float, default=0.1)
    ap.add_argument("--map-width", type=int, default=182)
    ap.add_argument("--map-height", type=int, default=182)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--quality-gates", action="store_true",
                    help="require verified real opponent combat, checkpoint, visibility and <5%% command errors")
    a = ap.parse_args()
    info = build_dataset(a.episodes, a.out, a.val_fraction, a.noop_keep,
                         a.map_width, a.map_height, a.seed, a.quality_gates)
    print(json.dumps({k: info[k] for k in
                      ("feature_version", "obs_dim", "samples_train",
                       "samples_val", "dropped_hidden_target_samples",
                       "noop_samples_added", "episodes_train", "episodes_val")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
