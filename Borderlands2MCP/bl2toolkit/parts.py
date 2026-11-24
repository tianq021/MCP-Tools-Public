"""离线部件解析、查找与组合预览（不读写存档、不接触游戏）。

数据来源（一手）：Gibbed Borderlands2Dumps
  https://github.com/gibbed/Borderlands2Dumps
由 `scripts/build_parts_data.py` 生成 `data/parts/*.json`。

能力边界：
- lib:asset → 资产路径的映射已用 635 条本地代码逐条验证（type/balance/manufacturer 100% 可解析；
  part 中值为全 1 的条目按“空槽”处理）。
- 组合预览只做静态检查；不保证游戏接受，也不写存档/profile。
"""

from __future__ import annotations

import copy
import json
from typing import Any, Optional

from . import DATA_DIR, itemcodes
from . import library as gunlib

PARTS_DIR = DATA_DIR / "parts"
ASSET_LIBRARY_FILE = PARTS_DIR / "asset_library.json"
PART_META_FILE = PARTS_DIR / "part_meta.json"
GAME_TOTALS_FILE = PARTS_DIR / "game_totals.json"
SPECIALS_FILE = DATA_DIR / "special_weapons.json"

WEAPON_SLOTS = {
    "Body": "BodyPartDefinition",
    "Grip": "GripPartDefinition",
    "Barrel": "BarrelPartDefinition",
    "Sight": "SightPartDefinition",
    "Stock": "StockPartDefinition",
    "Elemental": "ElementalPartDefinition",
    "Accessory1": "Accessory1PartDefinition",
    "Accessory2": "Accessory2PartDefinition",
    "Material": "MaterialPartDefinition",
    "Prefix": "PrefixPartDefinition",
    "Title": "TitlePartDefinition",
}
ITEM_SLOTS = {
    "Alpha": "AlphaItemPartDefinition",
    "Beta": "BetaItemPartDefinition",
    "Gamma": "GammaItemPartDefinition",
    "Delta": "DeltaItemPartDefinition",
    "Epsilon": "EpsilonItemPartDefinition",
    "Zeta": "ZetaItemPartDefinition",
    "Eta": "EtaItemPartDefinition",
    "Theta": "ThetaItemPartDefinition",
    "Material": "MaterialItemPartDefinition",
    "Prefix": "PrefixItemNamePartDefinition",
    "Title": "TitleItemNamePartDefinition",
}
# 用户输入别名 → 规范部件类型
SLOT_ALIASES: dict[str, str] = {}
for _t, _slot in {**WEAPON_SLOTS, **ITEM_SLOTS}.items():
    for _alias in (_t, _slot, _slot.replace("PartDefinition", ""), _slot.replace("ItemPartDefinition", ""), _slot.replace("ItemNamePartDefinition", "")):
        SLOT_ALIASES[_alias.lower()] = _t

_cache: dict[str, Any] = {}


def _load_json(path):
    if not path.is_file():
        raise FileNotFoundError(f"缺少离线部件数据: {path}（运行 scripts/build_parts_data.py）")
    return json.loads(path.read_text(encoding="utf-8"))


def asset_library() -> dict:
    if "lib" not in _cache:
        _cache["lib"] = _load_json(ASSET_LIBRARY_FILE)
    return _cache["lib"]


def part_meta() -> dict:
    if "meta" not in _cache:
        _cache["meta"] = _load_json(PART_META_FILE)
    return _cache["meta"]


def game_totals() -> dict:
    if "totals" not in _cache:
        _cache["totals"] = _load_json(GAME_TOTALS_FILE)
    return _cache["totals"]


def _sets() -> dict[int, dict]:
    return {s["id"]: s for s in asset_library()["sets"]}


def _configs() -> dict:
    return asset_library()["configs"]


