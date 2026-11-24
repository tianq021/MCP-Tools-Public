"""实验 A 编排与分析（离线；不启动训练/游戏）。

实验 A：预训练模型（A）vs 随机初始化（A'）学习 S1，主指标=达到 S1 掌握所需环境步数，
预算耗尽未掌握 = 右删失（CENSORING_ID），seed 级为主推断（SEED_UNIT_ID）。
本模块只生成计划/配置模板/分析真实结果；**不包含任何训练启动代码**。
"""
import argparse
import pathlib

from dsh_learning import common, fixed_eval_protocol, seeding, skill_metrics, survival_stats

SCHEMA_VERSION = 'v2.2-experiment-a'
CENSORING_ID = 'v2.2-right-censored-at-budget'
SEED_UNIT_ID = 'v2.2-seed-level-primary-inference'
TRANSFER_METRIC_ID = 'v2.2-steps-to-mastery-censored'

ARMS = {
    'A': {'name': '预训练模型学习 S1', 'init': 'pretrained',
          'starting_model_hint': 'R1 或 203156（实验前固定一个）'},
    "A'": {'name': '随机初始化学习 S1', 'init': 'fresh',
           'starting_model_hint': None},
}
EXPLORATORY_SEEDS = [11, 22, 33]
FORMAL_SEEDS = [11, 22, 33, 44, 55]
BUDGET_STEPS = 15000
EVAL_EVERY_STEPS = 2500
EVAL_EPISODES = 20
WORKERS = 8
EPISODE_SECONDS = 300
TAU_STEPS = BUDGET_STEPS


def build_plan(formal=False):
    seeds = FORMAL_SEEDS if formal else EXPLORATORY_SEEDS
    protocol = dict(fixed_eval_protocol.EVAL_CONFIG)
    arms = []
    for arm_id, arm in ARMS.items():
        runs = []
        for seed in seeds:
            runs.append({
                'arm': arm_id, 'seed': seed,
                'run_dir_template': 'reports/rl/transfer-v2/expA-%s-seed%d'
                                    % ('pretrained' if arm_id == 'A' else 'fresh', seed),
                'config_template': 'patches: approval.experiment=A, approval.skill=S1, '
                                   'approval.budget={max_steps:%d, eval_every_steps:%d, '
                                   'eval_episodes:%d}, seed=%d, workers=%d, '
                                   'episode_seconds=%d'
                                   % (BUDGET_STEPS, EVAL_EVERY_STEPS,
                                      EVAL_EPISODES, seed, WORKERS, EPISODE_SECONDS),
                'init': arm['init'],
                'starting_model_hint': arm['starting_model_hint'],
                'status': 'NOT_STARTED',
            })
        arms.append({'arm': arm_id, 'name': arm['name'], 'runs': runs})
    return {
        'schema': SCHEMA_VERSION,
        'generated_at': common.now_iso(),
        'status': 'PLANNED_NOT_EXECUTED',
        'training_executed': False,
        'game_executed': False,
        'approval_required': True,
        'approval_gate': 'D4: explicit user approval required before any training run',
        'censoring_rule_id': CENSORING_ID,
        'seed_unit_id': SEED_UNIT_ID,
        'primary_metric_id': TRANSFER_METRIC_ID,
        'design': {
            'arms': list(ARMS.keys()),
            'seeds': seeds,
            'seed_count': len(seeds),
            'evidence_level': 'EXPLORATORY' if len(seeds) < 5 else 'CONFIRMATORY',
            'budget_steps': BUDGET_STEPS,
            'eval_every_steps': EVAL_EVERY_STEPS,
            'eval_episodes': EVAL_EPISODES,
            'workers': WORKERS,
            'episode_seconds': EPISODE_SECONDS,
            'primary_metric': 'steps_to_S1_mastery（右删失，seed 级）',
            'thresholds_version': skill_metrics.THRESHOLDS_VERSION,
            'thresholds_hash': skill_metrics.THRESHOLDS_HASH,
            'mastery_rule_id': skill_metrics.MASTERY_RULE_ID,
            'eval_protocol_hash': fixed_eval_protocol.PROTOCOL_HASH,
            'seeding_support': seeding.seed_everything.__doc__.strip().splitlines()[0],
            'statistics': {
                'censoring': CENSORING_ID,
                'seed_unit': SEED_UNIT_ID,
                'methods': ['Kaplan-Meier median', 'RMST', 'log-rank',
                            'seed-level bootstrap 95% CI'],
                'analysis_module': 'scripts/dsh_learning/survival_stats.py',
                'not_allowed': ['只对成功 seed 求均值', '把 episode 当独立训练种子',
                                '删除删失样本'],
            },
            'stopping_rules': ['训练不稳定（>5 连续 interrupted）→ EXCLUDED_RUN',
                               '每 2500 步冻结评测（optimizer_updates==0）',
                               '预算耗尽未掌握 → 右删失，不得删样本'],
        },
        'arms': arms,
        'execution_plan': {
            'step_1': '用户批准 D4 并发放每 run 一次性审批令牌（P0）',
            'step_2': '按 runs[] 逐个启动训练（一次一个 run；token 一次性）',
            'step_3': '每 2500 步用 fixed-eval-plan 协议冻结评测（20 局）',
            'step_4': '用 survival_stats.compare_arms 做 seed 级删失感知分析',
            'step_5': '报告每 seed 原始值 + KM/RMST + bootstrap CI + 敏感性分析',
        },
        'not_a_result': '本计划不是训练结果；未产生任何 steps_to_mastery 数据',
    }


