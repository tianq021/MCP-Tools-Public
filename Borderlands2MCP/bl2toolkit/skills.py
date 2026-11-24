"""技能修改：未分配技能点、已学技能等级、技能树校验、按职业填充/重置。

数据来源（只读）：
- `data/skills/skills.json`（Codex 提供的全职业技能表，含英文名/树/层级/最大等级/内部路径）
- `data/skills/native_skill_definitions.json`（本机本地化里提取的技能定义）
- 角色存档里已学技能的实际路径（用于补全 skills.json 中未匹配到路径的技能）

存档里的技能字段：
- 顶层 field4 = 未分配技能点（int）
- 顶层 field8 = 重复条目 {1: 技能内部路径(bytes), 2: 等级, 3/4: 未知}
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from borderlands.datautil.protobuf import read_protobuf, write_protobuf

from . import DATA_DIR
from .paths import list_saves
from .saves import F_SKILL_POINTS, F_SKILLS, SaveFile

CATALOG_FILE = DATA_DIR / "skills" / "skills.json"

#: 存档里的职业类 -> skills.json 的 class_id
CLASS_ID_BY_PATH: dict[str, str] = {
    "GD_Mercenary.Character.CharClass_Mercenary": "gunzerker",
    "GD_Siren.Character.CharClass_Siren": "siren",
    "GD_Assassin.Character.CharClass_Assassin": "assassin",
    "GD_Soldier.Character.CharClass_Soldier": "commando",
    "GD_Tulip_Mechromancer.Character.CharClass_Mechromancer": "mechromancer",
    "GD_Lilac_PlayerClass.Character.CharClass_LilacPlayerClass": "psycho",
}

CLASS_LABELS = {
    "gunzerker": "狂枪手 Salvador",
    "siren": "魔女 Maya",
    "assassin": "刺客 Zer0",
    "commando": "突击兵 Axton",
    "mechromancer": "机械术士 Gaige",
    "psycho": "疯子 Krieg",
}

#: 技能树内层级解锁规则（社区通用：每 5 点解锁下一层）
TIER_POINTS = 5

_catalog_cache: Optional[list[dict[str, Any]]] = None
_path_cache: Optional[dict[str, dict[str, str]]] = None


def _norm(text: str) -> str:
    """名字归一化：小写、只保留字母数字，并把 0 当 o（Zer0 的技能名里有 0）。"""
    t = (text or "").lower().replace("%", "percent")
    t = re.sub(r"[^a-z0-9]", "", t)
    return t.replace("0", "o")


#: 技能表里的英文名和内部路径不一致的已知情况（来源：bl2skills 数据对照/存档实际路径）
_NAME_FIXES = {
    "shockandaaagggghhh": "shockandaaaggghhh",
    "divergentlikeness": "divergentlikness",
    "yippiekiyay": "yippeekiyay",
    "alloutofbubblegum": "outofbubblegum",
    "hardtokill": "diehard",
    "maglock": "maglock",
    "bloodtwitch": "bloodytwitch",
    "buzzaxebombardier": "buzzaxebombadier",
    "emptytherage": "emptyrage",
    "20cooler": "20percentcooler",
    "summondeathtrap": "deathtrap",
    "sabreturret": "scorpio",
    "phaselock": "phaselock",
    "criticalascension": "criticalascention",
}


def _path_aliases(path: str) -> set[str]:
    """内部路径最后一段的多个别名（去掉 Skill_ 前缀/后缀）。"""
    segment = path.rsplit(".", 1)[-1]
    aliases = {segment}
    if segment.lower().startswith("skill"):
        aliases.add(segment[5:].lstrip("_"))
    if segment.lower().endswith("skill"):
        aliases.add(segment[:-5].rstrip("_"))
    return {_norm(a) for a in aliases if a}


def load_catalog() -> list[dict[str, Any]]:
    global _catalog_cache
    if _catalog_cache is None:
        if CATALOG_FILE.is_file():
            _catalog_cache = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
        else:
            _catalog_cache = []
    return _catalog_cache


def _learned_paths_from_saves() -> dict[str, set[str]]:
    """从本机存档的已学技能里收集路径（只读），用于补全 skills.json 缺失的路径。"""
    result: dict[str, set[str]] = {}
    try:
        from .paths import find_steam_id_dir
        from .saves import SaveFile

        d = find_steam_id_dir()
        if d is None:
            return result
        for p in list_saves(d):
            try:
                sf = SaveFile(p)
            except Exception:  # noqa: BLE001
                continue
            cid = CLASS_ID_BY_PATH.get(sf.class_path)
            if not cid:
                continue
            bucket = result.setdefault(cid, set())
            for skill in sf.list_skills():
                if skill["name"]:
                    bucket.add(skill["name"])
    except Exception:  # noqa: BLE001
        pass
    return result


def path_index() -> dict[str, dict[str, str]]:
    """返回 class_id -> {归一化技能名: 内部路径}。"""
    global _path_cache
    if _path_cache is not None:
        return _path_cache
    catalog = load_catalog()
    learned = _learned_paths_from_saves()
    index: dict[str, dict[str, str]] = {}
    for item in catalog:
        cid = item["class_id"]
        for m in item.get("native_matches") or []:
            path = m.get("path")
            if path:
                index.setdefault(cid, {})[_norm(item["name"])] = path
                # 路径最后一段也作为别名
                index[cid].setdefault(_norm(path.rsplit(".", 1)[-1]), path)
    for cid, paths in learned.items():
        bucket = index.setdefault(cid, {})
        for path in paths:
            for alias in _path_aliases(path):
                bucket.setdefault(alias, path)
    _path_cache = index
    return index


def _lookup_path(class_id: str, name: str) -> Optional[str]:
    """按技能英文名找内部路径（带已知名字修正）。"""
    index = path_index().get(class_id, {})
    key = _norm(name)
    path = index.get(key)
    if path:
        return path
    fixed = _NAME_FIXES.get(key)
    if fixed:
        return index.get(fixed)
    # 反向：路径别名反过来包含技能名
    for alias, candidate in index.items():
        if key in alias or alias in key:
            return candidate
    return None


@dataclass
class SkillInfo:
    class_id: str
    name: str
    tree: str
    tier: int
    max_rank: int
    path: Optional[str]

    def display(self) -> str:
        path = self.path or "（未找到内部路径）"
        return f"[{self.tree} T{self.tier}] {self.name} 0/{self.max_rank}  {path}"


def class_skills(class_id: str) -> list[SkillInfo]:
    result = []
    for item in load_catalog():
        if item["class_id"] != class_id:
            continue
        path = _lookup_path(class_id, item["name"])
        if path is None:
            for m in item.get("native_matches") or []:
                path = m.get("path")
                if path:
                    break
        result.append(
            SkillInfo(
                class_id=class_id,
                name=item["name"],
                tree=item["tree"],
                tier=int(item.get("tier", 0)),
                max_rank=int(item.get("max_rank", 5)),
                path=path,
            )
        )
    return result


def resolve_class_id(save: SaveFile) -> Optional[str]:
    return CLASS_ID_BY_PATH.get(save.class_path)


def find_skill(save: SaveFile, query: str) -> SkillInfo:
    """按技能名/内部路径查找当前角色的技能（大小写不敏感、支持部分匹配）。"""
    cid = resolve_class_id(save)
    if cid is None:
        raise ValueError(f"未知职业: {save.class_path}")
    skills = class_skills(cid)
    q = (query or "").strip()
    if not q:
        raise ValueError("技能名不能为空")
    # 完整路径优先
    for s in skills:
        if s.path and s.path.lower() == q.lower():
            return s
    # 精确名字
    for s in skills:
        if s.name.lower() == q.lower():
            return s
    # 归一化精确
    nq = _norm(q)
    for s in skills:
        if _norm(s.name) == nq:
            return s
    # 部分匹配
    matches = [s for s in skills if nq in _norm(s.name) or (s.path and nq in _norm(s.path))]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError(f"在 {CLASS_LABELS.get(cid, cid)} 的技能表里找不到 {query!r}")
    names = ", ".join(s.name for s in matches[:8])
    raise ValueError(f"技能名 {query!r} 匹配到多个：{names}，请写更完整")

    return matches[0]


# --------------------------------------------------------------------------
# 存档读写
# --------------------------------------------------------------------------


def _skill_entries(player) -> list[Any]:
    return player.get(F_SKILLS) or []


def current_levels(save: SaveFile) -> dict[str, int]:
    return {s["name"]: s["level"] for s in save.list_skills()}


def _set_level_raw(save: SaveFile, path: str, level: int) -> bool:
    """直接写入某个内部路径的等级（0 表示删除条目），返回是否发生变化。

    路径比较忽略大小写（行动技能在存档里可能是 skill_psycho / Skill_Psycho 这种大小写差异）。
    """
    changed = {"value": False}
    path_bytes = path.encode("utf-8")
    path_lower = path.lower()

    def do(player):
        entries = player.get(F_SKILLS) or []
        found = False
        kept = []
        for entry in entries:
            try:
                inner = read_protobuf(entry[1])
                inner_path = inner[1][0][1] if 1 in inner else b""
            except Exception:  # noqa: BLE001
                kept.append(entry)
                continue
            if inner_path.decode("utf-8", "replace").lower() == path_lower:
                found = True
                if level <= 0:
                    changed["value"] = True
                    continue  # 删除
                if int(inner[2][0][1]) != level:
                    inner[2][0][1] = int(level)
                    entry[1] = write_protobuf(inner)
                    changed["value"] = True
                kept.append(entry)
            else:
                kept.append(entry)
        if level > 0 and not found:
            inner = {
                1: [[2, path_bytes]],
                2: [[0, int(level)]],
                3: [[0, 0]],
                4: [[0, 0]],
            }
            kept.append([2, write_protobuf(inner)])
            changed["value"] = True
        player[F_SKILLS] = kept

    save._edit_player(do)
    return changed["value"]


def tree_points(save: SaveFile, tree: str) -> int:
    cid = resolve_class_id(save)
    if cid is None:
        return 0
    catalog = {s.name: s for s in class_skills(cid)}
    total = 0
    for name, level in current_levels(save).items():
        info = _by_path_or_name(catalog, name)
        if info and info.tree == tree:
            total += level
    return total


def _by_path_or_name(catalog: dict[str, SkillInfo], path: str) -> Optional[SkillInfo]:
    for info in catalog.values():
        if info.path == path:
            return info
    # 用路径最后一段匹配
    last = _norm(path.rsplit(".", 1)[-1])
    for info in catalog.values():
        if _norm(info.name) == last:
            return info
    return None


def skill_summary(save: SaveFile) -> dict[str, Any]:
    cid = resolve_class_id(save)
    catalog = class_skills(cid) if cid else []
    by_path_lower = {s.path.lower(): s for s in catalog if s.path}
    by_name_norm = {_norm(s.name): s for s in catalog}
    learned = []
    internal = []
    for skill in save.list_skills():
        path = skill["name"]
        info = by_path_lower.get(path.lower()) or by_name_norm.get(_norm(path.rsplit(".", 1)[-1]))
        item = {
            "path": path,
            "level": skill["level"],
            "name": info.name if info else None,
            "tree": info.tree if info else None,
            "tier": info.tier if info else None,
            "max_rank": info.max_rank if info else None,
            "over_max": bool(info and skill["level"] > info.max_rank),
        }
        if info is None:
            # 机械术士/疯子等存档里的内部辅助技能（firestatusdetector 之类）或行动技能大小写差异，
            # 不属于普通技能树，不能当普通技能处理
            internal.append(item)
        else:
            learned.append(item)
    trees: dict[str, int] = {}
    for item in learned:
        if item["tree"]:
            trees[item["tree"]] = trees.get(item["tree"], 0) + item["level"]
    return {
        "character": save.character_name,
        "class_id": cid,
        "class_label": CLASS_LABELS.get(cid or "", cid),
        "unspent": save.unspent_skill_points,
        "used": save.used_skill_points,
        "learned": learned,
        "internal_skills": internal,
        "tree_points": trees,
        "catalog_count": len(catalog),
        "catalog_with_path": sum(1 for s in catalog if s.path),
    }


def _level_of(save: SaveFile, path: str) -> int:
    """按路径查等级（忽略大小写，兼容 skill_psycho / Skill_Psycho）。"""
    target = path.lower()
    for name, level in current_levels(save).items():
        if name.lower() == target:
            return level
    return 0


def set_skill_level(
    save: SaveFile,
    skill: str,
    level: int,
    *,
    allow_over_max: bool = False,
    enforce_tier: bool = False,
) -> dict[str, Any]:
    """设置一个技能的等级。

    - level=0 表示移除该技能
    - 默认不允许超过技能最大等级（allow_over_max=True 可放开，风险自负）
    - enforce_tier=True 时按"每 5 点解锁下一层"的技能树规则校验
    """
    info = find_skill(save, skill)
    if info.path is None:
        raise ValueError(f"技能 {info.name} 没有内部路径，暂不支持写入")
    if level < 0 or level > 255:
        raise ValueError("技能等级必须在 0..255")
    if level > info.max_rank and not allow_over_max:
        raise ValueError(f"{info.name} 最大等级是 {info.max_rank}，超过请加 --allow-over-max（风险自负）")
    if enforce_tier and level > 0:
        need = TIER_POINTS * max(0, info.tier - 1)
        have = tree_points(save, info.tree)
        if have < need:
            raise ValueError(
                f"{info.tree} 树当前只点了 {have} 点，T{info.tier} 需要 {need} 点才能解锁（可用 --no-tier-check 跳过）"
            )
    before = _level_of(save, info.path)
    changed = _set_level_raw(save, info.path, level)
    after = _level_of(save, info.path)
    return {
        "skill": info.name,
        "tree": info.tree,
        "tier": info.tier,
        "path": info.path,
        "before": before,
        "after": after,
        "max_rank": info.max_rank,
        "changed": changed,
        "warnings": (["等级超过技能最大等级，游戏内可能显示异常或不被识别"] if level > info.max_rank else []),
    }


def fill_tree(save: SaveFile, tree: str) -> dict[str, Any]:
    """把一个技能树里所有技能点到各自上限。"""
    cid = resolve_class_id(save)
    if cid is None:
        raise ValueError("未知职业")
    skills = [s for s in class_skills(cid) if s.tree.lower() == tree.lower()]
    if not skills:
        raise ValueError(f"找不到技能树 {tree!r}")
    filled = 0
    for info in skills:
        if info.path is None:
            continue
        _set_level_raw(save, info.path, info.max_rank)
        filled += 1
    return {"tree": tree, "skills_filled": filled, "used": save.used_skill_points, "unspent": save.unspent_skill_points}


def fill_all(save: SaveFile) -> dict[str, Any]:
    cid = resolve_class_id(save)
    if cid is None:
        raise ValueError("未知职业")
    filled = 0
    for info in class_skills(cid):
        if info.path is None or info.tier == 0 and info.tree == "Action Skill":
            continue
        _set_level_raw(save, info.path, info.max_rank)
        filled += 1
    return {"skills_filled": filled, "used": save.used_skill_points, "unspent": save.unspent_skill_points}


def reset_skills(save: SaveFile) -> dict[str, Any]:
    """清空所有"技能树里的技能"，保留存档里的内部辅助技能条目。

    机械术士/疯子等职业的存档里有一些不属于普通技能树的内部条目
    （例如 firestatusdetector、appliedstatuseffectlistener、_Bloodlust 等），
    直接删掉可能影响游戏行为，所以这里只移除目录里能识别的技能。
    """
    cid = resolve_class_id(save)
    catalog_paths = {s.path.lower() for s in (class_skills(cid) if cid else []) if s.path}
    before = save.used_skill_points
    removed = {"count": 0}

    def do(player):
        entries = player.get(F_SKILLS) or []
        kept = []
        for entry in entries:
            try:
                inner = read_protobuf(entry[1])
                path = inner[1][0][1].decode("utf-8", "replace") if 1 in inner else ""
            except Exception:  # noqa: BLE001
                kept.append(entry)
                continue
            if path.lower() in catalog_paths:
                removed["count"] += 1
                continue
            kept.append(entry)
        player[F_SKILLS] = kept

    save._edit_player(do)
    return {
        "before_used": before,
        "after_used": save.used_skill_points,
        "unspent": save.unspent_skill_points,
        "removed_entries": removed["count"],
    }


def set_unspent(save: SaveFile, points: int) -> int:
    return save.set_skill_points(points)