def _ref_parts(lib: int, group: str) -> tuple[int, int, bool, int]:
    """把 item code 里的 lib 拆成 (实际 set 是否使用, 实际 sublibrary, useSet, sublibrary_bits)，"""
    cfg = _configs()[group]
    use_bit = 1 << (cfg["sublibrary_bits"] - 1)
    return use_bit, lib & (use_bit - 1), bool(lib & use_bit), cfg["sublibrary_bits"]


def resolve_reference(setid: int, group: str, lib: int, asset: int) -> Optional[dict[str, Any]]:
    """解析一项 (lib, asset) 引用；空槽/None 返回 None。"""
    use_bit, sub, use_set, sub_bits = _ref_parts(lib, group)
    cfg = _configs()[group]
    asset_bits = cfg["asset_bits"]
    if use_set and sub == use_bit - 1:
        return None  # 全 1：None 占位（实测 63/1023、63/2047）
    if asset == (1 << asset_bits) - 1 and use_set and sub == use_bit - 1:
        return None
    actual_set = setid if use_set else 0
    s = _sets().get(actual_set)
    if not s:
        return None
    g = s["libraries"].get(group)
    if not g:
        return None
    subs = g.get("sublibraries") or []
    if sub >= len(subs):
        return None
    assets = subs[sub].get("assets") or []
    if asset >= len(assets):
        return None
    package = subs[sub].get("package") or ""
    return {
        "set": actual_set,
        "lib": lib,
        "asset": asset,
        "path": (package + "." + assets[asset]) if package else assets[asset],
    }


_reverse: Optional[dict[str, list[tuple[int, str, int, int]]]] = None


def _reverse_index() -> dict[str, list[tuple[int, str, int, int]]]:
    """path → [(set, group, lib, asset)]，用于把部件路径编码回 lib:asset。"""
    global _reverse
    if _reverse is not None:
        return _reverse
    out: dict[str, list[tuple[int, str, int, int]]] = {}
    for s in asset_library()["sets"]:
        setid = s["id"]
        for group, lib in (s.get("libraries") or {}).items():
            if group not in _configs():
                continue
            use_bit, _, _, _ = _ref_parts(0, group)  # use_bit 只取位数
            for sub_i, sub in enumerate(lib.get("sublibraries") or []):
                package = sub.get("package") or ""
                lib_enc = sub_i | (use_bit if setid != 0 else 0)
                for asset_i, asset in enumerate(sub.get("assets") or []):
                    path = (package + "." + asset) if package else asset
                    out.setdefault(path, []).append((setid, group, lib_enc, asset_i))
    _reverse = out
    return out


def ref_for_path(path: str, item_setid: int = 0, group: Optional[str] = None) -> Optional[dict[str, Any]]:
    """给完整部件路径找可编码的 lib:asset（优先 base set / 与物品 set 一致）。"""
    cands = _reverse_index().get(path) or []
    if group:
        cands = [c for c in cands if c[1] == group]
    if not cands:
        return None
    cands = sorted(cands, key=lambda c: (0 if c[0] == 0 else (1 if c[0] == item_setid else 2), c[0]))
    setid, g, lib, asset = cands[0]
    return {"set": setid, "group": g, "lib": lib, "asset": asset}


def _part_type(path: str, is_weapon: bool) -> Optional[str]:
    m = part_meta()
    meta = (m["weapon_parts"] if is_weapon else m["item_parts"]).get(path)
    if meta:
        return meta.get("type") or ""
    if path in m.get("name_parts", {}):
        if ".Name.Prefix" in path or path.endswith("Prefix") or ".Prefix." in path:
            return "Prefix"
        if ".Name.Title" in path or path.endswith("Title") or ".Title." in path:
            return "Title"
    # 兜底：路径段名
    seg = path.rsplit(".", 1)[-1]
    for t in (list(WEAPON_SLOTS) + list(ITEM_SLOTS)):
        if seg.startswith(t):
            return t
    return None


