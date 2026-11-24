from __future__ import annotations

import base64
import hashlib
import io
import json
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile
import time
from typing import Literal
import uuid

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image, ImageChops, ImageOps, PngImagePlugin
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .storage import ROOT, atomic_json, sha256
from .png16 import read_header
from .resources import resource_snapshot
from .image_tools_models import ImageToolModels
from .seam_blend import blend_outpaint_seams, verify_core
from .engine import extra_tokens

MODEL_ID = 'realesrgan-anime-6b'
MODEL_REL = 'models/upscalers/RealESRGAN_x4plus_anime_6B.pth'
OUTPAINT_ADAPTER = 'qwen-image-2.1-outpaint-v2'
OUTPAINT_FILE = OUTPAINT_ADAPTER + '.safetensors'
OUTPAINT_SHA256 = '3611a5ccae5f790405eeacf1dfb2c89285c1e4f2987b9abc88cd0161a60eca28'
OUTPAINT_TRIGGER = ('Outpaint the image: replace the solid gray areas with a seamless continuation '
                    'of the scene, keeping the existing picture unchanged.')
TASK_TIMEOUT_SECONDS = 1800
LIMITS = dict(
    max_input_bytes=25 * 1024**2, max_input_pixels=16_000_000,
    max_dimension=8192, max_output_pixels=67_108_864,
    # 放大不设固定输出上限，按内存/显存/磁盘预算放行；8K 只是实测参考值。
    upscale=dict(max_dimension=None, max_output_pixels=None,
                 recommended_dimension=7680, recommended_pixels=33_177_600),
    outpaint=dict(max_dimension=4096, max_output_pixels=16_000_000,
                  recommended_dimension=2048, recommended_pixels=2_220_800),
)


class UpscaleParams(BaseModel):
    model_config = ConfigDict(extra='forbid')
    model_id: Literal['realesrgan-anime-6b'] | None = MODEL_ID
    tile_size: int = Field(default=128, ge=64, le=384)
    repeats: int = Field(default=1, ge=1, le=2)
    output_format: Literal['png'] = 'png'

    @field_validator('tile_size')
    @classmethod
    def tile_alignment(cls, value):
        if value % 32:
            raise ValueError('分块大小必须为 32 的倍数')
        return value


class OutpaintParams(BaseModel):
    model_config = ConfigDict(extra='forbid')
    extend_left: int = Field(default=128, ge=0, le=4096)
    extend_right: int = Field(default=128, ge=0, le=4096)
    extend_top: int = Field(default=0, ge=0, le=4096)
    extend_bottom: int = Field(default=0, ge=0, le=4096)
    prompt: str = Field(default='', max_length=12000)
    negative_prompt: str = Field(default='', max_length=4000)
    steps: int = Field(default=25, ge=1, le=100)
    cfg: float = Field(default=1, ge=1, le=20)
    seed: int = Field(default=-1, ge=-1, le=2**53 - 16)
    sampler: Literal['euler', 'euler_a', 'heun', 'dpm++2m'] = 'euler'
    scheduler: Literal['default', 'simple', 'discrete', 'karras'] = 'simple'
    keep_original_pixels: Literal[True] = True
    seam_mode: Literal['strict', 'feather'] = 'strict'
    feather_px: Literal[16, 32, 48] = 32
    tone_match: bool = True
    output_format: Literal['png'] = 'png'


class ToolJob(BaseModel):
    model_config = ConfigDict(extra='forbid')
    task_type: Literal['upscale', 'outpaint']
    input_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    params: dict
    content_confirmed: bool = False


class PreviewRequest(BaseModel):
    input_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    params: OutpaintParams


class GalleryInput(BaseModel):
    image_id: str = Field(pattern=r'^[a-f0-9]{32}$')


def params_for(mode, value):
    return (UpscaleParams if mode == 'upscale' else OutpaintParams).model_validate(value).model_dump()


