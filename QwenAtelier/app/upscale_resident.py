"""Isolated, persistent ESRGAN context using the bundled stable-diffusion C API.

Control files are local, atomically replaced, and scoped to one random session.
Only the owning PID (including its creation time) may be stopped. The worker
outlives the web service; restarting the web service never replays a command.
"""
from __future__ import annotations

import ctypes as C
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

from PIL import Image

from .power import power_snapshot
from .process import pid_alive, process_path, process_birth, spawn_flags, terminate_pid
from .resources import memory_snapshot
from .storage import ROOT, atomic_json


def read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


class ResidentUpscaler:
    def __init__(self, store):
        self.store = store
        self.registry = store.data / 'upscale-engine.json'
        self.command_lock = threading.Lock()
        self.lifecycle_lock = threading.RLock()
        # Launch the actual interpreter, not Windows' venv redirector process.
        self.executable = str(Path(getattr(sys, '_base_executable', sys.executable)).resolve())
        self.record = read_json(self.registry)

    def owned(self):
        r = self.record
        if not r or not pid_alive(r.get('pid')):
            return False
        actual = process_path(r['pid'])
        return bool(actual and Path(actual).resolve() == Path(self.executable)
                    and r.get('birth') and process_birth(r['pid']) == r['birth'])

    def status(self):
        if not self.owned():
            return {'state': 'idle', 'loaded': False, 'pid': None}
        value = read_json(Path(self.record['directory']) / 'status.json')
        if value.get('session') != self.record['session']:
            return {'state': 'unknown', 'loaded': False, 'pid': self.record['pid']}
        command = read_json(Path(self.record['directory']) / 'command.json')
        result = read_json(Path(self.record['directory']) / 'result.json')
        if command.get('id') and command.get('id') != result.get('id') and value['state'] in ('idle', 'ready'):
            value['state'] = 'running'
        return value | {'pid': self.record['pid']}

    def start(self):
        with self.lifecycle_lock:
            if self.owned():
                return
            session = uuid.uuid4().hex
            directory = self.store.data / 'upscale-worker' / session
            directory.mkdir(parents=True)
            command = [self.executable, '-m', 'app.upscale_resident', str(directory), str(self.store.root), session]
            environment = os.environ.copy()
            environment['PYTHONPATH'] = os.pathsep.join([str(ROOT), str(Path(sys.prefix) / 'Lib/site-packages')])
            with (directory / 'engine.log').open('w', encoding='utf-8') as log:
                try:
                    process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT,
                                               creationflags=spawn_flags() | getattr(subprocess, 'CREATE_BREAKAWAY_FROM_JOB', 0))
                except OSError:
                    process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT,
                                               creationflags=spawn_flags())
            self.record = {'pid': process.pid, 'birth': process_birth(process.pid), 'session': session,
                           'directory': str(directory), 'exe': self.executable}
            atomic_json(self.registry, self.record)

    def stop(self):
        with self.lifecycle_lock:
            if self.owned() and not terminate_pid(self.record['pid'], timeout=10):
                raise RuntimeError('放大进程尚未退出，未释放任务锁')
            # PID reuse is not ownership: do not terminate an unrelated process.
            self.record = {}
            atomic_json(self.registry, {})

    def call(self, operation, params, cancelled=lambda: False, timeout=1800):
        with self.command_lock:
            if cancelled():
                raise InterruptedError('已取消放大模型操作')
            self.start()
            status = self.status()
            deadline = time.monotonic() + 15
            while status['state'] == 'unknown' and time.monotonic() < deadline and not cancelled():
                time.sleep(.1)
                status = self.status()
            if status['state'] in ('loading', 'running', 'unknown'):
                raise ValueError('放大进程仍在处理上次操作，请等待结束')
            directory = Path(self.record['directory'])
            identity = uuid.uuid4().hex
            atomic_json(directory / 'command.json', {'id': identity, 'op': operation, 'params': params})
            start = time.monotonic()
            while True:
                if cancelled():
                    self.stop()
                    raise InterruptedError('已取消并释放放大模型')
                result = read_json(directory / 'result.json')
                if result.get('id') == identity:
                    if result.get('error'):
                        raise RuntimeError(result['error'])
                    return result
                if not self.owned():
                    reason = read_json(directory / 'status.json').get('error')
                    raise RuntimeError(reason or '放大进程已退出，请重新加载')
                if time.monotonic() - start > timeout:
                    self.stop()
                    raise TimeoutError('放大操作超时，模型已释放')
                time.sleep(.1)


class SDImage(C.Structure):
    _fields_ = [('width', C.c_uint32), ('height', C.c_uint32), ('channel', C.c_uint32),
                ('data', C.POINTER(C.c_uint8))]


