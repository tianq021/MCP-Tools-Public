"""从本机参考资料生成"特殊武器总表"。

数据来源：
- 特殊装备代码.xlsx：特殊武器（代码/英文名/中文名/类型/厂商）、龙堡DLC枪械、蓝色模组、DLC彩（彩虹）、精灵
- 无主2代码.docx：给物品标注稀有度（橙装/粉装/青装/品红/蓝装/紫装），并补充护盾/手雷/模组/圣物

产出：
- data/special_weapons.json  结构化总表
- data/特殊武器表.md          人类可读表格（按稀有度分组）

用法：python scripts\\build_special_weapons.py
"""

from __future__ import annotations

import json
import re
import sys
import zipfile
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import openpyxl  # noqa: E402

from bl2toolkit import DATA_DIR, itemcodes  # noqa: E402
from bl2toolkit import library as gunlib  # noqa: E402

XLSX = gunlib.REFERENCE_DIR / "特殊装备代码.xlsx"
DOCX = gunlib.REFERENCE_DIR / "无主2代码.docx"
JSON_OUT = DATA_DIR / "special_weapons.json"
MD_OUT = DATA_DIR / "特殊武器表.md"
CSV_OUT = DATA_DIR / "特殊武器表.csv"

#: 稀有度元数据：内部键 -> (显示名, 英文, 说明)
RARITY_META: dict[str, tuple[str, str, str]] = {
    "传说": ("传说（橙装）", "Legendary", "橙色名字，全部带红字，世界掉落/特定Boss高掉率"),
    "珠光": ("珠光（青装）", "Pearlescent", "青色名字，二代最高稀有度之一，世界掉落"),
    "彩色": ("彩色（彩虹/DLC彩）", "Effervescent", "彩虹色名字，指挥官莉莉丝DLC新增"),
    "炽天使": ("炽天使（粉装）", "Seraph", "粉色名字，DLC水晶商店购买/隐藏Boss"),
    "E科技": ("E科技（品红/深红）", "E-tech", "品红（紫红）名字，外星科技枪，发射能量弹"),
    "独特蓝": ("独特（蓝装红字）", "Unique (Blue)", "蓝色名字但有红字特殊效果，多为任务/Boss专属"),
    "独特紫": ("独特（紫装红字）", "Unique (Purple)", "紫色名字但有红字特殊效果"),
    "龙堡宝石": ("龙堡DLC宝石武器", "Gemstone", "龙堡DLC的宝石命名武器（蓝宝石/翡翠/钻石等）"),
    "普通绿": ("普通（绿装）", "Uncommon", "绿色，一般不算特殊装备"),
    "道具": ("限时/特殊道具", "Consumable", "糖果、妖精瓶等限时增益道具"),
    "未标注": ("未标注稀有度", "Unknown", "资料里没有标稀有度，可能为独特/传说"),
    "黑枪": ("社区黑枪代码", "Modded", "社区自制/改装代码（非原版合法物品）"),
}

RARITY_ORDER = ["传说", "珠光", "彩色", "炽天使", "E科技", "独特紫", "独特蓝", "龙堡宝石", "普通绿", "道具", "未标注", "黑枪"]

#: 用户可能用这些词来指代稀有度
RARITY_ALIASES = {
    "深红色": "E科技",
    "深红": "E科技",
    "品红": "E科技",
    "紫红": "E科技",
    "青装": "珠光",
    "青": "珠光",
    "珠光宝气": "珠光",
    "彩虹": "彩色",
    "彩": "彩色",
    "DLC彩": "彩色",
    "橙装": "传说",
    "橙": "传说",
    "粉色": "炽天使",
    "粉装": "炽天使",
    "粉": "炽天使",
    "蓝装": "独特蓝",
    "蓝": "独特蓝",
    "紫装": "独特紫",
    "紫": "独特紫",
    "宝石": "龙堡宝石",
}

DOCX_TAG_TO_RARITY = {
    "橙装": "传说",
    "橙": "传说",
    "青装": "珠光",
    "青": "珠光",
    "粉装": "炽天使",
    "粉": "炽天使",
    "品红": "E科技",
    "品红装": "E科技",
    "蓝装": "独特蓝",
    "蓝": "独特蓝",
    "紫装": "独特紫",
    "紫": "独特紫",
    "绿装": "普通绿",
    "绿": "普通绿",
}

