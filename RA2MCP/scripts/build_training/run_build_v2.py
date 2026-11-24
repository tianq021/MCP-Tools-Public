"""建设训练 V2 实验编排器（单 Worker / 单游戏实例 / BUILD_ONLY / 预部署基地）。

流程：
1. 预检（安全门：无游戏/训练进程、物理可用≥6GB、提交率≤80%、GPU可用≥1GB）
2. 创建独立实验目录 experiments/build_training_v2/<timestamp>-<name>/
3. 安装 V2 独立地图副本（base_2peaks.map 的副本，不改原图）+ 无敌人 spawn 配置
   （PlayerCount=1 / AIPlayers=0 / Credits=50000 / ShortGame=No）；预部署基地由
   环境 warmup 用真实游戏命令完成（地图 [Structures] 预置玩家建筑已验证不可行）。
4. 复制 R1 checkpoint（不覆盖原模型）与 action-contract；复制 V2 奖励配置
5. 记录训练前资源快照；启动训练 + 每 5 秒资源监控（V2 第八节阈值）
6. 训练结束/超时/安全阈值 → 优雅停止；不自动重启，保存现状与故障信息
7. 记录训练后资源快照，清理异常残留游戏进程
8. 归集交付物：build_training_v2_metrics.csv / _events.jsonl / _resource.csv /
   _model/ / _manifest.json

用法：
  .venv-build\\Scripts\\python.exe scripts\\build_training\\run_build_v2.py \\
      --name build_v2_round1 --steps 3000 --episode-seconds 100 \\
      --decision-seconds 0.2 --max-runtime-minutes 25
"""
import argparse
import json
import pathlib
import shutil
import subprocess
import sys
import time
import traceback

ROOT = pathlib.Path(__file__).resolve().parents[2]
THIS_DIR = pathlib.Path(__file__).resolve().parent
SCRIPTS = ROOT / 'scripts'
for path in (str(THIS_DIR), str(SCRIPTS), str(SCRIPTS / 'dsh_train'),
             str(ROOT / 'vendor' / 'pyra2yr')):
    if path not in sys.path:
        sys.path.insert(0, path)

# 复用 V1 编排器的只读工具与安全门
from run_build_experiment import (preflight, snapshot, cleanup_leftover_games,
                                  sha256, save_json, PROTECTED_FILES)  # noqa: E402

TRAIN_SCRIPT = THIS_DIR / 'train_build_v2.py'
MONITOR_SCRIPT = THIS_DIR / 'monitor_build.py'
REWARD_V2_CONFIG = ROOT / 'training' / 'build_curriculum' / 'reward_build_v2_1.json'
MAP_DIR = ROOT / 'experiments' / 'build_training_v2' / 'map'
BASE_MAP = MAP_DIR / 'base_2peaks.map'
SPAWN_V2 = MAP_DIR / 'build_v2_spawn.ini'

# V2 第八节资源保护阈值
SAFETY_V2 = {
    'phys_avail_min_mb': 3 * 1024,
    'commit_pct_max': 85.0,
    'gpu_dedicated_used_max_mb': 1e9,   # V2 用「GPU 剩余显存 <500MB」判定
    'gpu_free_min_mb': 500.0,
    'commit_growth_window_s': 120.0,
    'commit_growth_max_mb': 2 * 1024,
}


def install_v2_map(game_dir):
    """安装独立地图副本 + 无敌人 spawn 配置（不修改原版地图/原游戏目录）。"""
    game_dir = pathlib.Path(game_dir)
    if not BASE_MAP.exists() or not SPAWN_V2.exists():
        raise SystemExit('V2 map assets missing; run prepare_build_v2.py first')
    shutil.copyfile(BASE_MAP, game_dir / 'spawnmap.ini')
    shutil.copyfile(SPAWN_V2, game_dir / 'spawn.ini')
    return {
        'map_source_copy': str(BASE_MAP),
        'map_source_copy_sha256': sha256(BASE_MAP),
        'spawn_ini_source': str(SPAWN_V2),
        'spawn_ini_source_sha256': sha256(SPAWN_V2),
        'installed_spawn_ini_sha256': sha256(game_dir / 'spawn.ini'),
        'installed_spawnmap_sha256': sha256(game_dir / 'spawnmap.ini'),
        'original_map_untouched': True,
        'note': '预部署基地由 build_env_v2 warmup 用真实游戏命令完成；'
                '地图玩家建筑预置（owner=Americans）实测不可行（多人模式不实例化地图 Houses）',
    }


