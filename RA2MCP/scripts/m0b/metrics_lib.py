"""M0-B 指标库：从单局 episode JSONL + result.json 计算战斗/战术指标（只读、可重复）。

设计约束（M0-B）：
- 不训练、不改模型、不改奖励/动作/观测/桥接；只读解析历史与 M0-A 的 episode 数据。
- 6 秒有效攻击等"可见 HP"指标复用 `scripts/dsh_learning/trace_parser.py` 的**修复后**
  口径（动作时间 = 所属前一条观测的 t；同组单位+同目标+≤1.5s 去重；目标在
  [t, t+6s] 内可见 HP 下降即判有效）。本模块不重新定义该口径，只做交叉核对。
- 空间距离统一用观测里的 `cell`（格）字段；`xyz` 只在把 MOVE 目标点换算成格时使用
  （1 格 = 256 leptons）。这与 trace_parser 的 CLOSE_CELLS=8 / 30 格等阈值单位一致。
  说明：trace_parser._pos() 读的是 obj['x']/obj['y']，而真实 episode 的实体对象只有
  `cell`/`xyz`，因此旧的空间类指标在真实数据上结构性不可计算（见
  reports/m0b-2026-11-01/M0B_SKILL_REJUDGMENT.md）。本模块用 `cell` 显式重算，
  阈值/窗口保持不变。
- 所有无法从数据得到的东西写 None 或 "UNKNOWN"，不猜测。

字段命名与定义见 METRIC_DEFINITIONS（随输出一起落盘）。
"""
import json
import math
import pathlib
import statistics
from collections import Counter

MODEL_SOURCES = ("ppo", "model", "rl", "policy")

DECISION_SECONDS = 0.5
ATTACK_DEDUP_SECONDS = 1.5     # trace_parser.ATTACK_DEDUP_SECONDS
EFFECTIVE_WINDOW = 6.0          # trace_parser.EFFECTIVE_WINDOW
EFFECTIVE_WINDOW_SHORT = 3.0    # trace_parser 的 effective3
EFFECTIVE_WINDOW_LONG = 30.0    # M0-B：30s 宽松窗口（历史 P0 报告用的 30s 口径）
CLOSE_CELLS = 8.0               # trace_parser.CLOSE_CELLS
UNCONFIRMED_CELLS = 20.0        # trace_parser.UNCONFIRMED_CELLS
CONCURRENT_WINDOW = 3.0         # M0-B：判定"同一目标并发攻击"的时间窗
LOW_HP_RATIO = 0.35             # M0-B：低血量阈值（占本局该类型观测最大 HP 的比例）
LOW_HP_ENEMY_RANGE_CELLS = 30.0 # M0-B：低血量"有机会撤退"的敌人距离上限
RETREAT_CHECK_SECONDS = 10.0    # M0-B：撤退判定观察窗
RETREAT_DISTANCE_CELLS = 3.0    # M0-B：判定"确实后撤"的最小距离增量
MOVE_EFFECT_CHECK_SECONDS = 1.5 # M0-B：MOVE 是否有位移的观察窗
MOVE_ZERO_CELLS = 1.0           # M0-B：目标点距选择单位质心 <1 格视为"零位移指令"
MOVE_NO_DISPLACEMENT_CELLS = 0.5  # M0-B：1.5s 内质心位移 <0.5 格视为"未见位移"
CELL_SIZE = 256.0               # 1 格 = 256 leptons（xyz 单位）

BUILDING_WORDS = ("building", "house", "factory", "refinery", "power", "lab", "radar",
                  "helipad", "airfield", "shipyard", "defense", "plant", "yard",
                  "barracks", "tower")
NONCOMBAT_WORDS = ("harvester", "mcv", "miner", "refinery")


# --------------------------------------------------------------------------- #
# 基础读取 / 几何
# --------------------------------------------------------------------------- #
def load_jsonl(path):
    rows = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:  # noqa: BLE001
                continue
    return rows


def load_result(path):
    try:
        return json.loads(pathlib.Path(path).read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return {}


def is_building_obj(obj):
    return obj.get("abstract") == "ABSTRACT_TYPE_BUILDING"


def is_combat_name(name):
    lowered = (name or "").lower()
    if any(key in lowered for key in BUILDING_WORDS):
        return False
    if any(key in lowered for key in NONCOMBAT_WORDS):
        return False
    return True


def cell_of(obj):
    """实体格坐标（float tuple）；缺失返回 None。"""
    cell = obj.get("cell")
    if isinstance(cell, (list, tuple)) and len(cell) >= 2:
        try:
            return (float(cell[0]), float(cell[1]))
        except (TypeError, ValueError):
            return None
    xyz = obj.get("xyz")
    if isinstance(xyz, (list, tuple)) and len(xyz) >= 2:
        try:
            return (float(xyz[0]) / CELL_SIZE, float(xyz[1]) / CELL_SIZE)
        except (TypeError, ValueError):
            return None
    return None


def pos_of(obj):
    """亚格精度的连续坐标（格）：优先 xyz/256，其次 cell。用于位移/距离计算。"""
    xyz = obj.get("xyz")
    if isinstance(xyz, (list, tuple)) and len(xyz) >= 2:
        try:
            return (float(xyz[0]) / CELL_SIZE, float(xyz[1]) / CELL_SIZE)
        except (TypeError, ValueError):
            pass
    return cell_of(obj)


def dist_cells(a, b):
    if a is None or b is None:
        return None
    return math.hypot(a[0] - b[0], a[1] - b[1])


def centroid(points):
    points = [p for p in points if p is not None]
    if not points:
        return None
    return (sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points))


