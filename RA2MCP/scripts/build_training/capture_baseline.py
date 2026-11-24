"""RA2MCP「无敌人建设训练 V1」系统资源基线 / 阶段快照采集（只读，不启动游戏/训练）。

采集内容（每次样本）：
- Windows 物理内存 / 提交内存（GlobalMemoryStatusEx）
- 内存计数器（\\Memory\\*）：Paged/Nonpaged Pool、System Cache、Modified Page List、
  Standby Cache、Free&Zero Page List 等
- 内存压缩状态（Get-MMAgent）与 Memory Compression 进程
- 页文件计数器（\\Paging File(_Total)\\*）
- 全部进程私有内存 / Working Set / 句柄 / 线程（fast_procs NtQuerySystemInformation），
  TOP30 Private、TOP30 WS，以及 Python / Node / DSH / Codex / WSL / 游戏 / 桌面 关键进程
- GPU：nvidia-smi 查询（型号/显存/利用率/温度/功耗/计算进程）+ WDDM
  \\GPU Process Memory(*)\\Dedicated|Shared Usage、\\GPU Adapter Memory(*)\\* 计数器

输出：
  基线模式（默认）：<out-dir>/baseline_before.json、baseline_processes.csv、
                    baseline_gpu.txt、baseline_report.md
  快照模式（--tag X）：<out-dir>/snapshots/X.json、snapshots/X_gpu.txt

注意：
- 需要完整进程可见性（DSH 默认 workspace-write 沙箱只暴露少量进程，会漏 node/explorer 等；
  本脚本应在获批的完整只读权限下运行）。
- WDDM 下进程级显存归属可能为 N/A / 不可读：一律保留 UNKNOWN，不当作 0。

用法：
  .venv-rl\\Scripts\\python.exe scripts\\build_training\\capture_baseline.py \
      --out-dir reports\\build_training --samples 3 --interval 30
  ... --tag before_map_test --samples 1
"""
import argparse
import csv
import ctypes
import json
import os
import pathlib
import platform
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta

try:
    import psutil
except Exception:  # noqa: BLE001
    psutil = None

THIS_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parents[1]
FAST_PROCS_DIR = REPO_ROOT / 'experiments' / 'memory_audit' / 'tools'
if str(FAST_PROCS_DIR) not in sys.path:
    sys.path.insert(0, str(FAST_PROCS_DIR))
try:
    from fast_procs import process_snapshot  # noqa: E402
except Exception:  # noqa: BLE001
    process_snapshot = None

TS_FMT = '%Y-%m-%dT%H:%M:%S'
MB = 1024.0 * 1024.0
GB = 1024.0 * 1024.0 * 1024.0

# 用户任务书 §七 的资源保护阈值（训练期间每 10s 监控使用）
TRAIN_THRESHOLDS = {
    'phys_avail_min_mb': 4 * 1024,        # 可用物理内存低于 4GB：暂停训练
    'commit_pct_max': 80.0,               # 提交量超过提交上限 80%：暂停训练
    'gpu_dedicated_used_max_mb': 7.2 * 1024,  # 专用显存超过 7.2GB：暂停训练
}

# 历史 memory_audit 安全门（更严格，用于对照）
MEMORY_AUDIT_GATE = {
    'commit_pct_max': 80.0,
    'commit_avail_min_gb': 20.0,
    'phys_avail_min_gb': 6.0,
    'gpu_free_min_mb': 1024.0,
}

POWERSHELL_COUNTERS = r"""
$ErrorActionPreference = 'SilentlyContinue'
function Get-CS([string]$set) {
  $r = @()
  try {
    $samples = (Get-Counter $set -MaxSamples 1 -ErrorAction Stop).CounterSamples
    foreach ($s in $samples) {
      $inst = ''
      try { $inst = [string]$s.InstanceName } catch {}
      $r += @{ name = (($s.Path -split '\\')[-1]); instance = $inst; value = [double]$s.CookedValue }
    }
  } catch {}
  return ,$r
}
$out = @{}
$out.memory = Get-CS '\Memory\*'
$out.paging_file = Get-CS '\Paging File(_Total)\*'
$out.gpu_process_dedicated = Get-CS '\GPU Process Memory(*)\Dedicated Usage'
$out.gpu_process_shared = Get-CS '\GPU Process Memory(*)\Shared Usage'
$out.gpu_process_local = Get-CS '\GPU Process Memory(*)\Local Usage'
$out.gpu_process_nonlocal = Get-CS '\GPU Process Memory(*)\Non Local Usage'
$out.gpu_adapter_dedicated = Get-CS '\GPU Adapter Memory(*)\Dedicated Usage'
$out.gpu_adapter_shared = Get-CS '\GPU Adapter Memory(*)\Shared Usage'
$out.gpu_engine_util = Get-CS '\GPU Engine(*)\Utilization Percentage'
$out.mm_agent = $null
try { $out.mm_agent = (Get-MMAgent | ConvertTo-Json -Compress -Depth 3) } catch {}
$out | ConvertTo-Json -Depth 6 -Compress
"""


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ('dwLength', ctypes.c_ulong),
        ('dwMemoryLoad', ctypes.c_ulong),
        ('ullTotalPhys', ctypes.c_ulonglong),
        ('ullAvailPhys', ctypes.c_ulonglong),
        ('ullTotalPageFile', ctypes.c_ulonglong),
        ('ullAvailPageFile', ctypes.c_ulonglong),
        ('ullTotalVirtual', ctypes.c_ulonglong),
        ('ullAvailVirtual', ctypes.c_ulonglong),
        ('ullAvailExtendedVirtual', ctypes.c_ulonglong),
    ]


