"""建设训练 V2 训练入口：BUILD_ONLY 动作掩码 + 预部署基地 + V2 奖励。

相对 V1/R1 的变化（任务书第三/四/五/六/七节）：
- 环境 build_env_v2.RA2BuildEnvV2：301 动作空间不变，但 action_masks() 只放开
  等待 + 建筑/单位生产 + 建筑放置（BUILD_ONLY）；预部署 CY+电厂后模型才开始控制。
- 模型：复制 R1 checkpoint（不覆盖原模型），保留特征提取网络，重新初始化
  action_net（Actor 动作输出层）与 value_net（Critic 价值输出层）及优化器状态。
- 奖励：reward_build_v2（建筑 +1.0 / 单位 +0.2）。
- 预算：默认 3000 transitions / 最长 25 分钟；每 250 步 checkpoint；每 500 步评估；
  每 500 步检查：前 500 步无任何正奖励则暂停并输出诊断（不盲目跑满 25 分钟）；
  若建设/单位奖励持续改善，自动扩展至 5000~10000 步（受总步数与运行时长上限约束）。
- 交付：build_training_v2_metrics.csv / _events.jsonl / _model/ + run.json。

用法（由 run_build_v2.py 编排调用）：
  .venv-build\\Scripts\\python.exe scripts\\build_training\\train_build_v2.py \\
      --out experiments\\build_training_v2\\<run> --resume <run>\\model_init_R1copy.zip \\
      --catalog-contract <run>\\model_init_action-contract.json \\
      --steps 3000 --episode-seconds 100 --decision-seconds 0.2 \\
      --game-dir test-game-build --port 14531 --instance 8
"""
import argparse
import csv
import hashlib
import json
import os
import pathlib
import sys
import time
import traceback

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / 'scripts'
THIS_DIR = pathlib.Path(__file__).resolve().parent
for path in (str(THIS_DIR), str(SCRIPTS), str(SCRIPTS / 'dsh_train'),
             str(ROOT / 'vendor' / 'pyra2yr')):
    if path not in sys.path:
        sys.path.insert(0, path)

import numpy as np  # noqa: E402
import torch  # noqa: E402
from sb3_contrib import MaskablePPO  # noqa: E402
from stable_baselines3.common.callbacks import BaseCallback  # noqa: E402
from stable_baselines3.common.monitor import Monitor  # noqa: E402

import build_env_v2 as BE2  # noqa: E402
from dsh_rl import full_spec as S  # noqa: E402

STOP_FILE = 'STOP'

CSV_FIELDS = [
    'ts', 'timesteps', 'steps_this_run', 'elapsed_s', 'episodes_completed',
    'money', 'setup_success', 'setup_duration_s',
    'current_buildings', 'current_building_types', 'current_units', 'current_unit_types',
    'cum_buildings_awarded', 'cum_units_awarded', 'cum_building_reward',
    'cum_unit_reward', 'window_buildings', 'window_units',
    'action_noop', 'action_deploy', 'action_produce', 'action_place',
    'action_unit_control', 'window_steps',
    'loss', 'policy_gradient_loss', 'value_loss', 'entropy_loss',
    'approx_kl', 'explained_variance', 'clip_fraction',
    'param_l2_from_init', 'param_l2_since_prev',
    'mask_produce_allowed', 'mask_place_allowed', 'eval_decision',
    'unit_orders', 'deploy_orders', 'miner_orders', 'combat_orders',
    'mcv_orders', 'new_bases_cum',
]


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def save_json(path, data):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def save_model(model, path):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.stem + '.next.zip')
    model.save(temporary)
    temporary.replace(path)


def weights(model):
    return np.concatenate([p.detach().cpu().numpy().ravel()
                           for p in model.policy.parameters()])


def head_hash(module):
    return hashlib.sha256(b''.join(p.detach().cpu().numpy().tobytes()
                                   for p in module.parameters())).hexdigest()


def reinit_output_heads(model):
    """保留特征提取网络，重新初始化 Actor 动作输出层与 Critic 价值输出层。"""
    policy = model.policy
    info = {}
    for name, gain in (('action_net', np.sqrt(2.0)), ('value_net', 1.0)):
        module = getattr(policy, name)
        info[name] = {'old_sha256': head_hash(module),
                      'shape': [int(p.numel()) for p in module.parameters()]}
        for p in module.parameters():
            if p.dim() >= 2:
                torch.nn.init.orthogonal_(p, gain=gain)
            else:
                torch.nn.init.constant_(p, 0.0)
        info[name]['new_sha256'] = head_hash(module)
    # 优化器重建：输出层的旧 Adam 动量不应继续用于新初始化的头
    policy.optimizer = torch.optim.Adam(policy.parameters(), lr=model.learning_rate,
                                        eps=1e-5)
    info['optimizer_reinitialized'] = True
    info['extractor_untouched'] = True
    return info


