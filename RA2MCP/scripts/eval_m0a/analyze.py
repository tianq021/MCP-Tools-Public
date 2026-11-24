"""M0-A 单局能力分析（只读解析 episode JSONL + result.json）。

从一局真实对局中提取“模型自身做了什么”，用于区分模型行为与脚本代做行为：
- 决策来源统计（模型 vs 非模型）：full env 中非模型动作必须为 0，否则标记脚本干扰；
- MCV 展开：DEPLOY 指令数、是否出现 Construction Yard、首次出现时间；
- 建设：建筑类型集合、电力/矿厂/兵营/工厂是否出现、建筑数峰值；
- 经济：资金序列（起止/峰谷）、矿车/矿厂证据；
- 生产：单位类型/数量峰值、新类型；
- 战斗：攻击指令数（动作行）、有效攻击（result truth 指标）、目标数、目标切换；
- 组织：多单位指令占比、最大选择宽度、单位空间离散度；
- 结局：win/defeat/timeout/crash + elapsed。
"""
import json
import pathlib
from collections import Counter

MODEL_SOURCES = ("ppo", "model", "rl", "policy")
NON_MODEL_SOURCES = ("fixed_economy", "rule", "script", "teacher")


def _read_rows(jsonl_path):
    rows = []
    with open(jsonl_path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:  # noqa: BLE001
                continue
    return rows


def _is_building(obj):
    return obj.get("abstract") == "ABSTRACT_TYPE_BUILDING"


def _placed(obj):
    return bool(obj.get("on_map")) and not bool(obj.get("in_limbo"))


def _money(row):
    if row.get("money") is not None:
        return int(row.get("money") or 0)
    for house in row.get("houses") or []:
        if house.get("current_player"):
            return int(house.get("money") or 0)
    return None


def _power(row):
    for house in row.get("houses") or []:
        if house.get("current_player"):
            return int(house.get("power_output") or 0), int(house.get("power_drain") or 0)
    return None, None


def _dist2(a, b):
    ax, ay = a
    bx, by = b
    return (ax - bx) ** 2 + (ay - by) ** 2


def analyze_episode(jsonl_path, result):
    """返回单局能力分析 dict（字段缺失时给 None，不猜测）。"""
    jsonl_path = pathlib.Path(jsonl_path)
    result = result or {}
    rows = _read_rows(jsonl_path)
    observations = [r for r in rows if r.get("type") == "observation"]
    actions = [r for r in rows if r.get("type") == "action"]
    model_actions = [a for a in actions if a.get("decision_source") in MODEL_SOURCES]
    non_model_actions = [a for a in actions if a.get("decision_source") in NON_MODEL_SOURCES]

    action_counts = Counter((a.get("action") or "UNKNOWN").upper() for a in model_actions)
    script_counts = Counter((a.get("action") or "UNKNOWN").upper() for a in non_model_actions)

    # ---- MCV 展开与建筑 ----
    building_first = {}
    unit_first = {}
    initial_unit_types = set()
    initial_unit_count = 0
    initial_seen = False
    building_peak = 0
    unit_peak = 0
    money_series = []
    power_series = []
    cy_first_t = None
    harvesters = set()
    for obs in observations:
        t = float(obs.get("t") or 0.0)
        own = [o for o in (obs.get("own_objects") or []) if _placed(o)]
        buildings = [o for o in own if _is_building(o)]
        units = [o for o in own if not _is_building(o)]
        if not initial_seen:
            initial_seen = True
            initial_unit_count = len(units)
            initial_unit_types = {str(u.get("type_name") or "?") for u in units}
        building_peak = max(building_peak, len(buildings))
        unit_peak = max(unit_peak, len(units))
        for building in buildings:
            name = str(building.get("type_name") or "?")
            if name not in building_first:
                building_first[name] = t
            if "Construction Yard" in name and cy_first_t is None:
                cy_first_t = t
        for unit in units:
            name = str(unit.get("type_name") or "?")
            if name not in unit_first:
                unit_first[name] = t
            lowered = name.lower()
            if any(word in lowered for word in ("miner", "harvester")):
                harvesters.add(name)
        m = _money(obs)
        if m is not None:
            money_series.append([round(t, 2), m])
        po, pd = _power(obs)
        if po is not None:
            power_series.append([round(t, 2), po, pd])
    new_unit_types = sorted(set(unit_first) - initial_unit_types)

    # ---- 战斗/组织（动作行；按 JSONL 顺序关联“动作时存活单位数”，用于区分全选/子集） ----
    actions_with_pool = []
    _alive = 0
    for row in rows:
        if row.get("type") == "observation":
            own = [o for o in (row.get("own_objects") or []) if _placed(o) and not _is_building(o)]
            _alive = len(own)
        elif row.get("type") == "action" and row.get("decision_source") in MODEL_SOURCES:
            actions_with_pool.append((row, _alive))
    attack_actions = [a for a in model_actions if (a.get("action") or "").upper() == "ATTACK"]
    attack_targets = Counter(a.get("target_address") for a in attack_actions)
    group_sizes = [len(a.get("object_addresses") or []) for a in model_actions]
    multi_actions = sum(1 for n in group_sizes if n >= 2)
    # 多单位 ATTACK（协同攻击）与“子集选择”（非全选、非单选）分别统计
    multi_unit_attacks = sum(1 for a in attack_actions
                             if len(a.get("object_addresses") or []) >= 2)
    subset_selections = 0
    all_unit_selections = 0
    all_unit_attacks = 0
    subset_group_attacks = 0
    for row, alive in actions_with_pool:
        n = len(row.get("object_addresses") or [])
        is_attack = (row.get("action") or "").upper() == "ATTACK"
        if alive >= 2 and n == alive:
            all_unit_selections += 1
            if is_attack:
                all_unit_attacks += 1
        elif 2 <= n < max(2, alive):
            subset_selections += 1
            if is_attack:
                subset_group_attacks += 1
    target_switches = 0
    prev_target = None
    for a in attack_actions:
        target = a.get("target_address")
        if prev_target is not None and target != prev_target:
            target_switches += 1
        prev_target = target

    # ---- 空间离散度（最后一帧己方非建筑单位；单位:格） ----
    dispersion = None
    if observations:
        own = [o for o in (observations[-1].get("own_objects") or []) if _placed(o) and not _is_building(o)]
        positions = []
        for o in own:
            cell = o.get("cell")
            if isinstance(cell, (list, tuple)) and len(cell) >= 2:
                positions.append((float(cell[0]), float(cell[1])))
        if len(positions) >= 2:
            best = max(_dist2(positions[i], positions[j])
                       for i in range(len(positions)) for j in range(i + 1, len(positions)))
            dispersion = round(best ** 0.5, 2)

    metrics = result.get("metrics") or {}
    issued_env = int(metrics.get("attacks_issued") or 0)
    effective_env = int(metrics.get("attacks_effective") or 0)
    outcome = result.get("kind") or "unknown"
    lower_btypes = {name.lower() for name in building_first}
    lower_utypes = {name.lower() for name in unit_first}

    def _has_building(*words):
        return any(all(w in name for w in words) for name in lower_btypes)

    def _has_unit(*words):
        return any(all(w in name for w in words) for name in lower_utypes)

    start_money = money_series[0][1] if money_series else None
    end_money = money_series[-1][1] if money_series else None
    peak_money = max((m for _, m in money_series), default=None)
    min_money = min((m for _, m in money_series), default=None)

    flags = {
        "is_script_free_full_env": len(non_model_actions) == 0,
        "can_deploy_mcv": bool(action_counts.get("DEPLOY")) and cy_first_t is not None,
        "deploy_attempted": bool(action_counts.get("DEPLOY")),
        "can_build_any": bool(building_first),
        "can_build_power": _has_building("power"),
        "can_build_refinery": _has_building("refinery"),
        "can_build_barracks": _has_building("barracks"),
        "can_build_factory": _has_building("factory"),
        "can_mine": bool(harvesters) or _has_building("refinery"),
        "can_produce_units": bool(int(action_counts.get("PRODUCE", 0))
                                  or unit_peak > initial_unit_count
                                  or new_unit_types),
        "issues_attack": len(attack_actions) > 0,
        "effective_attack": effective_env > 0,
        "organizes_groups": subset_group_attacks > 0 or subset_selections >= 3,
        "completes_full_match": outcome in ("win", "defeat", "game_end"),
        "wins": outcome == "win",
    }
    return {
        "episode_id": result.get("episode_id") or jsonl_path.stem,
        "jsonl": str(jsonl_path),
        "result": str(jsonl_path.parent / (jsonl_path.name.replace(".jsonl", ".result.json"))),
        "outcome": outcome,
        "elapsed_s": result.get("elapsed_s"),
        "rows": len(rows),
        "observations": len(observations),
        "model_actions": len(model_actions),
        "non_model_actions": len(non_model_actions),
        "action_counts": dict(action_counts),
        "script_action_counts": dict(script_counts),
        "deploy_orders": int(action_counts.get("DEPLOY", 0)),
        "produce_orders": int(action_counts.get("PRODUCE", 0)),
        "place_orders": int(action_counts.get("PLACE", 0)),
        "move_orders": int(action_counts.get("MOVE", 0)),
        "attack_orders": len(attack_actions),
        "multi_unit_attack_orders": multi_unit_attacks,
        "all_unit_attack_orders": all_unit_attacks,
        "subset_group_attack_orders": subset_group_attacks,
        "stop_orders": int(action_counts.get("STOP", 0)),
        "initial_unit_count": initial_unit_count,
        "initial_unit_types": sorted(initial_unit_types),
        "new_unit_types": new_unit_types,
        "construction_yard_first_s": cy_first_t,
        "building_types": sorted(building_first),
        "building_first_seen": {k: round(v, 2) for k, v in sorted(building_first.items())},
        "building_peak": building_peak,
        "unit_types": sorted(unit_first),
        "unit_first_seen": {k: round(v, 2) for k, v in sorted(unit_first.items())},
        "unit_peak": unit_peak,
        "harvester_types": sorted(harvesters),
        "money": {"start": start_money, "end": end_money, "min": min_money,
                  "max": peak_money, "n": len(money_series)},
        "power_last": power_series[-1] if power_series else None,
        "attacks_issued_env": issued_env,
        "attacks_effective_env": effective_env,
        "attack_effective_rate_env": (round(effective_env / issued_env, 4) if issued_env else None),
        "attack_effective_rate_action_rows": (round(effective_env / len(attack_actions), 4)
                                             if attack_actions else None),
        "unique_attack_targets": len(attack_targets),
        "target_switches": target_switches,
        "mean_group_size": (round(sum(group_sizes) / len(group_sizes), 3) if group_sizes else None),
        "multi_unit_action_share": (round(multi_actions / len(model_actions), 4)
                                    if model_actions else None),
        "subset_selection_actions": subset_selections,
        "all_unit_selection_actions": all_unit_selections,
        "unit_dispersion_cells_last": dispersion,
        "truth_kills_infantry": int(metrics.get("truth_kills_infantry") or 0),
        "truth_kills_vehicle": int(metrics.get("truth_kills_vehicle") or 0),
        "truth_building_destroyed": int(metrics.get("truth_building_destroyed") or 0),
        "damage_dealt": metrics.get("damage_dealt"),
        "own_hp_lost": metrics.get("own_hp_lost"),
        "capabilities": flags,
    }
