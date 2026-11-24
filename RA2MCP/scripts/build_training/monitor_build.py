"""建设训练专用资源监控器（只读采集，不修改被监控进程）。

记录内容（V2 默认每 5 秒采样，可通过 --interval 配置）：
- 系统物理内存 / 提交内存 / 可用物理内存
- 训练进程 Private Bytes / Worker（训练进程子孙）Private Bytes / 游戏进程 Private Bytes
- GPU 专用显存 / 共享显存（WDDM 计数器）/ 利用率

保护阈值：--safety JSON 可覆盖，默认使用 V1 第六步阈值；V2 使用第八节阈值：
- 提交内存 >= 85% / 物理可用 < 3GB / GPU 剩余显存 < 500MB / 2 分钟提交增长 > 2GB
超阈值：写事件 + 向 --run-dir 写 STOP 文件（训练入口原生优雅停止），不重启、不掩盖。
另记录训练进程/游戏进程退出事件（Worker 异常退出/游戏异常残留）。

输出（--out-dir）：
  build_monitor.csv / monitor_events.jsonl / monitor_summary.json
"""
import argparse
import csv
import json
import pathlib
import re
import subprocess
import sys
import time
from collections import deque

THIS_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(THIS_DIR))

from capture_baseline import global_memory, powershell_counters, counters_to_map  # noqa: E402

FAST_DIR = THIS_DIR.parents[1] / 'experiments' / 'memory_audit' / 'tools'
if str(FAST_DIR) not in sys.path:
    sys.path.insert(0, str(FAST_DIR))
try:
    from fast_procs import process_snapshot  # noqa: E402
except Exception:  # noqa: BLE001
    process_snapshot = None

MB = 1024.0 * 1024.0

DEFAULT_SAFETY = {
    'phys_avail_min_mb': 4 * 1024,
    'commit_pct_max': 80.0,
    'gpu_dedicated_used_max_mb': 7.2 * 1024,
    'gpu_free_min_mb': 819.2,
    'commit_growth_window_s': 120.0,
    'commit_growth_max_mb': 2 * 1024,
    'commit_growth_grace_s': 180.0,   # 训练启动/模型加载/首局启动的瞬态增长不计入违规
}

CSV_FIELDS = [
    'ts', 'elapsed_s', 'label',
    'phys_total_mb', 'phys_avail_mb', 'phys_used_mb', 'phys_used_pct',
    'commit_used_mb', 'commit_limit_mb', 'commit_avail_mb', 'commit_pct',
    'paged_pool_mb', 'nonpaged_pool_mb', 'system_cache_mb',
    'trainer_count', 'trainer_pids', 'trainer_private_mb', 'trainer_ws_mb',
    'worker_count', 'worker_private_mb', 'worker_ws_mb',
    'game_count', 'game_pids', 'game_private_mb', 'game_ws_mb',
    'explorer_private_mb',
    'gpu_total_mb', 'gpu_used_mb', 'gpu_free_mb', 'gpu_util_pct', 'gpu_temp_c',
    'adapter_dedicated_mb', 'adapter_shared_mb',
    'trainer_gpu_dedicated_mb', 'trainer_gpu_shared_mb',
    'game_gpu_dedicated_mb', 'game_gpu_shared_mb',
    'commit_growth_120s_mb', 'safety_violation', 'process_detail',
]


