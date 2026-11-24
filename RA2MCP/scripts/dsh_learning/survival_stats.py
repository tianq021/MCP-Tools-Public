"""右删失统计（V2.2 迁移实验要求，纯离线）。

实现：Kaplan–Meier 估计、中位生存时间、RMST、log-rank 检验、seed 级 bootstrap 置信区间。
数据约定（seed 为实验单元）：
  record = {'seed': int, 'duration': float, 'event': bool}
  event=True 表示达到掌握（事件）；event=False 表示预算耗尽未掌握（右删失，duration=预算步数）。
禁止把删失样本从分母删除。
"""
import math
import random


def _validate(records):
    if not records:
        raise ValueError('EMPTY_SAMPLE')
    out = []
    for record in records:
        duration = record.get('duration')
        if duration is None or duration < 0:
            raise ValueError('BAD_DURATION: %r' % (record,))
        out.append({'seed': record.get('seed'), 'duration': float(duration),
                    'event': bool(record.get('event'))})
    return out


def kaplan_meier(records):
    """返回 {'times':[...], 'survival':[...], 'at_risk':[...], 'events':[...]}。"""
    data = _validate(records)
    times = sorted({record['duration'] for record in data})
    survival, points = 1.0, []
    for t in times:
        at_risk = sum(1 for record in data if record['duration'] >= t)
        events = sum(1 for record in data
                     if record['duration'] == t and record['event'])
        if at_risk:
            survival *= (1.0 - events / at_risk)
        points.append({'t': t, 'survival': round(survival, 6),
                       'at_risk': at_risk, 'events': events})
    return {'times': [p['t'] for p in points],
            'survival': [p['survival'] for p in points],
            'at_risk': [p['at_risk'] for p in points],
            'events': [p['events'] for p in points],
            'n': len(data), 'n_events': sum(1 for r in data if r['event'])}


def survival_at(km, t):
    value = 1.0
    for point_t, point_s in zip(km['times'], km['survival']):
        if point_t <= t:
            value = point_s
        else:
            break
    return value


def median_survival(records):
    """KM 中位：S(t)<=0.5 的最小 t；不可识别返回 None（不能伪造）。"""
    km = kaplan_meier(records)
    for t, s in zip(km['times'], km['survival']):
        if s <= 0.5:
            return t
    return None


def rmst(records, tau):
    """restricted mean survival time：KM 曲线在 [0, tau] 的积分。"""
    if tau <= 0:
        raise ValueError('BAD_TAU')
    km = kaplan_meier(records)
    area, previous_t, previous_s = 0.0, 0.0, 1.0
    for t, s in zip(km['times'], km['survival']):
        if t > tau:
            break
        area += previous_s * (t - previous_t)
        previous_t, previous_s = t, s
    area += previous_s * max(0.0, tau - previous_t)
    return round(area, 4)


def logrank(records_a, records_b):
    """两组 KM 差异的 log-rank 检验（df=1），返回 chi2 与 p。"""
    a, b = _validate(records_a), _validate(records_b)
    times = sorted({record['duration'] for record in a + b})
    observed = expected = variance = 0.0
    for t in times:
        at_risk_a = sum(1 for r in a if r['duration'] >= t)
        at_risk_b = sum(1 for r in b if r['duration'] >= t)
        at_risk = at_risk_a + at_risk_b
        events_a = sum(1 for r in a if r['duration'] == t and r['event'])
        events_b = sum(1 for r in b if r['duration'] == t and r['event'])
        events = events_a + events_b
        if at_risk <= 1 or events == 0:
            continue
        observed += events_a
        expected += events * (at_risk_a / at_risk)
        variance += (events * (at_risk_a / at_risk) * (at_risk_b / at_risk)
                     * (at_risk - events) / (at_risk - 1))
    if variance <= 0:
        return {'chi2': 0.0, 'p_value': 1.0, 'note': 'no_informative_events'}
    chi2 = (observed - expected) ** 2 / variance
    p_value = math.erfc(math.sqrt(chi2 / 2.0))
    return {'chi2': round(chi2, 4), 'p_value': round(p_value, 6),
            'observed_events_a': observed, 'expected_events_a': round(expected, 4)}


def bootstrap_stat_ci(group_a, group_b, stat_fn, n_boot=2000, seed=20261030):
    """seed 级 bootstrap：对两组 seed 分别有放回重采样，返回差值 95% CI（百分位）。"""
    if not group_a or not group_b:
        return {'diff': None, 'ci_low': None, 'ci_high': None,
                'note': 'empty_group'}
    rng = random.Random(seed)
    values = []
    for _ in range(n_boot):
        sample_a = [group_a[rng.randrange(len(group_a))] for _ in range(len(group_a))]
        sample_b = [group_b[rng.randrange(len(group_b))] for _ in range(len(group_b))]
        try:
            value_a = stat_fn(sample_a)
            value_b = stat_fn(sample_b)
        except ValueError:
            continue
        if value_a is None or value_b is None:
            continue  # 中位数在重采样中不可识别 → 跳过，不伪造
        values.append(value_a - value_b)
    if not values:
        return {'diff': None, 'ci_low': None, 'ci_high': None,
                'note': 'no_valid_bootstrap_samples'}
    values.sort()
    low = values[int(0.025 * (len(values) - 1))]
    high = values[int(0.975 * (len(values) - 1))]
    return {'diff': round(stat_fn(group_a) - stat_fn(group_b), 4),
            'ci_low': round(low, 4), 'ci_high': round(high, 4),
            'n_boot': len(values), 'bootstrap_seed': seed}


def summarize_arm(records):
    data = _validate(records)
    km = kaplan_meier(data)
    return {'n_seeds': len(data), 'n_events': km['n_events'],
            'n_censored': len(data) - km['n_events'],
            'median': median_survival(data), 'quartiles': None,
            'seeds': [{'seed': r['seed'], 'duration': r['duration'],
                       'event': r['event']} for r in data],
            'km': km}


def compare_arms(arm_a, arm_b, tau, stat='median'):
    summary_a = summarize_arm(arm_a)
    summary_b = summarize_arm(arm_b)
    stat_fn = (median_survival if stat == 'median'
               else (lambda group: rmst(group, tau)))
    if stat == 'median' and (summary_a['median'] is None
                             or summary_b['median'] is None):
        return {'stat': 'median', 'diff': None,
                'note': 'median_not_identifiable_due_to_censoring',
                'arm_a': summary_a, 'arm_b': summary_b}
    ci = bootstrap_stat_ci(list(arm_a), list(arm_b),
                           lambda group: stat_fn(group), seed=20261030)
    return {'stat': stat, 'tau': tau if stat == 'rmst' else None,
            'logrank': logrank(list(arm_a), list(arm_b)),
            'bootstrap': ci, 'arm_a': summary_a, 'arm_b': summary_b}
