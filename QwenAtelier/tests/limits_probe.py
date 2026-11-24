"""Measure the real upscale/outpaint limits on this machine.

The app's safety limits are deliberately bypassed here: this is a hardware
probe, not a product endpoint. Every output lives under
tests/artifacts/limits/<stamp>/ and the isolated outpaint engine never writes
into the production database.

    .\\.venv\\Scripts\\python.exe tests\\limits_probe.py --mode upscale --tiles 128,256
    .\\.venv\\Scripts\\python.exe tests\\limits_probe.py --mode upscale --tiles 256 --input-size 2048x2048
    .\\.venv\\Scripts\\python.exe tests\\limits_probe.py --mode outpaint --cases 2k1,2k16,4k1
"""
from __future__ import annotations

import argparse
import base64
import ctypes
from ctypes import wintypes
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engine import Engine
from app.image_tools import geometry, compose_preserved, verify_preserved
from app.power import power_snapshot
from app.resources import resource_snapshot
from app.storage import ROOT, Store, atomic_json, sha256

CLI = ROOT / 'runtime/stable-diffusion.cpp/sd-cli.exe'
SERVER = ROOT / 'runtime/stable-diffusion.cpp/sd-server.exe'
MODEL = ROOT / 'models/upscalers/RealESRGAN_x4plus_anime_6B.pth'
ENGINE_LOG = ROOT / 'logs/engine.log'
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def cpu_percent():
    values = [wintypes.FILETIME() for _ in range(3)]
    if not ctypes.windll.kernel32.GetSystemTimes(*(ctypes.byref(v) for v in values)):
        return None
    before = [(v.dwHighDateTime << 32) | v.dwLowDateTime for v in values]
    time.sleep(1)
    values = [wintypes.FILETIME() for _ in range(3)]
    ctypes.windll.kernel32.GetSystemTimes(*(ctypes.byref(v) for v in values))
    after = [(v.dwHighDateTime << 32) | v.dwLowDateTime for v in values]
    delta = [b - a for a, b in zip(before, after)]
    return round(100 * (1 - delta[0] / max(1, delta[1] + delta[2])), 1)


def preflight(require_free_gpu=5 * 1024**3):
    snapshot = resource_snapshot(None)
    memory = snapshot.get('memory') or {}
    gpu = snapshot.get('gpu') or {}
    cpu = cpu_percent()
    power = power_snapshot()
    reasons = []
    if power.get('on_battery'):
        reasons.append('正在使用电池')
    if cpu is not None and cpu > 40:
        reasons.append(f'CPU 占用 {cpu}% > 40%')
    if memory.get('available', 0) < 12 * 1024**3:
        reasons.append('可用内存不足 12 GB')
    if gpu.get('free', 0) < require_free_gpu:
        reasons.append(f'空闲显存不足 {require_free_gpu / 1024**3:.0f} GB')
    if gpu.get('utilization', 0) > 60:
        reasons.append(f'GPU 利用率 {gpu.get("utilization")}% > 60%')
    return dict(cpu_percent=cpu, resources=snapshot, power=power), reasons


def make_input(path, width, height):
    """Synthetic scene with gradients and detail; no user image leaves the disk."""
    image = Image.new('RGB', (width, height))
    draw = ImageDraw.Draw(image)
    horizon = int(height * 0.62)
    for y in range(height):
        if y < horizon:
            t = y / max(1, horizon)
            color = (int(64 + 60 * t), int(138 + 52 * t), int(205 + 35 * t))
        else:
            t = (y - horizon) / max(1, height - horizon)
            color = (int(58 + 55 * t), int(132 + 62 * t), int(62 + 35 * t))
        draw.line([(0, y), (width, y)], fill=color)
    # House in the lower middle, scaled to the canvas.
    cx, base = width // 2, int(height * 0.86)
    hw, body = int(width * 0.12), int(height * 0.22)
    draw.rectangle([cx - hw, base - body, cx + hw, base], fill=(225, 200, 160))
    draw.polygon([(cx - hw - 20, base - body), (cx, base - body - int(height * 0.14)),
                  (cx + hw + 20, base - body)], fill=(155, 58, 45))
    draw.rectangle([cx - int(hw * .2), base - int(body * .45), cx + int(hw * .2), base], fill=(92, 63, 47))
    # Texture: a little noise plus thin vertical/horizontal details.
    noise = Image.effect_noise((width, height), 18).convert('L')
    image = Image.blend(image, Image.merge('RGB', (noise, noise, noise)), 0.035)
    draw = ImageDraw.Draw(image)
    for x in range(0, width, 96):
        draw.line([(x, 0), (x, height)], fill=(255, 255, 255), width=1)
    for y in range(0, height, 96):
        draw.line([(0, y), (width, y)], fill=(0, 0, 0), width=1)
    image.save(path, format='PNG')
    return image


