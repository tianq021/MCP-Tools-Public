"""模型兼容性判定：能否独立加载 / 只读推理 / 继续训练 / 只读评估。

判定原则（任务书要求）：模型文件存在 != 兼容当前环境。逐项检查：
  1) 文件与哈希；2) 独立可加载（SB3 zip 结构 + 无 env 加载）；
  3) Observation 维度；4) Action Space 版本与 ACTION_N；
  5) 单位/目标槽语义（unit_slots_stable）；6) Reward 版本；
  7) 优化器状态；8) 训练历史（run.json 的 reward/action 变更标记）；
  9) 只读推理能力。
输出四类结论：
  READ_ONLY_EVAL     —— 可用于冻结参数评估
  CAN_RESUME         —— 可直接续训（reward/action 与当前默认一致）
  RESUME_WITH_FLAGS  —— 续训需显式 --allow-reward-change / --allow-action-space-change
  BLOCKED            —— 禁止续训（如 R3 保护）或结构不兼容
"""
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / 'scripts'))
from dsh_learning import common  # noqa: E402


def evaluate_asset(asset, contract):
    """对单条注册表记录做兼容性判定。返回可 JSON 化的判定结果。"""
    path = _ROOT / asset['path']
    file_info = asset.get('file') or {}
    run = (asset.get('run') or {}).get('run_json') or {}
    ins = asset.get('inspection') or {}
    checks = {}

    checks['file_exists'] = bool(file_info.get('exists'))
    checks['sha256_present'] = bool(file_info.get('sha256'))
    checks['kind'] = asset.get('kind')

    if asset.get('kind') == 'bc':
        loadable = bool(ins.get('loadable'))
        obs_ok = ins.get('obs_dim') == contract.get('obs_dim')
        n_ok = ins.get('action_n') == contract.get('action_n')
        verdict = 'BC_DIAGNOSTIC_ONLY'
        if loadable and not (obs_ok and n_ok):
            verdict = 'BC_INCOMPATIBLE_WITH_CURRENT_OBS_OR_ACTION'
        return {
            'id': asset['id'], 'path': asset['path'], 'verdict': verdict,
            'role': asset.get('role'),
            'read_only_eval': False,  # BC 仅离线诊断，不进入游戏评测
            'can_resume_train': False,
            'required_flags': [],
            'reasons': ['BC 权重按任务书不得强行迁移到 PPO；仅作离线行为参考/诊断'],
            'checks': {'loadable': loadable, 'obs_dim_match': obs_ok,
                       'action_n_match': n_ok,
                       'obs_dim': ins.get('obs_dim'),
                       'action_n': ins.get('action_n')},
        }

    if not ins:
        return {
            'id': asset['id'], 'path': asset['path'], 'role': asset.get('role'),
            'verdict': 'NOT_INSPECTED', 'read_only_eval': False,
            'can_resume_train': False, 'required_flags': [],
            'reasons': ['浅登记：该资产未做深度加载检查，不得据此下兼容性结论'],
            'checks': {'deep_inspection': False},
        }

    checks['loadable'] = bool(ins.get('loadable'))
    checks['inference_ok'] = bool(ins.get('inference_ok'))
    checks['obs_dim_model'] = ins.get('obs_dim')
    checks['obs_dim_current'] = contract.get('obs_dim')
    checks['obs_dim_match'] = ins.get('obs_dim') == contract.get('obs_dim')
    checks['action_n_model'] = ins.get('action_n')
    checks['action_n_current'] = contract.get('action_n')
    checks['action_n_match'] = ins.get('action_n') == contract.get('action_n')
    checks['optimizer_loaded'] = bool(ins.get('optimizer_loaded'))
    checks['parameters'] = ins.get('parameters')
    checks['net_arch'] = ins.get('net_arch')

    model_reward = run.get('reward_version')
    model_action = run.get('action_space_version')
    checks['reward_version_model'] = model_reward
    checks['reward_version_current'] = contract.get('reward_version')
    checks['reward_match'] = model_reward == contract.get('reward_version')
    checks['action_space_model'] = model_action
    checks['action_space_current'] = contract.get('action_space_version')
    checks['action_space_match'] = model_action == contract.get('action_space_version')
    checks['control_mode'] = run.get('control_mode') or (
        'full' if run.get('control_mode') is None and asset['id'] in ('R1', 'R2', 'R3') else None)
    checks['control_mode_full'] = checks['control_mode'] == 'full'

    reasons = []
    if not checks['file_exists']:
        return _verdict(asset, 'BLOCKED', False, False, reasons + ['模型文件不存在'], checks)
    if not checks['loadable']:
        reasons.append(f"SB3 独立加载失败：{ins.get('load_error')}")
        return _verdict(asset, 'BLOCKED', False, False, reasons, checks)
    if not checks['obs_dim_match']:
        reasons.append(f"observation 维度不匹配：模型 {checks['obs_dim_model']} vs 当前 {checks['obs_dim_current']}")
    if not checks['action_n_match']:
        reasons.append(f"ACTION_N 不匹配：模型 {checks['action_n_model']} vs 当前 {checks['action_n_current']}")
    obs_action_ok = checks['obs_dim_match'] and checks['action_n_match']

    read_only_eval = bool(checks['inference_ok'] and obs_action_ok)
    if not checks['inference_ok']:
        reasons.append('只读推理失败（predict 异常）')
    if not obs_action_ok:
        reasons.append('观测/动作结构不兼容 → 不能进入当前环境评估')

    if asset.get('do_not_resume'):
        return _verdict(asset, 'BLOCKED', read_only_eval, False,
                        reasons + ['该资产被标记为禁止续训（PROTECTED_DO_NOT_RESUME）'],
                        checks)

    flags = []
    if not checks['reward_match']:
        flags.append('--allow-reward-change')
        reasons.append(f"reward 版本不同：模型 {model_reward} vs 当前 {contract.get('reward_version')} "
                       "（续训会混合不同奖励量纲的 return，必须显式批准）")
    if not checks['action_space_match']:
        flags.append('--allow-action-space-change')
        reasons.append(f"action space 版本不同：模型 {model_action} vs 当前 {contract.get('action_space_version')} "
                       "（目标/单位槽语义变化，必须显式批准）")
    if not checks['control_mode_full']:
        reasons.append(f"control_mode={checks['control_mode']} != full，不能按当前训练入口续训")
        return _verdict(asset, 'BLOCKED', read_only_eval, False, reasons, checks)
    if not obs_action_ok:
        return _verdict(asset, 'BLOCKED', read_only_eval, False, reasons, checks)
    if flags:
        return _verdict(asset, 'RESUME_WITH_FLAGS', read_only_eval, True, reasons, checks,
                        required_flags=flags)
    reasons.append('结构/观测/动作/reward/control_mode 全部匹配，可直接续训（仍需用户批准启动训练）')
    return _verdict(asset, 'CAN_RESUME', read_only_eval, True, reasons, checks)


