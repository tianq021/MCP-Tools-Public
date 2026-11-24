"""Resource admission and manual model controls shared by image-tool routes."""
from contextlib import contextmanager
from pathlib import Path
import threading
import time

from .engine import same_context
from .resources import resource_snapshot, memory_snapshot
from .upscale_resident import ResidentUpscaler

GIB = 1024**3
MODEL_KEYS = ('diffusion_model', 'text_encoder', 'vae', 'vision')


class ImageToolModels:
    def init_models(self):
        self.upscaler = ResidentUpscaler(self.store)
        self.model_operation = None
        self.model_cancel = threading.Event()
        self.model_error = {'upscale': None, 'outpaint': None}
        if self.upscaler.status()['state'] in ('loading', 'running', 'unknown'):
            # Serialize against a worker left running by a previous web service.
            self.engine.draining = 'upscale-recovery'
            threading.Thread(target=self._recover_upscaler, daemon=True).start()

    def _recover_upscaler(self):
        with self.engine.operation_lock:
            while not self.engine.stopping.is_set() and self.upscaler.status()['state'] in ('loading', 'running', 'unknown'):
                self.engine.stopping.wait(.3)
            if self.engine.draining == 'upscale-recovery':
                self.engine.draining = None

    def model_status(self):
        alive, pid = self.engine._engine_alive()
        components = self.engine.components_status() if alive else None
        complete = bool(components and all(
            (components.get(k) or {}).get('loaded_tensors', 0) > 0
            for k in MODEL_KEYS))
        value = {
            'upscale': self.upscaler.status(),
            'outpaint': {'state': self.engine.state if alive else 'idle', 'loaded': complete,
                         'context_loaded': alive, 'shared_with_generation': True, 'pid': pid,
                         'detail': '与生图共用 Qwen；释放会同时释放生图组件。'},
        }
        for mode, item in value.items():
            item['operation'] = self.model_operation if self.model_operation and self.model_operation['mode'] == mode else None
            item['error'] = self.model_error[mode] or item.get('error')
            if item['operation']:
                item['state'] = 'loading'
        return value

    def resources_for(self, mode, params, size=None, loading=False):
        """Conservative admission estimate, not a promise against all native OOMs."""
        snapshot = resource_snapshot(self.engine.pid)
        gpu, memory = snapshot.get('gpu') or {}, snapshot.get('memory') or {}
        pixels = (size[0] * size[1]) if size else 384 * 256
        # Outpaint treats the RAM/VRAM estimate as a warning and still allows generation.
        # Unknown readings, model integrity, battery and GPU contention stay blocking.
        advisory = mode == 'outpaint' and not loading
        if mode == 'upscale':
            tile = params.get('tile_size', 128)
            vram = (.5 if loading else 1.0 if tile <= 128 else 2.2 if tile <= 256 else 4.5) * GIB
            ram = max(3 * GIB, 2 * GIB + pixels * 24)
        else:
            settings = self.store.settings()
            alive = self.engine._engine_alive()[0]
            components = self.engine.components_status() if alive and same_context(settings, self.engine.loaded_settings) else None
            missing = 0
            for key in MODEL_KEYS:
                path = Path(settings.get(key) or '__missing__')
                if not path.is_file():
                    return {'allowed': False, 'reason': '扩图模型文件不完整，已取消加载；请先配置 Qwen 组件', 'can_cancel': True}
                resident = (components or {}).get(key) or {}
                total = resident.get('weight_bytes') or path.stat().st_size
                missing += max(0, total - (resident.get('loaded_weight_bytes') or 0))
            ram = missing * 1.1 + 3 * GIB + (0 if loading else pixels * 1536)
            vram = (.5 * GIB) if loading else max(2 * GIB, 1.2 * GIB + pixels * 2300)
        available_ram, available_vram = memory.get('available'), gpu.get('free')
        memory_reasons, other_reasons = [], []
        if available_ram is None or available_vram is None:
            other_reasons.append('无法读取内存或显存，暂不加载')
        else:
            if available_ram < ram:
                memory_reasons.append(f'系统内存可用 {available_ram/GIB:.1f} GiB，估计至少需要 {ram/GIB:.1f} GiB（含余量）')
            if available_vram < vram:
                memory_reasons.append(f'显存可用 {available_vram/GIB:.1f} GiB，估计至少需要 {vram/GIB:.1f} GiB')
        if gpu.get('utilization', 0) > 85:
            other_reasons.append('显卡繁忙，请等待当前任务结束')
        reasons = other_reasons + ([] if advisory else memory_reasons)
        return {'allowed': not reasons, 'reason': '；'.join(reasons),
                'warning': '；'.join(memory_reasons) if advisory else '',
                'can_cancel': True,
                'required_ram': int(ram), 'required_vram': int(vram),
                'available_ram': available_ram, 'available_vram': available_vram,
                'note': '按当前空闲资源保守估算，加载与执行前重新检查，不保证所有图片都能完成。'
                        + ('扩图的资源估算仅作提示，仍会尝试生成；OOM 或超时按任务失败处理。'
                           if advisory and memory_reasons else '')}

    def require_resources(self, mode, params, size=None, loading=False):
        result = self.resources_for(mode, params, size, loading)
        if not result['allowed']:
            raise ValueError('资源不足，已取消本次操作：' + result['reason'])
        return result

    def load_model(self, mode, params):
        self.engine._check_power()
        with self.engine.lock:
            if self.engine.active or not self.engine.pending.empty() or self.engine.draining or self.engine.component_operation or self.engine.state == 'loading':
                raise ValueError('已有任务或模型操作，请等待结束')
            if self.engine.stopping.is_set() or self.engine.resume_lost:
                raise ValueError('引擎停止或恢复中，暂不能加载')
            if not self.capabilities()[mode]['verified']:
                raise ValueError('当前模型尚未通过本机验证')
            self.require_resources(mode, params, loading=True)
            if mode == 'outpaint' and self.engine._engine_alive()[0] and not same_context(self.store.settings(), self.engine.loaded_settings):
                raise ValueError('Qwen 设置已变化，请先手动释放旧组合')
            self.model_cancel = threading.Event()
            self.model_error[mode] = None
            self.model_operation = {'mode': mode, 'action': 'load'}
            self.engine.component_operation = {'key': mode, 'action': 'load'}
            thread = threading.Thread(target=self._load_model_worker, args=(mode, params, self.model_cancel), daemon=True)
            thread.start()
        return {'state': 'loading', 'mode': mode}

    def _load_model_worker(self, mode, params, cancel):
        cancelled = lambda: cancel.is_set() or self.engine.stopping.is_set() or self.engine.power_blocked
        with self.engine.operation_lock:
            try:
                if cancelled():
                    raise InterruptedError('已取消加载')
                self.require_resources(mode, params, loading=True)
                if mode == 'upscale':
                    self.upscaler.call('load', params, cancelled, timeout=180)
                else:
                    with self.outpaint_memory_guard():
                        settings = self.store.settings()
                        self.engine._load(settings, cancelled=cancelled, eager_override=False)
                        for key in MODEL_KEYS:
                            if cancelled():
                                raise InterruptedError('已取消加载')
                            value = self.engine.request('POST', f'/sdcpp/v1/models/components/{key}/load', timeout=1800)
                            self.engine.component_info = value['components']
                        if cancelled():
                            raise InterruptedError('已取消加载')
                        self.engine.component_checked_at = time.monotonic()
                        self.engine.state = 'ready'
            except Exception as exc:
                self.model_error[mode] = str(exc)
                if mode == 'outpaint' and self.engine.state == 'loading':
                    self.engine.state = 'ready' if self.engine.loaded_settings and self.engine._engine_alive()[0] else 'error'
            finally:
                with self.engine.lock:
                    if not self.model_operation or self.model_operation['action'] != 'release':
                        self.model_operation = None
                        self.engine.component_operation = None

    def release_model(self, mode):
        with self.engine.lock:
            if self.engine.active or not self.engine.pending.empty() or self.engine.draining:
                raise ValueError('请先取消或等待当前任务结束，再释放模型')
            if self.engine.component_operation and not (self.model_operation and self.model_operation['mode'] == mode):
                raise ValueError('另一个模型正在操作，请等待结束')
            if self.engine.state == 'loading' and not self.model_operation:
                raise ValueError('Qwen 正在执行其他加载，请使用原加载面板取消')
            self.model_cancel.set()
            self.model_operation = {'mode': mode, 'action': 'release'}
            self.engine.component_operation = {'key': mode, 'action': 'release'}
        # Terminate before joining operation_lock so an in-flight native load can return.
        try:
            if mode == 'upscale':
                self.upscaler.stop()
            else:
                self.engine._stop_process()
            with self.engine.operation_lock:
                if mode == 'upscale':
                    self.upscaler.stop()
                else:
                    self.engine._stop_process()
                    self.engine.state, self.engine.error, self.engine.resume_lost = 'idle', None, False
                self.model_error[mode] = None
        finally:
            with self.engine.lock:
                self.model_operation = None
                self.engine.component_operation = None
        return {'state': 'idle', 'mode': mode}

    @contextmanager
    def outpaint_memory_guard(self):
        done, failed = threading.Event(), []
        def watch():
            while not done.wait(.5):
                available = memory_snapshot().get('available')
                if available is not None and available < 3 * GIB:
                    failed.append('可用内存低于 3 GiB，已停止扩图并释放 Qwen 模型')
                    self.engine._stop_process()
                    self.engine.state = 'idle'
                    return
        thread = threading.Thread(target=watch, daemon=True)
        thread.start()
        try:
            yield
        finally:
            done.set()
            thread.join(timeout=12)
            if failed:
                raise RuntimeError(failed[0])
