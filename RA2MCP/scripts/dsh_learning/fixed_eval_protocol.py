"""P3 固定评测协议与计划（离线；不启动游戏/评测）。

本模块只做：
1) 规范化并哈希固定评测协议（地图/难度/资金/单位/局长/决策周期/冻结断言/阈值版本）；
2) 生成每模型评测计划（局数、出生角落分层、期望 record schema、证据要求）；
3) 从实际模型文件重算 SHA256 并校验；
4) 输出 status=PLANNED_NOT_EXECUTED，禁止把计划当结果。

真正执行评测（启动游戏）属于 D3 单独批准范围，本模块不包含任何启动代码。
"""
import argparse
import hashlib
import json
import pathlib
import zipfile

from dsh_learning import common, skill_metrics

PROTOCOL_VERSION = 'v2.2-fixed-eval-1'
RECORD_SCHEMA = 'v2.2-skill-record'
FROZEN_ASSERTION = 'optimizer_updates == 0'
SPAWN_STRATEGY = 'stratified_by_corner'
KNOWN_CORNERS = [[92, 59], [90, 127]]

EVAL_CONFIG = {
    'protocol_version': PROTOCOL_VERSION,
    'map': '2peaks',
    'difficulty': 'easy',
    'credits': 20000,
    'units': 8,
    'superweapons': False,
    'control_mode': 'full',
    'episode_seconds': 300,
    'decision_seconds': 0.5,
    'spawn_strategy': SPAWN_STRATEGY,
    'corners': KNOWN_CORNERS,
    'frozen_assertion': FROZEN_ASSERTION,
    'thresholds_version': skill_metrics.THRESHOLDS_VERSION,
    'thresholds_hash': skill_metrics.THRESHOLDS_HASH,
    'mastery_rule_id': skill_metrics.MASTERY_RULE_ID,
    's4_sample_rule_id': skill_metrics.S4_SAMPLE_RULE_ID,
    's5_aggregation_id': skill_metrics.S5_AGG_ID,
    'record_schema': RECORD_SCHEMA,
}


def protocol_hash_of(config=None):
    config = config or EVAL_CONFIG
    return hashlib.sha256(
        json.dumps(config, sort_keys=True, separators=(',', ':'),
                   ensure_ascii=False).encode('utf-8')).hexdigest()


PROTOCOL_HASH = protocol_hash_of()


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def validate_model(model_path):
    """从实际文件取证（不信任调用方提供的哈希），返回模型信息。"""
    path = pathlib.Path(model_path)
    if not path.is_absolute():
        path = common.ROOT / path
    if not path.exists():
        raise ValueError('MODEL_NOT_FOUND: %s' % model_path)
    info = {'path': common.rel(path), 'size': path.stat().st_size,
            'sha256': _sha256_file(path), 'zip_valid': zipfile.is_zipfile(path)}
    if not info['zip_valid']:
        raise ValueError('MODEL_NOT_ZIP: %s' % model_path)
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        info['zip_members_sample'] = names[:8]
        info['is_maskable_ppo'] = any('policy' in name or 'data' in name for name in names)
    return info


def _corner_plan(n_episodes):
    """出生角落分层：先平均分配，余数给第一个角落；记录每角落最少局数。"""
    corners = len(KNOWN_CORNERS)
    base = n_episodes // corners
    remainder = n_episodes % corners
    return [{'corner': KNOWN_CORNERS[i],
             'min_episodes': base + (1 if i < remainder else 0)}
            for i in range(corners)]


def build_plan(model_paths, n_episodes=20, include_observed_models=None):
    if n_episodes < 20:
        raise ValueError('P3 fixed evaluation requires n_episodes >= 20 '
                         '(mastery rule cannot be evaluated with fewer)')
    models = [validate_model(path) for path in model_paths]
    plan = {
        'schema': 'v2.2-fixed-eval-plan',
        'protocol_version': PROTOCOL_VERSION,
        'protocol': EVAL_CONFIG,
        'protocol_hash': PROTOCOL_HASH,
        'generated_at': common.now_iso(),
        'status': 'PLANNED_NOT_EXECUTED',
        'launch_performed': False,
        'approval_required': True,
        'approval_gate': 'D3: user must explicitly approve game launch and evaluation budget',
        'requirements': {
            'frozen_model': FROZEN_ASSERTION,
            'episodes_per_model': n_episodes,
            'corner_plan': _corner_plan(n_episodes),
            'record_schema': RECORD_SCHEMA,
            'required_record_fields': ['model_sha256', 'eval_protocol_hash',
                                       'thresholds_version', 'thresholds_hash',
                                       'generated_at', 'data_source',
                                       'source_manifest_sha256'],
            'quality_gates': ['obs_coverage_composite >= 0.80',
                              'damage_coverage >= 0.50 or UNDETERMINED',
                              'UNUSABLE episodes excluded and logged'],
            'not_a_result': '本计划本身不是评测结果，禁止据此更新技能状态',
        },
        'models': [{'model': info, 'episodes': n_episodes,
                    'corner_plan': _corner_plan(n_episodes),
                    'expected_status': 'NOT_MEASURED_UNTIL_EXECUTED'}
                   for info in models],
        'notes': ['模型 SHA256 从实际文件重算；不信任调用方传入值',
                  '执行时必须断言 optimizer_updates==0（冻结参数）',
                  '协议或阈值变化后必须重新生成计划，不混合比较'],
    }
    if include_observed_models:
        plan['observed_models_reference'] = list(include_observed_models)
    return plan


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--model', action='append', required=True,
                    help='待评测模型路径（可重复）')
    ap.add_argument('--n-episodes', type=int, default=20)
    ap.add_argument('--out', default='reports/dsh-learning/fixed-eval-plan.json')
    args = ap.parse_args(argv)
    plan = build_plan(args.model, args.n_episodes)
    common.save_json_atomic(common.ROOT / args.out, plan)
    print('[fixed-eval] status=%s protocol_hash=%s models=%d -> %s'
          % (plan['status'], plan['protocol_hash'][:16], len(plan['models']), args.out))
    print('[fixed-eval] execution NOT performed: game launch requires explicit D3 approval')


if __name__ == '__main__':
    main()