def _verdict(asset, verdict, read_only, can_resume, reasons, checks, required_flags=None):
    return {'id': asset['id'], 'path': asset['path'], 'role': asset.get('role'),
            'verdict': verdict, 'read_only_eval': bool(read_only),
            'can_resume_train': bool(can_resume),
            'required_flags': required_flags or [],
            'reasons': reasons, 'checks': checks}


def evaluate_registry(registry):
    contract = registry.get('contract') or common.current_contract()
    results = [evaluate_asset(a, contract) for a in registry.get('assets', [])]
    return {'generated_at': common.now_iso(), 'contract': contract, 'results': results}


def compatibility_markdown(evaluation):
    c = evaluation['contract']
    lines = ['# 模型兼容性报告（自动生成）', '',
             f"- 生成时间：{evaluation['generated_at']}",
             f"- 当前代码契约：action={c.get('action_space_version')} "
             f"ACTION_N={c.get('action_n')} obs_dim={c.get('obs_dim')} "
             f"reward={c.get('reward_version')} catalog=`{(c.get('catalog_sha256') or '')[:16]}`",
             '', '| 模型 | 结论 | 只读评估 | 可续训 | 需要的标志 | 主要原因 |',
             '|---|---|---|---|---|---|']
    primary = [r for r in evaluation['results'] if r['verdict'] != 'NOT_INSPECTED']
    shallow = [r for r in evaluation['results'] if r['verdict'] == 'NOT_INSPECTED']
    for r in primary:
        flags = r.get('required_flags') or []
        lines.append(f"| {r['id']} | **{r['verdict']}** | {r['read_only_eval']} | "
                     f"{r['can_resume_train']} | {','.join(flags) or '-'} | "
                     f"{'；'.join(r['reasons'])[:180]} |")
    lines += ['', f'> 另有 {len(shallow)} 个浅登记资产（历史 run 的 model.zip/checkpoint）：'
              '仅登记哈希与 run.json 元数据，未做深度加载检查，不给出兼容性结论。', '']
    lines += ['', '## 判定含义',
              '- `CAN_RESUME`：结构与契约匹配，可在用户批准后由现有训练入口续训；',
              '- `RESUME_WITH_FLAGS`：需显式 `--allow-reward-change` / '
              '`--allow-action-space-change` 才能续训（新旧回报/动作语义不同）；',
              '- `BLOCKED`：不得续训（受保护/结构不兼容/control_mode 不符）；',
              '- `BC_DIAGNOSTIC_ONLY`：仅离线诊断参考，不得迁移进 PPO。', '']
    return '\n'.join(lines)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--registry', default='reports/dsh-learning/model-registry.json')
    ap.add_argument('--out', default='reports/dsh-learning/model-compatibility.json')
    ap.add_argument('--md', default='reports/dsh-learning/model-compatibility.md')
    args = ap.parse_args(argv)
    registry = common.load_json(_ROOT / args.registry)
    if not registry:
        raise SystemExit(f'registry not found: {args.registry} (先运行 model_registry.py)')
    evaluation = evaluate_registry(registry)
    common.save_json_atomic(_ROOT / args.out, evaluation)
    (_ROOT / args.md).write_text(compatibility_markdown(evaluation), encoding='utf-8')
    for r in evaluation['results']:
        print(f"  {r['id']:<24} {r['verdict']:<20} read_only={r['read_only_eval']} "
              f"resume={r['can_resume_train']} flags={r['required_flags']}")
    print('wrote', args.out, 'and', args.md)


if __name__ == '__main__':
    main()