def _slot_for_type(part_type: Optional[str], is_weapon: bool) -> Optional[str]:
    if not part_type:
        return None
    return (WEAPON_SLOTS if is_weapon else ITEM_SLOTS).get(part_type)


def _meta_for(path: str) -> dict:
    m = part_meta()
    return (
        m["weapon_parts"].get(path)
        or m["item_parts"].get(path)
        or m["name_parts"].get(path)
        or {}
    )


def decode_resolved(code: str) -> dict[str, Any]:
    """解码物品代码，并把 type/balance/manufacturer/parts 全部解析成资产路径。"""
    info = itemcodes.decode(code)
    is_weapon = bool(info.get("is_weapon"))
    setid = int(info.get("set", 0))
    out = dict(info)
    out["resolved"] = {}
    for key, group in (
        ("type", "WeaponTypes" if is_weapon else "ItemTypes"),
        ("balance", "BalanceDefs"),
        ("manufacturer", "Manufacturers"),
    ):
        ref = info.get(key) or {}
        resolved = resolve_reference(setid, group, int(ref.get("lib", 0)), int(ref.get("asset", 0)))
        out["resolved"][key] = resolved
    parts = []
    for i, ref in enumerate(info.get("parts") or []):
        if ref is None:
            parts.append({"index": i, "raw": None, "path": None, "type": None, "slot": None, "meta": {}})
            continue
        group = "WeaponParts" if is_weapon else "ItemParts"
        resolved = resolve_reference(setid, group, int(ref.get("lib", 0)), int(ref.get("asset", 0)))
        path = resolved["path"] if resolved else None
        ptype = _part_type(path, is_weapon) if path else None
        parts.append(
            {
                "index": i,
                "raw": {"lib": ref.get("lib"), "asset": ref.get("asset")},
                "path": path,
                "type": ptype,
                "slot": _slot_for_type(ptype, is_weapon),
                "meta": _meta_for(path) if path else {},
            }
        )
    out["resolved"]["parts"] = parts
    if is_weapon:
        out["resolved"]["weapon_type"] = part_meta()["weapon_types"].get((out["resolved"].get("type") or {}).get("path"))
    return out


def slot_table() -> dict[str, Any]:
    return {"weapon": WEAPON_SLOTS, "item": ITEM_SLOTS}


def search_parts(query: str = "", kind: str = "", limit: int = 50) -> list[dict[str, Any]]:
    """按路径/名称/类型搜索部件定义；kind: weapon/item/name。"""
    q = (query or "").lower().strip()
    kind = (kind or "").strip().lower()
    m = part_meta()
    rows = []
    groups = []
    if kind in ("", "weapon"):
        groups.append(("weapon", m["weapon_parts"]))
    if kind in ("", "item"):
        groups.append(("item", m["item_parts"]))
    if kind in ("", "name"):
        groups.append(("name", m["name_parts"]))
    for group, table in groups:
        for path, meta in table.items():
            ptype = _part_type(path, group == "weapon") if group != "name" else _part_type(path, True)
            text = " ".join([path, str(meta.get("name", "")), str(ptype or ""), str(meta.get("type", ""))]).lower()
            if q and q not in text:
                continue
            ref = ref_for_path(path, 0, "WeaponParts" if group == "weapon" else ("ItemParts" if group == "item" else None))
            rows.append(
                {
                    "kind": group,
                    "path": path,
                    "type": ptype or meta.get("type", ""),
                    "slot": _slot_for_type(ptype, group != "item"),
                    "name": meta.get("name", ""),
                    "ref": None if not ref else f"{ref['lib']}:{ref['asset']}",
                    "set": None if not ref else ref["set"],
                }
            )
    rows.sort(key=lambda r: (r["kind"], r["type"] or "", r["path"]))
    return rows[: max(1, int(limit))]