def global_memory():
    st = MEMORYSTATUSEX()
    st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
        raise OSError('GlobalMemoryStatusEx failed')
    phys_total = st.ullTotalPhys / MB
    phys_avail = st.ullAvailPhys / MB
    commit_limit = st.ullTotalPageFile / MB
    commit_avail = st.ullAvailPageFile / MB
    return {
        'phys_total_mb': round(phys_total, 1),
        'phys_avail_mb': round(phys_avail, 1),
        'phys_used_mb': round(phys_total - phys_avail, 1),
        'phys_used_pct': round(100.0 * (phys_total - phys_avail) / phys_total, 2) if phys_total else None,
        'commit_limit_mb': round(commit_limit, 1),
        'commit_avail_mb': round(commit_avail, 1),
        'commit_used_mb': round(commit_limit - commit_avail, 1),
        'commit_pct': round(100.0 * (commit_limit - commit_avail) / commit_limit, 2) if commit_limit else None,
        'mem_load_pct': int(st.dwMemoryLoad),
        'virtual_total_mb': round(st.ullTotalVirtual / MB, 1),
        'virtual_avail_mb': round(st.ullAvailVirtual / MB, 1),
    }


def powershell_counters(timeout=120):
    try:
        out = subprocess.run(
            ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
             POWERSHELL_COUNTERS],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=timeout)
        txt = (out.stdout or '').strip()
        if not txt:
            return {'state': 'UNKNOWN', 'error': 'empty stdout',
                    'stderr': (out.stderr or '')[:400]}
        data = json.loads(txt)
        data['state'] = 'OK'
        return data
    except Exception as exc:  # noqa: BLE001
        return {'state': 'UNKNOWN', 'error': repr(exc)[:400]}


def nvidia_smi_text():
    parts = []
    queries = [
        ('gpu', 'nvidia-smi --query-gpu=index,name,driver_version,memory.total,memory.used,'
                'memory.free,utilization.gpu,utilization.memory,temperature.gpu,power.draw,'
                'power.limit,power.default_limit,clocks.sm,clocks.mem --format=csv,noheader,nounits'),
        ('compute_apps', 'nvidia-smi --query-compute-apps=pid,process_name,used_memory '
                         '--format=csv,noheader,nounits'),
    ]
    for label, cmd in queries:
        try:
            p = subprocess.run(cmd.split(), capture_output=True, text=True, timeout=30)
            txt = (p.stdout or '').strip()
            err = (p.stderr or '').strip()
            parts.append('### %s (exit=%s)\n%s\n%s' % (
                label, p.returncode, txt or '<empty>',
                ('stderr: ' + err[:500]) if err else ''))
        except Exception as exc:  # noqa: BLE001
            parts.append('### %s\nUNKNOWN: %r' % (label, exc))
    try:
        p = subprocess.run(['nvidia-smi', '--display=MEMORY,UTILIZATION,TEMPERATURE,POWER,PROCESSES',
                            '-q'], capture_output=True, text=True, timeout=30)
        parts.append('### nvidia-smi -q --display=... (节选)\n' + (p.stdout or '')[:12000])
    except Exception as exc:  # noqa: BLE001
        parts.append('### nvidia-smi -q\nUNKNOWN: %r' % (exc,))
    return '\n\n'.join(parts)


def wddm_text(counters):
    """WDDM 显存计数器文本（适配器 + 进程级，N/A 不当 0）。"""
    lines = ['### WDDM 显存计数器（Get-Counter）']
    if not counters or counters.get('state') != 'OK':
        lines.append('UNKNOWN: 计数器不可读（%s）' % (
            (counters or {}).get('error', 'no counters')))
        return '\n'.join(lines)
    lines.append('WDDM Adapter 级：')
    for key, label in (('gpu_adapter_dedicated', 'Dedicated'),
                       ('gpu_adapter_shared', 'Shared')):
        for it in counters.get(key) or []:
            lines.append('  adapter[%s] %s Usage = %.1f MB' % (
                it.get('instance'), label, float(it.get('value') or 0) / MB))
    proc_ded = {}
    proc_sha = {}
    for it in counters.get('gpu_process_dedicated') or []:
        m = re.search(r'pid_(\d+)', it.get('instance') or '')
        if m:
            proc_ded[int(m.group(1))] = proc_ded.get(int(m.group(1)), 0.0) + float(it.get('value') or 0)
    for it in counters.get('gpu_process_shared') or []:
        m = re.search(r'pid_(\d+)', it.get('instance') or '')
        if m:
            proc_sha[int(m.group(1))] = proc_sha.get(int(m.group(1)), 0.0) + float(it.get('value') or 0)
    pids = sorted(set(proc_ded) | set(proc_sha),
                  key=lambda p: -(proc_ded.get(p, 0.0) + proc_sha.get(p, 0.0)))
    lines.append('WDDM 进程级（按 Dedicated+Shared 降序，前 25 个）：')
    for pid in pids[:25]:
        d = proc_ded.get(pid, 0.0) / MB
        s = proc_sha.get(pid, 0.0) / MB
        if d + s < 0.1:
            continue
        lines.append('  pid=%-6d dedicated=%8.1f MB  shared=%8.1f MB' % (pid, d, s))
    lines.append('注：nvidia-smi --query-compute-apps 的 used_memory 在 WDDM 下多为 '
                 'N/A / [Insufficient Permissions]；一律保持 UNKNOWN，不当作 0。')
    return '\n'.join(lines)


