"""轨迹解析（V2.2 口径）：从真实 episode jsonl/result.json 提取可追溯指标。

设计依据 reports/dsh-learning/v2.2/：obs_coverage 槽位采样完整性、时间误差界、
unique_attack_orders 去重、S1 三分离、S2 同分母嵌套、S3 单位/建筑伤害拆分、
S4 机会/执行/保存、S5.1 发现（排除开局可见）/S5.2 探索 PROVISIONAL/S5.3 环境不可计算。
只读解析；不生成任何模拟数据，无法计算的值输出 None 并给 status/na_reason。
"""
import json
import math
import pathlib

SCHEMA_VERSION = 'v2.2-episode-metrics'
DECISION_SECONDS = 0.5
DAMAGE_COVERAGE_MIN = 0.5
OBS_COVERAGE_MIN = 0.80
OBS_COVERAGE_UNUSABLE = 0.50
TIME_ERROR_MAX = 15.0
ATTACK_DEDUP_SECONDS = 1.5
EFFECTIVE_WINDOW = 6.0
CLOSE_CELLS = 8.0
UNCONFIRMED_CELLS = 20.0
DISADVANTAGE_HP_RATIO = 0.8
DISADVANTAGE_MIN_DIST = 30.0
DISADVANTAGE_MIN_DURATION = 3.0
RETREAT_CHECK_SECONDS = 20.0
RETREAT_DISTANCE_CELLS = 8.0
RETREAT_MERGE_SECONDS = 5.0


BUILDING_CODES = (
    'gapowr', 'gadpsa', 'gaweap', 'garadr', 'gahpad', 'gaairc', 'gapile', 'garefn',
    'gayard', 'gacnst', 'gaorep', 'naweps', 'nadr', 'napowr', 'narefn', 'nahand',
    'naweap', 'naradr', 'nairf', 'nahelipad', 'nayard', 'nacnst', 'naorep', 'smcv',
)
NONCOMBAT_CODES = ('harv', 'smcv', 'mcv', 'sminer', 'nminer')
BUILDING_WORDS = ('building', 'house', 'factory', 'refinery', 'power', 'lab', 'radar',
                  'helipad', 'airfield', 'shipyard', 'defense', 'plant', 'yard',
                  'barracks', 'tower')


def _is_building(name):
    lowered = (name or '').lower()
    if lowered in BUILDING_CODES:
        return True
    return any(key in lowered for key in BUILDING_WORDS)


def _is_noncombat(name):
    lowered = (name or '').lower()
    if lowered in NONCOMBAT_CODES:
        return True
    return any(key in lowered for key in ('harvester', 'mcv', 'miner', 'refinery'))


def _is_combat(name):
    return not _is_building(name) and not _is_noncombat(name)


def _pos(obj):
    """Return game-cell coordinates from a raw observation object.

    M0-CLOSE P0-1 fix (2026-11-01): raw episode objects store position as
    ``cell=[x,y]`` (cells) or ``xyz=[x,y]`` (leptons, 1 cell = 256 leptons).
    The old implementation only read legacy ``x``/``y`` keys, so every
    position-dependent metric was silently None. Priority: cell -> xyz/256 ->
    legacy x/y. Never fabricates a position when no field is present.
    """
    cell = obj.get('cell')
    if isinstance(cell, (list, tuple)) and len(cell) >= 2:
        try:
            return (float(cell[0]), float(cell[1]))
        except (TypeError, ValueError):
            pass
    xyz = obj.get('xyz')
    if isinstance(xyz, (list, tuple)) and len(xyz) >= 2:
        try:
            return (float(xyz[0]) / 256.0, float(xyz[1]) / 256.0)
        except (TypeError, ValueError):
            pass
    x, y = obj.get('x'), obj.get('y')
    if x is None or y is None:
        return None
    return (float(x), float(y))


