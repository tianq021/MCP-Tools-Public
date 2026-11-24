"""Small standalone Borderlands 2 launcher; no save or weapon modifications."""
import ctypes
import datetime
import os
from pathlib import Path
import re
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox
from launcher_support import add_tools_tab
from launcher_offline import prepare_offline
from launcher_qol import prepare_qol
from runtime_window import add_runtime_tab

GAME = Path(r'F:\SteamLibrary\steamapps\common\Borderlands 2\Binaries\Win32\Borderlands2.exe')
buf = ctypes.create_unicode_buffer(32768)
if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf):
    raise RuntimeError('无法找到文档目录')
CONFIG = Path(buf.value) / 'My Games/Borderlands 2/WillowGame/Config/WillowEngine.ini'

def read_config():
    raw = CONFIG.read_bytes()
    encoding = 'utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig' if raw.startswith(b'\xef\xbb\xbf') else 'utf-8'
    return raw.decode(encoding), encoding

def section(text):
    match = re.search(r'(?ms)^\[SystemSettings\][^\r\n]*\r?\n(.*?)(?=^\[|\Z)', text)
    if not match:
        raise ValueError('找不到 SystemSettings，未修改配置')
    return match

def update(text, values):
    match = section(text)
    body = match.group(1)
    for key, value in values.items():
        pattern = rf'(?m)^({re.escape(key)}=)[^\r\n]*'
        if not re.search(pattern, body):
            raise ValueError(f'配置缺少 {key}，未修改配置')
        body = re.sub(pattern, lambda m: m.group(1) + str(value), body)
    return text[:match.start(1)] + body + text[match.end(1):]

def running():
    return 'borderlands2.exe' in subprocess.check_output(
        ['tasklist', '/FI', 'IMAGENAME eq Borderlands2.exe', '/FO', 'CSV', '/NH'],
        creationflags=subprocess.CREATE_NO_WINDOW).decode(errors='replace').lower()

