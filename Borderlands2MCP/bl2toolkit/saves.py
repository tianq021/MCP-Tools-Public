"""无主之地2 存档读写与常用修改。"""

from __future__ import annotations

import io
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from borderlands.bl2 import AppBL2
from borderlands.datautil.common import replace_raw_item_key
from borderlands.datautil.protobuf import apply_structure, read_protobuf_value, remove_structure, write_protobuf
from borderlands.savefile import read_protobuf

from . import itemcodes
from .paths import backup_save, is_game_running, restore_backup, write_bytes_atomic

# 顶层 protobuf 字段编号（来自 bl2.py 的 save_structure）
F_CLASS = 1
F_LEVEL = 2
F_XP = 3
F_SKILL_POINTS = 4
F_CURRENCY = 6
F_PLAYTHROUGHS = 7
F_SKILLS = 8
F_SIZES = 13
F_APPEARANCE = 19
F_SAVE_GAME_ID = 20
F_BLACK_MARKET = 36
F_BANK = 41
F_ITEMS = 53
F_WEAPONS = 54
F_BANK_SIZE = 56

#: 角色类 -> (中文名, 英文职业名)
CHARACTER_CLASSES = {
    "GD_Mercenary.Character.CharClass_Mercenary": ("萨尔瓦多", "Gunzerker"),
    "GD_Siren.Character.CharClass_Siren": ("玛雅", "Siren"),
    "GD_Assassin.Character.CharClass_Assassin": ("零", "Assassin"),
    "GD_Soldier.Character.CharClass_Soldier": ("艾克斯顿", "Commando"),
    "GD_Tulip_Mechromancer.Character.CharClass_Mechromancer": ("盖姬", "Mechromancer"),
    "GD_Lilac_PlayerClass.Character.CharClass_LilacPlayerClass": ("克里格", "Psycho"),
}

SECTION_FIELDS = {"bank": F_BANK, "items": F_ITEMS, "weapons": F_WEAPONS}
SECTION_LABELS = {"bank": "银行", "items": "背包物品", "weapons": "背包武器"}


def _new_app(*extra_args: str) -> AppBL2:
    """构造一个只用来编解码的 AppBL2（输入取 stdin 占位、不输出文件）。"""
    return AppBL2(["-", *extra_args])


@dataclass
class ItemEntry:
    section: str
    code: str
    level: list[int]
    is_weapon: int
    raw: bytes = field(repr=False, default=b"")

    @property
    def level_value(self) -> int:
        return int(self.level[0]) if self.level else 0