def analysis_template():
    return {
        'schema': 'v2.2-experiment-a-analysis',
        'status': 'NOT_MEASURED',
        'na_reason': 'experiment_not_executed',
        'required_input': 'real per-seed records: [{seed, duration, event}] per arm',
        'censoring_rule_id': CENSORING_ID,
        'seed_unit_id': SEED_UNIT_ID,
    }


def analyze_arms(arm_a, arm_b, tau=TAU_STEPS, mark_synthetic=False):
    """分析真实结果；空样本必须返回 NOT_MEASURED，不得伪造。"""
    if not arm_a or not arm_b:
        return analysis_template()
    result = survival_stats.compare_arms(arm_a, arm_b, tau=tau)
    result.update({'schema': 'v2.2-experiment-a-analysis',
                   'status': 'ANALYZED',
                   'censoring_rule_id': CENSORING_ID,
                   'seed_unit_id': SEED_UNIT_ID,
                   'primary_metric_id': TRANSFER_METRIC_ID,
                   'synthetic_input': bool(mark_synthetic),
                   'thresholds_version': skill_metrics.THRESHOLDS_VERSION})
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest='command', required=True)
    p_plan = sub.add_parser('plan', help='生成实验 A 计划（不启动训练）')
    p_plan.add_argument('--formal', action='store_true', help='使用 5 seed（正式）')
    p_plan.add_argument('--out', default='reports/dsh-learning/experiment-a-plan.json')
    p_ana = sub.add_parser('analyze', help='分析真实 per-seed 结果 JSON')
    p_ana.add_argument('--results', required=True,
                       help='{"A":[{seed,duration,event}], "A\'":[...]}')
    p_ana.add_argument('--out', default='reports/dsh-learning/experiment-a-analysis.json')
    args = ap.parse_args(argv)
    if args.command == 'plan':
        plan = build_plan(formal=args.formal)
        common.save_json_atomic(common.ROOT / args.out, plan)
        print('[expA] status=%s seeds=%d runs=%d -> %s'
              % (plan['status'], plan['design']['seed_count'],
                 sum(len(arm['runs']) for arm in plan['arms']), args.out))
        print('[expA] training NOT started: D4 approval required')
        return 0
    payload = common.load_json(pathlib.Path(args.results)) or {}
    analysis = analyze_arms(payload.get('A') or [], payload.get("A'") or [])
    common.save_json_atomic(common.ROOT / args.out, analysis)
    print('[expA] analysis status=%s -> %s' % (analysis['status'], args.out))
    return 0


if __name__ == '__main__':
    main()
