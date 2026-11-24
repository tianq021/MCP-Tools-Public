"""V1 统一模型注册表：登记 RA2MCP 现有模型资产的来源、哈希、结构与兼容性。

只读：仅读取模型/元数据文件；不训练、不启动游戏、不修改模型。
深度检查会调用 sb3_contrib.MaskablePPO.load（无 env 只读加载）做结构核验，
运行环境需：conda python + PYTHONPATH=.venv-rl/Lib/site-packages。

用法：
  PYTHONPATH=scripts python scripts/dsh_learning/model_registry.py \
      --out reports/dsh-learning/model-registry.json --deep
"""
import argparse
import pathlib
import sys
import zipfile

_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / 'scripts'))
from dsh_learning import common  # noqa: E402

# 任务书要求逐个检查的资产（顺序即报告顺序）。
ASSET_SPECS = [
    {'id': 'R1', 'kind': 'ppo', 'path': 'reports/rl/stage-A-round1-20261030-153225/model.zip',
     'role': 'best_current_candidate', 'protected': True, 'do_not_resume': False},
    {'id': 'R2', 'kind': 'ppo', 'path': 'reports/rl/stage-A-round2-20261030-155824/model.zip',
     'role': 'secondary_reference', 'protected': True, 'do_not_resume': False},
    {'id': 'R3', 'kind': 'ppo', 'path': 'reports/rl/stage-A-round3-20261030-162536/model.zip',
     'role': 'regression_diagnostic_only', 'protected': True, 'do_not_resume': True},
    {'id': 'OLD_REFERENCE', 'kind': 'ppo',
     'path': 'reports/rl/ppo-shared-20261030-102633/model.zip',
     'role': 'historical_baseline', 'protected': True, 'do_not_resume': False},
    {'id': 'RUN_203156', 'kind': 'ppo',
     'path': 'reports/rl/ppo-shared-20261030-203156/model.zip',
     'role': 'post_diagnosis_v4_run_16workers', 'protected': False, 'do_not_resume': False},
    {'id': 'RUN_210239_CHECKPOINT', 'kind': 'ppo',
     'path': 'reports/rl/ppo-shared-20261030-210239/checkpoint.zip',
     'role': 'survival_reward_v1_checkpoint', 'protected': False, 'do_not_resume': False},
    {'id': 'RUN_210239_EMERGENCY', 'kind': 'ppo',
     'path': 'reports/rl/ppo-shared-20261030-210239/emergency.zip',
     'role': 'survival_reward_v1_unclean_stop_snapshot', 'protected': False,
     'do_not_resume': False},
    {'id': 'BC_ACTION_MODEL', 'kind': 'bc',
     'path': 'reports/teacher-collect/20261030-090218/play-bc-091929/bc_action_model.pt',
     'role': 'bc_diagnostic_only', 'protected': False, 'do_not_resume': True},
]


def discover_run_metadata(model_path):
    """读取模型所在 run 的 run.json / action-contract.json（只读）。"""
    run_dir = pathlib.Path(model_path).parent
    meta = {'run_dir': common.rel(run_dir)}
    run = common.load_json(run_dir / 'run.json')
    if run:
        keep = ('status', 'control_mode', 'reward_version', 'reward_changed_from',
                'reward_config_path', 'reward_config_sha256', 'reward_config_revision',
                'action_space_version', 'action_changed_from', 'target_candidate_range',
                'target_nearest_k', 'model_init', 'resume_model', 'timesteps_before',
                'timesteps_after', 'optimizer_epochs', 'completed_episodes',
                'policy_parameters', 'batch_size', 'device', 'combat_stage',
                'shared_policy')
        meta['run_json'] = {k: run.get(k) for k in keep if k in run}
        params = run.get('parameters') or {}
        meta['run_parameters'] = {k: params.get(k) for k in
                                  ('workers', 'episode_seconds', 'decision_seconds',
                                   'max_episodes', 'episode_jitter_seconds',
                                   'launch_stagger_seconds') if k in params}
        meta['source_sha256'] = run.get('source_sha256')
    contract = common.load_json(run_dir / 'action-contract.json')
    if contract:
        meta['action_contract'] = {k: contract.get(k) for k in
                                   ('version', 'action_space_version', 'action_n',
                                    'catalog_sha256', 'target_candidate_range',
                                    'target_nearest_k', 'unit_slots_stable')
                                   if k in contract}
    return meta