def gpu_txt_for_sample(sample):
    return '\n\n'.join([sample.get('gpu_text') or '',
                        wddm_text(sample.get('counters')),
                        '### Get-MMAgent（内存压缩状态，需提升权限；失败为 UNKNOWN）\n%s'
                        % (sample.get('counters', {}).get('mm_agent') or 'UNKNOWN')])


def gpu_summary(text):
    """从 nvidia-smi 文本里解析第一块 GPU 的关键指标（用于 JSON）。"""
    res = {'state': 'UNKNOWN', 'gpu_process_attribution': 'UNKNOWN_WDDM'}
    for line in text.splitlines():
        if line.startswith('### gpu (exit=0)'):
            res['state'] = 'OK'
        if re.match(r'^\d+,\s*NVIDIA', line) or re.match(r'^0,\s*', line):
            f = [x.strip() for x in line.split(',')]
            if len(f) >= 10:
                try:
                    res.update({
                        'index': f[0], 'name': f[1], 'driver': f[2],
                        'memory_total_mb': float(f[3]),
                        'memory_used_mb': float(f[4]),
                        'memory_free_mb': float(f[5]),
                        'util_gpu_pct': float(f[6]),
                        'util_mem_pct': float(f[7]),
                        'temp_c': float(f[8]),
                        'power_w': None if f[9].upper() in ('N/A', '[N/A]') else float(f[9]),
                    })
                except Exception:  # noqa: BLE001
                    pass
    return res


def _process_rows():
    if process_snapshot is not None:
        try:
            rows = process_snapshot()
            for r in rows:
                r['source'] = 'ntquery'
            return rows
        except Exception:  # noqa: BLE001
            pass
    if psutil is None:
        return []
    rows = []
    for p in psutil.process_iter(['pid', 'ppid', 'name', 'memory_info',
                                  'num_handles', 'num_threads']):
        try:
            info = p.info
            mi = info.get('memory_info')
            private = getattr(mi, 'private', 0) / MB if mi else 0.0
            ws = getattr(mi, 'rss', 0) / MB if mi else 0.0
            rows.append({'pid': info['pid'], 'ppid': info.get('ppid') or 0,
                         'name': info.get('name') or '',
                         'private_mb': private, 'ws_mb': ws,
                         'handles': info.get('num_handles') or 0,
                         'threads': info.get('num_threads') or 0,
                         'source': 'psutil'})
        except Exception:  # noqa: BLE001
            continue
    return rows


def _enrich(pid):
    if psutil is None:
        return {}
    try:
        p = psutil.Process(pid)
        out = {}
        try:
            out['start_time'] = datetime.fromtimestamp(p.create_time()).strftime(TS_FMT)
        except Exception:  # noqa: BLE001
            out['start_time'] = None
        try:
            out['exe'] = p.exe()
        except Exception:  # noqa: BLE001
            out['exe'] = None
        try:
            cmdline = p.cmdline()
            out['cmdline'] = ' '.join(cmdline)[:500] if cmdline else ''
        except Exception:  # noqa: BLE001
            out['cmdline'] = None
        try:
            out['username'] = p.username()
        except Exception:  # noqa: BLE001
            out['username'] = None
        return out
    except Exception:  # noqa: BLE001
        return {'start_time': None, 'exe': None, 'cmdline': None, 'username': None}


KEY_NAME_PATTERNS = [
    ('python', re.compile(r'^python', re.I)),
    ('node', re.compile(r'^node', re.I)),
    ('codex', re.compile(r'codex', re.I)),
    ('dsh', re.compile(r'dsh|deepseek|opencode', re.I)),
    ('wsl', re.compile(r'vmmem|^wsl|wslhost|wslservice', re.I)),
    ('game', re.compile(r'^gamemd', re.I)),
    ('desktop', re.compile(r'^(explorer|dwm)\.exe$', re.I)),
    ('memory_compression', re.compile(r'memory compression|memcompression', re.I)),
]


def classify(name, cmdline=''):
    for cat, pat in KEY_NAME_PATTERNS:
        if pat.search(name or ''):
            return cat
    cmd = (cmdline or '').lower()
    if 'train_shared.py' in cmd or 'dsh_rl' in cmd:
        return 'dsh_training'
    if 'dsh' in cmd or 'deepseek' in cmd:
        return 'dsh'
    return 'other'


def counters_to_map(cs):
    """把 powershell 计数器列表转成 {name或instance: value} 便于阅读。"""
    out = {}
    if not isinstance(cs, list):
        return out
    for item in cs:
        try:
            name = item['name']
            inst = item.get('instance') or ''
            key = name if not inst else '%s[%s]' % (name, inst)
            out[key] = item['value']
        except Exception:  # noqa: BLE001
            continue
    return out


def wddm_process_map(gpu_process_dedicated):
    """WDDM 进程显存：instance 形如 pid_1234_luid_0x..._phys_0。N/A 一律 UNKNOWN。"""
    users = {}
    state = 'UNKNOWN'
    if isinstance(gpu_process_dedicated, list) and gpu_process_dedicated:
        state = 'OK_COUNTER'
        for item in gpu_process_dedicated:
            try:
                inst = item.get('instance') or ''
                val = item.get('value')
                m = re.search(r'pid_(\d+)', inst)
                pid = int(m.group(1)) if m else None
                if pid is None:
                    continue
                # 忽略 System Idle 等非真实 pid 归并
                users[pid] = round(users.get(pid, 0.0) + float(val) / MB, 1)
            except Exception:  # noqa: BLE001
                continue
    return {'state': state, 'dedicated_mb_by_pid': dict(sorted(
        ((k, v) for k, v in users.items() if v >= 0.1), key=lambda kv: -kv[1])),
        'note': 'N/A 或不可读的进程显存保持 UNKNOWN，不当作 0'}