def collect_artifacts(run):
    wanted = [
        'run.json', 'build_training_v2_model/model.zip',
        'build_training_v2_model/checkpoint_latest.zip',
        'build_training_v2_metrics.csv', 'build_training_v2_events.jsonl',
        'build-events.jsonl', 'setup-events.jsonl',
        'model_init_R1copy.zip', 'model_init_action-contract.json',
        'reward_build_v2.json', 'spawn.ini', 'train_stdout.log', 'monitor.csv',
        'monitor/monitor_summary.json', 'monitor/build_monitor.csv',
        'monitor/monitor_events.jsonl', 'early_stop_diagnosis.json',
        'snapshots/v2_training_before.json', 'snapshots/v2_training_after.json',
    ]
    artifacts = {}
    for rel in wanted:
        path = run / rel
        if path.exists() and path.is_file():
            artifacts[rel] = {'bytes': path.stat().st_size, 'sha256': sha256(path)}
    ckpt_dir = run / 'build_training_v2_model' / 'checkpoints'
    if ckpt_dir.exists():
        artifacts['checkpoints'] = [
            {'name': p.name, 'bytes': p.stat().st_size, 'sha256': sha256(p)}
            for p in sorted(ckpt_dir.glob('*.zip'))]
    return artifacts


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--name', default='build_v2_round1')
    ap.add_argument('--out-root', default='experiments/build_training_v2')
    ap.add_argument('--model', default='reports/rl/stage-A-round1-20261030-153225/model.zip')
    ap.add_argument('--resume-model', default=None,
                    help='续训 checkpoint 路径（默认使用 --model 的 R1 并重新初始化输出层）')
    ap.add_argument('--no-reinit-heads', action='store_true',
                    help='续训模式：保留 checkpoint 输出层与优化器状态，不重新初始化')
    ap.add_argument('--reward-config', default=str(REWARD_V2_CONFIG),
                    help='奖励配置路径（默认 V2.1）')
    ap.add_argument('--no-curriculum-masks', action='store_true',
                    help='关闭 V2.2 电力修复/奖励上限课程掩码（消融实验用）')
    ap.add_argument('--allow-unit-control', action='store_true',
                    help='V2.3：开放单位控制（观察采矿车/战斗单位控制与 MCV 展开新基地）')
    ap.add_argument('--unit-control-mode', default=None,
                    choices=('off', 'full', 'mcv_only'),
                    help='V2.4 单位控制课程（mcv_only=仅 MCV 控制/展开）')
    ap.add_argument('--allow-parallel', action='store_true',
                    help='允许与其他训练实例并行（预检不因已有训练/游戏进程而阻塞）')
    ap.add_argument('--focus-mcv-until-new-base', action='store_true',
                    help='V2.8：MCV 存在时只开放 MCV 动作直到新基地建立')
    ap.add_argument('--mcv-clear-deploy-only', action='store_true',
                    help='V2.8：部署仅当 MCV 周围清空时可用')
    ap.add_argument('--mcv-production-focus', action='store_true',
                    help='V2.8b：战车工厂建成后只允许生产 MCV')
    ap.add_argument('--focus-mcv-mask-noop', action='store_true',
                    help='V2.8c：MCV 焦点模式下屏蔽等待，强制操作 MCV')
    ap.add_argument('--contract',
                    default='reports/rl/stage-A-round1-20261030-153225/action-contract.json')
    ap.add_argument('--steps', type=int, default=3000)
    ap.add_argument('--max-extend-steps', type=int, default=10000)
    ap.add_argument('--episode-seconds', type=float, default=100.0)
    ap.add_argument('--decision-seconds', type=float, default=0.2)
    ap.add_argument('--seed', type=int, default=11)
    ap.add_argument('--game-dir', default='test-game-build')
    ap.add_argument('--port', type=int, default=14531)
    ap.add_argument('--instance', type=int, default=8)
    ap.add_argument('--max-runtime-minutes', type=float, default=25.0)
    ap.add_argument('--monitor-interval', type=float, default=5.0)
    ap.add_argument('--python', default=sys.executable)
    a = ap.parse_args()

    out_root = (ROOT / a.out_root).resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    run = out_root / (time.strftime('%Y%m%d-%H%M%S') + '-' + a.name)
    run.mkdir(parents=True, exist_ok=False)
    game_dir = (ROOT / a.game_dir).resolve() \
        if not pathlib.Path(a.game_dir).is_absolute() else pathlib.Path(a.game_dir)
    manifest = {'run_dir': str(run), 'name': a.name,
                'created_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
                'parameters': vars(a), 'safety_thresholds': SAFETY_V2}

    pf = preflight()
    if a.allow_parallel and pf.get('reasons'):
        pf['reasons'] = [r for r in pf['reasons']
                         if not (r.startswith('game processes running')
                                 or r.startswith('training processes running'))]
        pf['ok'] = not pf['reasons']
        pf['parallel_mode'] = True
    save_json(run / 'preflight.json', pf)
    if not pf['ok']:
        manifest.update({'status': 'BLOCKED_BY_PREFLIGHT', 'preflight': pf})
        save_json(run / 'run_manifest.json', manifest)
        print('[v2-orchestrator] BLOCKED_BY_PREFLIGHT:',
              json.dumps(pf['reasons'], ensure_ascii=False))
        return 3
    print('[v2-orchestrator] preflight PASS', flush=True)

    train = monitor = None
    train_log = monitor_log = None
    timed_out = False
    killed = []
    try:
        model_param = a.resume_model or a.model
        model_src = (ROOT / model_param).resolve() \
            if not pathlib.Path(model_param).is_absolute() else pathlib.Path(model_param)
        contract_src = (ROOT / a.contract).resolve() \
            if not pathlib.Path(a.contract).is_absolute() else pathlib.Path(a.contract)
        reward_config = pathlib.Path(a.reward_config)
        if not reward_config.is_absolute():
            reward_config = (ROOT / reward_config).resolve()
        for src in (model_src, contract_src, reward_config):
            if not src.exists():
                raise SystemExit('missing required input: %s' % src)
        model_copy = run / ('model_resume_copy.zip' if a.resume_model
                            else 'model_init_R1copy.zip')
        contract_copy = run / 'model_init_action-contract.json'
        shutil.copy2(model_src, model_copy)
        shutil.copy2(contract_src, contract_copy)
        shutil.copy2(reward_config, run / 'reward_build_v2.json')
        map_info = install_v2_map(game_dir)
        shutil.copy2(game_dir / 'spawn.ini', run / 'spawn.ini')

        plan = {
            'run_dir': str(run), 'name': a.name,
            'created_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'python': a.python, 'python_version': sys.version.split()[0],
            'map': map_info,
            'reward_config': {'path': str(reward_config),
                              'sha256': sha256(reward_config),
                              'independent_from_v4_and_v1': True},
            'inputs': {
                'model': {'source': str(model_src), 'copy': str(model_copy),
                          'sha256': sha256(model_copy),
                          'source_sha256': sha256(model_src),
                          'resume_mode': bool(a.resume_model),
                          'reinit_heads': not a.no_reinit_heads},
                'contract': {'source': str(contract_src), 'copy': str(contract_copy),
                             'sha256': sha256(contract_copy)},
            },
            'protected_files_sha256_before': {
                rel: (sha256(ROOT / rel) if (ROOT / rel).exists() else None)
                for rel in PROTECTED_FILES},
            'game': {
                'game_dir': str(game_dir),
                'exe_sha256': sha256(game_dir / 'gamemd-spawn-ra2yrcpp.exe'),
                'bridge_dll_sha256': sha256(game_dir / 'libra2yrcpp.dll'),
                'spawn_ini_sha256': sha256(game_dir / 'spawn.ini'),
                'spawnmap_sha256': sha256(game_dir / 'spawnmap.ini'),
                'port': a.port,
            },
            'training': {'steps': a.steps, 'max_extend_steps': a.max_extend_steps,
                         'episode_seconds': a.episode_seconds,
                         'decision_seconds': a.decision_seconds,
                         'seed': a.seed, 'instance': a.instance,
                         'max_runtime_minutes': a.max_runtime_minutes,
                         'checkpoint_every': 250, 'eval_every': 500,
                         'monitor_interval_s': a.monitor_interval,
                         'build_only_mask': True},
            'new_source_sha256': {
                name: sha256(THIS_DIR / name) for name in (
                    'build_reward_v2.py', 'build_env_v2.py', 'train_build_v2.py',
                    'monitor_build.py', 'run_build_v2.py', 'prepare_build_v2.py')},
            'safety_thresholds': SAFETY_V2,
        }
        manifest['plan'] = plan
        save_json(run / 'plan.json', plan)

        before = snapshot(a.python, run, 'v2_training_before')

        train_cmd = [
            a.python, str(TRAIN_SCRIPT), '--out', str(run),
            '--resume', str(model_copy), '--catalog-contract', str(contract_copy),
            '--steps', str(a.steps), '--max-extend-steps', str(a.max_extend_steps),
            '--episode-seconds', str(a.episode_seconds),
            '--decision-seconds', str(a.decision_seconds),
            '--seed', str(a.seed), '--game-dir', str(game_dir),
            '--port', str(a.port), '--instance', str(a.instance),
            '--checkpoint-every', '250', '--eval-every', '500',
            '--max-runtime-minutes', str(a.max_runtime_minutes),
            '--reward-config', str(reward_config)]
        if a.no_reinit_heads:
            train_cmd.append('--no-reinit-heads')
        if a.no_curriculum_masks:
            train_cmd.append('--no-curriculum-masks')
        if a.allow_unit_control:
            train_cmd.append('--allow-unit-control')
        if a.unit_control_mode:
            train_cmd.extend(['--unit-control-mode', a.unit_control_mode])
        if a.focus_mcv_until_new_base:
            train_cmd.append('--focus-mcv-until-new-base')
        if a.mcv_clear_deploy_only:
            train_cmd.append('--mcv-clear-deploy-only')
        if a.mcv_production_focus:
            train_cmd.append('--mcv-production-focus')
        if a.focus_mcv_mask_noop:
            train_cmd.append('--focus-mcv-mask-noop')
        print('[v2-orchestrator] training: %s' % ' '.join(train_cmd), flush=True)
        train_log = (run / 'train_stdout.log').open('wb')
        train = subprocess.Popen(train_cmd, cwd=str(ROOT), stdout=train_log,
                                 stderr=subprocess.STDOUT)

        monitor_cmd = [
            a.python, str(MONITOR_SCRIPT),
            '--out-dir', str(run / 'monitor'), '--run-dir', str(run),
            '--label', a.name, '--trainer-pid', str(train.pid),
            '--interval', str(a.monitor_interval),
            '--until-file', str(run / 'MONITOR_STOP'),
            '--safety', json.dumps(SAFETY_V2)]
        print('[v2-orchestrator] monitor: %s' % ' '.join(monitor_cmd), flush=True)
        monitor_log = (run / 'monitor_stdout.log').open('wb')
        monitor = subprocess.Popen(monitor_cmd, cwd=str(ROOT), stdout=monitor_log,
                                   stderr=subprocess.STDOUT)

        deadline = time.monotonic() + (a.max_runtime_minutes + 10.0) * 60.0
        while True:
            if train.poll() is not None:
                break
            if time.monotonic() >= deadline:
                timed_out = True
                (run / 'STOP').write_text('orchestrator_timeout', encoding='utf-8')
                print('[v2-orchestrator] runtime limit; requesting graceful stop',
                      flush=True)
                grace = time.monotonic() + 180.0
                while train.poll() is None and time.monotonic() < grace:
                    time.sleep(2.0)
                if train.poll() is None:
                    train.terminate()
                    try:
                        train.wait(timeout=30)
                    except Exception:  # noqa: BLE001
                        train.kill()
                break
            time.sleep(2.0)
        train_rc = train.wait(timeout=60)
        print('[v2-orchestrator] training exited rc=%s' % train_rc, flush=True)
    except BaseException as exc:  # noqa: BLE001
        manifest['orchestrator_error'] = '%s: %s' % (type(exc).__name__, exc)
        manifest['orchestrator_traceback'] = traceback.format_exc()[-5000:]
        train_rc = None
    finally:
        try:
            (run / 'MONITOR_STOP').write_text('training_finished', encoding='utf-8')
        except Exception:  # noqa: BLE001
            pass
        if monitor is not None:
            try:
                monitor.wait(timeout=120)
            except Exception:  # noqa: BLE001
                monitor.terminate()
                try:
                    monitor.wait(timeout=30)
                except Exception:  # noqa: BLE001
                    monitor.kill()
        if train is not None and train.poll() is None:
            train.terminate()
            try:
                train.wait(timeout=30)
            except Exception:  # noqa: BLE001
                train.kill()
        for handle in (train_log, monitor_log):
            try:
                if handle is not None:
                    handle.close()
            except Exception:  # noqa: BLE001
                pass
        killed = cleanup_leftover_games()
        after = snapshot(a.python, run, 'v2_training_after')

    def read_json(path):
        try:
            return json.loads(pathlib.Path(path).read_text(encoding='utf-8'))
        except Exception:  # noqa: BLE001
            return None

    train_run = read_json(run / 'run.json')
    monitor_summary = read_json(run / 'monitor' / 'monitor_summary.json')
    before_payload = read_json(run / 'snapshots' / 'v2_training_before.json')
    after_payload = read_json(run / 'snapshots' / 'v2_training_after.json')

    def snapshot_digest(payload):
        if not payload:
            return None
        sample = (payload.get('samples') or [{}])[-1]
        return {'ts': sample.get('ts'), 'memory': sample.get('memory'),
                'gpu': {k: sample.get('gpu', {}).get(k) for k in
                        ('name', 'memory_total_mb', 'memory_used_mb',
                         'memory_free_mb', 'util_gpu_pct', 'temp_c')},
                'process_summary': sample.get('process_summary')}

    # 交付物归集
    deliverables = {}
    reports_dir = ROOT / 'reports' / 'build_training'
    reports_dir.mkdir(parents=True, exist_ok=True)
    metrics_src = run / 'build_training_v2_metrics.csv'
    events_src = run / 'build_training_v2_events.jsonl'
    resource_src = run / 'monitor' / 'build_monitor.csv'
    model_src = run / 'build_training_v2_model'
    if metrics_src.exists():
        shutil.copy2(metrics_src, reports_dir / 'build_training_v2_metrics.csv')
        deliverables['metrics_csv'] = str(reports_dir / 'build_training_v2_metrics.csv')
    if events_src.exists():
        shutil.copy2(events_src, reports_dir / 'build_training_v2_events.jsonl')
        deliverables['events_jsonl'] = str(reports_dir / 'build_training_v2_events.jsonl')
    if resource_src.exists():
        shutil.copy2(resource_src, reports_dir / 'build_training_v2_resource.csv')
        deliverables['resource_csv'] = str(reports_dir / 'build_training_v2_resource.csv')
    if model_src.exists():
        target_model = out_root / 'build_training_v2_model'
        if target_model.exists():
            target_model = run / 'build_training_v2_model'  # 不覆盖旧交付，保留 run 内
        else:
            shutil.copytree(model_src, target_model)
            deliverables['model_dir'] = str(target_model)
        deliverables.setdefault('model_dir_run', str(model_src))

    status = (train_run or {}).get('status') or 'failed'
    if timed_out and status not in ('paused_no_positive_reward',):
        status = status + '_timeout' if status == 'stopped_by_request' else status
    manifest.update({
        'status': status,
        'train_exit_code': train_rc,
        'timed_out': timed_out,
        'preflight': pf,
        'train_run': train_run,
        'monitor_summary': monitor_summary,
        'snapshot_before': before,
        'snapshot_after': after,
        'resource_digest_before': snapshot_digest(before_payload),
        'resource_digest_after': snapshot_digest(after_payload),
        'leftover_games_killed': killed,
        'artifacts': collect_artifacts(run),
        'deliverables': deliverables,
        'protected_files_sha256_after': {
            rel: (sha256(ROOT / rel) if (ROOT / rel).exists() else None)
            for rel in PROTECTED_FILES},
        'finished_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
    })
    plan = manifest.get('plan') or {}
    if plan:
        manifest['protected_files_unchanged'] = (
            plan.get('protected_files_sha256_before') ==
            manifest.get('protected_files_sha256_after'))
    save_json(run / 'run_manifest.json', manifest)
    save_json(reports_dir / 'build_training_v2_manifest.json', manifest)
    deliverables['manifest'] = str(reports_dir / 'build_training_v2_manifest.json')
    print('[v2-orchestrator] status=%s run=%s' % (status, run), flush=True)
    print('[v2-orchestrator] RESULT_JSON %s' % json.dumps({
        'run_dir': str(run), 'status': status, 'timed_out': timed_out,
        'train_exit_code': train_rc,
        'verdict': (train_run or {}).get('verdict'),
        'stop_reason': (train_run or {}).get('stop_reason'),
        'budget_final': (train_run or {}).get('budget_final'),
        'cumulative_rewards': (train_run or {}).get('cumulative_rewards'),
        'transitions': (train_run or {}).get('real_transitions_this_run'),
        'updates': (train_run or {}).get('optimizer_updates_this_run'),
        'monitor_stop_reason': (monitor_summary or {}).get('stop_reason'),
        'monitor_violations': len((monitor_summary or {}).get('violations') or []),
        'protected_files_unchanged': manifest.get('protected_files_unchanged'),
        'deliverables': deliverables,
    }, ensure_ascii=False), flush=True)
    if status in ('complete', 'stopped_by_request', 'paused_no_positive_reward'):
        return 0
    return 1


if __name__ == '__main__':
    sys.exit(main())
