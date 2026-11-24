"""Native Windows desktop UI. Game operations run on a single worker thread."""
import ctypes
import json
import os
from pathlib import Path
import queue
import sys
import threading
from types import SimpleNamespace
import tkinter as tk
from tkinter import ttk, filedialog

ROOT = Path(sys._MEIPASS) if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'src'))
from apply_runtime_patches import run_request, OUTPUT

BG = '#111821'
CARD = '#1c2633'
TEXT = '#ecf2fa'
MUTED = '#a8b6c9'
ACCENT = '#f4be46'


def request_for(action, amount='10'):
    args = SimpleNamespace(status=False, skill=None, xp=None, backpack_add=0, apply=False,
                           cash_add=0, eridium_add=0, cancel_currency=False)
    if action == 'status':
        args.status = True
    elif action in ('skill_on', 'skill_off'):
        args.skill, args.apply = action.split('_')[1], True
    elif action == 'restore':
        args.skill, args.xp, args.apply = 'off', 1, True
        args.cancel_currency = True
    elif action == 'xp_off':
        args.xp, args.apply = 1, True
    elif action in ('cash', 'eridium', 'xp'):
        try:
            value = int(amount)
        except ValueError:
            raise ValueError('请输入整数。') from None
        maximum = 100 if action == 'xp' else 1000000
        if not 1 <= value <= maximum:
            raise ValueError(f'请输入 1～{maximum} 之间的整数。')
        setattr(args, 'xp' if action == 'xp' else action + '_add', value)
        args.apply = True
    elif action == 'backpack':
        try:
            value = int(amount)
        except ValueError:
            raise ValueError('请输入整数，例如 10。') from None
        if not 1 <= value <= 1000:
            raise ValueError('增加数量应在 1～1000 之间，最终容量不得超过 1000。')
        args.backpack_add, args.apply = value, True
    else:
        raise ValueError('未知操作')
    return args


def readable_error(report):
    if report.get('resume_errors') or report.get('protection_errors') or any(
            not row.get('restored', False) for row in report.get('rollback', [])):
        return '恢复未完成，请先停止操作并查看“设置与记录”中的详细结果。'
    error = report.get('error', report.get('journal_error', '未知错误'))
    if 'found 0' in error:
        return '未检测到游戏。请启动无主之地 4，进入离线单人角色后再连接。'
    if 'Unsupported executable' in error:
        return '游戏版本不匹配，已停止操作。需要更新适配后才能使用。'
    if 'WinError 5' in error:
        return '访问被拒绝。请检查修改器和游戏的运行权限是否一致。'
    if 'inside patch range' in error:
        return '游戏正在执行相关代码，本次未修改。稍后重试即可。'
    if 'Expected one local controller' in error:
        return '尚未找到单人角色。请进入角色场景后重试。'
    if 'A currency bonus is already pending' in error:
        return '该货币已有待发放加成，尚未触发，不能重复添加。当前货币功能效果待修复。'
    if 'No such file' in error or 'WinError 2' in error:
        return '找不到游戏或配置文件。请在“设置与记录”中检查路径。'
    return error


