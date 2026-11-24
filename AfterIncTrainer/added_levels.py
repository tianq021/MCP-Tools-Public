"""Options for levels created with the session's Add button."""
import ctypes
from ctypes import wintypes
import json
import math
import os
from pathlib import Path
import tempfile
import uuid
import tkinter as tk
from tkinter import ttk

COMPONENT_VERSION = "26049821.14"
ICON_DIR = Path(__file__).resolve().parent / "trait_icons"
CATALOGUE_FILE = ICON_DIR.parent / "trait_catalogue_zh.json"
BUILD = "26049821"
DIRECTORY = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "AfterIncTrainer" / "added_levels"
DANGERS = ("无", "低", "中", "高", "很高")
FALLBACK_TRAITS = {
    "soldier_combat_debuff_motorway": {"title": "盲区", "dangerMin": 1},
    "zone_production_drop_sheep": {"title": "腐蹄疫", "family": "zone_production_drop_area",
                                  "excludes": ["zone_production_boost_sheep"]},
}


def read_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return default


def write_json(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temp = Path(stream.name)
    try:
        temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def make_options(ambition, danger, selected, catalogue, random_min="", random_max="", procedural_map=False):
    def count(text):
        text = text.strip()
        if not text.isascii() or not text.isdecimal() or len(text) > 10 or int(text) > 2147483647:
            raise ValueError("请同时填写每关特性总数的最少和最多值，或将两栏留空")
        return int(text)

    limited = bool(random_min.strip() or random_max.strip())
    lower, upper = (count(random_min), count(random_max)) if limited else (None, None)
    if limited and lower > upper:
        raise ValueError("最少特性总数不能大于最多特性总数")
    ambition = ambition.strip()
    if ambition == "游戏生成":
        ambition = ""
    if ambition and (not ambition.isascii() or not ambition.isdecimal() or len(ambition) > 8
                     or int(ambition) > 2**24):
        raise ValueError("雄心须为游戏可精确表示的非负整数")
    amount = int(ambition) if ambition else None
    grade = None if danger == "游戏生成" else DANGERS.index(danger)
    min_ambition, max_ambition = 0, float("inf")
    min_danger, max_danger = 0, len(DANGERS) - 1
    for index, ident in enumerate(selected):
        item = catalogue[ident]
        title = item["title"]
        min_ambition = max(min_ambition, item.get("ambitionMin", -1))
        max_ambition = min(max_ambition, item.get("ambitionMax", float("inf")))
        min_danger = max(min_danger, math.ceil(item.get("dangerMin", -1)))
        max_danger = min(max_danger, math.floor(item.get("dangerMax", len(DANGERS) - 1)))
        for other_id in selected[:index]:
            other = catalogue[other_id]
            pair = f"「{title}」与「{other['title']}」"
            if ident == other_id or title == other["title"]:
                raise ValueError(f"{pair}同名，不能重复勾选")
            if other_id in (item.get("excludes") or ()) or ident in (other.get("excludes") or ()):
                raise ValueError(f"{pair}互斥，游戏规定二者不能同时生效")
            if item.get("family") and item["family"] == other.get("family"):
                raise ValueError(f"{pair}互斥，同一特性组只能选择一项")
            for key, label in (("ambition", "雄心"), ("danger", "危险度")):
                lo = max(item.get(key+"Min", -1), other.get(key+"Min", -1))
                hi = min(item.get(key+"Max", float("inf")), other.get(key+"Max", float("inf")))
                if (math.ceil(lo) if key == "danger" else lo) > hi:
                    raise ValueError(f"{pair}没有共同的{label}范围")
        if min_ambition > max_ambition or min_danger > max_danger:
            raise ValueError(f"「{title}」没有可用的雄心或危险度范围")
        for value, key, name in ((amount, "ambition", "雄心"), (grade, "danger", "危险度")):
            if value is not None and not item.get(key+"Min", -1) <= value <= item.get(key+"Max", float("inf")):
                raise ValueError(f"「{title}」不适用于所选{name}（{trait_requirements(item)}）。可改为「游戏生成」以自动匹配。")
    if limited and len(selected) > upper:
        raise ValueError(f"已手选 {len(selected)} 项，超过每关最多 {upper} 项")
    if selected or amount is not None or grade is not None or limited or procedural_map:
        options = {"modifiers": list(selected), "ambition": amount, "danger": grade, "proceduralMap": procedural_map}
        if limited:
            options.update(totalMin=lower, totalMax=upper)
        return options
    return None


def session_file(handle, pid, required):
    data = read_json(DIRECTORY / "ready.json", {})
    if data.get("pid") == pid and data.get("version") == COMPONENT_VERSION and data.get("build") == BUILD:
        stamps = [ctypes.c_ulonglong() for _ in range(4)]
        if ctypes.windll.kernel32.GetProcessTimes(wintypes.HANDLE(handle), *(ctypes.byref(t) for t in stamps)):
            if stamps[0].value == data.get("start"):
                return DIRECTORY / f"levels-{pid}-{stamps[0].value}.json"
    if required:
        raise ValueError("请先安装本版 AfterIncLevelTraits.dll 并重启游戏一次，再添加自定义关卡")
    return None


def next_settings(previous, existing, added, options):
    settings = {str(seed): previous[str(seed)] for seed in existing if str(seed) in previous}
    if options:
        if options.get("totalMax") is not None:
            options = dict(options, batchId=uuid.uuid4().hex)
        settings.update({str(seed): options for seed in added})
    return settings


def readback(handle, pid, seeds):
    path = session_file(handle, pid, False)
    if not path:
        return ""
    desired = read_json(path, {})
    observed = read_json(path.with_suffix(".results.json"), {})
    messages = []
    for seed in seeds:
        key = str(seed)
        if key not in desired:
            continue
        row = observed.get(key, {})
        # Older .9 session records omitted the random count keys.
        defaults = {"randomMin": 0, "randomMax": 0, "proceduralMap": False, "totalMin": None, "totalMax": None, "batchId": None}
        if row.get("options") is None or defaults | row["options"] != defaults | desired[key]:
            messages.append(f"{seed} 等待地图生成")
        elif row.get("error"):
            messages.append(f"{seed}：{row['error']}")
        else:
            traits = "、".join(row["traits"]) or "无"
            rolled = row.get("randomTraits", [])
            suffix = f"；随机追加 {len(rolled)} 项" if desired[key].get("randomMax") else ""
            if rolled and desired[key].get("randomMax"):
                suffix += "（" + "、".join(rolled) + "）"
            if row.get("map"):
                layout = row["map"]
                suffix += f"；随机地图 {layout['land']} 块陆地、{layout['links']} 条通路"
                if "bridges" in layout:
                    suffix += f"，{layout['rivers']} 条河、{layout['bridges']} 座桥"
            messages.append(f"{seed}：雄心 {row['ambition']:g}，危险度 {DANGERS[row['danger']]}，特性共 {len(row['traits'])} 项（{traits}）{suffix}")
    return "；".join(messages)


class AddOptions(ttk.LabelFrame):
    def __init__(self, master):
        super().__init__(master, text="本次添加的关卡", padding=5)
        self.ambition = tk.StringVar(self, value="游戏生成")
        self.procedural_map = tk.BooleanVar(self, value=False)
        self.random_min = tk.StringVar(self)
        self.random_max = tk.StringVar(self)
        self.danger = tk.StringVar(self, value="游戏生成")
        self.catalogue = dict(FALLBACK_TRAITS)
        self.selected = set()
        self.icons = {}
        self.picker = None
        ttk.Label(self, text="定居者雄心").grid(row=0, column=0, sticky="w")
        ttk.Combobox(self, width=8, textvariable=self.ambition, values=("游戏生成", *range(16))).grid(row=0, column=1, sticky="w", padx=4)
        ttk.Label(self, text="危险度").grid(row=0, column=2)
        ttk.Combobox(self, state="readonly", width=8, textvariable=self.danger,
                     values=("游戏生成", *DANGERS)).grid(row=0, column=3, sticky="w", padx=4)
        ttk.Button(self, text="恢复游戏默认", command=self.reset).grid(row=0, column=4)
        quantities = ttk.Frame(self)
        quantities.grid(row=1, column=0, columnspan=5, sticky="w", pady=(5, 2))
        ttk.Label(quantities, text="每关特性总数").pack(side="left", padx=(0, 10))
        for label, variable in (("最少", self.random_min), ("最多", self.random_max)):
            ttk.Label(quantities, text=label).pack(side="left")
            ttk.Spinbox(quantities, from_=0, to=2147483647, width=5, textvariable=variable).pack(side="left", padx=4)
            ttk.Label(quantities, text="个").pack(side="left", padx=(0, 8))
        ttk.Checkbutton(quantities, text="地块随机（重建地图）", variable=self.procedural_map).pack(side="left", padx=8)
        self.summary = tk.StringVar(self)
        ttk.Button(self, text="选择特性…", command=self.open_picker).grid(row=2, column=0, sticky="w", pady=5)
        self.summary_label = ttk.Label(self, textvariable=self.summary, wraplength=330, compound="left")
        self.summary_label.grid(row=2, column=1, columnspan=4, sticky="w", padx=4)
        self.note = tk.StringVar(self)
        ttk.Label(self, textvariable=self.note, wraplength=500).grid(row=4, column=0, columnspan=5, sticky="w")
        self.columnconfigure(3, weight=1)
        self.refresh()

    def refresh(self):
        bundled = read_json(CATALOGUE_FILE, FALLBACK_TRAITS)
        self.catalogue = bundled
        try:
            catalogue = read_json(DIRECTORY / "catalogue.json", {})
            if catalogue:
                self.catalogue = {ident: bundled.get(ident, {}) | item for ident, item in catalogue.items()}
            self.note.set("总数包含原有、手选和随机特性。填范围后，同批关卡的非手选特性不重复。两栏留空沿用游戏数量。")
        except (OSError, ValueError, TypeError) as exc:
            self.note.set(f"特性目录读取失败：{exc}")
        self.selected.intersection_update(self.catalogue)
        self.update_summary()

    def icon_for(self, ident):
        if ident not in self.icons:
            path = ICON_DIR / f"{ident}.png"
            self.icons[ident] = (tk.PhotoImage(master=self, file=path)
                                 if path.parent == ICON_DIR and path.is_file() else None)
        return self.icons[ident]

    def update_summary(self):
        ordered = sorted(self.selected, key=lambda ident: (self.catalogue[ident]["title"], ident))
        preview = "、".join(self.catalogue[ident]["title"] for ident in ordered[:3])
        self.summary.set(f"已选 {len(ordered)} 项　{preview}{'…' if len(ordered) > 3 else ''}" if ordered else "未手选特性")
        images = [icon for ident in ordered[:3] if (icon := self.icon_for(ident))]
        strip = None
        if images:
            strip = tk.PhotoImage(master=self, width=22 * len(images), height=20)
            for i, image in enumerate(images):
                self.tk.call(str(strip), "copy", str(image), "-subsample", 2, 2, "-to", 22 * i, 0)
        self.summary_label.configure(image=strip or "")
        self.summary_image = strip

    def open_picker(self):
        if self.picker and self.picker.winfo_exists():
            self.picker.lift()
            return
        self.refresh()
        self.picker = TraitPicker(self)

    def reset(self):
        self.ambition.set("游戏生成")
        self.procedural_map.set(False)
        self.random_min.set("")
        self.random_max.set("")
        self.danger.set("游戏生成")
        self.selected.clear()
        self.update_summary()

    def options(self, selection=None):
        return make_options(self.ambition.get(), self.danger.get(),
                            sorted(self.selected) if selection is None else selection, self.catalogue,
                            self.random_min.get(), self.random_max.get(), self.procedural_map.get())


def trait_requirements(item):
    constraints = []
    for key, label in (("ambition", "雄心"), ("danger", "危险度")):
        low = item.get(key + "Min", -1)
        high = item.get(key + "Max", float("inf"))
        ceiling = 4 if key == "danger" else 2**24
        def display(value):
            if key == "danger" and value in range(5):
                return DANGERS[int(value)]
            return f"{value:g}"
        if low > 0:
            constraints.append(f"{label} ≥ {display(low)}")
        if high < ceiling:
            constraints.append(f"{label} ≤ {display(high)}")
    return "，".join(constraints) or "无"


class TraitPicker(tk.Toplevel):
    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel
        self.title("选择关卡特性")
        self.transient(panel.winfo_toplevel())
        width = min(840, self.winfo_screenwidth() - 48)
        height = min(540, self.winfo_screenheight() - 100)
        self.geometry(f"{width}x{height}")
        self.minsize(min(440, width), min(280, height))
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        controls = ttk.Frame(self, padding=(12, 10))
        controls.grid(row=0, column=0, sticky="ew")
        controls.columnconfigure(1, weight=1)
        ttk.Label(controls, text="搜索特性").grid(row=0, column=0)
        self.query = tk.StringVar(self)
        search = ttk.Entry(controls, textvariable=self.query)
        search.grid(row=0, column=1, sticky="ew", padx=8)
        self.only_selected = tk.BooleanVar(self)
        ttk.Checkbutton(controls, text="只看已选", variable=self.only_selected, command=self.render).grid(row=0, column=2)

        body = ttk.Frame(self, padding=(12, 0))
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        ttk.Style(self).configure("TraitPicker.Treeview", rowheight=44)
        self.tree = ttk.Treeview(body, columns=("check", "description", "limits"), show="tree headings",
                                 style="TraitPicker.Treeview", selectmode="browse")
        for key, title, size in (("#0", "特性", 182), ("check", "勾选", 62),
                                 ("description", "效果说明", 326), ("limits", "适用条件", 130)):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=size, minwidth=48, stretch=key != "check", anchor="center" if key == "check" else "w")
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<Button-1>", self.click_row)
        self.tree.bind("<Double-Button-1>", lambda _: "break")
        self.tree.bind("<space>", lambda _: self.toggle(self.tree.focus()))
        self.tree.bind("<Motion>", lambda event: self.show_detail(self.tree.identify_row(event.y)))
        self.tree.bind("<<TreeviewSelect>>", lambda _: self.show_detail(self.tree.focus()))
        self.tree.tag_configure("unavailable", foreground="#755449")

        self.detail = tk.StringVar(self)
        detail = ttk.Label(self, textvariable=self.detail, wraplength=width-40, justify="left")
        detail.grid(row=2, column=0, sticky="ew", padx=14, pady=(7, 0))
        detail.bind("<Configure>", lambda event: detail.configure(wraplength=max(100, event.width-4)))
        self.reasons = {}

        footer = ttk.Frame(self, padding=(12, 10))
        footer.grid(row=3, column=0, sticky="ew")
        self.count = tk.StringVar(self)
        ttk.Label(footer, textvariable=self.count).pack(side="left")
        ttk.Button(footer, text="完成", command=self.destroy).pack(side="right")
        ttk.Button(footer, text="清除勾选", command=self.clear).pack(side="right", padx=8)
        ttk.Button(footer, text="检查冲突", command=self.check_selection).pack(side="right")
        self.query.trace_add("write", lambda *_: self.render())
        self.bind("<Escape>", lambda _: self.destroy())
        self.render()
        self.grab_set()
        search.focus_set()

    def render(self):
        self.tree.delete(*self.tree.get_children())
        query = self.query.get().strip().casefold()
        for ident, item in sorted(self.panel.catalogue.items(), key=lambda pair: pair[1]["title"]):
            checked = ident in self.panel.selected
            description = item.get("description") or "暂无效果说明"
            if (self.only_selected.get() and not checked) or query not in f"{item['title']} {ident} {description}".casefold():
                continue
            self.tree.insert("", "end", iid=ident, text=item["title"], image=self.panel.icon_for(ident) or "",
                             values=("", description, trait_requirements(item)))
        self.update_states()
        self.detail.set("")
        visible = self.tree.get_children()
        if visible:
            self.show_detail(visible[0])
        self.update_count()

    def update_states(self):
        self.reasons.clear()
        chosen = sorted(self.panel.selected)
        for ident in self.tree.get_children():
            checked = ident in self.panel.selected
            if not checked:
                try:
                    self.panel.options([*chosen, ident])
                except ValueError as exc:
                    self.reasons[ident] = str(exc)
            blocked = ident in self.reasons
            self.tree.set(ident, "check", "不可选" if blocked else ("☑" if checked else "☐"))
            self.tree.item(ident, tags=("unavailable",) if blocked else ())

    def show_detail(self, ident):
        if ident and self.tree.exists(ident):
            item = self.panel.catalogue[ident]
            description = item.get("description") or "暂无效果说明"
            reason = self.reasons.get(ident)
            self.detail.set(f"{item['title']}　{description}" + (f"\n{reason}" if reason else ""))

    def check_selection(self):
        try:
            self.panel.options()
        except ValueError as exc:
            self.detail.set(str(exc))
        else:
            size = len(self.panel.selected)
            self.detail.set(f"已检查 {size} 项手选特性，与当前设置相容。" if size else "当前没有手选特性。")

    def update_count(self):
        self.count.set(f"显示 {len(self.tree.get_children())} 项　·　已选 {len(self.panel.selected)} 项")

    def click_row(self, event):
        ident = self.tree.identify_row(event.y)
        if ident and self.tree.identify_region(event.x, event.y) in ("tree", "cell"):
            self.tree.focus_set()
            self.tree.focus(ident)
            self.tree.selection_set(ident)
            return self.toggle(ident)

    def toggle(self, ident):
        if ident:
            selected = self.panel.selected
            if ident in selected:
                selected.remove(ident)
                if self.only_selected.get():
                    successor = self.tree.next(ident) or self.tree.prev(ident)
                    self.tree.delete(ident)
                    if successor:
                        self.tree.focus(successor)
                        self.tree.selection_set(successor)
            else:
                try:
                    self.panel.options([*sorted(selected), ident])
                except ValueError as exc:
                    self.detail.set(str(exc))
                    return "break"
                selected.add(ident)
            self.panel.update_summary()
            self.update_states()
            self.detail.set("")
            self.show_detail(self.tree.focus() if not self.tree.exists(ident) else ident)
            self.update_count()
        return "break"

    def clear(self):
        self.panel.selected.clear()
        self.panel.update_summary()
        self.render()

