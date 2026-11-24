"""Evaluate the 3-episode trial against the pre-registered success gates.

Read-only: reads episode JSONL/result/ckpt + runner logs, writes a JSON report.

Usage:
  .venv\\Scripts\\python.exe scripts\\dsh_trial_eval.py --episodes ep-trial-01 ep-trial-02 ep-trial-03
"""
import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
EP = ROOT / "reports" / "dsh-learning" / "episodes"
LOGS = ROOT / "build" / "trial-logs"
sys.path.insert(0, str(ROOT / "scripts"))
from dsh_episode import schema  # noqa: E402


def evaluate(eid):
    path = EP / f"{eid}.jsonl"
    log = LOGS / f"{eid}.log"
    ep = schema.read_episode(path)
    r = ep["result"] or {}
    rows = ep["rows"]
    obs = [x for x in rows if x.get("type") == "observation"]
    actions = [x for x in rows if x.get("type") == "action"]
    # contact / attack / first damage
    first_contact = next((x for x in obs if x.get("visible_enemies")), None)
    first_attack = None
    last_obs = None
    attacks = 0
    attack_hidden = 0
    for x in rows:
        if x.get("type") == "observation":
            last_obs = x
        elif x.get("type") == "action" and x.get("action") == "ATTACK":
            attacks += 1
            visible = {e.get("addr") for e in (last_obs or {}).get("visible_enemies") or []}
            if x.get("target_address") not in visible:
                attack_hidden += 1
            if first_attack is None:
                first_attack = (last_obs or {}).get("frame")
    # enemy damage first
    first_damage = None
    last_hp = {}
    for x in obs:
        hp_now = {}
        for o in x.get("own_objects") or []:
            hp_now[("own", o["addr"])] = o["health"]
        for e in x.get("visible_enemies") or []:
            hp_now[("enemy", e["addr"])] = e["health"]
        for k, h in hp_now.items():
            if k in last_hp and h < last_hp[k] and k[0] == "enemy" and first_damage is None:
                first_damage = (x.get("t"), x.get("frame"), last_hp[k], h)
        last_hp = hp_now
    stats = r.get("stats") or {}
    metrics = r.get("metrics") or {}
    ok = int(stats.get("orders_ok") or 0)
    invalid = int(stats.get("invalid_orders") or 0)
    err_ratio = invalid / max(1, ok + invalid)
    ckpt_ok = None
    ckpt_path = EP / f"{eid}.ckpt.json"
    if ckpt_path.exists():
        try:
            ckpt = json.loads(ckpt_path.read_text(encoding="utf-8"))
            ckpt_ok = (ckpt.get("done") is True
                       and int(ckpt.get("row_count", -1)) == len(rows))
        except Exception:  # noqa: BLE001
            ckpt_ok = False
    log_text = ""
    if log.exists():
        raw = log.read_bytes()
        for enc in ("utf-8", "utf-16", "utf-16-le", "gbk"):
            try:
                log_text = raw.decode(enc)
                if "acquired game lock" in log_text or "launched" in log_text:
                    break
            except Exception:  # noqa: BLE001
                continue
    gates = {
        "g1_schema_and_result": (bool(r) and not schema.validate_episode_file(path)
                                  and ckpt_ok is True),
        "g2_visibility": (int(stats.get("visibility_violations") or 0) == 0
                          and attack_hidden == 0),
        "g3_action_error_ratio": err_ratio < 0.05,
        "g4_not_crash": (r.get("kind") != "crash" and not r.get("is_crash")),
        "g5_contact_attack_damage": (first_contact is not None and attacks > 0
                                     and int(metrics.get("damage_dealt") or 0) > 0),
        "g6_lock_used": "acquired game lock" in log_text,
    }
    return {
        "episode_id": eid,
        "kind": r.get("kind"),
        "is_crash": r.get("is_crash"),
        "is_win": r.get("is_win"),
        "elapsed_s": r.get("elapsed_s"),
        "observations": len(obs),
        "actions": {a: sum(1 for x in actions if x.get("action") == a) for a in
                    ("DEPLOY", "PRODUCE", "PLACE", "MOVE", "ATTACK")},
        "first_contact_t": None if first_contact is None else first_contact.get("t"),
        "first_contact_frame": None if first_contact is None else first_contact.get("frame"),
        "first_attack_frame": first_attack,
        "attacks": attacks,
        "attack_target_hidden": attack_hidden,
        "first_enemy_damage": None if first_damage is None else {
            "t": first_damage[0], "frame": first_damage[1],
            "hp_before": first_damage[2], "hp_after": first_damage[3]},
        "damage_dealt": metrics.get("damage_dealt"),
        "own_hp_lost": metrics.get("own_hp_lost"),
        "buildings_peak": metrics.get("buildings_peak"),
        "units_peak": metrics.get("units_peak"),
        "orders_ok": ok,
        "invalid_orders": invalid,
        "error_ratio": round(err_ratio, 4),
        "stale_skipped": stats.get("stale_skipped"),
        "stale_filtered": stats.get("stale_filtered"),
        "visibility_violations": stats.get("visibility_violations"),
        "schema_errors": schema.validate_episode_file(path)[:3],
        "ckpt_consistent": ckpt_ok,
        "gates": gates,
        "all_gates_pass": all(gates.values()),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--episodes", nargs="+",
                    default=["ep-trial-01", "ep-trial-02", "ep-trial-03"])
    ap.add_argument("-o", "--out", default="reports/dsh-trial-3ep.json")
    a = ap.parse_args()
    results = [evaluate(e) for e in a.episodes]
    report = {
        "generated_at": schema.now_wall(),
        "gate_definitions": {
            "g1": "schema validation clean + result present + ckpt consistent",
            "g2": "visibility_violations==0 and all ATTACK targets visible",
            "g3": "invalid/(ok+invalid) < 5%",
            "g4": "kind != crash (defeat/timeout/win/game_end allowed)",
            "g5": ">=1 visible enemy contact AND ATTACK orders>0 AND damage_dealt>0",
            "g6": "runner acquired the exclusive game lock",
        },
        "episodes": results,
        "passed": sum(1 for r in results if r["all_gates_pass"]),
        "total": len(results),
        "contact_achieved": sum(1 for r in results if r["gates"]["g5_contact_attack_damage"]),
        "verdict": ("all_gates_passed" if all(r["all_gates_pass"] for r in results)
                    else "partial"),
    }
    pathlib.Path(a.out).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    for r in results:
        print(json.dumps({k: r[k] for k in
                          ("episode_id", "kind", "first_contact_t", "attacks",
                           "damage_dealt", "own_hp_lost", "error_ratio",
                           "visibility_violations", "all_gates_pass")},
                         ensure_ascii=False))
    print("passed:", report["passed"], "/", report["total"],
          "| contact:", report["contact_achieved"], "| verdict:", report["verdict"])
    print("written:", a.out)


if __name__ == "__main__":
    main()