class TrainerApp:
    def __init__(self, root, runner=run_request, auto_connect=True):
        self.root, self.runner = root, runner
        self.busy, self.events, self.controls = False, queue.Queue(), []
        # Layout dimensions are physical pixels; keep point fonts consistent
        # when Windows is configured for 150% or 200% display scaling.
        root.tk.call('tk', 'scaling', 96 / 72)
        root.title('无主之地 4 修改器')
        root.geometry('980x880')
        root.minsize(960, 850)
        root.configure(bg=BG)
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.option_add('*Font', ('Microsoft YaHei UI', 10))
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('TFrame', background=BG)
        style.configure('TNotebook', background=BG, borderwidth=0)
        style.configure('TNotebook.Tab', padding=(22, 10), background=CARD, foreground=MUTED)
        style.map('TNotebook.Tab', background=[('selected', '#2b3b50')], foreground=[('selected', TEXT)])
        style.configure('TProgressbar', troughcolor=BG, background=ACCENT, borderwidth=0)
        header = tk.Frame(root, bg=BG)
        header.pack(fill='x', padx=28, pady=(24, 16))
        tk.Label(header, text='BORDERLANDS 4', fg=ACCENT, bg=BG,
                 font=('Segoe UI', 11, 'bold')).pack(anchor='w')
        tk.Label(header, text='无主之地 4 修改器', fg=TEXT, bg=BG,
                 font=('Microsoft YaHei UI', 25, 'bold')).pack(anchor='w', pady=(4, 5))
        tk.Label(header, text='离线单人  /  桌面实验版', fg=MUTED, bg=BG).pack(anchor='w')
        self.tabs = ttk.Notebook(root)
        self.tabs.pack(fill='both', expand=True, padx=28)
        main = tk.Frame(self.tabs, bg=BG)
        settings = tk.Frame(self.tabs, bg=BG)
        self.tabs.add(main, text='功能')
        self.tabs.add(settings, text='设置与记录')
        connection = tk.Frame(main, bg=BG)
        connection.pack(fill='x', pady=16)
        self.connection = tk.StringVar(value='尚未连接游戏')
        tk.Label(connection, textvariable=self.connection, bg=BG, fg=TEXT,
                 font=('Microsoft YaHei UI', 12, 'bold')).pack(side='left')
        self.button(connection, '连接 / 刷新状态', lambda: self.start('status')).pack(side='right')
        grid = tk.Frame(main, bg=BG)
        grid.pack(fill='both', expand=True)
        for col in (0, 1):
            grid.columnconfigure(col, weight=1, uniform='cards')
        for row in (0, 1):
            grid.rowconfigure(row, weight=1, uniform='cards')
        cash = self.card(grid, 0, 0, '01  钱与镒矿', '实验功能')
        self.label(cash, '下一次正常获得对应货币时，额外追加一次。').pack(anchor='w', pady=(8, 6))
        self.cash_amount, self.eridium_amount = tk.StringVar(value='100'), tk.StringVar(value='100')
        for action, label, variable in [('cash', '加现金', self.cash_amount), ('eridium', '加镒矿', self.eridium_amount)]:
            row = tk.Frame(cash, bg=CARD)
            row.pack(anchor='w', pady=3)
            entry = tk.Entry(row, textvariable=variable, width=10, bg=BG, fg=TEXT, insertbackground=TEXT, relief='flat')
            entry.pack(side='left', ipady=7, padx=(0, 10))
            self.controls.append(entry)
            self.button(row, label, lambda a=action: self.start(a), primary=True).pack(side='left')
        self.currency = tk.StringVar(value='待发放状态：未读取')
        tk.Label(cash, textvariable=self.currency, fg=MUTED, bg=CARD, wraplength=390,
                 justify='left').pack(anchor='w', pady=(6, 0))
        skill = self.card(grid, 0, 1, '02  技能点不消耗', '实验功能')
        self.skill = tk.StringVar(value='状态：未读取')
        tk.Label(skill, textvariable=self.skill, bg=CARD, fg=TEXT).pack(anchor='w', pady=(8, 3))
        self.label(skill, '需要已有可用技能点，0 点不能购买技能。').pack(anchor='w')
        buttons = tk.Frame(skill, bg=CARD)
        buttons.pack(anchor='w', pady=(12, 0))
        self.button(buttons, '开启', lambda: self.start('skill_on'), primary=True).pack(side='left', padx=(0, 8))
        self.button(buttons, '关闭', lambda: self.start('skill_off')).pack(side='left')
        xp = self.card(grid, 1, 0, '03  杀敌经验倍率', '实验功能')
        self.label(xp, '选择或输入 1～100 的整数倍率，再点击应用。\n用于后续杀敌经验；游戏效果待确认。').pack(anchor='w', pady=12)
        xp_row = tk.Frame(xp, bg=CARD)
        xp_row.pack(anchor='w')
        self.xp_amount = tk.StringVar(value='2')
        combo = ttk.Combobox(xp_row, textvariable=self.xp_amount, values=(1, 2, 3, 4, 5, 10, 20, 50, 100), width=7)
        combo.pack(side='left', padx=(0, 12), ipady=5)
        self.controls.append(combo)
        self.button(xp_row, '应用倍率', lambda: self.start('xp'), primary=True).pack(side='left')
        self.button(xp_row, '恢复 1 倍', lambda: self.start('xp_off')).pack(side='left', padx=(8, 0))
        self.xp_status = tk.StringVar(value='当前倍率：未读取')
        tk.Label(xp, textvariable=self.xp_status, fg=MUTED, bg=CARD).pack(anchor='w', pady=(12, 0))
        backpack = self.card(grid, 1, 1, '04  背包容量', '可调整')
        self.label(backpack, '输入增加数量；操作前自动备份存档。').pack(anchor='w', pady=(10, 8))
        amount_row = tk.Frame(backpack, bg=CARD)
        amount_row.pack(anchor='w')
        self.amount = tk.StringVar(value='10')
        entry = tk.Entry(amount_row, textvariable=self.amount, width=8, bg=BG, fg=TEXT,
                         insertbackground=TEXT, relief='flat', font=('Segoe UI', 14))
        entry.pack(side='left', ipady=5, padx=(0, 12))
        self.controls.append(entry)
        self.button(amount_row, '增加容量', lambda: self.start('backpack'), primary=True).pack(side='left')
        self.backpack = tk.StringVar(value='每次点击都会再次增加容量。')
        tk.Label(backpack, textvariable=self.backpack, bg=CARD, fg=MUTED).pack(anchor='w', pady=(8, 0))
        footer = tk.Frame(main, bg=BG)
        grid.pack_forget()
        footer.pack(side='bottom', fill='x', pady=14)
        grid.pack(fill='both', expand=True)
        self.button(footer, '恢复补丁 / 取消待发放', lambda: self.start('restore')).pack(side='left')
        tk.Label(footer, text='关闭窗口不会自动撤销已应用的修改。', fg=MUTED, bg=BG).pack(side='right')
        self.build_settings(settings)
        # Reserve the status area before allocating remaining height to tabs.
        self.tabs.pack_forget()
        self.progress = ttk.Progressbar(root, mode='indeterminate')
        self.message = tk.StringVar(value='准备就绪。先进入游戏，再连接角色。')
        tk.Label(root, textvariable=self.message, bg=BG, fg=MUTED, anchor='w',
                 justify='left', wraplength=880).pack(side='bottom', fill='x', padx=28, pady=(8, 18))
        self.progress.pack(side='bottom', fill='x', padx=28, pady=(12, 0))
        self.tabs.pack(fill='both', expand=True, padx=28)
        self.root.after(100, self.poll)
        if auto_connect:
            self.root.after(300, lambda: self.start('status'))

    def label(self, parent, text):
        return tk.Label(parent, text=text, fg=MUTED, bg=CARD, justify='left', wraplength=360)

    def card(self, parent, row, col, title, badge):
        frame = tk.Frame(parent, bg=CARD, padx=18, pady=14)
        frame.grid(row=row, column=col, sticky='nsew', padx=(0, 10) if col == 0 else (0, 0), pady=(0, 10))
        heading = tk.Frame(frame, bg=CARD)
        heading.pack(fill='x')
        tk.Label(heading, text=title, fg=TEXT, bg=CARD,
                 font=('Microsoft YaHei UI', 13, 'bold')).pack(side='left')
        tk.Label(heading, text=badge, fg=ACCENT, bg=CARD, font=('Microsoft YaHei UI', 9)).pack(side='right')
        return frame

    def button(self, parent, text, command, primary=False, enabled=True):
        button = tk.Button(parent, text=text, command=command, relief='flat', bd=0,
                           padx=16, pady=7, cursor='hand2' if enabled else 'arrow',
                           bg=ACCENT if primary else '#304156', fg=BG if primary else TEXT,
                           activebackground='#ffd470' if primary else '#415972',
                           activeforeground=BG if primary else TEXT, disabledforeground='#6c7c90')
        if enabled:
            self.controls.append(button)
        else:
            button.configure(state='disabled')
        return button

    def build_settings(self, parent):
        self.config_path = OUTPUT / 'trainer_config.json'
        try:
            path = self.config_path if self.config_path.exists() else ROOT / 'trainer_config.json'
            self.config = json.loads(path.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError):
            self.config = {}
        self.paths = {}
        for key, title in [('expected_executable', '游戏主程序（OakGame / Binaries / Win64 中的 EXE）'), ('save_root', '存档目录（SaveGames）')]:
            tk.Label(parent, text=title, fg=TEXT, bg=BG).pack(anchor='w', pady=(16, 6))
            row = tk.Frame(parent, bg=BG)
            row.pack(fill='x')
            value = tk.StringVar(value=self.config.get(key, ''))
            self.paths[key] = value
            entry = tk.Entry(row, textvariable=value, bg=CARD, fg=TEXT, insertbackground=TEXT, relief='flat')
            entry.pack(side='left', fill='x', expand=True, ipady=9, padx=(0, 8))
            self.controls.append(entry)
            self.button(row, '浏览', lambda k=key: self.browse(k)).pack(side='right')
        row = tk.Frame(parent, bg=BG)
        row.pack(fill='x', pady=16)
        self.button(row, '保存路径', self.save_settings, primary=True).pack(side='left')
        self.button(row, '打开日志文件夹', self.open_logs).pack(side='left', padx=10)
        tk.Label(parent, text='最近一次操作的详细结果', fg=MUTED, bg=BG).pack(anchor='w', pady=(0, 8))
        self.details = tk.Text(parent, height=10, bg=CARD, fg=TEXT, relief='flat', padx=12, pady=12,
                               wrap='word', font=('Consolas', 10), state='disabled')
        self.details.pack(fill='both', expand=True, pady=(0, 10))

    def browse(self, key):
        value = filedialog.askopenfilename(filetypes=[('游戏程序', '*.exe')]) if key == 'expected_executable' else filedialog.askdirectory()
        if value:
            self.paths[key].set(value)

    def save_settings(self):
        if self.busy:
            return
        values = {key: value.get().strip() for key, value in self.paths.items()}
        if not Path(values['expected_executable']).is_file() or not Path(values['save_root']).is_dir():
            self.message.set('路径未保存：请选择存在的游戏 EXE 和存档目录。')
            return
        try:
            updated = {**self.config, **values}
            temporary = self.config_path.with_suffix('.json.tmp')
            temporary.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(self.config_path)
            self.config = updated
            self.connection.set('路径已更新，请重新连接')
            self.skill.set('状态：未读取')
            self.message.set('路径已保存。回到“功能”页连接游戏。')
        except OSError as error:
            self.message.set(f'保存失败：{error}')

    def open_logs(self):
        try:
            folder = OUTPUT / 'logs'
            folder.mkdir(exist_ok=True)
            os.startfile(folder)
        except OSError as error:
            self.message.set(f'无法打开日志：{error}')

    def start(self, action):
        if self.busy:
            return
        try:
            variable = {'cash': self.cash_amount, 'eridium': self.eridium_amount, 'xp': self.xp_amount}.get(action, self.amount)
            args = request_for(action, variable.get())
        except ValueError as error:
            self.message.set(str(error))
            return
        self.busy = True
        for control in self.controls:
            control.configure(state='disabled')
        self.progress.start(12)
        self.message.set('正在连接并校验游戏…' if action == 'status' else '正在处理，请稍候…')
        def work():
            try:
                result = self.runner(args)
            except Exception as error:
                result = dict(status='error', error=str(error))
            self.events.put((action, result))
        threading.Thread(target=work, daemon=False).start()

    def poll(self):
        try:
            action, report = self.events.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            self.progress.stop()
            for control in self.controls:
                control.configure(state='normal')
            self.details.configure(state='normal')
            self.details.delete('1.0', 'end')
            self.details.insert('end', json.dumps(report, ensure_ascii=False, indent=2))
            self.details.configure(state='disabled')
            if report.get('status') == 'success':
                self.connection.set(f'已连接游戏  ·  PID {report.get("process", {}).get("pid", "—")}')
                value = report.get('after', {}).get('skill_no_spend', {}).get('variant')
                self.skill.set('状态：' + {'on': '已开启', 'off': '已关闭'}.get(value, '未知，需检查'))
                reward = report.get('after', {})
                multiplier = reward.get('kill_xp_multiplier', {}).get('variant', '未知')
                self.xp_status.set(f'当前倍率：{multiplier} 倍')
                currency = reward.get('currency_bonus', {})
                self.currency.set(f'待发放：现金 {currency.get("cash_pending", 0)} / 镒矿 {currency.get("eridium_pending", 0)}')
                if 'backpack' in report:
                    item = report['backpack']
                    self.backpack.set(f'本次容量：{item["before"]} → {item["target"]}')
                if action in ('cash', 'eridium'):
                    self.message.set('已设置一次性加成，尚未到账。请正常获得对应货币，再刷新状态并确认游戏余额。')
                elif action in ('xp', 'xp_off'):
                    self.message.set('经验倍率已恢复为 1 倍。' if multiplier == 1 else
                                     f'已设置 ×{multiplier}。请击杀敌人核对经验增量；切换角色后请重新应用。')
                else:
                    self.message.set('状态已更新。' if action == 'status' else '操作完成。请在游戏中确认效果。')
                elapsed = report.get('timings', {}).get('total_seconds')
                if elapsed is not None:
                    self.message.set(self.message.get() + f'（耗时 {elapsed:.1f} 秒）')
            else:
                self.connection.set('未连接 / 本次操作未完成')
                self.skill.set('状态：需重新读取')
                self.xp_status.set('当前倍率：需重新读取')
                self.currency.set('待发放状态：需重新读取')
                self.message.set(readable_error(report))
        self.root.after(100, self.poll)

    def close(self):
        if self.busy:
            self.message.set('操作进行中，完成后即可关闭窗口。')
            return
        self.root.destroy()


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    root = tk.Tk()
    TrainerApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