def sample_peak(stop, baseline):
    peak = {'gpu_used': baseline.get('gpu_used', 0), 'gpu_util': 0,
            'memory_available_min': baseline.get('memory_available', 0)}
    while not stop.is_set():
        snap = resource_snapshot(None)
        gpu = snap.get('gpu') or {}
        memory = snap.get('memory') or {}
        peak['gpu_used'] = max(peak['gpu_used'], gpu.get('used') or 0)
        peak['gpu_util'] = max(peak['gpu_util'], gpu.get('utilization') or 0)
        available = memory.get('available')
        if available:
            peak['memory_available_min'] = min(peak['memory_available_min'], available)
        stop.wait(0.5)
    return peak


def run_upscale(root, input_path, tile, repeats, timeout):
    output = root / f'upscale-t{tile}-r{repeats}.png'
    log = root / f'upscale-t{tile}-r{repeats}.log'
    command = [str(CLI), '-M', 'upscale', '--upscale-model', str(MODEL),
               '-i', str(input_path), '-o', str(output),
               '--upscale-tile-size', str(tile), '--upscale-repeats', str(repeats)]
    import threading
    baseline = resource_snapshot(None)
    base = {'gpu_used': (baseline.get('gpu') or {}).get('used') or 0,
            'memory_available': (baseline.get('memory') or {}).get('available') or 0}
    stop = threading.Event()
    peak_box = {}
    sampler = threading.Thread(target=lambda: peak_box.update(sample_peak(stop, base)), daemon=True)
    sampler.start()
    started = time.monotonic()
    killed = False
    with log.open('w', encoding='utf-8') as stream:
        process = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
        try:
            while process.poll() is None:
                if time.monotonic() - started > timeout:
                    process.kill()
                    killed = True
                    break
                time.sleep(0.2)
        finally:
            stop.set()
            sampler.join(timeout=3)
            if process.poll() is None:
                process.wait(timeout=10)
    seconds = round(time.monotonic() - started, 2)
    record = dict(kind='upscale', tile=tile, repeats=repeats, command=command,
                  exit_code=process.returncode, killed=killed, seconds=seconds,
                  peak=peak_box, log=str(log.relative_to(ROOT)), output_size=None,
                  output_bytes=None, output_sha256=None, png_bit_depth=None)
    if output.is_file():
        with Image.open(output) as image:
            image.load()
            record['output_size'] = list(image.size)
            record['png_bit_depth'] = image.info.get('bits', 8)
        record['output_bytes'] = output.stat().st_size
        record['output_sha256'] = sha256(output)
    print(json.dumps({k: record[k] for k in ('tile', 'repeats', 'exit_code', 'killed', 'seconds',
                                            'output_size', 'output_bytes')}, ensure_ascii=False), flush=True)
    return record


def outpaint_prompt(canvas, original):
    return (f'Remove both gray padding bars completely and replace every gray padding pixel with a natural continuation of the scene. '
            f'The final image must have no gray bars, no border, no frame. Extend the sky and ground all the way to the outer edges. '
            f'Keep the original scene at its exact location. '
            f'The original occupies x={canvas["offset_x"]}, y={canvas["offset_y"]}, '
            f'width={original.width}, height={original.height}. Fill only the new border naturally. '
            f'Continue the blue sky, the grassy meadow and the small red-roof house. Preserve the straight horizon and natural colors.')