def dst_cell(coords):
    """动作 coordinates(xyz leptons) → 格坐标。"""
    if isinstance(coords, (list, tuple)) and len(coords) >= 2:
        try:
            return (float(coords[0]) / CELL_SIZE, float(coords[1]) / CELL_SIZE)
        except (TypeError, ValueError):
            return None
    return None


def _ratio(num, den):
    if not den:
        return None
    return round(float(num) / float(den), 4)


# --------------------------------------------------------------------------- #
# 观测/动作索引
# --------------------------------------------------------------------------- #
def index_observations(observations):
    """每帧的己方作战单位、可见敌人、HP 上限估计。"""
    frames = []
    hp_max = {}
    for obs in observations:
        own = []
        for obj in (obs.get("own_objects") or []):
            if not obj.get("on_map") or obj.get("in_limbo"):
                continue
            if is_building_obj(obj):
                continue
            name = str(obj.get("type_name") or "?")
            health = obj.get("health")
            try:
                health = int(health)
            except (TypeError, ValueError):
                health = None
            if health is not None:
                hp_max[name] = max(hp_max.get(name, 0), health)
            own.append({"addr": obj.get("addr"), "name": name,
                        "health": health, "cell": cell_of(obj),
                        "pos": pos_of(obj)})
        enemies = []
        for obj in (obs.get("visible_enemies") or []):
            enemies.append({"addr": obj.get("addr"),
                            "name": str(obj.get("type_name") or "?"),
                            "health": obj.get("health"),
                            "cell": cell_of(obj),
                            "pos": pos_of(obj),
                            "is_building": is_building_obj(obj)})
        frames.append({"t": float(obs.get("t") or 0.0), "own": own, "enemies": enemies})
    return frames, hp_max


def index_actions(rows):
    """模型动作 + 其所属前一条观测的 t / index（与 trace_parser 修复口径一致）。"""
    actions = []
    last_obs = None
    last_idx = -1
    obs_count = 0
    for row in rows:
        kind = row.get("type")
        if kind == "observation":
            obs_count += 1
            last_obs = row
            last_idx = obs_count - 1
        elif kind == "action" and row.get("decision_source") in MODEL_SOURCES:
            act = dict(row)
            act["obs_index"] = last_idx if last_idx >= 0 else 0
            act["obs_t"] = float(last_obs.get("t") or 0.0) if last_obs is not None else None
            act["action"] = (row.get("action") or "UNKNOWN").upper()
            act["time_mapping"] = ("preceding_observation"
                                   if last_obs is not None else "missing_observation")
            actions.append(act)
    return actions


def build_attacks(actions):
    """ATTACK 指令列表（原始 + 去重），去重规则与 trace_parser 完全一致。"""
    raw = []
    for act in actions:
        if act["action"] != "ATTACK":
            continue
        units = tuple(sorted(act.get("object_addresses") or []))
        raw.append({
            "row": act,
            "units": units,
            "target": act.get("target_address"),
            "target_type": act.get("target_type"),
            "n_units": len(units),
            "t": act.get("obs_t"),
            "obs_index": act.get("obs_index"),
            "coordinates": act.get("coordinates"),
        })
    unique = []
    for attack in raw:
        if unique and unique[-1]["units"] == attack["units"] \
                and unique[-1]["target"] == attack["target"] \
                and (attack["t"] or 0.0) - (unique[-1]["t"] or 0.0) <= ATTACK_DEDUP_SECONDS:
            continue
        unique.append(dict(attack))
    return raw, unique


def damage_series(frames):
    """相邻观测可见敌人血量差 → 掉血事件（等价 trace_parser._damage_series）。"""
    events = []
    for i in range(1, len(frames)):
        prev = {e["addr"]: e for e in frames[i - 1]["enemies"]}
        for enemy in frames[i]["enemies"]:
            old = prev.get(enemy["addr"])
            if old is None or old.get("name") != enemy.get("name"):
                continue
            try:
                delta = int(old.get("health") or 0) - int(enemy.get("health") or 0)
            except (TypeError, ValueError):
                continue
            if delta > 0:
                events.append({"t": frames[i]["t"], "addr": enemy["addr"], "damage": delta,
                               "is_building": enemy.get("is_building"),
                               "target_name": enemy.get("name")})
    return events


def effective_in(attack, damage_events, window):
    if attack.get("target") is None:
        return False
    t = attack.get("t") or 0.0
    return any(e["addr"] == attack["target"] and t - 1e-9 <= e["t"] <= t + window + 1e-9
               for e in damage_events)