def _zip_members(path):
    try:
        with zipfile.ZipFile(path) as zf:
            return zf.namelist()
    except zipfile.BadZipFile:
        return []


def inspect_ppo(path):
    """SB3 MaskablePPO 深度检查：结构、参数、优化器状态、只读推理。"""
    out = {'format': 'sb3_zip', 'zip_members': _zip_members(path)}
    out['has_policy_pth'] = 'policy.pth' in out['zip_members']
    out['has_optimizer_pth'] = 'policy.optimizer.pth' in out['zip_members']
    out['has_data_json'] = 'data' in out['zip_members']
    out['has_pytorch_variables'] = 'pytorch_variables.pth' in out['zip_members']
    try:
        import numpy as np
        from sb3_contrib import MaskablePPO
        model = MaskablePPO.load(path, device='cpu')
        policy = model.policy
        obs_space = getattr(policy, 'observation_space', None)
        act_space = getattr(policy, 'action_space', None)
        out.update(loadable=True,
                   algorithm='MaskablePPO',
                   policy_class=type(policy).__name__,
                   obs_dim=int(obs_space.shape[0]) if obs_space is not None else None,
                   action_n=int(act_space.n) if act_space is not None else None,
                   num_timesteps=int(model.num_timesteps),
                   n_updates=int(model._n_updates),
                   parameters=int(sum(p.numel() for p in policy.parameters())))
        # 结构：MLP 隐层
        mlp = getattr(policy, 'mlp_extractor', None)
        arch = []
        if mlp is not None:
            net = getattr(mlp, 'policy_net', None)
            if net is not None:
                for layer in net:
                    if hasattr(layer, 'out_features'):
                        arch.append(int(layer.out_features))
        out['net_arch'] = arch
        # 优化器状态
        optim = getattr(policy, 'optimizer', None)
        out['optimizer_loaded'] = bool(optim is not None and
                                       getattr(optim, 'state', None))
        # 只读推理（全零观测 + 全合法 mask）
        try:
            obs = np.zeros(obs_space.shape, dtype=np.float32)
            masks = np.ones((1, int(act_space.n)), dtype=bool)
            action, _ = model.predict(obs, action_masks=masks, deterministic=True)
            out['inference_ok'] = True
            out['inference_action'] = int(action)
        except Exception as exc:  # noqa: BLE001
            out['inference_ok'] = False
            out['inference_error'] = repr(exc)
    except Exception as exc:  # noqa: BLE001
        out['loadable'] = False
        out['load_error'] = repr(exc)
    return out


def inspect_bc(path):
    """BC（行为克隆）权重：torch state_dict 结构检查（非 PPO，不做续训）。"""
    out = {'format': 'torch_state_dict'}
    try:
        import torch
    except Exception as exc:  # noqa: BLE001
        out['loadable'] = False
        out['error'] = f'torch unavailable: {exc!r}'
        return out
    try:
        state = torch.load(path, map_location='cpu', weights_only=True)
    except Exception:
        state = torch.load(path, map_location='cpu', weights_only=False)
    layers = []
    params = 0
    for name, tensor in state.items():
        if hasattr(tensor, 'shape') and len(tensor.shape) == 2:
            layers.append({'name': name, 'shape': [int(x) for x in tensor.shape]})
            params += int(tensor.numel())
        elif hasattr(tensor, 'numel'):
            params += int(tensor.numel())
    linear = [l for l in layers if 'weight' in l['name']]
    out.update(loadable=True, parameter_like_entries=len(state), parameters=params,
               linear_shapes=linear)
    if linear:
        out['obs_dim'] = linear[0]['shape'][1]
        out['action_n'] = linear[-1]['shape'][0]
    out['role_note'] = ('BC 权重仅用于离线诊断/行为参考；按任务书不得强行迁移到 PPO。')
    return out


