"""Shared evidence gates for collection and A/B; reads only recorded data."""
import json
import pathlib

from dsh_episode import schema
from dsh_episode.hostility import is_opponent


def inspect_episode(path):
    path = pathlib.Path(path)
    errors = schema.validate_episode_file(path)
    if errors:
        return {"all_pass": False, "errors": errors}
    ep = schema.read_episode(path)
    result = ep["result"] or {}
    rows = ep["rows"]
    obs = [r for r in rows if r.get("type") == "observation"]
    actions = [r for r in rows if r.get("type") == "action"]
    invalid = [r for r in actions if r.get("error") and "already exists" not in r["error"]]
    last_obs, hidden_targets, neutral_targets, attacks = None, 0, 0, 0
    has_contact = False
    first_contact = None
    for row in rows:
        if row.get("type") == "observation":
            last_obs = row
            opponents = [e for e in row.get("visible_enemies", []) if is_opponent(e)]
            if opponents and not has_contact:
                first_contact = row.get("t")
                has_contact = True
        elif row.get("type") == "action" and row.get("action") == "ATTACK":
            visible = {e["addr"] for e in (last_obs or {}).get("visible_enemies", [])
                       if e.get("cell_shrouded") is False}
            hidden_targets += row.get("target_address") not in visible
            targets = {e["addr"]: e for e in (last_obs or {}).get("visible_enemies", [])}
            target = targets.get(row.get("target_address"), {})
            neutral_targets += bool(target) and not is_opponent(target)
            attacks += not bool(row.get("error")) and is_opponent(target)
    metrics = schema.derive_metrics(rows)
    ckpt_path = path.with_suffix(".ckpt.json")
    try:
        ckpt = json.loads(ckpt_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ckpt = {}
    model_ok = sum(not a.get("error") and a.get("decision_source") == "model" for a in actions)
    all_ok = sum(not a.get("error") for a in actions)
    gates = {
        "schema_result_checkpoint": (ckpt.get("done") is True
            and ckpt.get("row_count") == len(rows) == result.get("rows")),
        "real_complete": (not ep["meta"].get("synthetic")
            and result.get("kind") in ("defeat", "win", "timeout", "game_end")
            and not result.get("is_crash") and len(obs) > 1
            and obs[-1]["frame"] > obs[0]["frame"]),
        "visibility": (hidden_targets == 0
            and (result.get("stats") or {}).get("visibility_violations", 0) == 0),
        "opponents_only": neutral_targets == 0,
        "metrics_match_raw": all((result.get("metrics") or {}).get(k) == metrics[k]
                                 for k in ("damage_dealt", "own_hp_lost")),
        "action_errors": len(invalid) / max(1, len(actions)) < 0.05,
        "contact_attack_damage": bool(has_contact and attacks
                                     and metrics["damage_dealt"] > 0),
    }
    return {"episode_id": ep["meta"].get("episode_id"), "gates": gates,
            "all_pass": all(gates.values()), "errors": errors,
            "kind": result.get("kind"), "observations": len(obs),
            "actions": len(actions), "invalid_orders": len(invalid),
            "error_ratio": len(invalid) / max(1, len(actions)),
            "attacks": attacks, "hidden_targets": hidden_targets, "neutral_targets": neutral_targets,
            "first_contact_s": first_contact, "metrics": metrics,
            "model_orders_ok": model_ok, "orders_ok": all_ok,
            "model_order_ratio": model_ok / max(1, all_ok)}
