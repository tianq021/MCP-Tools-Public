"""Standalone Chinese controls, sharing the MCP runtime client."""
import json
import queue
import threading
import tkinter as tk
from tkinter import ttk
from runtime_client import request, item_definition


def add_runtime_tab(notebook):
    tab = ttk.Frame(notebook, padding=12)
    notebook.add(tab, text='游戏内工具')
    ttk.Label(tab, text='进入地图后查询；修改需在游戏 MODS 设置开启“允许运行时修改”。', wraplength=540).pack(anchor='w')
    identity = {}
    mailbox = queue.Queue()
    buttons = []
    busy = False
    output = tk.Text(tab, height=10, wrap='word')
    def start(work):
        nonlocal busy
        if busy:
            return
        busy = True
        for button in buttons:
            button.configure(state='disabled')
        def run():
            try:
                mailbox.put(work())
            except Exception as exc:
                mailbox.put({'error': str(exc)})
        threading.Thread(target=run, daemon=True).start()
    def poll():
        nonlocal busy
        try:
            result = mailbox.get_nowait()
        except queue.Empty:
            pass
        else:
            busy = False
            data = result.get('result', {})
            if result.get('status') == 'unknown':
                identity.clear()
            if 'identity' in data:
                identity.clear()
                identity.update(data['identity'])
                skills['values'] = [s['path'] for s in data['skills']]
            output.delete('1.0', 'end')
            output.insert('1.0', json.dumps(result, ensure_ascii=False, indent=2))
            for button in buttons:
                button.configure(state='normal')
        tab.after(100, poll)
    def button(parent, text, work):
        widget = ttk.Button(parent, text=text, command=lambda: start(work))
        widget.pack(side='left', padx=3)
        buttons.append(widget)
    def act(op, args):
        if not identity:
            raise ValueError('请先查询当前角色')
        return request(op, args, dict(identity))
    row = ttk.Frame(tab); row.pack(fill='x', pady=6)
    button(row, '查询角色与技能', lambda: request())
    points = tk.StringVar(value='0')
    ttk.Entry(row, textvariable=points, width=7).pack(side='left')
    button(row, '设置剩余技能点', lambda: act('skill_points', {'value': int(points.get())}))
    row = ttk.Frame(tab); row.pack(fill='x', pady=6)
    skills = ttk.Combobox(row, state='readonly', width=37); skills.pack(side='left')
    grade = tk.StringVar(value='1')
    ttk.Entry(row, textvariable=grade, width=4).pack(side='left')
    button(row, '设置技能等级', lambda: act('skill_set', {'path': skills.get(), 'grade': int(grade.get())}))
    ttk.Label(tab, text='装备代码 BL2(...)（每次一件标准装备）').pack(anchor='w')
    row = ttk.Frame(tab); row.pack(fill='x', pady=6)
    code = tk.StringVar(); level = tk.StringVar(value='1')
    ttk.Entry(row, textvariable=code, width=38).pack(side='left')
    ttk.Entry(row, textvariable=level, width=4).pack(side='left')
    button(row, '发到背包', lambda: act('give_item', item_definition(code.get().strip(), int(level.get()))))
    row = ttk.Frame(tab); row.pack(fill='x', pady=6)
    ttk.Label(row, text='数值膨胀：每级增长 %').pack(side='left')
    percent = tk.StringVar(value='13')
    ttk.Spinbox(row, from_=0, to=13, textvariable=percent, width=5).pack(side='left')
    button(row, '应用曲线', lambda: act('scaler', {'percent': int(percent.get())}))
    button(row, '恢复曲线', lambda: act('scaler_restore', {}))
    ttk.Label(tab, text='原版13表示每级+13%；调低可抑制等级数值膨胀，例如8表示每级+8%。\n先在游戏“设置选项 → MODS → 本模组”开启“允许运行时修改”。\n应用影响全部等级及玩家、敌人、装备；已有对象可能需要重载。效果仍待实测。', wraplength=540).pack(anchor='w')
    output.pack(fill='both', expand=True, pady=6)
    poll()


if __name__ == '__main__':
    root = tk.Tk()
    root.title('无主之地2 · 游戏内工具')
    root.geometry('760x600')
    notebook = ttk.Notebook(root)
    notebook.pack(fill='both', expand=True)
    add_runtime_tab(notebook)
    root.mainloop()
