from __future__ import annotations

import base64
import importlib.metadata
import json
import platform
from pathlib import Path
import queue
import secrets
import socket
import subprocess
import threading
import time
import uuid

import httpx
import pynvml

from .storage import ROOT, sha256
from .png16 import read_header
from .process import (EngineRegistry, pid_alive, process_path, spawn_flags, terminate_pid)
from .resources import resource_snapshot
from .power import power_snapshot
from .timing import JobTiming, historical_seconds, timing_signature


CONTEXT_KEYS = ('diffusion_model', 'text_encoder', 'vae', 'vision', 'max_vram',
                'text_encoder_cpu', 'vae_cpu', 'extra_args')


def same_context(settings, loaded):
    return loaded is not None and all(settings.get(key) == loaded.get(key) for key in CONTEXT_KEYS)


def device_for(key, settings):
    if key in ('text_encoder', 'vision'):
        return 'CPU' if settings.get('text_encoder_cpu') else 'CUDA0'
    if key == 'vae':
        return 'CPU' if settings.get('vae_cpu') else 'CUDA0'
    return 'CUDA0'


def extra_tokens(raw):
    """User-supplied startup flags: one per line, appended verbatim as argv entries (never via a shell)."""
    tokens = []
    for line in str(raw or '').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        tokens.extend(line.split())
    return tokens


def model_status(settings, loaded=None, component_info=None, individual_supported=False):
    result, blockers = [], []
    for key, label in [('diffusion_model', '扩散模型'), ('text_encoder', '文本编码器'), ('vae', 'VAE'), ('vision', '视觉编码器')]:
        value = settings.get(key, '')
        path = Path(value) if value else None
        exists = bool(path and path.is_file())
        state, detail = ('available', '文件就绪，加载后验证兼容性') if exists else ('missing', '未找到文件')
        if not exists and key != 'vision':
            blockers.append(f'{label}文件不存在')
        if exists and key == 'text_encoder' and 'w4a8' in path.name.lower():
            # 实测：w4a8 以 4-bit 打包权重，张量形状为引擎期望的一半（如 [2048,4096] vs [4096,4096]），
            # sd-server 0518b34 在 model metadata 校验阶段即退出。
            state = 'incompatible'
            detail = 'w4a8 为 4-bit 打包权重，张量形状与引擎期望不符（实测加载失败），需改用 Qwen3VL-8B-Instruct 的 GGUF / BF16 / INT8 convrot'
            blockers.append('文本编码器不兼容：现有 w4a8 无法被引擎加载，请配置 Qwen3VL-8B-Instruct 的 GGUF（如 Q4_K_M）或 BF16 / INT8 convrot')
        # Context ownership and per-component weight residency are separate.
        resident = bool(exists and loaded and loaded.get(key) == value)
        residency = (component_info or {}).get(key)
        weights_loaded = resident if residency is None else bool(residency.get('loaded_tensors'))
        result.append({'key': key, 'label': label, 'path': value, 'name': path.name if path else '未配置',
                       'size': path.stat().st_size if exists else 0, 'state': state, 'detail': detail,
                       'optional': key == 'vision', 'device': device_for(key, loaded if resident else settings),
                       'loaded': weights_loaded, 'residency': residency,
                       'weight_placement': 'custom' if '--params-backend' in extra_tokens(settings.get('extra_args')) else 'CPU',
                       'eager_load': bool((loaded if resident else settings).get('eager_load') or '--eager-load' in extra_tokens((loaded if resident else settings).get('extra_args')))})
    if not (ROOT / 'runtime/stable-diffusion.cpp/sd-server.exe').is_file():
        blockers.append('推理运行库不存在')
    return {'components': result, 'blockers': blockers, 'files_ready': not blockers,
            'individual_preload_supported': individual_supported}


