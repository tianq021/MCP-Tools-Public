"""One MaskablePPO policy, concurrent real games, isolated per-game evidence."""
import argparse
import hashlib
import json
import os
import pathlib
import shutil
import sys
import time
from functools import partial

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from dsh_rl.full_env import RA2FullEnv
if os.environ.get('DSH_COMBAT_ENV') == '1':
    from dsh_rl.combat_env import RA2CombatEnv as RA2FullEnv
from dsh_rl import full_spec as S
from dsh_rl.train import save_model, save_report
from dsh_episode import lockfile
from sb3_contrib import MaskablePPO
from stable_baselines3.common.vec_env import SubprocVecEnv
from dsh_rl.safe_vec import (SafeSubprocVecEnv, FeedbackBatchInterrupted,
                             resume_after_feedback)
from dsh_rl.recovering_vec import RecoveringSubprocVecEnv, WorkerRecoveryError
from dsh_rl import port_config
from dsh_rl.feedback import FEEDBACK_VERSION
from dsh_rl.reward_config import load as load_reward_config, revision as reward_config_revision
from stable_baselines3.common.monitor import Monitor
from stable_baselines3.common.callbacks import BaseCallback
import torch

# Fresh-init hyperparameters, copied from the V4/P2.0 reference checkpoint so a
# new actor/critic keeps the same architecture/hyperparameters as the baseline
# (net_arch 64x64, n_steps 32, n_epochs 4, ent_coef 0.03, ...) but starts from
# random weights.  Recorded in run.json for reproducibility.
FRESH_HYPERPARAMS = dict(
    n_steps=32, batch_size=64, n_epochs=4, gamma=0.99, gae_lambda=0.95,
    learning_rate=3e-4, clip_range=0.2, ent_coef=0.03, vf_coef=0.5,
    max_grad_norm=0.5, policy_kwargs={'net_arch': [64, 64]},
)


def prepare(index, base_port=port_config.DEFAULT_BASE_PORT):
    source = ROOT / 'test-game'
    target = ROOT / ('test-game' if index == 0 else f'test-game-{index}')
    if not target.exists():
        size = sum(p.stat().st_size for p in source.rglob('*') if p.is_file())
        if shutil.disk_usage(ROOT).free < size + 4 * 1024**3:
            raise RuntimeError('Insufficient disk headroom for another isolated game')
        shutil.copytree(source, target)
    if index:
        # Always derive from verified primary bytes, never re-patch a replica.
        data = (source / 'gamemd-spawn-ra2yrcpp.exe').read_bytes()
        names = []
        for guid in ('29e3bb2a-2f36-11d3-a72c-0090272fa661',
                     '01AF9993-3492-11d3-8F6F-0060089C05B1'):
            # 双字符十六进制后缀：支持最多 255 个实例（原单字符方案在第 16 个实例
            # 时 replacement 长度变化会触发 Unexpected mutex layout）。
            replacement = guid[:-2] + format(index + 1, '02X')
            if len(replacement) != len(guid) or data.count(guid.encode()) != 1:
                raise RuntimeError('Unexpected mutex layout')
            data = data.replace(guid.encode(), replacement.encode())
            names.append(replacement)
        exe_path = target / 'gamemd-spawn-ra2yrcpp.exe'
        # 上一个 run 残留的游戏进程可能短暂锁住 exe（PermissionError）：
        # 重试最多 30 秒，并给出明确错误而不是直接崩溃。
        last_error = None
        for _attempt in range(30):
            try:
                exe_path.write_bytes(data)
                last_error = None
                break
            except PermissionError as exc:  # noqa: PERF203
                last_error = exc
                time.sleep(1.0)
        if last_error is not None:
            raise RuntimeError(
                f'cannot write patched exe (a stale game may still be running): '
                f'{exe_path}: {last_error!r}')
        save_report(target / 'ra2mcp-instance.json', dict(schema_version=1,
            instance=index, patched=True, patched_sha256=hashlib.sha256(data).hexdigest(),
            mutex_names=names))
    # P0-6：所有 Worker 的游戏 bridge 端口统一按 base_port 分配，不能有 Worker 残留旧端口。
    port_config.configure_game_port(target, int(base_port) + index)
    if index:
        for name in ('spawn.ini', 'spawnmap.ini'):
            shutil.copy2(source / name, target / name)


