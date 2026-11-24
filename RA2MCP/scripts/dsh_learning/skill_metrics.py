"""技能指标与掌握判定（V2.2 口径）。

- 指标来源：trace_parser 从真实 episode 轨迹/result 提取的字段；
- 掌握规则：三态（MASTERED/NOT_MASTERED/UNDETERMINED）+ Wilson 95% 下界≥0.60 + n≥20；
- S4 额外样本门槛：n_total_eval≥20、n_opp_episodes≥10、n_opportunity≥20、n_retreat_executed≥5；
- S5 拆分 S5.1/S5.2/S5.3，总体聚合需 S5.1 与 S5.3 均 MASTERED；
- 阈值版本 v2.2-draft（草案，未正式校准），哈希绑定规范化配置。
"""
import hashlib
import json
import math

MASTERY_RULE_ID = 'v2.2-wilson-n20-lower0.60'
S4_SAMPLE_RULE_ID = 'v2.2-s4-op20-ep10-exec5-episode-level-wilson'
S5_AGG_ID = 'v2.2-s5-all-subskills-required'
THRESHOLDS_VERSION = 'v2.2-draft'

MASTERY = {'id': MASTERY_RULE_ID, 'min_episodes': 20, 'wilson_low': 0.60}
S4_SAMPLE_RULE = {
    'id': S4_SAMPLE_RULE_ID,
    'min_total_episodes': 20,
    'min_opportunity_episodes': 10,
    'min_opportunities': 20,
    'min_executed': 5,
    'per_episode_min_opportunities': 2,
}

SKILLS = {
    'S1': {
        'name': '主动进攻',
        'indicators': {
            'first_effective_attack_s': {'threshold': 90.0, 'lower_better': True,
                                         'unit': 'seconds', 'time_based': True},
            'attacks_per_min': {'threshold': 10.0, 'lower_better': False, 'unit': 'per_min'},
            'attack_effective6_rate': {'threshold': 0.30, 'lower_better': False, 'unit': 'ratio'},
        },
        'gates': {'pre_contact_invalid_orders_max': 5, 'order_repeat_rate_max': 0.5},
        'mastery': MASTERY,
    },
    'S2': {
        'name': '有组织进攻',
        'indicators': {
            'close_multi_unit_effective_share': {'threshold': 0.25, 'lower_better': False,
                                                 'unit': 'ratio'},
            'ineffective_switch_rate': {'threshold': 0.50, 'lower_better': True, 'unit': 'ratio'},
        },
        'mastery': MASTERY,
    },
    'S3': {
        'name': '集中火力',
        'indicators': {
            'attack_concentration': {'threshold': 0.40, 'lower_better': False, 'unit': 'ratio'},
            'kills_per_1000_unit_damage': {'threshold': 3.0, 'lower_better': False,
                                           'unit': 'kills/1000hp'},
            'kills_per_effective_attack': {'threshold': 0.15, 'lower_better': False,
                                           'unit': 'kills/attack'},
        },
        'mastery': MASTERY,
    },
    'S4': {
        'name': '生存与撤退',
        'indicators': {
            'hp_exchange': {'threshold': 0.30, 'lower_better': False, 'unit': 'ratio'},
            'own_losses_per_min': {'threshold': 4.0, 'lower_better': True, 'unit': 'per_min'},
            'retreat_execution_rate': {'threshold': 0.30, 'lower_better': False,
                                       'unit': 'ratio'},
            'preserved_unit_ratio': {'threshold': 0.60, 'lower_better': False,
                                     'unit': 'ratio'},
        },
        'mastery': MASTERY,
        'sample_rule': S4_SAMPLE_RULE,
    },
    'S5': {
        'name': '侦察与反制（含 S5.1 发现 / S5.2 探索 / S5.3 反制）',
        'subskills': {
            'S5.1': {'name': '发现敌方建筑/目标',
                      'indicators': {'first_new_enemy_building_s': {
                          'threshold': 120.0, 'lower_better': True, 'unit': 'seconds'}}},
            'S5.2': {'name': '地图探索与覆盖', 'provisional': True,
                      'indicators': {'map_coverage_final': {'threshold': None,
                                                            'lower_better': False,
                                                            'unit': 'ratio',
                                                            'provisional': True}}},
            'S5.3': {'name': '战术反制与适应',
                      'computability': 'INCOMPUTABLE_ENV_CONSTRAINT',
                      'indicators': {}},
        },
        'aggregation': S5_AGG_ID,
        'mastery': MASTERY,
    },
}

