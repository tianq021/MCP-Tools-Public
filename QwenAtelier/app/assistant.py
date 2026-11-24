"""DeepSeek-compatible streaming chat. Conversations are never persisted.

The API key lives in process memory only, unless the user explicitly asks to remember it:
then it is encrypted with Windows DPAPI (current user scope) and stored in data/assistant.key.
"""
import asyncio
import base64
import ctypes
from ctypes import wintypes
import io
import json
import os
import re
from urllib.parse import urlsplit

import httpx
from .storage import ROOT
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from PIL import Image
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from typing import Literal


class _Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_char))]


def _dpapi(raw, decrypt=False):
    """Encrypt/decrypt with the current Windows user's DPAPI key. Never leaves plaintext on disk."""
    if os.name != 'nt':
        raise OSError('DPAPI 仅在 Windows 上可用')
    crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    entropy = ctypes.create_string_buffer(b'qwen-atelier', 12)
    source = ctypes.create_string_buffer(raw, len(raw))
    blob_in = _Blob(len(raw), ctypes.cast(source, ctypes.POINTER(ctypes.c_char)))
    blob_entropy = _Blob(12, ctypes.cast(entropy, ctypes.POINTER(ctypes.c_char)))
    blob_out = _Blob()
    if decrypt:
        ok = crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, ctypes.byref(blob_entropy),
                                        None, None, 0, ctypes.byref(blob_out))
    else:
        ok = crypt32.CryptProtectData(ctypes.byref(blob_in), 'qwen-atelier', ctypes.byref(blob_entropy),
                                      None, None, 0, ctypes.byref(blob_out))
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(blob_out.data, blob_out.size)
    finally:
        kernel32.LocalFree(blob_out.data)


def normalize_url(value):
    value = value.strip().rstrip('/')
    if value.endswith('/chat/completions'):
        value = value[:-len('/chat/completions')]
    parsed = urlsplit(value)
    if (parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment
            or (parsed.scheme == 'http' and parsed.hostname not in ('localhost', '127.0.0.1', '::1'))):
        raise HTTPException(400, 'API 地址需使用 HTTPS；本机接口可使用 http://127.0.0.1:端口/v1')
    return value


class AssistantConfig(BaseModel):
    base_url: str = Field(default='https://api.deepseek.com', max_length=1000)
    model: str = Field(default='deepseek-flash', min_length=1, max_length=100)
    api_key: SecretStr | None = None
    remember: bool = False


class ChatMessage(BaseModel):
    role: Literal['user', 'assistant']
    content: str = Field(default='', max_length=24000)
    # Local upload ids (data/inputs/<id>.png). The DeepSeek API only accepts images in user messages.
    image_ids: list[str] = Field(default_factory=list, max_length=4)

    @field_validator('image_ids')
    @classmethod
    def known_ids(cls, value):
        for identity in value:
            if not re.fullmatch(r'[a-f0-9]{32}', identity):
                raise ValueError('图片标识不合法')
        return value

    @model_validator(mode='after')
    def needs_content(self):
        if self.role == 'assistant' and self.image_ids:
            raise ValueError('图片只能出现在用户消息里')
        if not self.content.strip() and not self.image_ids:
            raise ValueError('消息内容或图片至少要有一样')
        return self


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=41)
    thinking: bool = False

    @model_validator(mode='after')
    def bounded_history(self):
        if self.messages[-1].role != 'user':
            raise ValueError('最后一条消息必须来自用户')
        if sum(len(m.content) for m in self.messages) > 60000:
            raise ValueError('对话内容过长，请清空对话后重试')
        if sum(len(m.image_ids) for m in self.messages) > 8:
            raise ValueError('一次对话里最多附带 8 张图片，请清空对话后重试')
        return self


ERRORS = {401: 'API Key 无效，请检查后重新应用配置。', 402: 'DeepSeek 账户余额不足。',
          403: 'API 访问被拒绝，请检查账户权限。', 404: '接口或模型不存在，请检查 API 地址和模型名称。',
          429: '请求过于频繁，请稍后再试。'}