def sample_once():
    t0 = time.time()
    ts = datetime.now().strftime(TS_FMT)
    mem = global_memory()
    counters = powershell_counters()
    gpu_txt = nvidia_smi_text()
    gpu = gpu_summary(gpu_txt)
    wddm = wddm_process_map(counters.get('gpu_process_dedicated'))
    rows = _process_rows()
    rows.sort(key=lambda r: -r['private_mb'])
    for rank, r in enumerate(rows, 1):
        r['private_rank'] = rank
    rows.sort(key=lambda r: -r['ws_mb'])
    for rank, r in enumerate(rows, 1):
        r['ws_rank'] = rank
    rows.sort(key=lambda r: -r['private_mb'])
    top_private = rows[:30]
    top_ws = sorted(rows, key=lambda r: -r['ws_mb'])[:30]
    top_union = {r['pid'] for r in top_private} | {r['pid'] for r in top_ws}
    for r in rows:
        r['category'] = classify(r.get('name', ''))
    key_pids = {r['pid'] for r in rows if r['category'] != 'other'} | top_union
    details = {}
    for pid in sorted(key_pids):
        details[pid] = _enrich(pid)
    sum_private = sum(r['private_mb'] for r in rows)
    sum_ws = sum(r['ws_mb'] for r in rows)
    commit_used = mem['commit_used_mb']
    non_process_commit = commit_used - sum_private
    # 内存压缩进程信息
    compression = [r for r in rows
                   if 'compression' in (r.get('name') or '').lower()]
    sample = {
        'index': None,
        'ts': ts,
        'sample_ms': round((time.time() - t0) * 1000, 1),
        'memory': mem,
        'counters': counters,
        'gpu': gpu,
        'wddm': wddm,
        'process_summary': {
            'proc_count': len(rows),
            'sum_private_mb': round(sum_private, 1),
            'sum_ws_mb': round(sum_ws, 1),
            'non_process_commit_mb': round(non_process_commit, 1),
            'non_process_commit_pct_of_commit': round(
                100.0 * non_process_commit / commit_used, 2) if commit_used else None,
            'memory_compression_procs': [
                {'pid': r['pid'], 'name': r['name'],
                 'private_mb': round(r['private_mb'], 1),
                 'ws_mb': round(r['ws_mb'], 1)} for r in compression],
        },
        'top_private': [dict(r, **details.get(r['pid'], {})) for r in top_private],
        'top_ws': [dict(r, **details.get(r['pid'], {})) for r in top_ws],
        'key_processes': [dict(r, **details.get(r['pid'], {}))
                          for r in rows if r['category'] != 'other'],
        'gpu_text': gpu_txt,
    }
    return sample, rows, details


def summarize(samples, rows_by_sample, timeout_notes):
    def stat(key_path, fmt=float):
        vals = []
        for s in samples:
            cur = s
            for k in key_path:
                cur = (cur or {}).get(k) if isinstance(cur, dict) else None
            if cur is not None:
                try:
                    vals.append(fmt(cur))
                except Exception:  # noqa: BLE001
                    pass
        if not vals:
            return {'min': None, 'max': None, 'mean': None, 'values': []}
        return {'min': min(vals), 'max': max(vals), 'mean': sum(vals) / len(vals),
                'values': vals}

    first, last = samples[0], samples[-1]
    deltas = {
        'phys_avail_mb': round(last['memory']['phys_avail_mb'] - first['memory']['phys_avail_mb'], 1),
        'commit_used_mb': round(last['memory']['commit_used_mb'] - first['memory']['commit_used_mb'], 1),
        'sum_private_mb': round(last['process_summary']['sum_private_mb']
                                - first['process_summary']['sum_private_mb'], 1),
        'gpu_used_mb': (round(last['gpu']['memory_used_mb'] - first['gpu']['memory_used_mb'], 1)
                        if last['gpu'].get('memory_used_mb') is not None
                        and first['gpu'].get('memory_used_mb') is not None else 'UNKNOWN'),
    }

    # 关键进程在整个采样窗口内的私有内存变化（对照泄漏趋势）
    growth = []
    rows_f = {r['pid']: r for r in rows_by_sample[0]}
    rows_l = {r['pid']: r for r in rows_by_sample[-1]}
    for pid, r in rows_l.items():
        if pid in rows_f:
            delta = r['private_mb'] - rows_f[pid]['private_mb']
            if abs(delta) >= 1.0:
                growth.append({'pid': pid, 'name': r.get('name'),
                               'private_from_mb': round(rows_f[pid]['private_mb'], 1),
                               'private_to_mb': round(r['private_mb'], 1),
                               'delta_mb': round(delta, 1)})
    growth.sort(key=lambda x: -x['delta_mb'])

    verdict = {}
    reasons = []
    m = last['memory']
    if m['phys_avail_mb'] < TRAIN_THRESHOLDS['phys_avail_min_mb']:
        reasons.append('phys_avail_mb=%.0f < %d' % (
            m['phys_avail_mb'], TRAIN_THRESHOLDS['phys_avail_min_mb']))
    if m['commit_pct'] is not None and m['commit_pct'] > TRAIN_THRESHOLDS['commit_pct_max']:
        reasons.append('commit_pct=%.1f > %.0f' % (
            m['commit_pct'], TRAIN_THRESHOLDS['commit_pct_max']))
    g = last['gpu']
    if g.get('memory_used_mb') is not None and \
            g['memory_used_mb'] > TRAIN_THRESHOLDS['gpu_dedicated_used_max_mb']:
        reasons.append('gpu_used_mb=%.0f > %.1f' % (
            g['memory_used_mb'], TRAIN_THRESHOLDS['gpu_dedicated_used_max_mb']))
    verdict['task_thresholds'] = {
        'thresholds': TRAIN_THRESHOLDS,
        'violations': reasons,
        'verdict': 'PASS' if not reasons else 'BLOCKED',
    }
    gate = []
    if m['commit_pct'] is not None and m['commit_pct'] >= MEMORY_AUDIT_GATE['commit_pct_max']:
        gate.append('commit_pct>=%.0f' % MEMORY_AUDIT_GATE['commit_pct_max'])
    if m['commit_avail_mb'] / 1024.0 < MEMORY_AUDIT_GATE['commit_avail_min_gb']:
        gate.append('commit_avail<%.0fGB' % MEMORY_AUDIT_GATE['commit_avail_min_gb'])
    if m['phys_avail_mb'] / 1024.0 < MEMORY_AUDIT_GATE['phys_avail_min_gb']:
        gate.append('phys_avail<%.0fGB' % MEMORY_AUDIT_GATE['phys_avail_min_gb'])
    if g.get('memory_free_mb') is not None and \
            g['memory_free_mb'] < MEMORY_AUDIT_GATE['gpu_free_min_mb']:
        gate.append('gpu_free<%.0fMB' % MEMORY_AUDIT_GATE['gpu_free_min_mb'])
    verdict['memory_audit_gate'] = {
        'thresholds': MEMORY_AUDIT_GATE, 'violations': gate,
        'verdict': 'PASS' if not gate else 'BLOCKED',
    }
    return {
        'memory_across_samples': {
            'phys_avail_mb': stat(('memory', 'phys_avail_mb')),
            'phys_used_mb': stat(('memory', 'phys_used_mb')),
            'commit_used_mb': stat(('memory', 'commit_used_mb')),
            'commit_pct': stat(('memory', 'commit_pct')),
            'sum_private_mb': stat(('process_summary', 'sum_private_mb')),
            'non_process_commit_mb': stat(('process_summary', 'non_process_commit_mb')),
        },
        'gpu_across_samples': {
            'memory_used_mb': stat(('gpu', 'memory_used_mb')),
            'memory_free_mb': stat(('gpu', 'memory_free_mb')),
            'util_gpu_pct': stat(('gpu', 'util_gpu_pct')),
            'temp_c': stat(('gpu', 'temp_c')),
        },
        'first_last_deltas': deltas,
        'top_growth_between_samples': growth[:15],
        'verdict': verdict,
        'notes': timeout_notes,
    }