class BuildV2Callback(BaseCallback):
    """checkpoint / 评估 / 无奖励早停 / 指标与事件落盘。"""

    def __init__(self, out, raw, config, eval_every=500, checkpoint_every=250,
                 no_reward_floor=500, max_runtime_minutes=25.0, log=None):
        super().__init__()
        self.out = pathlib.Path(out)
        self.raw = raw
        self.config = config
        self.eval_every = int(eval_every)
        self.checkpoint_every = int(checkpoint_every)
        self.no_reward_floor = int(no_reward_floor)
        self.max_runtime_minutes = float(max_runtime_minutes)
        self.log = log or (lambda msg: print(msg, flush=True))
        self.metrics_path = self.out / 'build_training_v2_metrics.csv'
        self.events_path = self.out / 'build_training_v2_events.jsonl'
        self.model_dir = self.out / 'build_training_v2_model'
        self.checkpoint_dir = self.model_dir / 'checkpoints'
        self.backup_dir = self.checkpoint_dir
        self.initial_weights = None
        self.last_weights = None
        self.cum_buildings_reward = 0.0
        self.cum_units_reward = 0.0
        self.cum_buildings = 0
        self.cum_units = 0
        self.cum_unit_orders = 0
        self.cum_deploy_orders = 0
        self.cum_miner_orders = 0
        self.cum_combat_orders = 0
        self.cum_mcv_orders = 0
        self.building_types = {}
        self.unit_types = {}
        self.window = self._blank_window()
        self.evals = []
        self.stop_requested = False
        self.stop_reason = None
        self.diagnosis = None
        self.started_at = None
        self._csv_ready = False

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _blank_window():
        return {'steps': 0, 'noop': 0, 'deploy': 0, 'produce': 0, 'place': 0,
                'unit_control': 0, 'buildings': 0, 'units': 0,
                'building_reward': 0.0, 'unit_reward': 0.0,
                'unit_orders': 0, 'deploy_orders': 0, 'miner_orders': 0,
                'combat_orders': 0, 'mcv_orders': 0}

    def _event(self, name, **data):
        rec = {'ts': time.strftime('%Y-%m-%dT%H:%M:%S'), 'event': name,
               'kind': 'training', **data}
        with self.events_path.open('a', encoding='utf-8') as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + '\n')
        self.log('[v2-event] %s %s' % (name, json.dumps(data, ensure_ascii=False)))

    def _save_checkpoint(self, tag):
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        path = self.checkpoint_dir / ('checkpoint_%s.zip' % tag)
        save_model(self.model, path)
        save_model(self.model, self.out / 'checkpoint.zip')
        self._event('checkpoint_saved', path=str(path),
                    timesteps=int(self.model.num_timesteps))

    def set_initial_weights(self, model):
        self.initial_weights = weights(model)
        self.last_weights = self.initial_weights.copy()

    # ------------------------------------------------------------------- step
    def _on_step(self):
        if self.started_at is None:
            self.started_at = time.monotonic()
        if (self.out / STOP_FILE).exists():
            self.stop_requested = True
            self.stop_reason = 'stop_file:' + (self.out / STOP_FILE).read_text(
                encoding='utf-8', errors='replace')[:120]
            self._save_checkpoint('%08d_stop' % int(self.model.num_timesteps))
            self._event('stop_requested', reason=self.stop_reason,
                        timesteps=int(self.model.num_timesteps))
            return False
        infos = self.locals.get('infos') or []
        if infos:
            self._record(infos[0])
        if self.checkpoint_every and self.n_calls % self.checkpoint_every == 0:
            self._save_checkpoint('%08d' % int(self.model.num_timesteps))
        if self.eval_every and self.n_calls % self.eval_every == 0:
            keep_going = self._evaluate()
            if not keep_going:
                return False
        if time.monotonic() - self.started_at > self.max_runtime_minutes * 60.0:
            self.stop_requested = True
            self.stop_reason = 'runtime_limit_%.0fmin' % self.max_runtime_minutes
            self._save_checkpoint('%08d_runtime' % int(self.model.num_timesteps))
            self._event('stop_requested', reason=self.stop_reason,
                        timesteps=int(self.model.num_timesteps))
            return False
        return True

    def _record(self, info):
        try:
            action = int(info.get('action'))
            parts = info.get('reward_parts') or {}
            self.window['steps'] += 1
            if action == 0:
                self.window['noop'] += 1
            elif action == 1:
                self.window['deploy'] += 1
            elif S.PRODUCE_START <= action < S.PLACE_START:
                self.window['produce'] += 1
            elif S.PLACE_START <= action < S.UNIT_START:
                self.window['place'] += 1
            else:
                self.window['unit_control'] += 1
            building_reward = float(parts.get('building_created') or 0.0)
            unit_reward = float(parts.get('unit_created') or 0.0)
            self.cum_buildings_reward += building_reward
            self.cum_units_reward += unit_reward
            self.window['building_reward'] += building_reward
            self.window['unit_reward'] += unit_reward
            if building_reward > 0:
                count = max(1, round(building_reward / max(1e-9, float(
                    self.config['rules']['building_created']['score']))))
                self.window['buildings'] += count
                self.cum_buildings += count
            if unit_reward > 0:
                count = int(round(unit_reward / max(1e-9, float(
                    self.config['rules']['unit_created']['score']))))
                self.window['units'] += count
                self.cum_units += count
            # V2.3 单位控制观察：模型实际对哪些单位下了命令
            order_action = info.get('order_action')
            if order_action in ('DEPLOY', 'STOP', 'MOVE', 'ATTACK'):
                self.window['unit_orders'] += 1
                self.cum_unit_orders += 1
                if order_action == 'DEPLOY':
                    self.window['deploy_orders'] += 1
                    self.cum_deploy_orders += 1
                roles = info.get('ordered_roles') or []
                if 'miner' in roles:
                    self.window['miner_orders'] += 1
                    self.cum_miner_orders += 1
                if 'combat' in roles:
                    self.window['combat_orders'] += 1
                    self.cum_combat_orders += 1
                if 'mcv' in roles:
                    self.window['mcv_orders'] += 1
                    self.cum_mcv_orders += 1
            for key in ('kind', 'frame', 'episode_id'):
                if key in info:
                    pass
        except Exception as exc:  # noqa: BLE001 - 诊断不得中断训练
            self._event('record_error', error=repr(exc)[:200])

    # ------------------------------------------------------------------ eval
    def _logger_values(self):
        values = {}
        try:
            for key, value in self.model.logger.name_to_value.items():
                if isinstance(value, (int, float)):
                    values[str(key)] = float(value)
        except Exception:  # noqa: BLE001
            pass
        return values

    def _evaluate(self):
        diag = self.raw.build_diagnostics() if hasattr(self.raw, 'build_diagnostics') \
            else {}
        current = weights(self.model)
        param_from_init = float(np.linalg.norm(current - self.initial_weights)) \
            if self.initial_weights is not None else None
        param_prev = float(np.linalg.norm(current - self.last_weights)) \
            if self.last_weights is not None else None
        self.last_weights = current
        elapsed = time.monotonic() - self.started_at if self.started_at else 0.0
        logger = self._logger_values()
        mask_stats = diag.get('mask_stats') or {}
        episodes = getattr(self.raw, 'completed', []) or []
        decision = 'continue'
        # 早停条件：前 500 步没有任何正奖励（建筑或单位）→ 暂停并诊断
        if (self.cum_buildings + self.cum_units) == 0 and \
                self.n_calls >= self.no_reward_floor:
            decision = 'paused_no_positive_reward'
            self.diagnosis = {
                'reason': 'no positive building/unit reward within first %d steps'
                          % self.n_calls,
                'steps': int(self.n_calls),
                'mask_stats': mask_stats,
                'ledger_counts': diag.get('counts') or {},
                'setup': diag.get('setup') or {},
                'current_buildings': diag.get('current_buildings'),
                'current_units': diag.get('current_units'),
                'current_money': diag.get('current_money'),
                'episodes_completed': len(episodes),
                'action_window': dict(self.window),
                'checks': {
                    'mask_allows_produce': (mask_stats.get('produce_allowed') or 0) > 0,
                    'mask_allows_place': (mask_stats.get('place_allowed') or 0) > 0,
                    'mask_noop': mask_stats.get('noop'),
                    'setup_success': (diag.get('setup') or {}).get('success'),
                },
            }
            save_json(self.out / 'early_stop_diagnosis.json', self.diagnosis)
            self._event('eval_decision', decision=decision,
                        timesteps=int(self.model.num_timesteps),
                        **{k: self.diagnosis[k] for k in
                           ('steps', 'current_buildings', 'current_units',
                            'episodes_completed')})
            self.stop_requested = True
            self.stop_reason = 'paused_no_positive_reward_after_%d' % self.n_calls
            self._save_checkpoint('%08d_paused' % int(self.model.num_timesteps))
            return False
        row = {
            'ts': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'timesteps': int(self.model.num_timesteps),
            'steps_this_run': int(self.n_calls),
            'elapsed_s': round(elapsed, 1),
            'episodes_completed': len(episodes),
            'money': diag.get('current_money'),
            'setup_success': (diag.get('setup') or {}).get('success'),
            'setup_duration_s': (diag.get('setup') or {}).get('duration_s'),
            'current_buildings': diag.get('current_buildings'),
            'current_building_types': json.dumps(diag.get('current_building_types') or [],
                                                 ensure_ascii=False),
            'current_units': diag.get('current_units'),
            'current_unit_types': json.dumps(diag.get('current_unit_types') or [],
                                             ensure_ascii=False),
            'cum_buildings_awarded': self.cum_buildings,
            'cum_units_awarded': self.cum_units,
            'cum_building_reward': round(self.cum_buildings_reward, 4),
            'cum_unit_reward': round(self.cum_units_reward, 4),
            'window_buildings': self.window['buildings'],
            'window_units': self.window['units'],
            'action_noop': self.window['noop'],
            'action_deploy': self.window['deploy'],
            'action_produce': self.window['produce'],
            'action_place': self.window['place'],
            'action_unit_control': self.window['unit_control'],
            'window_steps': self.window['steps'],
            'loss': logger.get('train/loss'),
            'policy_gradient_loss': logger.get('train/policy_gradient_loss'),
            'value_loss': logger.get('train/value_loss'),
            'entropy_loss': logger.get('train/entropy_loss'),
            'approx_kl': logger.get('train/approx_kl'),
            'explained_variance': logger.get('train/explained_variance'),
            'clip_fraction': logger.get('train/clip_fraction'),
            'param_l2_from_init': round(param_from_init, 6)
            if param_from_init is not None else None,
            'param_l2_since_prev': round(param_prev, 6)
            if param_prev is not None else None,
            'mask_produce_allowed': mask_stats.get('produce_allowed'),
            'mask_place_allowed': mask_stats.get('place_allowed'),
            'eval_decision': decision,
            'unit_orders': self.window['unit_orders'],
            'deploy_orders': self.window['deploy_orders'],
            'miner_orders': self.window['miner_orders'],
            'combat_orders': self.window['combat_orders'],
            'mcv_orders': self.window['mcv_orders'],
            'new_bases_cum': diag.get('new_base_count') or 0,
        }
        header = not self.metrics_path.exists()
        with self.metrics_path.open('a', newline='', encoding='utf-8') as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction='ignore')
            if header:
                writer.writeheader()
            writer.writerow(row)
        self.evals.append(row)
        self._event('eval', **{k: row[k] for k in (
            'timesteps', 'steps_this_run', 'episodes_completed',
            'current_buildings', 'current_units', 'cum_buildings_awarded',
            'cum_units_awarded', 'cum_building_reward', 'cum_unit_reward',
            'action_noop', 'action_produce', 'action_place', 'action_unit_control',
            'entropy_loss', 'explained_variance', 'param_l2_since_prev',
            'mask_produce_allowed', 'mask_place_allowed', 'eval_decision',
            'unit_orders', 'deploy_orders', 'miner_orders', 'combat_orders',
            'mcv_orders', 'new_bases_cum')})
        self.window = self._blank_window()
        return True

    # -------------------------------------------------------------- extension
    def should_extend(self):
        """建设/单位奖励持续改善时允许自动扩展训练预算。"""
        if len(self.evals) < 2:
            return False
        last, prev = self.evals[-1], self.evals[-2]
        last_events = (last.get('window_buildings') or 0) + (last.get('window_units') or 0)
        prev_events = (prev.get('window_buildings') or 0) + (prev.get('window_units') or 0)
        improving = last_events > 0 and (last_events >= prev_events)
        productive = (last.get('cum_buildings_awarded') or 0) + \
            (last.get('cum_units_awarded') or 0) > 0
        return bool(improving and productive)