def make_env(index, out, contract, seconds, limit,
             jitter_seconds=0.0, stagger_seconds=0.0,
             base_port=port_config.DEFAULT_BASE_PORT):
    torch.set_num_threads(1)
    folder = pathlib.Path(out) / 'workers' / str(index)
    # 错峰训练：不同实例的局长不同（确定性抖动），避免所有对局同时结束/同时重开，
    # 也避免启动瞬间同时拉起全部游戏造成窗口/桥接竞争。
    jitter = 0.0
    if limit and jitter_seconds:
        jitter = jitter_seconds * ((index * 7) % 10) / 9.0
    max_seconds = (limit + jitter) if limit else limit
    env = RA2FullEnv(folder / 'episodes', max_seconds, seconds,
        game_dir=ROOT / ('test-game' if index == 0 else f'test-game-{index}'),
        port=int(base_port) + index, instance=index, kill_stale=False, render_off=True,
        launch_delay_seconds=stagger_seconds * index)
    if contract:
        env.expected_catalog_hash = contract['catalog_sha256']
    env.transport_feedback_failures = True
    return Monitor(env, str(folder / 'monitor.csv'))


def write_index(out):
    index = []
    for p in sorted((out / 'workers').glob('*/episodes/*.result.json')):
        r = json.loads(p.read_text(encoding='utf-8'))
        index.append(dict(worker=p.parents[1].name, episode_id=r['episode_id'],
            kind=r['kind'], elapsed_s=r['elapsed_s'], metrics=r['metrics'],
            feedback_version=r.get('feedback_version'),
            outcome_confirmed=r.get('outcome_confirmed', False),
            learning_excluded=r.get('learning_excluded', False),
            failure=r.get('failure'), reward_parts=r.get('reward_parts'),
            result=str(p.relative_to(out)),
            review=str((p.parents[1]/'reflections'/(r['episode_id']+'.md')).relative_to(out))))
    save_report(out / 'episode-index.json', index)


class Evidence(BaseCallback):
    def __init__(self, out, start_steps, started, max_episodes=0,
                 max_runtime_minutes=0.0, checkpoint_every_episodes=2):
        super().__init__()
        self.out, self.start_steps, self.started = out, start_steps, started
        self.rollouts = 0
        self.last_sample = None
        self.discarded_rollouts = self.discarded_samples = 0
        self.max_episodes = int(max_episodes or 0)
        self.max_runtime_minutes = float(max_runtime_minutes or 0.0)
        self.checkpoint_every_episodes = int(checkpoint_every_episodes or 0)
        self._next_episode_checkpoint = self.checkpoint_every_episodes
        self._episode_count = 0

    def completed_episodes(self):
        """Number of finalized games (defeat/win/timeout) across all workers."""
        count = 0
        for path in (self.out / 'workers').glob('*/episodes/*.result.json'):
            try:
                kind = json.loads(path.read_text(encoding='utf-8')).get('kind')
            except Exception:  # noqa: BLE001
                continue
            if kind in ('defeat', 'win', 'timeout'):
                count += 1
        return count

    def _on_rollout_start(self):
        save_model(self.model, self.out / 'checkpoint.zip')
        self.rollouts += 1

    def _on_step(self):
        if self.n_calls % 16 == 0:
            if shutil.disk_usage(self.out).free < 2 * 1024**3:
                (self.out / 'STOP').write_text('disk_headroom_below_2GiB', encoding='utf-8')
            counts = self.training_env.get_attr('transitions')
            elapsed = time.monotonic() - self.started
            current = (time.monotonic(), sum(counts))
            rate = ((current[1] - self.last_sample[1]) / (current[0] - self.last_sample[0])) if self.last_sample else None
            self.last_sample = current
            self._episode_count = self.completed_episodes()
            # Explicit per-N-episode checkpoints (Stage A round-1 requirement).
            if (self.checkpoint_every_episodes
                    and self._episode_count >= self._next_episode_checkpoint):
                save_model(self.model, self.out / f'checkpoint-ep{self._episode_count:03d}.zip')
                self._next_episode_checkpoint = (self._episode_count
                                                 + self.checkpoint_every_episodes)
            instances_running = None
            try:
                flags = self.training_env.env_method('is_episode_active')
                active = sum(1 for f in flags if f)
                instances_running = f'{active}/{len(flags)}'
            except Exception:  # noqa: BLE001
                instances_running = None
            save_report(self.out / 'progress.json', dict(status='training',
                timesteps=self.num_timesteps, optimizer_epochs=self.model._n_updates,
                workers=len(counts), worker_transitions=counts,
                instances_running=instances_running,
                new_steps=self.num_timesteps-self.start_steps,
                completed_episodes=self._episode_count,
                feedback_version=FEEDBACK_VERSION,
                feedback_discarded_rollouts=self.discarded_rollouts,
                feedback_discarded_samples=self.discarded_samples,
                transitions_per_second=sum(counts)/max(1, elapsed),
                recent_transitions_per_second=rate,
                updated_at=time.strftime('%Y-%m-%dT%H:%M:%S')))
            write_index(self.out)
            print(f'shared steps={self.num_timesteps} epochs={self.model._n_updates} '
                  f'episodes={self._episode_count} samples/s={sum(counts)/max(1,elapsed):.2f} '
                  f'instances={instances_running}',
                  flush=True)
            if self.max_episodes and self._episode_count >= self.max_episodes:
                (self.out / 'STOP').write_text(
                    f'episode_limit_reached:{self._episode_count}', encoding='utf-8')
                print(f'episode limit reached ({self._episode_count}); stopping', flush=True)
            if (self.max_runtime_minutes
                    and (time.monotonic() - self.started) / 60.0 >= self.max_runtime_minutes):
                (self.out / 'STOP').write_text(
                    f'runtime_limit:{self.max_runtime_minutes}min', encoding='utf-8')
                print('runtime limit reached; stopping', flush=True)
        return not (self.out / 'STOP').exists()