def image8(raw):
    if len(raw) > LIMITS['max_input_bytes']:
        raise ValueError('输入图片上限 25 MiB')
    if raw.startswith(b'\x89PNG\r\n\x1a\n') and read_header(raw[:33])['bit_depth'] > 8:
        raise ValueError('新工作流暂不支持 16 bit 输入，请显式导出 8 bit 副本后再使用')
    try:
        with Image.open(io.BytesIO(raw)) as im:
            if im.format not in ('PNG', 'JPEG', 'WEBP') or getattr(im, 'n_frames', 1) != 1:
                raise ValueError('仅支持单帧 PNG、JPEG、WebP 图片')
            if im.width * im.height > LIMITS['max_input_pixels']:
                raise ValueError('输入图片像素数超限')
            im.load()
            if im.mode not in ('RGB', 'RGBA', 'P', 'L', 'LA', 'CMYK', '1'):
                raise ValueError('不支持此图片色彩模式')
            return ImageOps.exif_transpose(im).convert('RGBA' if 'A' in im.getbands() or 'transparency' in im.info else 'RGB')
    except (OSError, Image.DecompressionBombError) as error:
        raise ValueError('输入不是有效图片') from error


def geometry(size, params):
    width, height = size
    left, right, top, bottom = (params['extend_' + k] for k in ('left', 'right', 'top', 'bottom'))
    if any(type(v) is not int for v in (width, height, left, right, top, bottom)) or min(width,height) < 1 or min(left,right,top,bottom) < 0:
        raise ValueError('画布尺寸与扩展量必须为有效整数')
    if not any((left, right, top, bottom)):
        raise ValueError('至少扩展一个方向')
    requested = (width + left + right, height + top + bottom)
    final = tuple((v + 31) // 32 * 32 for v in requested)
    check_size(final, 'outpaint')
    return dict(width=final[0], height=final[1], offset_x=left, offset_y=top,
                original_width=width, original_height=height, requested_width=requested[0],
                requested_height=requested[1], alignment=32, adjusted=final != requested,
                requested_margins=dict(left=left, right=right, top=top, bottom=bottom),
                actual_margins=dict(left=left, right=final[0]-width-left, top=top, bottom=final[1]-height-top))


def check_size(size, mode='upscale'):
    limits = LIMITS.get(mode) or LIMITS
    if min(size) < 1:
        raise ValueError('目标尺寸无效')
    max_dimension, max_pixels = limits.get('max_dimension'), limits.get('max_output_pixels')
    if max_dimension and max(size) > max_dimension:
        raise ValueError('目标尺寸超出当前工作流限制')
    if max_pixels and size[0] * size[1] > max_pixels:
        raise ValueError('目标像素数超出当前工作流限制')


# alpha 低于该值才算“肉眼可见的透明”。Qwen 输出常见 245–254 的 alpha 噪声，
# 255 与 254 的差别不可见，不能作为拒绝整张图的依据。
ALPHA_VISIBLE_LEVEL = 250
# 明显透明像素占比低于该阈值时按不透明处理（丢弃 alpha，RGB 不变）。
ALPHA_OPAQUE_RATIO = 0.01
ALPHA_BACKGROUND = '#ffffff'


def alpha_profile(image):
    if image.mode != 'RGBA':
        return None
    alpha = image.getchannel('A')
    histogram = alpha.histogram()
    total = image.width * image.height
    visible = total - sum(histogram[ALPHA_VISIBLE_LEVEL:])
    ratio = visible / total
    return dict(min=alpha.getextrema()[0], visible_pixels=visible, visible_ratio=ratio,
                policy='drop-alpha' if ratio < ALPHA_OPAQUE_RATIO else 'composite-white')


def flatten_alpha(image):
    profile = alpha_profile(image)
    if not profile:
        return image, None
    if profile['policy'] == 'drop-alpha':
        return image.convert('RGB'), profile | dict(background=None)
    board = Image.new('RGBA', image.size, (255, 255, 255, 255))
    return Image.alpha_composite(board, image).convert('RGB'), profile | dict(background=ALPHA_BACKGROUND)


def compose_preserved(original, candidate, canvas):
    expected = (canvas['width'], canvas['height'])
    if candidate.size != expected:
        raise ValueError('引擎输出尺寸与目标画布不一致')
    result = candidate.convert(original.mode)
    result.paste(original, (canvas['offset_x'], canvas['offset_y']))
    verify_preserved(original, result, canvas)
    return result


def verify_preserved(original, result, canvas):
    x, y = canvas['offset_x'], canvas['offset_y']
    crop = result.crop((x, y, x + original.width, y + original.height)).convert(original.mode)
    # Compare all channels explicitly: RGBA getbbox can otherwise ignore RGB-only differences.
    difference = ImageChops.difference(original, crop)
    bands = difference.split()
    mask = bands[0]
    for band in bands[1:]:
        mask = ImageChops.lighter(mask, band)
    changed = original.width * original.height - mask.histogram()[0]
    if changed:
        raise ValueError(f'原图像素保护校验失败：{changed} 个像素不同')
    return {'verified': True, 'different_pixels': 0}


def fingerprint(settings):
    value = {}
    for key in ('diffusion_model', 'text_encoder', 'vae', 'vision'):
        path = Path(settings.get(key) or '__missing__')
        try:
            stat = path.stat()
            value[key] = [str(path.resolve()), stat.st_size, stat.st_mtime_ns]
        except OSError:
            value[key] = None
    for key in ('max_vram', 'text_encoder_cpu', 'vae_cpu', 'extra_args'):
        value[key] = settings.get(key)
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class ImageTools(ImageToolModels):
    def __init__(self, store, engine):
        self.store, self.engine = store, engine
        self.data = store.data / 'image-tools'
        self.data.mkdir(exist_ok=True)
        self.configs = store.root / 'configs'
        self.configs.mkdir(exist_ok=True)
        self.hashes = {}
        engine.image_tools = self
        self.init_models()

    def digest(self, path):
        stat = path.stat()
        key = (str(path), stat.st_size, stat.st_mtime_ns)
        if key not in self.hashes:
            self.hashes[key] = sha256(path)
        return self.hashes[key]

    def evidence(self):
        try:
            value = json.loads((self.configs / 'image-tools-verified.json').read_text(encoding='utf-8'))
            return {k: v for k, v in value.items() if isinstance(v, dict)} if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def outpaint_weight(self, settings):
        args = extra_tokens(settings.get('extra_args'))
        for index in range(len(args) - 1):
            if args[index] == '--lora-model-dir':
                return Path(args[index + 1]) / OUTPAINT_FILE
        return Path(settings['diffusion_model']).parent.parent / 'loras' / OUTPAINT_FILE

    def capabilities(self):
        evidence = self.evidence()
        model, cli, server = ROOT / MODEL_REL, ROOT / 'runtime/stable-diffusion.cpp/sd-cli.exe', ROOT / 'runtime/stable-diffusion.cpp/sd-server.exe'
        upscale, outpaint = evidence.get('upscale', {}), evidence.get('outpaint', {})
        up_ok = bool(model.is_file() and cli.is_file() and upscale.get('verified') is True
                     and upscale.get('scale') == 4 and upscale.get('model_sha256') == self.digest(model)
                     and upscale.get('cli_sha256') == self.digest(cli)
                     and upscale.get('resident_verified') is True
                     and upscale.get('library_sha256') == self.digest(ROOT / 'runtime/stable-diffusion.cpp/stable-diffusion.dll'))
        weight = self.outpaint_weight(self.store.settings())
        out_ok = bool(weight.is_file() and outpaint.get('adapter_sha256') == OUTPAINT_SHA256
                      and self.digest(weight) == OUTPAINT_SHA256 and server.is_file() and outpaint.get('verified') is True
                      and outpaint.get('settings_fingerprint') == fingerprint(self.store.settings())
                      and outpaint.get('server_sha256') == self.digest(server))
        blocked = self.engine.power_blocked or self.engine.power_stopping or self.engine.stopping.is_set() or self.engine.draining or self.engine.resume_lost
        busy = self.engine.state == 'loading' or self.engine.component_operation
        reason = '电源保护、引擎恢复或模型操作期间不可提交' if blocked or busy else ''
        return {
            'upscale': dict(verified=up_ok, available=bool(up_ok and not blocked and not busy),
                            reason=reason or ('' if up_ok else '待本机常驻放大验证'),
                            models=[dict(id=MODEL_ID, name='RealESRGAN anime 6B', scale=4, verified=True)] if up_ok else []),
            'outpaint': dict(adapter=OUTPAINT_ADAPTER, verified=out_ok, available=bool(out_ok and not blocked and not busy),
                             reason=reason or ('' if out_ok else '当前模型组合尚未通过扩图实测'), alignment=32, strict_preservation=True),
            'enhance': dict(verified=False, available=False, models=[],
                            reason='VOSR 2.0 / SeedVR2 7B Sharp Q4_K_M 尚未完成本机加载、推理和质量验证；基础放大仍可独立使用'),
            'limits': LIMITS, 'output_bit_depths': [8]}

    def defaults(self, mode, value=None):
        path = self.configs / f'{mode}.default.json'
        if value is not None:
            result = params_for(mode, value)
            atomic_json(path, result)
            return result
        if path.exists():
            return params_for(mode, json.loads(path.read_text(encoding='utf-8')))
        return params_for(mode, {})

    def record_path(self, identity):
        if not re.fullmatch('[a-f0-9]{32}', identity):
            raise ValueError('无效输入 ID')
        return self.data / f'{identity}.json'

    def input_path(self, identity):
        path = self.record_path(identity)
        if not path.is_file():
            raise KeyError('输入图不存在')
        record = json.loads(path.read_text(encoding='utf-8'))
        if record.get('source_image_id'):
            source_id = record['source_image_id']
            if source_id in self.store.private_ids():
                raise ValueError('私密图片不能作为工作流输入')
            item = self.store.image(source_id)
            if item['deleted']:
                raise ValueError('输入图片已移入回收站')
            source = self.store.file(source_id)
        else:
            source = self.data / f'{identity}.png'
        source = source.resolve()
        allowed = [self.data.resolve(), (self.store.root / 'outputs').resolve(), (self.store.root / '图片').resolve()]
        if not any(source.is_relative_to(base) for base in allowed):
            raise ValueError('图片不在允许的目录内')
        if not source.is_file() or source.stat().st_size > LIMITS['max_input_bytes']:
            raise ValueError('输入图片不可用或超过大小限制')
        if self.digest(source) != record['sha256']:
            raise ValueError('输入图片已变化，请重新选择')
        return source, record

    def describe(self, record):
        identity = record['id']
        return {k: record[k] for k in ('id', 'name', 'width', 'height', 'size', 'bit_depth')} | {
            'has_transparency': record.get('has_transparency', False),
            'alpha': record.get('alpha'),
            'preview_url': f'/api/image-tools/inputs/{identity}/preview',
            'download_url': f'/api/image-tools/inputs/{identity}/download'}

    def add_input(self, raw=None, name='', image_id=None):
        identity = uuid.uuid4().hex
        if image_id:
            if image_id in self.store.private_ids():
                raise ValueError('私密图片不能作为工作流输入')
            item = self.store.image(image_id)
            if item['deleted']:
                raise ValueError('图片已移入回收站')
            source = self.store.file(image_id).resolve()
            if not any(source.is_relative_to((self.store.root / d).resolve()) for d in ('outputs', '图片')):
                raise ValueError('图片不在允许的目录内')
            if source.stat().st_size > LIMITS['max_input_bytes']:
                raise ValueError('输入图片上限 25 MiB')
            raw, name = source.read_bytes(), item['name']
        image = image8(raw)
        if not image_id:
            # Preserve the uploaded bytes for an honest original download; suffix is not used to infer format.
            (self.data / f'{identity}.png').write_bytes(raw)
        record = dict(id=identity, name=Path(name).name[:200], width=image.width, height=image.height,
                      size=len(raw), bit_depth=8, sha256=hashlib.sha256(raw).hexdigest(), source_image_id=image_id,
                      has_transparency=image.mode == 'RGBA' and image.getchannel('A').getextrema()[0] < 255,
                      alpha=alpha_profile(image))
        atomic_json(self.record_path(identity), record)
        return self.describe(record)

    def preview(self, identity, params):
        _, record = self.input_path(identity)
        return geometry((record['width'], record['height']), params)

    def submit(self, request):
        mode = request.task_type
        params = params_for(mode, request.params)
        if not request.content_confirmed:
            raise ValueError('请确认遵守内容约定')
        cap = self.capabilities()[mode]
        if not cap['available']:
            raise ValueError(cap['reason'])
        _, record = self.input_path(request.input_id)
        if mode == 'upscale':
            if params['model_id'] != MODEL_ID:
                raise ValueError('请选择已验证的放大模型')
            check_size((record['width'] * 4**params['repeats'], record['height'] * 4**params['repeats']), 'upscale')
        else:
            canvas = self.preview(request.input_id, params)
            if min(canvas['width'], canvas['height']) < 256:
                raise ValueError('扩图目标宽高至少为 256 像素，请增加扩展量')
            if not params['prompt'].strip():
                raise ValueError('扩图提示词不能为空')
            if re.search(r'肢解|血腥内脏|伪造证件|冒充真人|\b(dismemberment)\b', params['prompt'], re.I):
                raise ValueError('提示词触发已约定的内容限制，请修改')
            if params['seed'] == -1:
                params['seed'] = secrets.randbelow(2**32)
        size = ((record['width'] * 4**params['repeats'], record['height'] * 4**params['repeats'])
                if mode == 'upscale' else (canvas['width'], canvas['height']))
        self.require_resources(mode, params, size)
        return self.engine.submit_image_tool(mode, request.input_id, self.describe(record), params)

    def cancelled(self, job):
        return job['_cancel'] or self.engine.stopping.is_set() or self.engine.power_blocked

    def run(self, job):
        try:
            self._execute(job)
        except Exception:
            directory = self.store.root / 'outputs' / job['task_type'] / job['id']
            if not job.get('image_ids'):
                for name in ('output.pending.png', 'output.png', 'output.json', 'strict.png', 'comparison.png'):
                    (directory / name).unlink(missing_ok=True)
            raise

    def _execute(self, job):
        mode, params = job['task_type'], job['params']
        if self.cancelled(job):
            raise InterruptedError('已取消')
        cap = self.capabilities()[mode]
        if not cap['available']:
            raise ValueError(cap['reason'])
        source, record = self.input_path(job['input']['id'])
        raw = source.read_bytes()
        if hashlib.sha256(raw).hexdigest() != record['sha256']:
            raise ValueError('输入图在读取时发生变化')
        original, alpha = flatten_alpha(image8(raw))
        directory = self.store.root / 'outputs' / mode / job['id']
        directory.mkdir(parents=True, exist_ok=False)
        expected = ((original.width * 4**params['repeats'], original.height * 4**params['repeats'])
                    if mode == 'upscale' else None)
        canvas = geometry(original.size, params) if mode == 'outpaint' else None
        if canvas:
            expected = canvas['width'], canvas['height']
        check_size(expected, mode)
        if shutil.disk_usage(directory).free < max(256 * 1024**2, expected[0] * expected[1] * 24):
            raise ValueError('磁盘空间不足，无法安全保存任务')
        scratch_root = self.store.root / '.tmp'
        scratch_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='image-tool-', dir=scratch_root) as temp:
            scratch = Path(temp)
            seam_info, protection_mask = None, None
            original.save(scratch / 'input.png')
            if mode == 'upscale':
                candidate, execution = self.run_upscale(job, scratch, directory)
            else:
                candidate, execution = self.run_outpaint(job, original, canvas)
                # Reject malformed native results before any comparison artifact is published.
                if candidate.size != expected or candidate.mode not in ('RGB', 'RGBA'):
                    raise ValueError('引擎输出尺寸或通道与预期不符')
                candidate, _ = flatten_alpha(candidate)
                strict = compose_preserved(original, candidate, canvas)
                candidate, seam_info, protection_mask = blend_outpaint_seams(original, candidate, canvas, params)
                if params.get('seam_mode') == 'feather':
                    strict.save(directory / 'strict.png')
                    # Identical crop and scale for a strict / blended comparison; no extra inference.
                    a, b = strict.copy(), candidate.copy()
                    a.thumbnail((768,768)); b.thumbnail((768,768))
                    comparison = Image.new('RGB',(a.width+b.width,a.height))
                    comparison.paste(a,(0,0)); comparison.paste(b,(a.width,0))
                    comparison.save(directory / 'comparison.png')
            if candidate.size != expected:
                raise ValueError('引擎输出尺寸与预期不符')
            if self.cancelled(job):
                raise InterruptedError('已取消')
            config = dict(schema_version='1.0', task_type=mode, run_id=job['id'], job_id=job['id'],
                          created=job['created'], started=job['started'], finished=time.time(),
                          params=params, input=record, alpha=alpha, execution=execution, canvas=canvas,
                          output_size=list(candidate.size), output_bit_depth=8,
                          status='completed', preservation=None, seam=seam_info)
            pending = directory / 'output.pending.png'
            candidate.save(pending, format='PNG')
            with Image.open(pending) as saved:
                saved.load()
                if saved.size != expected:
                    raise ValueError('保存后的输出尺寸不符')
                if canvas:
                    config['preservation'] = (verify_core(original, saved, canvas, protection_mask)
                                              if seam_info['effective_mode'] == 'feather' else verify_preserved(original, saved, canvas))
            if self.cancelled(job):
                raise InterruptedError('已取消')
            metadata = PngImagePlugin.PngInfo()
            metadata.add_text('qwen_studio', json.dumps(config, ensure_ascii=False))
            candidate.save(pending, format='PNG', pnginfo=metadata)
            with Image.open(pending) as saved:
                saved.load()
                if canvas:
                    if seam_info['effective_mode'] == 'feather':
                        verify_core(original, saved, canvas, protection_mask)
                    else:
                        verify_preserved(original, saved, canvas)
            with self.engine.lock:
                if self.cancelled(job):
                    raise InterruptedError('已取消')
                final = directory / 'output.png'
                pending.replace(final)
                config['image_sha256'] = sha256(final)
                atomic_json(final.with_suffix('.json'), config)
                identity = self.store.register(final)
                job['image_ids'] = [identity]
                job['completed_images'] = 1
                job['result'] = dict(image_id=identity, width=candidate.width, height=candidate.height,
                                     preview_url=f'/api/images/{identity}/file', download_url=f'/api/images/{identity}/file?download=true',
                                     manifest_url=f'/api/image-tools/jobs/{job["id"]}/manifest', preservation=config['preservation'], seam=seam_info)
                if canvas and params.get('seam_mode') == 'feather':
                    job['result']['strict_url'] = f'/api/image-tools/jobs/{job["id"]}/artifacts/strict'
                    job['result']['comparison_url'] = f'/api/image-tools/jobs/{job["id"]}/artifacts/comparison'
                job.update(status='completed', stage='处理完成', progress=1)

    def run_upscale(self, job, scratch, directory):
        params = job['params']
        with Image.open(scratch / 'input.png') as source:
            size = (source.width * 4**params['repeats'], source.height * 4**params['repeats'])
        self.require_resources('upscale', params, size)
        job['stage'] = '加载 / 复用放大模型'
        start = time.monotonic()
        result = self.upscaler.call('run', params | {'input': str(scratch / 'input.png'),
                                                    'output': str(scratch / 'output.png')},
                                    lambda: self.cancelled(job), timeout=TASK_TIMEOUT_SECONDS)
        with Image.open(scratch / 'output.png') as output:
            output.load()
            candidate = output.copy()
        return candidate, dict(method='resident-upscaler-c-api', model_sha256=self.digest(ROOT / MODEL_REL),
                               library_sha256=self.digest(ROOT / 'runtime/stable-diffusion.cpp/stable-diffusion.dll'),
                               scale=4, seconds=time.monotonic()-start, worker_pid=self.upscaler.status()['pid'])

    def run_outpaint(self, job, original, canvas):
        self.require_resources('outpaint', job['params'], (canvas['width'], canvas['height']))
        with self.outpaint_memory_guard():
            return self._run_outpaint(job, original, canvas)

    def _run_outpaint(self, job, original, canvas):
        settings, params = job['_settings'], job['params']
        self.engine._load(settings, cancelled=lambda: self.cancelled(job))
        board = Image.new('RGB', (canvas['width'], canvas['height']), '#808080')
        board.paste(original, (canvas['offset_x'], canvas['offset_y']))
        stream = io.BytesIO()
        board.save(stream, format='PNG')
        weight = self.outpaint_weight(settings)
        if not weight.is_file() or self.digest(weight) != OUTPAINT_SHA256:
            raise ValueError('扩图 LoRA v2 权重缺失或 SHA-256 校验失败')
        capabilities = self.engine.request('GET', '/sdcpp/v1/capabilities')
        matches = [row for row in capabilities.get('loras', [])
                   if Path(row['path']).name in (OUTPAINT_FILE, OUTPAINT_ADAPTER)
                   or row.get('name') == OUTPAINT_ADAPTER]
        if len(matches) != 1:
            raise ValueError('推理引擎未识别唯一的扩图 LoRA v2，请检查 LoRA 目录')
        adapter = dict(path=matches[0]['path'], multiplier=1.0)
        prompt = OUTPAINT_TRIGGER + ' Scene: ' + params['prompt'].strip()
        body = dict(prompt=prompt, lora=[adapter], negative_prompt=params['negative_prompt'], width=canvas['width'], height=canvas['height'],
                    seed=params['seed'], batch_count=1, ref_images=[base64.b64encode(stream.getvalue()).decode()],
                    sample_params=dict(sample_method=params['sampler'], sample_steps=params['steps'], guidance={'txt_cfg': params['cfg']}),
                    vae_tiling_params={'enabled': board.width * board.height > 1024 * 1024}, output_format='png', output_bit_depth=8, embed_image_metadata=False)
        if params['scheduler'] != 'default':
            body['sample_params']['scheduler'] = params['scheduler']
        if self.cancelled(job):
            raise InterruptedError('已取消扩图')
        job['stage'] = 'Qwen LoRA v2 扩图中'
        native = self.engine.request('POST', '/sdcpp/v1/img_gen', json=body)
        job['native_id'] = native['id']
        job['native_pending'] = True
        self.store.save_job(job)
        start = time.monotonic()
        while True:
            if self.cancelled(job):
                self.engine._drain_job(job)
                raise InterruptedError('已取消扩图')
            try:
                result = self.engine.request('GET', f'/sdcpp/v1/jobs/{native["id"]}', timeout=10)
            except Exception:
                self.engine._drain_job(job)
                raise
            if result['status'] in ('completed', 'failed', 'cancelled'):
                job['native_pending'] = False
                break
            if time.monotonic() - start > TASK_TIMEOUT_SECONDS:
                self.engine._drain_job(job)
                raise TimeoutError('扩图超过 30 分钟，等待引擎结束后停止')
            time.sleep(.7)
        if result['status'] != 'completed':
            raise RuntimeError(str(result.get('error') or '扩图未完成'))
        images = (result.get('result') or {}).get('images', [])
        if len(images) != 1:
            raise RuntimeError('扩图引擎未返回唯一图片')
        item = images[0]
        raw = base64.b64decode(item['b64_json'], validate=True)
        with Image.open(io.BytesIO(raw)) as output:
            output.load()
            candidate = output.copy()
        execution = dict(native_request=body | {'ref_images': ['expanded canvas from input SHA256']},
                         server_sha256=self.digest(ROOT / 'runtime/stable-diffusion.cpp/sd-server.exe'),
                         settings_fingerprint=fingerprint(settings), seconds=time.monotonic()-start,
                         models={key: {k:v for k,v in item.items() if k != 'path'} | {'name':Path(item['path']).name}
                                 for key,item in self.engine.model_hashes(settings).items()},
                         adapter=dict(id=OUTPAINT_ADAPTER, sha256=OUTPAINT_SHA256, **adapter),
                         method='outpaint-lora-v2-reference-edit')
        return candidate, execution


