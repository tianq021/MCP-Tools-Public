"""Native visual editor for the real-game match settings (config/spawn.ini)."""
import os
import pathlib
import sys
import tkinter as tk
from tkinter import ttk, messagebox

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import match_config as M

FONT = ('Microsoft YaHei UI', 10)

STATUS_HELP = (
    '状态栏说明：\n'
    '· 当前对局：正在训练的 run 目录名 / 当前对局 ID\n'
    '· 对局使用配置：当前这局实际读取的 spawn.ini 哈希\n'
    '· 已保存：当前保存的 spawn.ini 哈希\n'
    '· 地图：当前对局地图和出生点人数\n'
    '· 玩家：模型当前使用的国家\n'
    '两处哈希相同 = 当前局已经在用保存的设置；不同 = 已保存的设置将在下一局生效。'
)
MAP_HELP = (
    '地图说明：\n'
    '当前对局使用的地图文件、出生点数量（=最多玩家数）和地图名。\n'
    'Twin Peaks 只有 2 个出生点，只能 1 人类 + 1 电脑；\n'
    '电脑数量选 2～3 时会自动换成 4_divide_and_conquer.map（4 个出生点）。\n'
    '换地图会改变模型看到的地形，可以继续训练但评估要分开看。'
)
TEAM_HELP = (
    '阵营 = 游戏里的队伍编号（1-4）。\n'
    '同一编号的玩家互相结盟，不同编号互为敌人。\n'
    '0/无 = 不结盟（各自为战）。\n'
    '写入 spawn.ini 的 [MultiN_Alliances] HouseAlly 字段（值 = 盟友 house 编号 - 1）。\n'
    '注意：如果把人类和所有电脑放进同一队，就没有敌人、对局不会结束。'
)
COUNTRY_HELP = (
    '国家决定两件事：\n'
    '1) 属于哪个大阵营（盟军 / 苏联 / 尤里）；\n'
    '2) 可生产的具体兵种和特色单位。\n'
    '国家编号（游戏规则 [Countries] 顺序）：\n'
    '0 美国(盟军)、1 亚洲联盟(盟军)、2 法国(盟军)、3 德国(盟军)、4 英国(盟军)、\n'
    '5 非洲帝国(苏联)、6 阿拉伯联盟(苏联)、7 拉美联盟(苏联)、8 俄罗斯(苏联)、9 尤里。\n'
    '电脑的国家可以随时改，下一局生效。\n'
    '玩家国家决定模型的生产目录（action-contract），改了旧模型不能续训，\n'
    '需要用 .\\train.cmd -Fresh 新开训练。'
)
SAVE_HELP = ('把当前窗口里的设置写入 config/spawn.ini 并同步 test-game/spawn.ini'
             '（电脑数量>1 时同时换地图）。\n保存后下一局生效，当前对局不受影响。')
RELOAD_HELP = '放弃未保存的修改，重新从 config/spawn.ini 读取。'
DEFAULT_HELP = ('把各项恢复成当前训练基线（电脑 1 个、普通难度、10000 资金、'
                '玩家蓝色、电脑红色等），填好后仍需点保存才生效。')


class Tooltip:
    """Hover tooltip: short delay, word wrap, clamped to the screen."""

    def __init__(self, widget, text, delay=350, wraplength=430):
        self.widget = widget
        self.text = text
        self.delay = delay
        self.wraplength = wraplength
        self.tip = None
        self.after_id = None
        widget.bind('<Enter>', self._schedule, add='+')
        widget.bind('<Leave>', self._hide, add='+')
        widget.bind('<ButtonPress>', self._hide, add='+')

    def _schedule(self, _event=None):
        self._hide()
        self.after_id = self.widget.after(self.delay, self.show)

    def show(self):
        self._hide()
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.attributes('-topmost', True)
        tk.Label(self.tip, text=self.text, justify='left', anchor='w',
                 background='#fffbe6', foreground='#1c2b3a', relief='solid', borderwidth=1,
                 wraplength=self.wraplength, font=('Microsoft YaHei UI', 9),
                 padx=10, pady=7).pack()
        self.tip.update_idletasks()
        width = self.tip.winfo_reqwidth()
        height = self.tip.winfo_reqheight()
        screen_w = self.tip.winfo_screenwidth()
        screen_h = self.tip.winfo_screenheight()
        x = min(x, screen_w - width - 8)
        if y + height > screen_h - 8:
            y = self.widget.winfo_rooty() - height - 8
        self.tip.wm_geometry(f'+{max(4, x)}+{max(4, y)}')

    def _hide(self, _event=None):
        if self.after_id:
            try:
                self.widget.after_cancel(self.after_id)
            except tk.TclError:
                pass
            self.after_id = None
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None