CODE_RE = re.compile(r"BL2\([A-Za-z0-9+/=]+\)")
TAG_RE = r"(?:橙装|青装|粉装|品红装?|蓝装|紫装|绿装|橙|青|粉|蓝|紫|绿)"

TYPE_PREFIXES = (
    "手枪", "冲锋枪", "冲锋", "突击步枪", "步枪", "霰弹枪", "霰弹", "狙击枪", "狙击",
    "火箭筒", "护盾", "手雷", "模组", "圣物", "SMG",
)
TYPE_SUFFIX_MAP = {
    "冲锋枪": "冲锋枪",
    "冲锋": "冲锋枪",
    "散弹枪": "霰弹枪",
    "霰弹枪": "霰弹枪",
    "霰弹": "霰弹枪",
    "突击步枪": "步枪",
    "步枪": "步枪",
    "狙击步枪": "狙击枪",
    "狙击枪": "狙击枪",
    "狙击": "狙击枪",
    "手枪": "手枪",
    "火箭筒": "火箭筒",
    "手雷": "手雷",
    "护盾": "护盾",
    "圣物": "圣物",
}


def norm_code(code: str) -> str:
    try:
        return itemcodes.with_key_zero(code)
    except Exception:
        return code


def clean_name(name: str) -> str:
    name = name.replace("\u3000", " ").strip()
    name = re.sub(r"\s+", "", name)
    for prefix in sorted(TYPE_PREFIXES, key=len, reverse=True):
        if name.startswith(prefix) and len(name) > len(prefix):
            name = name[len(prefix):]
            break
    return name.strip("：: -")


def guess_type_from_name(name: str) -> str:
    for suffix, typ in sorted(TYPE_SUFFIX_MAP.items(), key=lambda kv: -len(kv[0])):
        if suffix in name:
            return typ
    return ""


WEAPON_TYPES = {
    "手枪", "冲锋枪", "冲锋", "步枪", "突击步枪", "霰弹枪", "霰弹", "狙击枪", "狙击",
    "火箭筒", "SMG",
}
TYPE_TO_KIND = {
    "护盾": "护盾",
    "手雷": "手雷",
    "手雷模组": "手雷",
    "模组": "模组",
    "职业模组": "模组",
    "圣物": "圣物",
    "道具": "道具",
}


def guess_kind(name: str, item_type: str = "") -> str:
    """把资料里的"类型"列归一化为 武器/护盾/手雷/模组/圣物/道具。"""
    item_type = (item_type or "").strip()
    if item_type in WEAPON_TYPES:
        return "武器"
    if item_type in TYPE_TO_KIND:
        return TYPE_TO_KIND[item_type]
    if item_type:
        return item_type
    if any(k in name for k in ("护盾", "盾")):
        return "护盾"
    if any(k in name for k in ("雷", "炸弹", "手雷")):
        return "手雷"
    if "圣物" in name:
        return "圣物"
    if "模组" in name or "传奇" in name or "魔虫" in name:
        return "模组"
    return "武器"


# --------------------------------------------------------------------------
# 解析 docx（稀有度标注 + 额外物品）
# --------------------------------------------------------------------------


def _docx_paragraph_index(lines: list[str]) -> tuple[str, list[int]]:
    """把段落拼成全文，并记录每段起始偏移，用于定位匹配属于哪一段。"""
    starts: list[int] = []
    parts: list[str] = []
    offset = 0
    for text in lines:
        starts.append(offset)
        parts.append(text)
        offset += len(text) + 1
    return "\n".join(parts), starts


def _kind_from_paragraph(idx: int, name: str, default: str) -> str:
    """docx 是按分类排版的，用段落位置判断物品种类。"""
    if idx == 1:
        return "武器"
    if 2 <= idx <= 5:
        return "护盾"
    if 6 <= idx <= 8:
        return "手雷"
    if 9 <= idx <= 10:
        return "模组"
    if 13 <= idx <= 53:
        if "模组" in name:
            return "模组"
        if "护盾" in name or name.endswith("盾"):
            return "护盾"
        return "圣物"
    if idx == 55:
        return "模组"
    if idx == 56:
        guessed = guess_kind(name)
        if guessed != "武器":
            return guessed
        return "圣物" if "圣物" in name else "模组"
    return default


