"""汇总 M0-A 批量评测结果 → Markdown 表格 + JSON（供报告引用）。"""
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
BASE = ROOT / "reports" / "m0a-2026-11-01"

# 历史报告中的原始记录（REPORTED；用于对照，不作为事实）
OLD_REPORTED = {
    "stage_a_r1": {"label": "R1（Stage A）", "old_effective6": 0.4979, "source": "reports/stage-A-round1-* / coordination"},
    "stage_a_r2": {"label": "R2（Stage A）", "old_effective6": 0.4227, "source": "reports/stage-A-round2-*"},
    "stage_a_r3": {"label": "R3（Stage A）", "old_effective6": 0.1485, "source": "reports/stage-A-round3-*"},
    "old_baseline_75g": {"label": "旧 75 局基线", "old_effective6": 0.2257, "source": "reports/dsh-direction-and-parallel-20261029.md"},
    "baseline_203156": {"label": "203156 基线", "old_effective6": 0.2450, "source": "报告/技能记录（env 口径）"},
    "experiment_a_eval": {"label": "183k（Experiment A，24局）", "old_effective6": 0.0531, "source": "reports/dsh-learning/p4-training-campaign-report.md（trace_parser bug 口径）"},
}


def load(path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def main():
    batch = load(BASE / "batch_summary.json")
    recompute = load(BASE / "history_recompute.json")
    out = {"models": {}, "history": []}

    lines = ["# M0-A 汇总表（自动生成）", ""]
    lines.append("## 1. 历史模型全环境（full env，无 RulePolicy）重评估")
    lines.append("")
    lines.append("| 模型 | N | win | defeat | timeout | crash | 建筑峰值均 | 单位峰值均 | DEPLOY成功率 | 建造任一项 | 电力 | 矿厂 | 生产单位 | 采矿 | 下达攻击 | 有效攻击 | 多单位攻击 | 脚本动作 | 交换比 | 模型SHA |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for name, m in batch.get("models", {}).items():
        s = m.get("summary") or {}
        c = s.get("capabilities") or {}
        k = s.get("kinds") or {}
        lines.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | `{}` |".format(
            name, s.get("games"),
            k.get("win", 0), k.get("defeat", 0), k.get("timeout", 0), k.get("crash", 0),
            s.get("mean_building_peak"), s.get("mean_unit_peak"),
            c.get("can_deploy_mcv"), c.get("can_build_any"), c.get("can_build_power"),
            c.get("can_build_refinery"), c.get("can_produce_units"), c.get("can_mine"),
            c.get("issues_attack"), c.get("effective_attack"), c.get("multi_unit_attack"),
            c.get("script_free"), s.get("damage_exchange"),
            (m.get("model_sha256") or "")[:12]))
        out["models"][name] = {"summary": s, "status": m.get("status"),
                               "model_sha256": m.get("model_sha256"),
                               "wall_seconds": m.get("wall_seconds")}
    lines.append("")
    lines.append("> 说明：full env = `RA2FullEnv`（模型控制经济/建造/生产/单位，`_economy()` 为 no-op，无 `RulePolicy`）；N=10；300s；确定性策略；6 workers。`脚本动作`=非模型 decision_source 占比（应为 0）。")
    lines.append("")

    lines.append("## 2. 历史“6s 有效攻击率”口径修复对照")
    lines.append("")
    lines.append("| 运行 | 历史报告值（REPORTED） | 修复后（可见 HP 差分，6s） | 修复后（env 真值 3s） | 攻击去重前/后 | 重复率 | 结论 |")
    lines.append("|---|---|---|---|---|---|---|")
    for key, rc in (recompute.get("runs") or {}).items():
        if "error" in rc:
            continue
        meta = OLD_REPORTED.get(key, {})
        old = meta.get("old_effective6")
        old_s = f"{old:.4f}" if isinstance(old, (int, float)) else "-"
        env_rate = rc.get("env_effective_rate")
        env_s = f"{env_rate:.4f}" if isinstance(env_rate, (int, float)) else "-"
        old_vs_new = ""
        if isinstance(old, (int, float)) and rc.get("effective6_rate_fixed") is not None:
            delta = rc["effective6_rate_fixed"] - old
            old_vs_new = f"Δ={delta:+.4f}"
        lines.append("| {} | {} | {} | {} | {}/{} | {} | {} |".format(
            meta.get("label", key), old_s, rc.get("effective6_rate_fixed"), env_s,
            rc.get("unique_attack_orders_total"), rc.get("unique_attack_orders_total"),
            rc.get("mean_order_repeat_rate"), old_vs_new))
        out["history"].append({"run": key, "label": meta.get("label", key),
                               "old_reported": old, "fixed_visible6": rc.get("effective6_rate_fixed"),
                               "env_truth3": env_rate,
                               "unique_attacks": rc.get("unique_attack_orders_total"),
                               "repeat_rate": rc.get("mean_order_repeat_rate"),
                               "episodes": rc.get("episodes")})
    lines.append("")
    lines.append("> `修复后（env 真值 3s）` 来自环境内 TruthTracker 的逐步统计（有效=攻击后 3s 内目标地址掉血），与 trace_parser 的可见 HP 差分（6s 窗口）定义不同；两者都不是“命中率”，报告时必须带口径。")
    lines.append("")
    lines.append("## 3. 备注")
    lines.append("- `R1_COMBAT_REF` 使用 RA2CombatEnv（脚本经济），其 `脚本动作` 应 >0，仅作对照，不得计为模型自主能力。")
    lines.append("- 设备/环境：见各 `eval-*/evaluation.json` 的 protocol 与 game_slots。")
    text = "\n".join(lines) + "\n"
    (BASE / "summary_tables.md").write_text(text, encoding="utf-8")
    (BASE / "summary_tables.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
