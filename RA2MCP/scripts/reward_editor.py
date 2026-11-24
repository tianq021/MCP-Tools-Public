"""Native editable reward table, independent of the training process."""
import json
import pathlib
import sys
import tkinter as tk
from tkinter import ttk, messagebox

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from dsh_rl.reward_config import CONFIG_PATH, SPECS, defaults, load, save, revision, validate

ROOT = pathlib.Path(__file__).resolve().parents[1]
FONT = 'Microsoft YaHei UI'


class RewardEditor:
    def __init__(self, root):
        self.root = root
        root.title('RA2 · 训练奖励设置')
        root.geometry('1120x700')
        root.minsize(1000, 620)
        root.configure(bg='#edf2f7')
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('.', font=(FONT, 9))
        style.configure('TFrame', background='#edf2f7')
        style.configure('TLabel', background='#edf2f7', foreground='#20334d')
        style.configure('TButton', padding=(9, 3))
        header = tk.Frame(root, bg='#172b45', padx=18, pady=7)
        header.pack(fill='x')
        tk.Label(header, text='训练奖励设置', bg='#172b45', fg='white',
                 font=(FONT, 17, 'bold')).pack(anchor='w')
        tk.Label(header, text='调整每项激励与限制 · 保存后从下一局生效 · 当前对局使用固定配置',
                 bg='#172b45', fg='#b8d3e9', font=(FONT, 9)).pack(anchor='w', pady=(2, 0))
        body = ttk.Frame(root, padding=8)
        body.pack(fill='both', expand=True)
        self.runtime = tk.StringVar(value='正在读取训练状态…')
        ttk.Label(body, textvariable=self.runtime, wraplength=1060).pack(anchor='w', pady=(0, 5))
        table = ttk.Frame(body)
        table.pack(fill='x')
        headings = ['启用', '奖励项目 / 单位', '分值（可负）', '每多少单位', '每步分数上限', '每局分数上限', '每局计分次数', '本局累计分数']
        for col, title in enumerate(headings):
            tk.Label(table, text=title, bg='#dbe7f2', fg='#20334d', padx=4, pady=4,
                     font=(FONT, 9, 'bold')).grid(row=0, column=col, sticky='nsew')
            table.columnconfigure(col, weight=1 if col > 1 else 0)
        self.fields, self.totals, self.enabled_labels = {}, {}, {}
        for row, (key, label, unit, *_rest) in enumerate(SPECS, 1):
            values = {'enabled': tk.BooleanVar()}
            shown = tk.StringVar(value='启用')

            def toggle(var=values['enabled'], text=shown):
                var.set(not var.get())
                text.set('启用' if var.get() else '停用')

            ttk.Button(table, textvariable=shown, width=5, command=toggle).grid(
                row=row, column=0, padx=6, pady=1)
            ttk.Label(table, text=f'{label} / {unit}').grid(row=row, column=1, sticky='w', padx=6)
            for col, name in enumerate(('score', 'unit', 'step_cap', 'episode_cap', 'max_events'), 2):
                values[name] = tk.StringVar()
                ttk.Entry(table, textvariable=values[name], width=9, justify='right').grid(
                    row=row, column=col, padx=4, pady=1, sticky='ew')
            self.totals[key] = tk.StringVar(value='—')
            ttk.Label(table, textvariable=self.totals[key], anchor='e').grid(
                row=row, column=7, sticky='ew', padx=8)
            self.fields[key], self.enabled_labels[key] = values, shown
        ttk.Label(body, text='计分 = 数量 ÷ 计分单位 × 分值；上限填正数限制绝对值，留空 = 不限制，0 = 不计分。'
                             '每局计分次数按产生非零分数的决策步计数。',
                  wraplength=1060, font=(FONT, 9)).pack(anchor='w', pady=(7, 2))
        ttk.Label(body, text='建筑、单位只奖励每局首次类型，探图只奖励新增格。侦察接近反馈可正可负；'
                             '资金净增加不等于总采矿收入；本局累计约每 16 步刷新。',
                  wraplength=1060, font=(FONT, 9)).pack(anchor='w')
        buttons = ttk.Frame(body)
        buttons.pack(fill='x', pady=(7, 0))
        ttk.Button(buttons, text='保存 · 下一局生效', command=self.save).pack(side='left')
        ttk.Button(buttons, text='重新读取', command=self.reload).pack(side='left', padx=6)
        ttk.Button(buttons, text='填入默认值', command=lambda: self.fill(defaults())).pack(side='left')
        self.notice = tk.StringVar()
        ttk.Label(body, textvariable=self.notice, wraplength=1060,
                  font=(FONT, 9)).pack(anchor='w', pady=(4, 0))
        self.reload()
        # Fit the table, buttons and hint lines without clipping, keeping the window compact.
        root.update_idletasks()
        root.geometry(f'1120x{max(620, min(root.winfo_reqheight() + 10, 1200))}')
        self.poll()

    def fill(self, data):
        for key, fields in self.fields.items():
            for name, variable in fields.items():
                value = data['rules'][key][name]
                variable.set(value if name == 'enabled' else '' if value is None else str(value))
            self.enabled_labels[key].set('启用' if data['rules'][key]['enabled'] else '停用')

    def reload(self):
        try:
            data = load()
            self.loaded_revision = revision(data)
            self.fill(data)
            self.notice.set('已读取配置 ' + self.loaded_revision[:12] + '；修改后点击保存。')
        except Exception as error:
            messagebox.showerror('读取失败', str(error), parent=self.root)

    def save(self):
        try:
            if revision(load()) != self.loaded_revision:
                raise ValueError('配置已被另一个窗口修改，请重新读取后编辑。')
            data = {'schema_version': 1, 'rules': {}}
            for key, fields in self.fields.items():
                rule = {'enabled': fields['enabled'].get()}
                for name in ('score', 'unit', 'step_cap', 'episode_cap', 'max_events'):
                    value = fields[name].get().strip()
                    rule[name] = None if value == '' else float(value)
                data['rules'][key] = rule
            validate(data)
            # Keep previous saved versions for comparison and recovery.
            history = CONFIG_PATH.parent / 'reward-history'
            history.mkdir(exist_ok=True)
            (history / (self.loaded_revision + '.json')).write_text(
                json.dumps(load(), ensure_ascii=False, indent=2), encoding='utf-8')
            self.loaded_revision = save(data)
            self.notice.set('保存成功 · 配置 ' + self.loaded_revision[:12] + ' 将从下一局生效；当前局不变。')
        except Exception as error:
            messagebox.showerror('未保存', str(error), parent=self.root)

    def poll(self):
        try:
            active = json.loads((ROOT / 'reports/rl/active-run.json').read_text(encoding='utf-8-sig'))
            run = pathlib.Path(active['run_dir'])
            report = json.loads((run / 'run.json').read_text(encoding='utf-8-sig'))
            progress = json.loads((run / 'progress.json').read_text(encoding='utf-8-sig'))
            effective_path = run / 'active-rewards.json'
            effective = json.loads(effective_path.read_text()) if effective_path.exists() else {}
            current = effective.get('revision', '')
            saved = revision(load())
            state = '当前局已使用已保存配置' if current == saved else '已保存配置等待下一局加载' if current else '旧版训练：重启训练后接入配置'
            self.runtime.set(f"{state}  |  累计 {progress['timesteps']} 步  |  状态 {report['status']}  |  当前局配置 {current[:12] or '—'}")
            parts = progress.get('episode_reward_parts', {}) if progress.get('reward_revision') == current else {}
            for key, label in self.totals.items():
                label.set(f'{parts[key]:+.4f}' if key in parts else '—')
        except (OSError, ValueError, KeyError):
            self.runtime.set('暂无可读取的训练进度；可以编辑配置，下次 Full 训练会加载。')
        self.root.after(3000, self.poll)


if __name__ == '__main__':
    window = tk.Tk()
    RewardEditor(window)
    window.mainloop()