def build_registry(deep=False, include_checkpoints=False, extra_discovery=True):
    contract = common.current_contract()
    assets = []
    seen = set()
    for spec in ASSET_SPECS:
        record = dict(spec)
        record['file'] = common.file_info(_ROOT / spec['path'])
        if not record['file']['exists']:
            record['status'] = 'MISSING'
            assets.append(record)
            continue
        record['run'] = discover_run_metadata(_ROOT / spec['path'])
        if deep:
            inspected = (inspect_ppo if spec['kind'] == 'ppo' else inspect_bc)(
                _ROOT / spec['path'])
            record['inspection'] = inspected
        assets.append(record)
        seen.add(record['file']['path'])

    if extra_discovery:
        extras = []
        for model in sorted(common.RL_DIR.glob('*/model.zip')):
            rel = common.rel(model)
            if rel in seen:
                continue
            extras.append({'id': model.parent.name, 'kind': 'ppo', 'path': rel,
                           'role': 'discovered_extra', 'protected': False,
                           'do_not_resume': 'round3' in rel,
                           'inspection_status': 'SHALLOW_INVENTORY_ONLY',
                           'note': '浅登记（哈希+run.json 元数据）；未做深度加载检查，'
                                   '如需兼容性结论请加入 ASSET_SPECS 后 --deep 重新生成',
                           'file': common.file_info(model),
                           'run': discover_run_metadata(model)})
        if include_checkpoints:
            for ckpt in sorted(common.RL_DIR.glob('*/checkpoint*.zip')):
                rel = common.rel(ckpt)
                if rel in seen:
                    continue
                extras.append({'id': ckpt.name, 'kind': 'ppo', 'path': rel,
                               'role': 'discovered_checkpoint', 'protected': False,
                               'do_not_resume': 'round3' in rel,
                               'inspection_status': 'SHALLOW_INVENTORY_ONLY',
                               'file': common.file_info(ckpt),
                               'run': discover_run_metadata(ckpt)})
        assets.extend(extras)

    return {'generated_at': common.now_iso(), 'root': str(common.ROOT),
            'contract': contract, 'assets': assets,
            'primary_assets': [a['id'] for a in ASSET_SPECS],
            'shallow_inventory_count': len(assets) - len(ASSET_SPECS),
            'task': 'RA2MCP autonomous-learning V1 model registry (offline, read-only)'}


def registry_markdown(registry):
    lines = ['# 模型资产登记表（自动生成）', '',
             f"- 生成时间：{registry['generated_at']}",
             f"- 当前契约：action={registry['contract'].get('action_space_version')} "
             f"ACTION_N={registry['contract'].get('action_n')} "
             f"obs_dim={registry['contract'].get('obs_dim')} "
             f"reward={registry['contract'].get('reward_version')}",
             '', '| ID | 角色 | 文件 | 大小 | SHA256 | 结构 | 可加载 | 可续训 |',
             '|---|---|---|---|---|---|---|---|']
    for a in registry['assets']:
        f = a.get('file') or {}
        ins = a.get('inspection') or {}
        sha = (f.get('sha256') or '')[:16]
        size = f.get('size')
        struct = ''
        if ins:
            if a['kind'] == 'ppo':
                struct = (f"MaskablePPO obs={ins.get('obs_dim')} n={ins.get('action_n')} "
                          f"params={ins.get('parameters')} arch={ins.get('net_arch')}")
            else:
                struct = f"BC MLP obs={ins.get('obs_dim')} n={ins.get('action_n')}"
        lines.append(f"| {a['id']} | {a.get('role')} | `{a.get('path')}` | "
                     f"{size if size is not None else '-'} | `{sha}` | {struct} | "
                     f"{ins.get('loadable', 'not_checked')} | "
                     f"{'BLOCKED' if a.get('do_not_resume') else (a.get('run', {}).get('run_json', {}).get('control_mode') or '?')} |")
    return '\n'.join(lines) + '\n'


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', default='reports/dsh-learning/model-registry.json')
    ap.add_argument('--md', default='reports/dsh-learning/model-registry.md')
    ap.add_argument('--deep', action='store_true', help='读取模型结构（需要 torch/sb3）')
    ap.add_argument('--include-checkpoints', action='store_true')
    args = ap.parse_args(argv)
    registry = build_registry(deep=args.deep,
                              include_checkpoints=args.include_checkpoints)
    common.save_json_atomic(_ROOT / args.out, registry)
    md = registry_markdown(registry)
    (_ROOT / args.md).write_text(md, encoding='utf-8')
    print(f"[registry] assets={len(registry['assets'])} deep={args.deep}")
    for a in registry['assets']:
        ins = a.get('inspection') or {}
        print(f"  {a['id']:<24} {a['kind']:<3} loadable={ins.get('loadable')} "
              f"obs={ins.get('obs_dim')} n={ins.get('action_n')}")
    print('wrote', args.out, 'and', args.md)


if __name__ == '__main__':
    main()