def target_damage_in(attack, damage_events, window):
    if attack.get("target") is None:
        return 0
    t = attack.get("t") or 0.0
    return sum(e["damage"] for e in damage_events
               if e["addr"] == attack["target"] and t - 1e-9 <= e["t"] <= t + window + 1e-9)


# --------------------------------------------------------------------------- #
# 单局分析
# --------------------------------------------------------------------------- #
def analyze_episode(jsonl_path, result=None):
    """返回单局指标 dict（键名见 METRIC_DEFINITIONS）。"""
    jsonl_path = pathlib.Path(jsonl_path)
    result = result or load_result(
        jsonl_path.with_name(jsonl_path.name.replace(".jsonl", ".result.json")))
    rows = load_jsonl(jsonl_path)
    observations = [r for r in rows if r.get("type") == "observation"]
    frames, hp_max = index_observations(observations)
    actions = index_actions(rows)
    raw_attacks, unique_attacks = build_attacks(actions)
    damage_events = damage_series(frames)
    unit_damage_events = [e for e in damage_events if not e["is_building"]]
    building_damage_events = [e for e in damage_events if e["is_building"]]

    metrics = result.get("metrics") or {}
    stats = result.get("stats") or {}
    elapsed = float(result.get("elapsed_s") or 0.0)
    if not elapsed and frames:
        elapsed = frames[-1]["t"] - frames[0]["t"]
    times = [f["t"] for f in frames]
    gaps = [times[i] - times[i - 1] for i in range(1, len(times))]
    positive = [g for g in gaps if g > 0]
    cadence = statistics.median(positive) if positive else None

    attack_rows = [a for a in actions if a["action"] == "ATTACK"]
    action_counts = Counter(a["action"] for a in actions)
    non_model = [r for r in rows if r.get("type") == "action"
                 and r.get("decision_source") not in MODEL_SOURCES]

    # ---- 有效攻击（可见 HP 口径，复用 trace_parser 判定） ----
    for attack in unique_attacks:
        attack["effective6"] = effective_in(attack, damage_events, EFFECTIVE_WINDOW)
        attack["effective3"] = effective_in(attack, damage_events,
                                            EFFECTIVE_WINDOW_SHORT)
        attack["target_damage6"] = target_damage_in(attack, damage_events,
                                                    EFFECTIVE_WINDOW)
    for attack in unique_attacks:
        attack["effective30"] = effective_in(attack, damage_events, EFFECTIVE_WINDOW_LONG)
        t = attack.get("t") or 0.0
        delays = [e["t"] - t for e in damage_events
                  if e["addr"] == attack["target"] and 0.0 <= e["t"] - t <= EFFECTIVE_WINDOW_LONG]
        attack["damage_delay_s"] = min(delays) if delays else None
    effective6 = sum(1 for a in unique_attacks if a["effective6"])
    effective3 = sum(1 for a in unique_attacks if a["effective3"])
    effective30 = sum(1 for a in unique_attacks if a["effective30"])
    delays = [a["damage_delay_s"] for a in unique_attacks if a.get("damage_delay_s") is not None]

    # ---- 目标选择 / 切换 ----
    target_orders = Counter(a["target"] for a in unique_attacks)
    target_names = Counter(a["target_type"] or "UNKNOWN" for a in unique_attacks)
    # 用观测里的 abstract 判定目标是否建筑（更可靠）；缺可见记录时退回动作 target_type
    enemy_index = {}
    for frame in frames:
        for enemy in frame["enemies"]:
            enemy_index[enemy["addr"]] = enemy
    building_orders = 0
    unit_orders = 0
    unknown_type_orders = 0
    for attack in unique_attacks:
        enemy = enemy_index.get(attack["target"])
        if enemy is not None:
            if enemy.get("is_building"):
                building_orders += 1
            else:
                unit_orders += 1
        elif attack.get("target_type"):
            # 动作行自带 target_type：用名称规则兜底（建筑词表）
            if any(word in str(attack["target_type"]).lower() for word in BUILDING_WORDS):
                building_orders += 1
            else:
                unit_orders += 1
        else:
            unknown_type_orders += 1

    switches = 0
    ineffective_switches = 0
    switch_prev_absent = 0
    switch_prev_effective = 0
    switch_times = []
    for i in range(1, len(unique_attacks)):
        prev, cur = unique_attacks[i - 1], unique_attacks[i]
        if cur["target"] == prev["target"]:
            continue
        switches += 1
        switch_times.append((cur["t"] or 0.0) - (prev["t"] or 0.0))
        if not prev["effective6"]:
            ineffective_switches += 1
        if prev["effective6"]:
            switch_prev_effective += 1
        if frames:
            obs_index = min(int(cur.get("obs_index") or 0), len(frames) - 1)
            visible = {e["addr"] for e in frames[obs_index]["enemies"]}
            if prev["target"] not in visible:
                switch_prev_absent += 1

    # ---- 集火 / 编组 ----
    multi = [a for a in unique_attacks if a["n_units"] >= 2]
    multi_effective = [a for a in multi if a["effective6"]]
    all_unit_attacks = 0
    subset_multi = 0
    group_hist = Counter()
    concurrent = 0
    for i, attack in enumerate(unique_attacks):
        alive = len(frames[min(int(attack.get("obs_index") or 0),
                               max(0, len(frames) - 1))]["own"]) if frames else 0
        n = attack["n_units"]
        if alive >= 2 and n == alive:
            all_unit_attacks += 1
        if 2 <= n < max(2, alive):
            subset_multi += 1
        if n <= 1:
            group_hist["1"] += 1
        elif n <= 3:
            group_hist["2-3"] += 1
        elif n <= 6:
            group_hist["4-6"] += 1
        else:
            group_hist["7+"] += 1
        for j, other in enumerate(unique_attacks):
            if i == j or other["target"] != attack["target"]:
                continue
            if other["units"] == attack["units"]:
                continue
            if abs((other["t"] or 0.0) - (attack["t"] or 0.0)) <= CONCURRENT_WINDOW:
                concurrent += 1
                break

    # ---- 距离（cell 口径；修正 trace_parser 读 x/y 导致的结构性缺失） ----
    close_effective = 0
    unconfirmed = 0
    for attack in multi_effective:
        obs_index = min(int(attack.get("obs_index") or 0), max(0, len(frames) - 1))
        frame = frames[obs_index] if frames else {"own": [], "enemies": []}
        target_pos = None
        for enemy in frame["enemies"]:
            if enemy["addr"] == attack["target"]:
                target_pos = enemy["pos"]
                break
        positions = {u["addr"]: u["pos"] for u in frame["own"]}
        dists = [d for d in (dist_cells(positions.get(addr), target_pos)
                             for addr in attack["units"]) if d is not None]
        mind = min(dists) if dists else None
        if mind is not None and mind <= CLOSE_CELLS:
            close_effective += 1
        if mind is None or mind > UNCONFIRMED_CELLS:
            unconfirmed += 1

    # ---- 伤害与击杀 ----
    damage_units = sum(e["damage"] for e in unit_damage_events)
    damage_buildings = sum(e["damage"] for e in building_damage_events)
    truth_damage = metrics.get("damage_dealt")
    truth_kills_inf = int(metrics.get("truth_kills_infantry") or 0)
    truth_kills_veh = int(metrics.get("truth_kills_vehicle") or 0)
    truth_kills = truth_kills_inf + truth_kills_veh
    truth_buildings = int(metrics.get("truth_building_destroyed") or 0)
    own_hp_lost_obs = int(metrics.get("own_hp_lost") or 0)
    truth_own_removed = metrics.get("truth_own_removed_hp")
    own_hp_lost = own_hp_lost_obs + (int(truth_own_removed) if truth_own_removed else 0)
    hp_exchange = _ratio(damage_units, own_hp_lost)
    hp_exchange_truth = _ratio(truth_damage, own_hp_lost) if truth_damage else None
    attacked_targets = {a["target"] for a in unique_attacks if a["target"] is not None}
    damaged_targets = {e["addr"] for e in damage_events}
    attacked_with_damage = len(attacked_targets & damaged_targets)
    attacked_zero_damage = len(attacked_targets - damaged_targets)

    # ---- 撤退启发式 ----
    low_hp_units = set()
    low_hp_deaths = 0
    opportunities = 0
    executed = 0
    preserved_ratios = []
    retreat_move_orders = 0
    move_orders = 0
    retreat_opportunity_keys = set()
    for i, frame in enumerate(frames):
        enemies = [e for e in frame["enemies"] if e.get("pos") is not None]
        for unit in frame["own"]:
            hp = unit.get("health")
            cap = hp_max.get(unit.get("name")) or 0
            if hp is None or cap <= 0:
                continue
            ratio = float(hp) / float(cap)
            if ratio > LOW_HP_RATIO:
                continue
            low_hp_units.add(unit["addr"])
            if unit.get("pos") is None or not enemies:
                continue
            nearest = min(enemies, key=lambda e: dist_cells(unit["pos"], e["pos"]))
            start_dist = dist_cells(unit["pos"], nearest["pos"])
            if start_dist is None or start_dist > LOW_HP_ENEMY_RANGE_CELLS:
                continue
            key = (unit["addr"], round(frame["t"], 2))
            if key in retreat_opportunity_keys:
                continue
            retreat_opportunity_keys.add(key)
            opportunities += 1
            # 找 >=10s 后的第一帧
            later = None
            for frame2 in frames[i + 1:]:
                if frame2["t"] - frame["t"] >= RETREAT_CHECK_SECONDS:
                    later = frame2
                    break
            if later is None:
                continue
            later_unit = next((u for u in later["own"] if u["addr"] == unit["addr"]), None)
            if later_unit is None or later_unit.get("pos") is None:
                continue
            preserved_ratios.append(1.0)
            later_enemy = next((e for e in later["enemies"]
                                if e["addr"] == nearest["addr"] and e.get("pos")), None)
            ref_pos = later_enemy["pos"] if later_enemy else nearest["pos"]
            later_dist = dist_cells(later_unit["pos"], ref_pos)
            if later_dist is not None and later_dist - start_dist >= RETREAT_DISTANCE_CELLS:
                executed += 1
    # 低血量单位是否在之后消失（≈被击杀/离场；用"最后出现帧"判定，不声称因果）
    last_seen = {}
    for i, frame in enumerate(frames):
        for unit in frame["own"]:
            last_seen[unit["addr"]] = i
    low_hp_deaths = sum(1 for addr in low_hp_units
                        if last_seen.get(addr, len(frames)) < len(frames) - 2)

    # ---- 移动有效性 / 重复命令 ----
    move_actions = [a for a in actions if a["action"] == "MOVE"]
    move_orders = len(move_actions)
    move_repeat = 0
    move_zero_dest = 0
    move_no_enemy = 0
    move_no_effect = 0
    move_toward_enemy = 0
    move_displacements = []
    prev_dest = None
    for act in move_actions:
        dest = dst_cell(act.get("coordinates"))
        idx = min(int(act.get("obs_index") or 0), max(0, len(frames) - 1))
        frame = frames[idx] if frames else {"own": [], "enemies": []}
        selected = [u for u in frame["own"] if u["addr"] in set(act.get("object_addresses") or [])]
        cen = centroid([u["pos"] for u in selected])
        if dest is not None and prev_dest is not None and dest == prev_dest:
            move_repeat += 1
        prev_dest = dest
        if dest is not None and cen is not None and dist_cells(dest, cen) < MOVE_ZERO_CELLS:
            move_zero_dest += 1
        enemies = [e for e in frame["enemies"] if e.get("pos") is not None]
        if not enemies:
            move_no_enemy += 1
        elif dest is not None and cen is not None:
            nearest_now = min(dist_cells(cen, e["pos"]) for e in enemies)
            nearest_dest = min(dist_cells(dest, e["pos"]) for e in enemies)
            if nearest_dest is not None and nearest_now is not None \
                    and nearest_dest < nearest_now:
                move_toward_enemy += 1
        # 是否有位移：1.5s 后的第一帧看被选单位质心
        if selected and dest is not None:
            later = None
            for frame2 in frames[idx + 1:]:
                if frame2["t"] - frame["t"] >= MOVE_EFFECT_CHECK_SECONDS:
                    later = frame2
                    break
            if later is not None:
                later_cen = centroid([u["pos"] for u in later["own"]
                                      if u["addr"] in set(act.get("object_addresses") or [])])
                if later_cen is not None and cen is not None:
                    shift = dist_cells(later_cen, cen)
                    move_displacements.append(shift)
                    if shift < MOVE_NO_DISPLACEMENT_CELLS:
                        move_no_effect += 1
        # 低血量+接敌时的"后撤型"移动指令
        if enemies and cen is not None and dest is not None:
            low = [u for u in selected
                   if u.get("health") is not None and (hp_max.get(u["name"]) or 0) > 0
                   and u["health"] / float(hp_max[u["name"]]) <= LOW_HP_RATIO]
            if low:
                nearest_now = min(dist_cells(cen, e["pos"]) for e in enemies)
                nearest_dest = min(dist_cells(dest, e["pos"]) for e in enemies)
                if nearest_dest is not None and nearest_now is not None \
                        and nearest_dest > nearest_now + 1.0:
                    retreat_move_orders += 1

    # 每单位累计路径（格）——移动能力本身是否成立（撤退/无效移动指标的 confounder）
    unit_paths = {}
    last_pos = {}
    for frame in frames:
        for unit in frame["own"]:
            if unit.get("pos") is None:
                continue
            addr = unit["addr"]
            if addr in last_pos:
                step = dist_cells(last_pos[addr], unit["pos"])
                if step is not None:
                    unit_paths[addr] = unit_paths.get(addr, 0.0) + step
            last_pos[addr] = unit["pos"]
    path_values = list(unit_paths.values())

    # 连续相同动作（逐字重复：动作+单位集+坐标/目标一致）
    def signature(act):
        if act["action"] == "ATTACK":
            return ("ATTACK", tuple(sorted(act.get("object_addresses") or [])),
                    act.get("target_address"))
        return (act["action"], tuple(sorted(act.get("object_addresses") or [])),
                tuple(act.get("coordinates") or ()))

    identical = 0
    max_run = 0
    run = 0
    prev_sig = None
    for act in actions:
        sig = signature(act)
        if prev_sig is not None and sig == prev_sig:
            identical += 1
            run += 1
            max_run = max(max_run, run + 1)
        else:
            run = 0
        prev_sig = sig

    # 每决策步的攻击条数（刷屏强度）
    per_obs = Counter(int(a.get("obs_index") or 0) for a in unique_attacks)
    max_per_obs = max(per_obs.values()) if per_obs else 0
    obs_with_attacks = len(per_obs)
    # 30s 窗内还有其它多单位攻击的条数（"持续组织"的弱代理）
    bursts = sum(1 for attack in multi
                 if sum(1 for other in multi
                        if abs((other["t"] or 0.0) - (attack["t"] or 0.0)) <= 30.0) >= 2)

    return {
        "episode_id": result.get("episode_id") or jsonl_path.stem,
        "jsonl": str(jsonl_path),
        "kind": result.get("kind"),
        "is_win": bool(result.get("is_win")),
        "elapsed_s": round(elapsed, 3),
        "rows": len(rows),
        "n_observations": len(frames),
        "obs_cadence_median_s": round(cadence, 3) if cadence else None,
        "obs_first_t": round(times[0], 3) if times else None,
        "obs_last_t": round(times[-1], 3) if times else None,
        "model_action_rows": len(actions),
        "non_model_action_rows": len(non_model),
        "action_type_counts": dict(action_counts),
        "model_actions_per_min": (round(len(actions) / (elapsed / 60.0), 3)
                                 if elapsed else None),
        "invalid_orders": int(stats.get("invalid_orders") or 0),
        "orders_ok": int(stats.get("orders_ok") or 0),
        "invalid_order_rate": _ratio(int(stats.get("invalid_orders") or 0),
                                     int(stats.get("orders_ok") or 0)
                                     + int(stats.get("invalid_orders") or 0)),

        # 攻击次数
        "attack_orders_raw": len(raw_attacks),
        "attack_orders_unique": len(unique_attacks),
        "attack_repeat_removed": len(raw_attacks) - len(unique_attacks),
        "attack_repeat_rate": _ratio(len(raw_attacks) - len(unique_attacks),
                                     len(raw_attacks)),
        "attack_orders_per_min": (round(len(unique_attacks) / (elapsed / 60.0), 3)
                                 if elapsed else None),
        "first_attack_command_s": (round(unique_attacks[0]["t"], 3)
                                   if unique_attacks else None),
        "first_effective_attack_s": next(
            (round(a["t"], 3) for a in unique_attacks if a["effective6"]), None),
        "first_damage_dealt_s": (round(min(e["t"] for e in damage_events), 3)
                                 if damage_events else None),
        "max_attacks_per_decision": max_per_obs,
        "decisions_with_attacks": obs_with_attacks,

        # 有效攻击
        "attacks_effective6": effective6,
        "attack_effective6_rate": _ratio(effective6, len(unique_attacks)),
        "attacks_effective3": effective3,
        "attack_effective3_rate": _ratio(effective3, len(unique_attacks)),
        "attacks_effective30": effective30,
        "attack_effective30_rate": _ratio(effective30, len(unique_attacks)),
        "median_attack_to_damage_delay_s": (round(statistics.median(delays), 3)
                                             if delays else None),
        "env_attacks_issued": int(metrics.get("attacks_issued") or 0),
        "env_attacks_effective": int(metrics.get("attacks_effective") or 0),
        "env_attack_effective_rate": _ratio(int(metrics.get("attacks_effective") or 0),
                                           int(metrics.get("attacks_issued") or 0)),
        "env_effective_over_unique": _ratio(int(metrics.get("attacks_effective") or 0),
                                            len(unique_attacks)),

        # 伤害 / 击杀
        "damage_visible_units": damage_units,
        "damage_visible_buildings": damage_buildings,
        "damage_visible_total": damage_units + damage_buildings,
        "truth_damage_dealt": truth_damage,
        "damage_coverage": _ratio(damage_units, truth_damage) if truth_damage else None,
        "own_hp_lost": own_hp_lost,
        "own_hp_lost_observed": own_hp_lost_obs,
        "truth_own_removed_hp": truth_own_removed,
        "truth_killed_hp": metrics.get("truth_killed_hp"),
        "truth_damage_events": metrics.get("truth_damage_events"),
        "truth_kills_infantry": truth_kills_inf,
        "truth_kills_vehicle": truth_kills_veh,
        "truth_kills_units": truth_kills,
        "truth_building_destroyed": truth_buildings,
        "hp_exchange_visible": hp_exchange,
        "hp_exchange_truth": hp_exchange_truth,
        "kills_per_1000_unit_damage": (round(truth_kills / damage_units * 1000, 4)
                                       if damage_units else None),
        "kills_per_1000_truth_damage": (round(truth_kills / truth_damage * 1000, 4)
                                        if truth_damage else None),
        "kills_per_effective_attack6": _ratio(truth_kills, effective6),
        "kills_per_env_effective_attack": _ratio(truth_kills,
                                                 int(metrics.get("attacks_effective") or 0)),
        "attacked_targets": len(attacked_targets),
        "attacked_targets_with_damage": attacked_with_damage,
        "attacked_targets_zero_damage": attacked_zero_damage,
        "attacked_targets_zero_damage_share": _ratio(attacked_zero_damage,
                                                     len(attacked_targets)),
        "target_hp_change_total": sum(a["target_damage6"] for a in unique_attacks),
        "last_kill_contribution": "UNKNOWN",
        "last_kill_contribution_missing_fields": [
            "result.metrics.* 只有聚合计数（truth_kills_* / truth_building_destroyed / "
            "truth_damage_events），没有逐次击杀事件、击杀者地址或伤害归属",
            "episode JSONL 的 type=event 行只有 ppo_control_start，没有 kill/damage 事件",
            "可见 HP 差分只能得到目标掉血，无法区分哪一发/哪一个单位造成最后一击",
        ],

        # 目标选择 / 切换
        "target_type_orders": dict(target_names),
        "target_building_orders": building_orders,
        "target_unit_orders": unit_orders,
        "target_unknown_type_orders": unknown_type_orders,
        "target_building_share": _ratio(building_orders, len(unique_attacks)),
        "distinct_attack_targets": len(target_orders),
        "attacks_per_target": _ratio(len(unique_attacks), len(target_orders)),
        "target_switches": switches,
        "target_switch_rate": _ratio(switches, max(0, len(unique_attacks) - 1)),
        "ineffective_switches": ineffective_switches,
        "ineffective_switch_rate": _ratio(ineffective_switches, switches),
        "switch_prev_target_absent": switch_prev_absent,
        "switch_prev_target_absent_share": _ratio(switch_prev_absent, switches),
        "switch_after_effective_share": _ratio(switch_prev_effective, switches),
        "mean_seconds_between_switch": (round(statistics.mean(switch_times), 3)
                                        if switch_times else None),
        "top1_attack_share": (round(max(target_orders.values()) / len(unique_attacks), 4)
                              if target_orders else None),
        "top1_damage_share": (round(max((sum(e["damage"] for e in unit_damage_events
                                             if e["addr"] == addr)
                                          for addr in damaged_targets), default=0)
                                    / damage_units, 4) if damage_units else None),

        # 集火 / 编组
        "multi_unit_attack_orders": len(multi),
        "multi_unit_attack_share": _ratio(len(multi), len(unique_attacks)),
        "single_unit_attack_orders": len(unique_attacks) - len(multi),
        "all_unit_attack_orders": all_unit_attacks,
        "all_unit_attack_share": _ratio(all_unit_attacks, len(unique_attacks)),
        "subset_multi_unit_attack_orders": subset_multi,
        "subset_multi_unit_attack_share": _ratio(subset_multi, len(unique_attacks)),
        "mean_units_per_attack": (round(statistics.mean([a["n_units"] for a in unique_attacks]), 3)
                                  if unique_attacks else None),
        "group_size_histogram": dict(group_hist),
        "concurrent_same_target_orders": concurrent,
        "concurrent_same_target_share": _ratio(concurrent, len(unique_attacks)),
        "multi_unit_effective_orders": len(multi_effective),
        "multi_unit_effective_share": _ratio(len(multi_effective), len(multi)),
        "multi_unit_effective_share_over_all": _ratio(len(multi_effective),
                                                      len(unique_attacks)),
        "close_multi_unit_effective_orders": close_effective,
        "close_multi_unit_effective_share_cell": _ratio(close_effective,
                                                        len(unique_attacks)),
        "attribution_unconfirmed_share_cell": _ratio(unconfirmed, len(unique_attacks)),
        "sustained_multi_unit_attacks_30s": bursts,

        # 撤退（启发式）
        "low_hp_unit_count": len(low_hp_units),
        "low_hp_unit_deaths": low_hp_deaths,
        "retreat_opportunities": opportunities,
        "retreat_executed": executed,
        "retreat_execution_rate": _ratio(executed, opportunities),
        "preserved_unit_ratio": (round(statistics.mean(preserved_ratios), 4)
                                 if preserved_ratios else None),
        "retreat_move_orders": retreat_move_orders,
        "retreat_move_share": _ratio(retreat_move_orders, move_orders),

        # 移动有效性 / 重复命令
        "move_orders": move_orders,
        "move_repeat_adjacent_orders": move_repeat,
        "move_repeat_adjacent_rate": _ratio(move_repeat, move_orders),
        "move_zero_destination_orders": move_zero_dest,
        "move_zero_destination_share": _ratio(move_zero_dest, move_orders),
        "move_without_visible_enemy_orders": move_no_enemy,
        "move_without_visible_enemy_share": _ratio(move_no_enemy, move_orders),
        "move_no_displacement_orders": move_no_effect,
        "move_no_displacement_share": _ratio(move_no_effect, move_orders),
        "mean_move_displacement_cells": (round(statistics.mean(move_displacements), 4)
                                         if move_displacements else None),
        "move_toward_enemy_orders": move_toward_enemy,
        "move_toward_enemy_share": _ratio(move_toward_enemy, move_orders),
        "mean_unit_path_cells": (round(statistics.mean(path_values), 3)
                                 if path_values else None),
        "median_unit_path_cells": (round(statistics.median(path_values), 3)
                                   if path_values else None),
        "units_with_path": len(path_values),
        "consecutive_identical_action_ratio": _ratio(identical, max(1, len(actions) - 1)),
        "max_identical_run": max_run,
    }


