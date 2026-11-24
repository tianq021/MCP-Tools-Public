"""生成 4 份史莱姆/市场文档（数据来自游戏内桥接，规则来自反编译源码）。

用法（游戏需在运行且已读档，桥接 v0.8.2）：
    python -B tool\\gen_slime_docs.py

产出：
    docs/data/*.json            原始数据快照（可复查、可 diff）
    docs/SLIMES.md              史莱姆定义总表
    docs/SLIME_FOODS.md         食物分组与最爱食物反查
    docs/LARGO_COMBOS.md        大型史莱姆组合与产值
    docs/MARKET.md              结晶市场价格机制与当前行情

静态知识来源（反编译源码 out/decompiled/full/）：
    Identifiable.cs      按 ID 后缀划分食物组：_FRUIT / _VEGGIE / HEN|ROOSTER / _TOFU
    SlimeDiet.cs         最爱食物 favoriteProductionCount = 2
    SlimeEat.cs          FinishChomp 遍历每种产出物，逐条 EatAndProduce（所以 Largo 喂最爱=两种结晶各 2 个）
    EconomyDirector.cs   价格 = baseValue × num × num2 × num3
    Noise.Noise.cs       GetNoise ∈ [0,1] → num2/num3 ∈ [0.7, 1.3]
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "mcp"))

import bridge_client as bc  # noqa: E402

DOCS = os.path.join(ROOT, "docs")
DATA = os.path.join(DOCS, "data")

FOOD_GROUP_CN = {
    "FRUIT": "水果",
    "VEGGIES": "蔬菜",
    "MEAT": "肉类",
    "GINGER": "金姜",
    "PLORTS": "结晶",
    "NONTARRGOLD_SLIMES": "其他史莱姆",
}
SPECIAL_NOTES = {
    "FIRE_SLIME": "不吃普通食物：焚化炉把食物烧成灰，火焰史莱姆吃灰产火焰结晶（Incinerate.cs）",
    "PUDDLE_SLIME": "靠吸水生存，吃水吐水滴结晶（SlimeEatWater.cs），需要水池/水源",
    "GOLD_SLIME": "只吃金姜，一次产 5 个黄金结晶；玩家靠近会逃跑并很快消失（GoldSlimeFlee.cs），不能养",
    "LUCKY_SLIME": "吃活鸡类掉新币，不产结晶；被靠近或命中后约 600 world-time 单位消失，不能养",
    "TARR_SLIME": "焦油怪：吃史莱姆和其他结晶，敌对，不可饲养；三种以上史莱姆混养/喂错结晶会产生",
    "GLITCH_SLIME": "故障史莱姆（特殊/模拟区内容），不参与正常牧场循环",
    "GLITCH_TARR_SLIME": "故障焦油怪（特殊/模拟区内容）",
    "QUICKSILVER_SLIME": "水银史莱姆（Mochi 相关内容）：不在常规牧场循环里，桥接的市场定价表中也没有水银结晶",
}


def fetch(out_dir: str) -> dict:
    """从桥接抓取文档所需的全部数据。"""
    jobs = {
        "slimes": ("sr_slime_info", {"include_largo": True}),
        "items_slime": ("sr_item_catalog", {"category": "slime", "limit": 200}),
        "items_food": ("sr_item_catalog", {"category": "food", "limit": 200}),
        "items_plort": ("sr_item_catalog", {"category": "plort", "limit": 200}),
        "market_advice": ("sr_market_advice", {}),
        "market": ("sr_market", {}),
        "time": ("sr_time", {}),
        "saves": ("sr_saves", {}),
    }
    out: dict = {}
    for key, (tool, args) in jobs.items():
        res = bc.call(tool, args)
        if not res.get("ok"):
            raise SystemExit(f"{tool} 失败：{json.dumps(res, ensure_ascii=False)[:300]}")
        out.setdefault("_bridge", res.get("bridge"))
        out[key] = res["data"]
        with open(os.path.join(out_dir, f"{key}.json"), "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)
    return out


def food_group_of(item_id: str) -> str:
    """按 Identifiable.cs 的静态初始化规则判组。"""
    if item_id == "GINGER_VEGGIE":
        return "GINGER"
    if item_id.endswith("_FRUIT"):
        return "FRUIT"
    if item_id.endswith("_VEGGIE"):
        return "VEGGIES"
    if item_id.endswith("HEN") or item_id.endswith("ROOSTER"):
        return "MEAT"
    if item_id.endswith("_TOFU"):
        return "TOFU"
    return "OTHER"


def half_up(x: float) -> int:
    """与 C# Mathf.RoundToInt 足够接近的取整（正数四舍五入）。"""
    return int(x + 0.5)


