"""P0-2：从真实 episode 轨迹诊断“重复攻击/刷指令”行为（只读，不启动游戏）。

用法：
  python scripts/dsh_rl/repeat_order_diagnose.py \
      --run reports/rl/eval-v22-trained-20261031-090057 \
      --run reports/rl/eval-v22-baseline-20261030-231956 \
      --out reports/autonomous_learning/p0_repeat_attack_diagnosis.json

它回答：
- raw_attack_orders / unique_attack_orders / order_repeat_rate 的真实构成；
- 相邻 raw 指令是否“同组单位 + 同目标 + <=1.5s”重复（与 trace_parser 去重口径一致）；
- 重复指令发生时目标是否本来就在掉血（是否只是“已在执行”的重复下发）；
- 目标切换频率、批次单位数分布；
- 观测中是否存在“当前攻击目标/当前命令”字段（决定模型是否能学会不重复下发）。
"""
import argparse
import json
import pathlib
import statistics
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from dsh_learning import trace_parser  # noqa: E402

ATTACK_DEDUP_SECONDS = trace_parser.ATTACK_DEDUP_SECONDS
EFFECTIVE_WINDOW = trace_parser.EFFECTIVE_WINDOW


def _attack_rows(observations, actions):
    """与 trace_parser._attack_records 相同的时间映射，但保留 raw 序列与 action 索引。"""
    obs_times = [float(o.get("t") or 0.0) for o in observations]
    raw = []
    for action_index, action in enumerate(actions):
        if (action.get("action") or "").upper() != "ATTACK":
            continue
        units = tuple(sorted(action.get("object_addresses") or []))
        raw.append({
            "raw_index": len(raw),
            "action_index": action_index,
            "units": units,
            "n_units": len(units),
            "target": action.get("target_address"),
            "error": action.get("error"),
        })
    for index, attack in enumerate(raw):
        attack["obs_index"] = min(index, max(0, len(obs_times) - 1))
        attack["t"] = obs_times[attack["obs_index"]] if obs_times else 0.0
    return raw


def _unique_attacks(raw):
    unique = []
    for attack in raw:
        if unique and unique[-1]["units"] == attack["units"] \
                and unique[-1]["target"] == attack["target"] \
                and attack["t"] - unique[-1]["t"] <= ATTACK_DEDUP_SECONDS:
            continue
        unique.append(dict(attack))
    return unique


def _observation_fields(observations):
    """返回首条观测中 own_objects 的字段名（用于判断是否有当前命令/目标状态）。"""
    if not observations:
        return {}
    own = observations[0].get("own_objects") or []
    if not own:
        return {}
    return {"own_object_keys": sorted(own[0].keys())}


ORDER_STATE_FIELDS = ("order", "order_type", "target", "target_address",
                      "attack_target", "current_order", "current_target")


def _distance_at_attack(attack, observations):
    """攻击指令下达时，攻击单位到目标的最小格距离（目标不在可见列表则 None）。"""
    if not observations:
        return None
    observation = observations[min(attack["obs_index"], len(observations) - 1)]
    target_pos = None
    for enemy in observation.get("visible_enemies") or []:
        if enemy.get("addr") == attack.get("target"):
            target_pos = trace_parser._pos(enemy)
            break
    if target_pos is None:
        return None
    positions = {own.get("addr"): trace_parser._pos(own)
                 for own in observation.get("own_objects") or []}
    distances = [distance for distance in
                 (trace_parser._dist(positions.get(addr), target_pos)
                  for addr in attack["units"])
                 if distance is not None]
    return min(distances) if distances else None


def _damage_after(damage_events, target, start_t, window=30.0):
    """start_t 之后 window 秒内对 target 的第一次掉血事件。"""
    candidates = [event for event in damage_events
                  if event["addr"] == target
                  and start_t - 1e-9 <= event["t"] <= start_t + window]
    return min(candidates, key=lambda event: event["t"]) if candidates else None