def parse_docx(path: Path) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """解析 docx：返回 (名字->稀有度, 额外条目)。

    docx 排版比较乱（有"代码在前"和"名字在前"两种），策略：
    - 名字→稀有度：直接按 "(名字)（XX装）" 的相邻关系提取，可靠；
    - 额外条目（护盾/手雷/模组/圣物）：只处理第 2 段之后的分类区域，
      每个"名字（稀有度）"配它后面最近的一条未用代码（这些区域都是名字在前）。
    - 第 0/1 段是消耗品和武器，武器以 xlsx 为准，不重复生成。
    """
    z = zipfile.ZipFile(path)
    xml = z.read("word/document.xml").decode("utf-8", "replace")
    paragraphs = re.findall(r"<w:p[ >].*?</w:p>", xml, re.S)
    lines = []
    for para in paragraphs:
        text = re.sub(r"<[^>]+>", "", para)
        lines.append(text.replace("\u3000", " "))
    full, starts = _docx_paragraph_index(lines)

    def paragraph_of(offset: int) -> int:
        idx = 0
        for i, start in enumerate(starts):
            if start <= offset:
                idx = i
            else:
                break
        return idx

    name_tag_pat = re.compile(rf"([^\n（）()]{{1,24}})（({TAG_RE})）")
    code_entries: list[tuple[int, str]] = []
    for m in CODE_RE.finditer(full):
        code_entries.append((m.start(), norm_code(m.group(0))))

    by_name: dict[str, str] = {}
    for m in name_tag_pat.finditer(full):
        name = clean_name(m.group(1))
        rarity = DOCX_TAG_TO_RARITY.get(m.group(2), "未标注")
        if name:
            by_name.setdefault(name, rarity)

    extras: list[dict[str, Any]] = []
    seen_codes: set[str] = set()
    cursor = 0
    for m in name_tag_pat.finditer(full):
        para_idx = paragraph_of(m.start())
        # 第 0 段消耗品、第 1 段武器（以 xlsx 为准）、第 60+ 段是皮肤
        if para_idx in (0, 1) or para_idx >= 60:
            continue
        name = clean_name(m.group(1))
        if not name:
            continue
        tag = m.group(2)
        rarity = DOCX_TAG_TO_RARITY.get(tag, "未标注")
        chosen: Optional[tuple[int, str]] = None
        for i in range(cursor, len(code_entries)):
            pos, code = code_entries[i]
            if pos >= m.end():
                chosen = (i, code)
                break
        if chosen is None:
            continue
        idx, code = chosen
        cursor = idx + 1
        if code in seen_codes:
            continue
        seen_codes.add(code)
        kind = _kind_from_paragraph(para_idx, name, guess_kind(name))
        extras.append(
            {
                "name": name,
                "code": code,
                "rarity": rarity,
                "kind": kind,
                "weapon_type": guess_type_from_name(name) if kind == "武器" else "",
                "manufacturer": "",
                "name_en": "",
                "source": "无主2代码.docx",
                "note": f"docx 标注：{tag}",
            }
        )
    return by_name, extras


# --------------------------------------------------------------------------
# 解析 xlsx
# --------------------------------------------------------------------------