def _build_vector_env(env_fns, a, out):
    """P0-3 feature flag：默认旧 SafeSubprocVecEnv；--recovering-vec 才使用独立恢复器。"""
    if not a.recovering_vec:
        return SafeSubprocVecEnv(env_fns, start_method='spawn')
    jitter = tuple(float(value) for value in (a.recovering_jitter_seconds or (1.0, 3.0)))
    stats_dir = pathlib.Path(out) / 'worker-stats'
    return RecoveringSubprocVecEnv(
        env_fns,
        start_method='spawn',
        max_launches=int(a.recovering_max_launches),
        launch_jitter=jitter,
        recv_timeout_s=max(60.0, float(a.episode_seconds)),
        startup_timeout_s=180.0,
        max_restarts_per_worker=int(a.recovering_max_restarts),
        stats_dir=stats_dir,
        verbose=0,
    )


def _base_port_type(value):
    text = str(value).strip().lower()
    if text == 'auto':
        return 'auto'
    try:
        port = int(text)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError('base-port must be an integer or auto')
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError('base-port must be within 1..65535')
    return port


def _resolve_base_port(requested, workers, approval_config):
    expected = (approval_config or {}).get('port_configuration') or {}
    expected_base = expected.get('base_port')
    if requested == 'auto':
        if expected_base is not None:
            candidate = int(expected_base)
        else:
            suggestions = port_config.suggest_base_ports(workers, count=1)
            if not suggestions:
                raise port_config.PortPreflightError(
                    'NO_FREE_BASE_PORT',
                    'no verified free base port found for %d workers' % workers)
            candidate = suggestions[0]
        print('AUTO_BASE_PORT_SELECTED base_port=%d workers=%d'
              % (candidate, workers), flush=True)
        return candidate
    return int(requested)


def prepare_port_setup(a, approval_config):
    """P0-6：解析/校验 base-port，检查审批配置一致性并做真实 bind 预检。"""
    base_port = _resolve_base_port(getattr(a, 'base_port', port_config.DEFAULT_BASE_PORT),
                                   int(a.workers), approval_config)
    approval_ports = port_config.validate_approval_port_config(
        approval_config or {}, base_port, int(a.workers))
    report = port_config.preflight_ports(base_port, int(a.workers))
    if not report['ok']:
        raise port_config.PortPreflightError(
            'PORT_PREFLIGHT_FAILED', port_config.format_failure(report), report)
    return base_port, approval_ports, report


