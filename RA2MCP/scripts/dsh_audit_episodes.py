"""Read-only audit of collected RA2 episodes (no game, no training).

Produces a machine-checkable JSON + console summary:
  * per-episode schema/row counts/validation/checkpoint consistency
  * action statistics and native error breakdown
  * visible-enemy timeline and combat opportunity (did the policy ever *see* an
    enemy in range? did it issue ATTACK? was the target visible at that moment?)
  * own damage / loss timeline (who got hurt without enemy damage feedback)
  * first effective engagement time
  * _failed/ and .part classification (must not enter training)

Usage:
  .venv\\Scripts\\python.exe scripts\\dsh_audit_episodes.py \
      -o reports/dsh-episode-audit.json
"""
import argparse
import json
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from dsh_episode import schema  # noqa: E402
from dsh_episode import quality

CELL = 256


def read_records(path):
    meta, result, rows = None, None, []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as e:
                rows.append({"type": "_bad_json", "error": str(e)})
                continue
            t = rec.get("type")
            if t == "meta":
                meta = rec.get("meta")
            elif t == "result":
                result = rec.get("result")
            else:
                rows.append(rec)
    return {"path": str(path), "meta": meta, "result": result, "rows": rows}


def obj_cell(o):
    c = o.get("cell") or [0, 0]
    return (float(c[0]), float(c[1]))


