"""自主学习管理器 V2.2：只提建议、不启动训练。

规则：回归复习优先 > 未决扩样 > 依赖前置 > 最低 pass_rate > 实验需求 > 维持/升阶。
所有决策必须基于同一 thresholds_version/hash 与同一 eval_protocol_hash 的记录，否则拒绝。
"""
import argparse
import pathlib

from dsh_learning import common, skill_metrics

SCHEMA_VERSION = 'v2.2-curriculum-proposal'
INCOMPARABLE = 'INCOMPARABLE'


def _run_name(record):
    run = record.get('run')
    return str(run.get('run') if isinstance(run, dict) else run)


class LearningManager:
    def __init__(self, skill_eval, run_scan=None):
        self.skill_eval = skill_eval or {}
        self.runs = list(self.skill_eval.get('runs', []))
        self.runs.sort(key=lambda r: r.get('generated_at') or '')
        self.run_scan = run_scan or {}
        self._consistency = self._check_consistency()

    def _check_consistency(self):
        if not self.runs:
            return {'ok': False, 'reason': 'no_skill_eval_records'}
        versions = {r.get('thresholds_version') for r in self.runs}
        hashes = {r.get('thresholds_hash') for r in self.runs}
        protocols = {r.get('eval_protocol_hash') for r in self.runs}
        if len(versions) > 1 or len(hashes) > 1:
            return {'ok': False, 'reason': 'thresholds_version_or_hash_mixed',
                    'versions': sorted(str(v) for v in versions)}
        if len(protocols) > 1:
            return {'ok': False, 'reason': 'eval_protocol_hash_mixed',
                    'protocols': sorted(str(p) for p in protocols)}
        return {'ok': True, 'thresholds_version': list(versions)[0],
                'thresholds_hash': list(hashes)[0],
                'eval_protocol_hash': list(protocols)[0]}

    @property
    def latest(self):
        if not self.runs:
            return {}
        return (self.runs[-1].get('skills') or {})

    def skill_status(self, sid):
        return (self.latest.get(sid) or {}).get('status')

    def skill_rate(self, sid):
        rate = (self.latest.get(sid) or {}).get('pass_rate')
        return -1.0 if rate is None else rate

    def dependencies_met(self, sid):
        return all(self.skill_status(dep) == 'MASTERED'
                   for dep in skill_metrics.DEPENDENCIES.get(sid, []))

    def unlocked_skills(self):
        return [sid for sid in skill_metrics.SKILL_ORDER if self.dependencies_met(sid)]

    def regression_flags(self):
        flags = []
        for index in range(1, len(self.runs)):
            prev = self.runs[index - 1].get('skills') or {}
            cur = self.runs[index].get('skills') or {}
            for sid in ('S1', 'S2', 'S3', 'S4'):
                before = (prev.get(sid) or {}).get('status')
                after = (cur.get(sid) or {}).get('status')
                if before == 'MASTERED' and after in ('NOT_MASTERED', 'UNDETERMINED'):
                    flags.append({'skill': sid, 'from': before, 'to': after,
                                  'run': _run_name(self.runs[index])})
        return flags

    def weakest_skill(self):
        trainable = []
        for sid in skill_metrics.SKILL_ORDER:
            status = self.skill_status(sid)
            if status in ('MASTERED', 'INCOMPUTABLE', 'UNKNOWN', 'NOT_MEASURED'):
                continue
            unmet = [dep for dep in skill_metrics.DEPENDENCIES.get(sid, [])
                     if self.skill_status(dep) != 'MASTERED']
            if unmet:
                continue
            trainable.append((sid, self.skill_rate(sid)))
        if not trainable:
            return None, 'all_trainable_skills_mastered_or_blocked'
        sid, rate = min(trainable, key=lambda item: item[1])
        return sid, 'lowest pass_rate among dependencies-met skills (%.3f)' % rate

    def experiment_required(self):
        missing = []
        for sid in ('S5',):
            block = self.latest.get(sid) or {}
            if block.get('status') == 'INCOMPUTABLE':
                missing.append({'skill': sid, 'reason': 'INCOMPUTABLE_ENV_CONSTRAINT',
                                'needed_experiment': 'close reveal mode / full-control counter-adaptation env'})
        return missing

    def propose_curriculum(self):
        base = {
            'schema': SCHEMA_VERSION,
            'generated_at': common.now_iso(),
            'evidence': self._consistency,
            'launch_performed': False,
            'requires_user_approval': True,
            'manager_started_training': False,
        }
        if not self._consistency.get('ok'):
            base.update(status=INCOMPARABLE, course=None,
                        rationale='评测记录不可比，拒绝课程决策：'
                                  + self._consistency.get('reason', 'unknown'))
            return base
        regressions = self.regression_flags()
        if regressions:
            target = regressions[0]['skill']
            base.update(status='OK', course={'type': 'REVIEW', 'target_skill': target},
                        rationale='已掌握技能回退，优先复习：%s' % regressions,
                        evidence_record_id=self.runs[-1].get('record_id'))
            return base
        undecided = [sid for sid in skill_metrics.SKILL_ORDER
                     if self.skill_status(sid) in ('UNDETERMINED',)]
        if undecided:
            base.update(status='OK',
                        course={'type': 'EXPAND_EVALUATION', 'target_skill': undecided[0],
                                'plan': {'from_episodes': 20, 'to_episodes': 40}},
                        rationale='技能证据不足（UNDETERMINED），先扩样评测，不排训练',
                        evidence_record_id=self.runs[-1].get('record_id'))
            return base
        target, reason = self.weakest_skill()
        if target is None:
            experiments = self.experiment_required()
            if experiments:
                base.update(status='OK',
                            course={'type': 'EXPERIMENT_REQUIRED', 'target_skill': 'S5',
                                    'experiments': experiments},
                            rationale='可训练技能已掌握或阻塞；需专门实验')
            else:
                base.update(status='OK',
                            course={'type': 'MAINTAIN_OR_ADVANCE', 'target_skill': None},
                            rationale='全部可测技能已掌握，进入维持/迁移评估阶段')
            return base
        base.update(
            status='OK',
            course={'type': 'LEARN', 'target_skill': target},
            rationale=reason,
            evidence_record_id=self.runs[-1].get('record_id'),
            training_profile={
                'workers': 8, 'episode_seconds': 300,
                'reward_version': (self.runs[-1].get('training_context') or {}).get(
                    'reward_version') or 'full_configurable_v4',
                'action_space_version': 'full_ground_v2p1',
                'thresholds_version': skill_metrics.THRESHOLDS_VERSION,
                'thresholds_hash': skill_metrics.THRESHOLDS_HASH,
                'note': '不修改 reward/action/obs；启动需一次性审批令牌与用户批准'},
            budget={'max_steps': 10000, 'eval_every_steps': 2500, 'eval_episodes': 20},
        )
        return base

    def select_best_model(self, compatibility_evaluation, run_dir_map=None):
        run_dir_map = run_dir_map or {}
        candidates = []
        records = {(record.get('run') if isinstance(record.get('run'), str)
                    else str(record.get('run'))): record for record in self.runs}
        for result in (compatibility_evaluation or {}).get('results', []):
            if not result.get('read_only_eval'):
                continue
            model_id = result['id']
            run_key = run_dir_map.get(model_id) or model_id
            record = None
            for key, candidate in records.items():
                if key.endswith(run_key) or run_key in key:
                    record = candidate
                    break
            if record is None:
                continue
            score = self._weighted_score(record.get('skills') or {})
            candidates.append({'id': model_id, 'path': result['path'],
                               'score': round(score, 4), 'run': _run_name(record),
                               'record_id': record.get('record_id'),
                               'eval_protocol_hash': record.get('eval_protocol_hash'),
                               'thresholds_version': record.get('thresholds_version')})
        return max(candidates, key=lambda item: item['score']) if candidates else None

    @staticmethod
    def _weighted_score(skills):
        weights = {'S1': 0.25, 'S2': 0.20, 'S3': 0.25, 'S4': 0.20, 'S5': 0.10}
        total = 0.0
        for sid, weight in weights.items():
            block = skills.get(sid) or {}
            rate = block.get('pass_rate')
            if block.get('status') == 'MASTERED':
                rate = max(rate or 0.0, 0.8)
            total += weight * (rate or 0.0)
        return total

    def archive_pointer(self, best, path='reports/dsh-learning/best-model.json'):
        if not best:
            return None
        payload = {'schema': 'v2.2-best-model-pointer', 'generated_at': common.now_iso(),
                   'pointer_only': True, 'model_copy': False, **best}
        common.save_json_atomic(common.ROOT / path, payload)
        return payload

    def rollback_plan(self, previous=None, reason=''):
        return {'requires_user_approval': True, 'auto_execute': False,
                'reason': reason, 'previous': previous,
                'steps': ['冻结当前评测证据', '用固定评测比较当前/上一最佳',
                          '用户批准后以旧模型作为续训起点（新 run 目录）',
                          '保留全部历史 run 与模型，不覆盖文件']}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--skill-eval', default='reports/dsh-learning/skill-eval-runs.json')
    ap.add_argument('--compatibility', default='reports/dsh-learning/model-compatibility.json')
    ap.add_argument('--out', default='reports/dsh-learning/manager/curriculum-proposal.json')
    ap.add_argument('--extra-run-scan', action='append', default=None)
    args = ap.parse_args(argv)
    payload = common.load_json(pathlib.Path(args.skill_eval)) or {}
    manager = LearningManager(payload)
    proposal = manager.propose_curriculum()
    common.save_json_atomic(common.ROOT / args.out, proposal)
    compatibility = common.load_json(pathlib.Path(args.compatibility)) or {}
    registry = common.load_json(pathlib.Path(
        'reports/dsh-learning/model-registry.json')) or {}
    run_dir_map = {}
    for asset in registry.get('assets', []):
        run_dir = (asset.get('run') or {}).get('run_dir')
        if run_dir:
            run_dir_map[asset['id']] = pathlib.Path(run_dir).name
    best = manager.select_best_model(compatibility, run_dir_map)
    pointer = manager.archive_pointer(best)
    print('[manager] status=%s course=%s launch_performed=False requires_approval=True'
          % (proposal.get('status'), proposal.get('course')))
    print('[manager] best model pointer ->', pointer)


if __name__ == '__main__':
    main()