def main():
    root = tk.Tk()
    root.title('无主之地 2 · 快速启动')
    root.geometry('660x680')
    root.resizable(False, False)
    frame = ttk.Frame(root, padding=24)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='无主之地 2', font=('Microsoft YaHei UI', 22, 'bold')).pack(anchor='w')
    ttk.Label(frame, text='直接启动游戏 · 独立显示设置').pack(anchor='w', pady=(4, 20))
    text, _ = read_config()
    values = dict(re.findall(r'(?m)^([^=\r\n]+)=([^\r\n]*)', section(text).group(1)))
    variables = {}
    notebook = ttk.Notebook(frame)
    add_runtime_tab(notebook)
    notebook.pack(fill='both', expand=True, pady=8)
    display = ttk.Frame(notebook, padding=16)
    quality = ttk.Frame(notebook, padding=16)
    effects = ttk.Frame(notebook, padding=16)
    notebook.add(display, text='显示')
    notebook.add(quality, text='画质与性能')
    notebook.add(effects, text='视觉效果')
    add_tools_tab(notebook, Path(__file__).resolve().parent)
    target = display
    mappings = {}
    def combo(label, key, choices, initial):
        row = ttk.Frame(target)
        row.pack(fill='x', pady=5)
        ttk.Label(row, text=label, width=14).pack(side='left')
        var = tk.StringVar(value=initial)
        ttk.Combobox(row, textvariable=var, values=choices, state='readonly', width=29).pack(side='right')
        variables[key] = var
    current = values['ResX'] + ' × ' + values['ResY']
    combo('分辨率', 'resolution', list(dict.fromkeys([current, '1280 × 720', '1600 × 900', '1920 × 1080', '2560 × 1440', '3840 × 2160'])), current)
    mode = '无边框窗口' if values['WindowedFullscreen'].lower() == 'true' else '全屏' if values['Fullscreen'].lower() == 'true' else '窗口'
    combo('显示模式', 'mode', ['全屏', '窗口', '无边框窗口'], mode)
    ttk.Label(display, text='启动时使用游戏当前设置；仅点击“保存设置”才写入画质配置。\n离线选项下次启动生效；游戏仍可能需要 Steam 后台。', wraplength=530).pack(anchor='w', pady=20)
    offline = tk.BooleanVar(value=True)
    ttk.Checkbutton(display, text='默认离线启动（不连接 SHiFT）', variable=offline).pack(anchor='w')
    ttk.Label(display, text='下次启动：F1 快速旅行；F5 保存后重载并恢复位置。\nF6 导出地图/任务快照；F9 开关经验观察（不发经验）。', wraplength=530).pack(anchor='w', pady=8)
    target = quality
    def mapped(label, key, choices):
        options = dict(choices)
        current = next((name for name, value in choices if str(value) == values[key]), '当前值：' + values[key])
        options[current] = values[key]
        combo(label, key, list(options), current)
        mappings[key] = options
    mapped('纹理质量', 'TextureQuality', [('高', 0), ('中', 1), ('低', 2)])
    mapped('可视距离', 'ViewDistance', [('低', 0), ('中', 1), ('高', 2), ('超高', 3)])
    mapped('弹痕数量', 'NumberOfDecals', [('低', 0), ('中', 1), ('高', 2)])
    mapped('各向异性过滤', 'MaxAnisotropy', [('关闭', 0), ('2×', 2), ('4×', 4), ('8×', 8), ('16×', 16)])
    mapped('PhysX 特效', 'PhysXLevel', [('低', 0), ('中', 1), ('高', 2)])
    mapped('渲染比例', 'ScreenPercentage', [(str(n) + '%', n) for n in (50, 75, 100)])
    mapped('阴影分辨率', 'MaxShadowResolution', [(str(n), n) for n in (256, 512, 1024, 2048)])
    ttk.Label(quality, text='PhysX、远景和阴影会增加性能开销。\n保留当前值不会自动套用预设。', wraplength=530).pack(anchor='w', pady=15)
    for key, label in [('UseVsync', '垂直同步'), ('DynamicShadows', '动态阴影'), ('AmbientOcclusion', '环境光遮蔽'), ('DepthOfField', '景深'), ('FXAA', 'FXAA 抗锯齿'), ('MotionBlur', '运动模糊'), ('Bloom', '泛光'), ('bAllowLightShafts', '光束'), ('LensFlares', '镜头光晕'), ('Distortion', '扭曲效果'), ('DynamicDecals', '动态贴花'), ('FogVolumes', '体积雾'), ('AllowRadialBlur', '径向模糊'), ('OneFrameThreadLag', '单帧渲染队列')]:
        var = tk.BooleanVar(value=values[key].lower() == 'true')
        variables[key] = var
        ttk.Checkbutton(effects, text=label, variable=var).pack(anchor='w', pady=2)
    initial = {k: v.get() for k, v in variables.items()}
    status = tk.StringVar(value='启动不改画质；默认离线。保存设置会自动备份原配置。')
    ttk.Label(frame, textvariable=status, wraplength=465).pack(anchor='w', pady=12)
    def save():
        if running():
            raise RuntimeError('请先退出游戏，再保存显示设置。')
        width, height = variables['resolution'].get().split(' × ')
        changes = {k: str(v.get()) for k, v in variables.items() if k not in ('resolution', 'mode')}
        for key, options in mappings.items():
            changes[key] = str(options[variables[key].get()])
        mode = variables['mode'].get()
        changes.update(ResX=width, ResY=height, Fullscreen=str(mode != '窗口'), WindowedFullscreen=str(mode == '无边框窗口'))
        original, encoding = read_config()
        live = dict(re.findall(r'(?m)^([^=\r\n]+)=([^\r\n]*)', section(original).group(1)))
        changed = {k for k, v in variables.items() if v.get() != initial[k]}
        wanted = changed - {'resolution', 'mode'}
        if 'resolution' in changed:
            wanted.update(('ResX', 'ResY'))
        if 'mode' in changed:
            wanted.update(('Fullscreen', 'WindowedFullscreen'))
        changes = {k: v for k, v in changes.items() if k in wanted}
        for key in changes:
            if live[key] != values[key]:
                raise RuntimeError(f'{key} 已被其他程序修改，请关闭并重新打开启动器。')
        result = update(original, changes)
        if result != original:
            backup = CONFIG.with_name(CONFIG.name + '.launcher-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.bak')
            backup.write_bytes(CONFIG.read_bytes())
            temporary = CONFIG.with_suffix('.launcher.tmp')
            temporary.write_bytes(result.encode(encoding))
            os.replace(temporary, CONFIG)
        values.update(changes)
        initial.update({k: v.get() for k, v in variables.items()})
        status.set('设置已保存；原配置备份位于游戏配置目录。')
    def action(launch=False):
        try:
            if launch and not GAME.is_file():
                raise FileNotFoundError(f'找不到游戏：{GAME}')
            if not launch:
                save()
                return
            if running():
                raise RuntimeError('游戏已经在运行，请先退出再重新启动。')
            if launch:
                prepare_qol(Path(__file__).resolve().parent, GAME)
                prepare_offline(Path(__file__).resolve().parent, GAME, offline.get())
                process = subprocess.Popen([str(GAME), '-NoLauncher'], cwd=GAME.parent)
                status.set('已启动游戏，正在检查进程…')
                def check():
                    code = process.poll()
                    status.set('游戏进程正在运行。' if code is None else f'游戏已退出（{code}）；可能需要 Steam 后台运行。')
                root.after(5000, check)
        except Exception as exc:
            messagebox.showerror('操作未完成', str(exc), parent=root)
    buttons = ttk.Frame(frame)
    buttons.pack(fill='x', pady=4)
    ttk.Button(buttons, text='保存设置', command=action).pack(side='left')
    ttk.Button(buttons, text='打开配置与备份', command=lambda: os.startfile(CONFIG.parent)).pack(side='left', padx=8)
    ttk.Button(buttons, text='启动游戏（使用游戏设置）', command=lambda: action(True)).pack(side='right')
    root.mainloop()

if __name__ == '__main__':
    main()
