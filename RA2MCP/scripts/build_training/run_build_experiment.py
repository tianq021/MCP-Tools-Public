"""无敌人建设训练 V1 实验编排器（单 Worker / 单游戏实例 / 短程训练）。

流程：
1. 预检（安全门）：无游戏/训练进程、物理可用 ≥6GB、提交率 ≤80%、GPU 可用 ≥1GB
   —— 不满足则写 BLOCKED 预检报告并退出，不启动任何游戏或训练。
2. 创建独立实验目录 experiments/build_training_v1/<timestamp>-<name>/
3. 复制 R1 checkpoint（model_init_R1copy.zip）与 action-contract（只读副本，不覆盖原模型）
4. 写入无敌人地图配置（方案 A1：PlayerCount=1 / AIPlayers=0）到 test-game-build
5. 记录训练前资源快照（capture_baseline.py --tag before_training）
6. 启动训练子进程（train_build_v1.py）+ 10 秒级资源监控子进程（monitor_build.py）
7. 训练结束/超时/安全阈值触发 → 优雅停止；保存故障信息，不自动重启
8. 记录训练后资源快照，清理残留游戏进程，写 run_manifest.json

用法：
  .venv-build\\Scripts\\python.exe scripts\\build_training\\run_build_experiment.py \\
      --name build_v1 --steps 320 --episode-seconds 75 --decision-seconds 0.5 \\
      --model reports\\rl\\stage-A-round1-20261030-153225\\model.zip \\
      --contract reports\\rl\\stage-A-round1-20261030-153225\\action-contract.json \\
      --game-dir test-game-build --port 14531 --max-runtime-minutes 20
"""
import argparse
import hashlib
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

from capture_baseline import global_memory  # noqa: E402
from monitor_build import nvidia_quick  # noqa: E402
import map_probe as MP  # noqa: E402
import psutil  # noqa: E402

TRAIN_SCRIPT = THIS_DIR / 'train_build_v1.py'
MONITOR_SCRIPT = THIS_DIR / 'monitor_build.py'
SNAPSHOT_SCRIPT = THIS_DIR / 'capture_baseline.py'
REWARD_CONFIG = ROOT / 'training' / 'build_curriculum' / 'reward_build_v1.json'

# 训练期间资源保护阈值（任务书第六步）
SAFETY = {
    'phys_avail_min_mb': 4 * 1024,
    'commit_pct_max': 80.0,
    'gpu_dedicated_used_max_mb': 7.2 * 1024,
    'gpu_free_min_mb': 819.2,
}

