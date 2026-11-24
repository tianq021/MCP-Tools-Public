#!/usr/bin/env python3
"""One-click Borderlands 3 offline trainer GUI."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import memory_scan
import runtime_features
from movement_hotkeys import MovementHotkeys
from loot_weights import parse_scale


APP_NAME = "BL3 简易修改器"
APP_DATA = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "BL3Trainer"
LOG_DIR = APP_DATA / "logs"

VALUE_ROWS = (
    ("money", "现金", "999999"),
    ("eridium", "镒矿", "9999"),
    ("xp", "经验值", "1000000"),
    ("skill_points", "技能点", "50"),
    ("golden_keys", "金钥匙", "99"),
)

PATCH_ROWS = (
    ("infinite_ammo", "无限弹药"),
    ("keep_skill_points", "技能点不减少"),
)


def application_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def load_config() -> dict:
    defaults = {
        "process_name": "Borderlands3.exe",
        "expected_executable": "",
    }
    path = application_directory() / "trainer_config.json"
    if path.exists():
        defaults.update(json.loads(path.read_text(encoding="utf-8")))
    return defaults


CONFIG = load_config()


class TrainerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("720x850")
        self.root.minsize(680, 800)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.pid: int | None = None
        self.worker_running = False
        self.status_var = tk.StringVar(value="正在等待 Borderlands3.exe……")
        self.value_vars = {
            key: tk.StringVar(value=default) for key, _, default in VALUE_ROWS
        }
        self.result_vars = {
            key: tk.StringVar(value="等待操作") for key, _, _ in VALUE_ROWS
        }
        self.patch_vars = {
            key: tk.BooleanVar(value=False) for key, _ in PATCH_ROWS
        }
        self.patch_status_vars = {
            key: tk.StringVar(value="关闭") for key, _ in PATCH_ROWS
        }
        self.action_widgets: list[ttk.Widget] = []
        self.movement_status = tk.StringVar(value='原速（未启用）')
        self.hotkeys = MovementHotkeys()
        self.pending_speed = None
        self.loot_status = tk.StringVar(value='尚未读取；请进入游戏场景后点击“读取当前值”')
        self.loot_current = {key:tk.StringVar(value='未读取') for key in ('VeryRare','Legendary')}
        self.loot_inputs = {'VeryRare':tk.StringVar(value='5'),'Legendary':tk.StringVar(value='10')}

        self.features = runtime_features.FeatureManager(
            str(CONFIG.get("expected_executable", "")), self._post_status
        )

        self._build_ui()
        self.root.after(300, self._poll_process)
        self.root.after(100, self._poll_hotkeys)

    def _build_ui(self) -> None:
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")

        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)

        heading = ttk.Frame(outer)
        heading.pack(fill="x")
        ttk.Label(
            heading,
            text="Borderlands 3 简易修改器",
            font=("Microsoft YaHei UI", 13, "bold"),
        ).pack(side="left")
        ttk.Button(heading, text="使用说明", command=self._show_help).pack(side="right")

        connection = ttk.LabelFrame(outer, text="连接状态", padding=8)
        connection.pack(fill="x", pady=(10, 8))
        ttk.Label(connection, textvariable=self.status_var, wraplength=600).pack(anchor="w")
        self.progress = ttk.Progressbar(connection, mode="indeterminate")
        self.progress.pack(fill="x", pady=(7, 0))

        tabs = ttk.Notebook(outer)
        tabs.pack(fill='both', expand=True)
        common = ttk.Frame(tabs, padding=4)
        loot_page = ttk.Frame(tabs, padding=12)
        tabs.add(common, text='常用功能 / 移动速度')
        tabs.add(loot_page, text='高级武器掉落')
        self._build_loot_ui(loot_page)

        values = ttk.LabelFrame(common, text="数值修改", padding=8)
        values.pack(fill="x")
        ttk.Label(values, text="功能", font=("Microsoft YaHei UI", 9, "bold")).grid(
            row=0, column=0, padx=5, pady=(0, 5), sticky="w"
        )
        ttk.Label(values, text="目标值", font=("Microsoft YaHei UI", 9, "bold")).grid(
            row=0, column=1, padx=5, pady=(0, 5), sticky="w"
        )
        ttk.Label(values, text="操作", font=("Microsoft YaHei UI", 9, "bold")).grid(
            row=0, column=2, padx=5, pady=(0, 5), sticky="w"
        )
        ttk.Label(values, text="状态", font=("Microsoft YaHei UI", 9, "bold")).grid(
            row=0, column=3, padx=5, pady=(0, 5), sticky="w"
        )
        values.columnconfigure(1, weight=1)
        values.columnconfigure(3, weight=2)

        for row, (key, label, _) in enumerate(VALUE_ROWS, start=1):
            ttk.Label(values, text=label).grid(row=row, column=0, padx=5, pady=4, sticky="w")
            entry = ttk.Entry(values, textvariable=self.value_vars[key], width=18)
            entry.grid(row=row, column=1, padx=5, pady=4, sticky="ew")
            button = ttk.Button(
                values,
                text="修改",
                width=9,
                command=lambda selected=key: self._start_value_write(selected),
            )
            button.grid(row=row, column=2, padx=5, pady=4)
            ttk.Label(values, textvariable=self.result_vars[key]).grid(
                row=row, column=3, padx=5, pady=4, sticky="w"
            )
            self.action_widgets.extend((entry, button))

        toggles = ttk.LabelFrame(common, text="开关功能", padding=8)
        toggles.pack(fill="x", pady=8)
        for row, (key, label) in enumerate(PATCH_ROWS):
            checkbox = ttk.Checkbutton(
                toggles,
                text=label,
                variable=self.patch_vars[key],
                command=lambda selected=key: self._start_patch_toggle(selected),
            )
            checkbox.grid(row=row, column=0, padx=5, pady=5, sticky="w")
            ttk.Label(toggles, textvariable=self.patch_status_vars[key]).grid(
                row=row, column=1, padx=20, pady=5, sticky="w"
            )
            self.action_widgets.append(checkbox)
        toggles.columnconfigure(1, weight=1)
        stop_button = ttk.Button(toggles, text="关闭全部开关", command=self._stop_all_patches)
        stop_button.grid(row=0, column=2, rowspan=2, padx=5)
        self.action_widgets.append(stop_button)

        movement = ttk.LabelFrame(common, text='快速移动 · 仅当前玩家', padding=8)
        movement.pack(fill='x', pady=(0, 8))
        for column, (multiplier, label) in enumerate(((1, '恢复原速'), (2, '2 倍速度'), (3, '3 倍速度'))):
            button = ttk.Button(movement, text=label,
                                command=lambda value=multiplier: self._start_movement(value))
            button.grid(row=0, column=column, padx=5, pady=4, sticky='ew')
            movement.columnconfigure(column, weight=1)
            self.action_widgets.append(button)
        ttk.Label(movement, textvariable=self.movement_status).grid(row=1, column=0, columnspan=3, sticky='w', padx=5)
        hotkey_text = '游戏内快捷键：Ctrl+Alt+1 原速 / Ctrl+Alt+2 两倍 / Ctrl+Alt+3 三倍'
        if self.hotkeys.failed:
            hotkey_text += '\n快捷键被占用：' + ', '.join(str(x) for x in self.hotkeys.failed) + '；可使用按钮'
        ttk.Label(movement, text=hotkey_text, wraplength=620).grid(row=2, column=0, columnspan=3, sticky='w', padx=5, pady=(4,0))

        notice = ttk.LabelFrame(outer, text="说明", padding=8)
        notice.pack(fill="both", expand=True)
        ttk.Label(
            notice,
            text=(
                "每项只需填写目标值并点击“修改”，程序会在后台通过当前版本的唯一特征码自动定位。"
                "定位与写入均会自动完成，不需要填写或查看地址。首次定位可能需要数秒。"
                "不会保存本次进程地址供下次复用。"
            ),
            wraplength=600,
            foreground="#444444",
        ).pack(anchor="w")

        footer = ttk.Frame(outer)
        footer.pack(fill="x", pady=(8, 0))
        ttk.Button(footer, text="打开日志目录", command=self._open_logs).pack(side="right")

    def _build_loot_ui(self, parent):
        ttk.Label(parent, text='高级武器掉落 · 紫色 / 橙色',
                  font=('Microsoft YaHei UI',12,'bold')).pack(anchor='w',pady=(0,10))
        ttk.Label(parent, text='游戏没有统一的“当前爆率百分比”。这里读取并调整普通武器池的稀有度权重系数。',
                  wraplength=620).pack(anchor='w',pady=(0,12))
        table = ttk.Frame(parent)
        table.pack(fill='x')
        for col,title in enumerate(('武器品质','当前权重系数','目标系数（0～1000）')):
            ttk.Label(table,text=title).grid(row=0,column=col,sticky='w',padx=6,pady=6)
        for row,(key,title) in enumerate((('VeryRare','紫色 · 史诗'),('Legendary','橙色 · 传奇')),1):
            ttk.Label(table,text=title).grid(row=row,column=0,sticky='w',padx=6,pady=8)
            ttk.Label(table,textvariable=self.loot_current[key]).grid(row=row,column=1,sticky='w',padx=6)
            entry=ttk.Entry(table,textvariable=self.loot_inputs[key],width=16)
            entry.grid(row=row,column=2,sticky='ew',padx=6)
            self.action_widgets.append(entry)
        table.columnconfigure(2,weight=1)
        buttons=ttk.Frame(parent)
        buttons.pack(fill='x',pady=16)
        for action,label in (('read','读取当前值'),('apply','应用目标系数'),('restore','恢复修改前值')):
            button=ttk.Button(buttons,text=label,command=lambda selected=action:self._start_loot(selected))
            button.pack(side='left',padx=4)
            self.action_widgets.append(button)
        ttk.Label(parent,textvariable=self.loot_status,wraplength=620).pack(anchor='w',pady=(0,16))
        ttk.Label(parent,text=(
            '例如：橙色输入 10，表示把该权重系数设为 10；不是 10% 爆率，也不保证必出橙装。\n\n'
            '提高某档权重通常会提高它在该武器池中的相对选择机会；两档同时提高时也会相互竞争。'
            '输入 0 会禁用该池中的对应档位。实际掉落还取决于敌人、箱子、等级、混乱模式等条件。\n\n'
            '作用范围：使用普通武器池生成的新武器。不会提高武器等级，不修改已掉落武器；'
            'Boss 专属池和其他独立掉落池可能不受影响。\n\n'
            '正常关闭或“关闭全部开关”时恢复本次修改前值。切地图后若对象卸载，请重新读取和应用。'
            '当前已验证权重读写与恢复；尚未完成大量掉落统计。'),wraplength=620).pack(anchor='w')

    def _display_loot(self, scales):
        for key,var in self.loot_current.items():
            var.set(f'{scales[key]:g} ×' if scales else '未读取')

    def _start_loot(self, action):
        if self.worker_running:
            return
        values = None
        if action == 'apply':
            try:
                values = [parse_scale(self.loot_inputs[key].get()) for key in ('VeryRare','Legendary')]
            except Exception as exc:
                messagebox.showerror('输入错误',str(exc),parent=self.root)
                return
        self.loot_status.set('正在处理，请稍候……')
        def task():
            if action == 'read':
                return self.features.read_loot()
            record = self.features.set_loot(*values) if action == 'apply' else self.features.restore_loot()
            try:
                memory_scan.append_write_log(LOG_DIR,record)
            except OSError:
                record['log_warning'] = True
            return record
        def success(record):
            if 'scales' in record:
                self._display_loot(record['scales'])
            else:
                self._display_loot(None)
            if action == 'restore':
                text = record.get('message') or ('已恢复本次修改前值' if record['status']=='restored' else
                      '没有待恢复的修改' if record['status']=='unchanged' else '已恢复可恢复项；保留游戏外部更新的值')
            else:
                text = '当前权重已读取（非爆率百分比）' if action=='read' else '目标系数已应用并读回验证'
            if record.get('log_warning'):
                text += '；日志保存失败'
            self.loot_status.set(text)
        def failure():
            self._display_loot(None)
            self.loot_status.set('操作失败，请查看错误信息；如已修改过，可点击恢复修改前值重试')
        self._run_worker(task,success,failure)

    def _show_help(self) -> None:
        messagebox.showinfo(
            "使用说明",
            "只用于离线单人环境，使用前请备份存档。\n\n"
            "数值修改：输入目标值，点击对应的“修改”，等待成功提示即可。"
            "首次定位会读取本地存档值并扫描当前游戏进程，可能需要数秒；存档只读不写。\n\n"
            "无限弹药和技能点不减少是可恢复的内存补丁。关闭修改器时会自动恢复；"
            "若游戏或修改器异常退出，重新启动游戏即可恢复原始代码。\n\n"
            "快速移动支持原速、2 倍、3 倍。Ctrl+Alt+1/2/3 可在游戏前台切换。"
            "首次定位可能需要 30～90 秒；之后切换很快。切地图或角色改变后可能停止加速，重新选择即可。"
            "正常关闭时恢复原速。只修改当前角色地面移动倍率。\n\n"
            "高级武器掉落：在独立页读取紫/橙权重系数，输入 0～1000 并应用；关闭时恢复修改前值。"
            "系数不是爆率百分比，不提高武器等级，也不保证影响 Boss 专属池。\n\n"
            "经验值修改后可能需要获得一次经验或重新打开角色菜单，等级显示才会刷新。",
            parent=self.root,
        )

    def _post_status(self, text: str) -> None:
        self.root.after(0, lambda value=text: self.status_var.set(value))

    def _poll_process(self) -> None:
        if not self.worker_running:
            try:
                pid = memory_scan.find_process(str(CONFIG["process_name"]))
                if pid != self.pid:
                    self.pid = pid
                    self.features.reset_for_process(pid)
                    self.movement_status.set('原速（未启用）')
                    self._display_loot(None)
                    self.loot_status.set('游戏进程已更新，请重新读取权重')
                    for key, _ in PATCH_ROWS:
                        self.patch_vars[key].set(False)
                        self.patch_status_vars[key].set("关闭")
                with memory_scan.ProcessMemory(pid, writable=False) as process:
                    path = process.image_path()
                expected = str(CONFIG.get("expected_executable", "")).strip()
                if expected and path.casefold() != expected.casefold():
                    self.pid = None
                    self.status_var.set(f"发现同名进程，但路径不匹配：{path}")
                else:
                    self.status_var.set(f"已连接游戏 | PID {pid}")
                try:
                    message = self.features.check_movement()
                    if message:
                        self.movement_status.set(message)
                except Exception as exc:
                    self.movement_status.set(f'速度状态检查失败：{exc}；可点击恢复原速重试')
                try:
                    message,scales=self.features.check_loot()
                    if self.features.loot:
                        self._display_loot(scales)
                    if message:
                        self.loot_status.set(message)
                except Exception as exc:
                    self._display_loot(None)
                    self.loot_status.set(f'权重状态检查失败：{exc}')
            except Exception:
                self.pid = None
                self.status_var.set("尚未连接：请先启动并进入 Borderlands 3")
                self.movement_status.set('游戏未连接；进入角色后重新选择倍率')
                self._display_loot(None)
        self.root.after(1000, self._poll_process)

    def _poll_hotkeys(self) -> None:
        requested = self.hotkeys.poll()
        if requested is not None:
            self.pending_speed = requested
        if self.pending_speed is not None and not self.worker_running:
            requested, self.pending_speed = self.pending_speed, None
            self._start_movement(requested)
        self.root.after(100, self._poll_hotkeys)

    def _start_movement(self, multiplier: int) -> None:
        if self.worker_running:
            self.pending_speed = multiplier
            return
        self.movement_status.set('正在恢复原速……' if multiplier == 1 else f'正在设置 {multiplier} 倍速度……')

        def task():
            record = self.features.set_movement_multiplier(multiplier)
            try:
                memory_scan.append_write_log(LOG_DIR, record)
            except OSError as exc:
                record['log_warning'] = str(exc)
            return record

        def success(record):
            text = record.get('message') or ('已恢复原速' if multiplier == 1 else f'已启用 {multiplier} 倍速度')
            if record.get('log_warning'):
                text += '（日志保存失败）'
            self.movement_status.set(text)
            self.status_var.set(text)

        def failure():
            active = self.features.movement.active if self.features.movement else None
            state = f"仍为 {active['multiplier']} 倍" if active else '未启用'
            self.movement_status.set('操作失败，' + state)

        self._run_worker(task, success, failure)

    @staticmethod
    def _parse_int(text: str, label: str) -> int:
        try:
            return int(text.strip(), 10)
        except ValueError as exc:
            raise memory_scan.TrainerError(f"{label}必须是十进制整数") from exc

    def _set_busy(self, busy: bool) -> None:
        self.worker_running = busy
        state = "disabled" if busy else "normal"
        for widget in self.action_widgets:
            widget.configure(state=state)
        if busy:
            self.progress.start(12)
        else:
            self.progress.stop()

    def _run_worker(self, task, success, failure=None) -> None:
        if self.worker_running:
            return
        self._set_busy(True)

        def worker() -> None:
            try:
                result = task()
                self.root.after(0, lambda: self._worker_success(result, success))
            except Exception as exc:
                self.root.after(0, lambda error=exc: self._worker_error(error, failure))

        threading.Thread(target=worker, daemon=True).start()

    def _worker_success(self, result, callback) -> None:
        self._set_busy(False)
        callback(result)

    def _worker_error(self, error: Exception, callback) -> None:
        self._set_busy(False)
        if callback:
            callback()
        self.status_var.set(f"操作失败：{error}")
        messagebox.showerror("操作失败", str(error), parent=self.root)

    def _start_value_write(self, feature_name: str) -> None:
        label = next(label for key, label, _ in VALUE_ROWS if key == feature_name)
        try:
            value = self._parse_int(self.value_vars[feature_name].get(), label)
        except Exception as exc:
            messagebox.showerror("输入错误", str(exc), parent=self.root)
            return

        self.result_vars[feature_name].set("正在自动定位……")
        self.status_var.set(f"正在修改{label}……")

        def task():
            record = self.features.set_value(feature_name, value)
            log_path = memory_scan.append_write_log(LOG_DIR, record)
            return value, log_path

        def success(result) -> None:
            target, log_path = result
            self.result_vars[feature_name].set(f"已修改为 {target}")
            self.status_var.set(f"{label}修改成功 | 日志：{log_path}")

        def failure() -> None:
            self.result_vars[feature_name].set("修改失败")

        self._run_worker(task, success, failure)

    def _start_patch_toggle(self, patch_name: str) -> None:
        enabled = bool(self.patch_vars[patch_name].get())
        label = next(label for key, label in PATCH_ROWS if key == patch_name)
        self.patch_status_vars[patch_name].set("正在处理……")
        self.status_var.set(f"正在{'启用' if enabled else '关闭'}{label}……")

        def task():
            record = self.features.set_patch(patch_name, enabled)
            memory_scan.append_write_log(LOG_DIR, record)
            return enabled

        def success(state: bool) -> None:
            self.patch_status_vars[patch_name].set("已启用" if state else "关闭")
            self.status_var.set(f"{label}{'已启用' if state else '已关闭'}")

        def failure() -> None:
            self.patch_vars[patch_name].set(not enabled)
            self.patch_status_vars[patch_name].set("已启用" if not enabled else "关闭")

        self._run_worker(task, success, failure)

    def _stop_all_patches(self) -> None:
        self.status_var.set("正在恢复全部开关功能……")

        def task():
            return self.features.restore_all()

        def success(_restored) -> None:
            for key, _ in PATCH_ROWS:
                self.patch_vars[key].set(False)
                self.patch_status_vars[key].set("关闭")
            self.status_var.set("全部开关功能已恢复")
            self.movement_status.set('已恢复原速')
            self.loot_status.set('掉落权重修改已结束；可读取当前值确认')
            self._display_loot(None)

        self._run_worker(task, success)

    def _open_logs(self) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(LOG_DIR)

    def _on_close(self) -> None:
        if self.worker_running:
            messagebox.showwarning("正在操作", "请等待当前操作完成后再关闭。", parent=self.root)
            return
        self.status_var.set("正在恢复补丁……")
        self.root.update_idletasks()
        try:
            self.features.restore_all()
            if self.features.movement:
                self.features.movement.close()
            if self.features.loot:
                self.features.loot.close()
        except Exception as exc:
            self.status_var.set(str(exc))
            messagebox.showerror('恢复失败', f'{exc}\n请重试关闭；退出游戏也会清除内存修改。', parent=self.root)
            return
        self.hotkeys.close()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    TrainerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