# The editable copy lives in prompts/system.md; this is only the fallback when that file is missing.
DEFAULT_SYSTEM_PROMPT = ('你是 Qwen Atelier 的中文创作助手，帮助用户讨论画面创意、撰写和优化 Qwen Image 提示词，'
                         '也可以回答普通问题。用户要求可直接使用的提示词时，把最终提示词放入独立的纯文本代码块，解释放在代码块外。'
                         '不要声称已查看本地图片、操作本机文件或生成图片；你只能看到此对话中发送的文字。'
                         '遵守用户工作空间的内容约定：不协助极端血腥或用于欺骗他人的图像创作。')
PROMPT_FILE = ROOT / 'prompts/system.md'
PROMPT_LIMIT = 20000
IMAGE_MAX_SIDE = 1536
IMAGE_MAX_BYTES = 20 * 1024 * 1024
_prompt_cache = {'stamp': None, 'text': DEFAULT_SYSTEM_PROMPT}


def parameter_block(store, engine):
    """Runtime snapshot of the workspace parameters, appended to the system prompt of every request."""
    from .engine import model_status
    settings = store.settings()
    status = model_status(settings, engine.loaded_settings if engine else None,
                          engine.components_status() if engine else None,
                          engine.component_support() if engine else False)
    lines = ['## 当前工作台参数（运行时快照，会随设置变化）']
    for component in status['components']:
        if component['name'] == '未配置':
            lines.append(f"- {component['label']}：未配置")
            continue
        size = f"{component['size'] / 1024 ** 3:.2f} GiB"
        lines.append(f"- {component['label']}：{component['name']}"
                     f"（{size}，{component['device']}，{'已加载' if component['loaded'] else '未加载'}）")
    lines.append(f"- 显存预算：{settings['max_vram']} GiB；界面允许 256–4096（向下对齐到 8 的倍数）")
    management = ('支持扩散模型、文本编码器、VAE、视觉编码器分别预加载和释放权重，保留的组件会复用；'
                  '只有释放后再次用到的权重才需重新读取，共享上下文保留' if status['individual_preload_supported']
                  else '当前运行库未提供独立组件权重管理接口')
    lines.append(f"- 整套权重预加载开关：{'开启' if settings.get('eager_load') else '关闭，首次使用时读取'}；{management}")
    lines.append('- 模型文件体积不等于实际内存 / 显存占用；CPU 卸载模式下权重保存在内存，GPU 计算时按需传输')
    lines.append('- 默认生成参数：1024×1024、30 步、CFG 1.0、采样器 euler、调度器默认、批量 1、VAE 分块开')
    states = {'idle': '未加载', 'loading': '加载中', 'ready': '已加载', 'error': '异常'}
    lines.append(f"- 推理引擎：stable-diffusion.cpp 0518b34；模型上下文：{states.get(engine.state if engine else 'idle', '未知')}")
    extra = (settings.get('extra_args') or '').strip()
    if extra:
        lines.append('- 自定义启动参数：' + ' '.join(extra.split('\n')))
    try:
        import pynvml
        pynvml.nvmlInit()
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            memory = pynvml.nvmlDeviceGetMemoryInfo(handle)
            lines.append(f"- 显卡：{pynvml.nvmlDeviceGetName(handle)}，显存 {memory.total / 1024 ** 3:.0f} GB")
        finally:
            pynvml.nvmlShutdown()
    except Exception:  # Monitoring is optional; the prompt still works without NVML.
        pass
    return '\n'.join(lines)


def system_content(store, engine):
    return system_prompt() + '\n\n' + parameter_block(store, engine)


def image_data_uri(path):
    """Re-encode a local upload as a data URL, downscaled to stay inside the API image limits."""
    try:
        with Image.open(path) as source:
            source.load()
            image = source.convert('RGB')
    except (OSError, ValueError):
        raise HTTPException(400, '图片无法读取，请重新上传')
    if max(image.size) > IMAGE_MAX_SIDE:
        image.thumbnail((IMAGE_MAX_SIDE, IMAGE_MAX_SIDE), Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format='JPEG', quality=88, optimize=True)
    raw = buffer.getvalue()
    if len(raw) > IMAGE_MAX_BYTES:
        raise HTTPException(400, '图片过大，请换一张更小的图')
    return 'data:image/jpeg;base64,' + base64.b64encode(raw).decode('ascii')


