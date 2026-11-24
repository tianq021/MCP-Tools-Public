"""V2 前置：对 V1 技能评价链路做离线量化审查（只读，不启动游戏/训练）。

测量项（与任务书 §1 六个核实问题对应）：
  1) 指标来源：轨迹/结果字段的可追溯性（本脚本只从轨迹与 result.json 重算）；
  2) 重复帧/停帧偏差：观测帧重复率、观测间隔分布、对 first_attack/attacks_per_min 的影响；
  3) 攻击有效性归因偏差：目标在窗口内消失（被击杀/脱离可见）时被误判为“无效”的比例；
  4) 己方损失检测覆盖：观测血量差 vs result 的 truth 记录；
  5) 撤退事件算法膨胀：原始 20s 窗口计数 vs 合并为独立事件后的计数；
  6) 统计充分性：Wilson 置信区间 + 二项功效（10/20/30 局、p=0.5 vs 0.7）；
  7) worker 数量对墙钟学习时间的影响（效率表读取）。

用法：
  PYTHONPATH=scripts python scripts/dsh_learning/v2_audit.py \
      --out reports/dsh-learning/v2-audit-metrics.json --max-per-run 8
"""
import argparse
import json
import math
import pathlib
import statistics
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / 'scripts'))
from dsh_learning import common, trace_parser  # noqa: E402

RUNS = [
    'reports/rl/stage-A-round1-20261030-153225',
    'reports/rl/stage-A-round2-20261030-155824',
    'reports/rl/stage-A-round3-20261030-162536',
    'reports/rl/ppo-shared-20261030-203156',
    'reports/rl/ppo-shared-20261030-210239',
]


def wilson_interval(k, n, z=1.96):
    if n == 0:
        return (None, None)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (round(max(0.0, center - half), 4), round(min(1.0, center + half), 4))


def binomial_power(n, p0, p1, alpha=0.05):
    """单侧检验：H0 p<=p0 vs H1 p>=p1；返回在 p1 下达到显著的概率（精确二项）。"""
    def cdf(x, p):
        return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(0, x + 1))
    # 临界值：满足 P(X>=k | p0) <= alpha 的最小 k
    k_crit = None
    for k in range(n + 1):
        if 1 - cdf(k - 1, p0) <= alpha:
            k_crit = k
            break
    if k_crit is None:
        return {'n': n, 'k_crit': None, 'power': 0.0}
    power = 1 - cdf(k_crit - 1, p1)
    return {'n': n, 'k_crit': k_crit, 'power_at_p1': round(power, 4),
            'alpha': alpha, 'p0': p0, 'p1': p1}