def nvidia_quick():
    try:
        out = subprocess.run(
            ['nvidia-smi', '--query-gpu=memory.total,memory.used,memory.free,'
                           'utilization.gpu,temperature.gpu,power.draw',
                       '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=20)
        if out.returncode != 0:
            return {'gpu_state': 'UNKNOWN', 'gpu_error': (out.stderr or '')[:200]}
        parts = [p.strip() for p in out.stdout.strip().splitlines()[0].split(',')]
        return {'gpu_state': 'OK', 'gpu_total_mb': float(parts[0]),
                'gpu_used_mb': float(parts[1]), 'gpu_free_mb': float(parts[2]),
                'gpu_util_pct': float(parts[3]), 'gpu_temp_c': float(parts[4]),
                'gpu_power_w': None if parts[5].upper() in ('N/A', '[N/A]')
                else float(parts[5]), 'gpu_per_process': 'UNKNOWN_WDDM'}
    except Exception as exc:  # noqa: BLE001
        return {'gpu_state': 'UNKNOWN', 'gpu_error': repr(exc)[:200]}


def _children_map(snap):
    children = {}
    for row in snap:
        children.setdefault(int(row['ppid']), []).append(int(row['pid']))
    return children


def classify(snap, trainer_pid):
    by_pid = {int(r['pid']): r for r in snap}
    children = _children_map(snap)
    games = [int(r['pid']) for r in snap
             if (r.get('name') or '').lower().startswith('gamemd')]
    trainers = [int(trainer_pid)] if trainer_pid and int(trainer_pid) in by_pid else []
    descendants, queue, seen = [], list(trainers), set(trainers)
    while queue:
        pid = queue.pop(0)
        for child in children.get(pid, []):
            if child in seen:
                continue
            seen.add(child)
            queue.append(child)
            if child not in games:
                descendants.append(child)
    return by_pid, trainers, descendants, games


def agg(by_pid, pids):
    private = ws = 0.0
    for pid in pids:
        row = by_pid.get(pid)
        if row is None:
            continue
        private += float(row.get('private_mb') or 0)
        ws += float(row.get('ws_mb') or 0)
    return round(private, 1), round(ws, 1)


def _wddm_maps(counters):
    adapter_ded = adapter_sha = 0.0
    per_pid_ded, per_pid_sha = {}, {}
    if not counters or counters.get('state') != 'OK':
        return {'state': 'UNKNOWN', 'adapter_dedicated_mb': 'UNKNOWN',
                'adapter_shared_mb': 'UNKNOWN', 'per_pid_dedicated': {},
                'per_pid_shared': {}}
    for item in counters.get('gpu_adapter_dedicated') or []:
        adapter_ded += float(item.get('value') or 0)
    for item in counters.get('gpu_adapter_shared') or []:
        adapter_sha += float(item.get('value') or 0)
    for key, target in (('gpu_process_dedicated', per_pid_ded),
                        ('gpu_process_shared', per_pid_sha)):
        for item in counters.get(key) or []:
            match = re.search(r'pid_(\d+)', item.get('instance') or '')
            if not match:
                continue
            pid = int(match.group(1))
            target[pid] = target.get(pid, 0.0) + float(item.get('value') or 0)
    return {
        'state': 'OK_COUNTER',
        'adapter_dedicated_mb': round(adapter_ded / MB, 1),
        'adapter_shared_mb': round(adapter_sha / MB, 1),
        'per_pid_dedicated': {str(k): round(v / MB, 1) for k, v in per_pid_ded.items()},
        'per_pid_shared': {str(k): round(v / MB, 1) for k, v in per_pid_sha.items()},
        'note': 'WDDM 进程级归属可能不可读；N/A 保持 UNKNOWN，不当作 0',
    }


def sample(label, trainer_pid):
    mem = global_memory()
    counters = powershell_counters(timeout=120)
    mem_map = counters_to_map(counters.get('memory')) if counters.get('state') == 'OK' else {}
    gpu = nvidia_quick()
    wddm = _wddm_maps(counters)
    snap = process_snapshot() if process_snapshot is not None else []
    by_pid, trainers, workers, games = classify(snap, trainer_pid)
    trainer_private, trainer_ws = agg(by_pid, trainers)
    worker_private, worker_ws = agg(by_pid, workers)
    game_private, game_ws = agg(by_pid, games)
    explorer_private = sum(float(r.get('private_mb') or 0) for r in snap
                           if (r.get('name') or '').lower() == 'explorer.exe')
    detail = []
    for role, pids in (('trainer', trainers), ('worker', workers), ('game', games)):
        for pid in pids:
            row = by_pid.get(pid) or {}
            detail.append({'pid': pid, 'role': role, 'name': row.get('name') or '',
                           'private_mb': round(float(row.get('private_mb') or 0), 1),
                           'ws_mb': round(float(row.get('ws_mb') or 0), 1)})

    def pool(name):
        value = mem_map.get(name)
        return 'UNKNOWN' if value is None else round(value / MB, 1)

    def gpu_pid_sum(pid_map, pids):
        return round(sum(pid_map.get(str(p), 0.0) for p in pids), 1)

    return {
        'ts': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'label': label,
        'phys_total_mb': round(mem['phys_total_mb'], 1),
        'phys_avail_mb': round(mem['phys_avail_mb'], 1),
        'phys_used_mb': round(mem['phys_used_mb'], 1),
        'phys_used_pct': mem['phys_used_pct'],
        'commit_used_mb': round(mem['commit_used_mb'], 1),
        'commit_limit_mb': round(mem['commit_limit_mb'], 1),
        'commit_avail_mb': round(mem['commit_avail_mb'], 1),
        'commit_pct': mem['commit_pct'],
        'paged_pool_mb': pool('pool paged bytes'),
        'nonpaged_pool_mb': pool('pool nonpaged bytes'),
        'system_cache_mb': pool('system cache resident bytes'),
        'trainer_count': len(trainers), 'trainer_pids': ';'.join(map(str, trainers)),
        'trainer_private_mb': trainer_private, 'trainer_ws_mb': trainer_ws,
        'worker_count': len(workers), 'worker_private_mb': worker_private,
        'worker_ws_mb': worker_ws,
        'game_count': len(games), 'game_pids': ';'.join(map(str, games)),
        'game_private_mb': game_private, 'game_ws_mb': game_ws,
        'explorer_private_mb': round(explorer_private, 1),
        'gpu_total_mb': gpu.get('gpu_total_mb', 'UNKNOWN'),
        'gpu_used_mb': gpu.get('gpu_used_mb', 'UNKNOWN'),
        'gpu_free_mb': gpu.get('gpu_free_mb', 'UNKNOWN'),
        'gpu_util_pct': gpu.get('gpu_util_pct', 'UNKNOWN'),
        'gpu_temp_c': gpu.get('gpu_temp_c', 'UNKNOWN'),
        'adapter_dedicated_mb': wddm['adapter_dedicated_mb'],
        'adapter_shared_mb': wddm['adapter_shared_mb'],
        'trainer_gpu_dedicated_mb': gpu_pid_sum(wddm['per_pid_dedicated'], trainers),
        'trainer_gpu_shared_mb': gpu_pid_sum(wddm['per_pid_shared'], trainers),
        'game_gpu_dedicated_mb': gpu_pid_sum(wddm['per_pid_dedicated'], games),
        'game_gpu_shared_mb': gpu_pid_sum(wddm['per_pid_shared'], games),
        'safety_violation': '',
        'process_detail': json.dumps(detail, ensure_ascii=False),
    }, mem, gpu, {'trainers': trainers, 'workers': workers, 'games': games}


def resolve_trainer_pid(configured_pid, run_dir):
    """uv/venv 启动器会 re-exec，真实训练 pid 由训练入口写入 trainer_pid.txt。"""
    pid_file = pathlib.Path(run_dir) / 'trainer_pid.txt'
    try:
        if pid_file.exists():
            value = int(pid_file.read_text(encoding='utf-8').strip())
            if value > 0:
                return value
    except Exception:  # noqa: BLE001
        pass
    return int(configured_pid)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out-dir', required=True)
    ap.add_argument('--run-dir', required=True)
    ap.add_argument('--trainer-pid', type=int, required=True)
    ap.add_argument('--label', default='build_training')
    ap.add_argument('--interval', type=float, default=10.0)
    ap.add_argument('--duration', type=float, default=0.0)
    ap.add_argument('--until-file', default=None)
    ap.add_argument('--safety', default=None)
    ap.add_argument('--no-safety-stop', action='store_true')
    args = ap.parse_args(argv)

    out = pathlib.Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    run_dir = pathlib.Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out / 'build_monitor.csv'
    events_path = out / 'monitor_events.jsonl'
    summary_path = out / 'monitor_summary.json'
    safety = dict(DEFAULT_SAFETY)
    if args.safety:
        safety.update(json.loads(args.safety))

    def event(name, detail=None):
        rec = {'ts': time.strftime('%Y-%m-%dT%H:%M:%S'), 'event': name,
               'detail': detail or {}}
        with events_path.open('a', encoding='utf-8') as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + '\n')
        print('[monitor] %s %s' % (name, json.dumps(detail or {}, ensure_ascii=False)),
              flush=True)

    with csv_path.open('w', newline='', encoding='utf-8') as fh:
        csv.DictWriter(fh, fieldnames=CSV_FIELDS).writeheader()

    start = time.monotonic()
    peaks = {'phys_used_mb': 0.0, 'commit_used_mb': 0.0, 'commit_pct': 0.0,
             'trainer_private_mb': 0.0, 'worker_private_mb': 0.0,
             'game_private_mb': 0.0, 'gpu_used_mb': 0.0,
             'adapter_dedicated_mb': 0.0, 'adapter_shared_mb': 0.0}
    violations = []
    stop_written = False
    stop_reason = None
    last_counts = None
    commit_window = deque()
    last_resolved = None
    event('monitor_start', {'label': args.label, 'interval_s': args.interval,
                            'trainer_pid': args.trainer_pid, 'safety': safety,
                            'run_dir': str(run_dir)})
    try:
        while True:
            t = time.monotonic()
            elapsed = t - start
            resolved = resolve_trainer_pid(args.trainer_pid, run_dir)
            if resolved != last_resolved:
                event('trainer_pid_resolved', {'configured': args.trainer_pid,
                                               'resolved': resolved})
                last_resolved = resolved
            row, mem, gpu, counts = sample(args.label, resolved)
            row['elapsed_s'] = round(elapsed, 1)
            commit_window.append((t, mem['commit_used_mb']))
            while commit_window and \
                    t - commit_window[0][0] > safety['commit_growth_window_s']:
                commit_window.popleft()
            commit_growth_mb = mem['commit_used_mb'] - commit_window[0][1]
            row['commit_growth_120s_mb'] = round(commit_growth_mb, 1)
            violations_now = []
            if mem['phys_avail_mb'] < safety['phys_avail_min_mb']:
                violations_now.append('phys_avail<%.0fMB' % safety['phys_avail_min_mb'])
            if mem['commit_pct'] is not None and \
                    mem['commit_pct'] > safety['commit_pct_max']:
                violations_now.append('commit_pct>%.0f%%' % safety['commit_pct_max'])
            if commit_growth_mb > safety['commit_growth_max_mb'] and \
                    elapsed >= float(safety.get('commit_growth_grace_s', 180.0)):
                violations_now.append('commit_growth>%.0fMB/%.0fs'
                                      % (safety['commit_growth_max_mb'],
                                         safety['commit_growth_window_s']))
            if gpu.get('gpu_state') == 'OK':
                if gpu['gpu_used_mb'] > safety['gpu_dedicated_used_max_mb']:
                    violations_now.append('gpu_dedicated_used>%.0fMB'
                                          % safety['gpu_dedicated_used_max_mb'])
                if gpu['gpu_free_mb'] < safety['gpu_free_min_mb']:
                    violations_now.append('gpu_free<%.0fMB' % safety['gpu_free_min_mb'])
            if violations_now:
                row['safety_violation'] = ';'.join(violations_now)
                violations.append({'ts': row['ts'], 'elapsed_s': row['elapsed_s'],
                                   'reasons': violations_now,
                                   'commit_pct': mem['commit_pct'],
                                   'phys_avail_mb': round(mem['phys_avail_mb'], 1),
                                   'gpu_used_mb': gpu.get('gpu_used_mb')})
                event('SAFETY_VIOLATION', {'reasons': violations_now,
                                           'commit_pct': mem['commit_pct'],
                                           'phys_avail_mb': round(mem['phys_avail_mb'], 1),
                                           'gpu_used_mb': gpu.get('gpu_used_mb')})
                if not args.no_safety_stop and not stop_written:
                    try:
                        (run_dir / 'STOP').write_text(
                            'monitor_safety:%s' % ';'.join(violations_now),
                            encoding='utf-8')
                        stop_written = True
                        event('SAFETY_STOP_WRITTEN',
                              {'reason': ';'.join(violations_now)})
                    except Exception as exc:  # noqa: BLE001
                        event('stop_file_failed', {'error': repr(exc)})
            current = {'trainers': len(counts['trainers']),
                       'workers': len(counts['workers']),
                       'games': len(counts['games'])}
            stop_requested = bool(args.until_file and
                                  pathlib.Path(args.until_file).exists())
            if last_counts is not None:
                if current['games'] < last_counts['games']:
                    if current['trainers'] > 0 and not stop_requested:
                        event('game_process_exited_abnormal',
                              {'from': last_counts['games'],
                               'to': current['games'],
                               'detail': row['process_detail']})
                        stop_reason = stop_reason or 'game_process_exited_abnormal'
                    else:
                        event('game_process_exited_normal',
                              {'from': last_counts['games'], 'to': current['games']})
                if current['trainers'] < last_counts['trainers']:
                    event('trainer_process_exited', {'from': last_counts['trainers'],
                                                     'to': current['trainers']})
                    stop_reason = stop_reason or 'trainer_process_exited'
                if current['workers'] != last_counts['workers']:
                    event('worker_count_change', {'from': last_counts['workers'],
                                                  'to': current['workers']})
            last_counts = current
            with csv_path.open('a', newline='', encoding='utf-8') as fh:
                csv.DictWriter(fh, fieldnames=CSV_FIELDS,
                               extrasaction='ignore').writerow(row)
            for key in peaks:
                value = row.get(key)
                if value not in (None, 'UNKNOWN'):
                    peaks[key] = max(peaks[key], float(value))
            if int(elapsed // max(1.0, args.interval)) % 3 == 0 and elapsed > 0:
                print('[monitor] t=%.0fs phys_avail=%.1fGB commit=%.1f%% train=%d '
                      'workers=%d games=%d gpu=%sMB' % (
                          elapsed, mem['phys_avail_mb'] / 1024.0, mem['commit_pct'],
                          current['trainers'], current['workers'], current['games'],
                          gpu.get('gpu_used_mb', 'UNKNOWN')), flush=True)
            if stop_reason in ('game_process_exited_abnormal',
                               'trainer_process_exited'):
                event('monitor_stop', {'reason': stop_reason})
                break
            if args.until_file and pathlib.Path(args.until_file).exists():
                stop_reason = 'until_file'
                event('monitor_stop', {'reason': stop_reason})
                break
            if args.duration and elapsed >= args.duration:
                stop_reason = 'duration'
                event('monitor_stop', {'reason': stop_reason})
                break
            sleep_s = args.interval - (time.monotonic() - t)
            if sleep_s > 0:
                time.sleep(sleep_s)
    except KeyboardInterrupt:
        stop_reason = 'keyboard_interrupt'
        event('monitor_stop', {'reason': stop_reason})
    finally:
        rows = []
        try:
            with csv_path.open(encoding='utf-8') as fh:
                rows = list(csv.DictReader(fh))
        except Exception:  # noqa: BLE001
            pass

        def series(key):
            values = []
            for r in rows:
                try:
                    values.append(float(r[key]))
                except Exception:  # noqa: BLE001
                    continue
            return values
        keys = ('phys_avail_mb', 'commit_used_mb', 'commit_pct', 'trainer_private_mb',
                'worker_private_mb', 'game_private_mb', 'gpu_used_mb',
                'adapter_dedicated_mb', 'adapter_shared_mb')
        summary = {
            'label': args.label,
            'ended_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'duration_s': round(time.monotonic() - start, 1),
            'stop_reason': stop_reason,
            'samples': len(rows),
            'interval_s': args.interval,
            'peaks': {k: round(v, 1) for k, v in peaks.items()},
            'first': {k: (series(k)[0] if series(k) else None) for k in keys},
            'last': {k: (series(k)[-1] if series(k) else None) for k in keys},
            'deltas': {k: (round(series(k)[-1] - series(k)[0], 1) if series(k) else None)
                       for k in keys},
            'violations': violations,
            'safety_thresholds': safety,
            'wddm_process_attribution': 'UNKNOWN_WDDM',
            'gpu_shared_note': 'WDDM 共享显存取 GPU Adapter/Process Memory Shared Usage '
                               '计数器；N/A 保持 UNKNOWN',
        }
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                encoding='utf-8')
        print('[monitor] summary -> %s' % summary_path, flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
