"""Submit one real generation job to the running studio and follow it to the end.

Used for the first end-to-end check and for later resolution/steps boundary tests.
It only talks to the local app API; it never starts or stops the inference process.

Usage:
  .venv\\Scripts\\python.exe scripts\\smoke_generate.py
  .venv\\Scripts\\python.exe scripts\\smoke_generate.py --size 768 --steps 12 --prompt "…"
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
# The machine has a system proxy configured; loopback requests must bypass it explicitly.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def request(base, method, path, body=None, token=None, timeout=30):
    data = json.dumps(body).encode('utf-8') if body is not None else None
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['X-Studio-Token'] = token
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    with OPENER.open(req, timeout=timeout) as response:
        return json.load(response)


def main() -> int:
    base = (ROOT / 'data/server-url.txt').read_text(encoding='utf-8').strip()
    parser = argparse.ArgumentParser(description='Run one real generation through the local app.')
    parser.add_argument('--size', type=int, default=512, help='square size; overridden by --width/--height')
    parser.add_argument('--width', type=int, default=None, help='any 256-4096, aligned down to a multiple of 8')
    parser.add_argument('--height', type=int, default=None)
    parser.add_argument('--steps', type=int, default=8)
    parser.add_argument('--cfg', type=float, default=1.0)
    parser.add_argument('--seed', type=int, default=12345)
    parser.add_argument('--batch', type=int, default=1)
    parser.add_argument('--timeout', type=float, default=1800)
    parser.add_argument('--output-bit-depth', type=int, choices=(8,16), default=8)
    parser.add_argument('--report', type=Path, help='save the final job and image details as JSON')
    parser.add_argument('--prompt', default='清晨的山间小屋，薄雾环绕，窗内透出温暖灯光，柔和晨光，胶片质感')
    args = parser.parse_args()

    width = args.width or args.size
    height = args.height or args.size
    token = request(base, 'GET', '/api/session')['token']
    config = {'prompt': args.prompt, 'negative_prompt': '', 'width': width, 'height': height,
              'steps': args.steps, 'cfg': args.cfg, 'seed': args.seed, 'batch_count': args.batch,
              'sampler': 'euler', 'scheduler': 'default', 'vae_tiling': True,
              'input_id': None, 'content_confirmed': True}
    if args.output_bit_depth == 16:
        config['output_bit_depth'] = 16
    job = request(base, 'POST', '/api/jobs', config, token)
    print(f"任务 {job['id'][:8]} 已提交：请求 {width}×{height} · {args.steps} 步 · seed {args.seed} · {args.output_bit_depth} bit", flush=True)

    started, last = time.monotonic(), None
    while time.monotonic() - started < args.timeout:
        time.sleep(3)
        jobs = request(base, 'GET', '/api/jobs')
        current = next(item for item in jobs if item['id'] == job['id'])
        stage = f"{current['stage']} {current['step']}/{current['total_steps']} 步 {round(current['progress'] * 100)}%"
        if stage != last:
            print(f'  [{time.monotonic() - started:6.1f}s] {stage}', flush=True)
            last = stage
        if current['status'] in ('completed', 'failed', 'cancelled', 'interrupted'):
            elapsed = time.monotonic() - started
            print(f"结果：{current['status']}  总耗时 {elapsed:.1f}s")
            if current.get('error'):
                print('错误：', current['error'])
            details=[]
            for identity in current.get('image_ids', []):
                detail = request(base, 'GET', f'/api/images/{identity}')
                details.append(detail)
                print(f"  图片 {identity[:8]} · {detail['width']}×{detail['height']} · "
                      f"{detail['size'] / 1024 / 1024:.2f} MB · {detail['name']}")
                print(f"  耗时记录 {detail['config'].get('elapsed_seconds')}s · SHA256 {detail['sha256'][:16]}…")
            if args.report:
                args.report.parent.mkdir(parents=True,exist_ok=True)
                args.report.write_text(json.dumps({'job':current,'images':details,'wall_seconds':elapsed},ensure_ascii=False,indent=2),encoding='utf-8')
            if args.output_bit_depth == 16 and current['status']=='completed':
                if not details or any(image.get('bit_depth')!=16 or image['config'].get('bit_depth_engine_declared')!=16 for image in details):
                    raise RuntimeError('16 bit output was requested but the saved image or engine declaration does not match')
            return 0 if current['status'] == 'completed' else 1
    print('超时，任务仍未结束')
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