def analyze_episode(path, include_failed=False):
    ep = read_records(path)
    rows = ep["rows"]
    out = {
        "path": str(path),
        "finalized": ep["result"] is not None,
        "meta_schema": (ep["meta"] or {}).get("schema_version"),
        "episode_id": (ep["meta"] or {}).get("episode_id"),
        "policy": (ep["meta"] or {}).get("policy", {}).get("name"),
        "counts": {},
        "result": ep["result"],
        "validation_errors": schema.validate_episode_file(path)[:10],
        "actions": {},
        "action_errors": {},
        "combat": {},
        "damage": {},
        "movement": {},
        "visibility": {},
        "data_quality": {},
    }
    for r in rows:
        out["counts"][r.get("type", "?")] = out["counts"].get(r.get("type", "?"), 0) + 1

    # --- checkpoint consistency ---
    ckpt_path = pathlib.Path(path).with_suffix("").with_suffix(".ckpt.json")
    if ckpt_path.exists():
        try:
            ckpt = json.loads(ckpt_path.read_text(encoding="utf-8"))
            expected = 1 + int(ckpt.get("row_count", -1)) + (1 if ep["result"] else 0)
            out["data_quality"]["ckpt"] = {
                "ckpt_row_count": ckpt.get("row_count"),
                "lines_expected": expected,
                "meta_rows": sum(1 for _ in [None]) if False else None,
                "consistent": (ckpt.get("done") is True and ep["result"] is not None
                               and ckpt.get("row_count") == len(rows)
                               and ep["result"].get("rows") == len(rows)),
            }
        except Exception as e:  # noqa: BLE001
            out["data_quality"]["ckpt_error"] = str(e)

    actions = [r for r in rows if r.get("type") == "action"]
    out["actions"] = {}
    for a in actions:
        out["actions"][a.get("action")] = out["actions"].get(a.get("action"), 0) + 1
    for a in actions:
        err = a.get("error")
        if err:
            key = f"{a.get('action')}: {str(err)[:80]}"
            out["action_errors"][key] = out["action_errors"].get(key, 0) + 1
    out["data_quality"]["actions_total"] = len(actions)
    out["data_quality"]["actions_with_error"] = sum(
        1 for a in actions if a.get("error"))
    out["data_quality"]["actions_error_ratio"] = round(
        out["data_quality"]["actions_with_error"] / max(1, len(actions)), 4)

    # --- timeline over observations ---
    last_obs = None
    obs_count = 0
    visible_hist = []
    min_enemy_dist = None
    first_visible_at = None
    first_visible_frame = None
    own_hp = {}
    damage_events = []
    loss_events = []
    first_own_hp_loss_at = None
    attack_records = []
    move_destinations = []
    max_infantry_y = None
    base_y = None
    for r in rows:
        t = r.get("type")
        if t == "observation":
            obs_count += 1
            last_obs = r
            # capture_observation already filtered by the visibility contract;
            # every recorded enemy is legal knowledge (validator re-checks it)
            enemies = list(r.get("visible_enemies") or [])
            own = r.get("own_objects") or []
            inf = [o for o in own
                   if o.get("abstract") == "ABSTRACT_TYPE_INFANTRY"]
            if enemies:
                visible_hist.append(len(enemies))
                if first_visible_frame is None:
                    first_visible_at = r.get("t")
                    first_visible_frame = r.get("frame")
                for e in enemies:
                    for o in inf:
                        d = math.hypot(obj_cell(e)[0] - obj_cell(o)[0],
                                       obj_cell(e)[1] - obj_cell(o)[1])
                        if min_enemy_dist is None or d < min_enemy_dist:
                            min_enemy_dist = d
            for o in own:
                if o.get("type_name") == "Allied Construction Yard" and base_y is None:
                    base_y = obj_cell(o)[1]
            if inf:
                ys = [obj_cell(o)[1] for o in inf]
                max_infantry_y = max(max_infantry_y, max(ys)) if max_infantry_y \
                    else max(ys)
            hp_now = {o["addr"]: o["health"] for o in own}
            for addr, hp in hp_now.items():
                if addr in own_hp and hp < own_hp[addr]:
                    if first_own_hp_loss_at is None:
                        first_own_hp_loss_at = r.get("t")
                    damage_events.append({"t": r.get("t"), "frame": r.get("frame"),
                                          "addr": addr, "before": own_hp[addr],
                                          "after": hp})
                own_hp[addr] = hp
            for addr in list(own_hp):
                if addr not in hp_now:
                    loss_events.append({"t": r.get("t"), "frame": r.get("frame"),
                                        "addr": addr})
                    own_hp.pop(addr, None)
        elif t == "action" and r.get("action") == "ATTACK":
            target = int(r.get("target_address") or 0)
            obs = last_obs or {}
            enemies = list(obs.get("visible_enemies") or [])
            target_visible = any(int(e.get("addr", -1)) == target for e in enemies)
            inf = [o for o in (obs.get("own_objects") or [])
                   if o.get("abstract") == "ABSTRACT_TYPE_INFANTRY"]
            dist = None
            for e in enemies:
                if int(e.get("addr", -1)) == target:
                    if inf:
                        dist = min(math.hypot(obj_cell(e)[0] - obj_cell(o)[0],
                                              obj_cell(e)[1] - obj_cell(o)[1])
                                   for o in inf)
            attack_records.append({
                "frame": obs.get("frame"), "target": target,
                "target_type": r.get("target_type"),
                "target_visible_in_obs": target_visible,
                "min_attacker_distance_cells": None if dist is None else round(dist, 2),
                "ack_ms": r.get("ack_ms"), "error": r.get("error"),
                "n_attackers": len(r.get("object_addresses") or []),
            })
        elif t == "action" and r.get("action") == "MOVE":
            coords = r.get("coordinates")
            if coords:
                move_destinations.append({"frame": (last_obs or {}).get("frame"),
                                          "x": coords[0], "y": coords[1],
                                          "error": r.get("error")})

    out["visibility"] = {
        "observations": obs_count,
        "obs_with_visible_enemies": len(visible_hist),
        "max_visible_enemies": max(visible_hist) if visible_hist else 0,
        "first_visible_enemy_at_s": first_visible_at,
        "first_visible_enemy_frame": first_visible_frame,
        "min_infantry_enemy_distance_cells": None if min_enemy_dist is None
        else round(min_enemy_dist, 2),
    }
    out["combat"] = {
        "attack_orders": len(attack_records),
        "attack_orders_target_visible": sum(
            1 for a in attack_records if a["target_visible_in_obs"]),
        "attack_orders_target_hidden": sum(
            1 for a in attack_records if not a["target_visible_in_obs"]),
        "attack_details": attack_records[:20],
        "first_damage_at_s": (ep["result"] or {}).get("stats", {}).get(
            "first_damage_at_s") or (ep["result"] or {}).get("metrics", {}).get(
            "first_damage_at_s"),
    }
    out["damage"] = {
        "own_hp_loss_events": len(damage_events),
        "own_objects_lost": len(loss_events),
        "first_own_hp_loss_at_s": first_own_hp_loss_at,
        "own_hp_loss_samples": damage_events[:15],
        "lost_own_objects": loss_events[:15],
        "enemy_damage_events_in_rows": sum(
            1 for r in rows if r.get("type") == "event"
            and r.get("event") == "enemy_object_damaged"),
        "result_damage_dealt": (ep["result"] or {}).get("metrics", {}).get(
            "damage_dealt"),
        "result_own_hp_lost": (ep["result"] or {}).get("metrics", {}).get(
            "own_hp_lost"),
    }
    out["movement"] = {
        "move_orders": len(move_destinations),
        "move_errors": sum(1 for m in move_destinations if m.get("error")),
        "base_y": base_y,
        "max_infantry_y": max_infantry_y,
        "advance_cells": None if (base_y is None or max_infantry_y is None)
        else round(max_infantry_y - base_y, 2),
        "last_move_dest": move_destinations[-1] if move_destinations else None,
    }
    out["data_quality"]["has_result"] = ep["result"] is not None
    result = ep["result"] or {}
    out["data_quality"]["trainable"] = (not include_failed
        and result.get("kind") in ("timeout", "defeat", "win", "game_end")
        and not result.get("is_crash") and not (ep["meta"] or {}).get("synthetic")
        and not (result.get("stats") or {}).get("visibility_violations")
        and not schema.validate_episode_file(path))
    out["quality_gates"] = quality.inspect_episode(path)
    out["corrected_metrics"] = out["quality_gates"].get("metrics")
    out["data_quality"]["combat_ready"] = out["quality_gates"]["all_pass"]
    return out


