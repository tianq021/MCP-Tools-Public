"""背包 / 银行空间上限的调查与修改。

调查结论（2026-11-15 本机实测）：

1. 原版上限（exe 里硬编码的 clamp）：
   - 背包 InventorySlotMax_Misc：最小 12，最大 39（9 级 SDU，每级 +3）
   - 银行 InventorySlotMax_Bank：最小 6，最大 24（9 级 SDU，每级 +2）
   - 装备栏 weapon_slots：4
2. 这段逻辑在 Borderlands2.exe 里是一个 clamp 函数，
   可以用固定的字节模式定位：
       cmp eax, 12 ; mov eax, 12 ; cmp eax, 39 ; mov eax, 39   （背包）
       cmp eax, 6  ; mov eax, 6  ; cmp eax, 24 ; mov eax, 24   （银行）
   社区"100背包"补丁就是把背包那条的 `mov eax,39` 改成 `mov eax,100`
   （有的版本还会把银行那条改成别的值）。
3. 存档里能存更大的值（本机角色存档已有背包 200/250），
   但**载入游戏时会被当前 exe 的 clamp 压回上限**，所以只改存档不能真正扩容。
   要真正扩容需要：① 打 exe 补丁；或 ② 用 PythonSDK mod 在运行时直接改
   `InvManager.InventorySlotMax_Misc`（BackpackManager 滑块 0-200）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Optional

from borderlands.datautil.protobuf import (
    read_protobuf,
    read_repeated_protobuf_value,
    write_protobuf,
    write_repeated_protobuf_value,
)

from . import BACKUP_DIR
from .paths import is_game_running
from .saves import F_BANK_SIZE, F_SIZES, F_BLACK_MARKET, SaveFile

#: 原版（vanilla）空间上限
VANILLA_LIMITS = {
    "backpack": {"min": 12, "max": 39, "step": 3, "label": "背包"},
    "bank": {"min": 6, "max": 24, "step": 2, "label": "银行"},
}
WEAPON_SLOTS_MAX = 4
#: 存档字段能表达的上限（我们允许写入的硬上限）
SAVE_HARD_MAX = 255
#: SDK BackpackManager 滑块上限
SDK_BACKPACK_MAX = 200

GAME_EXE_ENV = "BL2_GAME_EXE"
DEFAULT_GAME_EXE = Path(r"F:\SteamLibrary\steamapps\common\Borderlands 2\Binaries\Win32\Borderlands2.exe")

#: 背包 clamp 的字节模式（cmp 12 / mov 12 / cmp 39 / mov <max>）
BACKPACK_CLAMP_RE = re.compile(
    rb"\x83\xF8\x0C\x7D\x07\xB8\x0C\x00\x00\x00\xEB\x0A\x83\xF8(?P<cmp>.)\x7C\x05\xB8(?P<mov>....)"
)
#: 银行 clamp 的字节模式
BANK_CLAMP_RE = re.compile(
    rb"\x83\xF8\x06\x7D\x07\xB8\x06\x00\x00\x00\xEB\x0A\x83\xF8(?P<cmp>.)\x7C\x05\xB8(?P<mov>....)"
)
#: PythonSDK 加载器痕迹（用于判断这个 exe 是否带 SDK）
SDK_MARKERS = (b"python37.dll", b"PythonSDK", b"python-sdk", b"unrealsdk")


def find_game_exe() -> Optional[Path]:
    env = os.environ.get(GAME_EXE_ENV)
    if env and Path(env).is_file():
        return Path(env)
    if DEFAULT_GAME_EXE.is_file():
        return DEFAULT_GAME_EXE
    return None


def sha256(path: Path | str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class ExeCaps:
    path: str
    sha256: str
    size: int
    backpack_min: int
    backpack_cmp: int
    backpack_max: int
    bank_min: int
    bank_cmp: int
    bank_max: int
    backpack_offset: int
    bank_offset: int
    sdk_loader: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_exe(path: Path | str) -> ExeCaps:
    """读取 Borderlands2.exe，解析背包/银行的 clamp 上限。"""
    p = Path(path)
    data = p.read_bytes()
    bp = BACKPACK_CLAMP_RE.search(data)
    bk = BANK_CLAMP_RE.search(data)
    if not bp or not bk:
        raise ValueError(
            f"没有在 {p} 里找到已知的背包/银行 clamp 模式（可能游戏版本不同，暂不支持自动识别）"
        )
    return ExeCaps(
        path=str(p),
        sha256=hashlib.sha256(data).hexdigest(),
        size=len(data),
        backpack_min=12,
        backpack_cmp=bp.group("cmp")[0],
        backpack_max=int.from_bytes(bp.group("mov"), "little"),
        bank_min=6,
        bank_cmp=bk.group("cmp")[0],
        bank_max=int.from_bytes(bk.group("mov"), "little"),
        backpack_offset=bp.start(),
        bank_offset=bk.start(),
        sdk_loader=_detect_sdk_loader(p, data),
    )


def _detect_sdk_loader(exe: Path, data: bytes) -> bool:
    """判断这个 exe 是否带 PythonSDK 加载器。

    SDK 版 exe 旁边会有 python37.dll / python37.zip；exe 里也可能有加载器字符串。
    """
    if any(m in data for m in SDK_MARKERS):
        return True
    siblings = {f.name.lower() for f in exe.parent.iterdir()} if exe.parent.is_dir() else set()
    return "python37.dll" in siblings and "python37.zip" in siblings


def analyze_installed() -> Optional[ExeCaps]:
    exe = find_game_exe()
    if exe is None:
        return None
    try:
        return analyze_exe(exe)
    except ValueError:
        return None


# --------------------------------------------------------------------------
# 存档侧：读取 / 修改空间字段
# --------------------------------------------------------------------------


def _slot_values(sf: SaveFile) -> tuple[dict[int, list], list[int]]:
    """返回 (sizes 子 protobuf, black_market 列表)。"""
    if F_SIZES not in sf.player:
        sf.player[F_SIZES] = [[2, b"\x08\x0c\x10\x04\x18\x02"]]  # 12/4/2 的保底结构
    slots = read_protobuf(sf.player[F_SIZES][0][1])
    if F_BLACK_MARKET in sf.player:
        bm = read_repeated_protobuf_value(sf.player[F_BLACK_MARKET][0][1], 0)
    else:
        bm = [0] * 9
    while len(bm) < 9:
        bm.append(0)
    return slots, bm


def space_info(sf: SaveFile) -> dict[str, Any]:
    """读取角色的空间信息（容量、SDU 等级、占用、当前 exe 上限）。"""
    slots, bm = _slot_values(sf)
    backpack = slots.get(1, [[0, 0]])[0][1]
    weapon_slots = slots.get(2, [[0, 0]])[0][1]
    weapon_slots_shown = slots.get(3, [[0, 0]])[0][1]
    bank_size = sf.player[F_BANK_SIZE][0][1] if sf.player.get(F_BANK_SIZE) else 0
    counts = sf.item_counts()
    caps = analyze_installed()
    effective = None
    if caps:
        # clamp 逻辑：容量 < 阈值时保持原值；容量 >= 阈值时直接变成补丁上限
        effective = {
            "backpack": caps.backpack_max if backpack >= caps.backpack_cmp else backpack,
            "bank": caps.bank_max if bank_size >= caps.bank_cmp else bank_size,
        }
    return {
        "slot": sf.path.stem,
        "character": sf.character_name,
        "backpack": {
            "capacity": backpack,
            "sdu_level": bm[7] if len(bm) > 7 else None,
            "items_in_use": counts["items"] + counts["weapons"],
        },
        "bank": {
            "capacity": bank_size,
            "sdu_level": bm[8] if len(bm) > 8 else None,
            "items_in_use": counts["bank"],
        },
        "weapon_slots": {"capacity": weapon_slots, "shown": weapon_slots_shown, "vanilla_max": WEAPON_SLOTS_MAX},
        "vanilla_limits": VANILLA_LIMITS,
        "installed_exe": caps.to_dict() if caps else None,
        "effective_in_game": effective,
        "warnings": _space_warnings(backpack, bank_size, counts, caps),
    }


def _space_warnings(backpack: int, bank: int, counts: dict[str, int], caps: Optional[ExeCaps]) -> list[str]:
    warnings: list[str] = []
    if caps:
        eff_bp = caps.backpack_max if backpack >= caps.backpack_cmp else backpack
        eff_bk = caps.bank_max if bank >= caps.bank_cmp else bank
        if backpack > caps.backpack_max:
            if counts["items"] + counts["weapons"] > caps.backpack_max:
                warnings.append(
                    f"背包容量 {backpack} 会被补丁夹到 {caps.backpack_max}，"
                    f"而背包里已有 {counts['items'] + counts['weapons']} 件物品，超出的部分有丢失/不可见风险"
                )
            else:
                warnings.append(
                    f"背包容量 {backpack} 会被补丁夹到 {caps.backpack_max}（物品 {counts['items'] + counts['weapons']} 件仍放得下）"
                )
        elif backpack < caps.backpack_cmp:
            warnings.append(
                f"背包容量 {backpack} 还没达到补丁阈值 {caps.backpack_cmp}："
                f"这种角色（没在游戏里扩充过）载入后不会被自动提升到 {caps.backpack_max}；"
                f"处理办法：游戏里买满 SDU 到 {caps.backpack_cmp}，或用 space set --backpack max 直接写存档"
            )
        if bank > caps.bank_max:
            if counts["bank"] > caps.bank_max:
                warnings.append(
                    f"银行容量 {bank} 会被补丁夹到 {caps.bank_max}，"
                    f"而银行里已有 {counts['bank']} 件物品，超出的部分有丢失/不可见风险"
                )
            else:
                warnings.append(f"银行容量 {bank} 会被补丁夹到 {caps.bank_max}（物品 {counts['bank']} 件仍放得下）")
        elif bank < caps.bank_cmp:
            warnings.append(
                f"银行容量 {bank} 还没达到补丁阈值 {caps.bank_cmp}："
                f"载入后不会被自动提升到 {caps.bank_max}；"
                f"处理办法：游戏里买满 SDU 到 {caps.bank_cmp}，或用 space set --bank max 直接写存档"
            )
        if counts["items"] + counts["weapons"] > eff_bp:
            warnings.append(
                f"背包里已有 {counts['items'] + counts['weapons']} 件物品，超过实际生效容量 {eff_bp}，有不可见/丢失风险"
            )
        if counts["bank"] > eff_bk:
            warnings.append(f"银行里已有 {counts['bank']} 件物品，超过实际生效容量 {eff_bk}，有不可见/丢失风险")
    else:
        if counts["items"] + counts["weapons"] > backpack:
            warnings.append(f"背包里已有 {counts['items'] + counts['weapons']} 件物品，超过容量 {backpack}")
        if counts["bank"] > bank:
            warnings.append(f"银行里已有 {counts['bank']} 件物品，超过容量 {bank}")
    if caps and not caps.sdk_loader:
        warnings.append("当前 exe 看起来没有 PythonSDK 加载器；想用 BackpackManager mod 扩容需要 SDK 版 exe")
    return warnings


def cap_for(kind: str, caps: Optional[ExeCaps]) -> Optional[int]:
    """返回当前 exe 的该类上限（给 --backpack max 用）。"""
    if caps is None:
        return None
    return caps.backpack_max if kind == "backpack" else caps.bank_max


def _check_value(kind: str, n: int, allow_oversize: bool) -> None:
    info = VANILLA_LIMITS[kind]
    if n < info["min"]:
        raise ValueError(f"{info['label']}不能小于 {info['min']}（当前设置 {n}）")
    if n > SAVE_HARD_MAX:
        raise ValueError(f"{info['label']}上限按 {SAVE_HARD_MAX} 限制（当前设置 {n}）")
    if not allow_oversize:
        if n > info["max"]:
            raise ValueError(
                f"{info['label']}超过原版上限 {info['max']}；要写更大的值需要 --allow-oversize，"
                f"并且必须配合 exe 补丁或 SDK mod 才能生效"
            )
        if (n - info["min"]) % info["step"] != 0:
            raise ValueError(
                f"{info['label']}必须满足 {info['min']} + {info['step']}*n，可选值："
                + ", ".join(str(info["min"] + info["step"] * i) for i in range(0, 10))
            )


def set_space(
    sf: SaveFile,
    *,
    backpack: Optional[int] = None,
    bank: Optional[int] = None,
    allow_oversize: bool = False,
) -> dict[str, Any]:
    """修改背包/银行容量（同时同步 SDU 等级）。

    - 原版范围内：backpack 12..39 且步长 3；bank 6..24 且步长 2，会自动换算 SDU 等级。
    - allow_oversize=True 时允许写更大值（需要 exe 补丁或 SDK mod 才真正生效）。
    """
    if backpack is not None:
        _check_value("backpack", backpack, allow_oversize)
    if bank is not None:
        _check_value("bank", bank, allow_oversize)
    if backpack is None and bank is None:
        raise ValueError("至少要指定 backpack 或 bank")

    before = space_info(sf)

    def do(player):
        slots = read_protobuf(player[F_SIZES][0][1])
        bm = read_repeated_protobuf_value(player[F_BLACK_MARKET][0][1], 0)
        if backpack is not None:
            slots[1][0][1] = backpack
            bm[7] = 9 if backpack > VANILLA_LIMITS["backpack"]["max"] else (backpack - 12) // 3
        if bank is not None:
            if F_BANK_SIZE in player:
                player[F_BANK_SIZE][0][1] = bank
            else:
                player[F_BANK_SIZE] = [[0, bank]]
            bm[8] = 9 if bank > VANILLA_LIMITS["bank"]["max"] else (bank - 6) // 2
        player[F_SIZES][0][1] = write_protobuf(slots)
        player[F_BLACK_MARKET][0][1] = write_repeated_protobuf_value(bm, 0)

    sf._edit_player(do)
    after = space_info(sf)
    return {"before": before, "after": after}


# --------------------------------------------------------------------------
# exe 侧：补丁 / 备份 / 恢复
# --------------------------------------------------------------------------

EXE_BACKUP_DIR = BACKUP_DIR / "exe"


def patch_exe(
    source: Optional[Path | str] = None,
    *,
    output: Optional[Path | str] = None,
    backpack_max: Optional[int] = None,
    bank_max: Optional[int] = None,
    patch_cmp: bool = False,
    apply_to_game: bool = False,
) -> dict[str, Any]:
    """修改 exe 里的 clamp 上限（背包/银行）。

    - 默认模式（patch_cmp=False）：保留原版 clamp 阈值（背包 39 / 银行 24），
      把超过阈值的容量直接改成新上限 —— 社区"100背包"补丁就是这个做法，
      效果是"拉满的角色自动变成新上限"。
    - patch_cmp=True：把阈值和结果一起改成新上限，只有容量 >= 新上限才会被夹到新上限，
      更适合"每个角色保留各自容量"的用法（配合改存档）。

    默认只写输出副本（不碰游戏本体）。apply_to_game=True 时先自动备份再原地替换。
    """
    src = Path(source) if source else find_game_exe()
    if src is None or not src.is_file():
        raise FileNotFoundError("找不到 Borderlands2.exe，可用参数或环境变量 BL2_GAME_EXE 指定")
    if backpack_max is None and bank_max is None:
        raise ValueError("至少指定 backpack_max 或 bank_max")
    if backpack_max is not None and not (12 <= backpack_max <= SAVE_HARD_MAX):
        raise ValueError(f"backpack_max 必须在 12..{SAVE_HARD_MAX}")
    if bank_max is not None and not (6 <= bank_max <= SAVE_HARD_MAX):
        raise ValueError(f"bank_max 必须在 6..{SAVE_HARD_MAX}")

    data = bytearray(src.read_bytes())
    caps_before = analyze_exe(src)
    changed: list[dict[str, Any]] = []

    def patch_one(regex, label: str, new_max: Optional[int]):
        nonlocal data
        m = regex.search(bytes(data))
        if not m:
            raise ValueError(f"exe 里找不到{label} clamp 模式")
        old_cmp = data[m.start("cmp")]
        old_max = int.from_bytes(data[m.start("mov"):m.start("mov") + 4], "little")
        if new_max is None:
            return
        if patch_cmp and old_cmp != (new_max & 0xFF):
            data[m.start("cmp")] = new_max & 0xFF
            changed.append({"field": f"{label}阈值", "offset": hex(m.start("cmp")), "old": old_cmp, "new": new_max & 0xFF})
        if old_max != new_max:
            data[m.start("mov"):m.start("mov") + 4] = int(new_max).to_bytes(4, "little")
            changed.append({"field": label, "offset": hex(m.start("mov")), "old": old_max, "new": new_max})

    patch_one(BACKPACK_CLAMP_RE, "背包", backpack_max)
    patch_one(BANK_CLAMP_RE, "银行", bank_max)

    if apply_to_game:
        if is_game_running():
            raise RuntimeError("游戏正在运行，先退出游戏再打 exe 补丁")
        EXE_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = EXE_BACKUP_DIR / f"Borderlands2.exe.{caps_before.sha256[:12]}.{stamp}.bak"
        shutil.copy2(src, backup)
        tmp = src.with_name(src.name + ".tmp")
        tmp.write_bytes(bytes(data))
        os.replace(tmp, src)
        target = src
        backup_path = str(backup)
    else:
        target = Path(output) if output else (Path.cwd() / ".local" / "exe_patch" / "Borderlands2.exe")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(bytes(data))
        backup_path = None

    caps_after = analyze_exe(target)
    return {
        "source": str(src),
        "target": str(target),
        "backup": backup_path,
        "caps_before": caps_before.to_dict(),
        "caps_after": caps_after.to_dict(),
        "changed": changed,
        "patch_cmp": patch_cmp,
        "applied_to_game": apply_to_game,
    }


def list_exe_backups() -> list[dict[str, Any]]:
    out = []
    if not EXE_BACKUP_DIR.is_dir():
        return out
    for p in sorted(EXE_BACKUP_DIR.glob("*.bak"), reverse=True):
        out.append({"file": str(p), "size": p.stat().st_size, "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.stat().st_mtime))})
    return out


def restore_exe_backup(backup_file: str | Path, target: Optional[Path | str] = None) -> dict[str, Any]:
    """用备份恢复 Borderlands2.exe（恢复前会把当前 exe 再备份一次）。"""
    bak = Path(backup_file)
    if not bak.is_file():
        raise FileNotFoundError(f"备份不存在: {bak}")
    dest = Path(target) if target else find_game_exe()
    if dest is None:
        raise FileNotFoundError("找不到目标 Borderlands2.exe")
    if is_game_running():
        raise RuntimeError("游戏正在运行，先退出游戏")
    EXE_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    safety = EXE_BACKUP_DIR / f"Borderlands2.exe.before-restore.{time.strftime('%Y%m%d-%H%M%S')}.bak"
    shutil.copy2(dest, safety)
    shutil.copy2(bak, dest)
    return {"restored": str(dest), "from": str(bak), "safety_backup": str(safety), "caps": analyze_exe(dest).to_dict()}