SKILL_ORDER = ('S1', 'S2', 'S3', 'S4', 'S5')
DEPENDENCIES = {'S1': [], 'S2': ['S1'], 'S3': ['S2'], 'S4': ['S1'], 'S5': ['S1']}

THRESHOLDS = {
    'version': THRESHOLDS_VERSION,
    'mastery_rule': MASTERY,
    's4_sample_rule': S4_SAMPLE_RULE,
    's5_aggregation': S5_AGG_ID,
    'skills': {sid: {
        'indicators': {name: {k: v for k, v in spec.items() if k in
                              ('threshold', 'lower_better', 'unit')}
                       for name, spec in skill.get('indicators', {}).items()},
        'gates': skill.get('gates', {}),
        'subskills': {sub: {'indicators': {name: {k: v for k, v in spec.items()
                                                 if k in ('threshold', 'lower_better', 'unit')}
                                          for name, spec in sub_spec.get('indicators', {}).items()},
                            'computability': sub_spec.get('computability'),
                            'provisional': sub_spec.get('provisional', False)}
                    for sub, sub_spec in skill.get('subskills', {}).items()},
    } for sid, skill in SKILLS.items()},
}
THRESHOLDS_HASH = hashlib.sha256(
    json.dumps(THRESHOLDS, sort_keys=True, separators=(',', ':'),
               ensure_ascii=False).encode('utf-8')).hexdigest()


def wilson_interval(k, n, z=1.96):
    """Wilson 95% 置信区间（episode 级 pass 计数）。"""
    if n <= 0:
        return (None, None)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (round(max(0.0, center - half), 4), round(min(1.0, center + half), 4))


def _coverage_ok(episode):
    quality = episode.get('data_quality') or {}
    flags = set(quality.get('quality_flags') or [])
    if 'UNUSABLE' in flags:
        return False, 'UNUSABLE'
    coverage = quality.get('damage_coverage')
    if coverage is not None and coverage < 0.5:
        return False, 'LOW_DAMAGE_COVERAGE'
    return True, None


def _indicator_status(value, spec, quality_flags):
    if value is None:
        return 'NOT_MEASURED'
    if spec.get('time_based') and ('DEGRADED_TIME' in quality_flags):
        return 'DEGRADED_TIME'
    if spec.get('provisional'):
        return 'PROVISIONAL'
    return 'OK'


def _indicator_pass(value, spec):
    threshold = spec.get('threshold')
    if value is None or threshold is None:
        return None
    if spec.get('lower_better'):
        return value <= threshold
    return value >= threshold


def _evaluate_indicators(episode, indicators):
    quality_flags = set((episode.get('data_quality') or {}).get('quality_flags') or [])
    results, passes = {}, []
    for name, spec in indicators.items():
        value = episode.get(name)
        status = _indicator_status(value, spec, quality_flags)
        passed = _indicator_pass(value, spec) if status == 'OK' else None
        results[name] = {'value': value, 'unit': spec.get('unit'),
                         'threshold': spec.get('threshold'),
                         'lower_better': spec.get('lower_better'),
                         'status': status, 'pass': passed,
                         'na_reason': (None if status == 'OK'
                                       else 'missing_value_or_degraded_data')}
        passes.append(passed)
    return results, passes