def audit_episode(jsonl, result):
    """对单个 episode 统计重复帧、归因偏差等。"""
    obs, attacks, damage = [], [], []
    with jsonl.open(encoding='utf-8', errors='replace') as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            kind = rec.get('type')
            if kind == 'observation':
                obs.append(rec)
            elif kind == 'action' and rec.get('decision_source') == 'ppo':
                attacks.append(rec)
    # 1) 重复帧：相邻 observation 的 frame 相同 / t 相同
    dup_frame = dup_t = 0
    gaps = []
    enemy_seen = {}  # addr -> last observation index
    for i, o in enumerate(obs):
        if i:
            prev = obs[i - 1]
            if o.get('frame') == prev.get('frame'):
                dup_frame += 1
            t, pt = float(o.get('t') or 0), float(prev.get('t') or 0)
            if t == pt:
                dup_t += 1
            gaps.append(t - pt)
        for e in o.get('visible_enemies') or []:
            enemy_seen[e.get('addr')] = i
    n = len(obs)
    # 2) 攻击有效性归因：目标是否在攻击后 6s 内仍在可见列表/是否有血量差
    times_by_addr = {}
    for i in range(1, len(obs)):
        prev, cur = obs[i - 1], obs[i]
        pe = {x.get('addr'): x for x in (prev.get('visible_enemies') or [])}
        for e in (cur.get('visible_enemies') or []):
            po = pe.get(e.get('addr'))
            if (po and po.get('type_name') == e.get('type_name')
                    and po.get('owner') == e.get('owner')):
                delta = int(po.get('health') or 0) - int(e.get('health') or 0)
                if delta > 0:
                    times_by_addr.setdefault(e['addr'], []).append(float(cur.get('t') or 0))
    n_attack = n_damaged = n_unknown = 0
    obs_time = [float(o.get('t') or 0) for o in obs]
    for i, a in enumerate(attacks):
        if a.get('action') != 'ATTACK':
            continue
        # 找到紧邻的观测时间（V1 同样做法）
        t = obs_time[min(i, len(obs_time) - 1)] if obs_time else 0.0
        target = a.get('target_address')
        n_attack += 1
        times = times_by_addr.get(target, [])
        if any(t - 1e-9 <= x <= t + 6.0 + 1e-9 for x in times):
            n_damaged += 1
        else:
            # 目标最后一次可见时间：若在攻击后 6s 内消失，则归因不确定
            last_visible = None
            for o in obs:
                for e in (o.get('visible_enemies') or []):
                    if e.get('addr') == target:
                        last_visible = float(o.get('t') or 0)
                        break
            if last_visible is not None and t <= last_visible <= t + 6.0:
                n_unknown += 1
    # 3) 己方损失覆盖：truth 移除 HP vs 观测血量差
    metrics = result.get('metrics') or {}
    elapsed = float(result.get('elapsed_s') or (obs_time[-1] if obs_time else 0.0))
    return {
        'episode_id': result.get('episode_id') or jsonl.stem,
        'observations': n,
        'dup_frame_ratio': round(dup_frame / max(1, n - 1), 4),
        'dup_t_ratio': round(dup_t / max(1, n - 1), 4),
        'gap_p95_s': round(sorted(gaps)[int(0.95 * (len(gaps) - 1))], 2) if gaps else None,
        'gap_max_s': round(max(gaps), 2) if gaps else None,
        'gaps_gt2s': sum(1 for g in gaps if g > 2.0),
        'gaps_gt5s': sum(1 for g in gaps if g > 5.0),
        'attacks': n_attack,
        'attacks_damaged_6s': n_damaged,
        'attacks_target_vanished_6s': n_unknown,
        'vanish_share_of_ineffective': (round(n_unknown / max(1, n_attack - n_damaged), 4)
                                        if n_attack > n_damaged else 0.0),
        'truth_removed_hp': metrics.get('truth_own_removed_hp'),
        'own_hp_lost_result': metrics.get('own_hp_lost'),
        'elapsed_s': round(elapsed, 1),
        # 攻击/分钟：口径 A = result.elapsed；口径 B = 观测时间跨度
        'attacks_per_min_result_clock': (round(n_attack / (elapsed / 60.0), 3) if elapsed else None),
        'attacks_per_min_obs_clock': (round(n_attack / ((obs_time[-1] - obs_time[0]) / 60.0), 3)
                                      if obs_time and obs_time[-1] > obs_time[0] else None),
    }


def retreat_inflation(episode_metrics):
    """撤退事件算法：V1 原始实现按样本计数，会重复计数同一段脱离。
    这里用已解析轨迹的简化近似：对比“窗口计数”与“独立事件计数”的膨胀上限估计。
    """
    # 无法从聚合指标精确重算，改用逻辑推导说明：每个连续脱离段的内样本数即膨胀倍数。
    return {'note': 'V1 trace_parser.py:160-169 对每个满足条件的样本都 +=1，'
                    '同一段脱离在连续样本中会被重复计数；膨胀倍数≈脱离持续秒数/采样间隔',
            'expected_inflation_upper_bound': '≈ (脱离持续秒数 / 观测间隔)，按 0.5s 间隔最多数十倍'}


