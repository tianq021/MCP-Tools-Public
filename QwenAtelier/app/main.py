from __future__ import annotations

from contextlib import asynccontextmanager
import io
import json
import logging
from pathlib import Path
import re
import secrets
import time
from typing import Literal
import uuid

from cryptography.exceptions import InvalidTag
from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .engine import Engine, extra_tokens, model_status
from .storage import ROOT, Store
from .png16 import read_header
from .resources import resource_snapshot
from .assistant import assistant_routes, normalize_url
from .vault import Vault
from .image_tools import image_tool_routes
from .wan_video import video_routes


class Generation(BaseModel):
    model_config = ConfigDict(extra='ignore')
    prompt: str = Field(default='', max_length=12000)
    negative_prompt: str = Field(default='', max_length=4000)
    width: int = Field(default=1024, ge=256, le=4096)
    height: int = Field(default=1024, ge=256, le=4096)
    steps: int = Field(default=30, ge=1, le=100)
    cfg: float = Field(default=1, ge=1, le=20)
    seed: int = Field(default=-1, ge=-1, le=2**53 - 16)
    batch_count: int = Field(default=1, ge=1, le=8)
    sampler: Literal['euler', 'euler_a', 'heun', 'dpm++2m'] = 'euler'
    scheduler: Literal['default', 'simple', 'discrete', 'karras'] = 'default'
    vae_tiling: bool = True
    output_bit_depth: Literal[8, 16] = 8
    input_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')
    content_confirmed: bool = False

    @field_validator('width', 'height')
    @classmethod
    def align_to_eight(cls, value):
        # 任意尺寸都接受，但向下对齐到 8 的倍数：Qwen Image 的 VAE 以 8 倍下采样，
        # 非 8 倍数会产生半格错位。界面已提示，这里兜住直接调接口的情况。
        return max(256, value - value % 8)


class Settings(BaseModel):
    diffusion_model: str = Field(max_length=1000)
    text_encoder: str = Field(max_length=1000)
    vae: str = Field(max_length=1000)
    vision: str = Field(default='', max_length=1000)
    max_vram: float = Field(default=6.0, ge=1, le=8)
    text_encoder_cpu: bool = True
    vae_cpu: bool = False
    keep_loaded: bool = True
    eager_load: bool = False
    battery_protection: bool = True
    output_directory: str = Field(default='outputs', min_length=1, max_length=200)
    port: int = Field(default=17860, ge=1024, le=65535)
    assistant_url: str = Field(default='https://api.deepseek.com', max_length=1000)
    assistant_model: str = Field(default='deepseek-flash', max_length=100)
    extra_args: str = Field(default='', max_length=2000)

    @field_validator('extra_args')
    @classmethod
    def startup_flags(cls, value):
        lines = [line.strip() for line in value.splitlines() if line.strip()]
        for line in lines:
            if not line.startswith('--'):
                raise ValueError('自定义参数每行需以 -- 开头')
        tokens = extra_tokens(value)
        if len(tokens) > 24:
            raise ValueError('自定义参数过多，最多 24 个')
        if any(len(token) > 160 for token in tokens):
            raise ValueError('单个参数过长')
        return value.strip()

    @field_validator('output_directory')
    @classmethod
    def output_path(cls, value):
        # Limit outputs to a dedicated project subtree, never source/runtime/private folders.
        parts = Path(value).parts
        if not parts or parts[0] != 'outputs' or '..' in parts:
            raise ValueError('输出目录必须为 outputs 或其子目录')
        return value

    @field_validator('assistant_url')
    @classmethod
    def assistant_endpoint(cls, value):
        try:
            return normalize_url(value)
        except HTTPException as error:
            raise ValueError(error.detail)


class Tag(BaseModel):
    tag: Literal['none', 'favorite', 'like', 'dislike', 'blocked']