def analyze_episode(jsonl_path, result):
    observations, actions = trace_parser._load_records(jsonl_path)
    raw = _attack_rows(observations, actions)
    unique = _unique_attacks(raw)
    damage_events = trace_parser._damage_series(observations)
    for attack in unique:
        attack["effective6"] = trace_parser._effective(attack, damage_events)
        later = _damage_after(damage_events, attack["target"], attack["t"])
        attack["damage_within_30s"] = later is not None
        attack["first_damage_latency_s"] = (
            round(later["t"] - attack["t"], 3) if later else None)
        attack["distance_cells"] = _distance_at_attack(attack, observations)

    consecutive_duplicates = []
    same_units_transitions = 0
    target_switches = 0
    for previous, current in zip(raw, raw[1:]):
        if previous["units"] == current["units"]:
            same_units_transitions += 1
        if previous["target"] != current["target"]:
            target_switches += 1
        if (previous["units"] == current["units"]
                and previous["target"] == current["target"]
                and current["t"] - previous["t"] <= ATTACK_DEDUP_SECONDS):
            target_damage_after = sum(
                event["damage"] for event in damage_events
                if event["addr"] == current["target"]
                and current["t"] - 1e-9 <= event["t"] <= current["t"] + EFFECTIVE_WINDOW)
            later30 = _damage_after(damage_events, current["target"], current["t"])
            consecutive_duplicates.append({
                "previous_raw_index": previous["raw_index"],
                "raw_index": current["raw_index"],
                "gap_s": round(current["t"] - previous["t"], 3),
                "target_damage_in_window": target_damage_after,
                "target_already_damaged": target_damage_after > 0,
                "target_damaged_within_30s": later30 is not None,
            })

    action_counts = {}
    for action in actions:
        name = (action.get("action") or "").upper()
        action_counts[name] = action_counts.get(name, 0) + 1
    unique_effective = sum(1 for attack in unique if attack.get("effective6"))
    raw_attack_count = len(raw)
    unique_count = len(unique)
    elapsed = float(result.get("elapsed_s") or 0.0)
    fields = _observation_fields(observations)
    own_keys = fields.get("own_object_keys") or []
    has_order_state = any(key in own_keys for key in ORDER_STATE_FIELDS)
    duplicate_count = len(consecutive_duplicates)
    duplicate_with_damage = sum(1 for d in consecutive_duplicates if d["target_already_damaged"])
    duplicate_with_damage30 = sum(1 for d in consecutive_duplicates if d["target_damaged_within_30s"])
    duplicate_gaps = [d["gap_s"] for d in consecutive_duplicates]
    unique_damage30 = sum(1 for attack in unique if attack.get("damage_within_30s"))
    latencies = [attack["first_damage_latency_s"] for attack in unique
                 if attack.get("first_damage_latency_s") is not None]
    distances = [attack["distance_cells"] for attack in unique
                 if attack.get("distance_cells") is not None]
    repeat_rate = round(1.0 - unique_count / raw_attack_count, 4) if raw_attack_count else None
    unique_effective_rate = (round(unique_effective / unique_count, 4) if unique_count else None)
    return {
        "episode_id": result.get("episode_id") or pathlib.Path(jsonl_path).stem,
        "kind": result.get("kind"),
        "elapsed_s": round(elapsed, 2),
        "raw_attack_orders": raw_attack_count,
        "unique_attack_orders": unique_count,
        "order_repeat_rate": repeat_rate,
        "consecutive_duplicate_count": duplicate_count,
        "consecutive_duplicate_share_of_raw": (round(duplicate_count / raw_attack_count, 4)
                                               if raw_attack_count else None),
        "duplicates_with_target_already_damaged": duplicate_with_damage,
        "duplicate_share_with_existing_damage": (round(duplicate_with_damage / duplicate_count, 4)
                                                 if duplicate_count else None),
        "duplicates_with_target_damaged_within_30s": duplicate_with_damage30,
        "duplicate_share_with_damage_30s": (round(duplicate_with_damage30 / duplicate_count, 4)
                                            if duplicate_count else None),
        "median_duplicate_gap_s": (round(statistics.median(duplicate_gaps), 3)
                                   if duplicate_gaps else None),
        "same_units_transitions": same_units_transitions,
        "target_switches": target_switches,
        "unique_effective6": unique_effective,
        "unique_effective6_rate": unique_effective_rate,
        "unique_damage_within_30s": unique_damage30,
        "unique_damage_within_30s_rate": (round(unique_damage30 / unique_count, 4)
                                          if unique_count else None),
        "median_first_damage_latency_s": (round(statistics.median(latencies), 3)
                                          if latencies else None),
        "mean_attack_distance_cells": (round(statistics.mean(distances), 2)
                                       if distances else None),
        "attacks_per_min_raw": (round(raw_attack_count / (elapsed / 60.0), 3) if elapsed else None),
        "attacks_per_min_unique": (round(unique_count / (elapsed / 60.0), 3) if elapsed else None),
        "action_kinds": action_counts,
        "attack_action_share": (round(action_counts.get("ATTACK", 0) / len(actions), 4)
                                if actions else None),
        "observation_order_state_fields": {
            "own_object_keys": own_keys,
            "has_current_order_state": has_order_state,
            "checked_fields": list(ORDER_STATE_FIELDS),
        },
    }


def _mean(values):
    values = [v for v in values if isinstance(v, (int, float))]
    return round(statistics.mean(values), 4) if values else None