class Engine:
    def __init__(self, store):
        self.store = store
        self.process = None
        self.pid = None
        self.adopted = False
        self.registry = EngineRegistry(store.data)
        self.url = None
        self.state = 'idle'
        self.error = None
        self.started = None
        self.loaded_settings = None
        self.last_release = None
        self.lock = threading.RLock()
        self.process_lock = threading.RLock()
        self.operation_lock = threading.Lock()
        self.pending = queue.Queue()
        self.jobs = {}
        self.stopping = threading.Event()
        self.load_cancelled = threading.Event()
        self.active = None
        self.draining = None
        self.resume_lost = False
        self.hash_cache = {}
        self.output_capabilities = None
        self._png16_status = None
        self._png16_checked_at = 0.0
        self.power = power_snapshot()
        self.power_blocked = bool(store.settings().get('battery_protection', True) and self.power['on_battery'])
        self.power_stopping = False
        self.last_power_event = None
        self.component_info = None
        self.component_checked_at = 0.0
        self.component_operation = None
        self._last_tick = time.time()
        self.image_tools = None
        interrupted = []
        # A running engine outlives this service: cancel what was in flight, keep
        # its request parameters, and re-attach to the engine instead of killing it.
        for job in store.jobs():
            if job['status'] in ('queued', 'running') or job.get('native_pending') or job.get('cli_pid'):
                job.update(status='cancelled', stage='服务重启，任务已取消',
                           error='服务重启，任务已取消；模型保持加载', finished=time.time())
                self._preserve_parameters(job)
                store.save_job(job)
                if job.get('native_id') or job.get('cli_pid'):
                    interrupted.append(job)
        self.adopt()
        if interrupted:
            self.draining = 'recovery'
            self.operation_lock.acquire()
            threading.Thread(target=self._recover_operations, args=(interrupted,), daemon=True).start()
        self.worker = threading.Thread(target=self._worker, daemon=True)
        self.worker.start()
        self.power_worker = threading.Thread(target=self._monitor_power, daemon=True)
        self.power_worker.start()

    def _preserve_parameters(self, job):
        """Keep the full request of an interrupted job so it can be submitted again."""
        record = {key: value for key, value in job.items() if not key.startswith('_')}
        record['interrupted_at'] = time.time()
        path = self.store.data / 'interrupted'
        path.mkdir(parents=True, exist_ok=True)
        try:
            (path / f'{job["id"]}.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        except OSError:
            pass

    def _drain_after_restart(self, native_id):
        """The engine cannot interrupt sampling; wait for it to finish and discard the result."""
        while not self.stopping.is_set() and self._engine_alive()[0]:
            try:
                status = self.request('GET', f'/sdcpp/v1/jobs/{native_id}', timeout=5)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    return True
                self.stopping.wait(1)
                continue
            except Exception:
                self.stopping.wait(1)
                continue
            if status.get('status') in ('completed', 'failed', 'cancelled'):
                return True
            time.sleep(1)
        return not self.stopping.is_set()

    def _recover_operations(self, jobs):
        try:
            for job in jobs:
                if job.get('cli_pid'):
                    pid = job['cli_pid']
                    expected = ROOT / 'runtime/stable-diffusion.cpp/sd-cli.exe'
                    while not self.stopping.is_set() and pid_alive(pid):
                        actual = process_path(pid)
                        if actual and Path(actual).resolve() != expected.resolve():
                            break
                        # Unknown identity is not proof that the old GPU task ended.
                        self.stopping.wait(1)
                if job.get('native_id'):
                    if not self._drain_after_restart(job['native_id']):
                        break
                if not self.stopping.is_set():
                    job['native_pending'] = False
                    job.pop('cli_pid', None)
                    self.store.save_job(job)
        finally:
            self.draining = None
            self.operation_lock.release()

    def _engine_alive(self):
        process = self.process
        if process is not None:
            try:
                alive = process.poll() is None
            except Exception:
                alive = False
            return alive, (process.pid if alive else None)
        if self.pid and pid_alive(self.pid):
            return True, self.pid
        return False, None

    def adopt(self):
        """Re-attach to an engine that survived a service restart."""
        record = self.registry.read()
        if not record:
            return
        pid, port = int(record['pid']), int(record['port'])
        exe = ROOT / 'runtime/stable-diffusion.cpp/sd-server.exe'
        actual = process_path(pid)
        if not pid_alive(pid):
            self.registry.clear()
            return
        if actual and Path(actual).resolve() != exe.resolve():
            # Another program reused the PID: drop the stale record, never kill it.
            self.registry.clear()
            return
        self.pid, self.url, self.adopted = pid, f'http://127.0.0.1:{port}', True
        self.process = None
        try:
            status = self.request('GET', '/sdcpp/v1/models/status', timeout=3)
        except Exception:
            self.state, self.resume_lost = 'error', True
            self.error = '已有推理进程暂时无响应；请恢复连接或手动卸载，不能启动第二套任务'
            return
        if status.get('state') == 'error':
            self.state, self.error = 'error', status.get('error') or '推理引擎启动失败'
            self.loaded_settings = None
        else:
            self.state, self.error = 'ready', None
            self.loaded_settings = record.get('settings')

    def _monitor_power(self):
        while not self.stopping.wait(1):
            tick = time.time()
            if tick - self._last_tick > 30:
                self.after_resume()
            self._last_tick = tick
            self.update_power()

    def after_resume(self):
        """Sleep/hibernate froze this process; verify the engine survived the gap.

        Nothing is unloaded just because the lid closed. Only an engine that no
        longer answers is closed, so it cannot keep VRAM while being unusable.
        """
        alive, _ = self._engine_alive()
        if not alive:
            self.resume_lost = False
            return
        try:
            self.request('GET', '/sdcpp/v1/models/status', timeout=5)
        except Exception:
            self.resume_lost = True
            self.state = 'error'
            self.error = '休眠唤醒后推理引擎无响应，已关闭引擎；请重新加载模型'
            self._stop_process()
            return
        self.resume_lost = False

    def update_power(self):
        snapshot = power_snapshot()
        with self.lock:
            self.power = snapshot
            enabled = self.store.settings().get('battery_protection', True)
            if not enabled or snapshot['ac_connected'] is True:
                self.power_blocked = False
            elif snapshot['on_battery']:
                self.power_blocked = True
            # Unknown readings never clear a previously confirmed battery stop.
            stop = self.power_blocked and not self.power_stopping and bool(self.process or self.active or not self.pending.empty() or self.state == 'loading')
            if stop:
                self.power_stopping = True
                reason = '电池供电保护：已停止任务并释放模型，插电后请手动重新生成'
                affected = []
                for job in self.jobs.values():
                    if job['status'] in ('queued', 'running'):
                        job.update(_cancel=True, cancel_reason=reason)
                        if job['status'] == 'queued':
                            job.update(status='cancelled', stage=reason, finished=time.time())
                        self.store.save_job(job)
                        affected.append(job['id'])
                self.load_cancelled.set()
                self.last_power_event = {'timestamp': time.time(), 'reason': reason, 'job_ids': affected}
        if stop:
            try:
                if self.image_tools:
                    self.image_tools.model_cancel.set()
                    self.image_tools.upscaler.stop()
                # Terminate the owned process before waiting for an in-flight HTTP operation.
                self._stop_process()
                with self.operation_lock:
                    self._stop_process()
                    self.state, self.error = 'idle', None
            finally:
                with self.lock:
                    self.power_stopping = False
        return snapshot

    def _check_power(self):
        self.update_power()
        if self.power_blocked or self.power_stopping:
            raise ValueError('当前使用电池供电，电池保护已阻止加载或生成；请先连接电源')

    def request(self, method, path, **kwargs):
        url = self.url
        alive, _ = self._engine_alive()
        if not url or not alive:
            raise RuntimeError('推理服务未启动')
        with httpx.Client(trust_env=False, timeout=kwargs.pop('timeout', 15)) as client:
            response = client.request(method, url + path, **kwargs)
            if response.is_error:
                raise RuntimeError(response.text[:1000])
            return response.json()

    def status(self):
        alive, pid = self._engine_alive()
        operating = self.component_operation is not None
        if not alive and self.state in ('loading', 'ready'):
            # The engine can also be closed from the desktop while the page is idle.
            self.state = 'error'
            self.error = '推理引擎已退出，模型需重新加载'
            self.loaded_settings = None
            self.component_info = None
            self.pid, self.url, self.adopted = None, None, False
            self.registry.clear()
        return {'state': 'loading' if operating else self.state, 'error': self.error, 'active_job': self.active,
                'elapsed': round(time.time() - self.started, 1) if (self.state == 'loading' or operating) and self.started else 0,
                'loaded': self.loaded_settings is not None,
                'queued': self.pending.qsize(),
                'last_release': self.last_release,
                'pid': pid, 'adopted': self.adopted, 'draining': self.draining,
                'engine_lost': self.resume_lost,
                'power': self.power | {'protection_enabled': self.store.settings().get('battery_protection', True),
                                       'blocked': self.power_blocked or self.power_stopping,
                                       'stopping': self.power_stopping, 'last_event': self.last_power_event},
                'component_operation': self.component_operation}

    def _read_capabilities(self):
        value = self.request('GET', '/sdcpp/v1/capabilities', timeout=5)
        if not isinstance(value, dict):
            raise RuntimeError('引擎返回了无效的能力信息')
        return value

    def component_support(self):
        manifest = self.store.root / 'runtime/stable-diffusion.cpp/component-build.json'
        try:
            return json.loads(manifest.read_text(encoding='utf-8-sig')).get('individual_preload_supported') is True
        except (OSError, ValueError):
            return False

    def components_status(self, refresh=False):
        alive, _ = self._engine_alive()
        if self.state != 'ready' or not alive:
            # Never keep reporting weights that belong to a process that is gone.
            self.component_info = None
            return None
        if not refresh and time.monotonic() - self.component_checked_at < 3:
            return self.component_info
        self.component_checked_at = time.monotonic()
        try:
            value = self.request('GET', '/sdcpp/v1/models/components', timeout=2)
            if isinstance(value.get('components'), dict):
                self.component_info = value['components']
        except Exception:
            pass
        return self.component_info

    def operate_component(self, key, action):
        if action == 'load':
            self._check_power()
        with self.lock:
            if action == 'load' and (self.power_blocked or self.power_stopping):
                raise ValueError('电池保护已阻止组件加载')
            if self.draining or self.state == 'loading' or self.component_operation or self.active or not self.pending.empty():
                raise ValueError('已有模型操作或生成任务，请等待完成')
            if not self.component_support():
                raise ValueError('当前运行库尚未部署独立组件管理接口')
            settings = self.store.settings()
            alive, _ = self._engine_alive()
            existing = self.loaded_settings is not None and alive
            if action == 'load':
                if existing:
                    if not same_context(settings, self.loaded_settings):
                        raise ValueError('模型组合或运行设置已变更；单组件加载不会重建整套上下文，请先恢复设置或手动卸载整套模型')
                    if not settings.get(key) or not Path(settings[key]).is_file():
                        raise ValueError('未找到此组件文件')
                else:
                    blockers = model_status(settings)['blockers']
                    if blockers or not settings.get(key):
                        raise ValueError('；'.join(blockers) or '未配置此组件文件')
            elif not existing:
                raise ValueError('尚未创建模型上下文，无需释放组件')
            self.state, self.error, self.started = 'loading', None, time.time()
            self.load_cancelled = threading.Event()
            self.component_operation = {'key': key, 'action': action}
            threading.Thread(target=self._component_worker,
                             args=(key, action, settings, self.load_cancelled), daemon=True).start()

    def _component_worker(self, key, action, settings, cancelled):
        with self.operation_lock:
            try:
                if action == 'load':
                    self._load(settings, cancelled=cancelled.is_set, eager_override=False)
                if cancelled.is_set() or self.power_blocked:
                    raise InterruptedError('已取消组件操作')
                self.state = 'loading'
                value = self.request('POST', f'/sdcpp/v1/models/components/{key}/{action}', timeout=1800)
                if cancelled.is_set() or self.power_blocked:
                    raise InterruptedError('已取消组件操作')
                self.component_info = value['components']
                self.component_checked_at = time.monotonic()
                self.state, self.error = 'ready', None
            except Exception as exc:
                if cancelled.is_set() or self.power_blocked or self.stopping.is_set():
                    self._stop_process()
                    self.state, self.error = 'idle', None
                else:
                    self.state = 'ready' if self.loaded_settings is not None else 'error'
                    self.error = str(exc)
            finally:
                self.component_operation = None

    def capabilities(self):
        if self.output_capabilities is None:
            try:
                self.output_capabilities = self._read_capabilities()
            except Exception:
                return {}
        return self.output_capabilities

    def png16_status(self, refresh=False):
        # Three states: unknown (engine not loaded / probe failed), supported,
        # unsupported (engine answered without advertizing 16). Never loads a model.
        alive, _ = self._engine_alive()
        if self.state != 'ready' or not alive:
            return {'state': 'unknown', 'levels': None,
                    'detail': '引擎未加载，尚未确认 16 位输出能力；不会自动加载模型探测'}
        if self._png16_status is not None:
            return self._png16_status
        now = time.monotonic()
        if not refresh and now - self._png16_checked_at < 5:
            return {'state': 'unknown', 'levels': None,
                    'detail': '尚未确认 16 位输出能力；稍后重试'}
        self._png16_checked_at = now
        try:
            caps = self._read_capabilities()
        except Exception as exc:
            return {'state': 'unknown', 'levels': None, 'detail': f'无法读取引擎能力：{exc}'}
        self.output_capabilities = caps
        levels = caps.get('output_bit_depths')
        if not isinstance(levels, list):
            self._png16_status = {'state': 'unsupported', 'levels': None,
                                  'detail': '当前引擎未声明 output_bit_depths，16 bit 不可用'}
        elif 16 in levels:
            self._png16_status = {'state': 'supported', 'levels': levels,
                                  'detail': '当前引擎支持 16 bit 输出'}
        else:
            self._png16_status = {'state': 'unsupported', 'levels': levels,
                                  'detail': '当前引擎只声明了 8 bit 输出'}
        return self._png16_status

    def supports_png16(self):
        # Fail closed: unknown or unsupported both reject a 16-bit request.
        return self.png16_status(refresh=True)['state'] == 'supported'

    def preload(self, settings):
        self._check_power()
        with self.lock:
            if self.power_blocked or self.power_stopping:
                raise ValueError('电池保护已阻止模型加载')
            if self.draining or self.state == 'loading' or self.component_operation or self.active or not self.pending.empty():
                raise ValueError('已有模型操作或生成任务，请等待完成')
            if self.resume_lost:
                raise ValueError('休眠唤醒后推理引擎已失效，请重新加载模型')
            blockers = model_status(settings)['blockers']
            if blockers:
                raise ValueError('；'.join(blockers))
            alive, _ = self._engine_alive()
            if alive and same_context(settings, self.loaded_settings):
                # Already loaded: reuse instead of restarting the process.
                self.state, self.error = 'ready', None
                return {'state': 'ready', 'reused': True}
            self.state, self.error, self.started = 'loading', None, time.time()
            self.load_cancelled = threading.Event()
            threading.Thread(target=self._preload, args=(settings.copy(), self.load_cancelled), daemon=True).start()
            return {'state': 'loading', 'reused': False}

    def _preload(self, settings, cancelled):
        with self.operation_lock:
            if cancelled.is_set():
                return
            try:
                self._load(settings, cancelled=cancelled.is_set)
            except Exception as exc:
                self._stop_process()
                self.state, self.error = ('idle', None) if cancelled.is_set() else ('error', str(exc))

    def _load(self, settings, cancelled=None, eager_override=None):
        if cancelled and cancelled():
            raise InterruptedError('已取消模型加载')
        alive, _ = self._engine_alive()
        if alive and same_context(settings, self.loaded_settings) and (self.state == 'ready' or eager_override is False):
            # Same context already running (possibly adopted after a restart): adopt, do not respawn.
            self.state, self.error = 'ready', None
            return
        self._stop_process()
        self.state, self.started = 'loading', time.time()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        self.url = f'http://127.0.0.1:{port}'
        exe = ROOT / 'runtime/stable-diffusion.cpp/sd-server.exe'
        command = [str(exe), '--listen-ip', '127.0.0.1', '--listen-port', str(port),
                   '--diffusion-model', settings['diffusion_model'], '--llm', settings['text_encoder'],
                   '--vae', settings['vae'], '--offload-to-cpu', '--diffusion-fa', '--vae-tiling',
                   '--max-vram', str(settings['max_vram']), '--rng', 'cpu',
                   '--backend', f'diffusion=CUDA0,vae={device_for("vae", settings)},te={device_for("text_encoder", settings)}']
        if settings.get('eager_load') if eager_override is None else eager_override:
            command += ['--eager-load']
        tokens = extra_tokens(settings.get('extra_args'))
        command += [t for t in tokens if t != '--eager-load' or eager_override is not False]
        if settings.get('vision'):
            command += ['--llm_vision', settings['vision']]
        log_path = ROOT / 'logs/engine.log'
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.process_lock:
            if self.stopping.is_set() or self.power_blocked or self.power_stopping or (cancelled and cancelled()):
                raise InterruptedError('已停止模型加载')
            flags = spawn_flags()
            breakaway = getattr(subprocess, 'CREATE_BREAKAWAY_FROM_JOB', 0x01000000)
            with log_path.open('w', encoding='utf-8') as log:
                try:
                    self.process = subprocess.Popen(command, cwd=exe.parent, stdout=log, stderr=subprocess.STDOUT,
                                                    creationflags=flags | breakaway)
                except OSError:
                    # The surrounding job object may forbid breakaway; stay detached anyway.
                    self.process = subprocess.Popen(command, cwd=exe.parent, stdout=log, stderr=subprocess.STDOUT,
                                                    creationflags=flags)
            self.pid = self.process.pid
            self.adopted = False
            self.resume_lost = False
            self.registry.write(self.pid, port, settings, exe)
        deadline = time.monotonic() + 1800
        while time.monotonic() < deadline and not self.stopping.is_set():
            if cancelled and cancelled():
                raise InterruptedError('已取消模型加载')
            process = self.process
            if process is None or process.poll() is not None:
                raise RuntimeError('模型加载失败：请查看 logs/engine.log')
            try:
                status = self.request('GET', '/sdcpp/v1/models/status', timeout=2)
                if status.get('state') == 'ready':
                    self.loaded_settings = settings.copy()
                    self.state, self.error = 'ready', None
                    return
                if status.get('state') == 'error':
                    raise RuntimeError(status.get('error', '加载失败'))
            except httpx.RequestError:
                pass
            time.sleep(1)
        raise RuntimeError('模型加载已停止或超过 30 分钟')

    def _stop_process(self):
        with self.process_lock:
            self._stop_owned_process()

    def _stop_owned_process(self):
        process = self.process
        if process is not None:
            try:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
            except Exception:
                pass
        elif self.pid:
            # Adopted engine: there is no Popen object, so end it by PID.
            if pid_alive(self.pid):
                actual = process_path(self.pid)
                if not actual or Path(actual).resolve() != (ROOT / 'runtime/stable-diffusion.cpp/sd-server.exe').resolve():
                    raise RuntimeError('无法验证推理进程身份，未终止该进程')
                if not terminate_pid(self.pid, timeout=10):
                    raise RuntimeError('推理进程尚未退出，保留占用状态')
        self.process, self.pid, self.url, self.loaded_settings = None, None, None, None
        self.adopted = False
        self.registry.clear()
        self.output_capabilities, self._png16_status = None, None
        self.component_info = None
        self._png16_checked_at = 0.0

    def unload(self):
        with self.lock:
            if self.active or not self.pending.empty():
                raise ValueError('请先等待或取消当前任务')
            alive, pid = self._engine_alive()
            before = resource_snapshot(pid) if pid is not None else None
            self.load_cancelled.set()
            if alive and not self.component_operation and not self.active:
                # Free the weights first so the engine drops them before it exits.
                # Skipped while another model request is in flight: that request must
                # not delay the release, the process is ended right after.
                try:
                    self.request('POST', '/sdcpp/v1/models/unload', json={}, timeout=5)
                except Exception:
                    pass
            # Interrupt an in-flight native load request before joining the worker.
            # Waiting for operation_lock first could block for the full load timeout.
            self._stop_process()
            with self.operation_lock:
                self._stop_process()
                self.state, self.error = 'idle', None
                self.resume_lost = False
                if before is not None:
                    self.last_release = {
                        'pid': pid, 'timestamp': time.time(),
                        'process_rss': before['memory']['process_rss'],
                        'process_private': before['memory']['process_private'],
                        'process_gpu': (before['gpu'] or {}).get('process_used'),
                    }

    def submit(self, config):
        self._check_power()
        settings = self.store.settings()
        blockers = model_status(settings)['blockers']
        if blockers:
            raise ValueError('；'.join(blockers))
        if config.get('input_id') and not settings.get('vision') and settings['text_encoder'].lower().endswith('.gguf'):
            raise ValueError('GGUF 编码器进行参考图编辑还需要配置视觉编码器 mmproj')
        if config.get('output_bit_depth') == 16 and self.state == 'ready' and not self.supports_png16():
            raise ValueError('当前推理引擎未声明 16 位输出能力（output_bit_depths 不含 16），已拒绝；不会降级为 8 bit。')
        with self.lock:
            if self.draining:
                raise ValueError('正在等待上次推理结束，请稍后提交')
            if self.power_blocked or self.power_stopping:
                raise ValueError('电池保护已阻止生成')
            if self.resume_lost:
                raise ValueError('休眠唤醒后推理引擎已失效，请重新加载模型')
            if self.state == 'loading' or self.component_operation:
                raise ValueError('模型正在加载，请等待完成')
            if self.pending.qsize() >= 8:
                raise ValueError('队列已满，请等待现有任务完成')
            identity = uuid.uuid4().hex
            if config['seed'] == -1:
                config['seed'] = secrets.randbelow(2**32)
            job = dict(id=identity, status='queued', created=time.time(), started=None, finished=None,
                       stage='排队中', progress=0, step=0, total_steps=config['steps'], completed_images=0,
                       image_ids=[], error=None, config=config, _settings=settings, _cancel=False)
            self.jobs[identity] = job
            self.store.save_job(job)
            self.pending.put(identity)
            return self.public(job)

    def submit_image_tool(self, mode, input_id, input_info, params):
        self._check_power()
        with self.lock:
            if self.stopping.is_set() or self.power_blocked or self.power_stopping or self.draining or self.resume_lost:
                raise ValueError('引擎恢复或电源保护期间不能提交')
            if self.state == 'loading' or self.component_operation:
                raise ValueError('模型操作中，请等待完成')
            if self.pending.qsize() >= 8:
                raise ValueError('队列已满')
            identity = uuid.uuid4().hex
            job = dict(id=identity, task_type=mode, input=input_info, params=params,
                       config=params | {'input_id': input_id}, status='queued', created=time.time(),
                       started=None, finished=None, stage='排队中', progress=None, image_ids=[],
                       completed_images=0, error=None, result=None, _settings=self.store.settings(), _cancel=False)
            self.jobs[identity] = job
            self.store.save_job(job)
            self.pending.put(identity)
            return self.public(job)

    def public(self, job):
        with self.lock:
            result = {k: v for k, v in job.items() if not k.startswith('_')}
            timing = job.get('_timing')
            result['time_estimate'] = timing.snapshot(time.monotonic()) if timing and job['status'] == 'running' else None
            return result

    def list_jobs(self, task_types=None):
        with self.store.db() as db:
            private = {r[0] for r in db.execute('SELECT job_id FROM private_jobs')}
        stored = {job['id']: job for job in self.store.jobs(None if task_types else 50)}
        with self.lock:
            stored.update({k: self.public(v) for k, v in self.jobs.items()})
        # Filter before the 50-item window: image-tool history must not disappear
        # just because many generation jobs were created more recently.
        values = [job for job in stored.values()
                  if job['id'] not in private and (not task_types or job.get('task_type') in task_types)]
        return sorted(values, key=lambda job: job['created'], reverse=True)[:50]

    def cancel(self, identity):
        with self.lock:
            job = self.jobs.get(identity)
            if not job:
                raise ValueError('此服务中没有该任务，请在提交任务的网页地址取消')
            if job['status'] not in ('queued', 'running'):
                return self.public(job)
            job.update(_cancel=True, cancellation_requested=True, stage='正在取消；模型保持加载')
            if self.active != identity:
                job.update(status='cancelled', stage='已取消', finished=time.time())
                self.store.save_job(job)
                return self.public(job)
            self.store.save_job(job)
            # The native engine cannot interrupt sampling. Stop tracking the job,
            # wait for that sample to finish on its own, and keep the model loaded.
            self.draining = job.get('native_id')
            return self.public(job)

    def _drain_job(self, job):
        native_id = job.get('native_id')
        if not native_id:
            self.draining = None
            return
        self.draining = native_id
        job['stage'] = '已取消；等待引擎结束当前采样（模型保持加载）'
        while not self.stopping.is_set() and self._engine_alive()[0]:
            try:
                status = self.request('GET', f'/sdcpp/v1/jobs/{native_id}', timeout=10)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    job['native_pending'] = False
                    break
                self.stopping.wait(1)
                continue
            except Exception:
                job['stage'] = '引擎连接中断，等待恢复；当前 GPU 任务尚未确认结束'
                self.stopping.wait(1)
                continue
            if status.get('status') in ('completed', 'failed', 'cancelled'):
                job['native_pending'] = False
                break
            time.sleep(1)
        self.draining = None
        if not self._engine_alive()[0]:
            job['native_pending'] = False
        job['stage'] = '已取消'

    def model_hashes(self, settings):
        result = {}
        for key in ('diffusion_model', 'text_encoder', 'vae', 'vision'):
            if not settings.get(key):
                continue
            path = Path(settings[key])
            stat = path.stat()
            signature = (str(path), stat.st_size, stat.st_mtime_ns)
            if signature not in self.hash_cache:
                self.hash_cache[signature] = sha256(path, cancelled=lambda: self.stopping.is_set() or self.power_blocked or self.jobs.get(self.active, {}).get('_cancel', False))
            result[key] = {'path': str(path), 'sha256': self.hash_cache[signature], 'bytes': stat.st_size}
        return result

    def environment_record(self):
        record = {'python': platform.python_version(), 'platform': platform.platform(),
                  'packages': {d.metadata['Name']: d.version for d in importlib.metadata.distributions()},
                  'app_source_sha256': {p.name: sha256(p) for p in (ROOT / 'app').glob('*.py')}}
        try:
            pynvml.nvmlInit()
            try:
                record['gpu'] = pynvml.nvmlDeviceGetName(pynvml.nvmlDeviceGetHandleByIndex(0))
                record['driver'] = pynvml.nvmlSystemGetDriverVersion()
            finally:
                pynvml.nvmlShutdown()
        except pynvml.NVMLError:
            record['gpu'] = 'unavailable'
        return record

    def _worker(self):
        while not self.stopping.is_set():
            try:
                identity = self.pending.get(timeout=0.5)
            except queue.Empty:
                continue
            job = self.jobs[identity]
            with self.lock:
                self.active = identity
            try:
                if job['_cancel']:
                    job['status'] = 'cancelled'
                    continue
                if job.get('task_type') in ('upscale', 'outpaint'):
                    with self.operation_lock:
                        if self.stopping.is_set() or job['_cancel']:
                            raise InterruptedError('已停止任务')
                        job.update(status='running', started=time.time(), stage='准备图像处理')
                        self.store.save_job(job)
                        self.image_tools.run(job)
                        if job['task_type'] == 'outpaint' and not job['_settings']['keep_loaded']:
                            self._stop_process()
                            self.state = 'idle'
                    continue
                settings, config = job['_settings'], job['config']
                warm = bool(self.state == 'ready' and self.loaded_settings == settings)
                signature = timing_signature(config, settings, warm, self.hash_cache)
                history, count = historical_seconds(self.store.jobs(), signature)
                with self.lock:
                    if job['_cancel']:
                        continue
                    job.update(status='running', started=time.time(), stage='加载模型',
                               timing_signature=signature, _timing=JobTiming(time.monotonic(), history, count))
                with self.operation_lock:
                    self._load(settings, cancelled=lambda: job['_cancel'])
                    output_bit_depth = 8 if config.get('output_bit_depth') is None else int(config['output_bit_depth'])
                    if output_bit_depth == 16 and not self.supports_png16():
                        raise RuntimeError('当前推理引擎未声明 16 位输出能力（output_bit_depths 不含 16），已拒绝生成；未降级为 8 bit。')
                    job['stage'] = '计算模型 SHA256（首次运行）'
                    hashes = self.model_hashes(settings)
                    environment = self.environment_record()
                    for index in range(config['batch_count']):
                        if job['_cancel'] or self.stopping.is_set():
                            job['status'] = 'cancelled'
                            break
                        seed = config['seed'] + index
                        body = dict(prompt=config['prompt'], negative_prompt=config['negative_prompt'],
                                    width=config['width'], height=config['height'], seed=seed, batch_count=1,
                                    sample_params={'sample_method': config['sampler'], 'scheduler': config['scheduler'],
                                                   'sample_steps': config['steps'], 'guidance': {'txt_cfg': config['cfg']}},
                                    vae_tiling_params={'enabled': config['vae_tiling']}, preview='proj', preview_interval=1,
                                    output_format='png', embed_image_metadata=True)
                        if output_bit_depth == 16:
                            body['output_bit_depth'] = 16
                        if config['scheduler'] == 'default':
                            body['sample_params'].pop('scheduler')
                        input_meta = None
                        if config.get('input_id'):
                            path = self.store.data / 'inputs' / f'{config["input_id"]}.png'
                            body['ref_images'] = [base64.b64encode(path.read_bytes()).decode()]
                            input_meta = {'id': config['input_id'], 'sha256': sha256(path)}
                        with self.lock:
                            job['_timing'].begin_image(index + 1, config['steps'])
                            job.update(step=0, total_steps=config['steps'], stage=f'第 {index + 1} / {config["batch_count"]} 张 · 编码 / 等待首步')
                        native = self.request('POST', '/sdcpp/v1/img_gen', json=body)
                        job['_native_id'] = native['id']
                        job['native_id'] = native['id']
                        job['native_pending'] = True
                        self.store.save_job(job)
                        status = {'status': 'cancelled'}
                        while not self.stopping.is_set():
                            if job['_cancel']:
                                break
                            status = self.request('GET', f'/sdcpp/v1/jobs/{native["id"]}')
                            preview = status.get('preview') or {}
                            with self.lock:
                                timing = job['_timing']
                                timing.observe(preview.get('step', timing.step), preview.get('total_steps', config['steps']), time.monotonic())
                                phase = {'encoding': '编码 / 等待首步', 'sampling': '采样', 'finishing': '解码 / 保存'}[timing.phase]
                                job.update(stage=f'第 {index + 1} / {config["batch_count"]} 张 · {phase}',
                                           step=timing.step, total_steps=timing.total_steps)
                                job['progress'] = min(0.99, (index + job['step'] / max(job['total_steps'], 1)) / config['batch_count'])
                            if status['status'] in ('completed', 'failed', 'cancelled'):
                                job['native_pending'] = False
                                break
                            time.sleep(0.7)
                        job.pop('_native_id', None)
                        if status['status'] == 'cancelled' or job['_cancel']:
                            job['status'] = 'cancelled'
                            if job['_cancel']:
                                self._drain_job(job)
                            break
                        if status['status'] != 'completed':
                            raise RuntimeError(str(status.get('error') or '任务中断'))
                        images = (status.get('result') or {}).get('images', [])
                        if not images:
                            raise RuntimeError('推理服务没有返回图片')
                        with self.lock:
                            job['_timing'].phase = 'finishing'
                            job['stage'] = f'保存第 {index + 1} / {config["batch_count"]} 张'
                        for image in images:
                            elapsed = ((status.get('completed') or 0) - (status.get('started') or 0)) / 1000
                            record = config | dict(seed=seed, batch_count=1, source='qwen-studio',
                                                   generated_at=time.time(), elapsed_seconds=elapsed,
                                                   models=hashes, input=input_meta, native_request=body | {'ref_images': 'see input SHA256' if input_meta else []},
                                                   engine='stable-diffusion.cpp 0518b34', runtime_settings=settings,
                                                   environment=environment,
                                                   reproducibility='固定模型、软件、硬件与参数便于复现，跨引擎不保证逐像素相同。')
                            raw = base64.b64decode(image['b64_json'])
                            if output_bit_depth == 16:
                                header = read_header(raw[:33])
                                declared = image.get('bit_depth')
                                if header['bit_depth'] != 16 or header['color_type'] not in (2, 6):
                                    raise RuntimeError('推理服务返回的图片不是 16 位 PNG，已拒绝保存（未降级为 8 bit）')
                                if declared != 16:
                                    raise RuntimeError('推理服务未声明该图为 16 位，已拒绝保存（未降级为 8 bit）')
                                record |= {'output_bit_depth': 16, 'bit_depth_requested': 16,
                                           'bit_depth_engine_declared': declared,
                                           'precision_source': 'engine_api_generate_image_u16',
                                           'precision_source_note': '引擎声明该接口把 VAE 浮点样本直接转换为 uint16；文件位深与引擎返回声明分别记录，不代表像素具有 65536 个有效等级。',
                                           'precision_evidence': ['png_ihdr_bit_depth_16', 'engine_result_bit_depth_16',
                                                                  'engine_capabilities_include_16'],
                                           'engine_api': 'generate_image_u16',
                                           'engine_output_bit_depths': self.png16_status().get('levels')}
                                job['image_ids'].append(self.store.save_output16(raw, record))
                            else:
                                # Keep 8-bit metadata and files exactly as before.
                                record.pop('output_bit_depth', None)
                                job['image_ids'].append(self.store.save_output(raw, record))
                        job['completed_images'] = index + 1
                    else:
                        job.update(status='completed', stage='生成完成', progress=1)
                    if not settings['keep_loaded']:
                        self._stop_process()
                        self.state = 'idle'
            except Exception as exc:
                if job.get('native_pending') and not self.stopping.is_set():
                    with self.operation_lock:
                        self._drain_job(job)
                cancelled = job['_cancel'] or self.stopping.is_set()
                job.update(status='cancelled' if cancelled else 'failed', error=None if cancelled else str(exc),
                           stage='已取消' if cancelled else '任务失败')
                if self.state == 'loading':
                    self._stop_process()
                    self.state, self.error = ('idle', None) if cancelled else ('error', str(exc))
            finally:
                with self.lock:
                    if job['_cancel']:
                        # Cancelling a generation never unloads the model.
                        job.update(status='cancelled', error=None, stage='已取消')
                    job.pop('_native_id', None)
                    job['finished'] = job.get('finished') or time.time()
                    self.store.save_job(job)
                    self.active = None
                self.pending.task_done()

    def close(self):
        """Service shutdown: detach, never end the engine or unload the model."""
        self.stopping.set()
        self.load_cancelled.set()
        self.power_worker.join(timeout=3)
        # A CLI is cancellable; let its owner reap it before the service exits.
        if self.active and self.jobs.get(self.active, {}).get('task_type') == 'upscale':
            self.worker.join(timeout=12)