def _dist(a, b):
    if a is None or b is None:
        return None
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _load_records(jsonl_path):
    """读取 episode JSONL，并给每条模型动作附上“所属观测”的时间/序号。

    2026-11-01 修复（M0-A）：旧实现把“第 i 条 ATTACK”映射到“第 i 条观测”，
    但动作条数（含 fixed_economy/rule 动作、被跳过动作）与观测条数不等，
    导致攻击时间错位、6s 有效率等指标失真。正确语义是：一帧内先写观测行，
    同一决策步提交的动作行紧随其后；因此动作的时间应取其前一条观测的 t。
    """
    observations, actions = [], []
    model_sources = {'ppo', 'model', 'rl', 'policy'}
    last_obs = None
    last_obs_index = -1
    with open(jsonl_path, encoding='utf-8', errors='replace') as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            kind = rec.get('type')
            if kind == 'observation':
                observations.append(rec)
                last_obs = rec
                last_obs_index = len(observations) - 1
            elif kind == 'action' and rec.get('decision_source') in model_sources:
                action = dict(rec)
                action['obs_index'] = last_obs_index if last_obs_index >= 0 else 0
                action['obs_t'] = (float(last_obs.get('t') or 0.0)
                                   if last_obs is not None else None)
                action['time_mapping'] = ('preceding_observation'
                                          if last_obs is not None else 'missing_observation')
                actions.append(action)
    return observations, actions