def infer_full_saturation(sat: float, ratio: float) -> float | None:
    """由 当前饱和 与 饱和度比例 反推饱和阈值；优先对齐到整数/常见值。

    桥接把 saturation 四舍五入到 2 位、ratio 到 3 位，所以候选值只要落在
    「存在 sat' ∈ [sat-0.005, sat+0.005] 使 round(sat'/cand, 3) == ratio」即可。
    """
    if ratio <= 0:
        return None
    raw = sat / ratio
    nice = (5, 10, 12, 15, 20, 24, 25, 30, 40, 50, 60, 80, 100, 120, 150,
            200, 250, 300, 400, 500, 750, 1000, 1500, 2000, 2500, 3000,
            5000, 10000, 20000, 25000, 50000, 100000)
    cands = sorted(set([round(raw), half_up(raw), *nice]),
                   key=lambda c: (0 if c in nice else 1, abs(c - raw)))
    for cand in cands:
        if cand <= 0:
            continue
        if abs(sat / cand - ratio) <= 0.0005 + 0.005 / cand:
            return float(cand)
    return round(raw, 1)


def clamp01(x: float) -> float:
    return 0.0 if x < 0 else (1.0 if x > 1 else x)


def build_models(data: dict) -> dict:
    slime_names = {i["item_id"]: i["name"] for i in data["items_slime"]["items"]}
    food_names = {i["item_id"]: i["name"] for i in data["items_food"]["items"]}
    plort_names = {i["item_id"]: i["name"] for i in data["items_plort"]["items"]}

    def name_of(item_id: str) -> str:
        return slime_names.get(item_id) or food_names.get(item_id) or plort_names.get(item_id) or item_id

    def plort_name(item_id: str) -> str:
        return plort_names.get(item_id) or item_id

    # ---- 市场：基准价 / 饱和 / 今日噪声因子 ----
    market: dict[str, dict] = {}
    base_value: dict[str, float] = {}
    for row in data["market_advice"]["advice"]:
        iid = row["item_id"]
        base = float(row["base_value"])
        price = int(row["unit_price"])
        sat = float(row["saturation"])
        ratio = float(row["saturation_ratio"])
        full = infer_full_saturation(sat, ratio)
        num = 1.0 + clamp01((full - sat) / full) if full else 1.0
        noise = (price / base / num) if (base and num) else None
        base_value[iid] = base
        market[iid] = {
            "id": iid,
            "name": row["name"],
            "base": base,
            "price": price,
            "change": row.get("change"),
            "sat": sat,
            "ratio": ratio,
            "full": full,
            "num": round(num, 4),
            "noise": round(noise, 4) if noise else None,
            "floor_today": half_up(base * noise) if noise else None,
            "advice": row["advice"],
            "theory_min": half_up(base * 0.49),
            "theory_max": half_up(base * 3.38),
            "to_fill": max(0, half_up((full or 0) - sat)),
        }

    # ---- 史莱姆 ----
    slimes = {s["item_id"]: dict(s) for s in data["slimes"]["slimes"]}
    base_slimes = [s for s in slimes.values() if not s["is_largo"]]
    largos = [s for s in slimes.values() if s["is_largo"]]

    # 食物组数量统计（用于作物规划）
    group_slime_count: dict[str, int] = {}
    for s in base_slimes:
        for g in s["food_groups"]:
            group_slime_count[g] = group_slime_count.get(g, 0) + 1

    # 最爱食物反查
    fav_to_slimes: dict[str, list[str]] = {}
    for s in base_slimes:
        for fav in s["favorites"]:
            fav_to_slimes.setdefault(fav, []).append(s["item_id"])

    # 食物表
    foods = []
    for item in data["items_food"]["items"]:
        iid = item["item_id"]
        group = food_group_of(iid)
        eater_ids = [s["item_id"] for s in base_slimes if group in s["food_groups"] or iid in s["additional_foods"]]
        if iid.endswith("_TOFU"):
            group = "TOFU"
            eater_ids = [s["item_id"] for s in base_slimes if iid in s["additional_foods"]]
        foods.append({
            "id": iid,
            "name": item["name"],
            "group": group,
            "group_cn": FOOD_GROUP_CN.get(group, "额外"),
            "fav_of": fav_to_slimes.get(iid, []),
            "eaters": eater_ids,
        })

    # Largo 产值（基准价；一次最爱投喂 = 2 × 两个净结晶基准价）
    for lg in largos:
        val = sum(base_value.get(p, 0) for p in lg["produces"] if p in base_value)
        lg["value_per_feed"] = half_up(val * 2)
        lg["single_group"] = len(lg["food_groups"]) == 1
        lg["display"] = name_of(lg["item_id"])

    # 基础史莱姆价值
    for s in base_slimes:
        s["display"] = name_of(s["item_id"])
        s["plort_value"] = base_value.get(s["produces"][0], 0) if s["produces"] else 0

    return {
        "slime_names": slime_names,
        "food_names": food_names,
        "plort_names": plort_names,
        "name_of": name_of,
        "plort_name": plort_name,
        "market": market,
        "base_value": base_value,
        "slimes": slimes,
        "base_slimes": base_slimes,
        "largos": largos,
        "foods": foods,
        "group_slime_count": group_slime_count,
    }