class SaveFile:
    """一份 BL2 角色存档。

    典型用法::

        s = SaveFile("1")                 # 第 1 号存档
        print(s.summary())
        s.set_skill_points(50)
        s.save(backup=True)               # 自动备份后写回
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if not self.path.is_file():
            raise FileNotFoundError(f"存档不存在: {self.path}")
        self.app = _new_app()
        self.raw = self.path.read_bytes()
        self._reload()

    # -- 内部 ---------------------------------------------------------

    def _reload(self) -> None:
        self.player = read_protobuf(self.app.unwrap_player_data(self.raw))
        self._structured: Optional[dict] = None

    def _set_raw(self, raw: bytes) -> None:
        self.raw = raw
        self._reload()

    def _edit_player(self, fn) -> None:
        """在解码后的 player protobuf 字典上做修改，然后重新打包。"""
        self._reload()
        fn(self.player)
        self._set_raw(self.app.wrap_player_data(write_protobuf(self.player)))

    def _apply_library_args(self, args: list[str]) -> None:
        """复用底层库的成熟修改逻辑（等级/金钱/背包等）。"""
        if not args:
            return
        app = _new_app(*args)
        new_raw = app.modify_save(self.raw)
        if new_raw == self.raw:
            raise RuntimeError(f"修改没有生效，检查参数: {args}")
        self._set_raw(new_raw)

    @property
    def structured(self) -> dict:
        if self._structured is None:
            self._structured = apply_structure(self.player, self.app.save_structure)
        return self._structured

    # -- 只读信息 -----------------------------------------------------

    @property
    def class_path(self) -> str:
        field_ = self.player.get(F_CLASS)
        if not field_:
            return ""
        return field_[0][1].decode("utf-8", "replace")

    @property
    def character_class(self) -> tuple[str, str]:
        return CHARACTER_CLASSES.get(self.class_path, ("未知角色", "Unknown"))

    @property
    def character_name(self) -> str:
        field_ = self.player.get(F_APPEARANCE)
        if not field_:
            return ""
        try:
            sub = read_protobuf(field_[0][1])
            raw_name = sub[1][0][1]
            return raw_name.decode("utf-8", "replace")
        except Exception:
            return ""

    @property
    def level(self) -> int:
        field_ = self.player.get(F_LEVEL)
        return int(field_[0][1]) if field_ else 0

    @property
    def experience(self) -> int:
        field_ = self.player.get(F_XP)
        return int(field_[0][1]) if field_ else 0

    @property
    def unspent_skill_points(self) -> int:
        field_ = self.player.get(F_SKILL_POINTS)
        return int(field_[0][1]) if field_ else 0

    def list_skills(self) -> list[dict[str, Any]]:
        """返回已学习的技能：[{name, level}]。"""
        result = []
        for entry in self.player.get(F_SKILLS) or []:
            try:
                sub = read_protobuf(entry[1])
                result.append(
                    {
                        "name": sub[1][0][1].decode("utf-8", "replace"),
                        "level": int(sub[2][0][1]) if 2 in sub else 0,
                    }
                )
            except Exception:
                continue
        return result

    @property
    def used_skill_points(self) -> int:
        return sum(s["level"] for s in self.list_skills())

    def currency(self) -> dict[str, int]:
        """读取金钱/镒元素/晶石/托格代币等。"""
        raw = self.player.get(F_CURRENCY)
        if not raw:
            return {}
        data = raw[0][1]
        values = []
        try:
            b = io.BytesIO(data)
            while b.tell() < len(data):
                values.append(read_protobuf_value(b, 0))
        except Exception:
            return {}
        keys = ["money", "eridium", "seraph", "unknown3", "torgue"]
        out = {}
        for i, key in enumerate(keys):
            if i < len(values):
                out[key] = values[i]
        return out

    def playthroughs_completed(self) -> int:
        field_ = self.player.get(F_PLAYTHROUGHS)
        return int(field_[0][1]) if field_ else 0

    def op_level(self) -> int:
        """读取已解锁的 Overpower（OP）等级 0-10。

        OP 等级存在背包里的"假物品"（expansion item）中，按 Gibbed SaveExpansion.cs 的编码：
        该假物品的 unknown2 字段 = -(4 | (op << 8))（64 位有符号）。
        """
        for entry in self.player.get(F_ITEMS) or []:
            try:
                field_data = read_protobuf(entry[1])
                if 2 not in field_data:
                    continue
                _is_weapon, item, _key = self.app.unwrap_item(field_data[1][0][1])
                if not (item and item[0] == 255 and all(v == 0 for v in item[1:])):
                    continue
                raw = int(field_data[2][0][1])
                signed = raw - (1 << 64) if raw >= (1 << 63) else raw
                idnum = (-signed) & 0xFF
                if idnum == 4:
                    return ((-signed) >> 8) & 0x7FFFFF
            except Exception:  # noqa: BLE001
                continue
        return 0

    def item_counts(self) -> dict[str, int]:
        return {section: len(self._iter_item_entries(section)) for section in SECTION_FIELDS}

    def summary(self) -> dict[str, Any]:
        zh_name, en_name = self.character_class
        return {
            "file": str(self.path),
            "slot": self.path.stem,
            "name": self.character_name,
            "class": f"{zh_name}/{en_name}",
            "class_path": self.class_path,
            "level": self.level,
            "experience": self.experience,
            "playthroughs_completed": self.playthroughs_completed(),
            "skill_points": self.unspent_skill_points,
            "skill_points_used": self.used_skill_points,
            "skills": len(self.list_skills()),
            "currency": self.currency(),
            "items": self.item_counts(),
            "save_game_id": int(self.player[F_SAVE_GAME_ID][0][1]) if self.player.get(F_SAVE_GAME_ID) else None,
            "size": len(self.raw),
        }

    # -- 修改 ---------------------------------------------------------

    def set_skill_points(self, n: int) -> int:
        """设置"未分配技能点"数量（游戏里可以随便点技能树）。"""
        if n < 0 or n > 9999:
            raise ValueError("技能点数量必须在 0..9999 之间")

        def do(player):
            if F_SKILL_POINTS in player:
                player[F_SKILL_POINTS][0][1] = int(n)
            else:
                player[F_SKILL_POINTS] = [[0, int(n)]]

        self._edit_player(do)
        return self.unspent_skill_points

    def add_skill_points(self, delta: int) -> int:
        return self.set_skill_points(self.unspent_skill_points + delta)

    def edit(
        self,
        *,
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
        skill_points: Optional[int] = None,
        add_skill_points: Optional[int] = None,
    ) -> dict[str, Any]:
        """批量修改角色属性，返回修改后的摘要。"""
        args: list[str] = []
        if level is not None:
            args += ["--level", str(level)]
        if money is not None:
            args += ["--money", str(money)]
        if eridium is not None:
            args += ["--eridium", str(eridium)]
        if seraph is not None:
            args += ["--seraph", str(seraph)]
        if torgue is not None:
            args += ["--torgue", str(torgue)]
        if backpack is not None:
            args += ["--backpack", str(backpack)]
        if bank is not None:
            args += ["--bank", str(bank)]
        if gun_slots is not None:
            args += ["--gunslots", str(gun_slots)]
        if name is not None:
            args += ["--name", name]
        if unlock_uvhm:
            args += ["--unlock", "uvhm"]
        if max_ammo:
            args += ["--maxammo"]

        if args:
            self._apply_library_args(args)
        if skill_points is not None:
            self.set_skill_points(skill_points)
        if add_skill_points is not None:
            self.add_skill_points(add_skill_points)
        return self.summary()

    # -- 物品 / 武器 ---------------------------------------------------

    def _iter_item_raw(self, section: str) -> Iterable[tuple[bytes, bool]]:
        """遍历某分区的物品，产出 (原始物品字节, 是否假物品)。"""
        field_no = SECTION_FIELDS[section]
        for entry in self.player.get(field_no) or []:
            try:
                inner = read_protobuf(entry[1])
                raw = inner[1][0][1]
                _, item, _key = self.app.unwrap_item(raw)
                is_fake = item and item[0] == 255 and all(v == 0 for v in item[1:])
                yield raw, bool(is_fake)
            except Exception:
                continue

    def _iter_item_entries(self, section: str) -> list[ItemEntry]:
        out = []
        for raw, is_fake in self._iter_item_raw(section):
            if is_fake:
                continue
            info = self.app.unwrap_item_info(raw)
            out.append(
                ItemEntry(
                    section=section,
                    code=itemcodes.bytes_to_code(replace_raw_item_key(raw, 0)),
                    level=info["level"],
                    is_weapon=info["is_weapon"],
                    raw=raw,
                )
            )
        return out

    def export_codes(self, sections: Iterable[str] = ("bank", "items", "weapons")) -> dict[str, list[str]]:
        """导出各分区物品代码（key 归零，便于跨存档比较/去重）。"""
        result: dict[str, list[str]] = {}
        for section in sections:
            if section not in SECTION_FIELDS:
                raise ValueError(f"未知分区: {section!r}（可选 bank/items/weapons）")
            result[section] = [e.code for e in self._iter_item_entries(section)]
        return result

    def all_codes(self) -> list[str]:
        codes = self.export_codes()
        return codes["bank"] + codes["items"] + codes["weapons"]

    def has_code(self, code: str) -> bool:
        return itemcodes.with_key_zero(code) in set(self.all_codes())

    def import_codes(
        self,
        codes: Iterable[str],
        *,
        where: str = "auto",
        dedupe: bool = True,
    ) -> dict[str, int]:
        """把一批物品代码导入存档。

        where: auto（按物品类型进背包/银行由代码决定）| bank | items | weapons
        dedupe: 已存在的相同代码（忽略 key）跳过
        """
        if where not in ("auto", "bank", "items", "weapons"):
            raise ValueError(f"where 参数无效: {where!r}")

        codes = [c.strip() for c in codes if c and c.strip()]
        # 去重只在"写入前存档里已有的物品"范围内判断，
        # 这样一次请求里传重复代码（例如想要 N 把同名枪）不会被吞掉。
        existing = set(self.all_codes()) if dedupe else set()

        def do(player):
            counts = {"bank": 0, "items": 0, "weapons": 0}
            for code in codes:
                try:
                    code_bytes = itemcodes.code_to_bytes(code)
                except ValueError:
                    continue
                normalized = itemcodes.with_key_zero(code)
                if dedupe and normalized in existing:
                    continue

                key = random.randrange(0x100000000) - 0x80000000
                code_bytes = replace_raw_item_key(code_bytes, key)
                is_weapon = bool(code_bytes[0] & 0x80)

                if where == "bank":
                    section, field_no = "bank", F_BANK
                    entry = {1: [[2, code_bytes]]}
                elif where == "items" or (where == "auto" and not is_weapon):
                    section, field_no = "items", F_ITEMS
                    entry = {1: [[2, code_bytes]], 2: [[0, 1]], 3: [[0, 0]], 4: [[0, 1]]}
                else:
                    section, field_no = "weapons", F_WEAPONS
                    entry = {1: [[2, code_bytes]], 2: [[0, 0]], 3: [[0, 1]]}

                player.setdefault(field_no, []).append([2, write_protobuf(entry)])
                counts[section] += 1
            return counts

        holder: dict[str, int] = {}

        def wrapper(player):
            holder.update(do(player))

        self._edit_player(wrapper)
        return holder

    def remove_items(self, codes: Iterable[str], *, sections: Iterable[str] = ("bank", "items", "weapons")) -> int:
        """按代码删除物品（忽略 key 比较），返回删除数量。"""
        targets = {itemcodes.with_key_zero(c) for c in codes}
        removed = 0

        def do(player):
            nonlocal removed
            for section in sections:
                field_no = SECTION_FIELDS[section]
                kept = []
                for entry in player.get(field_no) or []:
                    try:
                        inner = read_protobuf(entry[1])
                        raw = inner[1][0][1]
                        if itemcodes.bytes_to_code(replace_raw_item_key(raw, 0)) in targets:
                            removed += 1
                            continue
                    except Exception:
                        pass
                    kept.append(entry)
                if field_no in player:
                    player[field_no] = kept

        self._edit_player(do)
        return removed

    # -- 保存 ---------------------------------------------------------

    def save(self, target: Optional[str | Path] = None, *, backup: bool = True, force: bool = False) -> Path:
        """写回存档（默认写回原文件，并自动备份原文件）。"""
        dest = Path(target) if target else self.path
        if dest.exists() and not force:
            pass  # 备份后覆盖是正常流程
        if is_game_running() and not force:
            raise RuntimeError("检测到 Borderlands2.exe 正在运行：游戏退出时会覆盖存档。请先退出游戏，或使用 force=True")
        if backup and dest.exists():
            backup_save(dest, note="SaveFile.save 自动备份")
        write_bytes_atomic(dest, self.raw)
        if dest == self.path:
            self._reload()
        return dest

    def save_as(self, target: str | Path) -> Path:
        """另存为（不覆盖原存档，用于先测试再同步）。"""
        return self.save(target, backup=False, force=True)
