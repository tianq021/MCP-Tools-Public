"""Conservative estimates from matching completed jobs and observed sampling steps."""
from __future__ import annotations

from collections import deque
import hashlib
import json
import math
from pathlib import Path
from statistics import median


def timing_signature(config, settings, warm, hash_cache):
    # Exact prompt/input matching avoids treating long CPU encoding like an empty prompt.
    request = {k: v for k, v in config.items() if k not in ('seed', 'content_confirmed', 'output_bit_depth')}
    if config.get('output_bit_depth') == 16:
        # 16-bit encoding/saving costs more, so it gets its own timing bucket.
        # 8-bit jobs omit the key to keep history recorded before the field existed.
        request['output_bit_depth'] = 16
    runtime = {k: settings.get(k) for k in (
        'diffusion_model', 'text_encoder', 'vae', 'vision', 'max_vram',
        'text_encoder_cpu', 'vae_cpu', 'keep_loaded', 'eager_load', 'extra_args')}
    files = {}
    hashes_cached = True
    for key in ('diffusion_model', 'text_encoder', 'vae', 'vision'):
        if not settings.get(key):
            continue
        try:
            path = Path(settings[key])
            stat = path.stat()
            files[key] = (stat.st_size, stat.st_mtime_ns)
            hashes_cached = hashes_cached and (str(path), stat.st_size, stat.st_mtime_ns) in hash_cache
        except OSError:
            files[key] = None
            hashes_cached = False
    payload = [1, '0518b34', request, runtime, files, warm, hashes_cached]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def historical_seconds(jobs, signature):
    samples = []
    for job in sorted(jobs, key=lambda j: j.get('created', 0), reverse=True):
        if job.get('status') != 'completed' or job.get('timing_signature') != signature:
            continue
        if job.get('started') is None or job.get('finished') is None:
            continue
        duration = job['finished'] - job['started']
        if math.isfinite(duration) and duration > 0:
            samples.append(duration)
        if len(samples) == 5:
            break
    return (median(samples), len(samples)) if samples else (None, 0)


class JobTiming:
    def __init__(self, started, history=None, sample_count=0):
        self.started = started  # monotonic clock, never a wall-clock timestamp
        self.history = history
        self.sample_count = sample_count
        self.phase = 'preparing'
        self.image_index = 0
        self.step = 0
        self.total_steps = 0
        self.observed_at = None
        self.rates = deque(maxlen=8)

    def begin_image(self, index, total_steps):
        self.phase = 'encoding'
        self.image_index = index
        self.step = 0
        self.total_steps = total_steps
        self.observed_at = None
        self.rates.clear()

    def observe(self, step, total_steps, now):
        if not isinstance(step, int) or not isinstance(total_steps, int) or total_steps <= 0:
            return
        step = min(max(step, 0), total_steps)
        if step < self.step or total_steps != self.total_steps:
            self.begin_image(self.image_index, total_steps)
        if step <= self.step:
            return
        if self.observed_at is not None and now > self.observed_at:
            self.rates.append((now - self.observed_at) / (step - self.step))
        # The first observed step includes encoding and warmup; it is never a rate sample.
        self.step, self.total_steps, self.observed_at = step, total_steps, now
        self.phase = 'finishing' if step == total_steps else 'sampling'

    def snapshot(self, now):
        elapsed = max(0, now - self.started)
        remaining = self.history - elapsed if self.history is not None else None
        sampling = None
        note = {
            'preparing': '正在加载模型或计算 SHA256，等待采样数据',
            'encoding': '正在编码或等待首步，首次运行可能较慢',
            'sampling': '正在收集实际步速',
            'finishing': '采样已结束，正在解码或保存图片',
        }[self.phase]
        if self.phase == 'sampling' and self.rates:
            rate = median(self.rates)
            since_step = max(0, now - self.observed_at)
            seconds = (self.total_steps - self.step) * rate - since_step
            if seconds > 0 and since_step <= max(15, rate * 3):
                sampling = max(0.1, round(seconds, 1))
                note = '按最近实际步速估算；仅当前图片采样，不含后续解码、保存和其他图片'
            else:
                note = '步速发生变化，等待新进度重新估算'
        return {
            'elapsed_seconds': round(elapsed, 1),
            'estimated_total_seconds': round(self.history, 1) if self.history is not None else None,
            'remaining_seconds': max(0.1, round(remaining, 1)) if remaining is not None and remaining > 0 else None,
            'history_samples': self.sample_count,
            'history_exceeded': remaining is not None and remaining <= 0,
            'sampling_remaining_seconds': sampling,
            'image_index': self.image_index,
            'note': note,
        }
