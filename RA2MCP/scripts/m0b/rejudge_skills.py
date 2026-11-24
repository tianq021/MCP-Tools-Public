"""M0-B 任务 2：用修复后口径重算 8 个 M0-A 模型的战斗与战术指标（只读、可重复）。

做什么
------
- 取 `eval-<MODEL>/evaluation.json` 里 protocol 层那批局（full-env 7 模型各 10 局，
  R1_COMBAT_REF 6 局），逐局用 `scripts/m0b/metrics_lib.py` 计算战斗/战术指标；
- 6 秒有效攻击复用 `scripts/dsh_learning/trace_parser.py` 的**修复后**口径，并做逐局交叉核对；
- 空间类指标（集火距离、撤退）改用观测 `cell`/`xyz` 计算：`trace_parser._pos()` 读
  obj['x']/obj['y']，真实数据只有 cell/xyz → 旧实现结构性返回 0（S2/S4 因此不可计算）。
  本脚本**不改阈值/窗口**，只把不可计算的指标改成可计算，并同时保留旧实现的 0 值；
- 技能判定用 `scripts/dsh_learning/skill_metrics.py`（阈值版本 v2.2-draft，哈希随输出），
  **不修改任何判定标准**。

输出：`reports/m0b-2026-11-01/raw/skill_metrics.json`
"""
import json
import pathlib
import statistics
import sys
import time

ROOT = pathlib.Path(r"E:\RA2MCP")
sys.path.insert(0, str(ROOT / "scripts"))

from dsh_learning import skill_metrics, trace_parser  # noqa: E402
from m0b import metrics_lib  # noqa: E402

M0A = ROOT / "reports" / "m0a-2026-11-01"
OUT = ROOT / "reports" / "m0b-2026-11-01" / "raw" / "skill_metrics.json"

MODELS = ["R1", "R2", "R3", "203156", "183k", "OLD_REF", "BUILD_V27", "R1_COMBAT_REF"]
FULL_ENV_MODELS = ["R1", "R2", "R3", "203156", "183k", "OLD_REF", "BUILD_V27"]
DICT_METRICS = ("action_type_counts", "target_type_orders", "group_size_histogram")
SCALAR_SKIP = ("episode_id", "jsonl", "kind", "last_kill_contribution",
               "last_kill_contribution_missing_fields")

COMPARISON_KEYS = [
    "attack_orders_raw", "attack_orders_unique", "attack_repeat_rate",
    "attack_orders_per_min", "attack_effective6_rate", "env_attack_effective_rate",
    "first_effective_attack_s", "distinct_attack_targets", "attacks_per_target",
    "target_switches", "target_switch_rate", "ineffective_switch_rate",
    "multi_unit_attack_share", "all_unit_attack_share", "mean_units_per_attack",
    "concurrent_same_target_share", "close_multi_unit_effective_share_cell",
    "multi_unit_effective_share", "target_building_share",
    "truth_kills_units", "truth_building_destroyed", "truth_damage_dealt",
    "damage_visible_total", "damage_coverage", "hp_exchange_visible",
    "retreat_opportunities", "retreat_executed", "retreat_execution_rate",
    "retreat_move_orders", "move_orders", "move_repeat_adjacent_rate",
    "move_no_displacement_share", "mean_move_displacement_cells",
    "move_without_visible_enemy_share", "consecutive_identical_action_ratio",
    "max_identical_run", "invalid_order_rate",
]


def load_protocol_episodes(model):
    run_dir = M0A / f"eval-{model}"
    evaluation = json.loads((run_dir / "evaluation.json").read_text(encoding="utf-8-sig"))
    episodes = []
    for entry in evaluation.get("episodes") or []:
        jsonl = pathlib.Path(entry["jsonl"])
        result_path = pathlib.Path(entry["result"])
        if not jsonl.exists():
            continue
        result = json.loads(result_path.read_text(encoding="utf-8-sig"))
        episodes.append((jsonl, result, entry))
    return evaluation, episodes


def episode_for_skill(tp_out, mlm):
    """把 skill_metrics 需要的字段替换成可计算版本（只修空间指标，不改阈值）。"""
    episode = dict(tp_out)
    episode["close_multi_unit_effective_share"] = mlm[
        "close_multi_unit_effective_share_cell"]
    episode["retreat_opportunities"] = mlm["retreat_opportunities"]
    episode["retreat_executed"] = mlm["retreat_executed"]
    episode["retreat_execution_rate"] = mlm["retreat_execution_rate"]
    episode["preserved_unit_ratio"] = mlm["preserved_unit_ratio"]
    episode["m0b_spatial_source"] = "cell/xyz (trace_parser uses x/y -> structurally 0)"
    return episode