def _obs_quality(observations, decision_seconds=DECISION_SECONDS):
    """槽位匹配的复合采样完整性。

    V2.2 修正：真实 env 的决策节奏可能慢于标称 decision_seconds（实测约 0.8s/game-time），
    用标称值当预期槽位会系统性误报 LOW_COVERAGE。这里用**观测间隔中位数**作为实测节奏
    （数据驱动，不猜测）：expected_slots 由实测节奏导出，检测停帧/重复槽位；
    标称 decision_seconds 仅作参考字段。观测不足（<3）→ UNKNOWN。
    """
    quality = {'decision_seconds_nominal': decision_seconds,
               'cadence_source': None, 'cadence_median_s': None,
               'n_obs': len(observations),
               'expected_slots': None, 'matched_slots': None, 'missing_slots': None,
               'duplicate_slot_count': None, 'obs_sampling_ratio': None,
               'usable_time_ratio': None, 'obs_coverage_composite': None,
               'obs_span_ratio': None, 'gap_p95_s': None, 'gap_max_s': None,
               'gaps_gt5s': None, 'lost_time_s': None, 'lost_time_ratio': None,
               'na_reason': None, 'flags': []}
    if len(observations) < 3:
        quality['na_reason'] = 'insufficient_observations'
        quality['flags'].append('UNUSABLE')
        return quality
    times = [float(o.get('t') or 0.0) for o in observations]
    gaps = [times[i] - times[i - 1] for i in range(1, len(times))]
    positive = [g for g in gaps if g > 0]
    if not positive:
        quality['na_reason'] = 'zero_observation_gaps'
        quality['flags'].append('UNUSABLE')
        return quality
    cadence = sorted(positive)[len(positive) // 2]
    quality['cadence_source'] = 'observed_median_interval'
    quality['cadence_median_s'] = round(cadence, 4)
    quality['gap_max_s'] = round(max(gaps), 2)
    quality['gap_p95_s'] = round(sorted(gaps)[int(0.95 * (len(gaps) - 1))], 2)
    quality['gaps_gt5s'] = sum(1 for g in gaps if g > 5.0)
    span = max(1e-9, times[-1] - times[0])
    slots = [int(round((t - times[0]) / cadence)) for t in times]
    matched = len(set(slots))
    expected = max(1, int(math.floor(span / cadence)) + 1)
    quality.update(expected_slots=expected, matched_slots=matched,
                   missing_slots=max(0, expected - matched),
                   duplicate_slot_count=len(times) - matched)
    quality['obs_sampling_ratio'] = round(matched / expected, 4)
    tolerance = 3.0 * cadence
    lost = sum(max(0.0, g - tolerance) for g in gaps)
    quality['lost_time_s'] = round(lost, 2)
    quality['lost_time_ratio'] = round(lost / span, 4)
    quality['usable_time_ratio'] = round(max(0.0, 1.0 - lost / span), 4)
    quality['obs_coverage_composite'] = round(
        min(quality['obs_sampling_ratio'], quality['usable_time_ratio']), 4)
    return quality


def _apply_coverage_flags(quality, elapsed_s):
    times = None
    flags = quality['flags']
    coverage = quality.get('obs_coverage_composite')
    if elapsed_s and quality.get('n_obs') and quality.get('n_obs') >= 2:
        span = quality.get('gap_max_s')
        del span
        quality['obs_span_ratio'] = None  # 由 parse_episode 补
    if coverage is None:
        flags.append('UNKNOWN_COVERAGE')
        return
    if coverage < OBS_COVERAGE_UNUSABLE:
        flags.append('UNUSABLE')
    elif coverage < OBS_COVERAGE_MIN:
        flags.append('LOW_COVERAGE')
    if quality.get('gap_max_s') is not None and quality['gap_max_s'] > TIME_ERROR_MAX:
        flags.append('DEGRADED_TIME')
    if (quality.get('duplicate_slot_count') or 0) > 0:
        flags.append('DUPLICATE_SLOTS')


def _event_time_error(times, index):
    """事件时间误差上界：事件所在观测与相邻观测的最大间隔。"""
    if not times:
        return None
    index = min(max(index, 0), len(times) - 1)
    before = times[index] - times[index - 1] if index > 0 else 0.0
    after = times[index + 1] - times[index] if index + 1 < len(times) else before
    return round(max(before, after), 3)


def _attack_records(observations, actions):
    """把 ppo/model ATTACK 指令映射到其所属观测时间，并按“同组单位+同目标+≤1.5s”去重。

    M0-A 修复：优先使用动作记录携带的 ``obs_t``（由 ``_load_records`` 按 JSONL
    顺序附加的“前一条观测时间”）；仅在旧数据缺失时退回按序号近似，并记录
    ``mapping`` 计数，保证指标可追溯到映射方式。
    """
    obs_times = [float(o.get('t') or 0.0) for o in observations]
    raw = []
    mapping = {'preceding_observation': 0, 'order_fallback': 0, 'no_time': 0}
    for action in actions:
        if (action.get('action') or '').upper() != 'ATTACK':
            continue
        units = tuple(sorted(action.get('object_addresses') or []))
        target = action.get('target_address')
        raw.append({'raw_index': len(raw), 'units': units, 'target': target,
                    'n_units': len(units),
                    'obs_index': action.get('obs_index'),
                    't': action.get('obs_t'),
                    'mapping': action.get('time_mapping')})
    # 旧数据兼容：没有 obs_t 时退回“第 i 条攻击 ≈ 第 i 条观测”（明确标记 lower bound）。
    for i, attack in enumerate(raw):
        if attack.get('t') is not None:
            mapping['preceding_observation'] += 1
            continue
        if obs_times:
            attack['obs_index'] = min(i, max(0, len(obs_times) - 1))
            attack['t'] = obs_times[attack['obs_index']]
            attack['mapping'] = 'order_fallback'
            mapping['order_fallback'] += 1
        else:
            attack['obs_index'] = 0
            attack['t'] = 0.0
            attack['mapping'] = 'no_time'
            mapping['no_time'] += 1
    unique = []
    for attack in raw:
        if unique and unique[-1]['units'] == attack['units'] \
                and unique[-1]['target'] == attack['target'] \
                and attack['t'] - unique[-1]['t'] <= ATTACK_DEDUP_SECONDS:
            continue
        unique.append(dict(attack))
    for attack in unique:
        attack['time_error_bound_s'] = _event_time_error(obs_times, attack['obs_index'] or 0)
        attack['time_mapping'] = attack.get('mapping')
    return raw, unique, mapping


def _damage_series(observations):
    """相邻观测可见敌人血量差 → 事件列表（地址、时间、伤害、是否建筑）。"""
    events = []
    hp_history = {}  # addr -> (t, health, is_building)
    for i in range(1, len(observations)):
        prev = {e.get('addr'): e for e in (observations[i - 1].get('visible_enemies') or [])}
        t = float(observations[i].get('t') or 0.0)
        for enemy in (observations[i].get('visible_enemies') or []):
            addr = enemy.get('addr')
            old = prev.get(addr)
            if old is None or old.get('type_name') != enemy.get('type_name'):
                continue
            delta = int(old.get('health') or 0) - int(enemy.get('health') or 0)
            if delta > 0:
                events.append({'t': t, 'addr': addr, 'damage': delta,
                               'is_building': _is_building(enemy.get('type_name')),
                               'target_name': enemy.get('type_name')})
            hp_history[addr] = (t, enemy.get('health'), enemy.get('type_name'))
    return events


def _effective(attack, damage_events, window=EFFECTIVE_WINDOW):
    target = attack.get('target')
    t = attack.get('t', 0.0)
    return any(e['addr'] == target and t - 1e-9 <= e['t'] <= t + window + 1e-9
               for e in damage_events)


def _switches(unique_attacks):
    switches = ineffective = 0
    prev = None
    for attack in unique_attacks:
        if prev is not None and attack.get('target') != prev.get('target'):
            switches += 1
            if not prev.get('effective6'):
                ineffective += 1
        prev = attack
    return switches, ineffective


def _enemy_positions(observation):
    positions = {}
    for enemy in (observation.get('visible_enemies') or []):
        pos = _pos(enemy)
        if pos is not None:
            positions[enemy.get('addr')] = pos
    return positions


def _own_combat_units(observation):
    units = []
    for own in (observation.get('own_objects') or []):
        name = own.get('type_name')
        if _is_combat(name):
            pos = _pos(own)
            units.append({'addr': own.get('addr'), 'pos': pos,
                          'health': int(own.get('health') or 0), 'name': name})
    return units


def _opportunities(observations):
    """S4：劣势机会识别、执行与保存（不要求先出现损失）。"""
    windows = []
    current = None
    for i, observation in enumerate(observations):
        t = float(observation.get('t') or 0.0)
        own_units = _own_combat_units(observation)
        enemies = _enemy_positions(observation)
        own_hp = sum(u['health'] for u in own_units)
        enemy_hp = sum(int(e.get('health') or 0) for e in
                       (observation.get('visible_enemies') or []) if _is_combat(e.get('type_name')))
        distance = None
        for unit in own_units:
            for pos in enemies.values():
                d = _dist(unit['pos'], pos)
                if d is not None and (distance is None or d < distance):
                    distance = d
        disadvantage = (enemy_hp > 0 and own_hp < enemy_hp * DISADVANTAGE_HP_RATIO
                        and distance is not None and distance <= DISADVANTAGE_MIN_DIST)
        if disadvantage:
            if current is None:
                current = {'start_t': t, 'start_index': i, 'own_hp': own_hp,
                           'enemy_hp': enemy_hp, 'own_units': own_units,
                           'enemy_positions': dict(enemies), 'last_t': t}
            else:
                current['last_t'] = t
                current['own_units'] = own_units
                current['own_hp'] = own_hp
                current['enemy_hp'] = enemy_hp
                current['enemy_positions'] = dict(enemies)
        elif current is not None:
            if t - current['last_t'] <= RETREAT_MERGE_SECONDS:
                current['last_t'] = t
            else:
                windows.append(current)
                current = None
    if current is not None:
        windows.append(current)
    eligible = []
    for window in windows:
        duration = window['last_t'] - window['start_t']
        if duration < DISADVANTAGE_MIN_DURATION and len(windows) > 1:
            continue
        eligible.append(window)
    executed = []
    for window in eligible:
        start_t = window['start_t']
        start_own = {u['addr']: u for u in window['own_units'] if u['pos']}
        start_enemy = window['enemy_positions']
        own_c = _centroid([u['pos'] for u in start_own.values()])
        enemy_c = _centroid(list(start_enemy.values()))
        start_dist = _dist(own_c, enemy_c)
        target_index = None
        for j in range(window['start_index'], len(observations)):
            if float(observations[j].get('t') or 0.0) - start_t >= RETREAT_CHECK_SECONDS:
                target_index = j
                break
        if start_dist is None or target_index is None:
            window.update(executed=False, preserved_unit_ratio=None,
                          loss_in_window=None)
            continue
        later_obs = observations[target_index]
        own_c2 = _centroid([u['pos'] for u in _own_combat_units(later_obs) if u['pos']])
        enemy_c2 = _centroid(list(_enemy_positions(later_obs).values()))
        later_dist = _dist(own_c2, enemy_c2)
        executed_ok = (later_dist is not None and start_dist is not None
                       and later_dist - start_dist >= RETREAT_DISTANCE_CELLS)
        start_units = {u['addr']: u for u in window['own_units']}
        later_units = {u['addr']: u for u in _own_combat_units(later_obs)}
        preserved = (sum(1 for addr in start_units if addr in later_units)
                     / max(1, len(start_units))) if start_units else None
        window.update(executed=bool(executed_ok), preserved_unit_ratio=preserved,
                      loss_in_window=sum(1 for addr in start_units if addr not in later_units))
        executed.append(window)
    return {'opportunities': len(eligible), 'executed': sum(1 for w in eligible if w.get('executed')),
            'not_applicable': sum(1 for w in eligible if not w.get('executed')),
            'details': eligible}


def _centroid(points):
    points = [p for p in points if p is not None]
    if not points:
        return None
    return (sum(p[0] for p in points) / len(points),
            sum(p[1] for p in points) / len(points))


def _first_new_enemy_building(observations):
    """S5.1：排除开局已可见建筑；开局已有建筑 → reveal 模式，指标无意义。"""
    baseline = set()
    first_building_t = None
    first = observations[0] if observations else {}
    for enemy in (first.get('visible_enemies') or []):
        if _is_building(enemy.get('type_name')):
            baseline.add(enemy.get('addr'))
    reveal_mode = bool(baseline)
    seen = set(baseline)
    if not reveal_mode:
        for observation in observations:
            for enemy in (observation.get('visible_enemies') or []):
                if _is_building(enemy.get('type_name')) and enemy.get('addr') not in seen:
                    seen.add(enemy.get('addr'))
                    if first_building_t is None:
                        first_building_t = float(observation.get('t') or 0.0)
    return {'reveal_mode': reveal_mode, 'baseline_buildings': len(baseline),
            'first_new_enemy_building_s': first_building_t,
            'status': 'INCOMPUTABLE_REVEAL_MODE' if reveal_mode else 'OK'}


def parse_episode(jsonl_path, result=None, decision_seconds=DECISION_SECONDS):
    jsonl_path = pathlib.Path(jsonl_path)
    observations, actions = _load_records(jsonl_path)
    result = result or {}
    metrics = result.get('metrics') or {}
    quality = _obs_quality(observations, decision_seconds)
    elapsed = float(result.get('elapsed_s') or 0.0)
    if not elapsed and observations:
        elapsed = float(observations[-1].get('t') or 0.0) - float(observations[0].get('t') or 0.0)
    if elapsed and quality.get('n_obs', 0) >= 2:
        times = [float(o.get('t') or 0.0) for o in observations]
        quality['obs_span_ratio'] = round((times[-1] - times[0]) / max(1e-9, elapsed), 4)
    _apply_coverage_flags(quality, elapsed)
    quality_flags = list(dict.fromkeys(quality['flags']))

    raw_attacks, unique_attacks, attack_time_mapping = _attack_records(observations, actions)
    damage_events = _damage_series(observations)
    unit_damage_events = [e for e in damage_events if not e['is_building']]
    building_damage_events = [e for e in damage_events if e['is_building']]
    for attack in unique_attacks:
        attack['effective6'] = _effective(attack, damage_events)
        attack['effective3'] = _effective(attack, damage_events, 3.0)
        target_damage = sum(e['damage'] for e in damage_events
                            if e['addr'] == attack.get('target')
                            and attack['t'] - 1e-9 <= e['t'] <= attack['t'] + EFFECTIVE_WINDOW)
        attack['target_damage'] = target_damage
    switches, ineffective = _switches(unique_attacks)
    raw_attack_count = len(raw_attacks)
    unique_count = len(unique_attacks)
    order_repeat_rate = round(1.0 - unique_count / raw_attack_count, 4) if raw_attack_count else None
    first_attack = unique_attacks[0]['t'] if unique_attacks else None
    first_effective = next((a['t'] for a in unique_attacks if a['effective6']), None)
    first_damage = min((e['t'] for e in damage_events), default=None)
    pre_contact_invalid = 0
    for attack in unique_attacks:
        if attack['t'] == first_effective:
            break
        if not attack['effective6']:
            pre_contact_invalid += 1
    damage_to_units_visible = sum(e['damage'] for e in unit_damage_events)
    damage_to_buildings_visible = sum(e['damage'] for e in building_damage_events)
    truth_total_damage = metrics.get('damage_dealt')
    damage_coverage = None
    damage_coverage_na = None
    if truth_total_damage:
        damage_coverage = round(damage_to_units_visible / truth_total_damage, 4)
    else:
        damage_coverage_na = 'truth_total_damage_zero_or_missing'
    attacked_targets = {}
    for attack in unique_attacks:
        key = attack.get('target')
        attacked_targets[key] = attacked_targets.get(key, 0) + 1
    top1_attack_share = (round(max(attacked_targets.values()) / unique_count, 4)
                         if unique_count and attacked_targets else None)
    damage_by_target = {}
    for event in unit_damage_events:
        damage_by_target[event['addr']] = damage_by_target.get(event['addr'], 0) + event['damage']
    damage_concentration = (round(max(damage_by_target.values()) / damage_to_units_visible, 4)
                            if damage_to_units_visible else None)
    truth_kills_units = (int(metrics.get('truth_kills_infantry') or 0)
                         + int(metrics.get('truth_kills_vehicle') or 0))
    truth_building_destroyed = int(metrics.get('truth_building_destroyed') or 0)
    effective_count = sum(1 for a in unique_attacks if a['effective6'])
    effective_rate = round(effective_count / unique_count, 4) if unique_count else None
    inefficient_rate = round(ineffective / switches, 4) if switches else 0.0
    attacks_per_min = round(unique_count / (elapsed / 60.0), 3) if elapsed else None
    time_errors = [a['time_error_bound_s'] for a in unique_attacks
                   if a.get('time_error_bound_s') is not None]
    first_attack_error = (max(time_errors) if time_errors else None)
    # S2：同分母 |A| 的严格子集
    multi = [a for a in unique_attacks if a['n_units'] >= 2]
    multi_eff = [a for a in multi if a['effective6']]
    close_eff, unconfirmed = [], []
    for attack in multi_eff:
        target_pos = None
        obs_index = min(attack['obs_index'], max(0, len(observations) - 1))
        observation = observations[obs_index] if observations else {}
        for enemy in (observation.get('visible_enemies') or []):
            if enemy.get('addr') == attack.get('target'):
                target_pos = _pos(enemy)
                break
        positions = {}
        for own in (observation.get('own_objects') or []):
            positions[own.get('addr')] = _pos(own)
        dists = [d for d in (_dist(positions.get(addr), target_pos)
                             for addr in attack['units']) if d is not None]
        mind = min(dists) if dists else None
        if mind is not None and mind <= CLOSE_CELLS:
            close_eff.append(attack)
        if mind is None or mind > UNCONFIRMED_CELLS:
            unconfirmed.append(attack)
    # S4
    opportunities = _opportunities(observations)
    executed = sum(1 for w in opportunities['details'] if w.get('executed'))
    preserved = [w['preserved_unit_ratio'] for w in opportunities['details']
                 if w.get('executed') and w.get('preserved_unit_ratio') is not None]
    own_losses = int(metrics.get('own_unit_losses') or 0)
    # own_losses：若 result 没有，用可见观测中己方作战单位地址消失估算
    if not own_losses:
        loss_events = 0
        for i in range(1, len(observations)):
            prev = {u.get('addr') for u in _own_combat_units(observations[i - 1])}
            cur = {u.get('addr') for u in _own_combat_units(observations[i])}
            loss_events += len(prev - cur)
        own_losses = loss_events
    truth_removed_hp = metrics.get('truth_own_removed_hp')
    own_hp_lost_obs = int(metrics.get('own_hp_lost') or 0)
    if truth_removed_hp is not None:
        own_hp_lost = own_hp_lost_obs + int(truth_removed_hp)
    else:
        own_hp_lost = own_hp_lost_obs
    hp_exchange = (round(damage_to_units_visible / own_hp_lost, 4)
                   if own_hp_lost > 0 else None)
    s5 = _first_new_enemy_building(observations)
    mapped_final = None
    for observation in observations:
        map_info = observation.get('map') or {}
        if map_info.get('cells'):
            mapped_final = round((map_info.get('mapped') or 0) / map_info['cells'], 5)
    gaps = [float(observations[i].get('t') or 0) - float(observations[i - 1].get('t') or 0)
            for i in range(1, len(observations))]
    out = {
        'schema_version': SCHEMA_VERSION,
        'episode_id': result.get('episode_id') or jsonl_path.stem,
        'elapsed_s': round(elapsed, 2),
        'victory': result.get('victory'),
        'outcome_confirmed': result.get('outcome_confirmed'),
        'data_quality': {
            **{k: v for k, v in quality.items() if k != 'flags'},
            'quality_flags': quality_flags,
            'damage_to_units_visible': damage_to_units_visible,
            'damage_to_buildings_visible': damage_to_buildings_visible,
            'truth_total_damage': truth_total_damage,
            'damage_coverage': damage_coverage,
            'damage_coverage_na_reason': damage_coverage_na,
            'damage_coverage_scope': 'visible_units_over_total_truth',
            'time_error_bound_s': first_attack_error,
        },
        'attack_time_mapping': attack_time_mapping,
        'attack_time_mapping_version': 'm0a-preceding-observation-v1',
        'first_attack_command_s': first_attack,
        'first_effective_attack_s': first_effective,
        'first_damage_dealt_s': first_damage,
        'pre_contact_invalid_orders': pre_contact_invalid,
        'raw_attack_orders': raw_attack_count,
        'unique_attack_orders': unique_count,
        'order_repeat_rate': order_repeat_rate,
        'attacks_issued': unique_count,
        'attacks_effective6': effective_count,
        'attack_effective6_rate': effective_rate,
        'attacks_per_min': attacks_per_min,
        'attack_concentration': top1_attack_share,
        'top1_attack_share': top1_attack_share,
        'damage_concentration': damage_concentration,
        'top1_damage_share': damage_concentration,
        'truth_kills_units': truth_kills_units,
        'truth_kills_infantry': int(metrics.get('truth_kills_infantry') or 0),
        'truth_kills_vehicle': int(metrics.get('truth_kills_vehicle') or 0),
        'truth_building_destroyed': truth_building_destroyed,
        'kills_per_1000_unit_damage': (round(truth_kills_units / damage_to_units_visible * 1000, 4)
                                       if damage_to_units_visible else None),
        'kills_per_1000_damage': (round(truth_kills_units / damage_to_units_visible * 1000, 4)
                                  if damage_to_units_visible else None),
        'kills_per_effective_attack': (round(truth_kills_units / effective_count, 4)
                                       if effective_count else None),
        'multi_unit_order_share': (round(len(multi) / unique_count, 4) if unique_count else None),
        'multi_unit_effective_share': (round(len(multi_eff) / unique_count, 4)
                                       if unique_count else None),
        'close_multi_unit_effective_share': (round(len(close_eff) / unique_count, 4)
                                             if unique_count else None),
        'attribution_unconfirmed_share': (round(len(unconfirmed) / unique_count, 4)
                                          if unique_count else None),
        'multi_unit_effectiveness': (round(len(multi_eff) / len(multi), 4) if multi else None),
        'close_effectiveness': (round(len(close_eff) / len(multi), 4) if multi else None),
        'group_attack_share': (round(len(multi) / unique_count, 4) if unique_count else None),
        'mean_selection_width': (round(sum(a['n_units'] for a in unique_attacks) / unique_count, 4)
                                 if unique_count else None),
        'mean_units_per_attack': (round(sum(a['n_units'] for a in unique_attacks) / unique_count, 4)
                                  if unique_count else None),
        'target_switches': switches,
        'ineffective_switches': ineffective,
        'ineffective_switch_rate': inefficient_rate,
        'retreat_opportunities': opportunities['opportunities'],
        'retreat_executed': opportunities['executed'],
        'n_retreat_not_applicable': opportunities['not_applicable'],
        'retreat_execution_rate': (round(executed / opportunities['opportunities'], 4)
                                   if opportunities['opportunities'] else None),
        'preserved_unit_ratio': (round(sum(preserved) / len(preserved), 4)
                                 if preserved else None),
        'own_unit_losses': own_losses,
        'own_losses_per_min': (round(own_losses / (elapsed / 60.0), 2) if elapsed else None),
        'hp_lost_visible': own_hp_lost_obs,
        'truth_own_removed_hp': truth_removed_hp,
        'damage_to_units_visible': damage_to_units_visible,
        'damage_to_buildings_visible': damage_to_buildings_visible,
        'hp_exchange': hp_exchange,
        'reveal_mode': s5['reveal_mode'],
        'baseline_enemy_buildings': s5['baseline_buildings'],
        'first_enemy_building_s': s5['first_new_enemy_building_s'],
        'first_new_enemy_building_s': s5['first_new_enemy_building_s'],
        's5_discovery_status': s5['status'],
        'map_mapped_final': mapped_final,
        'map_coverage_final': mapped_final,
        'map_coverage_status': 'PROVISIONAL',
        's5_counter_status': 'INCOMPUTABLE_ENV_CONSTRAINT',
        'gaps_gt5s': quality['gaps_gt5s'],
        'gap_max_s': quality['gap_max_s'],
        'elapsed_from_obs_s': round((obs_time_span(observations)), 2) if observations else None,
    }
    return out


def obs_time_span(observations):
    if len(observations) < 2:
        return 0.0
    return float(observations[-1].get('t') or 0.0) - float(observations[0].get('t') or 0.0)


def load_run_episodes(run_dir, max_episodes=0, completed_only=True,
                      include_timeouts=False):
    """返回 [(jsonl_path, result_dict)]，按 episode id 排序。

    completed_only: 只取 outcome_confirmed 的局；include_timeouts=True 时额外包含
    kind=timeout（固定评测的 300s 时限结局是有效评测结果，不能当缺失剔除）。
    interrupted/crash 等未确认局始终排除（由调用方记录原因）。
    """
    run_dir = pathlib.Path(run_dir)
    episodes = []
    for result_path in sorted(run_dir.glob('workers/*/episodes/*.result.json')):
        jsonl_path = result_path.with_name(result_path.name.replace('.result.json', '.jsonl'))
        if not jsonl_path.exists():
            continue
        try:
            result = json.loads(result_path.read_text(encoding='utf-8-sig'))
        except Exception:  # noqa: BLE001
            continue
        if completed_only:
            confirmed = bool(result.get('outcome_confirmed'))
            timeout_ok = include_timeouts and result.get('kind') == 'timeout'
            if not (confirmed or timeout_ok):
                continue
        episodes.append((jsonl_path, result))
        if max_episodes and len(episodes) >= max_episodes:
            break
    return episodes