def write_processes_csv(path, samples, rows_by_sample, details_by_sample):
    fields = ['sample_ts', 'sample_index', 'pid', 'ppid', 'name', 'category',
              'private_mb', 'ws_mb', 'handles', 'threads',
              'private_rank', 'ws_rank', 'is_top_private', 'is_top_ws', 'is_key',
              'start_time', 'exe', 'cmdline', 'username']
    with open(path, 'w', newline='', encoding='utf-8-sig') as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for idx, (s, rows) in enumerate(zip(samples, rows_by_sample)):
            details = details_by_sample[idx]
            for r in rows:
                d = details.get(r['pid'], {})
                w.writerow({
                    'sample_ts': s['ts'], 'sample_index': idx,
                    'pid': r['pid'], 'ppid': r.get('ppid'),
                    'name': r.get('name') or '',
                    'category': r.get('category') or 'other',
                    'private_mb': round(r['private_mb'], 1),
                    'ws_mb': round(r['ws_mb'], 1),
                    'handles': r.get('handles'),
                    'threads': r.get('threads'),
                    'private_rank': r.get('private_rank'),
                    'ws_rank': r.get('ws_rank'),
                    'is_top_private': int((r.get('private_rank') or 9999) <= 30),
                    'is_top_ws': int((r.get('ws_rank') or 9999) <= 30),
                    'is_key': int((r.get('category') or 'other') != 'other'),
                    'start_time': d.get('start_time'),
                    'exe': d.get('exe'),
                    'cmdline': d.get('cmdline'),
                    'username': d.get('username'),
                })


def fmt_mb(v):
    if v is None:
        return 'UNKNOWN'
    return '%.0f MB (%.2f GB)' % (v, v / 1024.0)


