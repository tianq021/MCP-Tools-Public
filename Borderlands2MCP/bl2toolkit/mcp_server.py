"""无主之地2 MCP 服务端（stdio）。

把 bl2toolkit 的能力暴露成 MCP 工具，让 DSH 等客户端可以直接：
查看存档、改技能点、导出/导入武器代码（同步武器）、解码制作黑枪代码、查本地黑枪代码库。

启动：
    python -m bl2toolkit.mcp_server

注意：写操作默认需要显式调用；sync 类工具默认 dry_run=True，避免误写。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Optional

from mcp.server.fastmcp import FastMCP

from . import __version__, itemcodes
from . import balance as bal
from . import library as gunlib
from . import parts as partmod
from . import xp as xpmod
from . import profile as profilemod
from . import skill_effects as fx
from . import skills as skillmod
from . import space as spacemod
from . import specials as spec
from .paths import find_steam_id_dir, is_game_running, list_backups, list_saves, resolve_save, restore_backup
from .saves import SECTION_FIELDS, SaveFile

mcp = FastMCP("borderlands2")

# 只读研究工具由 Codex 维护（research_tools.py / research-codex）；只注册，不修改。
try:
    from research_tools import register as register_research_tools

    register_research_tools(mcp)
except ImportError:  # 研究工具缺失时不影响主服务
    pass


def _open(slot: str) -> SaveFile:
    return SaveFile(resolve_save(slot))


def _ok(data: Any) -> Any:
    return data


# --------------------------------------------------------------------------
# 只读工具
# --------------------------------------------------------------------------


@mcp.tool()
def bl2_status() -> dict[str, Any]:
    """查看 BL2 工具状态：存档目录、存档数量、游戏是否在运行、代码库条目数、版本。"""
    save_dir = find_steam_id_dir()
    saves = list_saves(save_dir) if save_dir else []
    return {
        "version": __version__,
        "save_dir": str(save_dir) if save_dir else None,
        "saves": len(saves),
        "game_running": is_game_running(),
        "code_library": len(gunlib.load_library()),
        "code_library_file": str(gunlib.LIBRARY_FILE),
    }


@mcp.tool()
def bl2_list_saves() -> list[dict[str, Any]]:
    """列出所有无主之地2角色存档（槽位、名字、职业、等级、技能点、物品数量）。"""
    save_dir = find_steam_id_dir()
    if save_dir is None:
        return []
    out = []
    for path in list_saves(save_dir):
        try:
            out.append(_open(str(path)).summary())
        except Exception as exc:  # noqa: BLE001
            out.append({"file": str(path), "slot": path.stem, "error": str(exc)})
    return out


@mcp.tool()
def bl2_character_info(slot: str) -> dict[str, Any]:
    """查看指定角色存档详情。slot 可以是 1 / Save0001 / 完整路径。"""
    sf = _open(slot)
    data = sf.summary()
    data["skills"] = sf.list_skills()
    return data


@mcp.tool()
def bl2_export_item_codes(slot: str, sections: str = "bank,items,weapons") -> dict[str, Any]:
    """导出角色的物品代码（BL2(...) 格式），可用于备份或同步到别的角色。

    sections: 逗号分隔，可选 bank / items / weapons。
    """
    sf = _open(slot)
    secs = tuple(s.strip() for s in sections.split(",") if s.strip())
    return {"slot": slot, "character": sf.character_name, "codes": sf.export_codes(secs)}


@mcp.tool()
def bl2_decode_item_code(code: str) -> dict[str, Any]:
    """解码一条 BL2(...) 物品代码，返回类型/厂商/等级/配件编号（做黑枪的基础）。"""
    data = itemcodes.decode(code)
    data.pop("raw_hex", None)
    return data


@mcp.tool()
def bl2_describe_item_code(code: str) -> str:
    """用一行文字描述物品代码（等级、头部、配件编号）。"""
    return itemcodes.describe(code)


@mcp.tool()
def bl2_search_gun_library(query: str = "", kind: str = "", limit: int = 30) -> list[dict[str, Any]]:
    """搜索本地黑枪/特殊装备代码库（按中文名、备注、分区）。

    kind: 留空=全部, weapon=只搜武器, item=只搜护盾/手雷/模组/圣物等。
    """
    return gunlib.search(query, kind=kind or None, limit=limit)


@mcp.tool()
def bl2_special_stats() -> dict[str, Any]:
    """特殊装备总表（传说/珠光/彩色/炽天使/E科技/独特）的统计信息。"""
    return spec.stats()


@mcp.tool()
def bl2_special_weapons(
    query: str = "",
    rarity: str = "",
    kind: str = "",
    limit: int = 30,
) -> list[dict[str, Any]]:
    """搜索特殊装备总表（每条都带可直接导入的 BL2(...) 代码）。

    rarity: 传说(橙) / 珠光(青) / 彩色(彩虹) / 炽天使(粉) / 深红色或品红(E科技) /
            独特(蓝紫红字) / 蓝装 / 紫装 / 龙堡 / 留空=全部
    kind: 武器 / 护盾 / 手雷 / 模组 / 圣物 / 道具，留空=全部
    """
    return spec.search(query, rarity=rarity or None, kind=kind or None, limit=limit)


@mcp.tool()
def bl2_add_special_weapon(
    slot: str,
    name_or_code: str,
    count: int = 1,
    where: str = "bank",
    dedupe: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """把特殊装备总表里的一条直接导入角色（传说/彩色皮/炽天使/E科技/独特等）。

    name_or_code: 表里的名字（如 "不洁哈罗德" / "蜜蜂"），或直接给 BL2(...) 代码。
    写前自动备份。
    """
    sf = _open(slot)
    try:
        result = spec.add_to_save(sf, name_or_code, count=count, where=where, dedupe=dedupe)
    except ValueError as exc:
        return {"error": str(exc)}
    if not dry_run:
        sf.save(backup=True)
    result["slot"] = slot
    result["written"] = not dry_run
    return result


@mcp.tool()
def bl2_list_backups() -> list[dict[str, Any]]:
    """列出自动备份（改存档前生成），可用于回滚。"""
    return list_backups()


@mcp.tool()
def bl2_space_info(slot: str) -> dict[str, Any]:
    """查看角色的背包/银行空间：当前容量、SDU 等级、已用格数、当前游戏 exe 的实际上限与风险提示。"""
    sf = _open(slot)
    return spacemod.space_info(sf)


@mcp.tool()
def bl2_set_space(
    slot: str,
    backpack: Optional[int] = None,
    bank: Optional[int] = None,
    allow_oversize: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """修改背包/银行容量（原版：背包 12-39 步长 3，银行 6-24 步长 2）。

    超过原版上限需要 allow_oversize=True，并且必须配合 exe 补丁或 PythonSDK mod 才能生效；
    只改存档时游戏会按当前 exe 的上限 clamp。写前自动备份。
    """
    sf = _open(slot)
    result = spacemod.set_space(sf, backpack=backpack, bank=bank, allow_oversize=allow_oversize)
    if not dry_run:
        sf.save(backup=True)
    result["slot"] = slot
    result["written"] = not dry_run
    return result


@mcp.tool()
def bl2_exe_caps() -> dict[str, Any]:
    """只读检测当前 Borderlands2.exe 里硬编码的背包/银行上限（原版是 39/24），以及 exe 备份列表。"""
    caps = spacemod.analyze_installed()
    return {
        "exe_caps": caps.to_dict() if caps else None,
        "exe_backups": spacemod.list_exe_backups(),
        "vanilla_limits": spacemod.VANILLA_LIMITS,
        "note": "修改 exe 请用 CLI: bl2.cmd space patch-exe --backpack-max 100 --apply --yes（会自动备份）",
    }


# --------------------------------------------------------------------------
# 技能修改
# --------------------------------------------------------------------------


@mcp.tool()
def bl2_item_risk(code: str) -> dict[str, Any]:
    """检查一条物品代码的结构风险（数据版本/等级/关键部件/配件数），不启动游戏。"""
    return itemcodes.assess_risk(code)


@mcp.tool()
def bl2_skill_available(target: str) -> list[dict[str, Any]]:
    """列出某职业（或某个存档角色）的全部技能、技能树、层级、最大等级和内部路径。

    target: 职业 class_id（commando/siren/assassin/gunzerker/mechromancer/psycho）或存档槽位（如 1）。
    """
    cid = target
    if cid not in skillmod.CLASS_LABELS:
        sf = _open(target)
        cid = skillmod.resolve_class_id(sf) or ""
    return [s.__dict__ for s in skillmod.class_skills(cid)] if cid else []


@mcp.tool()
def bl2_skill_summary(slot: str) -> dict[str, Any]:
    """查看角色技能：未分配点、已用点、各树投点、已学技能（含超上限标记）。"""
    sf = _open(slot)
    return skillmod.skill_summary(sf)


@mcp.tool()
def bl2_skill_set(
    slot: str,
    skill: str,
    level: int,
    allow_over_max: bool = False,
    enforce_tier: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """设置一个技能的等级（level=0 移除该技能）。写前自动备份。

    enforce_tier=True 时按"每 5 点解锁下一层"校验技能树层级。
    """
    sf = _open(slot)
    result = skillmod.set_skill_level(
        sf, skill, level, allow_over_max=allow_over_max, enforce_tier=enforce_tier
    )
    if not dry_run:
        sf.save(backup=True)
    result["slot"] = slot
    result["written"] = not dry_run
    return result


@mcp.tool()
def bl2_skill_fill(slot: str, tree: str = "", all_skills: bool = False, dry_run: bool = False) -> dict[str, Any]:
    """把一个技能树（tree）或全部技能树点满。写前自动备份。"""
    sf = _open(slot)
    if all_skills:
        result = skillmod.fill_all(sf)
    elif tree:
        result = skillmod.fill_tree(sf, tree)
    else:
        return {"error": "需要 tree 或 all_skills=True"}
    if not dry_run:
        sf.save(backup=True)
    result["slot"] = slot
    result["written"] = not dry_run
    return result


@mcp.tool()
def bl2_skill_reset(slot: str, dry_run: bool = False) -> dict[str, Any]:
    """清空角色所有已学技能（不改未分配点数）。写前自动备份。"""
    sf = _open(slot)
    before = sf.used_skill_points
    if dry_run:
        return {"slot": slot, "before_used": before, "written": False}
    result = skillmod.reset_skills(sf)
    sf.save(backup=True)
    result["slot"] = slot
    result["written"] = True
    return result


@mcp.tool()
def bl2_skill_effect_capabilities(query: str = "", grade: str = "") -> dict[str, Any]:
    """技能效果扩展能力表（证据分级 L1 调参 / L2 追加属性 / L3 移植 / L4 新事件逻辑）。

    这是**证据表**，不是已生效功能；返回每条效果的原生属性/Hook、证据来源、依赖、持久化/叠加限制和可实现度。
    """
    return {
        "summary": fx.summary(),
        "effects": fx.list_effects(query, grade),
    }


@mcp.tool()
def bl2_skill_effect_plan(effect_id: str) -> dict[str, Any]:
    """某条技能效果（如 lifesteal、hp_regen、extra_projectiles、on_kill）的证据+补丁/模组预览。

    预览明确标记 PREVIEW ONLY：没有启动游戏、没有安装 mod、没有写真实存档，不代表已经生效。
    返回里同时标注每个属性的 BL2/TPS 适用性。
    """
    try:
        return fx.build_plan(effect_id)
    except ValueError as exc:
        return {"error": str(exc)}


@mcp.tool()
def bl2_parts_stats() -> dict[str, Any]:
    """离线部件目录统计（Gibbed dumps）：部件/名称部件/武器类型/槽位与全游戏 dumps 计数。"""
    return partmod.stats()


@mcp.tool()
def bl2_parts_search(query: str = "", kind: str = "", limit: int = 30) -> dict[str, Any]:
    """搜索离线部件目录：kind=weapon/item/name；返回路径/类型/槽位/lib:asset。"""
    rows = partmod.search_parts(query, kind, limit)
    return {"count": len(rows), "parts": rows, "note": "来自 Gibbed dumps 的静态数据，未实机验证"}


@mcp.tool()
def bl2_part_slots() -> dict[str, Any]:
    """武器/物品部件的槽位名映射（SanitySaver 的 PartDefinition 名称口径）。"""
    return partmod.slot_table()


@mcp.tool()
def bl2_parts_coverage() -> dict[str, Any]:
    """635 代码库/343 特殊表对 Gibbed dumps 总数的覆盖与缺口；不要把条目数当全游戏总数。"""
    return partmod.coverage_report()


@mcp.tool()
def bl2_gun_preview(code: str, parts: list[str] = [], allow_append: bool = False) -> dict[str, Any]:
    """黑枪组合预览：替换部件（格式 "Barrel=6:262" 或 "Barrel=GD_...路径"）并输出新代码与静态警告。

    **不写存档/profile，也不代表游戏一定接受**；结果只是 PREVIEW。
    """
    replacements = {}
    for item in parts:
        if "=" in item:
            k, v = item.split("=", 1)
            replacements[k.strip()] = v.strip()
    try:
        return partmod.preview(code, replacements, allow_append=allow_append)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


@mcp.tool()
def bl2_balance_sources() -> dict[str, Any]:
    """数值平衡模型来源（ScalingAdjuster/UCP/Nifsara/Aaron/Osetor/OP 对象/Badass 截图）。只读。"""
    return {"sources": bal.sources(), "disclaimer": bal.disclaimer()}


@mcp.tool()
def bl2_balance_curve(levels: str = "55,60,72,80", const: float = 1.13) -> dict[str, Any]:
    """UniversalBalanceScaler 相对曲线 const^(level-50)；只读模型，不是实际伤害/血量。"""
    lv = [int(x) for x in levels.split(",") if x.strip()]
    return {"const": const, "base_level": 50, "curve": bal.curve(lv, const), "note": "改全局 scaler 同时影响玩家/敌人/物品，不等于只削弱敌人。"}


@mcp.tool()
def bl2_balance_compare(levels: str = "", include_op: bool = True) -> dict[str, Any]:
    """对比原版/UCP、预演 1.08、Nifsara Regular/Hard 的敌人生命/护盾/伤害指数；Badass 不混入。"""
    lv = [int(x) for x in levels.split(",") if x.strip()] if levels else None
    return bal.compare(lv, include_op=include_op)


@mcp.tool()
def bl2_balance_badass() -> dict[str, Any]:
    """账号/Badass 加成独立因子（来自用户截图）；1.282*1.257 只是简化示意，不是实际 DPS。"""
    return bal.badass_report()


@mcp.tool()
def bl2_balance_op(const: float = 1.13) -> dict[str, Any]:
    """OP 0/5/10 的有效等级近似与相对缩放；不含未知 OP 额外加成。"""
    return {"const": const, "rows": bal.op_table(const), "note": "OP 按额外有效等级近似，不代表装备同步后的 TTK。"}


@mcp.tool()
def bl2_balance_equipment(const: float = 1.13) -> dict[str, Any]:
    """装备等级差相对伤害因子 const^Δ（简化模型）。"""
    return {"const": const, "rows": bal.equipment_delta(const=const), "note": "未计入部件/技能/元素/暴击/抗性。"}


@mcp.tool()
def bl2_balance_preview(const: float = 1.08) -> dict[str, Any]:
    """数值平衡补丁预览（UniversalBalanceScaler/PT3 参考字段）。PREVIEW ONLY，不写文件、不应用到运行中游戏。"""
    return bal.preview(const)


@mcp.tool()
def bl2_xp_calc(
    base_xp: int,
    extra_percent: float,
    current_level: Optional[int] = None,
    level_cap: int = 80,
    apply_at_cap: bool = False,
) -> dict[str, Any]:
    """离线 XP bonus 计算：bonus=floor(base_xp*extra_percent/100)，total=base+bonus。

    只读计算，不发真实 XP/不写存档；base_xp 必须是游戏最终结算 XP，不是 TotalExpPoints 中间量。
    """
    try:
        return xpmod.calculate(
            base_xp,
            extra_percent,
            current_level=current_level,
            level_cap=level_cap,
            apply_at_cap=apply_at_cap,
        )
    except ValueError as exc:
        return {"error": str(exc)}


@mcp.tool()
def bl2_xp_table(bases: str = "100,500,1000", extra_percent: float = 50) -> dict[str, Any]:
    """批量离线 XP 计算表；bases 逗号分隔。不写真实 XP/存档。"""
    try:
        values = [int(x.strip()) for x in bases.split(",") if x.strip()]
        return {"extra_percent": extra_percent, "rows": xpmod.table(values, extra_percent)}
    except (ValueError, TypeError) as exc:
        return {"error": str(exc)}


@mcp.tool()
def bl2_xp_capabilities() -> dict[str, Any]:
    """XP 能力/证据：公式、base 语义、杀敌/任务/挑战覆盖、hook 签名、满级与递归防护。只读。"""
    return xpmod.capabilities()


@mcp.tool()
def bl2_xp_preview(base_xp: int = 1000, extra_percent: float = 50) -> dict[str, Any]:
    """XP 加成离线预览（PREVIEW ONLY）；不写文件、不在运行中游戏发 XP。"""
    try:
        return xpmod.preview(base_xp, extra_percent)
    except ValueError as exc:
        return {"error": str(exc)}


# --------------------------------------------------------------------------
# Claptrap 共享储藏箱（profile.bin）
# --------------------------------------------------------------------------


def _profile(profile: str = ""):
    path = profile or profilemod.default_profile_path()
    if path is None:
        raise FileNotFoundError("找不到 profile.bin，请显式给 profile 路径")
    return profilemod.ProfileFile(path)


@mcp.tool()
def bl2_stash_info(profile: str = "") -> dict[str, Any]:
    """读取 Claptrap 共享储藏箱（profile.bin 的 4 个格子）以及 Badass/金钥匙等 profile 信息。"""
    return _profile(profile).stash_summary()


@mcp.tool()
def bl2_stash_set(
    stash_slot: int,
    code_or_name: str,
    profile: str = "",
    dry_run: bool = True,
) -> dict[str, Any]:
    """往 Claptrap 共享储藏箱（跨角色，4 格）写一件装备。

    stash_slot: 1-4；code_or_name: BL2(...) 代码或特殊武器表里的名字（如 不洁哈罗德）。
    默认 dry_run=True，只演示不写；确认后传 dry_run=False（写前自动备份 profile.bin）。
    """
    pf = _profile(profile)
    if not (1 <= stash_slot <= 4):
        return {"error": "stash_slot 只能是 1-4"}
    value = code_or_name
    label = "直接代码"
    if not itemcodes.is_code(value):
        entry = spec.find(value)
        if entry is None:
            return {"error": f"不是代码，也找不到名字: {value!r}"}
        value = entry["code"]
        label = entry["name"]
    pf.set_stash_slot(stash_slot, value)
    risk = itemcodes.assess_risk(value)
    if not dry_run:
        pf.save(backup=True)
    return {
        "profile": str(pf.path),
        "stash_slot": stash_slot,
        "item": label,
        "code": value,
        "risk": risk,
        "written": not dry_run,
    }


@mcp.tool()
def bl2_stash_clear(stash_slot: int, profile: str = "", dry_run: bool = True) -> dict[str, Any]:
    """清空 Claptrap 共享储藏箱的一个格子。默认 dry_run=True。"""
    pf = _profile(profile)
    if not (1 <= stash_slot <= 4):
        return {"error": "stash_slot 只能是 1-4"}
    pf.set_stash_slot(stash_slot, None)
    if not dry_run:
        pf.save(backup=True)
    return {"profile": str(pf.path), "stash_slot": stash_slot, "written": not dry_run}


# --------------------------------------------------------------------------
# 写操作
# --------------------------------------------------------------------------


@mcp.tool()
def bl2_set_skill_points(slot: str, points: int, dry_run: bool = False) -> dict[str, Any]:
    """设置角色的未分配技能点（进游戏即可点满技能树）。写前自动备份。

    dry_run=True 时只演示不写盘。
    """
    sf = _open(slot)
    before = sf.unspent_skill_points
    sf.set_skill_points(points)
    after = sf.unspent_skill_points
    if not dry_run:
        sf.save(backup=True)
    return {"slot": slot, "before": before, "after": after, "written": not dry_run}


@mcp.tool()
def bl2_edit_character(
    slot: str,
    level: Optional[int] = None,
    money: Optional[int] = None,
    eridium: Optional[int] = None,
    seraph: Optional[int] = None,
    torgue: Optional[int] = None,
    backpack: Optional[int] = None,
    bank: Optional[int] = None,
    gun_slots: Optional[int] = None,
    name: Optional[str] = None,
    unlock_uvhm: bool = False,
    max_ammo: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """修改角色属性：等级、金钱、镒元素、晶石、托格代币、背包/银行格子、武器槽、名字、解锁三周目、补弹药。

    dry_run=True 时只演示不写盘。写前自动备份。
    """
    sf = _open(slot)
    before = sf.summary()
    after = sf.edit(
        level=level,
        money=money,
        eridium=eridium,
        seraph=seraph,
        torgue=torgue,
        backpack=backpack,
        bank=bank,
        gun_slots=gun_slots,
        name=name,
        unlock_uvhm=unlock_uvhm,
        max_ammo=max_ammo,
    )
    if not dry_run:
        sf.save(backup=True)
    return {
        "slot": slot,
        "character": before["name"],
        "before": {k: before[k] for k in ("level", "currency", "items")},
        "after": {k: after[k] for k in ("level", "currency", "items")},
        "written": not dry_run,
    }


@mcp.tool()
def bl2_import_item_codes(
    slot: str,
    codes: list[str],
    where: str = "bank",
    dedupe: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """把一批 BL2(...) 物品代码导入角色存档。

    where: bank=银行 / items=背包物品 / weapons=背包武器 / auto=按代码自动判定。
    dedupe=True 时跳过角色已有的相同物品。写前自动备份。
    """
    sf = _open(slot)
    before = sf.item_counts()
    result = sf.import_codes(codes, where=where, dedupe=dedupe)
    after = sf.item_counts()
    if not dry_run:
        sf.save(backup=True)
    return {"slot": slot, "imported": result, "before": before, "after": after, "written": not dry_run}


@mcp.tool()
def bl2_sync_items(
    source_slot: str,
    target_slot: str,
    sections: str = "weapons",
    where: str = "bank",
    limit: Optional[int] = None,
    dedupe: bool = True,
    dry_run: bool = True,
) -> dict[str, Any]:
    """把武器/物品从一个角色同步到另一个角色（默认只演示，dry_run=False 才真正写入）。"""
    src = _open(source_slot)
    dst = _open(target_slot)
    secs = tuple(s.strip() for s in sections.split(",") if s.strip())
    codes = src.export_codes(secs)
    flat = [c for section in secs for c in codes[section]]
    if limit:
        flat = flat[:limit]
    before = dst.item_counts()
    result = dst.import_codes(flat, where=where, dedupe=dedupe)
    after = dst.item_counts()
    if not dry_run:
        dst.save(backup=True)
    return {
        "source": {"slot": source_slot, "character": src.character_name, "codes": len(flat)},
        "target": {"slot": target_slot, "character": dst.character_name},
        "imported": result,
        "before": before,
        "after": after,
        "written": not dry_run,
    }


@mcp.tool()
def bl2_add_gun(
    slot: str,
    name_or_code: str,
    count: int = 1,
    where: str = "bank",
    dry_run: bool = False,
) -> dict[str, Any]:
    """把本地代码库里的一把黑枪/特殊装备导入角色。

    name_or_code: 代码库里的名字（如 "超级哈罗德"），或直接给 BL2(...) 代码。
    写前自动备份。
    """
    if itemcodes.is_code(name_or_code):
        codes = [itemcodes.with_key_zero(name_or_code)] * max(1, count)
        display = "直接代码"
    else:
        entry = gunlib.find_by_name(name_or_code)
        if entry is None:
            return {"error": f"代码库里找不到 {name_or_code!r}", "hint": "先用 bl2_search_gun_library 搜索"}
        codes = [entry["code"]] * max(1, count)
        display = entry["name"]
    sf = _open(slot)
    before = sf.item_counts()
    result = sf.import_codes(codes, where=where, dedupe=True)
    after = sf.item_counts()
    if not dry_run:
        sf.save(backup=True)
    return {"slot": slot, "item": display, "imported": result, "before": before, "after": after, "written": not dry_run}


@mcp.tool()
def bl2_restore_backup(backup_dir: str) -> dict[str, Any]:
    """把某个备份恢复回原存档（恢复前会自动备份当前文件）。

    与其他写工具一致：游戏运行中会拒绝；默认不 force、不覆盖运行中存档。
    """
    try:
        dest = restore_backup(backup_dir)
        return {"restored_to": str(dest), "written": True}
    except RuntimeError as exc:
        return {"error": str(exc), "written": False}
    except (FileNotFoundError, ValueError) as exc:
        return {"error": str(exc), "written": False}


@mcp.tool()
def bl2_encode_item_code(info_json: dict[str, Any]) -> str:
    """把 bl2_decode_item_code 返回的字典（可手动改 parts/level）重新编码成 BL2(...) 代码。

    这是"制作黑枪"的核心：解码 -> 改配件编号 -> 编码。
    """
    return itemcodes.encode(info_json)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
