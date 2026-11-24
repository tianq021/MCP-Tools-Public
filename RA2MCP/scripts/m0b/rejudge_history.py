"""M0-B 任务 3：用修复后口径重判 P3/P4 等历史技能结论（只读、可重复）。

做法
----
1. 对 6 组可重建样本的历史 run（Stage A R1/R2/R3 = 10 局；旧 75 局基线 = 76 局；
   R1 24 局 fixed-eval；203156 24 局；183k(Experiment A) 24 局）逐局重算指标；
   210239 的原始 20 局样本无法从 73 个 result.json 中唯一还原 → 重判 UNKNOWN。
2. 旧值（REPORTED）从历史技能记录 JSON（`reports/dsh-learning/p3-exec-evidence/*.json`）
   与历史报告文本中取，并记录来源；新值由本脚本计算。
3. 技能判定复用 `scripts/dsh_learning/skill_metrics.py`（阈值版本 v2.2-draft，哈希随输出），
   **不修改阈值、窗口、样本量规则**；唯一改动是把统计上不可计算的空间指标
   （trace_parser._pos 读 x/y，真实数据只有 cell/xyz）改成可计算版本，同时保留旧实现的 0 值。

输出：`reports/m0b-2026-11-01/raw/before_after_metrics.json`
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

OUT = ROOT / "reports" / "m0b-2026-11-01" / "raw" / "before_after_metrics.json"
EVIDENCE = ROOT / "reports" / "dsh-learning" / "p3-exec-evidence"

RUNS = {
    "stage_a_r1": {
        "dir": "reports/rl/stage-A-round1-20261030-153225",
        "label": "Stage A R1（训练期 10 局，随机采样策略）",
    },
    "stage_a_r2": {
        "dir": "reports/rl/stage-A-round2-20261030-155824",
        "label": "Stage A R2（训练期 10 局）",
    },
    "stage_a_r3": {
        "dir": "reports/rl/stage-A-round3-20261030-162536",
        "label": "Stage A R3（训练期 10 局）",
    },
    "old_baseline_75g": {
        "dir": "reports/rl/ppo-shared-20261030-102633",
        "label": "旧 75 局基线（V3 时代训练 run 的全部已确认局：75 负 + 1 时限）",
    },
    "baseline_r1_24": {
        "dir": "reports/rl/eval-v22-baseline-20261030-231956",
        "label": "R1 24 局 fixed-eval 基线（P3 baseline skill record）",
    },
    "baseline_203156_24": {
        "dir": "reports/rl/eval-v22-baseline203156-20261030-233745",
        "label": "203156 24 局 fixed-eval 基线",
    },
    "trained_183k_24": {
        "dir": "reports/rl/eval-v22-trained-20261031-090057",
        "label": "183k（Experiment A 8h 训练后）24 局 fixed-eval",
    },
    "ppo_shared_203156": {
        "dir": "reports/rl/ppo-shared-20261030-203156",
        "label": "203156（训练期，V0 技能记录用 20 局）",
        "sample_restorable": False,
    },
    "ppo_shared_210239": {
        "dir": "reports/rl/ppo-shared-20261030-210239",
        "label": "210239（存活奖励短跑）",
        "sample_restorable": False,
    },
}

# 旧值来源：技能记录 JSON（程序读取）
RECORD_OLD = {
    "baseline_r1_24": "baseline-skill-record.json",
    "baseline_203156_24": "baseline203156-skill-record.json",
    "trained_183k_24": "trained-skill-record.json",
}

# 旧值来源：历史报告文本（人工摘录，带行号）
REPORT_OLD = {
    "stage_a_r1": {
        "source": "reports/dsh-learning/skill-eval-runs.md:13-15",
        "first_attack_s": 146.0, "attacks_per_min": 6.484,
        "attack_effective6_rate": 0.53, "ineffective_switch_rate": 0.45,
        "group_attack_share": 0.133, "mean_units_per_attack": 2.329,
        "top1_damage_share": 0.33, "kills_per_1000_damage": 5.124,
        "top1_attack_share": 0.264,
        "skills": {"S1": "NOT_MASTERED", "S2": "NOT_MASTERED", "S3": "NOT_MASTERED"},
    },
    "stage_a_r2": {
        "source": "reports/dsh-learning/skill-eval-runs.md:28-30",
        "first_attack_s": 140.29, "attacks_per_min": 6.346,
        "attack_effective6_rate": 0.467, "ineffective_switch_rate": 0.532,
        "group_attack_share": 0.146, "mean_units_per_attack": 2.362,
        "top1_damage_share": 0.339, "kills_per_1000_damage": 4.314,
        "top1_attack_share": 0.276,
        "skills": {"S1": "NOT_MASTERED", "S2": "NOT_MASTERED", "S3": "NOT_MASTERED"},
    },
    "stage_a_r3": {
        "source": "reports/dsh-learning/skill-eval-runs.md:43-45",
        "first_attack_s": 96.19, "attacks_per_min": 15.509,
        "attack_effective6_rate": 0.275, "ineffective_switch_rate": 0.739,
        "group_attack_share": 0.152, "mean_units_per_attack": 2.287,
        "top1_damage_share": 0.32, "kills_per_1000_damage": 5.117,
        "top1_attack_share": 0.221,
        "skills": {"S1": "NOT_MASTERED", "S2": "NOT_MASTERED", "S3": "NOT_MASTERED"},
    },
    "ppo_shared_210239": {
        "source": "reports/dsh-learning/skill-eval-runs.md:73-75（20 局样本）",
        "first_attack_s": 113.027, "attacks_per_min": 5.554,
        "attack_effective6_rate": 0.158, "ineffective_switch_rate": 0.838,
        "group_attack_share": 0.14, "mean_units_per_attack": 2.211,
        "top1_damage_share": 0.435, "kills_per_1000_damage": 2.657,
        "top1_attack_share": 0.254,
        "skills": {"S1": "NOT_MASTERED", "S2": "NOT_MASTERED", "S3": "NOT_MASTERED"},
    },
    "old_baseline_75g": {
        "source": "reports/dsh-learning/skill-metrics.md:88 / reports/audit-2026-10-31/"
                  "RA2_AI_REAUDIT_2026-10-31.md:223（旧基线 0.2257、切换率 84.8%）",
        "attack_effective6_rate": 0.2257, "ineffective_switch_rate": 0.8484,
    },
}


def load_record(name):
    path = EVIDENCE / name
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    run = (data.get("runs") or [{}])[0]
    return run


def summarize(values):
    return metrics_lib.summarize(values)


def analyze_run(name, spec):
    run_dir = ROOT / spec["dir"]
    episodes = trace_parser.load_run_episodes(run_dir, include_timeouts=True)
    per_episode, skill_eps = [], []
    for jsonl, result in episodes:
        mlm = metrics_lib.analyze_episode(jsonl, result)
        tp_out = trace_parser.parse_episode(jsonl, result)
        ep_for_skill = dict(tp_out)
        ep_for_skill["close_multi_unit_effective_share"] = mlm[
            "close_multi_unit_effective_share_cell"]
        ep_for_skill["retreat_opportunities"] = mlm["retreat_opportunities"]
        ep_for_skill["retreat_executed"] = mlm["retreat_executed"]
        ep_for_skill["retreat_execution_rate"] = mlm["retreat_execution_rate"]
        ep_for_skill["preserved_unit_ratio"] = mlm["preserved_unit_ratio"]
        skills = skill_metrics.evaluate_episode(ep_for_skill)
        rec = dict(mlm)
        rec["tp_close_multi_unit_effective_share"] = tp_out[
            "close_multi_unit_effective_share"]
        rec["tp_retreat_opportunities"] = tp_out["retreat_opportunities"]
        rec["tp_attack_effective6_rate"] = tp_out["attack_effective6_rate"]
        rec["tp_order_repeat_rate"] = tp_out["order_repeat_rate"]
        rec["tp_first_effective_attack_s"] = tp_out["first_effective_attack_s"]
        per_episode.append(rec)
        skill_eps.append({"episode_id": mlm["episode_id"], "skills": skills,
                          "episode": ep_for_skill})

    kinds = {}
    for rec in per_episode:
        kinds[rec["kind"]] = kinds.get(rec["kind"], 0) + 1

    numeric_keys = []
    for rec in per_episode:
        for key, value in rec.items():
            if key in ("episode_id", "jsonl", "kind", "last_kill_contribution",
                       "last_kill_contribution_missing_fields",
                       "action_type_counts", "target_type_orders",
                       "group_size_histogram") or key in numeric_keys:
                continue
            if isinstance(value, (int, float, type(None))) and not isinstance(value, bool):
                numeric_keys.append(key)
    aggregate = {key: summarize([r.get(key) for r in per_episode]) for key in numeric_keys}

    entries = [{"episode_id": e["episode_id"], "skills": e["skills"],
                "episode": e["episode"]} for e in skill_eps]
    verdicts = {sid: skill_metrics.aggregate_skill(sid, entries)
                for sid in ("S1", "S2", "S3", "S4")}
    verdicts["S5"] = skill_metrics.aggregate_skill("S5", entries)

    pooled = {
        "attack_orders_raw": sum(r["attack_orders_raw"] for r in per_episode),
        "attack_orders_unique": sum(r["attack_orders_unique"] for r in per_episode),
        "attacks_effective6": sum(r["attacks_effective6"] for r in per_episode),
        "attacks_effective3": sum(r["attacks_effective3"] for r in per_episode),
        "attacks_effective30": sum(r["attacks_effective30"] for r in per_episode),
        "env_attacks_issued": sum(r["env_attacks_issued"] for r in per_episode),
        "env_attacks_effective": sum(r["env_attacks_effective"] for r in per_episode),
        "close_multi_unit_effective_orders": sum(
            r["close_multi_unit_effective_orders"] for r in per_episode),
        "multi_unit_attack_orders": sum(r["multi_unit_attack_orders"] for r in per_episode),
        "multi_unit_effective_orders": sum(r["multi_unit_effective_orders"]
                                           for r in per_episode),
        "truth_kills_units": sum(r["truth_kills_units"] for r in per_episode),
        "truth_building_destroyed": sum(r["truth_building_destroyed"]
                                        for r in per_episode),
        "truth_damage_dealt": sum((r["truth_damage_dealt"] or 0) for r in per_episode),
    }
    pooled["attack_effective6_rate"] = (
        round(pooled["attacks_effective6"] / pooled["attack_orders_unique"], 4)
        if pooled["attack_orders_unique"] else None)
    pooled["attack_effective3_rate"] = (
        round(pooled["attacks_effective3"] / pooled["attack_orders_unique"], 4)
        if pooled["attack_orders_unique"] else None)
    pooled["attack_effective30_rate"] = (
        round(pooled["attacks_effective30"] / pooled["attack_orders_unique"], 4)
        if pooled["attack_orders_unique"] else None)
    pooled["env_attack_effective_rate"] = (
        round(pooled["env_attacks_effective"] / pooled["env_attacks_issued"], 4)
        if pooled["env_attacks_issued"] else None)
    pooled["close_multi_unit_effective_share"] = (
        round(pooled["close_multi_unit_effective_orders"]
              / pooled["attack_orders_unique"], 4)
        if pooled["attack_orders_unique"] else None)
    pooled["multi_unit_effective_share_over_all"] = (
        round(pooled["multi_unit_effective_orders"] / pooled["attack_orders_unique"], 4)
        if pooled["attack_orders_unique"] else None)

    # 旧实现的重复率/有效率（用于展示"旧值 → 修复值"）
    old_close = summarize([r["tp_close_multi_unit_effective_share"]
                           for r in per_episode])["mean"]
    old_impl = {
        "tp_attack_effective6_rate_mean": summarize(
            [r["tp_attack_effective6_rate"] for r in per_episode])["mean"],
        "tp_order_repeat_rate_mean": summarize(
            [r["tp_order_repeat_rate"] for r in per_episode])["mean"],
        "tp_first_effective_attack_s_mean": summarize(
            [r["tp_first_effective_attack_s"] for r in per_episode])["mean"],
        "tp_close_multi_unit_effective_share": old_close,
    }

    repeat_gt_half = sum(1 for r in per_episode
                         if (r["attack_repeat_rate"] or 0) > skill_metrics.SKILLS["S1"][
                             "gates"]["order_repeat_rate_max"])
    degraded_spam = sum(1 for e in skill_eps
                        if "DEGRADED_SPAM" in (e["skills"]["S1"].get("reasons") or []))
    spam_risk = sum(1 for e in skill_eps
                    if "SPAM_RISK" in (e["skills"]["S1"].get("reasons") or []))
    null_metric_share = {
        "attack_effective6_rate_missing": sum(
            1 for r in per_episode if r["attack_effective6_rate"] is None),
        "unuseable_episodes": sum(
            1 for r in per_episode if r["attack_orders_unique"] == 0),
    }

    old = {}
    if name in RECORD_OLD:
        run = load_record(RECORD_OLD[name])
        old = {
            "source": f"reports/dsh-learning/p3-exec-evidence/{RECORD_OLD[name]}"
                      f"#runs[0].skills",
            "record_run": run.get("run"),
            "episodes": run.get("episodes"),
            "skills": {sid: {k: (run.get("skills") or {}).get(sid, {}).get(k)
                             for k in ("status", "pass_rate", "n_episodes",
                                       "wilson_low", "wilson_high", "indicator_means")}
                       for sid in ("S1", "S2", "S3", "S4")},
        }
    elif name in REPORT_OLD:
        old = {"report_values": REPORT_OLD[name]}

    return {
        "run": name,
        "label": spec["label"],
        "run_dir": spec["dir"],
        "n_episodes": len(per_episode),
        "kinds": kinds,
        "sample_restorable": spec.get("sample_restorable", True),
        "old_reported": old,
        "recomputed": {
            "aggregate": aggregate,
            "pooled": pooled,
            "old_implementation_values": old_impl,
            "s1_gate_counts": {"episodes_repeat_rate_gt_0.5": repeat_gt_half,
                               "DEGRADED_SPAM_episodes": degraded_spam,
                               "SPAM_RISK_episodes": spam_risk},
            "missing": null_metric_share,
            "skill_verdicts": verdicts,
        },
        "per_episode": per_episode,
    }


def resolve(run, stat, key):
    agg = run["recomputed"]["aggregate"].get(key)
    pooled = run["recomputed"]["pooled"]
    if stat == "max":
        return agg["max"] if agg else None
    if stat == "mean":
        return agg["mean"] if agg else None
    if stat == "median":
        return agg["median"] if agg else None
    if stat == "sum":
        return agg["sum"] if agg else None
    if stat == "pooled":
        return pooled.get(key)
    if stat == "verdict":
        return (run["recomputed"]["skill_verdicts"].get(key) or {}).get("status")
    if stat == "verdict_n":
        return (run["recomputed"]["skill_verdicts"].get(key) or {}).get("n_episodes")
    if stat == "verdict_pass_rate":
        return (run["recomputed"]["skill_verdicts"].get(key) or {}).get("pass_rate")
    if stat == "gate":
        return run["recomputed"]["s1_gate_counts"].get(key)
    if stat == "old_impl":
        return run["recomputed"]["old_implementation_values"].get(key)
    raise ValueError(stat)


CLAIMS = [
    # ================= P4 / Experiment A（183k，24 局） =================
    {
        "id": "P4-01",
        "source": "reports/dsh-learning/p4-training-campaign-report.md:32,49,61",
        "original": "6s 有效率 0.053（5.3%），被判为“极低”“真实短板”，并作为 S1/S2/S3 不达标的量化依据",
        "run": "trained_183k_24",
        "old_metrics": {"attack_effective6_rate": 0.0531},
        "new_metrics": {
            "attack_effective6_rate_mean": ("mean", "attack_effective6_rate"),
            "attack_effective6_rate_pooled": ("pooled", "attack_effective6_rate"),
            "env_attack_effective_rate_pooled": ("pooled", "env_attack_effective_rate"),
        },
        "status": "SUPERSEDED",
        "status_reason": "旧值来自 trace_parser 时间映射 bug（第 i 条 ATTACK → 第 i 条观测时间），"
                         "不是模型行为差；同一批 24 局修复后 6s 可见 HP 口径高一个数量级，"
                         "且与 env 真值 3s 口径(0.32)方向一致。",
        "new_judgment": "“6s 有效率 5.3%”作废。重算后有效攻击率不是短板；"
                        "但该指标只是‘下攻击指令后目标在 6s 窗口内掉过血’，不等于命中率，"
                        "更不能当作战略/战术能力证据。",
    },
    {
        "id": "P4-02",
        "source": "reports/dsh-learning/p4-training-campaign-report.md:33",
        "original": "首次有效攻击 24.6s（“出现”）",
        "run": "trained_183k_24",
        "old_metrics": {"first_effective_attack_s": 24.6094},
        "new_metrics": {
            "first_effective_attack_s_mean": ("mean", "first_effective_attack_s"),
            "first_effective_attack_s_median": ("median", "first_effective_attack_s"),
        },
        "status": "SUPERSEDED",
        "status_reason": "同一错位时间轴产物；修复后 24 局均值 88.9s、中位 55.0s（旧记录 24.6s）。",
        "new_judgment": "24.6s 作废。以重算值判定 S1 的 first_effective_attack_s≤90s："
                        "均值 88.9s 看似擦线，但非刷屏的 9 局为 150–240s（见 P4-03），不达标。",
    },
    {
        "id": "P4-03",
        "source": "reports/dsh-learning/p4-training-campaign-report.md:41",
        "original": "S1 UNDETERMINED (n=9)：24 局中 15 局 order_repeat_rate>0.5 → DEGRADED_SPAM；"
                    "其余 9 局均 SPAM_RISK（有效攻击率 5.3%）；样本不足 → UNDETERMINED",
        "run": "trained_183k_24",
        "old_metrics": {"DEGRADED_SPAM_episodes": 15, "S1_n": 9,
                        "attack_effective6_rate": 0.0531,
                        "first_effective_attack_s": 24.6094},
        "new_metrics": {
            "repeat_rate_gt_0.5_episodes": ("gate", "episodes_repeat_rate_gt_0.5"),
            "DEGRADED_SPAM_episodes": ("gate", "DEGRADED_SPAM_episodes"),
            "SPAM_RISK_episodes": ("gate", "SPAM_RISK_episodes"),
            "S1_status": ("verdict", "S1"),
            "S1_n": ("verdict_n", "S1"),
            "S1_pass_rate": ("verdict_pass_rate", "S1"),
            "attack_effective6_rate_mean": ("mean", "attack_effective6_rate"),
            "first_effective_attack_s_mean": ("mean", "first_effective_attack_s"),
            "attacks_per_min_mean": ("mean", "attack_orders_per_min"),
        },
        "status": "PARTIALLY_SUPERSEDED",
        "status_reason": "门槛计数（15 局重复率>0.5、DEGRADED_SPAM=15、n=9、S1=UNDETERMINED）"
                         "重算后完全一致；但被引用的两个指标值（5.3% 有效率、24.6s 首次有效）是错的。",
        "new_judgment": "S1 状态维持 UNDETERMINED（n=9<20），且 n=9 局的失败原因是"
                        "attacks_per_min≈2–10（<10）与 first_effective≈150–240s（>90s），"
                        "不是“有效率 5.3%”。重算有效率 0.594。",
    },
    {
        "id": "P4-04",
        "source": "reports/dsh-learning/p4-training-campaign-report.md:42",
        "original": "S2 有组织进攻 NOT_MASTERED (n=24)：协同有效攻击仍为 0",
        "run": "trained_183k_24",
        "old_metrics": {"close_multi_unit_effective_share": 0.0,
                        "ineffective_switch_rate": 0.9601,
                        "S2_status": "NOT_MASTERED"},
        "new_metrics": {
            "close_multi_unit_effective_share_mean": (
                "mean", "close_multi_unit_effective_share_cell"),
            "close_multi_unit_effective_share_pooled": (
                "pooled", "close_multi_unit_effective_share"),
            "ineffective_switch_rate_mean": ("mean", "ineffective_switch_rate"),
            "S2_status": ("verdict", "S2"),
            "S2_n": ("verdict_n", "S2"),
            "S2_pass_rate": ("verdict_pass_rate", "S2"),
        },
        "status": "SUPERSEDED",
        "status_reason": "旧 close_multi_unit_effective_share 结构性为 0：trace_parser._pos() 读 obj['x']/obj['y']，"
                         "真实 episode 只有 cell/xyz，距离恒 None → S2 输入侧指标必然 0。"
                         "用 cell 重算后 close≈0.53、无效切换 0.398，S2 判定从 NOT_MASTERED 变为 UNDETERMINED。",
        "new_judgment": "“协同有效攻击=0”是工具缺陷产物，S2=NOT_MASTERED 不成立（SUPERSEDED）。"
                        "重判 S2=UNDETERMINED（pass_rate=0.708，Wilson 下界 0.508<0.60，未达 MASTERED）。"
                        "注意：通过该指标只说明‘多单位打同一近距离目标且目标掉血’，不等于真正的战术协同。",
    },
    {
        "id": "P4-05",
        "source": "reports/dsh-learning/p4-training-campaign-report.md:43",
        "original": "S3 集中火力 NOT_MASTERED (n=24)：“击杀转化改善但集中度/效率未达标”",
        "run": "trained_183k_24",
        "old_metrics": {"attack_concentration": 0.2598,
                        "kills_per_1000_unit_damage": 5.538,
                        "kills_per_effective_attack": 0.8658,
                        "S3_status": "NOT_MASTERED"},
        "new_metrics": {
            "top1_attack_share_mean": ("mean", "top1_attack_share"),
            "kills_per_1000_unit_damage_mean": ("mean", "kills_per_1000_unit_damage"),
            "kills_per_effective_attack6_mean": ("mean", "kills_per_effective_attack6"),
            "S3_status": ("verdict", "S3"),
            "S3_n": ("verdict_n", "S3"),
            "S3_pass_rate": ("verdict_pass_rate", "S3"),
        },
        "status": "PARTIALLY_SUPERSEDED",
        "status_reason": "S3=NOT_MASTERED 维持（pass 1/24），但三个指标值都变了："
                         "集中度 0.2545<0.40 是唯一真正不达标的指标；"
                         "kills/1000 单位伤害 5.95≥3.0 与 kills/有效攻击 0.307≥0.15 其实都达标。",
        "new_judgment": "S3 NOT_MASTERED 维持，理由改成“火力分散（top1 攻击集中度 0.25<0.40）”；"
                        "“击杀转化/效率未达标”不成立——这两项重算后达标。",
    },
    # ================= P3 baseline（R1 24 局 fixed-eval） =================
    {
        "id": "P3-01",
        "source": "reports/dsh-learning/p3-baseline-multiopen-report.md:22-23,30；"
                  "p3-exec-evidence/baseline-skill-record.md:15-17",
        "original": "S1 NOT_MASTERED（pass 0.0，Wilson [0,0.138]，n=24）；"
                    "S2 NOT_MASTERED（n=22）；S3 NOT_MASTERED（n=21）",
        "run": "baseline_r1_24",
        "old_metrics": {"S1_n": 24, "S2_n": 22, "S3_n": 21, "S1_pass_rate": 0.0},
        "new_metrics": {
            "S1_status": ("verdict", "S1"), "S1_n": ("verdict_n", "S1"),
            "S2_status": ("verdict", "S2"), "S2_n": ("verdict_n", "S2"),
            "S3_status": ("verdict", "S3"), "S3_n": ("verdict_n", "S3"),
            "attack_orders_unique_sum": ("sum", "attack_orders_unique"),
        },
        "status": "UPHELD",
        "status_reason": "该 run 的模型 0 次 ATTACK（全部 MOVE），S1/S2/S3 的分母为 0；"
                         "重算后状态与 n 完全一致。",
        "new_judgment": "结论不变（NOT_MASTERED）；但理由必须写成“模型从不主动攻击、"
                        "甚至没有任何攻击样本”，不是“有效率低”。",
    },
    # ================= Stage A 三个训练期 10 局 run（V0 口径） =================
    {
        "id": "SA-01",
        "source": "reports/dsh-learning/skill-eval-runs.md:13-15（R1 训练期 10 局，V0 阈值）",
        "original": "R1：first_attack_s=146.0、attacks_per_min=6.484、attack_effective6_rate=0.53；"
                    "S1/S2/S3 NOT_MASTERED",
        "run": "stage_a_r1",
        "old_metrics": {"first_attack_s": 146.0, "attacks_per_min": 6.484,
                        "attack_effective6_rate": 0.53},
        "new_metrics": {
            "first_attack_command_s_mean": ("mean", "first_attack_command_s"),
            "attack_orders_per_min_mean": ("mean", "attack_orders_per_min"),
            "attack_effective6_rate_mean": ("mean", "attack_effective6_rate"),
            "attack_effective6_rate_pooled": ("pooled", "attack_effective6_rate"),
            "S1_status_v22": ("verdict", "S1"),
        },
        "status": "UPHELD",
        "status_reason": "动作/观测接近 1:1 的旧数据，映射 bug 影响小：三项指标重算与旧值差 <0.01。"
                         "状态差异只来自判定规则版本：V0（pass<0.30→NOT_MASTERED）vs "
                         "v2.2（n<20→UNDETERMINED），不是数据变化。",
        "new_judgment": "数值与 V0 判定维持。按当前 v2.2 规则，10 局只能给 UNDETERMINED（样本不足），"
                        "两种写法必须标注阈值版本，不能混用。",
    },
    {
        "id": "SA-02",
        "source": "reports/dsh-learning/skill-eval-runs.md:28-30（R2 训练期 10 局，V0 阈值）",
        "original": "R2：first_attack_s=140.29、attacks_per_min=6.346、attack_effective6_rate=0.467；"
                    "S1/S2/S3 NOT_MASTERED",
        "run": "stage_a_r2",
        "old_metrics": {"first_attack_s": 140.29, "attacks_per_min": 6.346,
                        "attack_effective6_rate": 0.467},
        "new_metrics": {
            "first_attack_command_s_mean": ("mean", "first_attack_command_s"),
            "attack_orders_per_min_mean": ("mean", "attack_orders_per_min"),
            "attack_effective6_rate_mean": ("mean", "attack_effective6_rate"),
            "attack_effective6_rate_pooled": ("pooled", "attack_effective6_rate"),
            "S1_status_v22": ("verdict", "S1"),
        },
        "status": "UPHELD",
        "status_reason": "同 R1：重算与旧值差 <0.005。",
        "new_judgment": "数值与 V0 判定维持；v2.2 下为 UNDETERMINED（n=10<20）。",
    },
    {
        "id": "SA-03",
        "source": "reports/dsh-learning/skill-eval-runs.md:43-45（R3 训练期 10 局，V0 阈值）",
        "original": "R3：first_attack_s=96.19、attacks_per_min=15.509、attack_effective6_rate=0.275、"
                    "无效切换 0.739；判为“刷屏退化样本”",
        "run": "stage_a_r3",
        "old_metrics": {"first_attack_s": 96.19, "attacks_per_min": 15.509,
                        "attack_effective6_rate": 0.275,
                        "ineffective_switch_rate": 0.739},
        "new_metrics": {
            "first_attack_command_s_mean": ("mean", "first_attack_command_s"),
            "attack_orders_per_min_mean": ("mean", "attack_orders_per_min"),
            "attack_effective6_rate_mean": ("mean", "attack_effective6_rate"),
            "attack_effective6_rate_pooled": ("pooled", "attack_effective6_rate"),
            "ineffective_switch_rate_mean": ("mean", "ineffective_switch_rate"),
            "attack_repeat_rate_mean": ("mean", "attack_repeat_rate"),
            "S1_status_v22": ("verdict", "S1"),
        },
        "status": "UPHELD",
        "status_reason": "攻击量（553 条/10 局）、攻击/分 15.3、有效率 0.275、无效切换 0.762 "
                         "与旧值同量级；“刷屏但有效率低”的行为结论成立。",
        "new_judgment": "结论维持（V0：NOT_MASTERED；v2.2：UNDETERMINED，n=10<20）。"
                        "注意 pooled 有效率（0.148）与逐局均值（0.275）不是同一统计量，"
                        "旧报告用的是均值口径。",
    },
    {
        "id": "SA-04",
        "source": "reports/dsh-learning/skill-eval-runs.md:58-60（203156 训练期 20 局，V0 阈值）",
        "original": "203156（ppo-shared-20261030-203156，20 局）：first_attack_s=102.529、"
                    "attacks_per_min=5.513、attack_effective6_rate=0.322；S1/S2/S3 NOT_MASTERED",
        "run": "ppo_shared_203156",
        "old_metrics": {"first_attack_s": 102.529, "attacks_per_min": 5.513,
                        "attack_effective6_rate": 0.322},
        "new_metrics": {},
        "status": "UNKNOWN",
        "status_reason": "该 run 目录（reports/rl/ppo-shared-20261030-203156）有 119 个 result.json，"
                         "历史技能记录未保存所用 20 局的 episode_id 清单，无法唯一重建原样本；"
                         "用全量局重算会改变样本定义（违反“标准不变”）。",
        "new_judgment": "无法按原样重判（UNKNOWN）。需要重新固定样本后重跑；"
                        "不得用 24 局 fixed-eval（P3-02）的结果替代这 20 局训练期样本。",
    },
    # ================= 203156 24 局 fixed-eval =================
    {
        "id": "P3-02",
        "source": "reports/dsh-learning/p3-exec-evidence/baseline203156-skill-record.md:15-17",
        "original": "203156：S1 NOT_MASTERED（n=24）、S2 NOT_MASTERED（n=22）、S3 NOT_MASTERED（n=22）；"
                    "记录中 S1 attack_effective6_rate=0.0、S2 close=0.0",
        "run": "baseline_203156_24",
        "old_metrics": {"S1_attack_effective6_rate": 0.0, "S2_close": 0.0,
                        "S2_ineffective_switch_rate": 0.4545,
                        "S3_attack_concentration": 0.474,
                        "S3_kills_per_1000_unit_damage": 2.9206},
        "new_metrics": {
            "attack_effective6_rate_mean": ("mean", "attack_effective6_rate"),
            "attack_effective6_rate_pooled": ("pooled", "attack_effective6_rate"),
            "env_attack_effective_rate_pooled": ("pooled", "env_attack_effective_rate"),
            "close_multi_unit_effective_share_mean": (
                "mean", "close_multi_unit_effective_share_cell"),
            "multi_unit_attack_orders_sum": ("sum", "multi_unit_attack_orders"),
            "S1_status": ("verdict", "S1"), "S1_n": ("verdict_n", "S1"),
            "S2_status": ("verdict", "S2"), "S2_n": ("verdict_n", "S2"),
            "S3_status": ("verdict", "S3"), "S3_n": ("verdict_n", "S3"),
            "S3_pass_rate": ("verdict_pass_rate", "S3"),
        },
        "status": "PARTIALLY_SUPERSEDED",
        "status_reason": "S1 的 0.0 有效率与同批 env 真值 24.44% 自相矛盾（旧离线口径失效），"
                         "重算 6s 口径为 0.270（均值）/0.365（pooled）；"
                         "但 S2 的 close=0 是真实的——该模型 24 局只有 63 条去重攻击、全部单单位，"
                         "不是结构性 0；三个技能状态重算后仍 NOT_MASTERED。",
        "new_judgment": "状态维持 NOT_MASTERED；S1 的 0.0 应作废并改用重算值；"
                        "S2 的 0 是真实（单单位攻击），可作为“无编组攻击”的证据。",
    },
    # ================= P0 重复攻击诊断（183k） =================
    {
        "id": "P0-01",
        "source": "reports/autonomous_learning/P0_IMPLEMENTATION_REPORT.md:17,131",
        "original": "重复率 48.8%（order_repeat_rate=0.4884）",
        "run": "trained_183k_24",
        "old_metrics": {"order_repeat_rate": 0.4884},
        "new_metrics": {
            "attack_repeat_rate_mean": ("mean", "attack_repeat_rate"),
            "consecutive_identical_action_ratio_mean": (
                "mean", "consecutive_identical_action_ratio"),
            "max_identical_run_max": ("max", "max_identical_run"),
        },
        "status": "UPHELD",
        "status_reason": "按修复后时间轴重算重复率 0.4932，与旧值 0.4884 差 0.005——"
                         "审计报告推测的“48.8% 不可靠”并不成立；"
                         "另外逐字连续相同动作比例 0.83、最长连续 300+ 条，重复行为更严重。",
        "new_judgment": "重复率数量级维持；“大量重复指令”成立且更严重。"
                        "但 48.8% 只描述攻击指令去重，不等于效率或能力。",
    },
    {
        "id": "P0-02",
        "source": "reports/autonomous_learning/P0_IMPLEMENTATION_REPORT.md:137",
        "original": "中位“攻击→首次伤害”延迟 15.16s",
        "run": "trained_183k_24",
        "old_metrics": {"median_attack_to_damage_delay_s": 15.16},
        "new_metrics": {
            "median_attack_to_damage_delay_s_mean": (
                "mean", "median_attack_to_damage_delay_s"),
            "attack_effective30_rate_pooled": ("pooled", "attack_effective30_rate"),
        },
        "status": "SUPERSEDED",
        "status_reason": "延迟按错位时间轴计算；修复后中位延迟 0.47s（攻击时目标通常已在掉血）。",
        "new_judgment": "15.16s 作废；“攻击到伤害有明显延迟”的方向性结论同时失效。",
    },
    {
        "id": "P0-03",
        "source": "reports/autonomous_learning/P0_IMPLEMENTATION_REPORT.md:17",
        "original": "6s 有效攻击率 5.3%，30s 内造成伤害的比例 25.2%（据此论证“窗口太短不是主因”）",
        "run": "trained_183k_24",
        "old_metrics": {"attack_effective6_rate": 0.0531,
                        "attack_effective30_rate": 0.252},
        "new_metrics": {
            "attack_effective6_rate_mean": ("mean", "attack_effective6_rate"),
            "attack_effective6_rate_pooled": ("pooled", "attack_effective6_rate"),
            "attack_effective30_rate_mean": ("mean", "attack_effective30_rate"),
            "attack_effective30_rate_pooled": ("pooled", "attack_effective30_rate"),
        },
        "status": "SUPERSEDED",
        "status_reason": "两个窗口都显著高于旧值；6s 与 30s 的差距从 4.7 倍缩小到 1.1 倍。",
        "new_judgment": "旧论证链条（“6s 低 + 30s 也只有 25% → 不是窗口问题”）整体失效。",
    },
    # ================= 旧 75 局基线 =================
    {
        "id": "OLD-01",
        "source": "reports/dsh-learning/skill-metrics.md:88；"
                  "reports/audit-2026-10-31/RA2_AI_REAUDIT_2026-10-31.md:223（75 局审计）",
        "original": "旧 75 局基线：6s 有效率 0.2257、目标切换率 84.8%、43.2% 攻击目标整局未掉血",
        "run": "old_baseline_75g",
        "old_metrics": {"attack_effective6_rate": 0.2257,
                        "ineffective_switch_rate": 0.8484,
                        "attacked_targets_zero_damage_share": 0.432},
        "new_metrics": {
            "attack_effective6_rate_pooled": ("pooled", "attack_effective6_rate"),
            "attack_effective6_rate_mean": ("mean", "attack_effective6_rate"),
            "ineffective_switch_rate_mean": ("mean", "ineffective_switch_rate"),
            "attacked_targets_zero_damage_share_mean": (
                "mean", "attacked_targets_zero_damage_share"),
        },
        "status": "PARTIALLY_SUPERSEDED",
        "status_reason": "有效率（pooled 0.2237 vs 0.2257）与“目标整局未掉血”（0.448 vs 0.432）基本维持；"
                         "但“无效切换率 84.8%”重算为 0.6836，差异 0.17，旧值不可直接引用。",
        "new_judgment": "有效率基线可继续使用（须标注 pooled 口径）；无效切换率改为 0.68；"
                        "43.2%→44.8% 维持。",
    },
    # ================= 2026-10-31 审计报告自身对上述数字的判断 =================
    {
        "id": "AUD-01",
        "source": "reports/audit-2026-10-31/RA2_AI_REAUDIT_2026-10-31.md:223",
        "original": "“Stage A 有效攻击率 49.8%（R1）→ 5.3%（Experiment A 后，实测 24 局）；"
                    "目标切换率 84.8%；43.2% 攻击目标整局未掉血（75 局审计）”",
        "run": "trained_183k_24",
        "old_metrics": {"r1_effective6_rate": 0.4979,
                        "183k_effective6_rate": 0.0531,
                        "ineffective_switch_rate": 0.8484,
                        "zero_damage_share": 0.432},
        "new_metrics": {
            "r1_effective6_rate_pooled": ("pooled", "attack_effective6_rate", "stage_a_r1"),
            "183k_effective6_rate_mean": ("mean", "attack_effective6_rate"),
            "183k_effective6_rate_pooled": ("pooled", "attack_effective6_rate"),
            "old75_ineffective_switch_rate_mean": (
                "mean", "ineffective_switch_rate", "old_baseline_75g"),
            "old75_zero_damage_share_mean": (
                "mean", "attacked_targets_zero_damage_share", "old_baseline_75g"),
        },
        "status": "PARTIALLY_SUPERSEDED",
        "status_reason": "R1 的 49.8% 维持（pooled 0.4937）；“→5.3%”的下降方向是错的"
                         "（重算 0.594，反而更高）；84.8% 重算 0.6836；43.2% 重算 0.448。",
        "new_judgment": "“训练后有效率从 49.8% 掉到 5.3%”这一退化叙事作废；"
                        "R3/183k 的真实短板是攻击时机、集中度与 0 经济，不是“打不中”。",
    },
    {
        "id": "AUD-02",
        "source": "reports/audit-2026-10-31/_work/03_history_metrics.md:492-493（B11/B12）；"
                  "RA2_AI_REAUDIT_2026-10-31.md:1323（U-06）",
        "original": "“48.8% 重复率、15.16s 攻击→伤害延迟 = UNKNOWN（数值）；"
                    "5.31% 有效率需修工具后重算”",
        "run": "trained_183k_24",
        "old_metrics": {"order_repeat_rate": 0.4884,
                        "median_attack_to_damage_delay_s": 15.16},
        "new_metrics": {
            "attack_repeat_rate_mean": ("mean", "attack_repeat_rate"),
            "median_attack_to_damage_delay_s_mean": (
                "mean", "median_attack_to_damage_delay_s"),
            "attack_effective6_rate_mean": ("mean", "attack_effective6_rate"),
        },
        "status": "PARTIALLY_SUPERSEDED",
        "status_reason": "修复后：重复率 0.4932（旧 48.8% 实际成立，审计的“不可靠”判断过严）；"
                         "延迟 0.47s（旧 15.16s 确实不可靠）；有效率 0.594（旧 5.31% 不可靠）。",
        "new_judgment": "三项中两项确认旧值失效、一项（重复率）恢复为可信。"
                        "B12 的 UNKNOWN 可以关闭：重复率可信、延迟改值。",
    },
    # ================= 210239（无法重建样本） =================
    {
        "id": "L210239-01",
        "source": "reports/dsh-learning/skill-eval-runs.md:73-75（210239，20 局）",
        "original": "210239：attack_effective6_rate=0.158、无效切换 0.838；S1/S2/S3 NOT_MASTERED",
        "run": "ppo_shared_210239",
        "old_metrics": {"attack_effective6_rate": 0.158,
                        "ineffective_switch_rate": 0.838},
        "new_metrics": {},
        "status": "UNKNOWN",
        "status_reason": "该 run 目录有 73 个 result.json（33 局已确认 + 40 interrupted），"
                         "历史技能记录未保存 20 局清单，无法唯一重建原样本。",
        "new_judgment": "无法按原样重判（UNKNOWN）；本报告的 33 局全量聚合只能作补充观察，"
                        "不得冒充同一判定。",
    },
]


def main():
    started = time.time()
    runs = {}
    for name, spec in RUNS.items():
        runs[name] = analyze_run(name, spec)
        pooled = runs[name]["recomputed"]["pooled"]
        print(f"[history] {name}: n={runs[name]['n_episodes']} "
              f"kinds={runs[name]['kinds']} "
              f"unique={pooled['attack_orders_unique']} "
              f"eff6_pooled={pooled['attack_effective6_rate']} "
              f"env_pooled={pooled['env_attack_effective_rate']} "
              f"S1={(runs[name]['recomputed']['skill_verdicts']['S1'] or {}).get('status')}"
              f"/n={(runs[name]['recomputed']['skill_verdicts']['S1'] or {}).get('n_episodes')} "
              f"S2={(runs[name]['recomputed']['skill_verdicts']['S2'] or {}).get('status')} "
              f"S3={(runs[name]['recomputed']['skill_verdicts']['S3'] or {}).get('status')}",
              flush=True)

    claims = []
    for claim in CLAIMS:
        run = runs.get(claim["run"]) or {}
        resolved = {}
        for key, spec in (claim.get("new_metrics") or {}).items():
            stat, name = spec[0], spec[1]
            target = runs.get(spec[2]) if len(spec) > 2 else run
            try:
                resolved[key] = resolve(target, stat, name)
            except Exception as exc:  # noqa: BLE001
                resolved[key] = f"ERROR:{exc}"
        entry = dict(claim)
        entry["new_values"] = resolved
        entry["run_n_episodes"] = run.get("n_episodes")
        claims.append(entry)

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "task": "M0-B task 3: rejudge historical P3/P4 skill conclusions "
                "(fixed metric definitions, unchanged thresholds)",
        "method": {
            "old_values": "REPORTED: read from p3-exec-evidence skill record JSON "
                          "(programmatic) or historical report text (with line refs)",
            "new_values": "recomputed from raw episodes with metrics_lib + trace_parser "
                          "(fixed 6s rule) + skill_metrics (unchanged thresholds)",
            "skill_standard": {
                "thresholds_version": skill_metrics.THRESHOLDS_VERSION,
                "thresholds_hash": skill_metrics.THRESHOLDS_HASH,
                "mastery_rule_id": skill_metrics.MASTERY_RULE_ID,
                "min_episodes": skill_metrics.MASTERY["min_episodes"],
                "wilson_low": skill_metrics.MASTERY["wilson_low"],
                "s1": {k: v for k, v in skill_metrics.SKILLS["S1"]["indicators"].items()},
                "s2": {k: v for k, v in skill_metrics.SKILLS["S2"]["indicators"].items()},
                "s3": {k: v for k, v in skill_metrics.SKILLS["S3"]["indicators"].items()},
            },
            "spatial_fix": "close_multi_unit_effective_share / retreat_* recomputed from "
                           "cell/xyz; trace_parser values kept as *_tp_* for transparency",
            "sample_rule": "每个 run 用该目录下全部已确认局（含 timeout；interrupted 排除），"
                           "与历史 skill record 的 total/used 一致；210239 无法还原 → UNKNOWN",
        },
        "status_legend": {
            "SUPERSEDED": "旧数值/旧判定依据已被重算结果取代（结论本身可能方向不变，但引用值失效）",
            "UPHELD": "重算后判定维持（数值可能小幅变化）",
            "PARTIALLY_SUPERSEDED": "判定状态维持，但被引用的指标值/理由失效，必须改引用",
            "UNKNOWN": "样本无法唯一重建或指标不可计算，不能重判",
        },
        "runs": runs,
        "claims": claims,
        "elapsed_seconds": round(time.time() - started, 2),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[history] wrote {OUT} ({OUT.stat().st_size} bytes) in "
          f"{payload['elapsed_seconds']}s")


if __name__ == "__main__":
    main()