# ---------------------------------------------------------------- 文档生成

def header(title: str, intro: list[str], meta: dict) -> list[str]:
    return [
        f"# {title}",
        "",
        *intro,
        "",
        f"> 数据快照：游戏内第 **{meta['day']} 天 {meta['hour_real']}**（现实 {meta['wall']}）"
        f"，存档 `{meta['save']}`，桥接 **{meta['bridge']}**，史莱姆定义 **{meta['slime_count']}** 条。"
        f"本文件由 `tool/gen_slime_docs.py` 生成，游戏数据变化后可重新生成。",
        "",
        "---",
        "",
    ]


def write_slimes(m: dict, meta: dict) -> str:
    L: list[str] = header(
        "史莱姆定义总表",
        [
            "Slime Rancher 1 全部史莱姆的游戏内定义：食物组、最爱食物、产出结晶（附基准价）、能否 Largo，以及特殊史莱姆机制。",
            "",
            "相关文档：[食物与最爱](SLIME_FOODS.md) · [大型组合](LARGO_COMBOS.md) · [市场机制](MARKET.md)",
        ],
        meta,
    )
    L += [
        "## 1. 字段说明",
        "",
        "| 字段 | 含义 |",
        "| --- | --- |",
        "| 食物组 | 该史莱姆能吃的食物大类：水果 / 蔬菜 / 肉类（粉红是全吃）。食物组由物品 ID 后缀判定（`_FRUIT` / `_VEGGIE` / `HEN`·`ROOSTER`），见 `Identifiable.cs` |",
        "| 最爱食物 | 喂它时产出双倍（`favoriteProductionCount = 2`，见 `SlimeDiet.cs`） |",
        "| 产出结晶 | 吃常规食物时产出的结晶；括号内为结晶**基准价** |",
        "| 可 Largo | 能否与另一种史莱姆合体成大型史莱姆 |",
        "",
        "## 2. 常规史莱姆（14 种，按结晶基准价降序）",
        "",
        "| 史莱姆 | ID | 食物组 | 最爱食物 | 产出结晶（基准价） | 可 Largo |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    normal = sorted([s for s in m["base_slimes"] if s["can_largofy"]],
                    key=lambda s: (-s["plort_value"], s["item_id"]))
    for s in normal:
        groups = "+".join(FOOD_GROUP_CN.get(g, g) for g in s["food_groups"]) or "—"
        favs = "、".join(m["name_of"](f) for f in s["favorites"]) or "无"
        plorts = "、".join(
            f"{m['plort_name'](p)}（{m['base_value'].get(p, 0):g}）"
            for p in s["produces"]) or "—"
        L.append(f"| {s['display']} | `{s['item_id']}` | {groups} | {favs} | {plorts} | ✔ |")

    L += [
        "",
        "## 3. 特殊史莱姆（8 种，常规方式养不了 / 机制特殊）",
        "",
        "| 史莱姆 | ID | 食物定义（游戏数据） | 产出 | 机制说明 |",
        "| --- | --- | --- | --- | --- |",
    ]
    specials = [s for s in m["base_slimes"] if not s["can_largofy"]]
    for s in sorted(specials, key=lambda x: x["item_id"]):
        groups = "+".join(FOOD_GROUP_CN.get(g, g) for g in s["food_groups"]) or "无"
        plorts = []
        for p in s["produces"]:
            if p.endswith("_SLIME") or p == "NONE":
                plorts.append("—（不产结晶）")
            else:
                plorts.append(m["plort_name"](p))
        plort_text = "、".join(plorts) or "—（特殊机制产出）"
        note = SPECIAL_NOTES.get(s["item_id"], "")
        L.append(f"| {s['display']} | `{s['item_id']}` | {groups} | {plort_text} | {note} |")

    L += [
        "",
        "## 4. 需要注意的规则",
        "",
        "1. **粉红**能吃水果/蔬菜/肉，但**没有最爱食物**——粉红 Largo 想双倍产出，只能靠搭档的最爱食物；",
        "2. **特殊史莱姆不可 Largo**：黄金、招财猫、焦油怪、火焰、水滴、水银、故障都不参与合体；",
        "3. **焦油怪不是一种史莱姆**：它是混养/喂错结晶后的惩罚状态，出现后会吃光同围栏里的史莱姆；",
        "4. **Gordo 巨型史莱姆**不在本表（它不是可养个体）：用 `sr_gordos` 查全图状态、用 `sr_slime_info` 查它的喂食偏好；",
        "5. 本表来自**游戏定义数据**，与实际刷新区域无关——某史莱姆在哪个区域出现由存档进度和刷新表决定，桥接不暴露该表。",
        "",
        "## 5. 相关工具",
        "",
        "| 工具 | 用途 |",
        "| --- | --- |",
        "| `sr_slime_info` | 查任意史莱姆/大型史莱姆的完整定义（本文原始数据） |",
        "| `sr_slimes` | 查当前内存中实际存在的史莱姆（种类/位置/饥饿/feral） |",
        "| `sr_corrals` | 逐个围栏体检（史莱姆数量/饥饿/落晶/收集器/喂食器） |",
        "| `sr_gordos` | 全图 Gordo 的已喂进度与是否爆开 |",
        "",
    ]
    return "\n".join(L) + "\n"