def episode_summary(result):
    metrics = dict(result.get('metrics') or {})
    return {
        'episode_id': result.get('episode_id'),
        'kind': result.get('kind'),
        'is_crash': result.get('is_crash'),
        'reward_total': result.get('reward_total'),
        'reward_parts': result.get('reward_parts'),
        'elapsed_s': result.get('elapsed_s'),
        'rows': result.get('rows'),
        'setup_success': metrics.get('setup_success'),
        'setup_duration_s': metrics.get('setup_duration_s'),
        'buildings_awarded': metrics.get('buildings_awarded'),
        'units_awarded': metrics.get('units_awarded'),
        'building_types_awarded': metrics.get('building_types_awarded'),
        'unit_types_awarded': metrics.get('unit_types_awarded'),
        'build_reward_total': metrics.get('build_reward_total'),
        'failure': result.get('failure'),
    }


def merge_events(out):
    """把 setup / build / training 三类事件合并为交付用 events.jsonl。"""
    target = out / 'build_training_v2_events.jsonl'
    sources = [('setup', out / 'setup-events.jsonl'),
               ('reward', out / 'build-events.jsonl'),
               ('training', out / 'build_training_v2_events.jsonl')]
    lines = []
    for source, path in sources:
        if not path.exists():
            continue
        for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            rec.setdefault('source', source)
            lines.append(rec)
    # 第二个源 training 与目标同名，先读取后覆盖
    merged_path = out / 'build_training_v2_events_merged.jsonl'
    with merged_path.open('w', encoding='utf-8') as fh:
        for rec in lines:
            fh.write(json.dumps(rec, ensure_ascii=False) + '\n')
    return merged_path, len(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', required=True)
    ap.add_argument('--resume', required=True, help='R1 model copy in run dir')
    ap.add_argument('--catalog-contract', required=True)
    ap.add_argument('--steps', type=int, default=3000)
    ap.add_argument('--max-extend-steps', type=int, default=10000)
    ap.add_argument('--extend-chunk', type=int, default=2000)
    ap.add_argument('--episode-seconds', type=float, default=100.0)
    ap.add_argument('--decision-seconds', type=float, default=0.2)
    ap.add_argument('--seed', type=int, default=11)
    ap.add_argument('--game-dir', default='test-game-build')
    ap.add_argument('--port', type=int, default=14531)
    ap.add_argument('--instance', type=int, default=8)
    ap.add_argument('--checkpoint-every', type=int, default=250)
    ap.add_argument('--eval-every', type=int, default=500)
    ap.add_argument('--max-runtime-minutes', type=float, default=25.0)
    ap.add_argument('--reward-config', default=None,
                    help='奖励配置路径（默认 build_env_v2 的 V2.1 配置）')
    ap.add_argument('--no-curriculum-masks', dest='curriculum_masks', action='store_false',
                    default=True,
                    help='关闭 V2.2 电力修复/奖励上限课程掩码（消融实验用）')
    ap.add_argument('--allow-unit-control', action='store_true',
                    help='V2.3：开放 DEPLOY/STOP/MOVE 单位控制，观察控制对象并测试 MCV 展开新基地')
    ap.add_argument('--unit-control-mode', default=None,
                    choices=('off', 'full', 'mcv_only'),
                    help='V2.4 单位控制课程：off=BULD_ONLY, full=全部控制, mcv_only=仅 MCV 控制/展开')
    ap.add_argument('--focus-mcv-until-new-base', action='store_true',
                    help='V2.8：MCV 存在且尚未建新基地时只开放 MCV 动作（屏蔽建设）')
    ap.add_argument('--mcv-clear-deploy-only', action='store_true',
                    help='V2.8：DEPLOY 仅当 MCV 周围半径 3 格清空时可用（强制先移动到空地）')
    ap.add_argument('--mcv-production-focus', action='store_true',
                    help='V2.8b：战车工厂建成后只允许生产 MCV（让基地车更早出现）')
    ap.add_argument('--focus-mcv-mask-noop', action='store_true',
                    help='V2.8c：MCV 焦点模式下同时屏蔽等待动作，强制模型操作 MCV')
    ap.add_argument('--no-reinit-heads', dest='reinit_heads', action='store_false',
                    default=True,
                    help='续训模式：保留 checkpoint 中已学习的 Actor/Critic 输出层与优化器状态')
    a = ap.parse_args()

    out = pathlib.Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    (out / 'trainer_pid.txt').write_text(str(os.getpid()), encoding='utf-8')
    if (out / 'build_training_v2_model' / 'model.zip').exists():
        raise SystemExit('refusing to overwrite existing V2 model in %s' % out)
    report = {
        'status': 'starting',
        'mode': 'build_training_v2_phase1_predeployed',
        'parameters': vars(a),
        'reward_version': BE2.RA2BuildEnvV2.reward_version,
        'reward_config': str(BE2.CONFIG_PATH),
        'action_count': S.ACTION_N,
        'build_only_mask': (a.unit_control_mode or
                            ('full' if a.allow_unit_control else 'off')) == 'off',
        'unit_control_enabled': bool(a.allow_unit_control or a.unit_control_mode),
        'unit_control_mode': a.unit_control_mode or
        ('full' if a.allow_unit_control else 'off'),
        'started_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'game_dir': a.game_dir, 'bridge_port': a.port, 'instance': a.instance,
    }
    env = None
    raw = None
    model = None
    callback = None
    try:
        resume = pathlib.Path(a.resume).resolve()
        contract_path = pathlib.Path(a.catalog_contract).resolve()
        contract = json.loads(contract_path.read_text(encoding='utf-8'))
        if int(contract.get('action_n', 0)) != S.ACTION_N:
            raise SystemExit('action contract mismatch: %s vs local %s'
                             % (contract.get('action_n'), S.ACTION_N))
        if contract.get('version') != S.VERSION:
            raise SystemExit('action contract version mismatch')
        expected_version = contract.get('action_space_version') or S.ACTION_SPACE_VERSION
        if expected_version not in S.ACTION_SPACE_VERSIONS:
            raise SystemExit('unsupported action_space_version %r' % expected_version)
        os.environ['DSH_ACTION_SPACE_VERSION'] = expected_version
        report['checkpoint'] = {
            'path': str(resume), 'sha256': sha256(resume),
            'catalog_sha256_expected': contract.get('catalog_sha256'),
            'action_space_version': expected_version,
            'production_types_expected': len(contract.get('production_types') or []),
        }
        save_json(out / 'run.json', report)

        raw = BE2.RA2BuildEnvV2(
            out / 'episodes', max_seconds=a.episode_seconds,
            decision_seconds=a.decision_seconds,
            game_dir=str((ROOT / a.game_dir).resolve()
                         if not pathlib.Path(a.game_dir).is_absolute() else a.game_dir),
            port=a.port, instance=a.instance, kill_stale=True,
            reward_config_path=(str((ROOT / a.reward_config).resolve())
                                if a.reward_config else None),
            power_curriculum_masks=a.curriculum_masks,
            cap_curriculum_masks=a.curriculum_masks,
            allow_unit_control=a.allow_unit_control,
            unit_control_mode=a.unit_control_mode,
            focus_mcv_until_new_base=a.focus_mcv_until_new_base,
            mcv_clear_deploy_only=a.mcv_clear_deploy_only,
            mcv_production_focus=a.mcv_production_focus,
            focus_mcv_mask_noop=a.focus_mcv_mask_noop)
        raw.expected_catalog_hash = contract.get('catalog_sha256')
        env = Monitor(raw, str(out / 'monitor.csv'))
        resume_sha = sha256(resume)
        model = MaskablePPO.load(str(resume), env=env, device='cpu')
        if a.reinit_heads:
            head_info = reinit_output_heads(model)
        else:
            head_info = {'skipped': True,
                         'reason': 'continue training from checkpoint (--no-reinit-heads)'}
        report['model'] = {
            'n_steps': int(model.n_steps), 'batch_size': int(model.batch_size),
            'n_epochs': int(model.n_epochs), 'learning_rate': float(model.learning_rate),
            'ent_coef': float(model.ent_coef),
            'policy': type(model.policy).__name__,
            'policy_parameters': int(sum(p.numel() for p in model.policy.parameters())),
            'num_timesteps_loaded': int(model.num_timesteps),
            'resumed_from': {'path': str(resume), 'sha256': resume_sha},
            'head_reinit': head_info,
            'action_mask': 'BUILD_ONLY: noop + PRODUCE(2..97) + PLACE(98..129)',
        }
        steps_before = int(model.num_timesteps)
        updates_before = int(model._n_updates)
        report.update({'status': 'collecting_real_rollouts',
                       'timesteps_before': steps_before,
                       'updates_before': updates_before})
        save_json(out / 'run.json', report)

        callback = BuildV2Callback(
            out, raw, raw.build_reward_config, eval_every=a.eval_every,
            checkpoint_every=a.checkpoint_every, no_reward_floor=a.eval_every,
            max_runtime_minutes=a.max_runtime_minutes)
        callback.set_initial_weights(model)
        callback._event('train_start', steps_before=steps_before,
                        budget=a.steps, episode_seconds=a.episode_seconds,
                        decision_seconds=a.decision_seconds,
                        checkpoint_every=a.checkpoint_every,
                        eval_every=a.eval_every,
                        build_only_mask=True)

        report['reward_config'] = str(raw.reward_config_path)
        report['reward_revision'] = raw.build_reward_revision
        save_json(out / 'run.json', report)

        budget = int(a.steps)
        extended = 0
        t0 = time.monotonic()
        while True:
            done = int(model.num_timesteps) - steps_before
            if done >= budget:
                if extended < 2 and callback.should_extend() and \
                        budget < a.max_extend_steps and \
                        time.monotonic() - t0 < a.max_runtime_minutes * 60.0:
                    new_budget = min(a.max_extend_steps, budget + a.extend_chunk)
                    callback._event('budget_extended', old=budget, new=new_budget,
                                    reason='building/unit rewards improving',
                                    timesteps=int(model.num_timesteps))
                    budget = new_budget
                    extended += 1
                else:
                    break
            chunk = min(a.eval_every, budget - done)
            if chunk <= 0:
                break
            model.learn(total_timesteps=int(chunk), callback=callback,
                        reset_num_timesteps=False)
            if callback.stop_requested:
                break
            if time.monotonic() - t0 > a.max_runtime_minutes * 60.0:
                callback.stop_reason = 'runtime_limit_%.0fmin' % a.max_runtime_minutes
                callback._save_checkpoint('%08d_runtime' % int(model.num_timesteps))
                callback._event('stop_requested', reason=callback.stop_reason,
                                timesteps=int(model.num_timesteps))
                break

        # ---------------------------------------------------------- finalize
        model_dir = out / 'build_training_v2_model'
        model_dir.mkdir(parents=True, exist_ok=True)
        if callback.evals:
            save_model(model, model_dir / 'checkpoint_latest.zip')
        save_model(model, model_dir / 'model.zip')
        reloaded = MaskablePPO.load(str(model_dir / 'model.zip'), device='cpu')
        after = weights(model)
        round_trip = bool(np.array_equal(after, weights(reloaded)))
        delta = float(np.linalg.norm(after - callback.initial_weights))
        episodes = [episode_summary(r) for r in (getattr(raw, 'completed', []) or [])]
        final_diag = raw.build_diagnostics() if hasattr(raw, 'build_diagnostics') else {}
        merged_path, merged_count = merge_events(out)
        # 交付命名：metrics 已直接写到 build_training_v2_metrics.csv
        if merged_path.exists():
            merged_path.replace(out / 'build_training_v2_events.jsonl')
        events_count = merged_count
        status = 'complete'
        if callback.stop_requested:
            status = ('paused_no_positive_reward'
                      if callback.stop_reason.startswith('paused_no_positive_reward')
                      else 'stopped_by_request')
        report.update({
            'status': status,
            'stop_reason': callback.stop_reason,
            'budget_initial': int(a.steps),
            'budget_final': int(budget),
            'extended_rounds': extended,
            'timesteps_after': int(model.num_timesteps),
            'real_transitions_this_run': int(model.num_timesteps - steps_before),
            'updates_after': int(model._n_updates),
            'optimizer_updates_this_run': int(model._n_updates - updates_before),
            'parameter_l2_change_from_init': delta,
            'parameters_updated': delta > 0,
            'save_reload_weights_identical': round_trip,
            'model_sha256': sha256(model_dir / 'model.zip'),
            'episodes': episodes,
            'episode_count': len(episodes),
            'cumulative_rewards': {
                'building_events': callback.cum_buildings,
                'unit_events': callback.cum_units,
                'building_reward': round(callback.cum_buildings_reward, 4),
                'unit_reward': round(callback.cum_units_reward, 4),
            },
            'unit_control': {
                'enabled': bool(a.allow_unit_control),
                'unit_orders': callback.cum_unit_orders,
                'deploy_orders': callback.cum_deploy_orders,
                'miner_orders': callback.cum_miner_orders,
                'combat_orders': callback.cum_combat_orders,
                'mcv_orders': callback.cum_mcv_orders,
                'order_stats': final_diag.get('order_stats'),
                'new_bases_created': final_diag.get('new_base_count'),
                'new_base_events': final_diag.get('new_base_events'),
            },
            'evals': callback.evals,
            'diagnosis': callback.diagnosis,
            'events_file': str(out / 'build_training_v2_events.jsonl'),
            'events_count': events_count,
            'metrics_file': str(callback.metrics_path),
            'model_dir': str(model_dir),
            'compatibility': {
                'static_precheck': 'PASS',
                'catalog_matched_at_episode_start': True,
                'action_space_version': expected_version,
                'action_count': S.ACTION_N,
                'observation_dim': int(env.observation_space.shape[0]),
            },
            'finished_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
        })
        # 「是否真正学会建设」的判定（不只看奖励总量）
        evals = callback.evals or []
        building_evals = sum(1 for e in evals if (e.get('window_buildings') or 0) > 0)
        unit_evals = sum(1 for e in evals if (e.get('window_units') or 0) > 0)
        positive_windows = building_evals + unit_evals
        report['verdict'] = {
            'checkpoint_compatible': True,
            'reward_entered_training': bool(
                model._n_updates > updates_before and delta > 0),
            'model_produced_buildings': callback.cum_buildings > 0,
            'model_produced_units': callback.cum_units > 0,
            'building_reward_events': callback.cum_buildings,
            'unit_reward_events': callback.cum_units,
            'eval_windows_with_building_rewards': building_evals,
            'eval_windows_with_unit_rewards': unit_evals,
            'evaluation_windows': len(evals),
            'learned_building_behavior': bool(
                callback.cum_buildings >= 3 and building_evals >= 2),
            'learned_unit_production': bool(callback.cum_units >= 3 and unit_evals >= 2),
            'extension_condition_met': bool(callback.should_extend()),
            'extension_applied_rounds': extended,
            'unit_control_enabled': bool(a.allow_unit_control),
            'unit_control_orders': callback.cum_unit_orders,
            'deploy_orders': callback.cum_deploy_orders,
            'controlled_miner_orders': callback.cum_miner_orders,
            'controlled_combat_orders': callback.cum_combat_orders,
            'controlled_mcv_orders': callback.cum_mcv_orders,
            'new_bases_created': final_diag.get('new_base_count') or 0,
            'mcv_deployed_new_base': bool((final_diag.get('new_base_count') or 0) > 0),
        }
        save_json(out / 'run.json', report)
        print(json.dumps({k: report.get(k) for k in (
            'status', 'stop_reason', 'budget_final', 'extended_rounds',
            'real_transitions_this_run', 'optimizer_updates_this_run',
            'parameter_l2_change_from_init', 'cumulative_rewards', 'verdict')},
            ensure_ascii=False, indent=2), flush=True)
        return 0
    except BaseException as exc:  # noqa: BLE001
        # 异常退出（游戏/桥接崩溃等）也要保存 checkpoint 与故障信息，便于续训与审计。
        report.update({'status': 'failed',
                       'error': '%s: %s' % (type(exc).__name__, exc),
                       'traceback': traceback.format_exc()[-5000:]})
        try:
            if model is not None:
                crash_ckpt_dir = out / 'build_training_v2_model'
                crash_ckpt_dir.mkdir(parents=True, exist_ok=True)
                save_model(model, crash_ckpt_dir / 'checkpoint_crash.zip')
                report['crash_checkpoint'] = str(crash_ckpt_dir / 'checkpoint_crash.zip')
            if callback is not None:
                report['last_eval'] = callback.evals[-1] if callback.evals else None
                report['cumulative_rewards'] = {
                    'building_events': callback.cum_buildings,
                    'unit_events': callback.cum_units,
                    'building_reward': round(callback.cum_buildings_reward, 4),
                    'unit_reward': round(callback.cum_units_reward, 4)}
                report['evals'] = callback.evals
                report['timesteps_after'] = int(getattr(model, 'num_timesteps', 0))
        except BaseException:  # noqa: BLE001
            pass
        try:
            save_json(out / 'run.json', report)
        except Exception:  # noqa: BLE001
            pass
        raise
    finally:
        if env is not None:
            try:
                env.close()
            except BaseException:  # noqa: BLE001
                pass
        try:
            report['finished_at'] = time.strftime('%Y-%m-%dT%H:%M:%S')
            save_json(out / 'run.json', report)
        except Exception:  # noqa: BLE001
            pass


if __name__ == '__main__':
    sys.exit(main())
