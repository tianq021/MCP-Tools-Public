"""无敌人建设训练 V1 训练入口（单 Worker / 单游戏实例 / 独立建设奖励）。

与旧训练入口的区别：
- 环境：build_env.RA2BuildEnv（RA2FullEnv + 独立 building_created 奖励；V4 战斗奖励禁用）
- 奖励：training/build_curriculum/reward_build_v1.json（每座新的己方已放置建筑 +1.0）
- 不覆盖任何旧模型/checkpoint：从实验目录里的 R1 副本继续训练，产物全部写到 --out
- 兼容性检查：加载的 checkpoint 的 action-contract（301 动作 / 1801 观测 / 生产目录哈希）
  必须与本机游戏一致，否则直接失败并报告，不静默续训。

用法（由 run_build_experiment.py 编排调用）：
  .venv-build\\Scripts\\python.exe scripts\\build_training\\train_build_v1.py \\
      --out experiments\\build_training_v1\\<run> --resume <run>\\model_init_R1copy.zip \\
      --catalog-contract <run>\\model_init_action-contract.json \\
      --steps 320 --episode-seconds 75 --decision-seconds 0.5 \\
      --game-dir test-game-build --port 14531 --instance 9
"""
import argparse
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
from stable_baselines3.common.callbacks import BaseCallback  # noqa: E402
from stable_baselines3.common.monitor import Monitor  # noqa: E402
from sb3_contrib import MaskablePPO  # noqa: E402

import build_env as BE  # noqa: E402
from dsh_rl import full_spec as S  # noqa: E402

STOP_FILE = 'STOP'


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def save_json(path, data):
    path = pathlib.Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def save_model(model, path):
    path = pathlib.Path(path)
    temporary = path.with_name(path.stem + '.next.zip')
    model.save(temporary)
    temporary.replace(path)


def weights(model):
    return np.concatenate([p.detach().cpu().numpy().ravel()
                           for p in model.policy.parameters()])


class BuildEvidenceCallback(BaseCallback):
    """检查 STOP（监控器/人工）→ 保存 checkpoint → 写进度 JSON。"""

    def __init__(self, out, raw):
        super().__init__()
        self.out = pathlib.Path(out)
        self.raw = raw
        self.requested_stop = False
        self._last_saved_updates = 0

    def _on_step(self):
        raw = self.training_env.envs[0].unwrapped if hasattr(self.training_env, 'envs') else self.raw
        if (self.out / STOP_FILE).exists() and not self.requested_stop:
            self.requested_stop = True
            save_model(self.model, self.out / 'checkpoint.zip')
            print('[train] STOP detected (%s); graceful stop requested'
                  % (self.out / STOP_FILE).read_text(encoding='utf-8', errors='replace')[:200],
                  flush=True)
            return False
        if self.n_calls % 16 == 0:
            self._write_progress(raw)
        if self.n_calls % 32 == 0:
            save_model(self.model, self.out / 'checkpoint.zip')
        return True

    def _write_progress(self, raw):
        try:
            progress = {
                'timesteps': int(self.model.num_timesteps),
                'updates': int(self.model._n_updates),
                'n_calls': int(self.n_calls),
                'total_reward': round(float(getattr(raw, 'total_reward', 0.0)), 4),
                'episode_reward_parts': dict(getattr(raw, 'episode_reward_parts', {})),
                'completed_episodes': len(getattr(raw, 'completed', []) or []),
                'episode_index': int(getattr(raw, 'episode_count', 0)),
                'transitions': int(getattr(raw, 'transitions', 0)),
                'build': raw.build_diagnostics() if hasattr(raw, 'build_diagnostics') else {},
                'game_pid': getattr(raw, 'game_pid', None),
                'stopped_by_request': bool(self.requested_stop),
                'updated_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
            }
            save_json(self.out / 'progress.json', progress)
        except Exception:  # noqa: BLE001 - 诊断不得中断训练
            pass


