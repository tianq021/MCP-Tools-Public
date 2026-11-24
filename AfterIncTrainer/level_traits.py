"""Edit the profile consumed by the optional expedition traits plugin."""

import json
import os
from pathlib import Path
import secrets
import tempfile
import tkinter as tk
from tkinter import ttk


BUILD = "26049821"
PROFILE_SCHEMA = 3
PLUGIN_VERSION = "26049821.7"
DEFAULT_COUNT_LIMIT = 20
ERROR_LABELS = {
    "extra_danger_range": "额外远征的危险档位上限为 3",
    "modifier_unavailable": "特殊效果不可用",
    "modifier_family_conflict": "与关卡原有的同类效果冲突",
    "modifier_range": "雄心或危险档位不符合该效果的适用范围",
    "generation_mismatch": "游戏生成流程未匹配目标关卡",
    "native_error": "游戏组件应用失败，详情见 BepInEx 日志",
}


def make_profile(seed, ambition_min, ambition_max, danger_min, danger_max, count_min, count_max):
    def integer(text, name, minimum, maximum):
        text = text.strip()
        if not text.isascii() or not text.isdecimal() or len(text) > 10:
            raise ValueError(f"{name}须填写 {minimum}–{maximum} 的整数")
        value = int(text)
        if not minimum <= value <= maximum:
            raise ValueError(f"{name}须填写 {minimum}–{maximum} 的整数")
        return value

    def read_range(low, high, name, limit):
        if not (low.strip() or high.strip()):
            return None
        minimum = integer(low, f"{name}最小值", 0, limit)
        maximum = integer(high, f"{name}最大值", 0, limit)
        if minimum > maximum:
            raise ValueError(f"{name}最小值须小于或等于最大值")
        return {"min": minimum, "max": maximum}

    profile = {
        "schema": PROFILE_SCHEMA,
        "build": BUILD,
        "seed": integer(seed, "远征种子", 1, 2147483647),
        "ambitionRange": read_range(ambition_min, ambition_max, "定居者雄心", 10),
        "dangerRange": read_range(danger_min, danger_max, "感染者危险度", 4),
        "countRange": read_range(count_min, count_max, "追加特性数量", 2147483647) or {"min": 0, "max": 0},
        "rollSeed": secrets.randbelow(2**31),
    }
    if profile["countRange"]["max"] == 0 and profile["ambitionRange"] is None and profile["dangerRange"] is None:
        raise ValueError("至少选择一项关卡特性")
    return profile


def read_record(path):
    try:
        with path.open(encoding="utf-8") as stream:
            record = json.load(stream)
    except FileNotFoundError:
        return None
    allowed = (1, 2, PROFILE_SCHEMA) if path.name == "profile.json" else (1,)
    if not isinstance(record, dict) or record.get("schema") not in allowed or record.get("build") != BUILD:
        raise ValueError(f"{path.name} 的格式或游戏构建不匹配")
    return record


def save_profile(directory, profile):
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / "profile.json"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                         prefix="profile.", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(profile, stream, ensure_ascii=False)
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def observed_status(snapshot, saved):
    if snapshot is None:
        return "等待游戏组件生成远征列表。"
    state = snapshot.get("state")
    if state != "ready":
        return {
            "unsupported_build": "游戏组件检测到不支持的游戏构建。",
            "hook_failed": "游戏组件未能接入关卡生成。请查看 BepInEx 日志。",
        }.get(state, "游戏组件状态未知。")
    if snapshot.get("profileError"):
        return "最近一次游戏启动时无法读取配置。重新保存后重启游戏。"
    if snapshot.get("profile") != saved:
        return "配置将在下次启动游戏时加载。远征列表来自最近一次游戏会话。"
    if saved is None:
        return "从列表选择一场远征。"
    target = next((row for row in snapshot.get("expeditions", []) if row["seed"] == saved["seed"]), None)
    if target is None:
        return "游戏已加载配置，当前会话尚未生成目标远征。"
    if target.get("applied"):
        added = len(target.get("addedModifiers", []))
        return (f"已抽取雄心 {target['ambition']:g}、危险档位 {target['danger']}，"
                f"追加 {added} 项特性。")
    if target.get("error") == "not_enough_modifiers":
        return f"此范围最多可追加 {target['availableCount']} 项相容特性，本次配置未应用。"
    return ERROR_LABELS.get(target.get("error"), "目标远征暂未应用配置。")