def build_parser():
    ap = argparse.ArgumentParser()
    ap.add_argument('--workers', type=int, default=8, choices=range(2, 25))
    ap.add_argument('--base-port', type=_base_port_type, default=port_config.DEFAULT_BASE_PORT,
                    help='P0-6：Worker bridge 端口基址（Worker i 使用 base+i；默认 14521 向后兼容；auto=自动选可用基址）')
    ap.add_argument('--resume', default=None,
                    help='Resume a previous run checkpoint (mutually exclusive with --fresh)')
    ap.add_argument('--fresh', action='store_true',
                    help='Create a brand-new PPO actor/critic (default Stage A init)')
    ap.add_argument('--out', required=True)
    ap.add_argument('--device', choices=('cpu', 'cuda'), default='cpu',
                    help='PPO policy device; cuda 只在确有收益时使用（小 MLP 通常 CPU 足够）')
    ap.add_argument('--seed', type=int, default=None,
                    help='P4/W1：训练随机种子（未指定时从审批配置 approval.seed 读取；记录到 run.json）')
    ap.add_argument('--decision-seconds', type=float, default=.5)
    ap.add_argument('--episode-seconds', type=float, default=600)
    ap.add_argument('--episode-jitter-seconds', type=float, default=0.0,
                    help='错峰训练：每个实例在 episode-seconds 基础上确定性加 0..该值 的时长抖动，'
                         '避免所有对局同时结束/同时重开')
    ap.add_argument('--launch-stagger-seconds', type=float, default=0.0,
                    help='错峰启动：第 i 个实例首次开局前延迟 i*该值 秒，避免同时拉起全部游戏')
    # P0-3：Worker 独立恢复器（默认关闭，保留旧 SafeSubprocVecEnv 入口）
    ap.add_argument('--recovering-vec', action='store_true',
                    help='P0-3：使用 RecoveringSubprocVecEnv（单 Worker 故障只重启该 Worker）')
    ap.add_argument('--recovering-max-launches', type=int, default=2,
                    help='P0-3：全局同时启动游戏数上限（默认 2）')
    ap.add_argument('--recovering-max-restarts', type=int, default=5,
                    help='P0-3：每个 Worker 的最大恢复次数（超过则干净失败，不无限重启）')
    ap.add_argument('--recovering-jitter-seconds', type=float, nargs=2, default=(1.0, 3.0),
                    metavar=('MIN', 'MAX'),
                    help='P0-3：每次启动前的随机延迟范围（秒），默认 1 3')
    ap.add_argument('--steps', type=int, default=0, help='0 means until STOP')
    ap.add_argument('--max-episodes', type=int, default=0,
                    help='Stop automatically after this many completed games (0 = unlimited)')
    ap.add_argument('--max-runtime-minutes', type=float, default=0.0,
                    help='Safety stop after this many minutes (0 = unlimited)')
    ap.add_argument('--checkpoint-every-episodes', type=int, default=2,
                    help='Save checkpoint-epNNN.zip every N completed games')
    ap.add_argument('--allow-reward-change', action='store_true',
                    help='Allow resuming a checkpoint created under an older reward version')
    ap.add_argument('--action-space-version', choices=S.ACTION_SPACE_VERSIONS,
                    default=S.ACTION_SPACE_VERSION,
                    help='Action mapping version (default: P2.0 target/unit ordering)')
    ap.add_argument('--target-candidate-range', type=float,
                    default=S.DEFAULT_TARGET_CANDIDATE_RANGE,
                    help='P2.0 engagement radius in cells for attack target candidates')
    ap.add_argument('--target-nearest-k', type=int, default=S.DEFAULT_TARGET_NEAREST_K,
                    help='Neighbours used for the per-target army-proximity score')
    ap.add_argument('--allow-action-space-change', action='store_true',
                    help='Allow resuming an old checkpoint whose target/unit slot semantics differ')
    ap.add_argument('--approval-token', default=None,
                    help='P0 审批安全底座：一次性审批令牌 ID（启动训练必需）')
    ap.add_argument('--approval-config', default=None,
                    help='P0 审批安全底座：与令牌绑定的 run 配置 JSON 路径')
    ap.add_argument('--approval-check-only', action='store_true',
                    help='P0：只校验令牌与配置并退出，不消费令牌、不启动训练')
    return ap