def analyze_run(run_dir, max_episodes=0):
    run_dir = pathlib.Path(run_dir)
    episodes = trace_parser.load_run_episodes(run_dir, max_episodes=max_episodes,
                                              include_timeouts=True)
    rows = [analyze_episode(jsonl, result) for jsonl, result in episodes]
    summary = {
        "run_dir": str(run_dir),
        "n_episodes": len(rows),
        "raw_attack_orders_mean": _mean([r["raw_attack_orders"] for r in rows]),
        "unique_attack_orders_mean": _mean([r["unique_attack_orders"] for r in rows]),
        "order_repeat_rate_mean": _mean([r["order_repeat_rate"] for r in rows]),
        "consecutive_duplicate_count_mean": _mean([r["consecutive_duplicate_count"] for r in rows]),
        "duplicate_share_with_existing_damage_mean":
            _mean([r["duplicate_share_with_existing_damage"] for r in rows]),
        "duplicate_share_with_damage_30s_mean":
            _mean([r["duplicate_share_with_damage_30s"] for r in rows]),
        "median_duplicate_gap_s_mean":
            _mean([r["median_duplicate_gap_s"] for r in rows]),
        "unique_effective6_rate_mean": _mean([r["unique_effective6_rate"] for r in rows]),
        "unique_damage_within_30s_rate_mean":
            _mean([r["unique_damage_within_30s_rate"] for r in rows]),
        "median_first_damage_latency_s_mean":
            _mean([r["median_first_damage_latency_s"] for r in rows]),
        "mean_attack_distance_cells_mean":
            _mean([r["mean_attack_distance_cells"] for r in rows]),
        "attacks_per_min_raw_mean": _mean([r["attacks_per_min_raw"] for r in rows]),
        "attacks_per_min_unique_mean": _mean([r["attacks_per_min_unique"] for r in rows]),
        "target_switches_mean": _mean([r["target_switches"] for r in rows]),
        "attack_action_share_mean": _mean([r["attack_action_share"] for r in rows]),
        "observation_order_state_present": any(
            r["observation_order_state_fields"]["has_current_order_state"] for r in rows),
        "root_cause_hypothesis": None,
        "hypothesis_evidence": [],
    }
    if rows:
        repeat = summary["order_repeat_rate_mean"] or 0.0
        effective = summary["unique_effective6_rate_mean"] or 0.0
        effective30 = summary["unique_damage_within_30s_rate_mean"]
        raw_rate = summary["attacks_per_min_raw_mean"] or 0.0
        if repeat >= 0.25 and raw_rate >= 5.0:
            if effective < 0.30 and effective30 is not None and effective30 >= 0.50:
                summary["root_cause_hypothesis"] = "REISSUE_WHILE_APPROACHING_6S_WINDOW_TOO_SHORT"
            elif effective < 0.30:
                summary["root_cause_hypothesis"] = "POLICY_REISSUE_WITHOUT_PERSISTENT_ORDER_FEEDBACK"
            else:
                summary["root_cause_hypothesis"] = "NO_PRONOUNCED_REISSUE_PATTERN"
            summary["hypothesis_evidence"] = [
                f"mean order_repeat_rate={repeat}",
                f"mean raw attacks/min={raw_rate}",
                f"mean unique effective6 rate={effective}",
                f"mean unique damage-within-30s rate={effective30}",
                f"median first-damage latency={summary['median_first_damage_latency_s_mean']}s",
                f"mean attack distance={summary['mean_attack_distance_cells_mean']} cells",
                f"observation has current order/target state: "
                f"{summary['observation_order_state_present']}",
                "duplicates are same unit group + same target within "
                f"{ATTACK_DEDUP_SECONDS}s; only "
                f"{summary['duplicate_share_with_existing_damage_mean']} of duplicates had "
                "the target already taking damage in the 6s window",
            ]
        else:
            summary["root_cause_hypothesis"] = "NO_PRONOUNCED_REISSUE_PATTERN"
    return {"summary": summary, "episodes": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="append", required=True)
    parser.add_argument("--max-episodes", type=int, default=0)
    parser.add_argument("--out", default="reports/autonomous_learning/p0_repeat_attack_diagnosis.json")
    args = parser.parse_args()
    report = {
        "schema": "p0-repeat-attack-diagnosis-v1",
        "attack_dedup_seconds": ATTACK_DEDUP_SECONDS,
        "effective_window_seconds": EFFECTIVE_WINDOW,
        "runs": [analyze_run(run, args.max_episodes) for run in args.run],
        "no_game_launched": True,
    }
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for run in report["runs"]:
        summary = run["summary"]
        print(json.dumps({key: summary[key] for key in (
            "run_dir", "n_episodes", "raw_attack_orders_mean",
            "unique_attack_orders_mean", "order_repeat_rate_mean",
            "consecutive_duplicate_count_mean",
            "duplicate_share_with_existing_damage_mean",
            "unique_effective6_rate_mean", "attacks_per_min_raw_mean",
            "attacks_per_min_unique_mean", "target_switches_mean",
            "observation_order_state_present", "root_cause_hypothesis")},
            ensure_ascii=False, indent=2))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