def parse_xlsx(path: Path) -> list[dict[str, Any]]:
    wb = openpyxl.load_workbook(path, data_only=True)
    records: list[dict[str, Any]] = []

    def cell(row, idx):
        if idx >= len(row):
            return ""
        v = row[idx]
        return "" if v is None else str(v).strip()

    # 1) 特殊武器：代码 | 英文 | 名字 | 类型 | 厂商
    ws = wb["特殊武器"]
    for row in ws.iter_rows(values_only=True):
        row = list(row)
        code = next((c for c in row if c and "BL2(" in str(c)), None)
        if not code:
            continue
        code_m = CODE_RE.search(str(code))
        if not code_m:
            continue
        cols = [str(c).strip() if c is not None else "" for c in row]
        name = ""
        name_en = ""
        typ = ""
        mfr = ""
        for i, val in enumerate(cols):
            if CODE_RE.search(val):
                continue
            if i == 1:
                name_en = val
            elif i == 2:
                name = val
            elif i == 3:
                typ = val
            elif i == 4:
                mfr = val
        records.append(
            {
                "name": name.strip(),
                "code": norm_code(code_m.group(0)),
                "kind": guess_kind(name, typ),
                "weapon_type": typ,
                "manufacturer": mfr,
                "name_en": name_en,
                "source": "特殊装备代码.xlsx/特殊武器",
                "note": "",
            }
        )

    # 2) DLC彩（彩虹装备）：一列名字+代码，下一行是枪管注释
    ws = wb["DLC彩"]
    pending_hint = ""
    for row in ws.iter_rows(values_only=True):
        text = " ".join(str(c) for c in row if c is not None).strip()
        if not text:
            continue
        codes = CODE_RE.findall(text)
        if not codes:
            if text.startswith(("枪管", "GD_")):
                pending_hint = text
            continue
        for code in codes:
            name = clean_name(text.replace(code, "").split("（")[0])
            typ = guess_type_from_name(name)
            records.append(
                {
                    "name": name,
                    "code": norm_code(code),
                    "kind": "武器" if typ in ("手枪", "冲锋枪", "步枪", "霰弹枪", "狙击枪", "火箭筒") else guess_kind(name, typ),
                    "weapon_type": typ,
                    "manufacturer": "",
                    "name_en": "",
                    "rarity": "彩色",
                    "source": "特殊装备代码.xlsx/DLC彩",
                    "note": pending_hint,
                }
            )
            pending_hint = ""

    # 3) 龙堡DLC特殊枪械：厂商（宝石）分段 + 类型 + 代码
    ws = wb["龙堡dlc特殊枪械"]
    gem = ""
    mfr = ""
    for row in ws.iter_rows(values_only=True):
        vals = [str(c).strip() for c in row if c is not None and str(c).strip()]
        if not vals:
            continue
        text = " ".join(vals)
        m = re.match(r"^([A-Z]+)（(.+?)）", text)
        if m and not CODE_RE.search(text):
            mfr = m.group(1)
            gem = m.group(2)
            continue
        codes = CODE_RE.findall(text)
        if codes:
            prefix = text.split("BL2(")[0].strip()
            typ = guess_type_from_name(prefix) or TYPE_SUFFIX_MAP.get(prefix, "")
            for code in codes:
                records.append(
                    {
                        "name": f"{gem}（{prefix}）" if gem else prefix,
                        "code": norm_code(code),
                        "kind": "武器",
                        "weapon_type": typ or prefix,
                        "manufacturer": mfr,
                        "name_en": "",
                        "rarity": "龙堡宝石",
                        "source": "特殊装备代码.xlsx/龙堡dlc特殊枪械",
                        "note": f"龙堡宝石：{gem}",
                    }
                )

    # 4) 精灵（糖果/妖精瓶，限时道具）
    ws = wb["精灵"]
    kind_name = ""
    for row in ws.iter_rows(values_only=True):
        vals = [str(c).strip() for c in row if c is not None and str(c).strip()]
        if not vals:
            continue
        text = " ".join(vals)
        if not CODE_RE.search(text):
            if text in ("Candies", "Wisps", "Spoiler"):
                kind_name = text
            continue
        for code in CODE_RE.findall(text):
            label = text.replace(code, "").strip(" -—")
            records.append(
                {
                    "name": f"{kind_name} {label}".strip(),
                    "code": norm_code(code),
                    "kind": "道具",
                    "weapon_type": "",
                    "manufacturer": "",
                    "name_en": "",
                    "rarity": "道具",
                    "source": "特殊装备代码.xlsx/精灵",
                    "note": "限时增益道具（糖果/妖精瓶）",
                }
            )

    # 5) 蓝色模组：角色分段 + 名字 + 代码
    ws = wb["蓝色模组"]
    char = ""
    for row in ws.iter_rows(values_only=True):
        vals = [str(c).strip() for c in row if c is not None and str(c).strip()]
        if not vals:
            continue
        text = " ".join(vals)
        codes = CODE_RE.findall(text)
        if not codes:
            if len(text) <= 6:
                char = text
            continue
        name_part = text.split("BL2(")[0].strip(" |")
        for code in codes:
            records.append(
                {
                    "name": clean_name(name_part) or f"{char} 蓝色模组",
                    "code": norm_code(code),
                    "kind": "模组",
                    "weapon_type": "",
                    "manufacturer": "",
                    "name_en": "",
                    "rarity": "独特蓝",
                    "source": "特殊装备代码.xlsx/蓝色模组",
                    "note": f"角色：{char}" if char else "",
                }
            )

    return records


# --------------------------------------------------------------------------
# 合并 + 输出
# --------------------------------------------------------------------------