def write_foods(m: dict, meta: dict) -> str:
    L: list[str] = header(
        "食物分组与最爱食物",
        [
            "全部 20 种食物、它们所属的食物组、分别是哪些史莱姆的**最爱**（喂最爱产出双倍），以及 Largo 的双倍规则。",
            "",
            "相关文档：[史莱姆定义](SLIMES.md) · [大型组合](LARGO_COMBOS.md) · [市场机制](MARKET.md)",
        ],
        meta,
    )
    L += [
        "## 1. 食物组怎么判定",
        "",
        "游戏按物品 ID 后缀自动归类（`Identifiable.cs` 静态初始化）：",
        "",
        "| 食物组 | 判定规则 | 本作物品 |",
        "| --- | --- | --- |",
        "| 水果 | ID 以 `_FRUIT` 结尾 | 方方莓、薄荷芒果、刺刺梨、饱饱果、相位柠檬、库卡多巴果 |",
        "| 蔬菜 | ID 以 `_VEGGIE` 结尾 | 胡萝卜、欧卡薯、银萝卜、甜心菜、奇怪洋葱 |",
        "| 肉类 | ID 以 `HEN` / `ROOSTER` 结尾 | 非常母鸡、公鸡、老母鸡、老公鸡、母岩鸡、母石楠鸡、彩色母鸡 |",
        "| 金姜 | 单独一组，只有 `GINGER_VEGGIE` | 金姜（黄金史莱姆专用） |",
        "| 豆腐（额外） | ID 以 `_TOFU` 结尾 | 辣豆腐：任何史莱姆都能吃，但不算任何人的最爱 |",
        "",
        "## 2. 食物总表",
        "",
        "| 食物 | ID | 食物组 | 是谁的最爱 | 能吃它的史莱姆数 |",
        "| --- | --- | --- | --- | --- |",
    ]
    order = {"FRUIT": 0, "VEGGIES": 1, "MEAT": 2, "GINGER": 3, "TOFU": 4, "OTHER": 5}
    for f in sorted(m["foods"], key=lambda x: (order.get(x["group"], 9), x["id"])):
        fav = "、".join(m["name_of"](s) for s in f["fav_of"]) or "—（不是任何人的最爱）"
        L.append(f"| {f['name']} | `{f['id']}` | {f['group_cn']} | {fav} | {len(f['eaters'])} |")

    L += [
        "",
        "## 3. 最爱食物反查（哪种食物能触发谁的双倍）",
        "",
    ]
    fav_items = sorted(m["foods"], key=lambda x: (-len(x["fav_of"]), x["id"]))
    for f in fav_items:
        if not f["fav_of"]:
            continue
        slimes = "、".join(m["name_of"](s) for s in f["fav_of"])
        L.append(f"- **{f['name']}**（`{f['id']}`，{f['group_cn']}）→ {slimes}")
    L += [
        "",
        "## 4. Largo 的双倍规则（关键）",
        "",
        "大型史莱姆吃**任意一方的最爱食物**时，会把它的**两种产出结晶都产 2 个**：",
        "",
        "- `SlimeDiet.cs`：`NumToProduce()` 对最爱食物返回 `favoriteProductionCount = 2`；",
        "- `SlimeEat.cs` 的 `FinishChomp`：遍历该食物对应的**每一种产出物**条目，逐条 `EatAndProduce`。",
        "",
        "所以「荧光 + 水晶」喂**方方莓**（荧光的最爱）一次 → **2 个荧光结晶 + 2 个水晶结晶**。",
        "这也是为什么给 Largo 选搭档时，只需要搭档的结晶值钱、食物能种得出来即可。",
        "",
        "## 5. 按食物组规划种植",
        "",
        "| 食物组 | 常规史莱姆数 | 说明 |",
        "| --- | --- | --- |",
    ]
    for group in ("FRUIT", "VEGGIES", "MEAT"):
        cnt = m["group_slime_count"].get(group, 0)
        names = [m["name_of"](s["item_id"]) for s in m["base_slimes"]
                 if s["can_largofy"] and group in s["food_groups"]]
        L.append(f"| {FOOD_GROUP_CN[group]} | {cnt} | {'、'.join(names)} |")
    L += [
        "",
        "建议：**水果系最划算**——荧光、蜂蜜、量子、旋风都吃水果且都只吃水果，一块方方莓（或刺刺梨/相位柠檬）花园就能同时满足两边的双倍条件；肉类要养鸡、蔬菜要单独种，前期成本更高。",
        "",
        "## 6. 相关工具",
        "",
        "| 工具 | 用途 |",
        "| --- | --- |",
        "| `sr_slime_info` | 查史莱姆的食物组/最爱/产出（本文原始数据） |",
        "| `sr_item_catalog` | 查物品 ID 与中文名 |",
        "| `sr_plots` | 查花园地块种了什么、还有多久成熟 |",
        "",
    ]
    return "\n".join(L) + "\n"


