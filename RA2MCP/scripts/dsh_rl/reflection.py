"""Evidence-based episode reviews. PPO learns from rewards, not this prose."""
import collections
import json


def review(result, rows, reward_parts):
    actions = collections.Counter()
    buildings, units = set(), set()
    for row in rows:
        if row.get("type") == "action":
            actions[row.get("action", "unknown")] += 1
        if row.get("type") == "observation":
            for obj in row.get("own_objects", []):
                if obj.get("in_limbo") or not obj.get("on_map", True):
                    continue
                target = buildings if obj.get("abstract") == "ABSTRACT_TYPE_BUILDING" else units
                target.add(obj.get("type_name", "unknown"))
    metrics, stats = result["metrics"], result["stats"]
    findings = []
    if not buildings:
        findings.append("本局未观察到已放置的己方建筑。")
    if metrics.get("damage_dealt", 0) == 0:
        findings.append("本局未观察到对可见敌方造成伤害；这不能单独确定失败原因。")
    if stats.get("invalid_orders", 0):
        findings.append(f"记录到 {stats['invalid_orders']} 条失败指令，可检查原生回执。")
    if result["kind"] not in ("win", "defeat"):
        findings.append("没有确认胜负，不把中断、异常或退出当成胜利。")
    return {"episode_id": result["episode_id"], "kind": result["kind"],
            "elapsed_s": result["elapsed_s"], "metrics": metrics, "stats": stats,
            "action_counts": dict(actions), "observed_building_types": sorted(buildings),
            "observed_unit_types": sorted(units), "reward_parts": dict(reward_parts),
            "findings": findings,
            "learning": "PPO uses real transitions and terminal rewards. This diagnostic text is not model input."}


def write_reflection(out, result, rows, reward_parts):
    data = review(result, rows, reward_parts)
    folder = out / "reflections"
    folder.mkdir(exist_ok=True)
    (folder / (result["episode_id"] + ".json")).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"# 对局复盘：{data['episode_id']}", "",
             f"结果：{data['kind']}；时长：{data['elapsed_s']:.1f} 秒。",
             f"观察到的敌方 HP 损失：{data['metrics'].get('damage_dealt', 0)}。",
             f"己方 HP 损失奖励分项（含消失单位，MCV 转换除外）：{reward_parts.get('own_hp', 0):.3f}。",
             "观察到的建筑类型：" + ("、".join(data['observed_building_types']) or "无") + "。", ""]
    lines.extend("- " + item for item in data["findings"])
    lines += ["", "## 动作统计", "", json.dumps(data['action_counts'], ensure_ascii=False),
              "", "## 指令时间线（下达指令不等于实际达成）", ""]
    for row in rows:
        if row.get('type') == 'action':
            lines.append('- ' + json.dumps(row, ensure_ascii=False, separators=(',', ':')))
    lines += ["", "学习通过真实动作奖励和终局反馈进行 PPO 参数更新；本复盘是诊断记录，不是模型语言推理。", ""]
    (folder / (result["episode_id"] + ".md")).write_text("\n".join(lines), encoding="utf-8")
