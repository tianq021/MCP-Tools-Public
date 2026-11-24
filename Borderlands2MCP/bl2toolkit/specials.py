"""特殊武器总表（传说/珠光/彩色/炽天使/E科技/独特）的查询与直接获取。

数据来自 `data/special_weapons.json`（由 scripts/build_special_weapons.py 生成），
每一条都带可直接导入存档的 BL2(...) 代码。

稀有度对照：
- 传说 = 橙装 Legendary
- 珠光 = 青装 Pearlescent
- 彩色 = 彩虹 / DLC彩 Effervescent
- 炽天使 = 粉装 Seraph
- E科技 = 品红（部分人叫"深红色"）E-tech
- 独特 = 蓝装/紫装带红字 Unique
- 龙堡宝石 = 龙堡DLC宝石命名武器
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Optional

from . import DATA_DIR, itemcodes
from .saves import SaveFile

CATALOG_FILE = DATA_DIR / "special_weapons.json"

#: 稀有度内部键 -> (显示名, 英文, 颜色说明)
RARITY_META: dict[str, tuple[str, str, str]] = {
    "传说": ("传说（橙装）", "Legendary", "橙色名字"),
    "珠光": ("珠光（青装）", "Pearlescent", "青色名字"),
    "彩色": ("彩色（彩虹/DLC彩）", "Effervescent", "彩虹色名字"),
    "炽天使": ("炽天使（粉装）", "Seraph", "粉色名字"),
    "E科技": ("E科技（品红/深红）", "E-tech", "品红/深红色名字"),
    "独特紫": ("独特（紫装红字）", "Unique (Purple)", "紫色名字+红字"),
    "独特蓝": ("独特（蓝装红字）", "Unique (Blue)", "蓝色名字+红字"),
    "龙堡宝石": ("龙堡DLC宝石", "Gemstone", "宝石命名"),
    "普通绿": ("普通（绿装）", "Uncommon", "绿色名字"),
    "道具": ("限时/特殊道具", "Consumable", "糖果、妖精瓶等"),
    "未标注": ("未标注稀有度", "Unknown", "资料未标注"),
    "黑枪": ("社区黑枪代码", "Modded", "社区改装代码"),
}

RARITY_ORDER = ["传说", "珠光", "彩色", "炽天使", "E科技", "独特紫", "独特蓝", "龙堡宝石", "普通绿", "道具", "未标注", "黑枪"]

#: 用户可能使用的别名（含"深红色"）
RARITY_ALIASES: dict[str, str] = {
    "深红色": "E科技", "深红": "E科技", "品红": "E科技", "品红色": "E科技", "紫红": "E科技",
    "橙装": "传说", "橙": "传说", "传说武器": "传说", "传奇": "传说",
    "青装": "珠光", "青": "珠光",
    "彩虹": "彩色", "彩": "彩色", "彩装": "彩色", "DLC彩": "彩色", "彩色武器": "彩色",
    "粉色": "炽天使", "粉装": "炽天使", "粉": "炽天使", "天使": "炽天使",
    "蓝装": "独特蓝", "蓝": "独特蓝", "紫装": "独特紫", "紫": "独特紫",
    "独特": None,  # 特殊处理：独特蓝+独特紫
    "宝石": "龙堡宝石", "龙堡": "龙堡宝石",
}

KIND_ALIASES = {
    "枪": "武器", "枪械": "武器", "武器": "武器",
    "护盾": "护盾", "盾": "护盾",
    "手雷": "手雷", "手雷模组": "手雷",
    "模组": "模组", "职业模组": "模组",
    "圣物": "圣物", "道具": "道具",
}

_cache: Optional[list[dict[str, Any]]] = None


def load_catalog(refresh: bool = False) -> list[dict[str, Any]]:
    global _cache
    if _cache is None or refresh:
        if not CATALOG_FILE.is_file():
            _cache = []
        else:
            _cache = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    return _cache


def normalize_rarity(value: Optional[str]) -> Optional[list[str]]:
    """把用户输入的稀有度（含"深红色"等别名）转成内部键列表。"""
    if not value:
        return None
    value = value.strip()
    if value in RARITY_META:
        return [value]
    alias = RARITY_ALIASES.get(value)
    if alias:
        return [alias]
    if value == "独特":
        return ["独特蓝", "独特紫"]
    # 模糊：包含关系
    out = []
    for key, (display, en, _desc) in RARITY_META.items():
        if value in key or value in display or value.lower() in en.lower():
            out.append(key)
    return out or None


def normalize_kind(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    value = value.strip()
    return KIND_ALIASES.get(value, value)


def rarity_display(key: str) -> str:
    return RARITY_META.get(key, (key, key, ""))[0]


def stats() -> dict[str, Any]:
    from collections import Counter

    items = load_catalog()
    return {
        "total": len(items),
        "by_rarity": {k: sum(1 for x in items if x["rarity"] == k) for k in RARITY_ORDER if any(x["rarity"] == k for x in items)},
        "by_kind": dict(Counter(x.get("kind", "") for x in items)),
        "file": str(CATALOG_FILE),
    }


def search(
    query: str = "",
    *,
    rarity: Optional[str] = None,
    kind: Optional[str] = None,
    source: Optional[str] = None,
    limit: Optional[int] = None,
) -> list[dict[str, Any]]:
    """按名字/英文名/备注/代码搜索，可按稀有度和类型过滤。"""
    items = load_catalog()
    rarities = normalize_rarity(rarity)
    kind_n = normalize_kind(kind)
    q = (query or "").strip().lower()

    def match(item: dict[str, Any]) -> bool:
        if rarities and item.get("rarity") not in rarities:
            return False
        if kind_n and item.get("kind") != kind_n:
            return False
        if source and source not in (item.get("source") or ""):
            return False
        if not q:
            return True
        hay = " ".join(
            str(item.get(k, "")) for k in ("name", "name_en", "note", "manufacturer", "weapon_type", "source")
        ).lower()
        if q in hay:
            return True
        # 代码搜索（忽略 key）
        return q.startswith("bl2(") and item.get("code", "").lower().startswith("bl2(")

    result = [x for x in items if match(x)]
    result.sort(key=lambda r: (RARITY_ORDER.index(r["rarity"]) if r["rarity"] in RARITY_ORDER else 99, r.get("kind", ""), r.get("name", "")))
    if limit:
        result = result[:limit]
    return result


def find(name_or_code: str) -> Optional[dict[str, Any]]:
    """按名字/英文名/代码精确或模糊查找一条。"""
    items = load_catalog()
    key = (name_or_code or "").strip()
    if not key:
        return None
    # 直接是代码
    if itemcodes.is_code(key):
        norm = itemcodes.with_key_zero(key)
        for item in items:
            if item["code"] == norm:
                return item
        return None
    # 精确名字
    for item in items:
        if item.get("name") == key:
            return item
    for item in items:
        if (item.get("name_en") or "").lower() == key.lower():
            return item
    # 包含
    q = key.lower()
    for item in items:
        if q in (item.get("name") or "").lower() or q in (item.get("name_en") or "").lower():
            return item
    return None


def add_to_save(
    save: SaveFile,
    name_or_code: str,
    *,
    count: int = 1,
    where: str = "bank",
    dedupe: bool = True,
) -> dict[str, Any]:
    """把表里的一条特殊装备导入存档，返回统计。"""
    entry = find(name_or_code)
    codes: list[str]
    if entry is not None:
        codes = [entry["code"]] * max(1, count)
        display = entry["name"]
        rarity = entry["rarity"]
    elif itemcodes.is_code(name_or_code):
        codes = [itemcodes.with_key_zero(name_or_code)] * max(1, count)
        display = "直接代码"
        rarity = ""
    else:
        raise ValueError(f"特殊武器表里找不到 {name_or_code!r}")

    before = save.item_counts()
    imported = save.import_codes(codes, where=where, dedupe=dedupe)
    after = save.item_counts()
    return {
        "item": display,
        "rarity": rarity,
        "codes": len(codes),
        "imported": imported,
        "before": before,
        "after": after,
    }


def to_markdown_entries(items: Iterable[dict[str, Any]]) -> list[str]:
    """把条目渲染成 markdown 表格行（给 CLI 用）。"""
    lines = ["| 名称 | 英文名 | 类型 | 厂商 | 稀有度 | 代码 |", "| --- | --- | --- | --- | --- | --- |"]
    for r in items:
        lines.append(
            "| {name} | {en} | {typ} | {mfr} | {rarity} | `{code}` |".format(
                name=r.get("name", ""),
                en=r.get("name_en", ""),
                typ=r.get("weapon_type") or r.get("kind", ""),
                mfr=r.get("manufacturer", ""),
                rarity=rarity_display(r.get("rarity", "")),
                code=r.get("code", ""),
            )
        )
    return lines