def outpaint_case(engine, root, name, original, params, deadline, native_log):
    from PIL import Image as PILImage
    canvas = geometry(original.size, params)
    board = PILImage.new('RGB', (canvas['width'], canvas['height']), '#808080')
    board.paste(original, (canvas['offset_x'], canvas['offset_y']))
    stream = io.BytesIO()
    board.save(stream, format='PNG')
    body = dict(prompt=outpaint_prompt(canvas, original), negative_prompt='', width=canvas['width'], height=canvas['height'],
                seed=params.get('seed', 20261023), batch_count=1,
                ref_images=[base64.b64encode(stream.getvalue()).decode()],
                sample_params=dict(sample_method='euler', sample_steps=params['steps'],
                                   guidance={'txt_cfg': params['cfg']}),
                vae_tiling_params={'enabled': True}, output_format='png',
                output_bit_depth=8, embed_image_metadata=False)
    record = dict(kind='outpaint', case=name, canvas=canvas, params=params, status='starting',
                  seconds=None, peak=None, error=None, preservation=None, output_size=None,
                  output_bytes=None, native_request={k: (v if k != 'ref_images' else ['expanded canvas']) for k, v in body.items()})
    baseline = resource_snapshot(None)
    base = {'gpu_used': (baseline.get('gpu') or {}).get('used') or 0,
            'memory_available': (baseline.get('memory') or {}).get('available') or 0}
    import threading
    stop = threading.Event()
    peak_box = {}
    threading.Thread(target=lambda: peak_box.update(sample_peak(stop, base)), daemon=True).start()
    started = time.monotonic()
    native_id = None
    completed_images = []
    try:
        native = engine.request('POST', '/sdcpp/v1/img_gen', json=body, timeout=60)
        native_id = native.get('id')
        record['native_id'] = native_id
        if not native_id:
            raise RuntimeError(f'引擎未返回任务 ID：{native}')
        while time.monotonic() - started < deadline:
            status = engine.request('GET', f'/sdcpp/v1/jobs/{native_id}', timeout=15)
            snapshot = resource_snapshot(None)
            available = (snapshot.get('memory') or {}).get('available') or 0
            if available and available < 3 * 1024**3:
                record['abort_reason'] = f'可用内存低于 3 GB（{available / 1024**3:.1f} GB），主动结束测试引擎'
                record['status'] = 'aborted'
                break
            if status.get('status') in ('completed', 'failed', 'cancelled'):
                record['status'] = status['status']
                stage = status.get('result') or {}
                completed_images = stage.get('images') or []
                record['engine_status'] = {key: status.get(key) for key in
                                           ('status', 'error', 'started', 'completed', 'progress')}
                record['has_images'] = len(completed_images)
                if status.get('started') and status.get('completed'):
                    record['engine_seconds'] = round((status['completed'] - status['started']) / 1000, 2)
                break
            time.sleep(1)
        else:
            record['status'] = 'timeout'
            record['error'] = f'超过 {deadline} 秒仍未完成'
    except Exception as error:
        record['status'] = 'error'
        record['error'] = f'{type(error).__name__}: {error}'
    finally:
        stop.set()
        time.sleep(0.6)
        record['peak'] = peak_box
        record['seconds'] = round(time.monotonic() - started, 2)
    if record.get('status') == 'completed':
        images = completed_images
        if len(images) != 1:
            record['status'] = 'error'
            record['error'] = f'返回图片数量异常：{len(images)}'
        else:
            raw = base64.b64decode(images[0]['b64_json'], validate=True)
            candidate = PILImage.open(io.BytesIO(raw)).convert('RGB')
            candidate.save(root / f'outpaint-{name}-candidate.png')
            final = compose_preserved(original, candidate, canvas)
            record['preservation'] = verify_preserved(original, final, canvas)
            path = root / f'outpaint-{name}-final.png'
            final.save(path)
            record['output_size'] = list(final.size)
            record['output_bytes'] = path.stat().st_size
            record['output_sha256'] = sha256(path)
    print(json.dumps({k: record.get(k) for k in ('case', 'status', 'seconds', 'error', 'preservation',
                                                'output_size', 'output_bytes')}, ensure_ascii=False), flush=True)
    if record['status'] in ('timeout', 'aborted'):
        # The native engine cannot interrupt sampling; end the test engine so no GPU work continues.
        try:
            engine._stop_process()
        except Exception as error:
            record['stop_error'] = str(error)
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['upscale', 'outpaint', 'all'], required=True)
    parser.add_argument('--tiles', default='128,256')
    parser.add_argument('--repeats', type=int, default=1)
    parser.add_argument('--input-size', default='1920x1080')
    parser.add_argument('--timeout', type=int, default=1200, help='per upscale run, seconds')
    parser.add_argument('--cases', default='2k1,2k16,4k1')
    arguments = parser.parse_args()

    pre, reasons = preflight()
    print(json.dumps(dict(preflight=pre, blockers=reasons), ensure_ascii=False), flush=True)
    if reasons:
        raise SystemExit('环境不满足：' + '；'.join(reasons))

    stamp = time.strftime('%Y%m%d-%H%M%S')
    root = ROOT / 'tests/artifacts/limits' / stamp
    root.mkdir(parents=True)
    width, height = (int(value) for value in arguments.input_size.lower().split('x'))
    input_path = root / 'input.png'
    make_input(input_path, width, height)
    report = dict(started=time.time(), stamp=stamp, input=str(input_path), input_size=[width, height],
                  preflight=pre, upscale=[], outpaint=[])
    print(str(root), flush=True)

    if arguments.mode in ('upscale', 'all'):
        for tile in (int(value) for value in arguments.tiles.split(',') if value.strip()):
            report['upscale'].append(run_upscale(root, input_path, tile, arguments.repeats, arguments.timeout))
            atomic_json(root / 'result.json', report)

    if arguments.mode in ('outpaint', 'all'):
        settings = json.loads((ROOT / 'data/settings.json').read_text(encoding='utf-8'))
        settings['keep_loaded'] = True
        fixture = root / 'outpaint-fixture'
        fixture.mkdir()
        (fixture / '.tmp').mkdir()
        store = Store(fixture)
        store.save_settings(settings)
        backup = root / 'production-engine-log.bak'
        if ENGINE_LOG.is_file():
            shutil.copy2(ENGINE_LOG, backup)
        engine = Engine(store)
        try:
            load_started = time.monotonic()
            try:
                with engine.operation_lock:
                    engine._load(settings)
            except Exception as error:
                report['outpaint_load_error'] = f'{type(error).__name__}: {error}'
                atomic_json(root / 'result.json', report)
                raise
            report['outpaint_load_seconds'] = round(time.monotonic() - load_started, 2)
            for case in (value.strip() for value in arguments.cases.split(',') if value.strip()):
                if case == '2k1':
                    original = Image.open(input_path).convert('RGB').resize((1920, 1080), Image.Resampling.LANCZOS)
                    params = dict(extend_left=32, extend_right=32, extend_top=20, extend_bottom=20, steps=1, cfg=1)
                elif case == '2k16':
                    original = Image.open(input_path).convert('RGB').resize((1920, 1080), Image.Resampling.LANCZOS)
                    params = dict(extend_left=32, extend_right=32, extend_top=20, extend_bottom=20, steps=16, cfg=6)
                elif case == '4k1':
                    original = Image.open(input_path).convert('RGB').resize((1920, 1080), Image.Resampling.LANCZOS)
                    params = dict(extend_left=960, extend_right=960, extend_top=540, extend_bottom=540, steps=1, cfg=1)
                elif case == '4k4':
                    original = Image.open(input_path).convert('RGB').resize((1920, 1080), Image.Resampling.LANCZOS)
                    params = dict(extend_left=960, extend_right=960, extend_top=540, extend_bottom=540, steps=4, cfg=6)
                else:
                    raise SystemExit(f'未知 case：{case}')
                params.update(seed=20261023, negative_prompt='', prompt='', sampler='euler', scheduler='default')
                deadline = {'2k1': 600, '2k16': 1800, '4k1': 480, '4k4': 600}[case]
                report['outpaint'].append(outpaint_case(engine, root, case, original, params, deadline, None))
                atomic_json(root / 'result.json', report)
                if not engine._engine_alive()[0]:
                    report['outpaint_engine_died'] = True
                    break
        finally:
            try:
                if engine._engine_alive()[0]:
                    engine.unload()
            except Exception as error:
                report['unload_error'] = str(error)
                try:
                    engine._stop_process()
                except Exception as stop_error:
                    report['stop_error'] = str(stop_error)
            engine.close()
            if ENGINE_LOG.is_file():
                shutil.copy2(ENGINE_LOG, root / 'engine-test.log')
            if backup.is_file():
                shutil.copy2(backup, ENGINE_LOG)
            if (fixture / 'data/engine.json').is_file():
                (fixture / 'data/engine.json').write_text('{}', encoding='utf-8')

    report['finished'] = time.time()
    atomic_json(root / 'result.json', report)
    print('DONE ' + str(root), flush=True)


if __name__ == '__main__':
    main()