def main():
    a = build_parser().parse_args()
    if bool(a.fresh) == bool(a.resume):
        raise SystemExit('exactly one of --fresh or --resume is required')
    # ---- P0 审批安全底座：无一次性令牌不允许启动训练 ----
    # 说明：真正的令牌消费发生在所有 resume/contract 校验通过、即将创建环境之前
    # （见下方 reserve_and_mark_running）；此处先做无副作用校验，--approval-check-only
    # 在校验通过后直接退出，用于人工/自动化验证启动入口而不运行训练。
    if not (a.approval_token and a.approval_config):
        raise SystemExit(
            'TRAINING REFUSED (P0 approval gate): --approval-token and --approval-config are required.\n'
            'Issue a one-time token: python scripts/dsh_learning/approvals.py issue '
            '--config <run-config.json> [--model <model.zip>]\n'
            'Validate without training: add --approval-check-only')
    from dsh_learning import approvals as _approvals  # 离线模块：不启动游戏/训练
    _approval_request = None
    _approval_consumed = None
    try:
        _approval_request = _approvals.validate_launch_request(
            a.approval_token, a.approval_config,
            runtime={'workers': a.workers, 'episode_seconds': a.episode_seconds,
                     'steps': a.steps, 'device': a.device, 'seed': a.seed,
                     'resume': None if a.fresh else a.resume, 'fresh': bool(a.fresh),
                     'reward_version': RA2FullEnv.reward_version,
                     'action_space_version': a.action_space_version,
                     'obs_version': 'features_v2',
                     'config_path': a.approval_config})
    except _approvals.ApprovalError as error:
        raise SystemExit('TRAINING REFUSED (P0 approval gate): %s' % error)
    if a.seed is None:
        a.seed = int((_approval_request.get('config') or {}).get('approval', {}).get('seed'))
    # P0-6：端口审批一致性 + Windows 排除范围 + 实际 bind 预检（在消费令牌/启动游戏之前）。
    try:
        base_port, port_approval, initial_port_report = prepare_port_setup(
            a, (_approval_request.get('config') or {}))
    except port_config.PortPreflightError as error:
        print(port_config.format_failure(error.report or {}), flush=True)
        raise SystemExit('TRAINING REFUSED (P0-6 port preflight): %s' % error)
    a.base_port = base_port
    print('PORT_PREFLIGHT_OK base_port=%d workers=%d ports=%s'
          % (base_port, a.workers, port_config.worker_ports(base_port, a.workers)),
          flush=True)
    if a.approval_check_only:
        print('PORT_PREFLIGHT_OK_CHECK_ONLY base_port=%d ports=%s'
              % (base_port, port_config.worker_ports(base_port, a.workers)),
              flush=True)
        print('APPROVAL_CHECK_OK', a.approval_token,
              _approval_request['experiment'], _approval_request['skill'],
              'steps=%d' % _approval_request['steps'],
              'seed=%s' % a.seed, 'base_port=%d' % base_port, flush=True)
        return
    # Workers are spawned as subprocesses and inherit os.environ: this is how the
    # action-space mapping selection reaches every real-game env.
    os.environ['DSH_ACTION_SPACE_VERSION'] = a.action_space_version
    os.environ['DSH_TARGET_CANDIDATE_RANGE'] = str(a.target_candidate_range)
    os.environ['DSH_TARGET_NEAREST_K'] = str(a.target_nearest_k)
    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    out = pathlib.Path(a.out)
    source = pathlib.Path(a.resume) if a.resume else None
    out.mkdir(parents=True, exist_ok=True)
    if (out/'run.json').exists():
        raise RuntimeError('Use a new output directory')
    # A crash leaves lock files behind. Windows recycles PIDs, so the next run
    # must not mistake a recycled PID for a live holder and abort at startup.
    removed, kept = lockfile.reclaim_stale(ROOT/'build')
    if removed:
        print(f'reclaimed stale locks: {[p.name for p in removed]}', flush=True)
    for path, reason in kept:
        print(f'kept lock {path.name}: {reason}', flush=True)
    previous = contract = None
    reward_changed_from = action_changed_from = None
    if source is not None:
        previous = json.loads((source.parent/'run.json').read_text(encoding='utf-8-sig'))
        contract = json.loads((source.parent/'action-contract.json').read_text(encoding='utf-8-sig'))
        if previous.get('control_mode') != 'full':
            raise RuntimeError('Incompatible resume model')
        if previous.get('reward_version') != RA2FullEnv.reward_version:
            # A new reward contract changes the value scale/meaning of every stored
            # return; resuming silently would mix incompatible returns.  Callers
            # must opt in explicitly and a new run directory is still required.
            if not a.allow_reward_change:
                raise RuntimeError(
                    f"Incompatible reward version {previous.get('reward_version')!r} != "
                    f"{RA2FullEnv.reward_version!r}; pass --allow-reward-change after "
                    "reviewing the reward diff")
            reward_changed_from = previous.get('reward_version')
        previous_action = previous.get('action_space_version') or S.LEGACY_ACTION_SPACE_VERSION
        if previous_action != a.action_space_version:
            # same ACTION_N / observation size, but target-slot and unit-slot
            # semantics changed: silently resuming would reuse a policy that was
            # trained on a different action meaning.  Require explicit opt-in.
            if not a.allow_action_space_change:
                raise RuntimeError(
                    f"Incompatible action space {previous_action!r} != {a.action_space_version!r}; "
                    "pass --allow-action-space-change after reviewing docs/action-space-v2-design.md")
            action_changed_from = previous_action
    # Reward/action contract fingerprint actually used by this run (recorded even
    # for fresh runs so the report is reproducible without reading the env).
    if os.environ.get('DSH_COMBAT_ENV') == '1':
        reward_config_path = ROOT / os.environ.get(
            'DSH_COMBAT_REWARDS', 'config/rewards-combat.json')
    else:
        reward_config_path = ROOT / 'config/rewards.json'
    reward_config_data = load_reward_config(reward_config_path)
    reward_config_sha256 = hashlib.sha256(reward_config_path.read_bytes()).hexdigest()
    # P0：原子消费令牌并在启动任何游戏进程之前标记 RUNNING。
    try:
        _approval_consumed = _approvals.reserve_and_mark_running(
            a.approval_token, _approval_request, out, os.getpid())
        print('APPROVAL_CONSUMED', a.approval_token,
              _approval_consumed['lifecycle'], flush=True)
    except _approvals.ApprovalError as error:
        raise SystemExit('TRAINING REFUSED (P0 approval gate): %s' % error)
    from dsh_learning import seeding as _seeding
    _seed_info = _seeding.seed_everything(a.seed)
    print('TRAINING_SEED', _seed_info, flush=True)
    locks, env, model = [], None, None
    report = dict(status='preparing', control_mode='full',
        reward_version=RA2FullEnv.reward_version,
        reward_changed_from=reward_changed_from,
        reward_config_path=str(reward_config_path.relative_to(ROOT)),
        reward_config_sha256=reward_config_sha256,
        reward_config_revision=reward_config_revision(reward_config_data),
        action_space_version=a.action_space_version,
        action_changed_from=action_changed_from,
        target_candidate_range=a.target_candidate_range,
        target_nearest_k=a.target_nearest_k,
        model_init='fresh' if a.fresh else 'resume',
        fresh_hyperparameters=FRESH_HYPERPARAMS if a.fresh else None,
        resume_model=None if source is None else str(source),
        parameters=vars(a), shared_policy=True, pid=os.getpid(), device=a.device,
        training_seed=a.seed,
        approval_token_id=a.approval_token,
        approval_experiment=None if _approval_request is None else _approval_request['experiment'],
        approval_skill=None if _approval_request is None else _approval_request['skill'],
        feedback_version=FEEDBACK_VERSION,
        combat_stage=os.environ.get('DSH_COMBAT_ENV') == '1',
        recovering_vec=bool(a.recovering_vec),
        recovering_max_launches=a.recovering_max_launches,
        recovering_max_restarts=a.recovering_max_restarts,
        recovering_jitter_seconds=list(a.recovering_jitter_seconds),
        base_port=base_port,
        worker_ports=port_config.worker_ports(base_port, a.workers),
        port_approval_config=port_approval,
        port_preflight=initial_port_report,
        source_sha256={name: hashlib.sha256((ROOT/'scripts'/name).read_bytes()).hexdigest()
                       for name in ('dsh_rl/env.py', 'dsh_rl/feedback.py',
                                    'dsh_rl/safe_vec.py', 'dsh_episode/runner.py',
                                    'dsh_rl/truth_tracker.py', 'dsh_rl/reward_config.py',
                                    'dsh_rl/full_env.py', 'dsh_rl/full_spec.py',
                                    'dsh_episode/schema.py', 'dsh_rl/port_config.py',
                                    'dsh_rl/recovering_vec.py', 'dsh_rl/train_shared.py')})
    if contract is not None:
        contract = dict(contract)
        contract.update({'action_space_version': a.action_space_version,
                         'target_candidate_range': a.target_candidate_range,
                         'target_nearest_k': a.target_nearest_k,
                         'unit_slots_stable': a.action_space_version != S.LEGACY_ACTION_SPACE_VERSION})
    save_report(out/'run.json', report)
    if contract is not None:
        save_report(out/'action-contract.json', contract)
    try:
        for i in range(a.workers):
            locks.append(lockfile.acquire(ROOT/'build'/('dsh-experiment.lock' if i==0 else f'dsh-experiment-{i}.lock')))
            prepare(i, base_port)
        train_env_fns = [partial(make_env, i, str(out), contract, a.decision_seconds,
                                 a.episode_seconds, a.episode_jitter_seconds,
                                 a.launch_stagger_seconds, base_port)
                         for i in range(a.workers)]
        env = _build_vector_env(train_env_fns, a, out)
        if a.seed is not None and hasattr(env, 'seed'):
            try:
                env.seed(a.seed)
            except Exception as error:  # noqa: BLE001 - 环境 seed 失败不应掩盖训练
                print('env.seed failed: %r' % (error,), flush=True)
        if a.fresh:
            # Brand-new actor/critic: same architecture/hyperparameters as the
            # recorded reference policy, but random weights and no optimizer /
            # experience cache from the old checkpoint.
            model = MaskablePPO('MlpPolicy', env, verbose=1, device=a.device,
                                seed=a.seed, **FRESH_HYPERPARAMS)
        else:
            model = MaskablePPO.load(source, env=env, device=a.device)
            if a.seed is not None and hasattr(model, 'set_random_seed'):
                model.set_random_seed(a.seed)
        # Keep the optimizer/architecture/discount and decision duration intact.
        # A larger minibatch avoids hundreds of tiny optimizer steps per rollout.
        model.batch_size = min(256, model.n_steps * a.workers)
        before = model.num_timesteps
        report.update(status='training', timesteps_before=before, batch_size=model.batch_size,
                      policy_parameters=sum(p.numel() for p in model.policy.parameters()))
        save_report(out/'run.json', report)
        callback = Evidence(out, before, time.monotonic(),
                            max_episodes=a.max_episodes,
                            max_runtime_minutes=a.max_runtime_minutes,
                            checkpoint_every_episodes=a.checkpoint_every_episodes)
        # P0-6：启动游戏前的最后一次端口复查，尽量缩小预检与 reset/游戏启动的 TOCTOU 窗口。
        final_port_report = port_config.preflight_ports(base_port, a.workers)
        report['port_preflight_final'] = final_port_report
        save_report(out/'run.json', report)
        if not final_port_report['ok']:
            raise RuntimeError(
                'PORT_PREFLIGHT_FAILED_AT_LAST_MOMENT\n'
                + port_config.format_failure(final_port_report))
        print('PORT_PREFLIGHT_OK_FINAL base_port=%d ports=%s'
              % (base_port, port_config.worker_ports(base_port, a.workers)), flush=True)
        recoveries = 0
        while not (out/'STOP').exists():
            try:
                model.learn(total_timesteps=a.steps or 4096, callback=callback, reset_num_timesteps=False)
            except FeedbackBatchInterrupted as interruption:
                discarded = resume_after_feedback(model, interruption)
                callback.discarded_rollouts += 1
                callback.discarded_samples += discarded + a.workers
                diagnostic = dict(t=time.strftime('%Y-%m-%dT%H:%M:%S'),
                    timesteps=model.num_timesteps, excluded_rollout_samples=discarded,
                    excluded_vector_step_samples=a.workers,
                    failures=[info for info in interruption.infos
                              if info.get('feedback_valid') is False])
                with (out/'feedback-discarded.jsonl').open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(diagnostic) + '\n')
                report.update(feedback_discarded_rollouts=callback.discarded_rollouts,
                              feedback_discarded_samples=callback.discarded_samples)
                save_report(out/'run.json', report)
                print(f'feedback unavailable: excluded {discarded + a.workers} samples; '
                      'continuing from real reset observations', flush=True)
                continue
            except (EOFError, BrokenPipeError, ConnectionError, TimeoutError) as error:
                if a.recovering_vec:
                    # 恢复器本应内部处理单 Worker 故障；若仍到达此处说明基础设施异常，
                    # 不允许退回“全量重建”，干净失败并保留 emergency checkpoint。
                    print(f'worker infrastructure failure under recovering vec: {error!r}',
                          flush=True)
                    save_model(model, out/'emergency.zip')
                    report.update(status='failed', error=repr(error),
                                  recovery_failure=True)
                    save_report(out/'run.json', report)
                    raise
                # A worker process died despite in-env crash handling (e.g. its
                # game could not relaunch). Keep the in-memory model and rebuild
                # the workers instead of abandoning the run.
                recoveries += 1
                print(f'learner IPC failure {recoveries}: {error!r}; rebuilding workers', flush=True)
                save_model(model, out/'emergency.zip')
                report['recoveries'] = recoveries
                save_report(out/'run.json', report)
                if recoveries > 5:
                    raise
                try:
                    if env:
                        env.close()
                except BaseException:  # noqa: BLE001
                    pass
                env = None
                time.sleep(min(60, 5 * recoveries))
                rebuild_fns = [partial(make_env, i, str(out), contract,
                                       a.decision_seconds, a.episode_seconds,
                                       a.episode_jitter_seconds,
                                       a.launch_stagger_seconds, base_port)
                               for i in range(a.workers)]
                env = _build_vector_env(rebuild_fns, a, out)
                model.set_env(env)
                # Force a fresh reset of the rebuilt workers on the next learn().
                model._last_obs = None
                model._last_episode_starts = None
                continue
            except WorkerRecoveryError as error:
                # P0-3：恢复预算耗尽/持续失败 → 记录 Worker 统计后干净失败（不无限重启）。
                print(f'worker recovery failed: {error!r}', flush=True)
                save_model(model, out/'emergency.zip')
                report.update(status='failed', error=repr(error), recovery_failure=True,
                              worker_recovery_error=getattr(error, 'stats', None))
                if env is not None and hasattr(env, 'stats_summary'):
                    report['worker_stats'] = env.stats_summary()
                save_report(out/'run.json', report)
                raise
            recoveries = 0
            save_model(model, out/'checkpoint.zip')
            if a.steps:
                break
        save_model(model, out/'model.zip')
        if not (out/'action-contract.json').exists():
            # Fresh run: the real catalog manifest is written by the first env
            # worker; publish the worker's manifest at the run root so later
            # evaluation tooling (play.py) can verify the catalog hash.
            candidates = sorted((out/'workers').glob('*/action-contract.json'))
            if candidates:
                shutil.copy2(candidates[0], out/'action-contract.json')
        report.update(status='stopped_by_request' if (out/'STOP').exists() else 'complete',
                      timesteps_after=model.num_timesteps, optimizer_epochs=model._n_updates,
                      completed_episodes=callback.completed_episodes())
    except BaseException as error:
        report.update(status='failed', error=repr(error))
        # Publish failure before any cleanup that might itself fail.
        save_report(out/'run.json', report)
        save_report(out/'progress.json', dict(status='failed', error=repr(error),
                    timesteps=model.num_timesteps if model else 0))
        if model:
            save_model(model, out/'emergency.zip')
        raise
    finally:
        try:
            if env is not None and hasattr(env, 'stats_summary'):
                try:
                    report['worker_stats'] = env.stats_summary()
                    save_report(out/'run.json', report)
                except BaseException as stats_error:  # noqa: BLE001 - 统计失败不能掩盖训练结果
                    print('worker-stats snapshot failed: %r' % (stats_error,), flush=True)
            if env:
                env.close()
        finally:
            for held in reversed(locks):
                lockfile.release(held)
            write_index(out)
            save_report(out/'run.json', report)
            progress_path = out/'progress.json'
            progress = json.loads(progress_path.read_text()) if progress_path.exists() else {}
            progress.update(status=report['status'], updated_at=time.strftime('%Y-%m-%dT%H:%M:%S'))
            if model:
                progress.update(timesteps=model.num_timesteps, optimizer_epochs=model._n_updates)
            save_report(progress_path, progress)
            # P0：结束令牌生命周期（成功/停止 = FINISHED_OK，失败 = FINISHED_FAIL）。
            if _approval_consumed is not None:
                try:
                    _approvals.mark_finished(
                        a.approval_token,
                        report.get('status') in ('complete', 'stopped_by_request'))
                except Exception as error:  # noqa: BLE001 - 令牌收尾失败不能掩盖训练结果
                    print('approval finish hook failed: %r' % (error,), flush=True)


if __name__ == '__main__':
    main()