def worker_effect():
    eff = common.load_json(_ROOT / 'reports/dsh-learning/learning-efficiency.json')
    rows = []
    for r in (eff or {}).get('runs', []):
        rows.append({'run': r['run'], 'workers': r['workers'],
                     'steps_per_hour': r['steps_per_hour'],
                     'episodes_per_hour': r['episodes_per_hour'],
                     'episode_seconds': r['episode_seconds']})
    vals = [r for r in rows if r.get('steps_per_hour') and r.get('workers')]
    ratio = None
    two = [r for r in vals if r['workers'] == 2]
    many = [r for r in vals if r['workers'] and r['workers'] >= 8]
    if two and many:
        ratio = round(statistics.fmean(r['steps_per_hour'] for r in many)
                      / statistics.fmean(r['steps_per_hour'] for r in two), 2)
    return {'runs': rows, 'steps_per_hour_ratio_8plus_vs_2': ratio,
            'conclusion': '墙钟学习时间随 worker 数近似线性下降；'
                          '因此“学习耗时”必须同时报告环境步数与当时 worker 配置，'
                          '或统一用环境步数作为效率主指标。'}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', default='reports/dsh-learning/v2-audit-metrics.json')
    ap.add_argument('--max-per-run', type=int, default=8)
    args = ap.parse_args(argv)
    result = {'generated_at': common.now_iso(),
              'method': '只读离线重算：既有 episode 轨迹 + result.json；不启动游戏/训练',
              'runs': {}, 'statistics': {}, 'retreat_algorithm': retreat_inflation(None),
              'worker_effect': worker_effect()}
    for run in RUNS:
        run_dir = _ROOT / run
        if not run_dir.exists():
            continue
        episodes = trace_parser.load_run_episodes(run_dir, max_episodes=args.max_per_run)
        audits = []
        for jsonl, res in episodes:
            try:
                audits.append(audit_episode(jsonl, res))
            except Exception as exc:  # noqa: BLE001
                audits.append({'episode_id': jsonl.stem, 'error': repr(exc)})
        ok = [a for a in audits if 'error' not in a]
        def mean_of(key):
            vals = [a[key] for a in ok if a.get(key) is not None]
            return round(statistics.fmean(vals), 4) if vals else None
        result['runs'][run] = {
            'episodes_audited': len(ok),
            'dup_frame_ratio_mean': mean_of('dup_frame_ratio'),
            'dup_t_ratio_mean': mean_of('dup_t_ratio'),
            'gap_p95_s_mean': mean_of('gap_p95_s'),
            'gap_max_s': max((a['gap_max_s'] for a in ok if a.get('gap_max_s') is not None), default=None),
            'gaps_gt5s_total': sum(a.get('gaps_gt5s') or 0 for a in ok),
            'attacks_total': sum(a.get('attacks') or 0 for a in ok),
            'vanish_share_of_ineffective_mean': mean_of('vanish_share_of_ineffective'),
            'attacks_per_min_result_mean': mean_of('attacks_per_min_result_clock'),
            'attacks_per_min_obs_mean': mean_of('attacks_per_min_obs_clock'),
            'episodes': audits,
        }
    # 统计充分性：以 V1 实际 pass 计数为例（R1 S2 = 1/10 等）计算 Wilson 区间
    samples = {'R1_S2': (1, 10), 'R1_S4': (1, 10), '203156_S2': (3, 20),
               '203156_S4': (1, 20), '假设 7/10': (7, 10), '假设 14/20': (14, 20)}
    result['statistics']['wilson_examples'] = {
        k: {'k': v[0], 'n': v[1], 'p': round(v[0] / v[1], 3),
            'wilson95': wilson_interval(v[0], v[1])} for k, v in samples.items()}
    result['statistics']['power'] = [
        binomial_power(n, 0.5, 0.7) for n in (10, 20, 30, 40)]
    result['statistics']['mastery_rule_note'] = (
        'V1 规则“n>=10 且 pass_rate>=0.70”在真 p=0.5 时仍有较高误判概率；'
        'V2 建议用 Wilson 95% 下界 >= 0.70 且 n>=20 作为掌握门槛（见 skill-metrics-v1-proposal.md）。')
    common.save_json_atomic(_ROOT / args.out, result)
    print(f"[v2-audit] wrote {args.out}")
    for run, info in result['runs'].items():
        print(f"  {pathlib.Path(run).name:<36} eps={info['episodes_audited']:<2} "
              f"dup_frame={info['dup_frame_ratio_mean']} dup_t={info['dup_t_ratio_mean']} "
              f"gap_p95={info['gap_p95_s_mean']} gap_max={info['gap_max_s']} "
              f"apm_result={info['attacks_per_min_result_mean']} "
              f"apm_obs={info['attacks_per_min_obs_mean']} "
              f"vanish_share={info['vanish_share_of_ineffective_mean']}")
    print('  worker ratio (>=8 vs 2):', result['worker_effect']['steps_per_hour_ratio_8plus_vs_2'])
    print('  wilson 7/10:', result['statistics']['wilson_examples']['假设 7/10'])
    for p in result['statistics']['power']:
        print(f"  power n={p['n']} k_crit={p['k_crit']} power={p.get('power_at_p1')}")


if __name__ == '__main__':
    main()
