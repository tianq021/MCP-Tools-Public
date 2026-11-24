#!/usr/bin/env python3
"""Chinese GUI for the Infection Free Zone resource-only trainer."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import math
import os
from pathlib import Path
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

import mono_injector


PROCESS_NAME = "Infection Free Zone.exe"
SUPPORTED_BUILD = "24801014"
SUPPORTED_IFZ_SHA256 = "D87B98B0FC9343C014A3E5B1A76A8DF01B3CFF5AB15AEAD4E15DAE277A3BA6F1"
GAME_DIR = Path(r"F:\SteamLibrary\steamapps\common\Infection Free Zone")
IFZ_DLL_PATH = GAME_DIR / "Infection Free Zone_Data" / "Managed" / "Ifz.dll"

INSTALL_MONITOR_MESSAGE = 0x8455
VK_F6 = 0x75
WAIT_OBJECT_0 = 0
SMTO_ABORTIFHUNG = 0x0002
FILE_MAP_WRITE = 0x0002
FILE_MAP_READ = 0x0004
SHARED_MEMORY_SIZE = 256
RESOURCE_SEQUENCE_OFFSET = 0
MONITOR_READY_OFFSET = 8
MONITOR_HEARTBEAT_OFFSET = 16
RESOURCE_QUANTITY_OFFSET = 24
RESOURCE_ID_OFFSET = 32
RESOURCE_ID_CHARACTERS = 64
EXTRA_COMMAND_SEQUENCE_OFFSET = 160
EXTRA_COMMAND_CODE_OFFSET = 168
EXTRA_COMMAND_ARGUMENT_OFFSET = 176
MONITOR_STALL_NOTICE_SECONDS = 4.0
MONITOR_RECOVERY_SECONDS = 15.0
INSTALL_MONITOR_TIMEOUT_MS = 10_000
UINT64_MAX = (1 << 64) - 1

STATUS_PATH = (
    Path(os.environ.get("LOCALAPPDATA", Path.home()))
    / "InfectionFreeZoneTrainer"
    / "bridge_status_v3.txt"
)

RESOURCE_OPTIONS = (
    ("罐头", "res_cans"),
    ("燃料", "res_fuel"),
    ("弹药", "res_ammo"),
    ("砖块", "res_bricks"),
    ("金属", "res_metal"),
    ("木材", "res_wood"),
    ("肥料", "res_fertiliser"),
    ("药品", "res_drugs"),
    ("炸药", "res_explosives"),
    ("手榴弹", "res_grenades"),
    ("载具弹药", "res_ammotank"),
    ("蔬菜", "res_vegetables"),
    ("肉类", "res_meat"),
    ("电子元件", "res_electronics"),
    ("基础工具", "res_basic_tool"),
    ("高级工具", "res_adv_tool"),
    ("科研材料", "res_scientificmaterial"),
    ("木板", "res_planks"),
    ("谷物", "res_grain"),
    ("口粮", "res_food_rations"),
    ("鸡", "res_chicken"),
    ("地雷", "res_mines"),
    ("饮料", "res_beverages"),
    ("科学家手提箱（任务）", "res_scientists_case"),
    ("科学家疫苗（任务）", "res_scientists_vaccine"),
    ("感染科学家（任务）", "res_scientists_infected"),
    ("科学家样本（任务）", "res_scientists_sample"),
    ("基础近战武器", "eq_basic_melee_weapon"),
    ("手枪", "eq_pistol"),
    ("狙击步枪", "eq_sniper_rifle"),
    ("突击步枪", "eq_assault_rifle"),
    ("霰弹枪", "eq_shotgun"),
    ("弓", "eq_bow"),
    ("弩", "eq_crossbow"),
    ("火焰喷射器", "eq_flamethrower"),
    ("燃烧瓶", "eq_molotov"),
    ("长柄武器", "eq_polearm"),
    ("重口径武器（HCAL）", "eq_hcal"),
    ("迫击炮", "eq_mortar"),
    ("坦克炮", "eq_tank_cannon"),
    ("防护服", "eq_wear_protectors"),
    ("防暴装备", "eq_wear_riot_gear"),
    ("护甲板", "eq_wear_plate"),
    ("迷彩装备", "eq_wear_camouflage"),
    ("士兵装备", "eq_wear_soldier_gear"),
)
RESOURCE_LABEL_TO_ID = dict(RESOURCE_OPTIONS)
RESOURCE_ID_TO_LABEL = {resource_id: label for label, resource_id in RESOURCE_OPTIONS}
EXTRA_COMMANDS = {
    1: "显示感染者巢穴",
    2: "显示敌对藏身处",
    3: "显示全部巢穴和藏身处",
    4: "显示地图车辆",
}
TOGGLE_COMMANDS = {
    10: "快速建造",
    11: "快速研究",
    12: "免费研究",
    13: "快速搜刮",
    14: "无限弹药",
    15: "无饥饿",
    16: "完美心情",
    17: "完美天气",
}


user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = mono_injector.kernel32

user32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = wintypes.SHORT
user32.SendMessageTimeoutW.argtypes = [
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
    wintypes.UINT,
    wintypes.UINT,
    ctypes.POINTER(ctypes.c_size_t),
]
user32.SendMessageTimeoutW.restype = wintypes.LPARAM
kernel32.CreateEventW.argtypes = [
    ctypes.c_void_p,
    wintypes.BOOL,
    wintypes.BOOL,
    wintypes.LPCWSTR,
]
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.ResetEvent.argtypes = [wintypes.HANDLE]
kernel32.ResetEvent.restype = wintypes.BOOL
kernel32.OpenFileMappingW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.OpenFileMappingW.restype = wintypes.HANDLE
kernel32.MapViewOfFile.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.c_size_t,
]
kernel32.MapViewOfFile.restype = ctypes.c_void_p
kernel32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
kernel32.UnmapViewOfFile.restype = wintypes.BOOL


def bundled_bridge_path() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    candidates = (
        base / "InfectionFreeZoneBridgeV3.dll",
        base / "managed" / "InfectionFreeZoneBridgeV3.dll",
        Path(__file__).resolve().parent / "managed" / "InfectionFreeZoneBridgeV3.dll",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def verify_supported_build(path: Path = IFZ_DLL_PATH) -> None:
    if not path.is_file():
        raise mono_injector.InjectorError(f"找不到游戏程序集：{path}")
    actual = sha256_file(path)
    if actual != SUPPORTED_IFZ_SHA256:
        raise mono_injector.InjectorError(
            "检测到未验证的游戏版本，已停止连接。请重新分析 Ifz.dll 后再更新修改器。"
        )


def find_game_window(pid: int) -> int:
    result = 0
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(window: int, unused: int) -> bool:
        nonlocal result
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(window, ctypes.byref(process_id))
        if (
            process_id.value == pid
            and user32.IsWindowVisible(window)
            and not user32.GetWindow(window, 4)
        ):
            result = int(window)
            return False
        return True

    callback = callback_type(visit)
    user32.EnumWindows(callback, 0)
    return result


def create_ready_event(pid: int) -> int:
    handle = kernel32.CreateEventW(
        None,
        True,
        False,
        f"Local\\InfectionFreeZoneTrainer_ReadyV3_{pid}",
    )
    if not handle:
        code = ctypes.get_last_error()
        raise mono_injector.InjectorError(
            f"无法创建连接事件（Win32 {code}: {ctypes.FormatError(code).strip()}）"
        )
    if not kernel32.ResetEvent(handle):
        code = ctypes.get_last_error()
        kernel32.CloseHandle(handle)
        raise mono_injector.InjectorError(
            f"无法重置连接事件（Win32 {code}: {ctypes.FormatError(code).strip()}）"
        )
    return int(handle)


def parse_bridge_status(text: str) -> tuple[str, int | None, list[str]]:
    parts = text.strip().split("|")
    if len(parts) < 2:
        return "INVALID", None, []
    try:
        pid = int(parts[1])
    except ValueError:
        pid = None
    return parts[0], pid, parts[2:]


def validate_resource_request(label: str, amount_text: str) -> tuple[str, float]:
    resource_id = RESOURCE_LABEL_TO_ID.get(label)
    if resource_id is None:
        raise ValueError("请选择有效资源。")
    try:
        quantity = float(amount_text.strip())
    except ValueError as error:
        raise ValueError("数量必须是数字。") from error
    if not math.isfinite(quantity) or quantity <= 0:
        raise ValueError("数量必须是大于 0 的有限数字。")
    if quantity > 1_000_000:
        raise ValueError("单次添加数量不能超过 1000000。")
    return resource_id, quantity


def open_shared_state(pid: int) -> tuple[int, int]:
    handle = kernel32.OpenFileMappingW(
        FILE_MAP_READ | FILE_MAP_WRITE,
        False,
        f"Local\\InfectionFreeZoneTrainer_SharedV3_{pid}",
    )
    if not handle:
        code = ctypes.get_last_error()
        raise mono_injector.InjectorError(
            f"无法打开桥接状态（Win32 {code}: {ctypes.FormatError(code).strip()}）"
        )
    view = kernel32.MapViewOfFile(
        handle,
        FILE_MAP_READ | FILE_MAP_WRITE,
        0,
        0,
        SHARED_MEMORY_SIZE,
    )
    if not view:
        code = ctypes.get_last_error()
        kernel32.CloseHandle(handle)
        raise mono_injector.InjectorError(
            f"无法映射桥接状态（Win32 {code}: {ctypes.FormatError(code).strip()}）"
        )
    return int(handle), int(view)


def close_shared_state(handle: int, view: int) -> None:
    if view:
        kernel32.UnmapViewOfFile(view)
    if handle:
        kernel32.CloseHandle(handle)


def read_shared_uint64(view: int, offset: int) -> int:
    return ctypes.c_uint64.from_address(view + offset).value


def monitor_is_ready(view: int) -> bool:
    return read_shared_uint64(view, MONITOR_READY_OFFSET) == 1


def read_monitor_heartbeat(view: int) -> int:
    return read_shared_uint64(view, MONITOR_HEARTBEAT_OFFSET)


def classify_monitor_health(is_ready: bool, heartbeat_age_seconds: float) -> str:
    if not is_ready or heartbeat_age_seconds >= MONITOR_RECOVERY_SECONDS:
        return "RECOVER"
    if heartbeat_age_seconds >= MONITOR_STALL_NOTICE_SECONDS:
        return "BUSY"
    return "HEALTHY"


def should_trigger_hotkey(
    is_down: bool,
    was_down: bool,
    foreground_window: int,
    game_window: int,
    ready: bool,
) -> bool:
    """Trigger once per key press, only while the connected game is foreground."""
    return bool(
        is_down
        and not was_down
        and ready
        and game_window
        and foreground_window == game_window
    )


def install_resource_monitor(game_window: int) -> bool:
    result = ctypes.c_size_t()
    succeeded = user32.SendMessageTimeoutW(
        game_window,
        INSTALL_MONITOR_MESSAGE,
        0,
        0,
        SMTO_ABORTIFHUNG,
        INSTALL_MONITOR_TIMEOUT_MS,
        ctypes.byref(result),
    )
    return bool(succeeded and result.value == 1)


def write_resource_request(view: int, resource_id: str, quantity: float) -> None:
    encoded = resource_id.encode("utf-16-le")
    capacity = RESOURCE_ID_CHARACTERS * 2
    if len(encoded) > capacity - 2:
        raise ValueError("资源 ID 过长。")

    ctypes.memset(view + RESOURCE_ID_OFFSET, 0, capacity)
    ctypes.memmove(view + RESOURCE_ID_OFFSET, encoded, len(encoded))
    ctypes.c_double.from_address(view + RESOURCE_QUANTITY_OFFSET).value = quantity

    address = view + RESOURCE_SEQUENCE_OFFSET
    sequence = ctypes.c_uint64.from_address(address).value
    ctypes.c_uint64.from_address(address).value = (sequence + 1) & UINT64_MAX


def write_extra_command(view: int, command_code: int, argument: int = 0) -> None:
    if command_code not in EXTRA_COMMANDS and command_code not in TOGGLE_COMMANDS:
        raise ValueError(f"不支持的额外功能代码：{command_code}")
    ctypes.c_int64.from_address(view + EXTRA_COMMAND_ARGUMENT_OFFSET).value = argument
    ctypes.c_int64.from_address(view + EXTRA_COMMAND_CODE_OFFSET).value = command_code
    address = view + EXTRA_COMMAND_SEQUENCE_OFFSET
    sequence = ctypes.c_uint64.from_address(address).value
    ctypes.c_uint64.from_address(address).value = (sequence + 1) & UINT64_MAX


class TrainerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Infection Free Zone 资源修改器")
        self.root.geometry("720x690")
        self.root.minsize(680, 650)

        self.pid: int | None = None
        self.game_window = 0
        self.ready_event = 0
        self.shared_handle = 0
        self.shared_view = 0
        self.connecting = False
        self.was_f6_down = False
        self.last_status_mtime = 0
        self.last_monitor_heartbeat = 0
        self.last_monitor_heartbeat_time = time.monotonic()

        self.connection_var = tk.StringVar(value="等待游戏启动")
        self.monitor_var = tk.StringVar(value="桥接监视：未连接")
        self.result_var = tk.StringVar(value="启动游戏并载入存档后连接")
        self.resource_var = tk.StringVar(value=RESOURCE_OPTIONS[0][0])
        self.amount_var = tk.StringVar(value="100")

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.after(500, self._connection_tick)
        self.root.after(30, self._hotkey_tick)
        self.root.after(250, self._status_tick)
        self.root.after(250, self._monitor_tick)

    def _build_ui(self) -> None:
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")

        outer = ttk.Frame(self.root, padding=(18, 12))
        outer.pack(fill="both", expand=True)

        ttk.Label(
            outer,
            text="Infection Free Zone 资源修改器",
            font=("Microsoft YaHei UI", 17, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            outer,
            text=f"适用 Steam 构建 {SUPPORTED_BUILD}｜资源、地图显示与稳定功能",
            foreground="#666666",
        ).pack(anchor="w", pady=(3, 8))

        connection = ttk.LabelFrame(outer, text="连接", padding=8)
        connection.pack(fill="x")
        row = ttk.Frame(connection)
        row.pack(fill="x")
        self.connect_button = ttk.Button(
            row,
            text="连接 / 重新注入",
            command=self.start_connect,
        )
        self.connect_button.pack(side="left")
        ttk.Label(row, textvariable=self.connection_var).pack(side="left", padx=12)
        self.progress = ttk.Progressbar(connection, mode="determinate")
        self.progress.pack(fill="x", pady=(6, 0))

        action = ttk.LabelFrame(outer, text="添加总部资源", padding=10)
        action.pack(fill="both", expand=True, pady=8)
        form = ttk.Frame(action)
        form.pack(pady=(4, 2))
        ttk.Label(form, text="资源：").grid(row=0, column=0, padx=(0, 6), pady=4)
        self.resource_box = ttk.Combobox(
            form,
            textvariable=self.resource_var,
            values=[label for label, _ in RESOURCE_OPTIONS],
            state="readonly",
            width=20,
        )
        self.resource_box.grid(row=0, column=1, padx=(0, 18), pady=4)
        ttk.Label(form, text="数量：").grid(row=0, column=2, padx=(0, 6), pady=4)
        self.amount_entry = ttk.Entry(form, textvariable=self.amount_var, width=16)
        self.amount_entry.grid(row=0, column=3, pady=4)

        self.add_button = ttk.Button(
            action,
            text="添加到总部   [F6]",
            command=self.add_resource,
            state="disabled",
        )
        self.add_button.pack(ipadx=48, ipady=5, pady=(4, 3))
        ttk.Label(action, textvariable=self.result_var, foreground="#555555").pack(pady=(3, 1))
        ttk.Label(action, textvariable=self.monitor_var, foreground="#777777").pack()
        ttk.Label(
            action,
            text=(
                "F6 仅在游戏窗口位于前台时触发；若添加后超过总部仓库容量，"
                "本次资源不会添加。"
            ),
            foreground="#777777",
            wraplength=640,
            justify="left",
        ).pack(pady=(4, 0))

        extra = ttk.LabelFrame(outer, text="地图显示", padding=8)
        extra.pack(fill="x", pady=(0, 8))
        button_row = ttk.Frame(extra)
        button_row.pack(fill="x")
        self.extra_buttons: list[ttk.Button] = []
        for index, (command_code, label) in enumerate(EXTRA_COMMANDS.items()):
            button = ttk.Button(
                button_row,
                text=label,
                command=lambda code=command_code: self.run_extra_command(code),
                state="disabled",
            )
            button.grid(
                row=index // 2,
                column=index % 2,
                padx=6,
                pady=3,
                sticky="ew",
            )
            self.extra_buttons.append(button)
        button_row.columnconfigure(0, weight=1)
        button_row.columnconfigure(1, weight=1)

        stable = ttk.LabelFrame(outer, text="稳定功能开关", padding=8)
        stable.pack(fill="x", pady=(0, 8))
        toggle_grid = ttk.Frame(stable)
        toggle_grid.pack(fill="x")
        self.toggle_vars: dict[int, tk.BooleanVar] = {}
        self.toggle_buttons: list[ttk.Checkbutton] = []
        for index, (command_code, label) in enumerate(TOGGLE_COMMANDS.items()):
            variable = tk.BooleanVar(value=False)
            button = ttk.Checkbutton(
                toggle_grid,
                text=label,
                variable=variable,
                command=lambda code=command_code, var=variable: self.set_stable_toggle(
                    code, var
                ),
                state="disabled",
            )
            button.grid(
                row=index // 4,
                column=index % 4,
                padx=8,
                pady=3,
                sticky="w",
            )
            self.toggle_vars[command_code] = variable
            self.toggle_buttons.append(button)
        for column in range(4):
            toggle_grid.columnconfigure(column, weight=1)
        ttk.Label(
            stable,
            text="开关会调用游戏自带布尔命令；载入新存档后如状态重置，请重新切换。",
            foreground="#777777",
        ).pack(anchor="w", padx=18, pady=(2, 0))

        ttk.Label(
            outer,
            text="不替换游戏 DLL；所有请求在 Unity 主线程调用游戏原生资源方法。",
            foreground="#777777",
        ).pack(anchor="w")

    def _close_ready_event(self) -> None:
        if self.ready_event:
            kernel32.CloseHandle(self.ready_event)
            self.ready_event = 0

    def _close_shared_state(self) -> None:
        close_shared_state(self.shared_handle, self.shared_view)
        self.shared_handle = 0
        self.shared_view = 0

    def _clear_connection(self, status: str = "等待游戏启动") -> None:
        self._close_ready_event()
        self._close_shared_state()
        self.pid = None
        self.game_window = 0
        self.connection_var.set(status)
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        self.add_button.configure(state="disabled")
        for button in self.extra_buttons:
            button.configure(state="disabled")
        for button in self.toggle_buttons:
            button.configure(state="disabled")
        self.monitor_var.set("桥接监视：未连接")
        self.last_monitor_heartbeat = 0
        self.last_monitor_heartbeat_time = time.monotonic()

    def start_connect(self) -> None:
        if self.connecting:
            return
        self.connecting = True
        self.connect_button.configure(state="disabled")
        self.progress.configure(mode="indeterminate", value=0)
        self.progress.start(12)
        self.connection_var.set("正在验证版本并连接……")

        def worker() -> None:
            ready_event = 0
            shared_handle = 0
            shared_view = 0
            try:
                verify_supported_build()
                pid = mono_injector.find_process(PROCESS_NAME)
                ready_event = create_ready_event(pid)
                mono_injector.inject_managed_bridge(
                    pid,
                    bundled_bridge_path(),
                    namespace="InfectionFreeZoneTrainerV3",
                )

                deadline = time.monotonic() + 18.0
                while kernel32.WaitForSingleObject(ready_event, 100) != WAIT_OBJECT_0:
                    if time.monotonic() >= deadline:
                        raise mono_injector.InjectorError(self._latest_bridge_error(pid))

                game_window = find_game_window(pid)
                if not game_window:
                    raise mono_injector.InjectorError("桥接已加载，但找不到游戏窗口。")
                shared_handle, shared_view = open_shared_state(pid)
                if not monitor_is_ready(shared_view):
                    if not install_resource_monitor(game_window):
                        raise mono_injector.InjectorError(self._latest_bridge_error(pid))
                    monitor_deadline = time.monotonic() + 3.0
                    while not monitor_is_ready(shared_view):
                        if time.monotonic() >= monitor_deadline:
                            raise mono_injector.InjectorError("Unity 主线程资源桥接启动超时。")
                        time.sleep(0.05)

                self.root.after(
                    0,
                    lambda: self._connect_finished(
                        pid,
                        ready_event,
                        game_window,
                        shared_handle,
                        shared_view,
                        None,
                    ),
                )
            except Exception as exception:
                close_shared_state(shared_handle, shared_view)
                self.root.after(
                    0,
                    lambda error=exception, event=ready_event: self._connect_finished(
                        None, event, 0, 0, 0, error
                    ),
                )

        threading.Thread(target=worker, daemon=True).start()

    def _latest_bridge_error(self, pid: int) -> str:
        try:
            state, status_pid, details = parse_bridge_status(
                STATUS_PATH.read_text(encoding="utf-8-sig")
            )
            if status_pid == pid and state == "ERROR" and details:
                return details[0]
        except (OSError, UnicodeError):
            pass
        return "等待游戏桥接响应超时，请进入主菜单后重试。"

    def _connect_finished(
        self,
        pid: int | None,
        ready_event: int,
        game_window: int,
        shared_handle: int,
        shared_view: int,
        error: Exception | None,
    ) -> None:
        self.connecting = False
        self.connect_button.configure(state="normal")
        self.progress.stop()
        if error is not None:
            if ready_event:
                kernel32.CloseHandle(ready_event)
            self._clear_connection("连接失败")
            self.result_var.set(str(error))
            messagebox.showerror("Infection Free Zone 资源修改器", str(error))
            return

        self._close_ready_event()
        self._close_shared_state()
        self.pid = pid
        self.ready_event = ready_event
        self.game_window = game_window
        self.shared_handle = shared_handle
        self.shared_view = shared_view
        self.progress.configure(mode="determinate", value=100)
        self.last_monitor_heartbeat = read_monitor_heartbeat(shared_view)
        self.last_monitor_heartbeat_time = time.monotonic()
        self.connection_var.set(f"已连接（PID {pid}）")
        self.result_var.set("已就绪：选择资源和数量后点击添加")
        self.monitor_var.set("桥接监视：运行中")
        self.add_button.configure(state="normal")
        for button in self.extra_buttons:
            button.configure(state="normal")
        for button in self.toggle_buttons:
            button.configure(state="normal")

    def add_resource(self) -> None:
        try:
            resource_id, quantity = validate_resource_request(
                self.resource_var.get(),
                self.amount_var.get(),
            )
        except ValueError as error:
            messagebox.showwarning("输入有误", str(error))
            return

        if not self.pid or not self.shared_view:
            self.start_connect()
            return
        write_resource_request(self.shared_view, resource_id, quantity)
        self.result_var.set(
            f"已发送：{RESOURCE_ID_TO_LABEL[resource_id]} +{quantity:g}，等待游戏确认……"
        )

    def run_extra_command(self, command_code: int) -> None:
        label = EXTRA_COMMANDS.get(command_code)
        if label is None:
            messagebox.showwarning("功能不可用", f"未知功能代码：{command_code}")
            return
        if not self.pid or not self.shared_view:
            self.start_connect()
            return
        write_extra_command(self.shared_view, command_code)
        self.result_var.set(f"已发送：{label}，等待游戏确认……")

    def set_stable_toggle(self, command_code: int, variable: tk.BooleanVar) -> None:
        label = TOGGLE_COMMANDS.get(command_code)
        if label is None:
            return
        enabled = bool(variable.get())
        if not self.pid or not self.shared_view:
            variable.set(not enabled)
            self.start_connect()
            return
        write_extra_command(self.shared_view, command_code, 1 if enabled else 0)
        state_text = "开启" if enabled else "关闭"
        self.result_var.set(f"已发送：{state_text}{label}，等待游戏确认……")

    def _connection_tick(self) -> None:
        try:
            current_pid = mono_injector.find_process(PROCESS_NAME)
        except mono_injector.InjectorError:
            if self.pid is not None:
                self._clear_connection()
            self.root.after(900, self._connection_tick)
            return

        if self.pid is not None and current_pid != self.pid:
            self._clear_connection("检测到游戏已重启，请重新连接")
        elif self.pid is not None and self.ready_event:
            if kernel32.WaitForSingleObject(self.ready_event, 0) != WAIT_OBJECT_0:
                self._clear_connection("桥接连接已失效")
        elif not self.connecting:
            self.connection_var.set("已发现游戏，点击连接")
        self.root.after(900, self._connection_tick)

    def _hotkey_tick(self) -> None:
        is_down = bool(user32.GetAsyncKeyState(VK_F6) & 0x8000)
        foreground = int(user32.GetForegroundWindow() or 0)
        ready = bool(
            self.pid
            and self.shared_view
            and str(self.add_button["state"]) != "disabled"
        )
        if should_trigger_hotkey(
            is_down,
            self.was_f6_down,
            foreground,
            self.game_window,
            ready,
        ):
            self.add_resource()
        self.was_f6_down = is_down
        self.root.after(30, self._hotkey_tick)

    def _status_tick(self) -> None:
        try:
            stat = STATUS_PATH.stat()
            if stat.st_mtime_ns != self.last_status_mtime:
                self.last_status_mtime = stat.st_mtime_ns
                state, pid, details = parse_bridge_status(
                    STATUS_PATH.read_text(encoding="utf-8-sig")
                )
                if self.pid is not None and pid == self.pid:
                    self._apply_bridge_status(state, details)
        except (OSError, UnicodeError):
            pass
        self.root.after(250, self._status_tick)

    def _monitor_tick(self) -> None:
        if self.pid is not None and self.shared_view:
            heartbeat = read_monitor_heartbeat(self.shared_view)
            now = time.monotonic()
            is_ready = monitor_is_ready(self.shared_view)
            if heartbeat != self.last_monitor_heartbeat:
                self.last_monitor_heartbeat = heartbeat
                self.last_monitor_heartbeat_time = now

            health = classify_monitor_health(
                is_ready,
                now - self.last_monitor_heartbeat_time,
            )
            if health == "RECOVER":
                self.monitor_var.set("桥接监视：已中断，正在自动恢复……")
                self.add_button.configure(state="disabled")
                for button in self.extra_buttons:
                    button.configure(state="disabled")
                for button in self.toggle_buttons:
                    button.configure(state="disabled")
                if not self.connecting:
                    self.root.after(0, self._recover_monitor)
            elif health == "BUSY":
                self.monitor_var.set("桥接监视：游戏加载中，暂时等待……")
                self.add_button.configure(state="disabled")
                for button in self.extra_buttons:
                    button.configure(state="disabled")
                for button in self.toggle_buttons:
                    button.configure(state="disabled")
            else:
                self.monitor_var.set("桥接监视：运行中")
                if not self.connecting:
                    self.add_button.configure(state="normal")
                    for button in self.extra_buttons:
                        button.configure(state="normal")
                    for button in self.toggle_buttons:
                        button.configure(state="normal")
        self.root.after(250, self._monitor_tick)

    def _recover_monitor(self) -> None:
        if self.connecting:
            return
        self._clear_connection("桥接中断，正在自动恢复……")
        self.result_var.set("正在重新建立 Unity 主线程资源桥接……")
        self.start_connect()

    def _apply_bridge_status(self, state: str, details: list[str]) -> None:
        if state == "OK" and len(details) >= 2:
            label = RESOURCE_ID_TO_LABEL.get(details[0], details[0])
            self.result_var.set(
                f"添加请求已执行：{label} +{details[1]}（受仓库容量限制）"
            )
        elif state == "EXTRA_OK" and details:
            try:
                command_code = int(details[0])
            except ValueError:
                command_code = -1
            if command_code in TOGGLE_COMMANDS and len(details) >= 2:
                enabled = details[1] == "1"
                self.toggle_vars[command_code].set(enabled)
                state_text = "已开启" if enabled else "已关闭"
                self.result_var.set(f"{state_text}：{TOGGLE_COMMANDS[command_code]}")
            else:
                label = EXTRA_COMMANDS.get(command_code, f"功能代码 {details[0]}")
                self.result_var.set(f"已执行：{label}")
        elif state == "WAITING" and details:
            self.result_var.set(details[0])
        elif state == "DENIED" and details:
            self.result_var.set(details[0])
            messagebox.showwarning("已拒绝操作", details[0])
        elif state == "ERROR" and details:
            self.result_var.set(details[0])

    def _close(self) -> None:
        self._close_ready_event()
        self._close_shared_state()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    TrainerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