def system_prompt():
    """Read prompts/system.md on every request (cached by mtime), so local edits apply immediately."""
    try:
        stat = PROMPT_FILE.stat()
    except OSError:
        return DEFAULT_SYSTEM_PROMPT
    if _prompt_cache['stamp'] != stat.st_mtime_ns:
        try:
            text = PROMPT_FILE.read_text(encoding='utf-8').strip()[:PROMPT_LIMIT]
        except (OSError, UnicodeDecodeError):
            return DEFAULT_SYSTEM_PROMPT
        _prompt_cache.update(stamp=stat.st_mtime_ns, text=text or DEFAULT_SYSTEM_PROMPT)
    return _prompt_cache['text']



class Assistant:
    def __init__(self, store, engine=None):
        self.store = store
        self.engine = engine
        self.key = os.environ.get('DEEPSEEK_API_KEY', '').strip()
        self.key_url = 'https://api.deepseek.com' if self.key else None
        self.saved_file = store.data / 'assistant.key'
        self.transport = None  # MockTransport only in isolated tests.
        if not self.key:
            self._restore()

    def _restore(self):
        """Load a remembered key. DPAPI ties it to this Windows user, so a copied file is useless."""
        try:
            record = json.loads(self.saved_file.read_text(encoding='utf-8'))
            url = normalize_url(str(record['url']))
            key = _dpapi(base64.b64decode(record['key'], validate=True), decrypt=True).decode('utf-8').strip()
        except (OSError, KeyError, ValueError, TypeError):
            return  # Missing, corrupt, or written by another account: behave as if nothing was saved.
        if key:
            self.key, self.key_url = key, url

    def _remember(self, url, key):
        record = {'url': url, 'key': base64.b64encode(_dpapi(key.encode('utf-8'))).decode('ascii')}
        self.saved_file.parent.mkdir(parents=True, exist_ok=True)
        temp = self.saved_file.with_name(self.saved_file.name + '.tmp')
        temp.write_text(json.dumps(record), encoding='utf-8')
        os.replace(temp, self.saved_file)

    def forget_saved(self):
        try:
            self.saved_file.unlink()
        except FileNotFoundError:
            pass

    def config(self):
        settings = self.store.settings()
        url = normalize_url(settings['assistant_url'])
        prompt = system_content(self.store, self.engine)
        return {'base_url': url, 'model': settings['assistant_model'],
                'has_key': bool(self.key and self.key_url == url),
                'saved': bool(self.saved_file.is_file() and self.key_url == url),
                'system_prompt': {'path': 'prompts/system.md', 'chars': len(prompt),
                                  'persona_chars': len(system_prompt()), 'custom': PROMPT_FILE.is_file()}}

    def credentials(self):
        config = self.config()
        if not config['has_key']:
            raise HTTPException(400, '请先展开「API 配置」，填写 API Key 并应用配置。')
        return config, self.key

    def prepared_messages(self, request):
        """Turn local image ids into OpenAI-style content blocks, which DeepSeek accepts inline as data URLs."""
        prepared = []
        for message in request.messages:
            if message.image_ids:
                parts = [{'type': 'text', 'text': message.content.strip() or '请分析这张图片。'}]
                for identity in message.image_ids:
                    path = self.store.data / 'inputs' / f'{identity}.png'
                    if not path.is_file():
                        raise HTTPException(400, '附加的图片不存在，请重新上传')
                    parts.append({'type': 'image_url',
                                  'image_url': {'url': image_data_uri(path), 'detail': 'auto'}})
                prepared.append({'role': message.role, 'content': parts})
            else:
                prepared.append({'role': message.role, 'content': message.content})
        return prepared

    def client(self):
        return httpx.AsyncClient(transport=self.transport, trust_env=False, follow_redirects=False,
                                 timeout=httpx.Timeout(180, connect=15))

    def configure(self, value):
        url = normalize_url(value.base_url)
        model = value.model.strip()
        if not model:
            raise HTTPException(400, '模型名称不能为空')
        key = value.api_key.get_secret_value().strip() if value.api_key is not None else None
        if key is not None and (len(key) > 512 or any(c.isspace() for c in key)):
            raise HTTPException(400, 'API Key 格式不正确')
        # Never forward a previously stored credential to a new endpoint implicitly.
        if url != self.key_url:
            self.key, self.key_url = '', None
        if key:
            self.key, self.key_url = key, url
            if value.remember:
                try:
                    self._remember(url, key)
                except OSError as error:
                    raise HTTPException(400, f'密钥加密保存失败：{error}')
            else:
                self.forget_saved()
        with self.store.lock:
            settings = self.store.settings()
            settings.update(assistant_url=url, assistant_model=model)
            self.store.save_settings(settings)
        return self.config()

    async def check(self):
        config, key = self.credentials()
        try:
            async with self.client() as client:
                response = await client.get(config['base_url'] + '/models', headers={'Authorization': f'Bearer {key}'})
                if response.status_code != 200:
                    raise HTTPException(502, ERRORS.get(response.status_code, f'连接失败（HTTP {response.status_code}）。'))
                models = [item['id'] for item in response.json().get('data', []) if isinstance(item.get('id'), str)]
                return {'ok': True, 'models': models, 'model_available': config['model'] in models}
        except httpx.TimeoutException:
            raise HTTPException(504, '连接超时，请检查网络或 API 地址。')
        except (httpx.RequestError, ValueError, TypeError, KeyError):
            raise HTTPException(502, '无法读取模型列表，请检查 API 地址和网络连接。')

    async def stream(self, request, config, key, messages):
        def event(kind, **data):
            return json.dumps({'type': kind, **data}, ensure_ascii=False) + '\n'
        body = {'model': config['model'],
                'messages': [{'role': 'system', 'content': system_content(self.store, self.engine)}] + messages,
                'stream': True, 'thinking': {'type': 'enabled' if request.thinking else 'disabled'}}
        content_seen, ended, finish, usage = False, False, None, None
        try:
            async with self.client() as client:
                async with client.stream('POST', config['base_url'] + '/chat/completions', json=body,
                                         headers={'Authorization': f'Bearer {key}'}) as response:
                    if response.status_code != 200:
                        yield event('error', message=ERRORS.get(response.status_code, f'对话请求失败（HTTP {response.status_code}），请检查配置。'))
                        return
                    async for line in response.aiter_lines():
                        if not line.startswith('data:'):
                            continue
                        payload = line[5:].strip()
                        if payload == '[DONE]':
                            ended = True
                            break
                        chunk = json.loads(payload)
                        if chunk.get('error'):
                            yield event('error', message='上游服务中断了回复，请稍后重试。')
                            return
                        usage = chunk.get('usage') or usage
                        for choice in chunk.get('choices', []):
                            delta = choice.get('delta', {})
                            if delta.get('reasoning_content'):
                                yield event('reasoning', text=delta['reasoning_content'])
                            if delta.get('content'):
                                content_seen = True
                                yield event('content', text=delta['content'])
                            finish = choice.get('finish_reason') or finish
                    if not content_seen:
                        yield event('error', message='服务没有返回正文，请重试或切换模型。')
                    elif not ended and not finish:
                        yield event('error', message='连接提前结束，回复可能不完整，请重试。')
                    else:
                        yield event('done', model=config['model'], usage=usage, finish_reason=finish)
        except asyncio.CancelledError:
            raise  # StreamingResponse disconnect closes the HTTP stream via async context managers.
        except httpx.TimeoutException:
            yield event('error', message='等待回复超时，请稍后重试。')
        except (httpx.RequestError, ValueError, KeyError, TypeError):
            # Never echo raw upstream exceptions, headers or bodies containing credentials.
            yield event('error', message='连接异常或响应格式不兼容，请检查 API 配置后重试。')


def assistant_routes(store, engine=None):
    assistant = Assistant(store, engine)
    router = APIRouter(prefix='/api/assistant')

    @router.get('/config')
    def config():
        return assistant.config()

    @router.get('/prompt')
    def prompt():
        text = system_content(store, engine)
        return {'path': 'prompts/system.md', 'editable': True, 'chars': len(text),
                'persona': system_prompt(), 'parameters': parameter_block(store, engine), 'text': text}

    @router.put('/config')
    def configure(value: AssistantConfig):
        return assistant.configure(value)

    @router.delete('/key')
    def forget_key():
        assistant.key, assistant.key_url = '', None
        assistant.forget_saved()
        return {'cleared': True}

    @router.post('/check')
    async def check():
        return await assistant.check()

    @router.post('/chat')
    async def chat(value: ChatRequest):
        config, key = assistant.credentials()
        messages = assistant.prepared_messages(value)
        return StreamingResponse(assistant.stream(value, config, key, messages), media_type='application/x-ndjson',
                                 headers={'X-Accel-Buffering': 'no'})

    return router, assistant
