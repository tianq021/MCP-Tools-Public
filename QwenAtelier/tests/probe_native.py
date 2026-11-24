"""Load the native sd-server once with current settings and report whether it reaches ready.

This is a diagnostic, not part of the web app: it starts the same command the engine
would use, waits for /sdcpp/v1/models/status, writes the outcome to
tests/artifacts/native-load-probe.json and the process output to logs/probe.log,
then always stops the process.

Usage:
  .venv\\Scripts\\python.exe tests\\probe_native.py
  .venv\\Scripts\\python.exe tests\\probe_native.py --diffusion <path> --timeout 900
"""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.storage import Store  # noqa: E402


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def main() -> int:
    settings = Store(ROOT).settings()
    parser = argparse.ArgumentParser(description='Probe one native model load.')
    parser.add_argument('--diffusion', default=settings['diffusion_model'])
    parser.add_argument('--encoder', default=settings['text_encoder'])
    parser.add_argument('--vae', default=settings['vae'])
    parser.add_argument('--vision', default=settings.get('vision') or '')
    parser.add_argument('--max-vram', default=str(settings['max_vram']))
    parser.add_argument('--timeout', type=float, default=900)
    args = parser.parse_args()

    exe = ROOT / 'runtime/stable-diffusion.cpp/sd-server.exe'
    if not exe.is_file():
        print('sd-server.exe missing', file=sys.stderr)
        return 2
    for label, value in (('扩散模型', args.diffusion), ('文本编码器', args.encoder), ('VAE', args.vae)):
        if not value or not Path(value).is_file():
            print(f'{label}不存在：{value or "未配置"}', file=sys.stderr)
            return 2

    port = free_port()
    command = [str(exe), '--listen-ip', '127.0.0.1', '--listen-port', str(port),
               '--diffusion-model', args.diffusion, '--llm', args.encoder, '--vae', args.vae,
               '--offload-to-cpu', '--diffusion-fa', '--vae-tiling',
               '--max-vram', args.max_vram, '--rng', 'cpu',
               '--backend', 'diffusion=CUDA0,vae=CUDA0,te=CPU']
    if args.vision:
        command += ['--llm_vision', args.vision]

    log_path = ROOT / 'logs/probe.log'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    report = {'started_at': time.strftime('%Y-%m-%d %H:%M:%S'), 'command': command[1:],
              'state': 'starting', 'error': None, 'ready_after_seconds': None, 'log_tail': []}
    print('probe:', ' '.join(command[3:]), flush=True)
    started = time.monotonic()
    with log_path.open('w', encoding='utf-8', errors='replace') as log:
        process = subprocess.Popen(command, cwd=exe.parent, stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        url = f'http://127.0.0.1:{port}'
        try:
            while time.monotonic() - started < args.timeout:
                if process.poll() is not None:
                    report.update(state='crashed', error=f'进程退出，代码 {process.returncode}')
                    break
                try:
                    with httpx.Client(trust_env=False, timeout=3) as client:
                        status = client.get(url + '/sdcpp/v1/models/status').json()
                except (httpx.HTTPError, ValueError):
                    time.sleep(2)
                    continue
                state = status.get('state')
                if state == 'ready':
                    report.update(state='ready', ready_after_seconds=round(time.monotonic() - started, 1))
                    break
                if state == 'error':
                    report.update(state='error', error=str(status.get('error') or '未知错误'),
                                  ready_after_seconds=round(time.monotonic() - started, 1))
                    break
                time.sleep(2)
            else:
                report.update(state='timeout', error=f'超过 {args.timeout:.0f} 秒仍未就绪')
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)

    tail = log_path.read_text(encoding='utf-8', errors='replace').splitlines()[-40:]
    report['log_tail'] = tail
    report['duration_seconds'] = round(time.monotonic() - started, 1)
    out = ROOT / 'tests/artifacts/native-load-probe.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('state', 'error', 'ready_after_seconds', 'duration_seconds')},
                     ensure_ascii=False, indent=2))
    for line in tail[-12:]:
        print('  |', line)
    return 0 if report['state'] == 'ready' else 1


if __name__ == '__main__':
    raise SystemExit(main())