def image_tool_routes(store, engine):
    tools = ImageTools(store, engine)
    router = APIRouter(prefix='/api/image-tools')

    @router.get('/capabilities')
    def capabilities():
        return tools.capabilities()

    @router.get('/models')
    def models():
        return tools.model_status()

    @router.post('/models/{mode}/{action}')
    def model_action(mode: Literal['upscale', 'outpaint'], action: Literal['load', 'release'], value: dict = {}):
        if action == 'release':
            return tools.release_model(mode)
        return tools.load_model(mode, params_for(mode, value.get('params', {})))

    @router.post('/resources/{mode}')
    def resources(mode: Literal['upscale', 'outpaint'], value: dict):
        params = params_for(mode, value.get('params', {}))
        size = None
        if value.get('input_id'):
            _, record = tools.input_path(value['input_id'])
            if mode == 'upscale':
                size = (record['width'] * 4**params['repeats'], record['height'] * 4**params['repeats'])
                check_size(size, mode)
            else:
                canvas = tools.preview(value['input_id'], params)
                size = canvas['width'], canvas['height']
        return tools.resources_for(mode, params, size, loading=value.get('loading') is True)

    @router.get('/defaults/{mode}')
    def defaults(mode: Literal['upscale', 'outpaint']):
        return tools.defaults(mode)

    @router.put('/defaults/{mode}')
    def save_defaults(mode: Literal['upscale', 'outpaint'], value: dict):
        return tools.defaults(mode, value)

    @router.post('/inputs')
    async def upload(file: UploadFile):
        raw = await file.read(LIMITS['max_input_bytes'] + 1)
        if len(raw) > LIMITS['max_input_bytes']:
            raise HTTPException(413, '输入图片上限 25 MiB')
        return tools.add_input(raw, file.filename or 'input.png')

    @router.post('/inputs/from-image')
    def from_image(value: GalleryInput):
        return tools.add_input(image_id=value.image_id)

    @router.get('/inputs/{identity}/{action}')
    def input_file(identity: str, action: Literal['preview', 'download']):
        path, record = tools.input_path(identity)
        return FileResponse(path, filename=record['name'] if action == 'download' else None)

    @router.post('/outpaint/preview')
    def preview(value: PreviewRequest):
        return tools.preview(value.input_id, value.params.model_dump())

    @router.post('/jobs', status_code=202)
    def submit(value: ToolJob):
        return tools.submit(value)

    @router.get('/jobs')
    def jobs():
        return engine.list_jobs(('upscale', 'outpaint'))

    def visible_job(identity):
        job = next((job for job in jobs() if job['id'] == identity), None)
        if job is None:
            raise KeyError('任务不存在')
        return job

    @router.post('/jobs/{identity}/cancel')
    def cancel(identity: str):
        visible_job(identity)
        job = engine.cancel(identity)
        return {'cancellation_requested': bool(job.get('cancellation_requested')), 'job': job}

    @router.get('/jobs/{identity}/manifest')
    def manifest(identity: str):
        job = visible_job(identity)
        if job['status'] != 'completed' or not job.get('result'):
            raise ValueError('任务尚未成功完成')
        path = store.file(job['result']['image_id']).with_suffix('.json')
        if not path.is_file():
            raise KeyError('运行记录不存在')
        return FileResponse(path, media_type='application/json', filename=f'{identity}.run.json')

    @router.get('/jobs/{identity}/artifacts/{kind}')
    def artifact(identity: str, kind: Literal['strict', 'comparison']):
        job = visible_job(identity)
        if job['status'] != 'completed' or not job.get('result'):
            raise ValueError('任务尚未成功完成')
        # store.file enforces current gallery visibility, including private/deleted records.
        final = store.file(job['result']['image_id'])
        path = final.parent / (kind + '.png')
        if not path.is_file():
            raise KeyError('此任务没有该对照图')
        return FileResponse(path, media_type='image/png', filename=f'{identity}-{kind}.png')

    return router, tools