def indicator_means(episodes, names):
    out = {}
    for name in names:
        vals = [e.get(name) for e in episodes
                if isinstance(e.get(name), (int, float)) and not isinstance(e.get(name), bool)]
        out[name] = round(statistics.mean(vals), 4) if vals else None
        out[name + "__n"] = len(vals)
    return out


def aggregate_skill_results(per_episode):
    entries = [{"episode_id": item["episode_id"], "skills": item["skills"],
                "episode": item["episode"]} for item in per_episode]
    verdicts = {}
    for sid in ("S1", "S2", "S3", "S4"):
        verdicts[sid] = skill_metrics.aggregate_skill(sid, entries)
    verdicts["S5"] = skill_metrics.aggregate_skill("S5", entries)
    return verdicts


def run_model(model):
    evaluation, episodes = load_protocol_episodes(model)
    per_episode = []
    skill_eps = []
    cross_mismatch = []
    for jsonl, result, entry in episodes:
        mlm = metrics_lib.analyze_episode(jsonl, result)
        tp_out = trace_parser.parse_episode(jsonl, result)
        if mlm["attack_orders_unique"] != tp_out["unique_attack_orders"] \
                or mlm["attacks_effective6"] != tp_out["attacks_effective6"] \
                or mlm["target_switches"] != tp_out["target_switches"]:
            cross_mismatch.append({
                "episode_id": mlm["episode_id"],
                "mine_unique": mlm["attack_orders_unique"],
                "tp_unique": tp_out["unique_attack_orders"],
                "mine_effective6": mlm["attacks_effective6"],
                "tp_effective6": tp_out["attacks_effective6"],
                "mine_switches": mlm["target_switches"],
                "tp_switches": tp_out["target_switches"],
            })
        ep_for_skill = episode_for_skill(tp_out, mlm)
        skills = skill_metrics.evaluate_episode(ep_for_skill)
        skill_eps.append({"episode_id": mlm["episode_id"], "skills": skills,
                          "episode": ep_for_skill})
        record = dict(mlm)
        record["tp_close_multi_unit_effective_share"] = tp_out[
            "close_multi_unit_effective_share"]
        record["tp_retreat_opportunities"] = tp_out["retreat_opportunities"]
        record["tp_multi_unit_effective_share"] = tp_out["multi_unit_effective_share"]
        record["tp_attack_concentration"] = tp_out["attack_concentration"]
        record["tp_kills_per_1000_unit_damage"] = tp_out["kills_per_1000_unit_damage"]
        record["tp_kills_per_effective_attack"] = tp_out["kills_per_effective_attack"]
        record["tp_quality_flags"] = (tp_out.get("data_quality") or {}).get("quality_flags")
        per_episode.append(record)

    # ---- 逐指标聚合 ----
    numeric_keys = []
    for record in per_episode:
        for key, value in record.items():
            if key in SCALAR_SKIP or key in DICT_METRICS or key in numeric_keys:
                continue
            if isinstance(value, (int, float, type(None))) and not isinstance(value, bool):
                numeric_keys.append(key)
    aggregate = {key: metrics_lib.summarize([r.get(key) for r in per_episode])
                 for key in numeric_keys}
    dict_totals = {}
    for key in DICT_METRICS:
        merged = {}
        for record in per_episode:
            for name, count in (record.get(key) or {}).items():
                merged[name] = merged.get(name, 0) + count
        dict_totals[key] = dict(sorted(merged.items(), key=lambda kv: -kv[1]))

    kinds = {}
    for record in per_episode:
        kinds[record["kind"]] = kinds.get(record["kind"], 0) + 1

    verdicts = aggregate_skill_results(skill_eps)
    s1 = verdicts["S1"]
    s2 = verdicts["S2"]
    s3 = verdicts["S3"]
    s4 = verdicts["S4"]
    degraded_spam = sum(1 for entry in skill_eps
                        if "DEGRADED_SPAM" in (entry["skills"]["S1"].get("reasons") or []))
    spam_risk = sum(1 for entry in skill_eps
                    if "SPAM_RISK" in (entry["skills"]["S1"].get("reasons") or []))
    return {
        "model": model,
        "run_dir": str((M0A / f"eval-{model}").relative_to(ROOT)),
        "mode": evaluation.get("mode"),
        "model_sha256": evaluation.get("model_sha256"),
        "frozen_verified": evaluation.get("frozen_verified"),
        "n_episodes": len(per_episode),
        "kinds": kinds,
        "episode_ids": [r["episode_id"] for r in per_episode],
        "aggregate": aggregate,
        "dict_totals": dict_totals,
        "skill_verdicts": verdicts,
        "skill_indicator_means": {
            "S1": indicator_means([e["episode"] for e in skill_eps],
                                  ["first_effective_attack_s", "attacks_per_min",
                                   "attack_effective6_rate", "order_repeat_rate",
                                   "pre_contact_invalid_orders"]),
            "S2": indicator_means([e["episode"] for e in skill_eps],
                                  ["close_multi_unit_effective_share",
                                   "ineffective_switch_rate",
                                   "multi_unit_effective_share_over_all"]),
            "S2_tp_structural": indicator_means(
                [{"close_multi_unit_effective_share":
                  r["tp_close_multi_unit_effective_share"]} for r in per_episode],
                ["close_multi_unit_effective_share"]),
            "S3": indicator_means([e["episode"] for e in skill_eps],
                                  ["attack_concentration", "kills_per_1000_unit_damage",
                                   "kills_per_effective_attack"]),
            "S4": indicator_means([e["episode"] for e in skill_eps],
                                  ["hp_exchange", "own_losses_per_min",
                                   "retreat_execution_rate", "preserved_unit_ratio"]),
        },
        "s1_gates": {"DEGRADED_SPAM_episodes": degraded_spam,
                     "SPAM_RISK_episodes": spam_risk},
        "cross_check_vs_trace_parser": {
            "mismatches": cross_mismatch,
            "episodes_checked": len(per_episode),
            "status": "OK" if not cross_mismatch else "MISMATCH",
        },
        "per_episode": per_episode,
    }


