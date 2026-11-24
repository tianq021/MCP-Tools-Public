"""M0-A：用修复后的指标口径重算历史 episode 指标（只读）。

对比：
- 修复前（旧 trace_parser 顺序近似）/ 修复后（动作→所属观测时间）；
- env 运行时真值指标（result.metrics.attacks_issued/attacks_effective，来自 TruthTracker）；
- 历史报告记录的聚合值（若 run 目录有 metrics.json / 报告，仅做展示，不自动对齐口径）。
"""
import argparse
import json
import pathlib
import statistics
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from dsh_learning import trace_parser  # noqa: E402

DEFAULT_RUNS = {
    "stage_a_r1": "reports/rl/stage-A-round1-20261030-153225",
    "stage_a_r2": "reports/rl/stage-A-round2-20261030-155824",
    "stage_a_r3": "reports/rl/stage-A-round3-20261030-162536",
    "old_baseline_75g": "reports/rl/ppo-shared-20261030-102633",
    "baseline_203156": "reports/rl/eval-v22-baseline203156-20261030-233745",
    "experiment_a_eval": "reports/rl/eval-v22-trained-20261031-090057",
}


def aggregate_run(run_dir):
    run_dir = ROOT / run_dir
    episodes = trace_parser.load_run_episodes(run_dir, include_timeouts=True)
    rows, env_issued, env_effective = [], 0, 0
    for jsonl, result in episodes:
        out = trace_parser.parse_episode(jsonl, result)
        metrics = result.get("metrics") or {}
        env_issued += int(metrics.get("attacks_issued") or 0)
        env_effective += int(metrics.get("attacks_effective") or 0)
        rows.append({
            "episode_id": out["episode_id"],
            "outcome": result.get("kind"),
            "raw_attack_orders": out["raw_attack_orders"],
            "unique_attack_orders": out["unique_attack_orders"],
            "effective6": out["attacks_effective6"],
            "effective6_rate_fixed": out["attack_effective6_rate"],
            "attacks_per_min": out["attacks_per_min"],
            "order_repeat_rate": out["order_repeat_rate"],
            "first_attack_s": out["first_attack_command_s"],
            "damage_visible": out["damage_to_units_visible"],
            "truth_damage": out["data_quality"].get("truth_total_damage"),
            "truth_kills_units": out["truth_kills_units"],
            "map_coverage_final": out["map_coverage_final"],
            "mapping": out.get("attack_time_mapping"),
            "quality_flags": out["data_quality"].get("quality_flags"),
        })
    n = len(rows)

    def mean(key):
        vals = [r[key] for r in rows if r.get(key) is not None]
        return round(statistics.mean(vals), 4) if vals else None

    kinds = {}
    for r in rows:
        kinds[r["outcome"]] = kinds.get(r["outcome"], 0) + 1
    return {
        "run_dir": str(run_dir.relative_to(ROOT)),
        "episodes": n,
        "kinds": kinds,
        "unique_attack_orders_total": sum(r["unique_attack_orders"] or 0 for r in rows),
        "effective6_total": sum(r["effective6"] or 0 for r in rows),
        "effective6_rate_fixed": (round(sum(r["effective6"] or 0 for r in rows)
                                        / max(1, sum(r["unique_attack_orders"] or 0 for r in rows)), 4)
                                  if rows else None),
        "mean_effective6_rate": mean("effective6_rate_fixed"),
        "mean_order_repeat_rate": mean("order_repeat_rate"),
        "mean_attacks_per_min": mean("attacks_per_min"),
        "mean_first_attack_s": mean("first_attack_s"),
        "mean_damage_visible": mean("damage_visible"),
        "mean_truth_damage": mean("truth_damage"),
        "mean_map_coverage_final": mean("map_coverage_final"),
        "env_attacks_issued": env_issued,
        "env_attacks_effective": env_effective,
        "env_effective_rate": (round(env_effective / env_issued, 4) if env_issued else None),
        "mapping_totals": {
            "preceding_observation": sum((r["mapping"] or {}).get("preceding_observation", 0)
                                         for r in rows),
            "order_fallback": sum((r["mapping"] or {}).get("order_fallback", 0) for r in rows),
        },
        "per_episode": rows,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="reports/m0a-2026-11-01/history_recompute.json")
    ap.add_argument("--runs", nargs="*", default=None,
                    help="name=path 列表；缺省用内置 6 组")
    a = ap.parse_args()
    runs = dict(DEFAULT_RUNS)
    if a.runs:
        runs = {}
        for item in a.runs:
            name, path = item.split("=", 1)
            runs[name] = path
    result = {"generated_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
              "parser": "trace_parser (M0-A fixed: action->preceding-observation)",
              "runs": {}}
    for name, path in runs.items():
        try:
            result["runs"][name] = aggregate_run(path)
            print(f"[recompute] {name}: {json.dumps({k: v for k, v in result['runs'][name].items() if k != 'per_episode'}, ensure_ascii=False)}",
                  flush=True)
        except Exception as exc:  # noqa: BLE001
            result["runs"][name] = {"error": repr(exc)}
            print(f"[recompute] {name}: ERROR {exc!r}", flush=True)
    out = ROOT / a.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[recompute] wrote {out}")


if __name__ == "__main__":
    main()