def _parse_replacement(value: Any, item_setid: int, is_weapon: bool) -> Optional[dict[str, Any]]:
    group = "WeaponParts" if is_weapon else "ItemParts"
    if isinstance(value, dict) and "lib" in value and "asset" in value:
        lib, asset = int(value["lib"]), int(value["asset"])
        resolved = resolve_reference(item_setid, group, lib, asset)
        if resolved:
            return {"path": resolved["path"], "lib": lib, "asset": asset}
        return {"path": None, "lib": lib, "asset": asset}
    s = str(value).strip()
    if not s:
        return None
    if s.lower().startswith("bl2("):
        raise ValueError("replacements 里需要部件路径或 lib:asset，不是物品代码")
    if "." in s and not s.replace(":", "").isdigit():
        cands = [c for c in (_reverse_index().get(s) or []) if c[1] == group]
        chosen = None
        for setid, g, lib, asset in cands:
            if setid == 0 or setid == item_setid:
                chosen = (setid, g, lib, asset)
                break
        if chosen is None:
            return {
                "path": s,
                "lib": None,
                "asset": None,
                "needs_set": (cands[0][0] if cands else None),
                "missing": not cands,
            }
        return {"path": s, "lib": chosen[2], "asset": chosen[3]}
    bits = s.split(":")
    # 支持 lib:asset 或 set:lib:asset
    setid = item_setid
    if len(bits) == 3:
        setid, lib, asset = (int(x) for x in bits)
    elif len(bits) == 2:
        lib, asset = (int(x) for x in bits)
    else:
        raise ValueError(f"无法解析部件引用 {value!r}（用 lib:asset 或完整路径）")
    resolved = resolve_reference(setid, group, lib, asset)
    return {"path": resolved["path"] if resolved else None, "lib": lib, "asset": asset}


def _item_category(balance_path: Optional[str], type_path: Optional[str]) -> Optional[str]:
    text = " ".join(x.lower() for x in (balance_path or "", type_path or "") if x)
    if "shield" in text:
        return "shield"
    if "grenade" in text:
        return "grenade"
    if "classmod" in text or "class_mod" in text:
        return "classmod"
    if "artifact" in text or "relic" in text:
        return "relic"
    return None


def _item_allowed_paths(category: Optional[str]) -> set[str]:
    if not category:
        return set()
    keywords = {"shield": ("shield",), "grenade": ("grenade",), "classmod": ("classmod",), "relic": ("artifact", "relic")}[category]
    out: set[str] = set()
    for key, paths in (part_meta().get("item_part_lists") or {}).items():
        low = key.lower()
        if any(k in low for k in keywords):
            out.update(paths or [])
    return out