PROTECTED_FILES = [
    'config/rewards.json',
    'config/rewards-combat.json',
    'config/spawn.ini',
    'reports/rl/stage-A-round1-20261030-153225/model.zip',
    'reports/rl/stage-A-round1-20261030-153225/action-contract.json',
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


def find_workloads():
    games, trainers = [], []
    me = set()
    try:
        proc = psutil.Process()
        while proc is not None:
            me.add(proc.pid)
            proc = proc.parent()
    except Exception:  # noqa: BLE001
        pass
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            pid = proc.pid
            name = (proc.info.get('name') or '').lower()
            cmd = ' '.join(proc.info.get('cmdline') or [])
        except Exception:  # noqa: BLE001
            continue
        if pid in me:
            continue
        if name.startswith('gamemd'):
            games.append({'pid': pid, 'name': proc.info.get('name')})
            continue
        # 只把 python 解释器进程当作训练进程，避免把命令行里带脚本名的 pwsh/node 误判
        if not name.startswith('python'):
            continue
        if 'train_build_v1.py' in cmd or 'train_shared.py' in cmd or \
                ('dsh_rl' in cmd and 'train.py' in cmd):
            trainers.append({'pid': pid, 'cmdline': cmd[:200]})
    return games, trainers


def preflight():
    mem = global_memory()
    gpu = nvidia_quick()
    games, trainers = find_workloads()
    reasons = []
    if mem['phys_avail_mb'] < 6 * 1024:
        reasons.append('phys_avail=%.1fGB < 6GB' % (mem['phys_avail_mb'] / 1024.0))
    if mem['commit_pct'] is not None and mem['commit_pct'] > 80.0:
        reasons.append('commit_pct=%.1f%% > 80%%' % mem['commit_pct'])
    if gpu.get('gpu_state') == 'OK' and gpu['gpu_free_mb'] < 1024.0:
        reasons.append('gpu_free=%.0fMB < 1024MB' % gpu['gpu_free_mb'])
    if games:
        reasons.append('game processes running: %s' % [g['pid'] for g in games])
    if trainers:
        reasons.append('training processes running: %s' % [t['pid'] for t in trainers])
    return {
        'ok': not reasons,
        'reasons': reasons,
        'memory': {k: round(v, 1) if isinstance(v, (int, float)) else v
                   for k, v in mem.items()},
        'gpu': {k: v for k, v in gpu.items() if k != 'gpu_power_w'},
        'games': games, 'trainers': trainers,
        'checked_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
    }


def snapshot(python, run, tag):
    """调用 capture_baseline.py 保存资源快照（<run>/snapshots/<tag>.json）。"""
    try:
        proc = subprocess.run(
            [python, str(SNAPSHOT_SCRIPT), '--out-dir', str(run),
             '--samples', '1', '--interval', '0', '--tag', tag],
            capture_output=True, text=True, timeout=180, cwd=str(ROOT))
        return {'tag': tag, 'returncode': proc.returncode,
                'stdout_tail': (proc.stdout or '')[-800:],
                'stderr_tail': (proc.stderr or '')[-800:]}
    except Exception as exc:  # noqa: BLE001
        return {'tag': tag, 'error': repr(exc)}


def cleanup_leftover_games():
    killed = []
    for proc in psutil.process_iter(['pid', 'name']):
        try:
            name = (proc.info.get('name') or '').lower()
        except Exception:  # noqa: BLE001
            continue
        if name.startswith('gamemd'):
            try:
                proc.kill()
                killed.append(proc.pid)
            except Exception:  # noqa: BLE001
                pass
    return killed


def collect_artifacts(run):
    wanted = ['run.json', 'model.zip', 'checkpoint.zip', 'progress.json',
              'build-events.jsonl', 'monitor.csv', 'plan.json',
              'model_init_R1copy.zip', 'model_init_action-contract.json',
              'reward_build_v1.json', 'spawn.ini', 'train_stdout.log',
              'monitor/monitor_summary.json', 'monitor/build_monitor.csv',
              'monitor/monitor_events.jsonl',
              'snapshots/before_training.json', 'snapshots/after_training.json']
    artifacts = {}
    for rel in wanted:
        path = run / rel
        if path.exists() and path.is_file():
            artifacts[rel] = {'bytes': path.stat().st_size, 'sha256': sha256(path)}
    return artifacts


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--name', default='build_v1')
    ap.add_argument('--out-root', default='experiments/build_training_v1')
    ap.add_argument('--model', default='reports/rl/stage-A-round1-20261030-153225/model.zip')
    ap.add_argument('--contract',
                    default='reports/rl/stage-A-round1-20261030-153225/action-contract.json')
    ap.add_argument('--steps', type=int, default=320)
    ap.add_argument('--episode-seconds', type=float, default=75.0)
    ap.add_argument('--decision-seconds', type=float, default=0.5)
    ap.add_argument('--seed', type=int, default=11)
    ap.add_argument('--game-dir', default='test-game-build')
    ap.add_argument('--port', type=int, default=14531)
    ap.add_argument('--instance', type=int, default=9)
    ap.add_argument('--max-runtime-minutes', type=float, default=20.0)
    ap.add_argument('--python', default=sys.executable)
    a = ap.parse_args()

    out_root = (ROOT / a.out_root).resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    run = out_root / (time.strftime('%Y%m%d-%H%M%S') + '-' + a.name)
    run.mkdir(parents=True, exist_ok=False)
    game_dir = (ROOT / a.game_dir).resolve() if not pathlib.Path(a.game_dir).is_absolute() \
        else pathlib.Path(a.game_dir)

    manifest = {
        'run_dir': str(run), 'name': a.name, 'created_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'parameters': vars(a), 'safety_thresholds': SAFETY,
    }

    # ---------------- 1. 预检 ----------------
    pf = preflight()
    save_json(run / 'preflight.json', pf)
    if not pf['ok']:
        manifest.update({'status': 'BLOCKED_BY_PREFLIGHT', 'preflight': pf})
        save_json(run / 'run_manifest.json', manifest)
        print('[orchestrator] BLOCKED_BY_PREFLIGHT:', json.dumps(pf['reasons'],
                                                                 ensure_ascii=False))
        return 3
    print('[orchestrator] preflight PASS', flush=True)

    train = None
    monitor = None
    train_log = monitor_log = None
    timed_out = False
    try:
        # ---------------- 2. 复制 checkpoint（不覆盖原模型）----------------
        model_src = (ROOT / a.model).resolve() if not pathlib.Path(a.model).is_absolute() \
            else pathlib.Path(a.model)
        contract_src = (ROOT / a.contract).resolve() \
            if not pathlib.Path(a.contract).is_absolute() else pathlib.Path(a.contract)
        for src in (model_src, contract_src, REWARD_CONFIG):
            if not src.exists():
                raise SystemExit('missing required input: %s' % src)
        model_copy = run / 'model_init_R1copy.zip'
        contract_copy = run / 'model_init_action-contract.json'
        shutil.copy2(model_src, model_copy)
        shutil.copy2(contract_src, contract_copy)
        reward_copy = run / 'reward_build_v1.json'
        shutil.copy2(REWARD_CONFIG, reward_copy)

        # ---------------- 3. 无敌人地图配置（方案 A1）----------------
        variant = MP.variant_spec('A1_no_enemy')
        written = MP.write_variant_files(game_dir, variant)
        spawn_copy = run / 'spawn.ini'
        shutil.copy2(game_dir / 'spawn.ini', spawn_copy)

        # ---------------- 4. 计划与证据哈希 ----------------
        plan = {
            'run_dir': str(run), 'name': a.name,
            'created_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'python': a.python, 'python_version': sys.version.split()[0],
            'command_training': None, 'command_monitor': None,
            'map_variant': {'name': variant['name'], 'description': variant['description'],
                            'written': {k: v for k, v in written.items()
                                        if k != 'spawn.ini'},
                            'spawn_ini': written['spawn.ini']},
            'reward_config': {'path': str(REWARD_CONFIG), 'sha256': sha256(REWARD_CONFIG),
                              'copied_to': str(reward_copy),
                              'independent_from_v4': True},
            'inputs': {
                'model': {'source': str(model_src), 'copy': str(model_copy),
                          'sha256': sha256(model_copy),
                          'source_sha256': sha256(model_src)},
                'contract': {'source': str(contract_src), 'copy': str(contract_copy),
                             'sha256': sha256(contract_copy)},
            },
            'protected_files_sha256_before': {
                rel: (sha256(ROOT / rel) if (ROOT / rel).exists() else None)
                for rel in PROTECTED_FILES},
            'game': {
                'game_dir': str(game_dir),
                'exe': str(game_dir / 'gamemd-spawn-ra2yrcpp.exe'),
                'exe_sha256': sha256(game_dir / 'gamemd-spawn-ra2yrcpp.exe'),
                'bridge_dll_sha256': sha256(game_dir / 'libra2yrcpp.dll'),
                'spawn_ini_sha256': sha256(game_dir / 'spawn.ini'),
                'spawnmap_sha256': sha256(game_dir / 'spawnmap.ini'),
                'ra2yrcpp_json': (game_dir / 'ra2yrcpp.json').read_text(
                    encoding='utf-8', errors='replace'),
                'port': a.port,
            },
            'training': {'steps_requested': a.steps,
                         'episode_seconds': a.episode_seconds,
                         'decision_seconds': a.decision_seconds,
                         'seed': a.seed, 'instance': a.instance,
                         'max_runtime_minutes': a.max_runtime_minutes},
            'new_source_sha256': {
                name: sha256(THIS_DIR / name) for name in (
                    'build_reward.py', 'build_env.py', 'train_build_v1.py',
                    'monitor_build.py', 'run_build_experiment.py',
                    'map_probe.py', 'capture_baseline.py')},
            'snapshot_script': str(SNAPSHOT_SCRIPT),
            'safety_thresholds': SAFETY,
        }
        manifest['plan'] = plan
        save_json(run / 'plan.json', plan)

        snapshot_before = snapshot(a.python, run, 'before_training')

        # ---------------- 5. 启动训练 + 监控 ----------------
        train_cmd = [
            a.python, str(TRAIN_SCRIPT),
            '--out', str(run), '--resume', str(model_copy),
            '--catalog-contract', str(contract_copy),
            '--steps', str(a.steps),
            '--episode-seconds', str(a.episode_seconds),
            '--decision-seconds', str(a.decision_seconds),
            '--seed', str(a.seed),
            '--game-dir', str(game_dir), '--port', str(a.port),
            '--instance', str(a.instance)]
        print('[orchestrator] launching training: %s' % ' '.join(train_cmd), flush=True)
        train_log = (run / 'train_stdout.log').open('wb')
        train = subprocess.Popen(train_cmd, cwd=str(ROOT), stdout=train_log,
                                 stderr=subprocess.STDOUT)
        plan['command_training'] = train_cmd
        save_json(run / 'plan.json', plan)

        monitor_cmd = [
            a.python, str(MONITOR_SCRIPT),
            '--out-dir', str(run / 'monitor'), '--run-dir', str(run),
            '--label', a.name, '--trainer-pid', str(train.pid),
            '--interval', '10', '--until-file', str(run / 'MONITOR_STOP'),
            '--safety', json.dumps(SAFETY)]
        print('[orchestrator] launching monitor: %s' % ' '.join(monitor_cmd), flush=True)
        monitor_log = (run / 'monitor_stdout.log').open('wb')
        monitor = subprocess.Popen(monitor_cmd, cwd=str(ROOT), stdout=monitor_log,
                                   stderr=subprocess.STDOUT)
        plan['command_monitor'] = monitor_cmd
        save_json(run / 'plan.json', plan)

        # ---------------- 6. 等待（含超时保护）----------------
        deadline = time.monotonic() + a.max_runtime_minutes * 60.0
        while True:
            if train.poll() is not None:
                break
            if time.monotonic() >= deadline:
                timed_out = True
                (run / 'STOP').write_text('orchestrator_timeout %.0fmin'
                                          % a.max_runtime_minutes, encoding='utf-8')
                print('[orchestrator] runtime limit hit; requested graceful stop', flush=True)
                grace = time.monotonic() + 120.0
                while train.poll() is None and time.monotonic() < grace:
                    time.sleep(2.0)
                if train.poll() is None:
                    print('[orchestrator] graceful stop timeout; terminating', flush=True)
                    train.terminate()
                    try:
                        train.wait(timeout=30)
                    except Exception:  # noqa: BLE001
                        train.kill()
                break
            if monitor is not None and monitor.poll() is not None:
                print('[orchestrator] monitor exited early rc=%s' % monitor.returncode,
                      flush=True)
            time.sleep(2.0)
        train_rc = train.wait(timeout=60)
        print('[orchestrator] training exited rc=%s' % train_rc, flush=True)
    except BaseException as exc:  # noqa: BLE001
        manifest['orchestrator_error'] = '%s: %s' % (type(exc).__name__, exc)
        manifest['orchestrator_traceback'] = traceback.format_exc()[-4000:]
        train_rc = None
    finally:
        # ---------------- 7. 停止监控 / 清理残留 ----------------
        try:
            (run / 'MONITOR_STOP').write_text('training_finished', encoding='utf-8')
        except Exception:  # noqa: BLE001
            pass
        if monitor is not None:
            try:
                monitor.wait(timeout=90)
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
        snapshot_after = snapshot(a.python, run, 'after_training')

    # ---------------- 8. 汇总 manifest ----------------
    def read_json(path):
        try:
            return json.loads(pathlib.Path(path).read_text(encoding='utf-8'))
        except Exception:  # noqa: BLE001
            return None

    train_run = read_json(run / 'run.json')
    monitor_summary = read_json(run / 'monitor' / 'monitor_summary.json')
    before = read_json(run / 'snapshots' / 'before_training.json')
    after = read_json(run / 'snapshots' / 'after_training.json')

    def snapshot_digest(payload):
        if not payload:
            return None
        sample = (payload.get('samples') or [{}])[-1]
        summary = payload.get('summary') or {}
        return {'ts': sample.get('ts'),
                'memory': sample.get('memory'),
                'process_summary': sample.get('process_summary'),
                'gpu': {k: sample.get('gpu', {}).get(k) for k in
                        ('name', 'memory_total_mb', 'memory_used_mb', 'memory_free_mb',
                         'util_gpu_pct', 'temp_c')},
                'verdict': summary.get('verdict')}

    status = 'failed'
    if train_run:
        status = train_run.get('status') or 'failed'
    if timed_out and status == 'stopped_by_request':
        status = 'stopped_by_runtime_limit'
    manifest.update({
        'status': status,
        'train_exit_code': train_rc,
        'timed_out': timed_out,
        'preflight': pf,
        'train_run': train_run,
        'monitor_summary': monitor_summary,
        'snapshot_before': snapshot_before,
        'snapshot_after': snapshot_after,
        'resource_digest_before': snapshot_digest(before),
        'resource_digest_after': snapshot_digest(after),
        'leftover_games_killed': killed,
        'artifacts': collect_artifacts(run),
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
    print('[orchestrator] status=%s run=%s' % (status, run), flush=True)
    print('[orchestrator] RESULT_JSON %s' % json.dumps({
        'run_dir': str(run), 'status': status,
        'timed_out': timed_out,
        'train_exit_code': train_rc,
        'verdict': (train_run or {}).get('verdict'),
        'build_reward': (train_run or {}).get('build_reward'),
        'transitions': (train_run or {}).get('real_transitions_this_run'),
        'updates': (train_run or {}).get('optimizer_updates_this_run'),
        'parameter_l2_change': (train_run or {}).get('parameter_l2_change'),
        'monitor_stop_reason': (monitor_summary or {}).get('stop_reason'),
        'monitor_violations': len((monitor_summary or {}).get('violations') or []),
        'protected_files_unchanged': manifest.get('protected_files_unchanged'),
    }, ensure_ascii=False), flush=True)
    if status in ('complete', 'stopped_by_request'):
        return 0
    if status == 'stopped_by_runtime_limit':
        return 4
    return 1


if __name__ == '__main__':
    sys.exit(main())