class UpscaleContext:
    def __init__(self):
        runtime = ROOT / 'runtime/stable-diffusion.cpp'
        self.dll_directory = os.add_dll_directory(str(runtime))
        os.chdir(runtime)  # ggml discovers the bundled dynamic backends here.
        self.lib = C.CDLL(str(runtime / 'stable-diffusion.dll'))
        self.lib.new_upscaler_ctx.argtypes = [C.c_char_p, C.c_bool, C.c_int, C.c_int, C.c_char_p, C.c_char_p]
        self.lib.new_upscaler_ctx.restype = C.c_void_p
        self.lib.free_upscaler_ctx.argtypes = [C.c_void_p]
        self.lib.free_upscaler_ctx.restype = None
        self.lib.get_upscale_factor.argtypes = [C.c_void_p]
        self.lib.get_upscale_factor.restype = C.c_int
        self.lib.upscale.argtypes = [C.c_void_p, SDImage, C.c_uint32, C.POINTER(C.POINTER(SDImage)), C.POINTER(C.c_int)]
        self.lib.upscale.restype = C.c_bool
        self.lib.free_sd_images.argtypes = [C.POINTER(SDImage), C.c_int]
        self.lib.free_sd_images.restype = None
        self.context, self.tile = None, None

    def load(self, tile):
        if self.context and self.tile == tile:
            return
        self.release()
        model = ROOT / 'models/upscalers/RealESRGAN_x4plus_anime_6B.pth'
        self.context = self.lib.new_upscaler_ctx(str(model).encode('utf-8'), False, 4, tile, b'CUDA0', b'CUDA0')
        if not self.context:
            raise RuntimeError('放大模型加载失败，请释放资源后重试')
        self.tile = tile
        if self.lib.get_upscale_factor(self.context) != 4:
            self.release()
            raise RuntimeError('放大模型倍率不是已验证的 4 倍')

    def release(self):
        if self.context:
            self.lib.free_upscaler_ctx(self.context)
        self.context, self.tile = None, None

    def run(self, source, destination, repeats):
        with Image.open(source) as image:
            current = image.convert('RGB')
        for _ in range(repeats):
            raw = current.tobytes()
            buffer = (C.c_uint8 * len(raw)).from_buffer_copy(raw)
            incoming = SDImage(current.width, current.height, 3, buffer)
            images, count = C.POINTER(SDImage)(), C.c_int()
            try:
                ok = self.lib.upscale(self.context, incoming, 4, C.byref(images), C.byref(count))
                if not ok or count.value != 1 or not images or not images[0].data:
                    raise RuntimeError('放大引擎未返回有效图片')
                output = images[0]
                if (output.width, output.height, output.channel) != (current.width * 4, current.height * 4, 3):
                    raise RuntimeError('放大输出尺寸不符，拒绝把未放大的图片保存为成功')
                current = Image.frombytes('RGB', (output.width, output.height),
                                          C.string_at(output.data, output.width * output.height * 3))
            finally:
                if images:
                    self.lib.free_sd_images(images, count.value)
        current.save(destination, format='PNG')


def worker(directory, root, session):
    directory, root = Path(directory).resolve(), Path(root).resolve()
    status = {'session': session, 'state': 'idle', 'loaded': False, 'tile_size': None}
    status_lock = threading.RLock()

    def publish(**fields):
        with status_lock:
            status.update(fields)
            atomic_json(directory / 'status.json', status)

    def protection():
        while True:
            try:
                settings = read_json(root / 'data/settings.json')
                battery = settings.get('battery_protection', True) and power_snapshot()['on_battery']
                available = memory_snapshot()['available']
                critical = status['state'] in ('loading', 'running') and available is not None and available < 3 * 1024**3
                if battery or critical:
                    publish(state='error', loaded=False, error='电池保护已释放放大模型' if battery else '可用内存低于 3 GiB，已停止放大并释放模型')
                    os._exit(3)
            except Exception:
                pass
            time.sleep(.5)

    publish()
    threading.Thread(target=protection, daemon=True).start()
    context, previous = None, None
    while True:
        command = read_json(directory / 'command.json')
        identity = command.get('id')
        if not identity or identity == previous:
            time.sleep(.1)
            continue
        previous = identity
        try:
            operation, params = command['op'], command['params']
            if operation not in ('load', 'run'):
                raise ValueError('未知放大操作')
            tile = int(params['tile_size'])
            if not 64 <= tile <= 384 or tile % 32:
                raise ValueError('分块大小无效')
            publish(state='loading', error=None)
            if context is None:
                context = UpscaleContext()
            context.load(tile)
            publish(state='ready', loaded=True, tile_size=tile)
            if operation == 'run':
                source, destination = (Path(params[k]).resolve() for k in ('input', 'output'))
                if not all(p.is_relative_to(root / '.tmp') for p in (source, destination)):
                    raise ValueError('放大输入输出不在任务临时目录')
                repeats = int(params['repeats'])
                if repeats not in (1, 2):
                    raise ValueError('重复次数无效')
                publish(state='running')
                context.run(source, destination, repeats)
            publish(state='ready', loaded=True)
            atomic_json(directory / 'result.json', {'id': identity, 'error': None})
        except Exception as exc:
            publish(state='error', error=str(exc), loaded=bool(context and context.context))
            atomic_json(directory / 'result.json', {'id': identity, 'error': str(exc)})


if __name__ == '__main__':
    worker(*sys.argv[1:])