class Preset(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    config: Generation


class Password(BaseModel):
    password: str = Field(min_length=10, max_length=256)


class NewVaultPassword(Password):
    password: str = Field(min_length=12, max_length=256)
    confirm_password: str = Field(min_length=12, max_length=256)
    legacy_password: str | None = Field(default=None, max_length=256)
    keep_unreadable: bool = False


class VaultAction(BaseModel):
    # Omitted: the saved vault password is used, so the user is not asked again.
    password: str | None = Field(default=None, min_length=12, max_length=256)
    confirm_password: str | None = Field(default=None, max_length=256)


class VaultRekey(VaultAction):
    password: str = Field(min_length=12, max_length=256)
    confirm_password: str = Field(min_length=12, max_length=256)
    current_password: str | None = Field(default=None, max_length=256)


MODEL_KEYS = ('diffusion_model', 'text_encoder', 'vae', 'vision', 'max_vram',
              'text_encoder_cpu', 'vae_cpu', 'keep_loaded', 'eager_load', 'extra_args')


class ProfileIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    settings: Settings


def create_app(root=ROOT):
    store = Store(root)
    engine = Engine(store)
    secure_vault = Vault(store)
    token = secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(app):
        yield
        engine.close()

    app = FastAPI(title='Qwen Atelier', lifespan=lifespan)
    app.state.store, app.state.engine = store, engine
    router, assistant = assistant_routes(store, engine)
    app.state.assistant = assistant
    app.include_router(router)
    tool_router, image_tools = image_tool_routes(store, engine)
    app.state.image_tools = image_tools
    app.include_router(tool_router)
    app.include_router(video_routes(store, engine))
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', 'testserver'])

    @app.middleware('http')
    async def local_only(request: Request, call_next):
        origin = request.headers.get('origin')
        if request.headers.get('sec-fetch-site') == 'cross-site' or (origin and origin != f'{request.url.scheme}://{request.headers.get("host")}'):
            return JSONResponse({'detail': '仅允许同源本地访问'}, status_code=403)
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and request.headers.get('x-studio-token') != token:
            return JSONResponse({'detail': '会话已过期，请刷新页面'}, status_code=403)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'"
        return response

    @app.exception_handler(ValueError)
    async def bad_value(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=400)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        # Pydantic's default error body includes the rejected input (including passwords).
        return JSONResponse({'detail': [{k: e[k] for k in ('loc', 'msg', 'type')} for e in exc.errors()]}, status_code=422)

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=404)

    @app.exception_handler(RuntimeError)
    async def engine_error(request, exc):
        logging.getLogger(__name__).exception('Runtime operation failed')
        return JSONResponse({'detail': '推理操作失败，请检查引擎状态后重试；详细原因已记录到服务日志。'}, status_code=503)

    @app.get('/api/session')
    def session():
        return {'token': token, 'app': 'Qwen Atelier', 'version': '0.1.0'}

    @app.get('/api/status')
    def status():
        engine_status = engine.status()
        pid = engine_status['pid']
        components = engine.components_status()
        models = model_status(store.settings(), engine.loaded_settings, components, engine.component_support())
        return {**resource_snapshot(pid), 'models': models,
                'engine': engine_status, 'png16': engine.png16_status(), 'timestamp': time.time()}

    @app.get('/api/settings')
    def get_settings():
        return store.settings()

    @app.put('/api/settings')
    def put_settings(value: Settings):
        if engine.active or engine.state == 'loading' or engine.component_operation or not engine.pending.empty():
            raise ValueError('生成或加载期间不能修改模型设置')
        store.within(store.root / value.output_directory)
        if engine.state == 'ready':
            engine.unload()
        store.save_settings(value.model_dump())
        return {'saved': True}

    @app.post('/api/models/load')
    def load_models(value: Settings | None = None):
        # Optional body: apply a component combination first, then build the context.
        # This endpoint loads the whole combination; component operations use their own endpoint.
        if value is not None:
            if engine.active or engine.state == 'loading' or engine.component_operation or not engine.pending.empty():
                raise ValueError('生成或加载期间不能修改模型设置')
            store.within(store.root / value.output_directory)
            # Validate before persisting, so a bad combination never replaces a working one.
            blockers = model_status(value.model_dump())['blockers']
            if blockers:
                raise ValueError('；'.join(blockers))
            store.save_settings(value.model_dump())
        return engine.preload(store.settings())

    @app.post('/api/models/unload')
    def unload_models():
        engine.unload()
        return {'state': 'idle', 'last_release': engine.last_release}

    @app.post('/api/models/components/{key}/{action}')
    def component_action(key: Literal['diffusion_model', 'text_encoder', 'vae', 'vision'],
                         action: Literal['load', 'release']):
        engine.operate_component(key, action)
        return {'state': 'loading', 'component': key, 'action': action}

    @app.post('/api/gallery/scan')
    def scan():
        return store.scan()

    @app.get('/api/images')
    def images(tag: str = 'all', q: str = ''):
        return store.images(tag, q)

    @app.get('/api/images/{identity}')
    def image_detail(identity: str):
        return store.image(identity)

    @app.get('/api/images/{identity}/file')
    def image_file(identity: str, thumb: bool = False, download: bool = False):
        path = store.file(identity, thumb)
        if not path.is_file():
            raise HTTPException(404, '图片文件已移走，请刷新图库')
        return FileResponse(path, filename=path.name if download else None)

    @app.get('/api/images/{identity}/preview')
    def image_preview(identity: str):
        return FileResponse(store.ensure_preview(identity), media_type='image/png')

    @app.get('/api/images/{identity}/export')
    def image_export(identity: str, depth: int = 8):
        # Export reuses the stored image only; it never asks the engine to resample.
        if depth not in (8, 16):
            raise ValueError('导出位深只能是 8 或 16')
        master = store.file(identity)
        if not master.is_file():
            raise HTTPException(404, '图片文件已移走，请刷新图库')
        with master.open('rb') as stream:
            header = read_header(stream.read(33))
        if depth == 16:
            if header['bit_depth'] != 16:
                raise HTTPException(409, detail='这张图落盘时只有 8 bit/通道，颜色信息已经丢失，无法恢复为真正的 16 位；本阶段不提供容器转换。')
            return FileResponse(master, media_type='image/png', filename=master.name,
                                headers={'X-Image-Bit-Depth': '16', 'X-Image-Source': 'master'})
        if header['bit_depth'] == 16:
            preview = store.ensure_preview(identity)
            return FileResponse(preview, media_type='image/png', filename=f'{master.stem}_8bit.png',
                                headers={'X-Image-Bit-Depth': '8', 'X-Image-Source': 'derived-8bit-preview'})
        return FileResponse(master, media_type='image/png', filename=master.name,
                            headers={'X-Image-Bit-Depth': '8', 'X-Image-Source': 'master'})

    @app.put('/api/images/{identity}/tag')
    def tag_image(identity: str, value: Tag):
        store.tag(identity, value.tag)
        return {'saved': True}

    @app.delete('/api/images/{identity}')
    def delete_image(identity: str):
        store.trash(identity)
        return {'trashed': True}

    @app.post('/api/images/{identity}/restore')
    def restore_image(identity: str):
        store.trash(identity, restore=True)
        return {'restored': True}

    @app.get('/api/presets')
    def presets():
        return store.presets()

    @app.post('/api/presets')
    def add_preset(value: Preset):
        return {'id': store.add_preset(value.name, value.config.model_dump())}

    @app.delete('/api/presets/{identity}')
    def remove_preset(identity: str):
        store.delete_preset(identity)
        return {'deleted': True}

    # Model-combination profiles: save a working set once, switch back to it later.
    @app.get('/api/profiles')
    def profiles():
        return [{**{key: value for key, value in profile.items() if key != 'settings'},
                 'summary': {key: profile['settings'].get(key) for key in ('diffusion_model', 'text_encoder', 'vae', 'max_vram', 'extra_args')}}
                for profile in store.profiles()]

    @app.post('/api/profiles')
    def add_profile(value: ProfileIn):
        selection = {key: getattr(value.settings, key) for key in MODEL_KEYS}
        return store.add_profile(value.name, selection)

    @app.delete('/api/profiles/{identity}')
    def delete_profile(identity: str):
        return store.delete_profile(identity)

    @app.post('/api/profiles/{identity}/apply')
    def apply_profile(identity: str):
        profile = next((item for item in store.profiles() if item['id'] == identity), None)
        if not profile:
            raise HTTPException(404, '这个组合已不存在')
        if engine.active or engine.state == 'loading' or engine.component_operation or not engine.pending.empty():
            raise ValueError('生成或加载期间不能修改模型设置')
        merged = store.settings() | {key: profile['settings'][key] for key in MODEL_KEYS if key in profile['settings']}
        blockers = model_status(merged)['blockers']
        if blockers:
            raise ValueError('；'.join(blockers))
        store.save_settings(merged)
        return merged

    @app.post('/api/inputs')
    async def upload(file: UploadFile):
        raw = await file.read(25 * 1024 * 1024 + 1)
        if len(raw) > 25 * 1024 * 1024:
            raise HTTPException(413, '参考图上限 25 MiB')
        try:
            with Image.open(io.BytesIO(raw)) as im:
                im.load()
                identity = uuid.uuid4().hex
                im.save(store.data / 'inputs' / f'{identity}.png')
        except (OSError, ValueError, Image.DecompressionBombError):
            raise ValueError('请选择有效的 PNG、JPEG 或 WebP 图片')
        return {'id': identity, 'name': file.filename}

    @app.get('/api/jobs')
    def jobs():
        private = store.private_ids()
        return [{**job, 'image_ids': [identity for identity in job.get('image_ids', []) if identity not in private]}
                for job in engine.list_jobs() if job.get('task_type') not in ('upscale', 'outpaint')]

    @app.post('/api/jobs', status_code=202)
    def generate(value: Generation):
        if not value.content_confirmed:
            raise ValueError('请确认遵守内容约定')
        if re.search(r'肢解|血腥内脏|伪造证件|冒充真人|\b(dismemberment)\b', value.prompt, re.I):
            raise ValueError('提示词触发已约定的内容限制，请修改')
        if value.input_id and not (store.data / 'inputs' / f'{value.input_id}.png').is_file():
            raise ValueError('参考图不存在，请重新上传')
        return engine.submit(value.model_dump())

    @app.post('/api/jobs/{identity}/cancel')
    def cancel(identity: str):
        job = engine.cancel(identity)
        return {'cancellation_requested': bool(job.get('cancellation_requested')), 'job': job}

    @app.get('/api/vault')
    def vault():
        return secure_vault.files()

    @app.get('/api/vault/status')
    def vault_status():
        return secure_vault.status()

    @app.get('/api/vault/key')
    def vault_key_status():
        return {'configured': secure_vault.key.get() is not None}

    @app.put('/api/vault/key')
    def save_vault_key(value: VaultAction):
        if not value.password:
            raise ValueError('请设置一个至少 12 个字符的私密库密码')
        return secure_vault.save_password(value.password)

    @app.delete('/api/vault/key')
    def forget_vault_key():
        secure_vault.key.clear()
        return {'configured': False}

    @app.post('/api/vault/rekey')
    def rekey_vault(value: VaultRekey):
        if value.password != value.confirm_password:
            raise ValueError('两次新密码不一致')
        with engine.lock:
            ensure_vault_idle()
            try:
                return secure_vault.rekey(value.password, value.current_password)
            except InvalidTag:
                raise ValueError('当前密码不对，未做任何修改') from None

    @app.api_route('/api/password-book', methods=['GET', 'POST'])
    def retired_password_book():
        raise HTTPException(410, '已停用密码本。私密图片每次查看都需要输入密码。')

    @app.post('/api/password-book/{identity}/default')
    def retired_password_binding(identity: str):
        raise HTTPException(410, '已停用密码本。私密图片每次查看都需要输入密码。')

    def ensure_vault_idle():
        if engine.active or not engine.pending.empty() or engine.state == 'loading' or engine.component_operation:
            raise ValueError('请等待生成队列和模型操作结束后再执行私密文件迁移')

    def forget_private_jobs():
        with store.db() as db:
            identities = {r[0] for r in db.execute('SELECT job_id FROM private_jobs')}
        for identity in identities:
            engine.jobs.pop(identity, None)

    @app.post('/api/vault/migrate')
    def migrate_vault(value: NewVaultPassword):
        if value.password != value.confirm_password:
            raise ValueError('两次新密码不一致')
        with engine.lock:
            ensure_vault_idle()
            try:
                return secure_vault.migrate(value.password, value.legacy_password, value.keep_unreadable)
            except InvalidTag:
                raise ValueError('密码错误或加密文件已损坏') from None
            finally:
                forget_private_jobs()

    @app.post('/api/images/{identity}/encrypt')
    def encrypt(identity: str, value: VaultAction | None = None):
        supplied = value.password if value else None
        if supplied and value.confirm_password and supplied != value.confirm_password:
            raise ValueError('两次密码不一致')
        with engine.lock:
            ensure_vault_idle()
            try:
                return secure_vault.encrypt(identity, supplied)
            finally:
                forget_private_jobs()

    @app.post('/api/vault/{identity}/unlock')
    def unlock(identity: str, value: VaultAction | None = None):
        try:
            return secure_vault.unlock(identity, value.password if value else None)
        except InvalidTag:
            raise ValueError('密码错误或文件已损坏') from None

    @app.get('/')
    def index():
        return FileResponse(ROOT / 'web/index.html')

    app.mount('/static', StaticFiles(directory=ROOT / 'web'), name='static')
    return app
