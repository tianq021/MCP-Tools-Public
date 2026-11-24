import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Literal
import re

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse


_launch_lock = threading.Lock()


def _ready(client, url):
    try:
        response = client.get(url + '/api/status')
    except (httpx.ConnectError, httpx.ConnectTimeout):
        return False
    except httpx.RequestError as exc:
        raise HTTPException(503, '视频端口响应异常，请检查 Wan 服务后重试。') from exc
    try:
        info = response.json()
    except ValueError:
        info = None
    if response.status_code == 200 and isinstance(info, dict) and info.get('app') == 'Wan Video':
        return True
    raise HTTPException(409, '视频端口已被其他服务占用，请检查 configs/wan-video.json 中的端口。')


def video_config(config_path):
    try:
        config = json.loads(config_path.read_text(encoding='utf-8'))
        root = Path(config['directory'])
        python = Path(config['python'])
        port = int(os.environ.get('WAN_VIDEO_PORT', config['port']))
        if not 1 <= port <= 65535:
            raise ValueError('port')
    except (KeyError, OSError, TypeError, ValueError) as exc:
        raise HTTPException(503, '视频入口配置无效，请检查 configs/wan-video.json 和 WAN_VIDEO_PORT。') from exc
    return root, python, f'http://127.0.0.1:{port}'


def open_video(config_path):
    root, python, url = video_config(config_path)
    port = url.rsplit(':', 1)[1]
    with _launch_lock, httpx.Client(trust_env=False, timeout=2) as client:
        if _ready(client, url):
            return {'url': url}
        script = root / 'serve.py'
        if not python.is_file() or not script.is_file():
            raise HTTPException(503, '找不到视频工作台或 Python 环境，请检查 configs/wan-video.json 中的路径。')
        try:
            proc = subprocess.Popen(
                [str(python), str(script), '--no-browser'], cwd=root,
                env={**os.environ, 'WAN_VIDEO_PORT': str(port)},
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except OSError as exc:
            raise HTTPException(503, '无法启动视频服务，请检查配置的 Python 环境。') from exc
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if _ready(client, url):
                return {'url': url}
            if proc.poll() is not None:
                break
            time.sleep(0.2)
    raise HTTPException(503, '视频服务未就绪，请打开「启动视频工作台.cmd」查看启动信息。')


def video_routes(store, engine):
    router = APIRouter(prefix='/api/video')
    config_path = store.root / 'configs' / 'wan-video.json'

    def check_start():
        engine._check_power()
        if engine.active or not engine.pending.empty() or engine.state == 'loading' or engine.component_operation:
            raise HTTPException(409, '请等当前 Qwen 任务完成，再启动视频推理。')

    def check_id(identity):
        if not re.fullmatch('[0-9a-f]{16}', identity):
            raise HTTPException(404, '视频不存在')

    async def forward(method, path, **kwargs):
        url = video_config(config_path)[2]
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=30) as client:
                response = await client.request(method, url + path, headers={
                    'X-Wan-Request': '1', **kwargs.pop('headers', {})}, **kwargs)
        except httpx.RequestError as exc:
            raise HTTPException(503, '视频服务连接中断，请重新连接。') from exc
        if not response.is_success:
            raise HTTPException(response.status_code, response.text[:500])
        return response.json()

    @router.post('/open')
    def connect():
        return open_video(config_path)

    @router.get('/status')
    async def status():
        result = await forward('GET', '/api/status')
        if 'model' not in result:
            raise HTTPException(503, '视频服务版本较旧，请重启视频服务。')
        return result

    @router.post('/model/{action}')
    async def model(action: Literal['preload', 'unload']):
        if action == 'preload':
            check_start()
        return await forward('POST', '/api/model/' + action)

    @router.post('/jobs')
    async def submit(request: Request):
        check_start()
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > 16 * 1024**2 + 65536:
                raise HTTPException(413, '参考图最大 16 MiB')
            body.extend(chunk)
        return await forward('POST', '/api/jobs', content=bytes(body),
                             headers={'Content-Type': request.headers.get('content-type', '')})

    @router.post('/jobs/{identity}/cancel')
    async def cancel(identity: str):
        check_id(identity)
        return await forward('POST', f'/api/jobs/{identity}/cancel')

    @router.get('/files/{identity}.mp4')
    def video(identity: str):
        check_id(identity)
        path = video_config(config_path)[0] / 'outputs' / identity / 'video.mp4'
        if not path.is_file():
            raise HTTPException(404, '视频文件已移走')
        return FileResponse(path, media_type='video/mp4')

    return router