class MatchEditor:
    def __init__(self, root):
        self.root = root
        root.title('RA2 · 对局设置')
        root.geometry('1360x760')
        root.minsize(1200, 700)
        root.configure(bg='#edf2f7')
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('.', font=FONT)
        style.configure('TFrame', background='#edf2f7')
        style.configure('TLabel', background='#edf2f7', foreground='#20334d')
        style.configure('TLabelframe', background='#edf2f7')
        style.configure('TLabelframe.Label', background='#edf2f7', foreground='#172b45',
                        font=('Microsoft YaHei UI', 10, 'bold'))
        style.configure('TButton', padding=(10, 5))

        header = tk.Frame(root, bg='#172b45', padx=22, pady=10)
        header.pack(fill='x')
        tk.Label(header, text='对局设置', bg='#172b45', fg='white',
                 font=('Microsoft YaHei UI', 20, 'bold')).pack(anchor='w')
        tk.Label(header, text='电脑数量 / 难度 / 资金 / 颜色 / 规则 / 阵营（队伍）与国家 · 保存后从下一局生效',
                 bg='#172b45', fg='#b8d3e9', font=FONT).pack(anchor='w', pady=(2, 0))
        tk.Label(header, text='鼠标悬停任意选项可查看详细说明', bg='#172b45', fg='#7fa8cc',
                 font=('Microsoft YaHei UI', 9)).pack(anchor='w', pady=(2, 0))

        body = ttk.Frame(root, padding=10)
        body.pack(fill='both', expand=True)
        self.tooltips = []
        self.demo_tooltip = None
        self.runtime = tk.StringVar(value='正在读取当前对局…')
        status_label = ttk.Label(body, textvariable=self.runtime, wraplength=1300)
        status_label.pack(anchor='w', pady=(0, 8))
        self._tip(status_label, STATUS_HELP)

        columns = ttk.Frame(body)
        columns.pack(fill='both', expand=True)
        left = ttk.LabelFrame(columns, text='对局与电脑', padding=10)
        left.pack(side='left', fill='both', expand=True, padx=(0, 6))
        middle = ttk.LabelFrame(columns, text='规则开关', padding=10)
        middle.pack(side='left', fill='both', expand=True, padx=(6, 6))
        right = ttk.LabelFrame(columns, text='阵营（队伍）/ 国家', padding=10)
        right.pack(side='left', fill='both', expand=True, padx=(6, 0))

        self.widgets = {}
        row = 0
        for spec in self._visible_specs():
            if spec['kind'] == 'bool':
                continue
            label = ttk.Label(left, text=spec['label'])
            label.grid(row=row, column=0, sticky='w', pady=3)
            control = self._make_widget(left, spec)
            control.grid(row=row, column=1, sticky='w', padx=(8, 0), pady=3)
            help_text = spec.get('help') or ''
            self._tip(label, help_text)
            self._tip(control, help_text)
            if spec['label'] == '电脑难度':
                self.demo_tooltip = self.tooltips[-1]
            if spec.get('note'):
                note_label = ttk.Label(left, text=spec['note'], foreground='#5b6b7d',
                                       font=('Microsoft YaHei UI', 9))
                note_label.grid(row=row, column=2, sticky='w', padx=(8, 0))
                self._tip(note_label, help_text)
            row += 1
        summary = ttk.Label(left, text='颜色不影响训练；难度、资金、单位、规则会改变对局强度和时长，随时可改。',
                            wraplength=430, foreground='#2b4a6b',
                            font=('Microsoft YaHei UI', 9))
        summary.grid(row=row, column=0, columnspan=3, sticky='w', pady=(10, 0))
        self._tip(summary, '这些选项都不会改变模型的动作空间（action-contract），保存后下一局生效，\n'
                           '但会改变对局强度和训练数据的分布，改完后新旧分数不要直接横比。')
        row += 1
        self.map_info = tk.StringVar()
        map_label = ttk.Label(left, textvariable=self.map_info, wraplength=430, foreground='#8a5a00',
                              font=('Microsoft YaHei UI', 9, 'bold'))
        map_label.grid(row=row, column=0, columnspan=3, sticky='w', pady=(6, 0))
        self._tip(map_label, MAP_HELP)
        self.widgets[('Settings', 'AIPlayers')].trace_add(
            'write', lambda *_: (self._update_map_hint(), self._sync_ai_rows()))

        bool_specs = [spec for spec in self._visible_specs() if spec['kind'] == 'bool']
        for index, spec in enumerate(bool_specs):
            cell = ttk.Frame(middle)
            cell.grid(row=index // 2, column=index % 2, sticky='w', padx=(0, 14), pady=3)
            toggle = self._make_widget(cell, spec)
            toggle.pack(side='left')
            rule_label = ttk.Label(cell, text=spec['label'])
            rule_label.pack(side='left', padx=(6, 0))
            rule_help = f"{spec['label']}（{spec['keys'][0]}）\n{spec.get('help', '')}"
            for widget in (cell, toggle, rule_label):
                self._tip(widget, rule_help)
        ttk.Label(middle, text='提示：关闭基地模式会没有基地车，模型无法展开基地；打开快速结束会缩短对局；\n'
                               '战争迷雾、可摧毁桥梁会改变可见性与通行。',
                  wraplength=380, foreground='#8a5a00',
                  font=('Microsoft YaHei UI', 9)).grid(
            row=(len(bool_specs) + 1) // 2, column=0, columnspan=2, sticky='w', pady=(10, 0))

        right_intro = ttk.Label(right, text='阵营 = 游戏里的队伍编号（0 无 / 1-4），同一编号的玩家结盟；\n'
                                            '国家决定阵营归属（盟军 / 苏联 / 尤里）和可选兵种。',
                                justify='left', foreground='#2b4a6b',
                                font=('Microsoft YaHei UI', 9))
        right_intro.grid(row=0, column=0, columnspan=3, sticky='w', pady=(0, 6))
        self._tip(right_intro, '阵营（队伍）和国家是两个不同概念：\n'
                               '· 阵营数字 1-4 决定谁和谁结盟（同一数字结盟，0=各自为战）；\n'
                               '· 国家决定可生产兵种和属于哪个大阵营（盟军/苏联/尤里）。\n'
                               '鼠标悬停下面的「阵营」「国家」下拉框可看详细说明。')
        for col, title in enumerate(('席位', '阵营', '国家')):
            header_label = ttk.Label(right, text=title, font=(FONT[0], 10, 'bold'))
            header_label.grid(row=1, column=col, sticky='w', padx=(0, 10), pady=(0, 2))
            self._tip(header_label, TEAM_HELP if title == '阵营' else COUNTRY_HELP)
        team_values = ['无'] + [str(option[0]) for option in M.TEAM_OPTIONS if option[0] > 0]
        country_values = [label for _value, label in M.COUNTRY_LABELS]
        self.match_rows = []
        for slot, name in enumerate(('玩家', '电脑 1', '电脑 2', '电脑 3')):
            cell = ttk.Frame(right)
            cell.grid(row=2 + slot, column=0, columnspan=3, sticky='w', pady=2)
            name_label = ttk.Label(cell, text=name, width=6)
            name_label.pack(side='left')
            team = tk.StringVar(value='无')
            team_box = ttk.Combobox(cell, textvariable=team, values=team_values, state='readonly',
                                    width=4)
            team_box.pack(side='left', padx=(4, 8))
            country = tk.StringVar(value=country_values[0])
            country_box = ttk.Combobox(cell, textvariable=country, values=country_values,
                                       state='readonly', width=25)
            country_box.pack(side='left')
            for widget in (name_label, team_box):
                self._tip(widget, f'{name} - {TEAM_HELP}')
            self._tip(country_box, f'{name} - {COUNTRY_HELP}')
            self.match_rows.append({'frame': cell, 'team': team, 'country': country})
        right_footer = ttk.Label(right, text='只改电脑阵营/国家随时生效；改「玩家」国家会换生产目录，\n'
                                             '现有模型不能续训，需要新开训练（保存时会提示）。',
                                 justify='left', foreground='#8a5a00',
                                 font=('Microsoft YaHei UI', 9))
        right_footer.grid(row=6, column=0, columnspan=3, sticky='w', pady=(8, 0))
        self._tip(right_footer, '电脑的阵营/国家只影响电脑的兵种和结盟，不改模型动作空间，随时可改。\n'
                                '玩家国家决定模型能造什么（生产目录），改了之后 action-contract 变化，\n'
                                '旧模型续训会直接报错，必须用 .\\train.cmd -Fresh 从零训练。')

        locked = ttk.LabelFrame(body, text='只读：改动会换动作契约 / 环境，需要重新训练', padding=8)
        locked.pack(fill='x', pady=(8, 0))
        self.locked_labels = {}
        for index, spec in enumerate(M.LOCKED):
            label = tk.StringVar()
            name_label = ttk.Label(locked, text=f"{spec['label']}：", font=(FONT[0], 10, 'bold'))
            name_label.grid(row=index, column=0, sticky='w', padx=(0, 2), pady=1)
            value_label = ttk.Label(locked, textvariable=label)
            value_label.grid(row=index, column=1, sticky='w', pady=1)
            reason_label = ttk.Label(locked, text=spec['reason'], foreground='#8a5a00',
                                     font=('Microsoft YaHei UI', 9))
            reason_label.grid(row=index, column=2, sticky='w', padx=(12, 0))
            locked_help = (f"{spec['label']} = {', '.join(spec['keys'])}\n{spec['reason']}\n"
                           '这个值会改变模型可生产的兵种/动作目录，改完旧模型无法续训，\n'
                           '必须新开训练。所以这里只显示当前值。')
            for widget in (name_label, value_label, reason_label):
                self._tip(widget, locked_help)
            self.locked_labels[spec['label']] = label

        note = ('保存会写 config/spawn.ini + test-game/spawn.ini（电脑数量>1 时同时换地图 spawnmap.ini）；'
                '当前对局不受影响，训练每开新局重新读取。改「玩家国家」后旧模型不能续训，'
                '需要用 .\\train.cmd -Fresh 新开训练。')
        note_label = ttk.Label(body, text=note, wraplength=1300, foreground='#2b4a6b')
        note_label.pack(anchor='w', pady=(8, 0))
        self._tip(note_label, '保存流程说明：\n'
                              '1) 校验窗口里的值（范围/枚举/地图出生点是否够）；\n'
                              '2) 旧配置复制到 config/spawn-history/ 留档；\n'
                              '3) 写入 config/spawn.ini 并同步 test-game/spawn.ini；\n'
                              '4) 电脑数量>1 时同时复制地图到 test-game/spawnmap.ini。\n'
                              '保存只影响下一局，当前正在跑的对局不变。')
        self.notice = tk.StringVar()
        notice_label = ttk.Label(body, textvariable=self.notice, wraplength=1300)
        notice_label.pack(anchor='w', pady=(6, 0))
        self._tip(notice_label, '最近一次保存/读取的结果和配置哈希。')
        buttons = ttk.Frame(body)
        buttons.pack(fill='x', pady=(8, 0))
        save_button = ttk.Button(buttons, text='保存 · 下一局生效', command=self.save)
        save_button.pack(side='left')
        reload_button = ttk.Button(buttons, text='重新读取', command=self.reload)
        reload_button.pack(side='left', padx=8)
        default_button = ttk.Button(buttons, text='填入基线默认值',
                                    command=lambda: self.fill(M.BASELINE))
        default_button.pack(side='left')
        self._tip(save_button, SAVE_HELP)
        self._tip(reload_button, RELOAD_HELP)
        self._tip(default_button, DEFAULT_HELP)

        self.reload()
        root.update_idletasks()
        root.geometry(f'1360x{max(760, min(root.winfo_reqheight() + 34, 1320))}')
        self.poll()

    def _visible_specs(self):
        return [spec for spec in M.SPECS if not spec.get('hidden')]

    def _tip(self, widget, text):
        if not text:
            return None
        tooltip = Tooltip(widget, text)
        self.tooltips.append(tooltip)
        return tooltip

    def _update_map_hint(self):
        try:
            ai_count = int(self.widgets[('Settings', 'AIPlayers')].get().split(' ', 1)[0])
        except (ValueError, KeyError, AttributeError):
            return
        info = M.current_map()
        hint = f"当前地图：{info['file']}（{info['players']} 人，{info['name']}）"
        plan = M.MAP_BY_AI.get(ai_count)
        if plan and plan != info['file']:
            try:
                target = M.read_map_info(M.map_path(plan))
                hint += f" → 保存后换到 {target['file']}（{target['players']} 人）"
            except OSError:
                hint += f" → 保存后换到 {plan}（文件缺失）"
        self.map_info.set(hint)
        self._sync_ai_rows()

    def _sync_ai_rows(self):
        try:
            ai_count = int(self.widgets[('Settings', 'AIPlayers')].get().split(' ', 1)[0])
        except (ValueError, KeyError, AttributeError):
            return
        for index in range(1, 4):
            row = self.match_rows[index]
            if index <= ai_count:
                row['frame'].grid()
            else:
                row['frame'].grid_remove()

    @staticmethod
    def _team_value(text):
        return 0 if text == '无' else int(text)

    @staticmethod
    def _country_value(text):
        return int(str(text).split(' ', 1)[0])

    def _fill_match(self, state):
        labels = dict(M.COUNTRY_LABELS)
        self.match_state = state
        self.match_rows[0]['team'].set('无' if state['teams']['player'] == 0
                                      else str(state['teams']['player']))
        self.match_rows[0]['country'].set(labels.get(state['player_country'],
                                                     str(state['player_country'])))
        for index in range(3):
            team = state['teams']['ai'][index] if index < len(state['teams']['ai']) else 0
            country = state['ai_countries'][index] if index < len(state['ai_countries']) else 0
            self.match_rows[index + 1]['team'].set('无' if team == 0 else str(team))
            self.match_rows[index + 1]['country'].set(labels.get(country, str(country)))
        self._sync_ai_rows()

    def collect_match(self):
        return {
            'player_country': self._country_value(self.match_rows[0]['country'].get()),
            'ai_countries': [self._country_value(row['country'].get())
                             for row in self.match_rows[1:]],
            'teams': {'player': self._team_value(self.match_rows[0]['team'].get()),
                      'ai': [self._team_value(row['team'].get())
                             for row in self.match_rows[1:]]},
        }

    def _make_widget(self, parent, spec):
        if spec['kind'] == 'bool':
            variable = tk.StringVar(value='关')

            def toggle(var=variable):
                var.set('关' if var.get() == '开' else '开')

            widget = ttk.Button(parent, textvariable=variable, width=4, command=toggle)
            self.widgets[(spec['section'], spec['keys'][0])] = variable
            return widget
        if spec['kind'] == 'enum':
            values = [f'{option} {name}' for option, name in spec['options']]
            variable = tk.StringVar(value=values[0])
            width = min(28, max(10, max(len(value) for value in values) + 2))
            widget = ttk.Combobox(parent, textvariable=variable, values=values,
                                  state='readonly', width=width)
            self.widgets[(spec['section'], spec['keys'][0])] = variable
            return widget
        variable = tk.StringVar()
        widget = ttk.Entry(parent, textvariable=variable, width=13)
        self.widgets[(spec['section'], spec['keys'][0])] = variable
        return widget

    def fill(self, values):
        for spec in self._visible_specs():
            variable = self.widgets[(spec['section'], spec['keys'][0])]
            value = values.get((spec['section'], spec['keys'][0]))
            if value is None:
                continue
            if spec['kind'] == 'bool':
                variable.set('开' if M._normalize_bool(value) == 'Yes' else '关')
            elif spec['kind'] == 'enum':
                name = dict(spec['options']).get(int(value), str(value))
                variable.set(f'{int(value)} {name}')
            else:
                variable.set(str(value))

    def reload(self):
        try:
            self.values = M.load()
            self.fill(self.values)
            for spec in M.LOCKED:
                self.locked_labels[spec['label']].set(
                    ' / '.join(f"{key}={self.values.get((spec['section'], key), '—')}"
                               for key in spec['keys']))
            self._update_map_hint()
            self._fill_match(M.load_match_state())
            self.notice.set('已读取已保存设置；修改后点保存，下一局生效。')
        except Exception as error:  # noqa: BLE001 - show the failure in the window
            messagebox.showerror('读取失败', str(error), parent=self.root)

    def collect(self):
        updates = {}
        for spec in self._visible_specs():
            variable = self.widgets[(spec['section'], spec['keys'][0])].get()
            if spec['kind'] == 'bool':
                value = 'Yes' if variable == '开' else 'No'
            elif spec['kind'] == 'enum':
                value = variable.split(' ', 1)[0]
            else:
                value = variable.strip()
            for key in spec['keys']:
                updates[(spec['section'], key)] = value
        return updates

    def save(self):
        try:
            match_state = self.collect_match()
            old_country = self.match_state['player_country']
            if match_state['player_country'] != old_country:
                labels = dict(M.COUNTRY_LABELS)
                if not messagebox.askyesno(
                        '玩家国家已改变',
                        f"玩家国家：{labels.get(old_country, old_country)} → "
                        f"{labels.get(match_state['player_country'], match_state['player_country'])}\n\n"
                        '换国家会换生产目录（action-contract 变化），现有模型不能续训。\n'
                        '保存后需要用 .\\train.cmd -Fresh 新开训练（旧模型作废）。仍要保存吗？',
                        parent=self.root):
                    return
            new_revision, old_revision = M.save(self.collect(), match=match_state)
            self.values = M.load()
            info = M.current_map()
            self._update_map_hint()
            self._fill_match(M.load_match_state())
            ai_count = str(self.values.get(('Settings', 'AIPlayers'), '1'))
            extra = f"（地图 {info['file']}，{info['players']} 人）" if ai_count != '1' else ''
            self.notice.set(f'已保存 · {old_revision[:8]} → {new_revision[:8]}{extra}；'
                            '将从下一局生效，当前局不变。')
            self.poll()
        except Exception as error:  # noqa: BLE001 - show validation errors in the window
            messagebox.showerror('未保存', str(error), parent=self.root)

    def poll(self):
        try:
            status = M.active_status()
            run = pathlib.Path(status['run_dir']).name if status['run_dir'] else '无'
            episode = (status['episode'] or '—')
            used = (status['episode_spawn'] or '—')[:8]
            info = status.get('map') or {}
            state = M.load_match_state()
            country = dict(M.COUNTRY_LABELS).get(state['player_country'], '?')
            self.runtime.set(f"当前对局：{run} / {episode} · 对局使用配置 {used} · "
                             f"已保存 {status['saved'][:8]} · 地图 {info.get('file', '?')}"
                             f"（{info.get('players', '?')} 人） · 玩家 {country} · {status['message']}")
        except Exception:  # noqa: BLE001 - status is best-effort
            self.runtime.set('暂无可读取的训练进度；设置保存后仍会在下次训练开局时加载。')
        self.root.after(3000, self.poll)


if __name__ == '__main__':
    window = tk.Tk()
    editor = MatchEditor(window)
    if os.environ.get('RA2_MATCH_TOOLTIP_DEMO') and editor.demo_tooltip is not None:
        window.after(1800, editor.demo_tooltip.show)
    window.mainloop()