# --------------------------------------------------------------------------- #
# 口径说明（随输出落盘，避免"数字脱离定义"）
# --------------------------------------------------------------------------- #
METRIC_DEFINITIONS = {
    "攻击/有效":
        "attack_orders_raw=模型 decision_source∈{ppo,model,rl,policy} 的 ATTACK 行数；"
        "attack_effective30=同 6s 判定但窗口 30s；median_attack_to_damage_delay_s=攻击到该目标"
        "首次掉血的中位间隔（仅统计 30s 内有掉血的攻击）；"
        "attack_orders_unique=按 trace_parser 规则去重（同组单位+同目标+相邻两条≤1.5s 只留第一条）；"
        "attack_effective6=去重后攻击中，其 target_address 在 [t, t+6.0s] 内出现可见 HP 下降的条数；"
        "attack_effective6_rate=attack_effective6/attack_orders_unique（分母是去重后攻击，不是命中率）；"
        "env_attack_effective_rate=result.metrics.attacks_effective/attacks_issued"
        "（TruthTracker 在环境内按 3s 窗口判定，分母是**未去重**的 env 攻击计数，与 6s 口径不同分母/不同窗口）。",
    "伤害/击杀":
        "damage_visible_units/buildings=可见敌人相邻观测 HP 差之和；truth_damage_dealt=result.metrics.damage_dealt"
        "（TruthTracker 真值，含不可见/建筑）；damage_coverage=可见单位伤害/真值伤害；"
        "truth_kills_*=环境真值计数；hp_exchange_visible=damage_visible_units/own_hp_lost。",
    "目标选择":
        "target_type_orders=攻击行自带的 target_type 计数；target_building_orders/unit_orders 优先用攻击"
        "所属观测里 target_address 的 abstract 判定（建筑=ABSTRACT_TYPE_BUILDING），不可见时用名称词表兜底；"
        "top1_attack_share=打单一目标最多的攻击条数/去重攻击数；top1_damage_share=单目标最大可见单位伤害/单位伤害总和。",
    "切换":
        "target_switches=去重攻击序列中 target_address 变化次数；target_switch_rate=switches/max(1,unique-1)；"
        "ineffective_switches=切换时‘上一条攻击 6s 内未使旧目标掉血’的次数；"
        "switch_prev_target_absent_share=切换时旧目标已不在该帧 visible_enemies 中的比例（近似‘目标消失/被击杀’）；"
        "switch_after_effective_share=切换时上一条攻击有效的比例（≈按战况换目标）。",
    "集火/编组":
        "multi_unit_attack_orders=去重攻击中 object_addresses≥2 的条数；all_unit_attack_orders=选择数等于"
        "该帧己方存活作战单位数的条数；subset_multi_unit_attack_orders=2≤n<存活数的条数；"
        "concurrent_same_target_share=存在另一组不同单位在 ±3s 内打同一目标的攻击占比；"
        "multi_unit_effective_share=多单位攻击中 6s 内目标掉血的比例。",
    "距离口径":
        "所有距离用观测 cell（格）；xyz 仅用于把 MOVE 目标点换算成格（1 格=256）。"
        "close_multi_unit_effective_share_cell=多单位且目标 6s 掉血、且至少一个攻击单位距目标≤8 格的"
        "攻击/去重攻击总数（与 skill_metrics S2 阈值 0.25 同义，但用 cell 修正了 trace_parser 读 x/y 的结构性缺失）。",
    "撤退（启发式）":
        "hp_ratio=health/本局该类型观测到的最大 health（可能低估真实上限）；low_hp=hp_ratio≤0.35；"
        "retreat_opportunities=低血量单位附近 30 格内有可见敌人的（单位,帧）机会数；"
        "retreat_executed=该单位 10s 后仍存活且与最近敌人距离增加≥3 格；retreat_execution_rate=executed/opportunities；"
        "retreat_move_orders=低血量+接敌时下达的、目标点比当前位置离敌人更远(>1 格)的 MOVE 指令数。"
        "这是行为启发式，不等于‘战术撤退’；无路径/命令状态字段，无法证明因果。",
    "移动有效性":
        "move_repeat_adjacent=连续两条 MOVE 目标点完全相同；move_zero_destination=目标点距所选单位质心<1 格；"
        "move_without_visible_enemy=下达 MOVE 时该帧无可见敌人；move_no_displacement=1.5s 后所选单位质心"
        "位移<1 格（可能被阻挡/寻路，属于‘无位移’代理指标，不是无效指令证明）；"
        "move_toward_enemy=目标点比当前质心更接近最近可见敌人；"
        "mean_unit_path_cells=每个己方作战单位整局的累计位移（格）的均值——"
        "用于判断‘不撤退/无位移’是否只是移动能力本身失效的 confounder。",
    "重复命令":
        "consecutive_identical_action_ratio=与上一条动作在(动作类型,单位集合,坐标或目标)上完全相同的比例；"
        "max_identical_run=最长连续相同串长度。",
}


def summarize(values):
    """数值列表 → n/mean/median/sum/min/max（None 过滤）。"""
    vals = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not vals:
        return {"n": 0, "mean": None, "median": None, "sum": None, "min": None, "max": None}
    return {
        "n": len(vals),
        "mean": round(statistics.mean(vals), 4),
        "median": round(statistics.median(vals), 4),
        "sum": round(sum(vals), 4),
        "min": round(min(vals), 4),
        "max": round(max(vals), 4),
    }