def main():
    started = time.time()
    results = {}
    for model in MODELS:
        results[model] = run_model(model)
        agg = results[model]["aggregate"]
        print(f"[rejudge] {model}: n={results[model]['n_episodes']} "
              f"kinds={results[model]['kinds']} "
              f"attack_unique={agg['attack_orders_unique']['sum']} "
              f"eff6_rate_mean={agg['attack_effective6_rate']['mean']} "
              f"env_rate_mean={agg['env_attack_effective_rate']['mean']} "
              f"S1={results[model]['skill_verdicts']['S1']['status']} "
              f"S2={results[model]['skill_verdicts']['S2']['status']} "
              f"S3={results[model]['skill_verdicts']['S3']['status']}", flush=True)

    comparison = {}
    for key in COMPARISON_KEYS:
        row = {}
        for model in MODELS:
            stats = results[model]["aggregate"].get(key)
            if stats is None:
                continue
            row[model] = {"n": stats["n"], "mean": stats["mean"],
                          "median": stats["median"], "sum": stats["sum"]}
        comparison[key] = row

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "task": "M0-B task 2: rejudge combat/tactical metrics with fixed definitions",
        "protocol": {
            "name": "m0a-freeze-1",
            "source": str(M0A.relative_to(ROOT)),
            "episode_selection": "episodes listed in eval-<MODEL>/evaluation.json "
                                 "(N=10 full-env, N=6 R1_COMBAT_REF)",
            "parser": "scripts/m0b/metrics_lib.py (+ trace_parser fixed 6s rule, "
                      "cross-checked per episode)",
            "skill_standard": {
                "thresholds_version": skill_metrics.THRESHOLDS_VERSION,
                "thresholds_hash": skill_metrics.THRESHOLDS_HASH,
                "mastery_rule_id": skill_metrics.MASTERY_RULE_ID,
                "s4_sample_rule_id": skill_metrics.S4_SAMPLE_RULE_ID,
                "note": "标准未修改；只把统计上不可计算的空间指标改成可计算（见 spatial_fix_note）",
            },
        },
        "spatial_fix_note": {
            "problem": "trace_parser._pos() reads obj['x']/obj['y']; real episodes expose "
                       "only cell/xyz, so close_multi_unit_effective_share (S2) and "
                       "retreat_opportunities (S4) are structurally 0/None in real data.",
            "fix": "recomputed distances from cell/xyz (1 cell = 256 xyz units); thresholds "
                   "CLOSE_CELLS=8, 30 cells, 3 cells, 10s unchanged.",
            "old_values_kept_as": "tp_close_multi_unit_effective_share / "
                                  "tp_retreat_opportunities (always 0 for real data)",
        },
        "model_order": MODELS,
        "full_env_models": FULL_ENV_MODELS,
        "combat_ref_models": ["R1_COMBAT_REF"],
        "group_notes": {
            "full_env": "RA2FullEnv（模型控制经济/建造/生产，无 RulePolicy）",
            "combat_ref": "R1_COMBAT_REF=RA2CombatEnv + 脚本经济（非模型自主能力，单列）",
        },
        "metric_definitions": metrics_lib.METRIC_DEFINITIONS,
        "aggregation": "每个数值指标给 n/mean/median/sum/min/max（逐局→模型）；"
                       "计数类用 sum，比率类用 mean/median 并注明分母（见 metric_definitions）",
        "models": results,
        "comparison": comparison,
        "elapsed_seconds": round(time.time() - started, 2),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[rejudge] wrote {OUT} ({OUT.stat().st_size} bytes) in "
          f"{payload['elapsed_seconds']}s")


if __name__ == "__main__":
    main()