def episode_skill(episode, skill_id):
    """单局技能判定：pass True/False 或 status NOT_MEASURED/INCOMPUTABLE/UNDETERMINED。"""
    quality_flags = set((episode.get('data_quality') or {}).get('quality_flags') or [])
    coverage_ok, coverage_reason = _coverage_ok(episode)
    result = {'skill': skill_id, 'pass': None, 'status': 'OK', 'indicators': {},
              'reasons': []}
    if skill_id == 'S1':
        indicators, passes = _evaluate_indicators(episode, SKILLS['S1']['indicators'])
        result['indicators'] = indicators
        gates = SKILLS['S1']['gates']
        spam_risk = (episode.get('pre_contact_invalid_orders') or 0) > gates[
            'pre_contact_invalid_orders_max']
        degraded_spam = (episode.get('order_repeat_rate') or 0) > gates[
            'order_repeat_rate_max']
        if degraded_spam:
            result.update(status='NOT_MEASURED', reasons=['DEGRADED_SPAM'])
            return result
        if spam_risk:
            result['reasons'].append('SPAM_RISK')
        result['pass'] = all(p is True for p in passes) and not spam_risk
        if not coverage_ok and 'LOW_DAMAGE_COVERAGE' in str(coverage_reason):
            return result  # S1 不依赖伤害覆盖率
    elif skill_id == 'S2':
        if not coverage_ok:
            result.update(status='UNDETERMINED', reasons=[coverage_reason])
            return result
        indicators, passes = _evaluate_indicators(episode, SKILLS['S2']['indicators'])
        result['indicators'] = indicators
        result['pass'] = all(p is True for p in passes)
    elif skill_id == 'S3':
        if not coverage_ok:
            result.update(status='UNDETERMINED', reasons=[coverage_reason])
            return result
        indicators, passes = _evaluate_indicators(episode, SKILLS['S3']['indicators'])
        result['indicators'] = indicators
        if episode.get('kills_per_1000_unit_damage') is None:
            result.update(status='UNDETERMINED',
                          reasons=['damage_scope_not_computable'])
            return result
        result['pass'] = all(p is True for p in passes)
    elif skill_id == 'S4':
        rule = S4_SAMPLE_RULE
        opportunities = episode.get('retreat_opportunities')
        if opportunities is None or opportunities < rule['per_episode_min_opportunities']:
            result.update(status='NOT_MEASURED',
                          reasons=['insufficient_opportunities_in_episode'])
            return result
        if not coverage_ok:
            result.update(status='UNDETERMINED', reasons=[coverage_reason])
            return result
        indicators, passes = _evaluate_indicators(episode, SKILLS['S4']['indicators'])
        result['indicators'] = indicators
        result['pass'] = all(p is True for p in passes)
    elif skill_id in ('S5.1', 'S5.2', 'S5.3'):
        sub = SKILLS['S5']['subskills'][skill_id]
        if skill_id == 'S5.3':
            result.update(status='INCOMPUTABLE',
                          reasons=['INCOMPUTABLE_ENV_CONSTRAINT'])
            return result
        if skill_id == 'S5.2':
            result.update(status='PROVISIONAL',
                          reasons=['map_coverage_semantics_unverified'],
                          indicators={'map_coverage_final': {
                              'value': episode.get('map_coverage_final'),
                              'status': 'PROVISIONAL'}})
            return result
        discovery_status = episode.get('s5_discovery_status')
        if discovery_status == 'INCOMPUTABLE_REVEAL_MODE':
            result.update(status='INCOMPUTABLE',
                          reasons=['INCOMPUTABLE_REVEAL_MODE'])
            return result
        indicators, passes = _evaluate_indicators(episode, sub['indicators'])
        result['indicators'] = indicators
        result['pass'] = all(p is True for p in passes)
    else:
        raise ValueError('unknown skill ' + skill_id)
    if result['pass'] is None:
        result['status'] = 'UNDETERMINED'
    return result


def evaluate_episode(episode):
    results = {sid: episode_skill(episode, sid) for sid in ('S1', 'S2', 'S3', 'S4')}
    for sub in ('S5.1', 'S5.2', 'S5.3'):
        results[sub] = episode_skill(episode, sub)
    results['S5'] = {'skill': 'S5', 'aggregation': S5_AGG_ID,
                     'status': 'PENDING_AGGREGATION', 'pass': None}
    return results