def add_panel(parent, directory):
    panel = ttk.LabelFrame(parent, text="自定义关卡特性", padding=6)
    panel.pack(fill="x", pady=(3, 0))
    panel.columnconfigure(1, weight=1, uniform="range")
    panel.columnconfigure(2, weight=1, uniform="range")

    target = tk.StringVar(panel)
    seed = tk.StringVar(panel)
    count_min = tk.IntVar(panel)
    count_max = tk.IntVar(panel)
    ambition_min = tk.IntVar(panel)
    ambition_max = tk.IntVar(panel, value=10)
    danger_min = tk.IntVar(panel)
    danger_max = tk.IntVar(panel, value=4)
    game_ambition = tk.BooleanVar(panel, value=True)
    game_danger = tk.BooleanVar(panel, value=True)
    status = tk.StringVar(panel)
    targets = {}
    effects = {}
    saved = None

    def label(row, text):
        ttk.Label(panel, text=text).grid(row=row, column=0, sticky="w", pady=1, padx=(0, 10))

    label(0, "远征列表")
    target_picker = ttk.Combobox(panel, textvariable=target, width=24, state="readonly")
    target_picker.grid(row=0, column=1, columnspan=2, sticky="ew")
    ttk.Button(panel, text="刷新列表", command=lambda: refresh()).grid(row=0, column=3, sticky="w", padx=(6, 0))
    label(1, "远征种子")
    ttk.Label(panel, textvariable=seed).grid(row=1, column=1, columnspan=2, sticky="w", pady=1)

    ttk.Label(panel, text="最小值").grid(row=2, column=1, sticky="w")
    ttk.Label(panel, text="最大值").grid(row=2, column=2, sticky="w")
    background = ttk.Style(panel).lookup("TFrame", "background")

    def slider(row, column, variable, upper):
        cell = ttk.Frame(panel)
        cell.grid(row=row, column=column, sticky="ew", padx=(0, 5))
        track = tk.Scale(cell, variable=variable, from_=0, to=upper, resolution=1,
                         orient="horizontal", showvalue=False, length=94,
                         background=background, highlightbackground=background)
        track.pack(side="left", fill="x", expand=True)
        ttk.Label(cell, textvariable=variable, width=4, anchor="e").pack(side="right")
        return track

    range_sliders = {}
    for row, key, title, minimum, maximum, upper, native in (
        (3, "count", "追加特性数量", count_min, count_max, DEFAULT_COUNT_LIMIT, None),
        (4, "ambition", "定居者雄心", ambition_min, ambition_max, 10, game_ambition),
        (5, "danger", "感染者危险度", danger_min, danger_max, 4, game_danger),
    ):
        label(row, title)
        pair = (slider(row, 1, minimum, upper), slider(row, 2, maximum, upper))
        range_sliders[key] = pair
        if native is not None:
            ttk.Checkbutton(panel, text="沿用游戏值", variable=native).grid(row=row, column=3, sticky="w", padx=(6, 0))

            def update_slider_state(*_args, tracks=pair, option=native):
                for track in tracks:
                    track.configure(state="disabled" if option.get() else "normal")

            native.trace_add("write", update_slider_state)
            update_slider_state()

    ttk.Label(panel, text="两端相同即固定该值。额外远征的危险度最高为 3。").grid(
        row=6, column=0, columnspan=4, sticky="w", pady=(2, 0))
    buttons = ttk.Frame(panel)
    buttons.grid(row=7, column=0, columnspan=4, sticky="ew", pady=(3, 0))
    ttk.Button(buttons, text="保存并抽取", command=lambda: store()).pack(side="left")
    ttk.Button(buttons, text="恢复游戏生成规则", command=lambda: reset()).pack(side="left", padx=8)
    ttk.Label(panel, textvariable=status, wraplength=490).grid(
        row=8, column=0, columnspan=4, sticky="w", pady=(3, 1))
    result_box = ttk.Frame(panel)
    result_box.grid(row=9, column=0, columnspan=4, sticky="ew")
    details = tk.Text(result_box, height=2, width=1, wrap="char", state="disabled", takefocus=False)
    scroll = ttk.Scrollbar(result_box, orient="vertical", command=details.yview)
    details.configure(yscrollcommand=scroll.set)
    details.pack(side="left", fill="x", expand=True)
    scroll.pack(side="right", fill="y")

    def display_details(text):
        details.configure(state="normal")
        details.delete("1.0", "end")
        details.insert("1.0", text)
        details.configure(state="disabled")

    def show_target(*_args):
        selected = next((name for name, item in targets.items() if str(item["seed"]) == seed.get()), "")
        target.set(selected)
        row = targets.get(selected)
        if row is None:
            display_details("")
            return
        active = "、".join(effects.get(ident, ident) for ident in row["modifiers"]) or "无"
        added = "、".join(effects.get(ident, ident) for ident in row.get("addedModifiers", []))
        result = f"最近生成记录：雄心 {row['ambition']:g}，危险档位 {row['danger']}，特性 {active}"
        display_details(f"{result}\n本次追加：{added}" if added else result)

    def select_target(_event):
        row = targets.get(target.get())
        if row is not None:
            seed.set(str(row["seed"]))

    def set_count_limit(pending=0):
        upper = max(len(effects) or DEFAULT_COUNT_LIMIT, count_min.get(), count_max.get(), pending)
        for track in range_sliders["count"]:
            track.configure(to=upper)

    def refresh():
        try:
            snapshot = read_record(directory / "observed.json")
            if snapshot and snapshot.get("version") != PLUGIN_VERSION:
                status.set("已显示已有记录。请使用本版本随附的远征关卡组件生成新结果。")
            else:
                status.set(observed_status(snapshot, saved))
            targets.clear()
            effects.clear()
            if snapshot:
                for row in reversed(snapshot.get("expeditions", [])):
                    targets[f"{row['title']}　[{row['seed']}]"] = row
                for item in snapshot.get("catalogue", []):
                    effects[item["id"]] = item["title"]
            set_count_limit()
            target_picker.configure(values=tuple(targets))
            show_target()
        except (OSError, ValueError, KeyError, TypeError) as error:
            status.set(f"读取游戏记录失败：{error}")

    def store():
        nonlocal saved
        try:
            if not seed.get():
                raise ValueError("请先从远征列表选择一场远征")
            ambition = ("", "") if game_ambition.get() else (str(ambition_min.get()), str(ambition_max.get()))
            danger = ("", "") if game_danger.get() else (str(danger_min.get()), str(danger_max.get()))
            profile = make_profile(seed.get(), *ambition, *danger, str(count_min.get()), str(count_max.get()))
            save_profile(directory, profile)
        except (OSError, ValueError) as error:
            status.set(f"保存失败：{error}")
            return
        saved = profile
        refresh()
        status.set("配置已保存。下次启动游戏并生成这场远征时可查看抽取结果。")

    def reset():
        nonlocal saved
        try:
            (directory / "profile.json").unlink(missing_ok=True)
        except OSError as error:
            status.set(f"清除失败：{error}")
            return
        saved = None
        count_min.set(0)
        count_max.set(0)
        ambition_min.set(0)
        ambition_max.set(10)
        danger_min.set(0)
        danger_max.set(4)
        game_ambition.set(True)
        game_danger.set(True)
        refresh()
        status.set("配置已清除。下次启动游戏时恢复游戏生成规则。")

    target_picker.bind("<<ComboboxSelected>>", select_target)
    seed.trace_add("write", show_target)
    try:
        saved = read_record(directory / "profile.json")
        if saved:
            seed.set(str(saved["seed"]))
            for key, lower, upper, native in (("count", count_min, count_max, None),
                                              ("ambition", ambition_min, ambition_max, game_ambition),
                                              ("danger", danger_min, danger_max, game_danger)):
                limits = saved.get(f"{key}Range")
                fixed = saved.get(key, int(bool(saved.get("modifier"))) if key == "count" else None)
                if limits is None and fixed is not None:
                    limits = {"min": fixed, "max": fixed}
                if limits is not None:
                    if key == "count":
                        set_count_limit(max(limits["min"], limits["max"]))
                    lower.set(limits["min"])
                    upper.set(limits["max"])
                    if native is not None:
                        native.set(False)
        refresh()
        if saved and saved["schema"] == 1:
            status.set("已载入上一版配置。点击「保存并抽取」可换成区间与随机特性配置。")
    except (OSError, ValueError, KeyError, TypeError, tk.TclError) as error:
        status.set(f"读取已有配置失败：{error}")
    return panel
