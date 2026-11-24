"""Read-only status and toolkit views for the BL2 launcher."""
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import tkinter as tk
from tkinter import ttk
from runtime_report import read_snapshot


def check_localization(base):
    manifests = sorted((base / 'localization-codex').glob('backup-*/manifest.json'))
    if not manifests:
        return '未找到汉化安装记录，无法确认安装状态。'
    manifest = manifests[-1]
    data = json.loads(manifest.read_text(encoding='utf-8'))
    matched = 0
    problems = []
    changed_configs = []
    for record in data['files']:
        path = Path(record['path'])
        try:
            content = path.read_bytes()
        except OSError as exc:
            problems.append(f'无法读取：{path}（{exc}）')
            continue
        if hashlib.sha256(content).hexdigest() == record['after']:
            matched += 1
        elif path.suffix.lower() == '.ini':
            changed_configs.append(str(path))
        else:
            problems.append(f'文件与安装记录不同：{path}')
    exe = Path(data['game']) / 'Binaries/Win32/Borderlands2.exe'
    expected = data.get('protected', {}).get(str(exe))
    try:
        actual = hashlib.sha256(exe.read_bytes()).hexdigest()
        exe_status = ('与汉化安装时一致（保留当时的背包补丁）' if actual == expected
                      else '与汉化安装时不同，需要另行检查')
    except OSError as exc:
        exe_status = f'无法读取：{exc}'
    lines = [f'汉化安装记录：{data["date"]}',
             f'文件哈希一致：{matched}/{len(data["files"])}',
             f'游戏程序：{exe_status}',
             '游戏内中文显示：尚未验证；文件校验不能代替实际运行。']
    if changed_configs:
        lines += ['配置文件有后续变化（可能是画质设置，不等于汉化失效）：', *changed_configs]
    lines += problems
    if not problems and not changed_configs:
        lines.append('中文资源和字体配置与安装记录一致。')
    lines += ['', f'备份记录：{manifest}']
    return '\n'.join(lines)


def run_query(base, arguments):
    python = Path(r'C:\Program Files\Python310\python.exe')
    env = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    result = subprocess.run([str(python), '-m', 'bl2toolkit', *arguments],
                            cwd=base, env=env, capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=60,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    output = (result.stdout + '\n' + result.stderr).strip()
    return (f'查询失败（退出码 {result.returncode}）\n' if result.returncode else '') + (output or '查询完成，无输出。')


def add_tools_tab(notebook, base):
    tab = ttk.Frame(notebook, padding=12)
    notebook.add(tab, text='汉化与工具')
    ttk.Label(tab, text='查看汉化、角色、技能和共享储藏箱。以下查询不会修改存档。',
              wraplength=540).pack(anchor='w')
    row = ttk.Frame(tab)
    row.pack(fill='x', pady=8)
    actions = []
    mailbox = queue.Queue()
    busy = False
    output = tk.Text(tab, height=15, wrap='word', font=('Microsoft YaHei UI', 9))
    scroll = ttk.Scrollbar(tab, command=output.yview)
    output.configure(yscrollcommand=scroll.set)

    def show(text):
        output.configure(state='normal')
        output.delete('1.0', 'end')
        output.insert('1.0', text)
        output.configure(state='disabled')

    def begin(work):
        nonlocal busy
        if busy:
            return
        busy = True
        for button in actions:
            button.configure(state='disabled')
        show('正在查询…')
        def worker():
            try:
                mailbox.put(work())
            except Exception as exc:
                mailbox.put(f'查询未完成：{exc}')
        threading.Thread(target=worker, daemon=True).start()
        tab.after(100, poll)

    def poll():
        nonlocal busy
        try:
            result = mailbox.get_nowait()
        except queue.Empty:
            tab.after(100, poll)
            return
        show(result)
        busy = False
        for button in actions:
            button.configure(state='normal')

    def button(parent, title, command):
        widget = ttk.Button(parent, text=title, command=command)
        widget.pack(side='left', padx=(0, 5))
        actions.append(widget)

    button(row, '校验汉化', lambda: begin(lambda: check_localization(base)))
    button(row, '角色列表', lambda: begin(lambda: run_query(base, ['saves'])))
    button(row, '共享储藏箱', lambda: begin(lambda: run_query(base, ['stash', 'info'])))
    second = ttk.Frame(tab)
    second.pack(fill='x', pady=(0, 8))
    ttk.Label(second, text='角色槽位').pack(side='left')
    slot = tk.StringVar(value='1')
    ttk.Entry(second, textvariable=slot, width=12).pack(side='left', padx=5)

    def skills():
        value = slot.get().strip()
        if not re.fullmatch(r'(?:[0-9]{1,4}|(?:[Ss]ave)?[0-9A-Fa-f]{4}(?:\.sav)?)', value):
            show('请先查看角色列表，再输入槽位，例如 1 或 Save000B。')
            return
        begin(lambda: run_query(base, ['skill', 'summary', value]))

    button(second, '查看技能', skills)
    button(second, '打开工具目录', lambda: os.startfile(base))
    third = ttk.Frame(tab)
    third.pack(fill='x', pady=(0, 8))
    radius = tk.StringVar(value='5000')
    target_name = tk.StringVar(value='')
    def snapshot():
        distance, name = radius.get().strip(), target_name.get().strip()
        begin(lambda: read_snapshot(radius_uu=distance, target=name))
    button(third, '地图/任务/单位快照', snapshot)
    ttk.Label(third, text='范围 UU').pack(side='left')
    ttk.Entry(third, textvariable=radius, width=7).pack(side='left', padx=3)
    ttk.Label(third, text='单位名/路径').pack(side='left')
    ttk.Entry(third, textvariable=target_name, width=16).pack(side='left', padx=3)
    scroll.pack(side='right', fill='y')
    output.pack(fill='both', expand=True)
    show('点击“校验汉化”检查中文资源、字体配置和游戏程序。\n\n天邈 v1.82 安装完成后仍需进游戏确认中文显示。\n技能效果扩展正在由 DSH 研究，尚未作为已生效功能提供。')