def analyze_part(path):
    """Interrupted .part: read meta + rows, never trainable."""
    recs = read_records(path)
    return {
        "path": str(path),
        "finalized": False,
        "trainable": False,
        "reason": "interrupted .part (no result line); must not enter training",
        "rows": len(recs["rows"]),
        "counts": {t: sum(1 for r in recs["rows"] if r.get("type") == t)
                   for t in ("observation", "action", "event", "note")},
        "meta_policy": (recs["meta"] or {}).get("policy", {}).get("name"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--episodes", default=str(ROOT / "reports/dsh-learning/episodes"))
    ap.add_argument("--out", default=str(ROOT / "reports/dsh-episode-audit.json"))
    a = ap.parse_args()
    ep_dir = pathlib.Path(a.episodes)
    audit = {"generated_at": schema.now_wall(), "episodes_dir": str(ep_dir),
             "complete": [], "failed": [], "interrupted": [], "archived": [], "batch_reports": []}
    for manifest in sorted((ep_dir / "_interrupted").glob("*/archive.json")):
        audit["archived"].append(json.loads(manifest.read_text(encoding="utf-8")))
    for p in sorted(ep_dir.glob("*.jsonl")):
        audit["complete"].append(analyze_episode(p))
    failed_dir = ep_dir / "_failed"
    if failed_dir.exists():
        for p in sorted(failed_dir.glob("*.jsonl")):
            audit["failed"].append(analyze_episode(p, include_failed=True))
    for p in sorted(ep_dir.glob("*.jsonl.part")):
        audit["interrupted"].append(analyze_part(p))
    for p in sorted((ROOT / "reports/dsh-learning").glob("batch-*.json")):
        try:
            b = json.loads(p.read_text(encoding="utf-8"))
            audit["batch_reports"].append({
                "path": str(p), "batch_id": b.get("batch_id"),
                "finished": b.get("finished"), "good": b.get("good"),
                "crash_or_missing": b.get("crash_or_missing"),
                "totals": b.get("totals"),
                "results": [{"episode_id": r.get("episode_id"), "kind": r.get("kind"),
                             "rows": r.get("rows"), "runner_exit": r.get("runner_exit")}
                            for r in (b.get("results") or [])],
            })
        except Exception as e:  # noqa: BLE001
            audit["batch_reports"].append({"path": str(p), "error": str(e)})
    pathlib.Path(a.out).write_text(json.dumps(audit, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    print(json.dumps({
        "out": a.out,
        "complete": [{"id": e["episode_id"], "policy": e["policy"],
                      "kind": (e["result"] or {}).get("kind"),
                      "rows": e["counts"].get("observation"),
                      "actions": e["actions"],
                      "attack_orders": e["combat"]["attack_orders"],
                      "attack_visible": e["combat"]["attack_orders_target_visible"],
                      "visible_enemy_obs": e["visibility"]["obs_with_visible_enemies"],
                      "min_dist_cells": e["visibility"]["min_infantry_enemy_distance_cells"],
                      "own_hp_loss_events": e["damage"]["own_hp_loss_events"],
                      "damage_dealt": e["damage"]["result_damage_dealt"],
                      "trainable": e["data_quality"]["trainable"],
                      "validation_errors": len(e["validation_errors"])}
                     for e in audit["complete"]],
        "failed": len(audit["failed"]), "interrupted": audit["interrupted"],
        "batches": audit["batch_reports"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
