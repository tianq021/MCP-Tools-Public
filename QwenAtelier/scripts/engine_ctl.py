"""Inspect and unload the resident inference engine from the desktop.

Works without the web service: the engine is described by `data/engine.json`
and controlled by PID. Run it directly, or double-click 卸载模型.cmd.

    .\\.venv\\Scripts\\python.exe scripts\\engine_ctl.py            # interactive
    .\\.venv\\Scripts\\python.exe scripts\\engine_ctl.py --status
    .\\.venv\\Scripts\\python.exe scripts\\engine_ctl.py --unload vae
    .\\.venv\\Scripts\\python.exe scripts\\engine_ctl.py --unload all
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from app.process import EngineRegistry, pid_alive, process_memory, process_path, terminate_pid  # noqa: E402

KEYS = ('diffusion_model', 'text_encoder', 'vae', 'vision')
LABELS = {'diffusion_model': '扩散模型', 'text_encoder': '文本编码器', 'vae': 'VAE', 'vision': '视觉编码器'}
EXE = ROOT / 'runtime/stable-diffusion.cpp/sd-server.exe'


def gib(value):
    return f'{value / 1024 ** 3:.2f} GiB' if isinstance(value, (int, float)) and value else '—'


def pad(text, width):
    """Pad by display width: Chinese characters take two columns."""
    used = sum(2 if ord(char) > 0x2E80 else 1 for char in text)
    return text + ' ' * max(1, width - used)


def client(url, timeout=5):
    return httpx.Client(trust_env=False, timeout=timeout, base_url=url)


def find_engine():
    record = EngineRegistry(ROOT / 'data').read()
    if not record:
        return None
    pid, port = int(record['pid']), int(record['port'])
    if not pid_alive(pid):
        return {'record': record, 'alive': False}
    actual = process_path(pid)
    if actual and Path(actual).resolve() != EXE.resolve():
        return {'record': record, 'alive': False, 'foreign': actual}
    return {'record': record, 'alive': True, 'pid': pid, 'url': f'http://127.0.0.1:{port}'}


def gpu_usage(pid):
    try:
        import pynvml
        pynvml.nvmlInit()
    except Exception:
        return None, None
    try:
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        used = pynvml.nvmlDeviceGetMemoryInfo(handle).used
        total = pynvml.nvmlDeviceGetMemoryInfo(handle).total
        mine = None
        try:
            for item in pynvml.nvmlDeviceGetComputeRunningProcesses(handle):
                if item.pid == pid:
                    mine = item.usedGpuMemory
        except Exception:
            mine = None
        return mine, (used, total)
    except Exception:
        return None, None
    finally:
        try:
            pynvml.nvmlShutdown()
        except Exception:
            pass


def snapshot():
    engine = find_engine()
    if engine is None:
        print('引擎未运行：data/engine.json 不存在。')
        return None
    if not engine['alive']:
        print('引擎未运行：记录的进程已经退出。')
        if engine.get('foreign'):
            print(f'注意：PID {engine["record"]["pid"]} 现在是别的程序：{engine["foreign"]}')
        return None
    pid, url = engine['pid'], engine['url']
    try:
        with client(url) as http:
            status = http.get('/sdcpp/v1/models/status').json()
            components = http.get('/sdcpp/v1/models/components').json().get('components', {})
    except Exception as error:
        print(f'引擎进程存在（PID {pid}），但接口无响应：{error}')
        return {'pid': pid, 'url': url, 'status': {}, 'components': {}, 'settings': engine['record'].get('settings', {})}

    memory = process_memory(pid) or {}
    mine, card = gpu_usage(pid)
    settings = engine['record'].get('settings', {})
    print(f'引擎 PID {pid} · 端口 {url.rsplit(":", 1)[-1]} · 状态 {status.get("state", "未知")}')
    parts = [f'进程内存 {gib(memory.get("process_rss"))}']
    parts.append(f'进程显存 {gib(mine) if mine else "未能读取"}')
    if card:
        parts.append(f'整卡 {gib(card[0])} / {gib(card[1])}')
    print(' · '.join(parts))
    print()
    print(pad('组件', 12) + pad('设备', 8) + pad('权重文件', 11) + pad('已读入', 9) + pad('缓冲区内存', 13) + '缓冲区显存')
    for key in KEYS:
        info = components.get(key) or {}
        device = 'CPU' if (key in ('text_encoder', 'vision') and settings.get('text_encoder_cpu')) or (key == 'vae' and settings.get('vae_cpu')) else 'CUDA0'
        tensors, loaded = info.get('tensors'), info.get('loaded_tensors')
        ratio = f'{loaded}/{tensors}' if tensors else '—'
        print(pad(LABELS[key], 12) + pad(device, 8) + pad(gib(info.get('weight_bytes')), 11) + pad(ratio, 9)
              + pad(gib(info.get('host_buffer_bytes')), 13) + gib(info.get('device_buffer_bytes')))
    print()
    print('缓冲区是引擎统计的权重占用；进程内存/显存包含计算缓存与共享上下文。')
    return {'pid': pid, 'url': url, 'status': status, 'components': components, 'settings': settings}


def unload_component(key):
    engine = find_engine()
    if not engine or not engine.get('alive'):
        print('引擎未运行，无需卸载。')
        return False
    try:
        with client(engine['url'], timeout=1800) as http:
            response = http.post(f'/sdcpp/v1/models/components/{key}/release')
        if response.status_code != 200:
            print(f'卸载失败：{response.text[:300]}')
            return False
    except Exception as error:
        print(f'卸载失败：{error}')
        return False
    print(f'已释放{LABELS.get(key, key)}的权重，其他组件保持原状。')
    return True


def unload_all():
    engine = find_engine()
    if not engine or not engine.get('alive'):
        print('引擎未运行，无需卸载。')
        EngineRegistry(ROOT / 'data').clear()
        return True
    pid, url = engine['pid'], engine['url']
    try:
        with client(url, timeout=30) as http:
            http.post('/sdcpp/v1/models/unload', json={})
    except Exception:
        pass
    if not terminate_pid(pid, timeout=15):
        print(f'无法结束推理进程 PID {pid}，请用任务管理器检查。')
        return False
    EngineRegistry(ROOT / 'data').clear()
    print(f'已卸载全部模型权重并结束推理进程 PID {pid}，内存与显存已释放。')
    return True


def interactive():
    while True:
        print('\n' + '=' * 56)
        state = snapshot()
        print('\n操作：')
        print('  1) 卸载扩散模型权重')
        print('  2) 卸载文本编码器权重')
        print('  3) 卸载 VAE 权重')
        print('  4) 卸载视觉编码器权重')
        print('  5) 卸载全部模型并关闭引擎')
        print('  0) 退出')
        choice = input('选择编号：').strip()
        if choice in ('0', 'q', ''):
            return 0
        actions = {'1': 'diffusion_model', '2': 'text_encoder', '3': 'vae', '4': 'vision'}
        if choice in actions:
            if not state:
                continue
            unload_component(actions[choice])
        elif choice == '5':
            if input('确认卸载全部模型并关闭引擎？输入 yes 继续：').strip().lower() in ('yes', 'y'):
                unload_all()
                return 0
        else:
            print('无效的编号。')


def main():
    parser = argparse.ArgumentParser(description='检查并卸载常驻推理引擎的模型')
    parser.add_argument('--status', action='store_true', help='只显示占用，不做修改')
    parser.add_argument('--unload', metavar='KEY', help='all 或 diffusion_model/text_encoder/vae/vision')
    arguments = parser.parse_args()
    if arguments.unload:
        if arguments.unload == 'all':
            return 0 if unload_all() else 1
        if arguments.unload not in KEYS:
            print('未知组件：' + arguments.unload)
            return 2
        return 0 if unload_component(arguments.unload) else 1
    if arguments.status:
        return 0 if snapshot() else 1
    return interactive()


if __name__ == '__main__':
    sys.exit(main())