def build() -> list[dict[str, Any]]:
    by_name, extras = parse_docx(DOCX)
    xlsx_records = parse_xlsx(XLSX)

    merged: dict[str, dict[str, Any]] = {}

    def put(rec: dict[str, Any]) -> None:
        key = rec["code"]
        old = merged.get(key)
        if old is None:
            merged[key] = rec
            return
        # 合并字段：空字段用新的补
        for k, v in rec.items():
            if not old.get(k) and v:
                old[k] = v
        if rec.get("name_en") and not old.get("name_en"):
            old["name_en"] = rec["name_en"]

    # xlsx 为主（有类型/厂商/英文名）
    for rec in xlsx_records:
        rarity = rec.get("rarity") or by_name.get(clean_name(rec["name"])) or "未标注"
        rec["rarity"] = rarity
        put(rec)

    # docx 补充（护盾/手雷/模组/圣物，以及 xlsx 里没有的武器）
    for rec in extras:
        if rec["code"] in merged:
            continue
        put(rec)

    items = list(merged.values())
    # 名字去重（同名不同代码加序号）
    name_count: dict[str, int] = {}
    for rec in items:
        base = rec["name"] or "(未命名)"
        name_count[base] = name_count.get(base, 0) + 1
        if name_count[base] > 1:
            rec["name"] = f"{base} #{name_count[base]}"
    items.sort(key=lambda r: (RARITY_ORDER.index(r["rarity"]) if r["rarity"] in RARITY_ORDER else 99, r.get("weapon_type", ""), r["name"]))
    return items


def write_markdown(items: list[dict[str, Any]]) -> None:
    from collections import Counter

    counts = Counter(r["rarity"] for r in items)
    lines: list[str] = []
    lines.append("# 无主之地2 特殊武器/装备总表")
    lines.append("")
    lines.append("> 由 `scripts/build_special_weapons.py` 从本机参考资料自动生成，可配合 `bl2.cmd specials add` 直接导入角色。")
    lines.append("> 稀有度颜色对照：传说=橙、珠光=青、彩色=彩虹、炽天使=粉、E科技=品红（深红）、独特=蓝/紫红字、龙堡=宝石命名。")
    lines.append("")
    lines.append("| 稀有度 | 条目数 |")
    lines.append("| --- | --- |")
    for rarity in RARITY_ORDER:
        if counts.get(rarity):
            lines.append(f"| {RARITY_META[rarity][0]} | {counts[rarity]} |")
    lines.append(f"| **合计** | **{len(items)}** |")
    lines.append("")

    for rarity in RARITY_ORDER:
        group = [r for r in items if r["rarity"] == rarity]
        if not group:
            continue
        display, en, desc = RARITY_META[rarity]
        lines.append(f"## {display}  <sub>{en}</sub>")
        lines.append("")
        lines.append(f"{desc}。共 {len(group)} 条。")
        lines.append("")
        lines.append("| 名称 | 英文名 | 类型 | 厂商 | 代码 | 备注 |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for r in group:
            name = r["name"].replace("|", "/")
            name_en = (r.get("name_en") or "").replace("|", "/")
            typ = r.get("weapon_type") or r.get("kind") or ""
            mfr = r.get("manufacturer") or ""
            note = (r.get("note") or "").replace("|", "/")
            lines.append(f"| {name} | {name_en} | {typ} | {mfr} | `{r['code']}` | {note} |")
        lines.append("")
    MD_OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_csv(items: list[dict[str, Any]]) -> None:
    """额外导出一份 CSV（用 Excel 打开方便筛选/打印）。"""
    import csv

    with CSV_OUT.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["稀有度", "类型", "厂商", "中文名", "英文名", "代码", "备注", "来源"])
        for r in items:
            writer.writerow(
                [
                    RARITY_META.get(r["rarity"], (r["rarity"],))[0],
                    r.get("weapon_type") or r.get("kind", ""),
                    r.get("manufacturer", ""),
                    r.get("name", ""),
                    r.get("name_en", ""),
                    r.get("code", ""),
                    r.get("note", ""),
                    r.get("source", ""),
                ]
            )


def main() -> int:
    if not XLSX.is_file():
        print(f"找不到资料文件: {XLSX}")
        return 1
    items = build()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    JSON_OUT.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    write_markdown(items)
    write_csv(items)
    from collections import Counter

    counts = Counter(r["rarity"] for r in items)
    kinds = Counter(r.get("kind", "") for r in items)
    print(f"总条目: {len(items)}  -> {JSON_OUT}")
    print(f"Markdown 表 -> {MD_OUT}")
    print("按稀有度:", dict(counts))
    print("按类型:", dict(kinds))
    print("\n示例:")
    for r in items[:6]:
        print(f"  [{r['rarity']}] {r['name']} {r.get('name_en','')} {r.get('weapon_type','')} {r.get('manufacturer','')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