def _aggregate_pass_rate(skill_id, per_episode, sample_rule=None):
    evaluated = []
    statuses = []
    for entry in per_episode:
        item = (entry.get('skills') or {}).get(skill_id) or entry.get(skill_id) or {}
        if item.get('pass') in (True, False):
            evaluated.append(bool(item['pass']))
        statuses.append(item.get('status'))
    n = len(evaluated)
    passes = sum(1 for value in evaluated if value)
    if n == 0:
        known = {status for status in statuses if status}
        propagated = 'UNDETERMINED'
        for candidate in ('INCOMPUTABLE', 'UNKNOWN', 'NOT_MEASURED', 'PROVISIONAL'):
            if known and known == {candidate}:
                propagated = candidate
                break
        return {'status': propagated, 'pass_rate': None, 'wilson_low': None,
                'wilson_high': None, 'n_episodes': 0, 'passes': 0,
                'reasons': ['no_episode_level_pass_evaluations'],
                'mastery_rule_id': MASTERY_RULE_ID,
                'thresholds_version': THRESHOLDS_VERSION,
                'thresholds_hash': THRESHOLDS_HASH}
    pass_rate = round(passes / n, 4) if n else None
    low, high = wilson_interval(passes, n) if n else (None, None)
    status = 'UNDETERMINED'
    reasons = []
    if n < MASTERY['min_episodes']:
        reasons.append('insufficient_episodes')
    elif low is not None and low >= MASTERY['wilson_low']:
        status = 'MASTERED'
    elif high is not None and high < MASTERY['wilson_low']:
        status = 'NOT_MASTERED'
    else:
        reasons.append('confidence_interval_straddles_mastery_line')
    return {'status': status, 'pass_rate': pass_rate, 'wilson_low': low,
            'wilson_high': high, 'n_episodes': n, 'passes': passes,
            'reasons': reasons,
            'mastery_rule_id': MASTERY_RULE_ID,
            'thresholds_version': THRESHOLDS_VERSION,
            'thresholds_hash': THRESHOLDS_HASH}


def s5_overall(sub_statuses):
    """S5 总体聚合：S5.1 与 S5.3 都 MASTERED 才 MASTERED；任一 INCOMPUTABLE→总体 INCOMPUTABLE。"""
    required = [sub_statuses.get('S5.1'), sub_statuses.get('S5.3')]
    if any(status == 'INCOMPUTABLE' for status in required):
        return 'INCOMPUTABLE'
    if any(status == 'NOT_MASTERED' for status in required):
        return 'NOT_MASTERED'
    if all(status == 'MASTERED' for status in required):
        return 'MASTERED'
    if any(status == 'UNKNOWN' for status in required):
        return 'UNKNOWN'
    return 'UNDETERMINED'


def aggregate_skill(skill_id, per_episodes):
    """per_episodes: [{'episode_id':..., 'skills': {sid: episode_skill_result}, ...}]"""
    if skill_id == 'S5':
        sub_results = {}
        for sub in ('S5.1', 'S5.2', 'S5.3'):
            sub_results[sub] = _aggregate_pass_rate(sub, per_episodes)
        overall = s5_overall({sub: r['status'] for sub, r in sub_results.items()})
        return {'skill': 'S5', 'status': overall, 'aggregation': S5_AGG_ID,
                'subskills': sub_results, 'mastery_rule_id': MASTERY_RULE_ID,
                'thresholds_version': THRESHOLDS_VERSION,
                'thresholds_hash': THRESHOLDS_HASH,
                'reasons': ['aggregated_from_subskills']}
    result = _aggregate_pass_rate(skill_id, per_episodes)
    result['skill'] = skill_id
    if skill_id == 'S4':
        rule = S4_SAMPLE_RULE
        totals = {'n_total_eval': 0, 'n_opp_episodes': 0, 'n_opportunity': 0,
                  'n_retreat_executed': 0}
        for entry in per_episodes:
            episode = entry.get('episode') or {}
            totals['n_total_eval'] += 1
            opportunities = episode.get('retreat_opportunities') or 0
            if opportunities:
                totals['n_opp_episodes'] += 1
            totals['n_opportunity'] += opportunities
            totals['n_retreat_executed'] += episode.get('retreat_executed') or 0
        result['counts'] = totals
        missing = []
        if totals['n_total_eval'] < rule['min_total_episodes']:
            missing.append('n_total_eval')
        if totals['n_opp_episodes'] < rule['min_opportunity_episodes']:
            missing.append('n_opp_episodes')
        if totals['n_opportunity'] < rule['min_opportunities']:
            missing.append('n_opportunity')
        if totals['n_retreat_executed'] < rule['min_executed']:
            missing.append('n_retreat_executed')
        result['sample_rule_id'] = S4_SAMPLE_RULE_ID
        result['sample_rule_missing'] = missing
        if missing and result['status'] == 'MASTERED':
            result['status'] = 'UNDETERMINED'
            result['reasons'].append('s4_sample_rule_not_met')
    return result


def thresholds_metadata():
    return {'thresholds_version': THRESHOLDS_VERSION,
            'thresholds_hash': THRESHOLDS_HASH,
            'mastery_rule_id': MASTERY_RULE_ID,
            's4_sample_rule_id': S4_SAMPLE_RULE_ID,
            's5_aggregation_id': S5_AGG_ID}