def preview(code: str, replacements: dict[str, Any], allow_append: bool = False) -> dict[str, Any]:
    """组合预览：替换/追加部件，返回新代码与静态风险提示；不写任何文件。"""
    base = decode_resolved(code)
    is_weapon = bool(base.get("is_weapon"))
    setid = int(base.get("set", 0))
    info = copy.deepcopy(itemcodes.decode(code))
    parts = info.get("parts") or []
    warnings: list[str] = []
    errors: list[str] = []
    changes: list[dict[str, Any]] = []
    current = base["resolved"]["parts"]
    by_slot: dict[str, list[int]] = {}
    for p in current:
        if p.get("type"):
            by_slot.setdefault(p["type"], []).append(p["index"])

    weapon_type = base["resolved"].get("weapon_type") or {}
    allowed_slot_fields = {"Body": "body_parts", "Grip": "grip_parts", "Barrel": "barrel_parts", "Stock": "stock_parts"}
    manufacturer_path = ((base["resolved"].get("manufacturer") or {}).get("path") or "")
    manufacturer = manufacturer_path.rsplit(".", 1)[-1] if manufacturer_path else ""
    known_manufacturers = set()
    for s in asset_library()["sets"]:
        for sub in ((s.get("libraries") or {}).get("Manufacturers") or {}).get("sublibraries") or []:
            for a in sub.get("assets") or []:
                known_manufacturers.add(a)

    for raw_slot, value in (replacements or {}).items():
        norm = SLOT_ALIASES.get(str(raw_slot).strip().lower()) or str(raw_slot).strip()
        repl = _parse_replacement(value, setid, is_weapon)
        if not repl or not repl.get("path"):
            errors.append(f"{raw_slot}: 解析不到部件路径")
            continue
        if repl.get("lib") is None:
            if repl.get("missing"):
                errors.append(f"{raw_slot}: 该部件路径不在离线资产库索引里（名字不对或未收录）: {repl.get('path')}")
            else:
                errors.append(
                    f"{raw_slot}: 该部件只存在于 DLC set {repl.get('needs_set')}，当前物品 set {setid} 表示不了；"
                    f"用同 set 的物品或等后续实现"
                )
            continue
        path = repl["path"]
        rtype = _part_type(path, is_weapon)
        if not rtype:
            errors.append(f"{raw_slot}: 该路径不是已登记的命中部件/名称部件: {path}")
            continue
        explicit = str(raw_slot).strip().lower() in SLOT_ALIASES
        if explicit and rtype != norm:
            errors.append(f"{raw_slot}: 部件类型 {rtype} 与指定槽位 {norm} 不匹配")
            continue
        slot_key = norm if explicit else rtype
        idxs = by_slot.get(slot_key) or []
        if not idxs:
            if not allow_append:
                warnings.append(f"{slot_key}: 该物品当前没有这个槽位，跳过；如确要追加用 --allow-append")
                continue
            idx = len(parts)
            parts.append({"lib": repl["lib"], "asset": repl["asset"]})
            warnings.append(f"{slot_key}: 物品没有该槽位，已按 --allow-append 追加（未验证游戏是否接受）")
        else:
            idx = idxs[0]
            if len(idxs) > 1:
                warnings.append(f"{slot_key}: 当前有 {len(idxs)} 个同类槽位，只替换了第 {idx} 个")
            parts[idx] = {"lib": repl["lib"], "asset": repl["asset"]}
        old = current[idx] if idx < len(current) else None
        changes.append(
            {
                "slot": slot_key,
                "index": idx,
                "old_path": (old or {}).get("path"),
                "old_ref": (old or {}).get("raw"),
                "new_path": path,
                "new_ref": {"lib": repl["lib"], "asset": repl["asset"]},
            }
        )
        # 静态检查（不是游戏验证）
        if is_weapon and slot_key in allowed_slot_fields:
            field = allowed_slot_fields[slot_key]
            list_path = weapon_type.get(field)
            allowed = part_meta().get("weapon_part_lists", {}).get(list_path or "")
            if allowed and path not in allowed:
                warnings.append(f"{slot_key}: 该部件不在武器类型 {weapon_type.get('name','')} 的允许列表里（可能被游戏忽略/清理，未验证）")
        if is_weapon and slot_key in ("Body", "Grip", "Barrel", "Stock", "Material") and manufacturer:
            hits = [m for m in known_manufacturers if m and ("_" + m) in path]
            if hits and manufacturer not in hits:
                warnings.append(f"{slot_key}: 部件名含 {hits}，物品制造商是 {manufacturer}（仅提示，混用可能产生异常/被清理，未验证）")
        if not is_weapon and slot_key in ITEM_SLOTS and slot_key not in ("Prefix", "Title"):
            category = _item_category(
                (base["resolved"].get("balance") or {}).get("path"),
                (base["resolved"].get("type") or {}).get("path"),
            )
            allowed = _item_allowed_paths(category)
            if allowed and path not in allowed:
                warnings.append(
                    f"{slot_key}: 不在 {category} 类别的物品部件列表并集里（数据可能不全；是否被忽略/清理未验证）"
                )
        if slot_key == "Elemental" and len(idxs) > 1:
            warnings.append(f"{slot_key}: 物品原本已有多个元素槽，替换后可能有冲突（未验证）")
        if slot_key in ("Prefix", "Title"):
            warnings.append(f"{slot_key}: 这是名称部件，只影响显示名/前缀，不是战斗属性")

    new_code = itemcodes.encode(info) if info.get("parts") is not None else code
    roundtrip = decode_resolved(new_code)
    for ch in changes:
        rt_parts = roundtrip["resolved"]["parts"]
        if ch["index"] < len(rt_parts):
            got = rt_parts[ch["index"]]
            if got.get("path") != ch["new_path"]:
                warnings.append(f"{ch['slot']}: 重新编码后路径与预期不一致（{got.get('path')} != {ch['new_path']}）")
        else:
            warnings.append(f"{ch['slot']}: 追加槽位没有出现在重新编码结果里（编码器可能丢弃尾部/固定槽位数）")
    risk = itemcodes.assess_risk(new_code)
    if risk.get("level", 0) > 80:
        warnings.append(
            f"等级 {risk['level']} 属于 80+OP 范围；能否被接受取决于存档 OP 等级，本预览未提供 OP 上下文"
        )
    return {
        "original": code,
        "new_code": new_code,
        "changes": changes,
        "warnings": warnings,
        "errors": errors,
        "risk": risk,
        "resolved_after": roundtrip["resolved"],
        "note": "PREVIEW ONLY：静态组合预览，没有写存档/profile，也没有进游戏验证。",
    }