def write_largos(m: dict, meta: dict) -> str:
    L: list[str] = header(
        "大型史莱姆（Largo）组合表",
        [
            f"全部 **{len(m['largos'])}** 种 Largo：基础组合、食物组、最爱食物、产出结晶，以及**每次投喂最爱食物的基准产值**"
            "（= 2 × 两种结晶基准价之和）。",
            "",
            "相关文档：[史莱姆定义](SLIMES.md) · [食物与最爱](SLIME_FOODS.md) · [市场机制](MARKET.md)",
        ],
        meta,
    )
    L += [
        "## 1. 怎么用这张表",
        "",
        "- **基准产值**用结晶基准价算，不受当天行情波动影响，适合横向比较组合优劣；实际收益请用 `sr_market_advice` 的当前价再算一次；",
        "- **单组食物**= 该 Largo 只吃一个大类（通常只有水果），意味着**一块地一种作物**就能稳定双倍；",
        "- 合体后 Largo 会吃两类食物，但**喂任意一方的最爱都能双倍**（见 [食物文档](SLIME_FOODS.md#4-largo-的双倍规则关键)）。",
        "",
        "## 2. 按基准产值排序（前 20）",
        "",
        "| Largo | 组合 | 食物组 | 最爱食物 | 产出结晶 | 每次投喂基准产值 | 单组食物 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    ranked = sorted(m["largos"], key=lambda x: (-x["value_per_feed"], x["item_id"]))
    for lg in ranked[:20]:
        parents = " + ".join(m["name_of"](p) for p in lg["base_slimes"])
        groups = "+".join(FOOD_GROUP_CN.get(g, g) for g in lg["food_groups"])
        favs = "、".join(m["name_of"](f) for f in lg["favorites"]) or "无"
        plorts = "、".join(m["plort_name"](p) for p in lg["produces"])
        L.append(f"| {lg['display']} | {parents} | {groups} | {favs} | {plorts} | **{lg['value_per_feed']}** | {'✔' if lg['single_group'] else ''} |")

    L += [
        "",
        "## 3. 完整组合表（按 ID 排序）",
        "",
        "| Largo | ID | 组合 | 食物组 | 最爱食物 | 产出结晶 | 基准产值/次 | 单组 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for lg in sorted(m["largos"], key=lambda x: x["item_id"]):
        parents = " + ".join(m["name_of"](p) for p in lg["base_slimes"])
        groups = "+".join(FOOD_GROUP_CN.get(g, g) for g in lg["food_groups"])
        favs = "、".join(m["name_of"](f) for f in lg["favorites"]) or "无"
        plorts = "、".join(m["plort_name"](p) for p in lg["produces"])
        L.append(f"| {lg['display']} | `{lg['item_id']}` | {parents} | {groups} | {favs} | {plorts} | {lg['value_per_feed']} | {'✔' if lg['single_group'] else ''} |")

    # 荧光专题
    phosphor = [lg for lg in m["largos"] if "PHOSPHOR_SLIME" in lg["base_slimes"]]
    L += [
        "",
        "## 4. 专题：荧光史莱姆的搭档怎么选",
        "",
        "荧光（`PHOSPHOR_SLIME`）吃水果、最爱方方莓，喂方方莓即可让两种结晶都双倍。搭档选择只剩一个问题：**它的结晶值多少**。",
        "",
        "| 荧光 + 搭档 | 搭档基准价 | 每次投喂基准产值 | 食物组 | 备注 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for lg in sorted(phosphor, key=lambda x: -x["value_per_feed"]):
        partner = [p for p in lg["base_slimes"] if p != "PHOSPHOR_SLIME"][0]
        ps = m["slimes"][partner]
        pv = m["base_value"].get(ps["produces"][0], 0) if ps["produces"] else 0
        groups = "+".join(FOOD_GROUP_CN.get(g, g) for g in lg["food_groups"])
        if lg["single_group"]:
            note = "只用水果，最省事"
        elif len(lg["food_groups"]) >= 3:
            note = "粉红全食物组（喂方方莓仍可双倍）"
        else:
            note = "需两种食物组（喂方方莓仍可双倍）"
        L.append(f"| {lg['display']} | {pv:g} | **{lg['value_per_feed']}** | {groups} | {note} |")

    L += [
        "",
        "结论：**荧光 + 旋风 / 藤蔓 / 马赛克**（搭档基准价 50）收益最高；其中**旋风**只吃水果，是后期最省事的组合。",
        "前期只有牧场/礁石/采石场时，可用**荧光 + 水晶**（水晶基准价 40），收益已经比粉红/岩石高一个量级。",
        "",
        "## 5. 养 Largo 的注意事项",
        "",
        "1. **一个围栏只养一种 Largo**：Largo 吃到「它不产出的结晶」会变成焦油怪（`SlimeDiet.cs` 里 `becomesId = TARR_SLIME`），所以别混养第三种史莱姆，并尽快收走地上的结晶（**集晶器**很重要）；",
        "2. **荧光怕光**：围栏升级里有太阳盾（Solar Shield），或把荧光养在山洞（Grotto）；",
        "3. **围栏升级顺序**：高墙 → 集晶器 → 喂食器 → 太阳盾；",
        "4. **喂最爱才双倍**：普通作物只产 1+1，双倍完全依赖最爱食物，所以花园里优先种搭档/荧光的最爱。",
        "",
    ]
    return "\n".join(L) + "\n"


def write_market(m: dict, meta: dict) -> str:
    rows = sorted(m["market"].values(), key=lambda r: (-r["base"], r["id"]))
    advice_cn = {"SELL_NOW": "现在卖", "HOLD": "持有", "WAIT": "等"}
    L: list[str] = header(
        "结晶市场价格机制与行情",
        [
            "结晶定价的完整公式（反编译源码实证）、饱和机制、价格上下限，以及生成时的实时行情与卖出建议。",
            "",
            "相关文档：[史莱姆定义](SLIMES.md) · [食物与最爱](SLIME_FOODS.md) · [大型组合](LARGO_COMBOS.md)",
        ],
        meta,
    )
    L += [
        "## 1. 定价公式",
        "",
        "```",
        "单价 = baseValue × num × num2 × num3        （结果四舍五入到整数）",
        "```",
        "",
        "| 因子 | 含义 | 取值范围 |",
        "| --- | --- | --- |",
        "| `baseValue` | 该结晶的基准价（游戏资产常量） | 见下表 |",
        "| `num` | 饱和加成：`1 + clamp01((饱和阈值 − 当前饱和) / 饱和阈值)` | **1 ~ 2** |",
        "| `num2` | 全局噪声：`PerlinNoise(天, 种子, -10000) + 0.7` | **0.7 ~ 1.3** |",
        "| `num3` | 单品噪声：`PerlinNoise(天, 种子, 结晶ID哈希) + 0.7` | **0.7 ~ 1.3** |",
        "",
        "源码：`EconomyDirector.cs` 的 `GetTargetValue()`；噪声范围由 `Noise.Noise.cs` 的",
        "`return (float)(32.0 * (n0+n1+n2+n3) + 1.0) * 0.5f` 保证输出 ∈ [0,1]，乘 0.6 再加 0.7。",
        "",
        "由此可得：",
        "",
        "| 极值 | 公式 | 含义 |",
        "| --- | --- | --- |",
        "| 理论最低 | `baseValue × 0.49` | 饱和拉满（num=1）且两项噪声都在谷底 |",
        "| 理论最高 | `baseValue × 3.38` | 市场全空（num=2）且两项噪声都在顶 |",
        "",
        "## 2. 饱和机制",
        "",
        "| 规则 | 数值 | 源码位置 |",
        "| --- | --- | --- |",
        "| 初始饱和 | 饱和阈值的 **50%** | `EconomyDirector.InitModel` |",
        "| 每卖 1 个结晶 | 饱和 **+1**（不是按整批） | `RegisterSold` |",
        "| 每游戏小时 | 饱和 **×0.75**（即恢复 25%） | `ResetPrices` |",
        "| 价格重算 | 每游戏小时一次；噪声因子按**天**固定 | `Update` / `GetTargetValue` |",
        "| 闭市 | 每天开头 5 分钟（`hour × 60 < 5`） | `IsMarketShutdown` |",
        "| 动态价格开关 | 仅 CLASSIC 模式动态；其他模式恒为 `baseValue × 1.5` | `GetModeSettings().plortMarketDynamic` |",
        "",
        "结论：把一种结晶**卖到饱和阈值**就吃光了涨价加成（num→1），之后再卖只会维持在地板价；",
        "但饱和每小时恢复 25%，按本机实测（1 游戏小时 ≈ 75 秒现实），**卖爆后等几分钟价格就会明显回来**。",
        "",
        "## 3. 各结晶价格表（生成时行情）",
        "",
        "| 结晶 | ID | 基准价 | 理论最低 | 理论最高 | 今日满饱和地板 | 当前价 | 当前饱和 / 阈值 | 拉满还需 | 建议 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        full = f"{r['full']:g}" if r["full"] else "?"
        L.append(
            f"| {r['name']} | `{r['id']}` | {r['base']:g} | {r['theory_min']} | {r['theory_max']} | "
            f"{r['floor_today'] if r['floor_today'] is not None else '?'} | **{r['price']}** | "
            f"{r['sat']:g} / {full}（{r['ratio']*100:.0f}%） | {r['to_fill']} | "
            f"{advice_cn.get(r['advice'], r['advice'])} |"
        )
    L += [
        "",
        "> 说明：**今日满饱和地板** = `baseValue × 今日噪声因子`（今天噪声固定，饱和拉满后的价格），是当天能拿到的最低档；",
        "> 跨天才会换噪声，所以真正的「历史最低 7 新币」只在噪声谷底的日子出现。",
        "> 黄金结晶的饱和阈值极大（50000），表里的「拉满还需」对它是天文数字——正常养不出来，忽略即可。",
        "",
        "## 4. 卖出策略",
        "",
        "1. **看建议再卖**：`sr_market_advice` 的 SELL_NOW = 价格高于基准 15% 或正在上涨；WAIT = 高饱和且下跌；",
        "2. **分批卖**：同一种结晶一次卖到接近饱和阈值（表里「拉满还需」列）就停手，剩的等下一小时；",
        "3. **优先卖高档**：基准价 50（旋风/藤蔓/马赛克）> 40（水晶/猎猫/量子/剑齿）> 30（炸弹/火焰/蜂蜜/水滴/辐射）> 15（荧光/岩石/喵喵）> 7（粉红）；",
        "4. **卖爆后等**：把价格打到底后，等 2~3 个游戏小时（现实约 3~4 分钟）再卖第二轮；",
        "5. **闭市别卖**：每天开头 5 分钟市场关闭，调用会返回 `MARKET_CLOSED`。",
        "",
        "## 5. 工具对接",
        "",
        "```powershell",
        "# 查行情（只读）",
        "python -B tool\\bridge_cli.py call sr_market",
        "python -B tool\\bridge_cli.py call sr_market_advice",
        "",
        "# 批量卖某种结晶（默认预演；真执行要 dry_run=false + request_id）",
        "python -B tool\\bridge_cli.py call sr_sell_all --arg item_id=CRYSTAL_PLORT --arg count=10",
        "```",
        "",
        "MCP 侧同名工具：`sr_market` / `sr_market_advice` / `sr_sell` / `sr_sell_all`。写操作规则见 `docs/START_HERE.md` §5.3。",
        "",
    ]
    return "\n".join(L) + "\n"


def main() -> None:
    os.makedirs(DATA, exist_ok=True)
    data = fetch(DATA)
    m = build_models(data)

    tm = data["time"]
    hour = tm["hour"]
    hour_real = f"{int(hour):02d}:{int(round((hour - int(hour)) * 60)):02d}"
    saves = data["saves"]
    meta = {
        "day": tm["day"],
        "hour_real": hour_real,
        "wall": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "save": saves.get("current_save") or saves.get("current") or "?",
        "bridge": data.get("_bridge") or "0.8.2",
        "slime_count": data["slimes"]["total"],
    }

    outputs = {
        "SLIMES.md": write_slimes(m, meta),
        "SLIME_FOODS.md": write_foods(m, meta),
        "LARGO_COMBOS.md": write_largos(m, meta),
        "MARKET.md": write_market(m, meta),
    }
    for fname, text in outputs.items():
        path = os.path.join(DOCS, fname)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        print(f"wrote {path} ({len(text)} chars)")

    # 覆盖率自检
    missing = [s["item_id"] for s in m["base_slimes"] if s["item_id"] not in m["slime_names"]]
    no_name_largo = [lg["item_id"] for lg in m["largos"] if lg["item_id"] not in m["slime_names"]]
    print(f"基础史莱姆缺中文名: {missing or '无'}；Largo 缺中文名: {len(no_name_largo)} 个")
    if missing or no_name_largo:
        print("（缺名时会回退成 ID，可接受）")


if __name__ == "__main__":
    main()
