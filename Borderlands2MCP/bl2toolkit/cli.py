"""BL2 工具包命令行入口。

用法示例（在 E:\\Borderlands2MCP 下运行，或使用项目里的 bl2.cmd）：

    python -m bl2toolkit saves                 列出所有角色存档
    python -m bl2toolkit info 1                 查看 1 号存档详情
    python -m bl2toolkit skills 1               查看技能与技能点
    python -m bl2toolkit edit 1 --skill-points 120
    python -m bl2toolkit export 1 -o items.txt  导出物品代码
    python -m bl2toolkit import 2 items.txt --where bank
    python -m bl2toolkit sync 1 2 --sections weapons --where bank
    python -m bl2toolkit code decode "BL2(...)"
    python -m bl2toolkit guns list --query 哈罗德
    python -m bl2toolkit guns add 1 超级哈罗德 --count 2
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, itemcodes
from . import library as gunlib
from . import space, specials
from .paths import (
    find_save_root,
    find_steam_id_dir,
    list_backups,
    list_saves,
    resolve_save,
    restore_backup,
)
from .saves import SECTION_LABELS, SaveFile

# Windows 控制台默认可能是 GBK，统一改成 UTF-8，避免中文/符号报错
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass


def _dump(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _parse_levels(value: str | None) -> list[int]:
    if not value:
        return []
    return [int(part.strip()) for part in str(value).split(",") if part.strip()]


def _open(spec: str) -> SaveFile:
    return SaveFile(resolve_save(spec))


def _require_steam_dir() -> Path:
    d = find_steam_id_dir()
    if d is None:
        raise SystemExit(f"找不到存档目录（默认 {find_save_root()}），可用环境变量 BL2_SAVE_ROOT 指定")
    return d


# --------------------------------------------------------------------------
# 各子命令
# --------------------------------------------------------------------------


def cmd_saves(args: argparse.Namespace) -> int:
    d = _require_steam_dir()
    saves = list_saves(d)
    if not saves:
        print(f"目录 {d} 里没有存档")
        return 1
    rows = []
    for path in saves:
        try:
            sm = SaveFile(path).summary()
        except Exception as exc:  # 损坏或非 BL2 存档
            rows.append({"slot": path.stem, "error": str(exc)})
            continue
        rows.append(sm)
    if args.json:
        _dump(rows)
        return 0
    print(f"存档目录: {d}\n")
    print(f"{'槽位':<10}{'名字':<10}{'角色':<22}{'等级':<6}{'技能点':<8}{'银行':<6}{'物品':<6}{'武器':<6}")
    for sm in rows:
        if "error" in sm:
            print(f"{sm['slot']:<10}<无法解析: {sm['error']}>")
            continue
        print(
            f"{sm['slot']:<10}{sm['name']:<10}{sm['class']:<22}{sm['level']:<6}"
            f"{sm['skill_points']:<8}{sm['items']['bank']:<6}{sm['items']['items']:<6}{sm['items']['weapons']:<6}"
        )
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    sf = _open(args.slot)
    sm = sf.summary()
    if args.json:
        _dump(sm)
        return 0
    cur = sm["currency"]
    print(f"存档: {sm['file']}")
    print(f"角色: {sm['name']}  {sm['class']}  Lv{sm['level']}  经验={sm['experience']:,}")
    print(f"通关周目: {sm['playthroughs_completed']}   存档ID: {sm['save_game_id']}")
    print(f"技能点: 未分配 {sm['skill_points']} / 已用 {sm['skill_points_used']}（{sm['skills']} 个技能）")
    print(
        "货币: 金钱={:,}  镒元素={:,}  晶石={:,}  托格={:,}".format(
            cur.get("money", 0), cur.get("eridium", 0), cur.get("seraph", 0), cur.get("torgue", 0)
        )
    )
    print(f"物品: 银行 {sm['items']['bank']}  背包物品 {sm['items']['items']}  背包武器 {sm['items']['weapons']}")
    return 0


def cmd_skills(args: argparse.Namespace) -> int:
    sf = _open(args.slot)
    skills = sf.list_skills()
    if args.json:
        _dump({"unspent": sf.unspent_skill_points, "used": sf.used_skill_points, "skills": skills})
        return 0
    print(f"未分配技能点: {sf.unspent_skill_points}    已用: {sf.used_skill_points}")
    for s in sorted(skills, key=lambda x: x["name"]):
        print(f"  {s['level']:>2}  {s['name']}")
    return 0


def cmd_edit(args: argparse.Namespace) -> int:
    sf = _open(args.slot)
    before = sf.summary()
    after = sf.edit(
        level=args.level,
        money=args.money,
        eridium=args.eridium,
        seraph=args.seraph,
        torgue=args.torgue,
        backpack=args.backpack,
        bank=args.bank,
        gun_slots=args.gun_slots,
        name=args.name,
        unlock_uvhm=args.unlock_uvhm,
        max_ammo=args.max_ammo,
        skill_points=args.skill_points,
        add_skill_points=args.add_skill_points,
    )
    changes = {k: (before[k], after[k]) for k in ("level", "skill_points", "currency", "items")
               if before.get(k) != after.get(k)}
    print("修改结果:")
    for k, (old, new) in changes.items():
        print(f"  {k}: {old} -> {new}")
    if not changes:
        print("  （没有变化）")
    if args.dry_run:
        print("\n--dry-run：未写入存档")
        return 0
    dest = sf.save(target=args.save_as, backup=not args.no_backup)
    suffix = "（原文件已备份）" if not args.no_backup else ""
    print(f"\n已写入: {dest}{suffix}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    sf = _open(args.slot)
    sections = tuple(s.strip() for s in args.sections.split(",") if s.strip())
    codes = sf.export_codes(sections)
    if args.output:
        out = Path(args.output)
        lines = []
        for section in sections:
            lines.append(f"; {section.capitalize()}")
            lines.extend(codes[section])
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"已导出 {sum(len(v) for v in codes.values())} 条到 {out}")
    else:
        for section in sections:
            print(f"; {section} ({len(codes[section])})")
            for code in codes[section]:
                print(code)
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    sf = _open(args.slot)
    codes: list[str] = []
    for path in args.files:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
        codes.extend(itemcodes.extract_codes(text))
    if not codes:
        print("没有找到任何 BL2(...) 代码")
        return 1
    before = sf.item_counts()
    result = sf.import_codes(codes, where=args.where, dedupe=not args.no_dedupe)
    after = sf.item_counts()
    print(f"读取 {len(codes)} 条代码，导入结果: {result}")
    print(f"数量变化: {before} -> {after}")
    if args.dry_run:
        print("--dry-run：未写入存档")
        return 0
    dest = sf.save(target=args.save_as, backup=not args.no_backup)
    print(f"已写入: {dest}")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    src = _open(args.source)
    dst = _open(args.target)
    sections = tuple(s.strip() for s in args.sections.split(",") if s.strip())
    codes = src.export_codes(sections)
    flat = [c for section in sections for c in codes[section]]
    if args.limit:
        flat = flat[: args.limit]
    before = dst.item_counts()
    result = dst.import_codes(flat, where=args.where, dedupe=not args.no_dedupe)
    after = dst.item_counts()
    print(f"来源 {src.path.stem}（{src.character_name}）: {len(flat)} 条代码")
    print(f"目标 {dst.path.stem}（{dst.character_name}）: 导入 {result}")
    print(f"数量变化: {before} -> {after}")
    if args.dry_run:
        print("--dry-run：未写入存档")
        return 0
    dest = dst.save(backup=not args.no_backup)
    print(f"已写入: {dest}")
    return 0


def cmd_code(args: argparse.Namespace) -> int:
    if args.action == "decode":
        print(json.dumps(itemcodes.decode(args.value), ensure_ascii=False, indent=2, default=str))
    elif args.action == "describe":
        print(itemcodes.describe(args.value))
    elif args.action == "encode":
        data = json.loads(Path(args.value).read_text(encoding="utf-8"))
        print(itemcodes.encode(data))
    elif args.action == "check":
        try:
            print(itemcodes.describe(args.value))
            print("代码有效 ✅")
            return 0
        except Exception as exc:
            print(f"代码无效 ❌ : {exc}")
            return 1
    elif args.action == "risk":
        _dump(itemcodes.assess_risk(args.value))
        return 0
    return 0


def cmd_parts(args: argparse.Namespace) -> int:
    from . import parts as partmod

    if args.action == "stats":
        data = partmod.stats()
        if args.json:
            _dump(data)
            return 0
        totals = data.get("game_totals", {})
        print(f"资产库 set: {data['asset_library_sets']}")
        print(f"武器部件定义: {data['weapon_part_defs']}  物品部件定义: {data['item_part_defs']}  名称部件: {data['name_part_defs']}")
        print(f"武器类型: {data['weapon_type_defs']}  武器部件列表: {data['weapon_part_list_count']}  物品部件列表: {data['item_part_list_count']}")
        print("\n部件定义按槽位:")
        for k, v in data["part_defs_by_slot"].items():
            print(f"  {k:<28} {v}")
        print("\n全游戏 dumps 计数（不是本工具条目数）:")
        for k in ("weapon_balance_defs", "weapon_balance_unique_bases", "item_balance_defs", "item_defs", "weapon_part_assets_set0", "item_part_assets_set0"):
            print(f"  {k}: {totals.get(k)}")
        print("\n注意:", data["note"])
        return 0
    if args.action == "slots":
        data = partmod.slot_table()
        if args.json:
            _dump(data)
            return 0
        print("武器槽位:")
        for t, slot in data["weapon"].items():
            print(f"  {t:<12} {slot}")
        print("物品槽位:")
        for t, slot in data["item"].items():
            print(f"  {t:<12} {slot}")
        return 0
    if args.action == "coverage":
        data = partmod.coverage_report()
        if args.json:
            _dump(data)
            return 0
        print("=== 覆盖率 / 缺口（基于 Gibbed dumps 分类计数，不是官方全物品清单） ===")
        print("代码库:", data["code_library"]["total"], "武器", data["code_library"]["weapons"], "物品", data["code_library"]["items"])
        print("特殊表:", data["special_table"]["total"], data["special_table"]["by_kind"])
        print("dumps 总数:", data["gibbed_totals"])
        print("\n分类对比:")
        for name, comp in data["comparisons"].items():
            print(f"  {name}: {comp}")
        print("\n缺口:")
        for gap in data["gaps"]:
            print(f"  - {gap}")
        return 0
    if args.action == "search":
        rows = partmod.search_parts(args.query, args.kind or "", args.limit)
        if args.json:
            _dump(rows)
            return 0
        for row in rows:
            ref = row["ref"] or "?"
            print(f"  {row['kind']:<7} {row['type'] or '':<14} {str(row['slot'] or ''):<26} {ref:<10} {row['path']}")
        print(f"\n共 {len(rows)} 条（上限 {args.limit}）。数据来自 Gibbed dumps，未实机验证。")
        return 0
    if args.action == "show":
        from . import parts as p

        q = args.query
        if not q:
            print("用法: parts show <完整部件路径 | lib:asset>")
            return 1
        m = p.part_meta()
        for group in ("weapon_parts", "item_parts", "name_parts"):
            if q in m[group]:
                is_w = group == "weapon_parts"
                ref = p.ref_for_path(q, 0, "WeaponParts" if group == "weapon_parts" else ("ItemParts" if group == "item_parts" else None))
                row = {
                    "kind": group,
                    "path": q,
                    "meta": m[group][q],
                    "type": p._part_type(q, is_w),
                    "slot": p._slot_for_type(p._part_type(q, is_w), is_w),
                    "ref": None if not ref else f"{ref['lib']}:{ref['asset']}",
                }
                if args.json:
                    _dump(row)
                else:
                    print(row)
                return 0
        bits = q.split(":")
        try:
            lib, asset = int(bits[-2]), int(bits[-1])
        except Exception:
            print("既不是已知部件路径，也不是 lib:asset")
            return 1
        for group in ("WeaponParts", "ItemParts"):
            resolved = p.resolve_reference(0, group, lib, asset)
            if resolved:
                path = resolved["path"]
                is_w = group == "WeaponParts"
                print({"group": group, "ref": f"{lib}:{asset}", "path": path, "type": p._part_type(path, is_w), "slot": p._slot_for_type(p._part_type(path, is_w), is_w)})
                return 0
        print("解析不到该 lib:asset（可能属于 DLC set 或空槽）")
        return 1
    print("用法: parts [stats|search|show|slots|coverage] [query] [--kind weapon|item|name] [--limit N] [--json]")
    return 1


def cmd_combine(args: argparse.Namespace) -> int:
    from . import parts as partmod

    replacements = {}
    for item in args.part or []:
        if "=" not in item:
            print(f"参数格式错误: {item!r}，应为 槽位=lib:asset 或 槽位=完整部件路径")
            return 1
        k, v = item.split("=", 1)
        replacements[k.strip()] = v.strip()
    try:
        result = partmod.preview(args.code, replacements, allow_append=args.allow_append)
    except Exception as exc:  # noqa: BLE001
        print(f"组合预览失败: {exc}")
        return 1
    if args.json:
        _dump(result)
        return 1 if result["errors"] else 0
    print(result["note"])
    print("原代码:", result["original"])
    print("预览代码:", result["new_code"])
    print("\n变更:")
    for ch in result["changes"]:
        print(f"  {ch['slot']}[{ch['index']}]: {ch['old_path']} -> {ch['new_path']}  ({ch['new_ref']})")
    if result["warnings"]:
        print("\n警告（静态检查，未实机验证）:")
        for w in result["warnings"]:
            print(f"  ! {w}")
    if result["errors"]:
        print("\n错误:")
        for e in result["errors"]:
            print(f"  x {e}")
    print("\n风险:", result["risk"].get("warnings") or "无结构性告警（不代表游戏一定接受）")
    print("没有写存档/profile；如需落地请另外走 import/stash 并先备份。")
    return 1 if result["errors"] else 0


def cmd_balance(args: argparse.Namespace) -> int:
    from . import balance as bal

    if args.action == "sources":
        data = bal.load_models()
        if args.json:
            _dump(data)
            return 0
        print("数值平衡来源（一手）：")
        for key, s in bal.sources().items():
            print(f"- {key}: {s.get('name','')}")
            if s.get("url"):
                print(f"  {s['url']}")
            for k in ("object", "property", "values", "note"):
                if s.get(k):
                    print(f"  {k}: {s[k]}")
        print("\n模型声明:", bal.disclaimer())
        return 0
    if args.action == "curve":
        levels = _parse_levels(args.levels) or [55, 60, 72, 80]
        const = args.const if args.const is not None else 1.13
        rows = bal.curve(levels, const)
        if args.json:
            _dump(rows)
            return 0
        print(f"UniversalBalanceScaler 相对曲线（const={const}, base=50；不是实际伤害/血量）")
        for r in rows:
            print(f"  Lv{r['level']:<3} relative={r['relative']:.4f}")
        print("注意: 改全局 scaler 同时影响玩家/敌人/物品，不等于只削弱敌人。")
        return 0
    if args.action == "compare":
        levels = _parse_levels(args.levels)
        data = bal.compare(levels, include_op=not args.no_op)
        if args.json:
            _dump(data)
            return 0
        print(bal.format_compare(data))
        return 0
    if args.action == "badass":
        data = bal.badass_report()
        if args.json:
            _dump(data)
            return 0
        print("Badass/账号加成（独立因子，不混进等级曲线）")
        for k, v in data["factors"].items():
            print(f"  {k:<20} x{v:.4f}")
        print("\n" + data["simplified_note"])
        return 0
    if args.action == "op":
        const = args.const if args.const is not None else 1.13
        rows = bal.op_table(const)
        if args.json:
            _dump(rows)
            return 0
        print(f"OP 近似（const={const}；只按额外有效等级，不含未知 OP 额外加成）")
        for r in rows:
            print(f"  Lv{r['level']} OP{r['op']} -> eff {r['effective_level']}, scaler_vs_80={r['scaler_relative_vs_80']:.3f}")
        print(rows[0]["note"])
        return 0
    if args.action == "equipment":
        const = args.const if args.const is not None else 1.13
        rows = bal.equipment_delta(const=const)
        if args.json:
            _dump(rows)
            return 0
        print(f"装备等级差相对因子（const={const}；模型，不是最终伤害）")
        for r in rows:
            print(f"  Δ={r['delta']:+d}  x{r['relative']:.4f}")
        print(rows[0]["note"])
        return 0
    if args.action == "preview":
        const = args.const if args.const is not None else 1.08
        data = bal.preview(const)
        if args.json:
            _dump(data)
            return 0
        print(data["text"])
        return 0
    print("用法: balance [sources|curve|compare|badass|op|equipment|preview] [--levels 55,60,72,80] [--const 1.13] [--json]")
    return 1


def cmd_xp(args: argparse.Namespace) -> int:
    from . import xp as xpmod

    if args.action == "calc":
        try:
            result = xpmod.calculate(
                args.base,
                args.percent,
                current_level=args.current_level,
                level_cap=args.level_cap,
                apply_at_cap=args.apply_at_cap,
            )
        except ValueError as exc:
            print(f"输入错误: {exc}")
            return 1
        if args.json:
            _dump(result)
            return 0
        print(f"base_xp = {result['base_xp']}")
        print(f"extra_percent = {result['extra_percent']:g}%")
        print(f"bonus = floor({result['base_xp']} * {result['extra_percent']:g} / 100) = {result['bonus']}")
        print(f"total = {result['base_xp']} + {result['bonus']} = {result['total']}")
        print(f"at_cap={result['at_cap']} applies={result['applies']} level_gain={result['level_gain']}")
        for note in result["notes"]:
            print(f"  ! {note}")
        return 0
    if args.action == "table":
        bases = _parse_levels(args.bases or "100,500,1000")
        rows = xpmod.table(bases, args.percent)
        if args.json:
            _dump(rows)
            return 0
        for r in rows:
            print(f"base={r['base_xp']:<8} +{r['extra_percent']:g}% -> bonus={r['bonus']:<8} total={r['total']}")
        return 0
    if args.action == "capabilities":
        data = xpmod.capabilities()
        if args.json:
            _dump(data)
            return 0
        print("XP 能力/证据（只读）：")
        print("公式:", data["formula"])
        print("运行时 hook:")
        for h in data["runtime_hooks"]:
            if h.get("observed_calls"):
                obs = "; ".join(f"{c['args']} 参: {c['call']}" for c in h["observed_calls"])
            else:
                obs = h.get("args") or h.get("args_new_sdk") or "(无参数记录)"
            print(f"  {h['hook']}  {obs}  status={h['status']}")
        print("覆盖:")
        for k, v in data["coverage"].items():
            print(f"  {k}: {v['status']}")
        print("\n等级上限:", data["level_cap"])
        print("递归防护:")
        for g in data["recursion_guards"]:
            print(f"  - {g}")
        print("\n可验证步骤（由只读诊断 Mod 执行）：")
        for step in data.get("verification_steps", []):
            print(f"  - {step['step']}: {step['detail']}")
        print("\n注意:", data["note"])
        return 0
    if args.action == "preview":
        data = xpmod.preview(args.base, args.percent)
        if args.json:
            _dump(data)
            return 0
        print(data["text"])
        return 0
    print("用法: xp [calc|table|capabilities|preview] --base 1000 --percent 50 [--bases 100,500,1000] [--current-level 79] [--level-cap 80] [--json]")
    return 1


def cmd_guns(args: argparse.Namespace) -> int:
    if args.action == "list":
        query = args.query or (args.name or "")
        entries = gunlib.search(query, kind=args.kind, limit=args.limit)
        if args.json:
            _dump(entries)
            return 0
        if not entries:
            print("代码库为空，先运行: python scripts\\build_code_library.py")
            return 1
        for i, e in enumerate(entries, 1):
            kind = "武器" if e.get("is_weapon") else "物品"
            print(f"{i:>3}. [{kind}] {e['name']}  Lv{e.get('level', 0)}  {e.get('note', '')[:30]}")
        print(f"\n共 {len(entries)} 条（库文件 {gunlib.LIBRARY_FILE}）")
    elif args.action == "build":
        entries, stats = gunlib.build_library()
        out = gunlib.save_library(entries)
        print(f"已生成代码库: {out}（{len(entries)} 条，成功解码 {stats['decoded']}）")
    elif args.action == "add":
        entry = gunlib.find_by_name(args.name)
        if entry is None:
            print(f"代码库里找不到 {args.name!r}，先用 guns list 查看")
            return 1
        sf = _open(args.slot)
        before = sf.item_counts()
        result = sf.import_codes([entry["code"]] * max(1, args.count), where=args.where, dedupe=not args.no_dedupe)
        after = sf.item_counts()
        print(f"导入 [{entry['name']}] x{args.count}: {result}  {before} -> {after}")
        if args.dry_run:
            print("--dry-run：未写入存档")
            return 0
        dest = sf.save(target=args.save_as, backup=not args.no_backup)
        print(f"已写入: {dest}")
    return 0


def cmd_specials(args: argparse.Namespace) -> int:
    extra = list(args.args or [])

    if args.action == "stats":
        data = specials.stats()
        if args.json:
            _dump(data)
            return 0
        print(f"特殊装备总表: {data['file']}")
        print(f"总条目: {data['total']}")
        print("按稀有度:")
        for key, count in data["by_rarity"].items():
            if "传说" == key:
                hint = "  ← 橙装/传说"
            elif "彩色" == key:
                hint = "  ← 彩虹"
            elif "E科技" == key:
                hint = "  ← 品红/深红色"
            else:
                hint = ""
            print(f"  {specials.rarity_display(key):<18} {count:>4}{hint}")
        print("按类型:", data["by_kind"])
        return 0

    if args.action == "list":
        query = args.query or (extra[0] if extra else "")
        rows = specials.search(query, rarity=args.rarity, kind=args.kind, limit=args.limit)
        if args.json:
            _dump(rows)
            return 0
        if not rows:
            print("没有匹配的条目（先确认 data\\special_weapons.json 已生成）")
            return 1
        print(f"{'稀有度':<20}{'类型':<8}{'名称':<22}{'英文名':<22}{'厂商':<10}")
        for r in rows:
            print(
                f"{specials.rarity_display(r['rarity']):<20}"
                f"{(r.get('weapon_type') or r.get('kind', '')):<8}"
                f"{r.get('name', ''):<22}"
                f"{(r.get('name_en') or ''):<22}"
                f"{r.get('manufacturer', ''):<10}"
            )
        print(f"\n共 {len(rows)} 条（完整表格见 data\\特殊武器表.md）")
        return 0

    if args.action == "add":
        if len(extra) < 2:
            print("用法: specials add <槽位> <名字或代码> [--count N] [--where bank]")
            return 1
        a, b = extra[0], extra[1]
        if _looks_like_slot(a):
            slot, name = a, b
        elif _looks_like_slot(b):
            slot, name = b, a
        else:
            print(f"找不到存档槽位: {a!r} / {b!r}")
            return 1
        sf = _open(slot)
        try:
            result = specials.add_to_save(
                sf,
                name,
                count=args.count,
                where=args.where,
                dedupe=not args.no_dedupe,
            )
        except ValueError as exc:
            print(exc)
            print("提示: 用 specials list 搜索名字，或直接用 BL2(...) 代码")
            return 1
        if result["rarity"]:
            print(f"条目: [{specials.rarity_display(result['rarity'])}] {result['item']} x{result['codes']}")
        else:
            print(f"条目: {result['item']} x{result['codes']}")
        print(f"导入结果: {result['imported']}   {result['before']} -> {result['after']}")
        if args.dry_run:
            print("--dry-run：未写入存档")
            return 0
        dest = sf.save(backup=not args.no_backup)
        print(f"已写入: {dest}")
        return 0
    return 0


def cmd_space(args: argparse.Namespace) -> int:
    if args.action == "info":
        if not args.slot:
            print("用法: space info <槽位>")
            return 1
        sf = _open(args.slot)
        info = space.space_info(sf)
        if args.json:
            _dump(info)
            return 0
        print(f"角色: {info['character']}（{info['slot']}）")
        bp, bk = info["backpack"], info["bank"]
        print(f"背包: 容量 {bp['capacity']}  SDU等级 {bp['sdu_level']}  已用 {bp['items_in_use']}")
        print(f"银行: 容量 {bk['capacity']}  SDU等级 {bk['sdu_level']}  已用 {bk['items_in_use']}")
        print(f"装备栏: {info['weapon_slots']['capacity']}（原版上限 {info['weapon_slots']['vanilla_max']}）")
        print("原版上限: 背包 12-39（每级+3，9级 SDU）；银行 6-24（每级+2，9级 SDU）")
        exe = info["installed_exe"]
        if exe:
            print(
                f"当前 exe: 背包上限 {exe['backpack_max']}（clamp 阈值 {exe['backpack_cmp']}），"
                f"银行上限 {exe['bank_max']}（clamp 阈值 {exe['bank_cmp']}）"
            )
            print(f"        SHA256 {exe['sha256'][:16]}…  带 PythonSDK: {'是' if exe['sdk_loader'] else '否'}")
            print(f"        实际生效: 背包 {info['effective_in_game']['backpack']}，银行 {info['effective_in_game']['bank']}")
        else:
            print("当前 exe: 未找到/无法识别")
        if info["warnings"]:
            print("\n注意:")
            for w in info["warnings"]:
                print(f"  - {w}")
        return 0

    if args.action == "set":
        if not args.slot:
            print("用法: space set <槽位> --backpack N|max --bank N|max [--allow-oversize] [--dry-run]")
            return 1
        if args.backpack is None and args.bank is None:
            print("至少要指定 --backpack 或 --bank")
            return 1
        sf = _open(args.slot)
        caps = space.analyze_installed()
        backpack = _parse_space_value(args.backpack, "backpack", caps)
        bank = _parse_space_value(args.bank, "bank", caps)
        allow_oversize = args.allow_oversize or _is_max(args.backpack) or _is_max(args.bank)
        result = space.set_space(
            sf,
            backpack=backpack,
            bank=bank,
            allow_oversize=allow_oversize,
        )
        before, after = result["before"], result["after"]
        print(
            f"背包: {before['backpack']['capacity']} -> {after['backpack']['capacity']}  "
            f"银行: {before['bank']['capacity']} -> {after['bank']['capacity']}"
        )
        if args.dry_run:
            print("--dry-run：未写入存档")
            return 0
        dest = sf.save(backup=not args.no_backup)
        print(f"已写入: {dest}")
        for w in after["warnings"]:
            print(f"注意: {w}")
        return 0

    if args.action == "exe":
        caps = space.analyze_installed()
        if caps is None:
            print("没有找到或无法识别当前游戏 exe（可用环境变量 BL2_GAME_EXE 指定）")
            return 1
        if args.json:
            _dump({"caps": caps.to_dict(), "backups": space.list_exe_backups()})
            return 0
        print(f"exe: {caps.path}")
        print(f"SHA256: {caps.sha256}")
        print(f"背包: 最小 {caps.backpack_min}，clamp 阈值 {caps.backpack_cmp}，上限 {caps.backpack_max}（偏移 0x{caps.backpack_offset:08X}）")
        print(f"银行: 最小 {caps.bank_min}，clamp 阈值 {caps.bank_cmp}，上限 {caps.bank_max}（偏移 0x{caps.bank_offset:08X}）")
        print(f"带 PythonSDK: {'是' if caps.sdk_loader else '否'}")
        backups = space.list_exe_backups()
        print(f"exe 备份: {len(backups)} 个，目录 {space.EXE_BACKUP_DIR}")
        for b in backups[:5]:
            print(f"  {b['mtime']}  {b['file']}")
        return 0

    if args.action == "patch-exe":
        if args.backpack_max is None and args.bank_max is None:
            print("用法: space patch-exe --backpack-max 100 --bank-max 24 [--out 文件] [--apply]")
            return 1
        if args.apply and not args.yes:
            print("--apply 会替换游戏本体 exe（自动备份）。确认请加 --yes")
            return 1
        result = space.patch_exe(
            source=args.exe,
            output=args.out,
            backpack_max=args.backpack_max,
            bank_max=args.bank_max,
            patch_cmp=args.patch_cmp,
            apply_to_game=args.apply,
        )
        if args.json:
            _dump(result)
            return 0
        print(f"源 exe: {result['source']}")
        print(f"输出:   {result['target']}")
        if result["backup"]:
            print(f"备份:   {result['backup']}")
        for c in result["changed"]:
            print(f"  修改 {c['field']}: {c['old']} -> {c['new']}（偏移 {c['offset']}）")
        print(
            f"修改后上限: 背包 {result['caps_after']['backpack_max']}，银行 {result['caps_after']['bank_max']}"
        )
        if not result["applied_to_game"]:
            print("\n这只是输出副本，没有动游戏本体。确认没问题后加 --apply --yes 才会替换游戏 exe。")
        return 0

    if args.action == "restore-exe":
        if not args.backup_file:
            print("用法: space restore-exe <备份文件>")
            return 1
        result = space.restore_exe_backup(args.backup_file, target=args.exe)
        print(f"已恢复: {result['restored']}（来自 {result['from']}）")
        print(f"恢复前备份: {result['safety_backup']}")
        return 0

    if args.action == "backups":
        rows = space.list_exe_backups()
        if args.json:
            _dump(rows)
            return 0
        if not rows:
            print("还没有 exe 备份")
            return 0
        for r in rows:
            print(f"{r['mtime']}  {r['size']}  {r['file']}")
        return 0
    return 0


def cmd_skill(args: argparse.Namespace) -> int:
    from . import skills as skillmod
    from . import skill_effects as fx

    extra = list(args.args or [])

    if args.action == "fx":
        sub = extra[0] if extra else "summary"
        if sub == "list":
            query = extra[1] if len(extra) > 1 else (args.query or "")
            rows = fx.list_effects(query, args.grade or "")
            if args.json:
                _dump(rows)
                return 0
            print(f"技能效果能力表（{len(rows)} 项）：")
            for e in rows:
                print(
                    f"  {e['id']:<20} {e['name']:<14} 分级={'/'.join(e.get('grades') or [])}  "
                    f"可实现度={e.get('implementability','')[:26]}"
                )
            print("\n提示: 详细/预览用 skill fx plan <id>；本表是证据表，不是已生效功能。")
            return 0
        if sub == "summary":
            data = fx.summary()
            if args.json:
                _dump(data)
                return 0
            print(f"技能效果能力表 v{data['version']}（{data['updated']}）")
            print(f"效果条目: {data['effect_count']}  证据来源: {data['evidence_count']}")
            print("分级定义:")
            for g, name in data["grades"].items():
                print(f"  {g} {name}（覆盖 {data['effect_count_by_grade'].get(g,0)} 项）")
            print("\n注意:", data["disclaimer"])
            return 0
        if sub in ("show", "plan"):
            if len(extra) < 2:
                print(f"用法: skill fx {sub} <效果id或名字>")
                return 1
            try:
                if sub == "show":
                    plan = fx.build_plan(extra[1])
                    if args.json:
                        _dump(plan)
                        return 0
                    print(fx.format_plan_text(extra[1]))
                else:
                    print(fx.format_plan_text(extra[1]))
                return 0
            except ValueError as exc:
                print(exc)
                return 1
        print("用法: skill fx [list|summary|show|plan] [效果id] [--query ...] [--grade L1..L4] [--json]")
        return 1

    if args.action == "available":
        if not extra:
            print("用法: skill available <职业 class_id 或存档槽位> [--tree 树名]")
            return 1
        target = extra[0]
        cid = target
        if cid not in skillmod.CLASS_LABELS:
            try:
                cid = skillmod.resolve_class_id(_open(target))
            except Exception:
                print(f"未知职业/槽位: {target!r}（可选: {', '.join(skillmod.CLASS_LABELS)}）")
                return 1
        lst = skillmod.class_skills(cid)
        if args.tree:
            lst = [s for s in lst if s.tree.lower() == args.tree.lower()]
        if args.json:
            _dump([s.__dict__ for s in lst])
            return 0
        print(f"{skillmod.CLASS_LABELS.get(cid, cid)} 技能表（{len(lst)} 条）：")
        for s in lst:
            print(f"  {s.display()}")
        return 0

    if args.action == "summary":
        if not extra:
            print("用法: skill summary <槽位>")
            return 1
        sf = _open(extra[0])
        data = skillmod.skill_summary(sf)
        if args.json:
            _dump(data)
            return 0
        print(f"角色: {data['character']}（{data['class_label']}）")
        print(f"未分配技能点: {data['unspent']}   已用: {data['used']}")
        print(f"技能目录: {data['catalog_count']} 条，其中可写路径 {data['catalog_with_path']} 条")
        print("各树已投点:", data["tree_points"])
        return 0

    if args.action == "set":
        if len(extra) < 3:
            print("用法: skill set <槽位> <技能名或路径> <等级> [--allow-over-max] [--no-tier-check]")
            return 1
        slot, skill, level = extra[0], extra[1], int(extra[2])
        sf = _open(slot)
        try:
            result = skillmod.set_skill_level(
                sf,
                skill,
                level,
                allow_over_max=args.allow_over_max,
                enforce_tier=not args.no_tier_check,
            )
        except ValueError as exc:
            print(f"无法修改技能: {exc}")
            return 1
        print(f"技能: {result['skill']}（{result['tree']} T{result['tier']}, 上限 {result['max_rank']}）")
        print(f"等级: {result['before']} -> {result['after']}   路径: {result['path']}")
        for w in result["warnings"]:
            print(f"注意: {w}")
        if args.dry_run:
            print("--dry-run：未写入存档")
            return 0
        dest = sf.save(backup=not args.no_backup)
        print(f"已写入: {dest}")
        return 0

    if args.action == "fill":
        if not extra:
            print("用法: skill fill <槽位> --tree <树名>  或  --all")
            return 1
        sf = _open(extra[0])
        try:
            if args.all:
                result = skillmod.fill_all(sf)
                print(f"已把所有技能树点满: {result['skills_filled']} 个技能，已用点 {result['used']}")
            else:
                if not args.tree:
                    print("用法: skill fill <槽位> --tree <树名>  或  --all")
                    return 1
                result = skillmod.fill_tree(sf, args.tree)
                print(f"已点满 {result['tree']} 树: {result['skills_filled']} 个技能，已用点 {result['used']}")
        except ValueError as exc:
            print(f"无法点满技能: {exc}")
            return 1
        if args.dry_run:
            print("--dry-run：未写入存档")
            return 0
        dest = sf.save(backup=not args.no_backup)
        print(f"已写入: {dest}")
        return 0

    if args.action == "reset":
        if not extra:
            print("用法: skill reset <槽位>")
            return 1
        sf = _open(extra[0])
        before = sf.used_skill_points
        if args.dry_run:
            print(f"--dry-run：将清空所有已学技能（当前已用 {before} 点），不写入")
            return 0
        result = skillmod.reset_skills(sf)
        sf.save(backup=not args.no_backup)
        print(f"已清空技能：已用点 {result['before_used']} -> {result['after_used']}，未分配 {result['unspent']}")
        return 0
    return 0


def cmd_stash(args: argparse.Namespace) -> int:
    from . import profile as profilemod

    target = args.profile or profilemod.default_profile_path()
    if target is None:
        print("找不到 profile.bin（可用 --profile 指定路径）")
        return 1
    pf = profilemod.ProfileFile(target)

    if args.action == "info":
        data = pf.stash_summary()
        if args.json:
            _dump(data)
            return 0
        print(f"profile: {data['profile']}")
        print(f"条目数: {data['entries']}   Badass: {data['badass']}   金钥匙: {data['golden_keys']}")
        for item in data["stash"]:
            if item.get("empty"):
                print(f"  储藏箱 {item['slot']}: 空")
            elif "error" in item:
                print(f"  储藏箱 {item['slot']}: 解析失败 {item['error']}")
            else:
                kind = "武器" if item["is_weapon"] else "物品"
                print(f"  储藏箱 {item['slot']}: [{kind}] Lv{item['level'][0]} {item['code']}")
                print(f"            {item['describe']}")
        return 0

    if args.action == "set":
        if args.index is None or not args.value:
            print("用法: stash set <1-4> <BL2(...)代码 或 代码库名字> [--dry-run]")
            return 1
        slot = int(args.index)
        value = args.value
        if not itemcodes.is_code(value):
            import bl2toolkit.specials as spec

            entry = spec.find(value)
            if entry is None:
                print(f"不是代码，也找不到名字: {value!r}")
                return 1
            code = entry["code"]
            label = entry["name"]
        else:
            code = value
            label = "直接代码"
        pf.set_stash_slot(slot, code)
        print(f"准备写入储藏箱 {slot}: {label}")
        if args.dry_run:
            print("--dry-run：未写入 profile.bin")
            return 0
        dest = pf.save(backup=not args.no_backup)
        print(f"已写入: {dest}")
        return 0

    if args.action == "clear":
        if args.index is None:
            print("用法: stash clear <1-4>")
            return 1
        slot = int(args.index)
        pf.set_stash_slot(slot, None)
        print(f"准备清空储藏箱 {slot}")
        if args.dry_run:
            print("--dry-run：未写入 profile.bin")
            return 0
        dest = pf.save(backup=not args.no_backup)
        print(f"已写入: {dest}")
        return 0

    if args.action == "export":
        codes = [c for c in pf.stash_slots() if c]
        if args.output:
            Path(args.output).write_text("\n".join(codes) + "\n", encoding="utf-8")
            print(f"已导出 {len(codes)} 条到 {args.output}")
        else:
            for i, code in enumerate(pf.stash_slots(), 1):
                print(f"{i}: {code or '(空)'}")
        return 0
    return 0


def cmd_backup(args: argparse.Namespace) -> int:
    if args.action == "list":
        rows = list_backups()
        if args.json:
            _dump(rows)
            return 0
        if not rows:
            print("还没有备份")
            return 0
        for row in rows:
            print(f"{row['created']:<22} {Path(row['backup_dir']).name:<40} {row['note'][:30]}")
    elif args.action == "restore":
        try:
            dest = restore_backup(args.backup_dir)
        except RuntimeError as exc:
            print(f"拒绝恢复：{exc}")
            return 1
        print(f"已恢复: {dest}")
    return 0


# --------------------------------------------------------------------------
# 参数解析
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bl2", description=f"无主之地2 存档/武器工具 v{__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("saves", help="列出所有角色存档")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_saves)

    sp = sub.add_parser("info", help="查看存档详情")
    sp.add_argument("slot", help="槽位：1 / Save0001 / 存档路径")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_info)

    sp = sub.add_parser("skills", help="列出已学技能与技能点")
    sp.add_argument("slot")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_skills)

    sp = sub.add_parser("edit", help="修改角色属性（等级/金钱/技能点等）")
    sp.add_argument("slot")
    sp.add_argument("--skill-points", type=int, help="设置未分配技能点数")
    sp.add_argument("--add-skill-points", type=int, help="增加技能点")
    sp.add_argument("--level", type=int, help="角色等级 1-80")
    sp.add_argument("--money", type=int)
    sp.add_argument("--eridium", type=int)
    sp.add_argument("--seraph", type=int)
    sp.add_argument("--torgue", type=int)
    sp.add_argument("--backpack", type=int, help="背包格子 12-39")
    sp.add_argument("--bank", type=int, help="银行格子 6-24")
    sp.add_argument("--gun-slots", type=int, choices=[2, 3, 4])
    sp.add_argument("--name", help="角色名")
    sp.add_argument("--unlock-uvhm", action="store_true", help="解锁三周目 UVHM")
    sp.add_argument("--max-ammo", action="store_true", help="补满弹药")
    sp.add_argument("--save-as", help="另存到指定文件（不覆盖原存档）")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--no-backup", action="store_true")
    sp.set_defaults(func=cmd_edit)

    sp = sub.add_parser("export", help="导出物品代码")
    sp.add_argument("slot")
    sp.add_argument("-o", "--output", help="输出文件（省略则打印到屏幕）")
    sp.add_argument("--sections", default="bank,items,weapons")
    sp.set_defaults(func=cmd_export)

    sp = sub.add_parser("import", help="导入物品代码（同步武器）")
    sp.add_argument("slot")
    sp.add_argument("files", nargs="+", help="含 BL2(...) 代码的文本文件")
    sp.add_argument("--where", choices=["auto", "bank", "items", "weapons"], default="auto")
    sp.add_argument("--no-dedupe", action="store_true", help="不跳过已存在的物品")
    sp.add_argument("--save-as")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--no-backup", action="store_true")
    sp.set_defaults(func=cmd_import)

    sp = sub.add_parser("sync", help="把武器/物品从一个角色同步到另一个角色")
    sp.add_argument("source", help="来源槽位")
    sp.add_argument("target", help="目标槽位")
    sp.add_argument("--sections", default="weapons", help="bank,items,weapons 组合")
    sp.add_argument("--where", choices=["auto", "bank", "items", "weapons"], default="bank")
    sp.add_argument("--limit", type=int, help="最多同步多少条")
    sp.add_argument("--no-dedupe", action="store_true")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--no-backup", action="store_true")
    sp.set_defaults(func=cmd_sync)

    sp = sub.add_parser("code", help="物品代码编解码")
    sp.add_argument("action", choices=["decode", "encode", "describe", "check", "risk"])
    sp.add_argument("value", help="代码 / 代码文件路径(json)")
    sp.set_defaults(func=cmd_code)

    sp = sub.add_parser("guns", help="本地黑枪代码库")
    sp.add_argument("action", choices=["list", "build", "add"])
    sp.add_argument("name", nargs="?", help="guns add 时按名字选择；guns list 时作为搜索词")
    sp.add_argument("slot", nargs="?", help="guns add 时的槽位")
    sp.add_argument("--query", default="", help="搜索关键词")
    sp.add_argument("--kind", choices=["weapon", "item"])
    sp.add_argument("--limit", type=int, default=200)
    sp.add_argument("--count", type=int, default=1, help="导入数量")
    sp.add_argument("--where", choices=["auto", "bank", "items", "weapons"], default="bank")
    sp.add_argument("--no-dedupe", action="store_true")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--save-as")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--no-backup", action="store_true")
    sp.set_defaults(func=cmd_guns)

    sp = sub.add_parser("parts", help="离线部件查找/统计/覆盖率（Gibbed dumps）")
    sp.add_argument("action", choices=["stats", "search", "show", "slots", "coverage"])
    sp.add_argument("query", nargs="?", default="", help="search 关键词 / show 的路径或 lib:asset")
    sp.add_argument("--kind", choices=["weapon", "item", "name"])
    sp.add_argument("--limit", type=int, default=30)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_parts)

    sp = sub.add_parser("balance", help="离线数值平衡：等级膨胀/UVHM/OP/Badass（只预览）")
    sp.add_argument("action", choices=["sources", "curve", "compare", "badass", "op", "equipment", "preview"])
    sp.add_argument("--levels", default="55,60,72,80", help="逗号分隔等级")
    sp.add_argument("--const", type=float, default=None, help="UniversalBalanceScaler 常量（默认 1.13）")
    sp.add_argument("--no-op", action="store_true", help="compare 时不算 OP 行")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_balance)

    sp = sub.add_parser("xp", help="离线 XP 加成计算/边界/能力（不写真实 XP/等级）")
    sp.add_argument("action", choices=["calc", "table", "capabilities", "preview"])
    sp.add_argument("--base", type=int, default=1000, help="游戏结算 base XP")
    sp.add_argument("--percent", type=float, default=50.0, help="额外百分比，如 50 表示 +50%")
    sp.add_argument("--bases", default="100,500,1000", help="table 的 base 列表，逗号分隔")
    sp.add_argument("--current-level", type=int, default=None)
    sp.add_argument("--level-cap", type=int, default=80)
    sp.add_argument("--apply-at-cap", action="store_true", help="满级仍标记应用（离线计算用，未验证）")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_xp)

    sp = sub.add_parser("combine", help="黑枪组合预览：替换部件并输出代码/警告（不写存档）")
    sp.add_argument("code", help="基础武器/物品代码 BL2(...)")
    sp.add_argument("--part", action="append", metavar="SLOT=VALUE", help="槽位=lib:asset 或 槽位=完整部件路径；可多次")
    sp.add_argument("--allow-append", action="store_true", help="物品没有该槽位时也追加（未验证）")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_combine)

    sp = sub.add_parser("skill", help="技能修改：查看目录/设置等级/点满/清空/效果能力表")
    sp.add_argument("action", choices=["available", "summary", "set", "fill", "reset", "fx"])
    sp.add_argument("args", nargs="*", help="available: <职业或槽位>；summary/reset: <槽位>；set: <槽位> <技能> <等级>；fx: [list|summary|show|plan] [效果id]")
    sp.add_argument("--tree", help="fill/available 时的技能树名")
    sp.add_argument("--all", action="store_true", help="fill 时点满全部")
    sp.add_argument("--query", default="", help="skill fx list 的搜索词")
    sp.add_argument("--grade", help="skill fx list 的分级过滤：L1/L2/L3/L4")
    sp.add_argument("--allow-over-max", action="store_true", help="允许超过技能最大等级")
    sp.add_argument("--no-tier-check", action="store_true", help="跳过技能树层级校验")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--no-backup", action="store_true")
    sp.set_defaults(func=cmd_skill)

    sp = sub.add_parser("stash", help="Claptrap 共享储藏箱（profile.bin，4 格，跨角色）")
    sp.add_argument("action", choices=["info", "set", "clear", "export"])
    sp.add_argument("index", nargs="?", help="储藏箱格子 1-4（set/clear）")
    sp.add_argument("value", nargs="?", help="set 时的 BL2(...) 代码或代码库名字")
    sp.add_argument("--profile", help="profile.bin 路径（默认自动找）")
    sp.add_argument("-o", "--output", help="export 输出文件")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--no-backup", action="store_true")
    sp.set_defaults(func=cmd_stash)

    sp = sub.add_parser("specials", help="特殊武器总表（传说/珠光/彩色/炽天使/E科技/独特）")
    sp.add_argument("action", choices=["list", "stats", "add"])
    sp.add_argument("args", nargs="*", help="list: 搜索词；add: <槽位> <名字或代码>")
    sp.add_argument("--rarity", help="稀有度过滤：传说/珠光/彩色/炽天使/深红色(E科技)/独特/蓝装/紫装/龙堡")
    sp.add_argument("--kind", help="类型过滤：武器/护盾/手雷/模组/圣物/道具")
    sp.add_argument("--query", default="", help="搜索关键词（名字/英文名/备注）")
    sp.add_argument("--limit", type=int, default=200)
    sp.add_argument("--count", type=int, default=1, help="specials add 时导入数量")
    sp.add_argument("--where", choices=["auto", "bank", "items", "weapons"], default="bank")
    sp.add_argument("--no-dedupe", action="store_true")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--no-backup", action="store_true")
    sp.set_defaults(func=cmd_specials)

    sp = sub.add_parser("space", help="背包/银行空间上限：查看、修改存档、检测/补丁 exe")
    sp.add_argument("action", choices=["info", "set", "exe", "patch-exe", "restore-exe", "backups"])
    sp.add_argument("slot", nargs="?", help="info/set 时的存档槽位")
    sp.add_argument("--backpack", help="背包容量（原版 12-39 步长 3；写 max 用当前 exe 上限）")
    sp.add_argument("--bank", help="银行容量（原版 6-24 步长 2；写 max 用当前 exe 上限）")
    sp.add_argument("--allow-oversize", action="store_true", help="允许写超过原版上限的值（需 exe 补丁或 SDK mod）")
    sp.add_argument("--backpack-max", type=int, help="patch-exe：把背包 clamp 上限改成这个值")
    sp.add_argument("--bank-max", type=int, help="patch-exe：把银行 clamp 上限改成这个值")
    sp.add_argument("--exe", help="指定 Borderlands2.exe 路径")
    sp.add_argument("--out", help="patch-exe 输出副本路径（默认 .local\\exe_patch）")
    sp.add_argument("--apply", action="store_true", help="patch-exe：直接替换游戏本体（会先备份）")
    sp.add_argument("--patch-cmp", action="store_true", help="patch-exe：连 clamp 阈值一起改（保留各角色各自容量）")
    sp.add_argument("--yes", action="store_true", help="配合 --apply 的确认")
    sp.add_argument("--backup-file", help="restore-exe：备份文件路径")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--no-backup", action="store_true")
    sp.set_defaults(func=cmd_space)

    sp = sub.add_parser("backup", help="备份列表 / 恢复")
    sp.add_argument("action", choices=["list", "restore"])
    sp.add_argument("backup_dir", nargs="?")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_backup)

    sp = sub.add_parser("version", help="版本")
    sp.set_defaults(func=lambda a: print(f"bl2toolkit {__version__}") or 0)

    return p


def _looks_like_slot(value: str) -> bool:
    """判断参数是不是一个可解析的存档槽位/路径。"""
    if not value:
        return False
    try:
        resolve_save(value)
        return True
    except Exception:
        return False


def _is_max(value) -> bool:
    return isinstance(value, str) and value.strip().lower() in ("max", "满", "上限")


def _parse_space_value(value, kind: str, caps) -> int | None:
    if value is None:
        return None
    if _is_max(value):
        cap = space.cap_for(kind, caps)
        if cap is None:
            raise SystemExit("找不到当前游戏 exe 的上限，无法使用 max；可直接写数字")
        return cap
    try:
        return int(value)
    except (TypeError, ValueError):
        raise SystemExit(f"无效的 {kind} 值: {value!r}（可用数字或 max）")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # guns add 的用法是 guns add <槽位> <名字>，argparse 会按 name/slot 顺序接到
    # 两个可选位置参数上，这里按"哪个能解析成存档"自动纠正顺序。
    if args.command == "guns" and args.action == "add":
        a, b = args.name, args.slot
        if a is None or b is None:
            print("用法: guns add <槽位> <代码库名字> [--count N] [--where bank]")
            return 1
        # 不管用户先写槽位还是先写名字，都把能解析成存档的那个当成槽位
        a_is_slot, b_is_slot = _looks_like_slot(a), _looks_like_slot(b)
        if a_is_slot and not b_is_slot:
            args.slot, args.name = a, b
        elif b_is_slot and not a_is_slot:
            args.slot, args.name = b, a
        else:
            print(f"无法判断哪个参数是存档槽位: {a!r} / {b!r}")
            return 1
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