def stats() -> dict[str, Any]:
    m = part_meta()
    slots = {}
    for group in ("weapon_parts", "item_parts"):
        isw = group == "weapon_parts"
        for v in m[group].values():
            t = v.get("type") or ""
            key = (WEAPON_SLOTS if isw else ITEM_SLOTS).get(t, t)
            slots[key] = slots.get(key, 0) + 1
    return {
        "asset_library_sets": sorted(_sets()),
        "weapon_part_defs": len(m["weapon_parts"]),
        "item_part_defs": len(m["item_parts"]),
        "name_part_defs": len(m["name_parts"]),
        "weapon_type_defs": len(m["weapon_types"]),
        "weapon_part_list_count": len(m["weapon_part_lists"]),
        "item_part_list_count": len(m["item_part_lists"]),
        "part_defs_by_slot": dict(sorted(slots.items(), key=lambda x: -x[1])),
        "game_totals": game_totals(),
        "note": "部件定义数≠全游戏实例数；同义/等级/厂商变体可能复用路径。",
    }


def coverage_report() -> dict[str, Any]:
    lib = gunlib.load_library()
    specials = json.loads(SPECIALS_FILE.read_text(encoding="utf-8")) if SPECIALS_FILE.is_file() else []
    totals = game_totals()
    lib_weapons = sum(1 for e in lib if e.get("is_weapon"))
    lib_items = len(lib) - lib_weapons
    levels: dict[str, int] = {}
    for e in lib:
        levels[str(e.get("level"))] = levels.get(str(e.get("level")), 0) + 1
    cats: dict[str, int] = {}
    for e in lib:
        cats[e.get("category") or "(无分类)"] = cats.get(e.get("category") or "(无分类)", 0) + 1
    # 代码级重叠（忽略 key）
    def canon(code):
        try:
            return itemcodes.with_key_zero(code)
        except Exception:
            return code

    lib_codes = {canon(e["code"]) for e in lib if e.get("code")}
    sp_codes = {canon(e["code"]) for e in specials if e.get("code")}
    overlap = len(lib_codes & sp_codes)
    specials_by_kind: dict[str, int] = {}
    specials_by_rarity: dict[str, int] = {}
    for e in specials:
        specials_by_kind[e.get("kind") or "?"] = specials_by_kind.get(e.get("kind") or "?", 0) + 1
        specials_by_rarity[e.get("rarity") or "?"] = specials_by_rarity.get(e.get("rarity") or "?", 0) + 1
    ib_cat = totals.get("item_balance_by_category") or {}
    comparisons = {
        "weapons": {
            "special_entries": specials_by_kind.get("武器", 0),
            "weapon_balance_defs": totals.get("weapon_balance_defs"),
            "unique_weapon_bases": totals.get("weapon_balance_unique_bases"),
            "note": "WeaponBalance 是 balance 定义数（含通用/DLC/等级变体），不等于“命名特殊武器总数”",
        },
        "shields": {
            "special_entries": specials_by_kind.get("护盾", 0),
            "item_balance_defs": ib_cat.get("shield"),
        },
        "grenades": {
            "special_entries": specials_by_kind.get("手雷", 0),
            "item_balance_defs": ib_cat.get("grenade"),
        },
        "relics": {
            "special_entries": specials_by_kind.get("圣物", 0),
            "item_balance_defs": ib_cat.get("relic"),
        },
        "class_mods": {
            "special_entries": specials_by_kind.get("模组", 0),
            "item_balance_classmod": ib_cat.get("classmod"),
            "items_json_classmod": 114,
            "items_json_cross_dlc_classmod": 24,
            "note": "dumps 分类计数口径不一致，class mod 总数需 Codex 总索引复核",
        },
        "items": {
            "special_entries": specials_by_kind.get("道具", 0),
            "item_defs_total": totals.get("item_defs"),
            "note": "特殊表里的“道具”不是全游戏 ItemDefinition",
        },
    }
    gaps = [
        "343 条是社区/资料收录的命名特殊装备，不是全游戏总数：dumps 有 1421 条 ItemBalance、353 条 WeaponBalance、1344 条 ItemDefinition。",
        f"护盾/圣物覆盖偏低：特殊表 {specials_by_kind.get('护盾',0)} 条 vs shield balance {ib_cat.get('shield')}；{specials_by_kind.get('圣物',0)} vs relic balance {ib_cat.get('relic')}。",
        f"635 条代码库中 {lib_weapons} 武器 / {lib_items} 物品，含重复/自改分类（{len(cats)} 个分类标签），不能当作全游戏条目数。",
        "part 定义 893 武器 / 635 物品，但 set0 资产库里有 1508 武器部件资产 / 930 物品部件资产；名称部件另有 876+757 条，说明还有未进入“部件定义”索引的资产。",
        "DLC 的 set 1-22 中部分部件/类型资产未全部出现在 Weapon_Parts/Item_Parts 元数据里；跨 DLC 组合需要实机验证。",
        "没有实机验证物品清理/重写规则；SanitySaver 作者说明游戏会做 sanity check，缺失/不可序列化部件会被删，需按风险边界处理。",
    ]
    return {
        "note": "覆盖率基于 dumps 的分类计数，不是官方“全物品清单”；不同来源口径不同，比例只用于发现缺口。",
        "code_library": {
            "total": len(lib),
            "weapons": lib_weapons,
            "items": lib_items,
            "unique_codes_ignoring_key": len(lib_codes),
            "level_distribution": dict(sorted(levels.items(), key=lambda x: int(x[0]))),
            "top_categories": dict(sorted(cats.items(), key=lambda x: -x[1])[:15]),
        },
        "special_table": {
            "total": len(specials),
            "by_kind": specials_by_kind,
            "by_rarity": specials_by_rarity,
            "unique_codes": len(sp_codes),
        },
        "overlap_code_library_vs_specials": overlap,
        "gibbed_totals": {
            k: totals.get(k)
            for k in (
                "weapon_balance_defs",
                "weapon_balance_unique_bases",
                "item_balance_defs",
                "item_defs",
                "weapon_part_defs",
                "item_part_defs",
                "weapon_part_assets_set0",
                "item_part_assets_set0",
                "weapon_type_defs",
            )
        },
        "comparisons": comparisons,
        "gaps": gaps,
    }
