"""Local launcher: one fixed port, one instance, reuse the running service.

The port is never silently changed. If this application already answers on the
port, the launcher only opens a browser tab; a second copy would start a second
engine and load the models again. If another program owns the port, the launcher
stops and says so instead of drifting to a random port.
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import socket
import sys
import threading
import urllib.request
import webbrowser

import uvicorn

from app.main import create_app
from app.storage import ROOT, Store

MUTEX_NAME = 'Local\\Qwen-Atelier-single-instance'
URL_FILE = ROOT / 'data' / 'server-url.txt'
ERROR_ALREADY_EXISTS = 183


def service_at(url, timeout=1.5):
    """True only when the URL answers as this application, not just any server."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url.rstrip('/') + '/api/session', timeout=timeout) as response:
            if response.status != 200:
                return False
            return json.loads(response.read(4096)).get('app') == 'Qwen Atelier'
    except (OSError, ValueError):
        return False


def remembered_url():
    try:
        url = URL_FILE.read_text(encoding='utf-8').strip()
    except OSError:
        return None
    return url if url.startswith('http://127.0.0.1:') else None


def acquire_single_instance():
    """A named Windows mutex keeps a second engine from loading the same models."""
    if os.name != 'nt':
        return None
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        return None
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return False
    return handle


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--port', type=int)
    arguments = parser.parse_args()
    preferred = arguments.port or Store().settings()['port']

    # An instance already serving this project: reuse it, never start a second one.
    # The configured port is checked first so a manual --port still finds the live copy.
    for candidate in (f'http://127.0.0.1:{preferred}', remembered_url()):
        if candidate and service_at(candidate):
            print(f'已在运行：{candidate}', flush=True)
            print('已复用现有服务，未启动第二个实例（避免重复加载模型）。', flush=True)
            if not arguments.no_browser:
                webbrowser.open(candidate)
            # Repair a missing or stale address file so the next launch goes straight there.
            if remembered_url() != candidate:
                URL_FILE.write_text(candidate, encoding='utf-8')
            return 0

    lock = acquire_single_instance()
    if lock is False:
        print('另一个本工作台实例已在运行，但目标端口没有响应。', flush=True)
        print('请关闭之前启动的窗口（或结束残留的 python 进程）后再试。', flush=True)
        return 1

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        sock.bind(('127.0.0.1', preferred))
    except OSError:
        sock.close()
        print(f'端口 {preferred} 已被其他程序占用，未启动。', flush=True)
        print('为避免换端口后出现第二个实例、重复加载模型，这里不会自动改用其他端口。', flush=True)
        print(f'可以关闭占用该端口的程序，或在 data\\settings.json 中修改 port 后重试。', flush=True)
        return 1
    sock.listen(128)
    port = sock.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    print(f'Qwen Atelier: {url}', flush=True)
    URL_FILE.write_text(url, encoding='utf-8')
    if not arguments.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    server = uvicorn.Server(uvicorn.Config(create_app(), host='127.0.0.1', port=port, log_level='info'))
    try:
        server.run(sockets=[sock])
    finally:
        sock.close()
        if remembered_url() == url:
            URL_FILE.unlink(missing_ok=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