def write_report(path, payload):
    samples = payload['samples']
    summary = payload['summary']
    last = samples[-1]
    m = last['memory']
    g = last['gpu']
    counts = last['counters']
    mem_map = counters_to_map(counts.get('memory')) if counts.get('state') == 'OK' else {}
    pf_map = counters_to_map(counts.get('paging_file')) if counts.get('state') == 'OK' else {}

    def c(name, default='UNKNOWN'):
        v = mem_map.get(name)
        if v is None:
            return default
        return '%.2f GB' % (v / 1024.0 / 1024.0 / 1024.0)

    def c_mb(name, default='UNKNOWN'):
        v = mem_map.get(name)
        if v is None:
            return default
        return '%.0f MB' % (v / 1024.0 / 1024.0)

    mm_agent = counts.get('mm_agent')
    try:
        mm = json.loads(mm_agent) if isinstance(mm_agent, str) and mm_agent else None
    except Exception:  # noqa: BLE001
        mm = None

    lines = []
    A = lines.append
    A('# 系统资源基线报告（无敌人建设训练 V1 — 第一步）')
    A('')
    A('- 生成时间：%s' % payload['created_at'])
    A('- 主机：%s / %s' % (payload['host'], payload['os']))
    A('- 采集方式：%s' % payload['collection']['method'])
    A('- 采样：%d 次，间隔 %ss；样本时间戳：%s' % (
        len(samples), payload['collection']['interval_s'],
        ' → '.join(s['ts'] for s in samples)))
    A('- 采集时无游戏、无训练进程（基线为干净状态）。')
    A('')
    A('## 1. Windows 内存（最后一次采样，%s）' % last['ts'])
    A('')
    A('| 指标 | 数值 |')
    A('|---|---|')
    A('| 物理内存总量 | %s |' % fmt_mb(m['phys_total_mb']))
    A('| 物理内存已用 | %s（%s%%） |' % (fmt_mb(m['phys_used_mb']), m['phys_used_pct']))
    A('| 物理内存可用 | %s |' % fmt_mb(m['phys_avail_mb']))
    A('| 提交内存已用 | %s |' % fmt_mb(m['commit_used_mb']))
    A('| 提交内存上限 | %s |' % fmt_mb(m['commit_limit_mb']))
    A('| 提交内存可用 | %s |' % fmt_mb(m['commit_avail_mb']))
    A('| 提交使用率 | %.2f%% |' % m['commit_pct'])
    A('| Paged Pool | %s |' % c('pool paged bytes'))
    A('| Paged Pool（常驻） | %s |' % c('pool paged resident bytes'))
    A('| Nonpaged Pool | %s |' % c('pool nonpaged bytes'))
    A('| 系统缓存（System Cache Resident） | %s |' % c('system cache resident bytes'))
    A('| Cache Bytes | %s |' % c('cache bytes'))
    A('| Modified Page List | %s |' % c('modified page list bytes'))
    A('| Standby Cache（合计） | %s |' % '%.2f GB' % (
        (mem_map.get('standby cache core bytes', 0) +
         mem_map.get('standby cache normal priority bytes', 0) +
         mem_map.get('standby cache reserve bytes', 0)) / GB))
    A('| Free & Zero Page List | %s |' % c('free & zero page list bytes'))
    A('| 内存压缩进程 | %s |' % (
        ', '.join('%s(pid=%s, WS=%s)' % (p['name'], p['pid'], fmt_mb(p['ws_mb']))
                  for p in last['process_summary']['memory_compression_procs'])
        or '未在进程列表中出现（可能不可见或未启用）'))
    A('| Memory Compression 启用（Get-MMAgent） | %s |' % (
        ('MemoryCompression=%s, PageCombining=%s' % (
            mm.get('MemoryCompression'), mm.get('PageCombining'))) if mm else
        'UNKNOWN（Get-MMAgent 需提升权限；Memory Compression 进程存在可佐证已启用）'))
    A('')
    if pf_map:
        A('页文件（Paging File _Total）：')
        A('')
        A('| 计数器 | 值 |')
        A('|---|---|')
        for k, v in sorted(pf_map.items()):
            A('| %s | %s |' % (k, ('%.2f%%' % v) if 'usage' in k.lower() or '%' in k
                               else ('%.0f MB' % v)))
        A('')
    A('## 2. 提交内存构成（可归因性）')
    A('')
    A('| 项目 | 数值 |')
    A('|---|---|')
    A('| 提交内存已用 | %s |' % fmt_mb(m['commit_used_mb']))
    A('| 全部可见进程 Private Bytes 合计 | %s |' % fmt_mb(last['process_summary']['sum_private_mb']))
    A('| 非进程可归因提交（内核池/驱动/共享等） | %s（占提交 %.2f%%） |' % (
        fmt_mb(last['process_summary']['non_process_commit_mb']),
        last['process_summary']['non_process_commit_pct_of_commit']))
    A('| 可见进程数 | %d |' % last['process_summary']['proc_count'])
    A('')
    A('## 3. 进程 TOP 30（按 Private Bytes，%s）' % last['ts'])
    A('')
    A('| # | PID | 进程 | 分类 | Private | WorkingSet | 句柄 | 线程 | 启动时间 | 可执行文件 |')
    A('|---|---|---|---|---|---|---|---|---|---|')
    for i, p in enumerate(last['top_private'], 1):
        A('| %d | %s | %s | %s | %s | %s | %s | %s | %s | %s |' % (
            i, p['pid'], p.get('name') or '', p.get('category') or '',
            fmt_mb(p['private_mb']), fmt_mb(p['ws_mb']),
            p.get('handles'), p.get('threads'),
            p.get('start_time') or 'UNKNOWN', (p.get('exe') or 'UNKNOWN')))
    A('')
    A('### TOP 30（按 Working Set）')
    A('')
    A('| # | PID | 进程 | 分类 | WorkingSet | Private | 线程 |')
    A('|---|---|---|---|---|---|---|')
    for i, p in enumerate(last['top_ws'], 1):
        A('| %d | %s | %s | %s | %s | %s | %s |' % (
            i, p['pid'], p.get('name') or '', p.get('category') or '',
            fmt_mb(p['ws_mb']), fmt_mb(p['private_mb']), p.get('threads')))
    A('')
    A('## 4. 关键进程（Python / Node / DSH / Codex / WSL / 游戏 / 桌面）')
    A('')
    key = last['key_processes']
    if key:
        A('| PID | 进程 | 分类 | Private | WS | 启动时间 | 可执行文件 | 命令行（截断） |')
        A('|---|---|---|---|---|---|---|---|')
        for p in sorted(key, key=lambda x: -x['private_mb']):
            A('| %s | %s | %s | %s | %s | %s | %s | %s |' % (
                p['pid'], p.get('name') or '', p.get('category') or '',
                fmt_mb(p['private_mb']), fmt_mb(p['ws_mb']),
                p.get('start_time') or 'UNKNOWN', p.get('exe') or 'UNKNOWN',
                (p.get('cmdline') or '')[:120].replace('|', '/')))
    else:
        A('（未见匹配进程）')
    A('')
    A('## 5. GPU 基线')
    A('')
    if g.get('state') == 'OK':
        A('| 指标 | 数值 |')
        A('|---|---|')
        A('| GPU | %s |' % g.get('name'))
        A('| 驱动 | %s |' % g.get('driver'))
        A('| 专用显存总量 | %.0f MB |' % g['memory_total_mb'])
        A('| 专用显存已用 | %.0f MB |' % g['memory_used_mb'])
        A('| 专用显存可用 | %.0f MB |' % g['memory_free_mb'])
        A('| GPU 利用率 | %s%% |' % g.get('util_gpu_pct'))
        A('| 温度 | %s °C |' % g.get('temp_c'))
        A('| 功耗 | %s W（limit: N/A 表示驱动不报告） |' % g.get('power_w'))
    else:
        A('nvidia-smi 查询状态：%s' % g.get('state'))
    A('')
    A('WDDM 进程级显存归属：%s（N/A/不可读一律 UNKNOWN，不当作 0）。' % last['wddm'].get('state'))
    ad_ded = [it for it in (counts.get('gpu_adapter_dedicated') or [])] if counts.get('state') == 'OK' else []
    ad_sha = [it for it in (counts.get('gpu_adapter_shared') or [])] if counts.get('state') == 'OK' else []
    if ad_ded or ad_sha:
        A('')
        A('WDDM Adapter 计数器：')
        A('')
        A('| Adapter (LUID) | Dedicated Usage | Shared Usage |')
        A('|---|---|---|')
        ded_map = {(it.get('instance') or ''): float(it.get('value') or 0)
                   for it in ad_ded}
        sha_map = {(it.get('instance') or ''): float(it.get('value') or 0)
                   for it in ad_sha}
        for inst in sorted(set(ded_map) | set(sha_map)):
            A('| %s | %.1f MB | %.1f MB |' % (inst, ded_map.get(inst, 0) / MB,
                                              sha_map.get(inst, 0) / MB))
    if last['wddm'].get('dedicated_mb_by_pid'):
        A('')
        A('WDDM 进程 Dedicated Usage TOP（按 pid）：')
        A('')
        A('| PID | 专用显存 MB |')
        A('|---|---|')
        for pid, mb in list(last['wddm']['dedicated_mb_by_pid'].items())[:15]:
            A('| %s | %.1f |' % (pid, mb))
    A('')
    A('原始 GPU 输出见 `baseline_gpu.txt`。')
    A('')
    A('## 6. 三次采样对比（稳定性 / 增长趋势）')
    A('')
    A('| 样本 | 时间 | 物理可用 | 提交已用 | 提交率 | 进程 Private 合计 | GPU 已用 | GPU 利用率 |')
    A('|---|---|---|---|---|---|---|---|')
    for s in samples:
        sm = s['memory']
        A('| %s | %s | %s | %s | %.2f%% | %s | %s | %s%% |' % (
            s['index'], s['ts'], fmt_mb(sm['phys_avail_mb']),
            fmt_mb(sm['commit_used_mb']), sm['commit_pct'],
            fmt_mb(s['process_summary']['sum_private_mb']),
            (('%.0f MB' % s['gpu']['memory_used_mb'])
             if s['gpu'].get('memory_used_mb') is not None else 'UNKNOWN'),
            s['gpu'].get('util_gpu_pct', 'UNKNOWN')))
    A('')
    A('首末样本差值：%s' % json.dumps(summary['first_last_deltas'], ensure_ascii=False))
    A('')
    growth = summary.get('top_growth_between_samples') or []
    if growth:
        A('采样窗口内私有内存增长 TOP：')
        A('')
        A('| PID | 进程 | 起始 MB | 结束 MB | 变化 MB |')
        A('|---|---|---|---|---|')
        for x in growth[:10]:
            A('| %s | %s | %.1f | %.1f | %+.1f |' % (
                x['pid'], x['name'], x['private_from_mb'], x['private_to_mb'], x['delta_mb']))
        A('')
    A('## 7. 训练启动阈值判定（任务书 §七）')
    A('')
    th = summary['verdict']['task_thresholds']
    A('| 阈值 | 要求 | 实测 |')
    A('|---|---|---|')
    A('| 可用物理内存 | ≥ 4 GB（低于则暂停训练） | %s |' % fmt_mb(m['phys_avail_mb']))
    A('| 提交内存使用率 | ≤ 80%%（超过则暂停训练） | %.2f%% |' % m['commit_pct'])
    A('| GPU 专用显存已用 | ≤ 7.2 GB（超过则暂停训练） | %s |' % (
        ('%.0f MB' % g['memory_used_mb']) if g.get('memory_used_mb') is not None else 'UNKNOWN'))
    A('')
    A('**任务书阈值判定：%s**' % th['verdict'])
    if th['violations']:
        A('')
        A('违规项：%s' % '; '.join(th['violations']))
    gate = summary['verdict']['memory_audit_gate']
    A('')
    A('历史 memory_audit 安全门（更严格，供对照）：%s%s' % (
        gate['verdict'],
        ('（%s）' % '; '.join(gate['violations'])) if gate['violations'] else ''))
    A('')
    A('## 8. 结论')
    A('')
    A('- 基线已记录：3 次采样、间隔 30s，样本间无明显漂移时视为稳定基线。')
    A('- 训练期间将用 10 秒级监控（复用 experiments/memory_audit/tools/monitor_resource.py 的采集逻辑）')
    A('  对照本基线，比较物理内存、提交内存、训练/Worker/游戏进程 Private Bytes、GPU 专用/共享显存。')
    A('- WDDM 下进程级显存归属不可靠：报告只给 UNKNOWN 与计数器可读部分，不虚构 0。')
    A('')
    path.write_text('\n'.join(lines), encoding='utf-8')
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out-dir', default=str(REPO_ROOT / 'reports' / 'build_training'))
    ap.add_argument('--samples', type=int, default=3)
    ap.add_argument('--interval', type=float, default=30.0)
    ap.add_argument('--tag', default=None,
                    help='若指定则做阶段快照（写入 snapshots/<tag>.json），不生成完整基线报告')
    ap.add_argument('--rebuild-from', default=None,
                    help='从已有 JSON 重建 gpu 文本与报告（不重新采样）')
    args = ap.parse_args(argv)

    out = pathlib.Path(args.out_dir)
    if not out.is_absolute():
        out = (REPO_ROOT / out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    if args.rebuild_from:
        src = pathlib.Path(args.rebuild_from)
        if not src.is_absolute():
            src = (REPO_ROOT / src).resolve()
        payload = json.loads(src.read_text(encoding='utf-8'))
        samples = payload['samples']
        (out / 'baseline_gpu.txt').write_text(
            '\n\n'.join('=== sample %d %s ===\n%s' % (
                s['index'], s['ts'], gpu_txt_for_sample(s)) for s in samples),
            encoding='utf-8')
        write_report(out / 'baseline_report.md', payload)
        print('[baseline] rebuilt gpu txt + report from %s' % src)
        return 0

    samples, rows_by_sample, details_by_sample = [], [], []
    for i in range(args.samples):
        if i > 0 and args.interval > 0:
            time.sleep(args.interval)
        s, rows, details = sample_once()
        s['index'] = i
        samples.append(s)
        rows_by_sample.append(rows)
        details_by_sample.append(details)
        print('[baseline] sample %d/%d ts=%s commit=%.1fGB(%.1f%%) phys_avail=%.1fGB '
              'procs=%d gpu_used=%sMB' % (
                  i + 1, args.samples, s['ts'],
                  s['memory']['commit_used_mb'] / 1024.0,
                  s['memory']['commit_pct'],
                  s['memory']['phys_avail_mb'] / 1024.0,
                  s['process_summary']['proc_count'],
                  s['gpu'].get('memory_used_mb', 'UNKNOWN')), flush=True)

    notes = {
        'process_visibility': '需完整进程可见性；默认沙箱仅可见少量进程，'
                              '基线须在获批完整只读权限下采集。',
        'wddm_attribution': 'WDDM 进程级显存归属可能 N/A/UNAVAILABLE，保持 UNKNOWN。',
        'gpu_power_limit': 'nvidia-smi power.limit 在本机报告 N/A，属驱动/机型限制。',
    }
    summary = summarize(samples, rows_by_sample, notes)
    created = datetime.now().strftime(TS_FMT)
    payload = {
        'schema': 'ra2mcp.build_training.baseline.v1',
        'created_at': created,
        'host': platform.node(),
        'os': '%s %s' % (platform.system(), platform.release()),
        'cpu': platform.processor(),
        'cpu_count': os.cpu_count(),
        'python': sys.version.split()[0],
        'boot_time': (datetime.fromtimestamp(psutil.boot_time()).strftime(TS_FMT)
                      if psutil else None),
        'uptime_hours': (round((time.time() - psutil.boot_time()) / 3600.0, 2)
                         if psutil else None),
        'collection': {
            'samples': args.samples,
            'interval_s': args.interval,
            'method': 'GlobalMemoryStatusEx + PowerShell Get-Counter(\\Memory\\*, Paging File, '
                      'GPU Process/Adapter Memory) + NtQuerySystemInformation 进程快照 + '
                      'psutil 进程详情补充 + nvidia-smi',
        },
        'samples': samples,
        'summary': summary,
    }

    if args.tag:
        snap_dir = out / 'snapshots'
        snap_dir.mkdir(parents=True, exist_ok=True)
        (snap_dir / (args.tag + '.json')).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        (snap_dir / (args.tag + '_gpu.txt')).write_text(
            '\n\n'.join('=== sample %d %s ===\n%s' % (
                s['index'], s['ts'], gpu_txt_for_sample(s)) for s in samples),
            encoding='utf-8')
        print('[baseline] snapshot -> %s' % (snap_dir / (args.tag + '.json')))
        return 0

    json_path = out / 'baseline_before.json'
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    write_processes_csv(out / 'baseline_processes.csv', samples, rows_by_sample,
                        details_by_sample)
    (out / 'baseline_gpu.txt').write_text(
        '\n\n'.join('=== sample %d %s ===\n%s' % (
            s['index'], s['ts'], gpu_txt_for_sample(s)) for s in samples),
        encoding='utf-8')
    write_report(out / 'baseline_report.md', payload)
    print('[baseline] json   -> %s' % json_path)
    print('[baseline] csv    -> %s' % (out / 'baseline_processes.csv'))
    print('[baseline] gpu    -> %s' % (out / 'baseline_gpu.txt'))
    print('[baseline] report -> %s' % (out / 'baseline_report.md'))
    print('[baseline] verdict task=%s memory_audit_gate=%s' % (
        summary['verdict']['task_thresholds']['verdict'],
        summary['verdict']['memory_audit_gate']['verdict']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