def episode_summary(result):
    """把 env._finish 的 result 精简为报告用字段。"""
    reward_parts = dict(result.get('reward_parts') or {})
    metrics = dict(result.get('metrics') or {})
    return {
        'episode_id': result.get('episode_id'),
        'kind': result.get('kind'),
        'is_crash': result.get('is_crash'),
        'learning_excluded': result.get('learning_excluded'),
        'reward_total': result.get('reward_total'),
        'reward_parts': reward_parts,
        'build_reward_total': metrics.get('build_reward_total'),
        'buildings_created_awarded': metrics.get('buildings_created_awarded'),
        'buildings_via_mcv_deploy': metrics.get('buildings_via_mcv_deploy'),
        'buildings_via_production': metrics.get('buildings_via_production'),
        'elapsed_s': result.get('elapsed_s'),
        'rows': result.get('rows'),
        'failure': result.get('failure'),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', required=True, help='new experiment run directory')
    ap.add_argument('--resume', required=True, help='R1 model copy inside the run directory')
    ap.add_argument('--catalog-contract', required=True,
                    help='R1 action-contract.json copy inside the run directory')
    ap.add_argument('--steps', type=int, default=320, help='new transitions to collect')
    ap.add_argument('--episode-seconds', type=float, default=75.0)
    ap.add_argument('--decision-seconds', type=float, default=0.5)
    ap.add_argument('--game-dir', default='test-game-build')
    ap.add_argument('--port', type=int, default=14531)
    ap.add_argument('--instance', type=int, default=9)
    ap.add_argument('--seed', type=int, default=11)
    ap.add_argument('--learning-rate', type=float, default=None,
                    help='optional override; default = keep checkpoint setting')
    ap.add_argument('--ent-coef', type=float, default=None)
    a = ap.parse_args()

    out = pathlib.Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    # uv/venv 启动器会 re-exec 出新的 python 进程，上层 Popen.pid 不等于真实训练进程 pid；
    # 这里写出真实 pid 供监控器精确追踪（Worker/游戏进程分类依赖它）。
    (out / 'trainer_pid.txt').write_text(str(os.getpid()), encoding='utf-8')
    if (out / 'model.zip').exists():
        raise SystemExit('refusing to overwrite an existing model.zip in %s' % out)
    report = {
        'status': 'starting',
        'mode': 'build_reward_v1_short_training',
        'parameters': vars(a),
        'reward_version': BE.RA2BuildEnv.reward_version,
        'reward_config': str(BE.CONFIG_PATH),
        'action_count': S.ACTION_N,
        'obs_dim': int(S.EXTRA_DIM + S.HISTORY_DIM),
        'game_dir': a.game_dir,
        'bridge_port': a.port,
        'instance': a.instance,
        'algorithm': 'MaskablePPO',
        'device': 'cpu',
        'started_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'source': 'real isolated RA2 game only (no enemies map config A1)',
    }

    env = None
    raw = None
    try:
        # ---- checkpoint 兼容性预检（不启动游戏即可判定的部分）----
        resume = pathlib.Path(a.resume).resolve()
        contract_path = pathlib.Path(a.catalog_contract).resolve()
        if not resume.exists() or not contract_path.exists():
            raise SystemExit('missing resume model or action-contract copy')
        contract = json.loads(contract_path.read_text(encoding='utf-8'))
        report['checkpoint'] = {
            'path': str(resume),
            'sha256': sha256(resume),
            'catalog_sha256_expected': contract.get('catalog_sha256'),
            'action_space_version_expected': contract.get('action_space_version'),
            'action_n_expected': contract.get('action_n'),
            'production_types_expected': len(contract.get('production_types') or []),
        }
        if int(contract.get('action_n', 0)) != S.ACTION_N:
            raise SystemExit('action contract mismatch: checkpoint action_n=%s vs local %s'
                             % (contract.get('action_n'), S.ACTION_N))
        if contract.get('version') != S.VERSION:
            raise SystemExit('action contract version mismatch: %s vs %s'
                             % (contract.get('version'), S.VERSION))
        expected_version = contract.get('action_space_version') or S.ACTION_SPACE_VERSION
        if expected_version not in S.ACTION_SPACE_VERSIONS:
            raise SystemExit('unsupported action_space_version in checkpoint: %s'
                             % expected_version)
        os.environ['DSH_ACTION_SPACE_VERSION'] = expected_version
        report['compatibility'] = {'static_precheck': 'PASS',
                                   'action_space_version': expected_version}
        save_json(out / 'run.json', report)

        # ---- 环境（会启动真实游戏并在首局校验生产目录哈希）----
        raw = BE.RA2BuildEnv(
            out / 'episodes', max_seconds=a.episode_seconds,
            decision_seconds=a.decision_seconds,
            game_dir=str((ROOT / a.game_dir).resolve()
                         if not pathlib.Path(a.game_dir).is_absolute() else a.game_dir),
            port=a.port, instance=a.instance, kill_stale=True)
        raw.expected_catalog_hash = contract.get('catalog_sha256')
        env = Monitor(raw, str(out / 'monitor.csv'))
        model = MaskablePPO.load(str(resume), env=env, device='cpu')
        if a.learning_rate is not None:
            model.learning_rate = a.learning_rate
            model._setup_lr_schedule()
        if a.ent_coef is not None:
            model.ent_coef = a.ent_coef
        report['model'] = {
            'n_steps': int(model.n_steps),
            'batch_size': int(model.batch_size),
            'n_epochs': int(model.n_epochs),
            'learning_rate': float(model.learning_rate),
            'ent_coef': float(model.ent_coef),
            'policy': type(model.policy).__name__,
            'policy_parameters': int(sum(p.numel() for p in model.policy.parameters())),
            'num_timesteps_loaded': int(model.num_timesteps),
        }
        before = weights(model).copy()
        steps_before, updates_before = int(model.num_timesteps), int(model._n_updates)
        optimizer_before = max((float(s.get('step', 0))
                                for s in model.policy.optimizer.state.values()),
                               default=0.0)
        report.update({'status': 'collecting_real_rollouts',
                       'timesteps_before': steps_before,
                       'updates_before': updates_before})
        save_json(out / 'run.json', report)

        # SB3 语义：reset_num_timesteps=False 时 total_timesteps 表示“本段新增步数”
        # （源码 _setup_learn: total_timesteps += self.num_timesteps），因此这里传增量。
        target = a.steps
        callback = BuildEvidenceCallback(out, raw)
        model.learn(total_timesteps=target, callback=callback, reset_num_timesteps=False)
        save_model(model, out / 'model.zip')
        save_model(model, out / 'checkpoint.zip')

        after = weights(model)
        loaded = MaskablePPO.load(str(out / 'model.zip'), device='cpu')
        round_trip = bool(np.array_equal(after, weights(loaded)))
        delta = float(np.linalg.norm(after - before))
        optimizer_after = max((float(s.get('step', 0))
                               for s in model.policy.optimizer.state.values()),
                              default=0.0)
        episodes = [episode_summary(r) for r in (getattr(raw, 'completed', []) or [])]
        build_awarded = sum(int(e.get('buildings_created_awarded') or 0) for e in episodes)
        build_via_prod = sum(int(e.get('buildings_via_production') or 0) for e in episodes)
        build_via_cy = sum(int(e.get('buildings_via_mcv_deploy') or 0) for e in episodes)
        live = raw.build_diagnostics() if hasattr(raw, 'build_diagnostics') else {}
        events_file = out / 'build-events.jsonl'
        event_lines = 0
        if events_file.exists():
            event_lines = sum(1 for _ in events_file.open(encoding='utf-8'))
        report.update({
            'status': 'stopped_by_request' if callback.requested_stop else 'complete',
            'timesteps_after': int(model.num_timesteps),
            'updates_after': int(model._n_updates),
            'optimizer_step_before': optimizer_before,
            'optimizer_step_after': optimizer_after,
            'real_transitions_this_run': int(model.num_timesteps - steps_before),
            'optimizer_updates_this_run': int(model._n_updates - updates_before),
            'parameter_l2_change': delta,
            'parameters_updated': delta > 0,
            'save_reload_weights_identical': round_trip,
            'optimizer_state_reloaded': bool(loaded.policy.optimizer.state),
            'model_sha256': sha256(out / 'model.zip'),
            'episodes': episodes,
            'episode_count': len(episodes),
            'build_reward': {
                'version': BE.RA2BuildEnv.reward_version,
                'config': str(BE.CONFIG_PATH),
                'revision': raw.build_reward_revision if raw is not None else None,
                'buildings_awarded_total': build_awarded,
                'buildings_via_production': build_via_prod,
                'buildings_via_mcv_deploy': build_via_cy,
                'event_log': str(events_file),
                'event_log_lines': event_lines,
                'last_episode_ledger': {'totals': live.get('totals'),
                                        'counts': live.get('counts')},
            },
            'compatibility': {
                'static_precheck': 'PASS',
                'catalog_matched_at_episode_start': True,
                'action_space_version': expected_version,
                'action_count': S.ACTION_N,
                'observation_dim': int(env.observation_space.shape[0]),
            },
            'total_reward': float(getattr(raw, 'total_reward', 0.0)),
            'finished_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
        })
        report['verdict'] = {
            'checkpoint_compatible': True,
            'reward_entered_training': bool(delta > 0 and model._n_updates > updates_before),
            'model_built_buildings': build_awarded > 0,
        }
        save_json(out / 'run.json', report)
        print(json.dumps({k: report.get(k) for k in (
            'status', 'real_transitions_this_run', 'optimizer_updates_this_run',
            'parameter_l2_change', 'save_reload_weights_identical', 'verdict')},
            ensure_ascii=False, indent=2), flush=True)
        return 0
    except BaseException as exc:  # noqa: BLE001
        report.update({'status': 'failed', 'error': '%s: %s' % (type(exc).__name__, exc),
                       'traceback': traceback.format_exc()[-4000:]})
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
            if 'episodes' not in report:
                report['episodes'] = []
            save_json(out / 'run.json', report)
        except Exception:  # noqa: BLE001
            pass


if __name__ == '__main__':
    sys.exit(main())
