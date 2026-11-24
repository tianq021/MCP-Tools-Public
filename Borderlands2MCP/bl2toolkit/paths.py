"""存档路径发现、备份与恢复。"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Iterable, Optional

from . import BACKUP_DIR

#: Borderlands 2 默认存档根目录（Windows）
DEFAULT_SAVE_ROOT = Path(os.environ.get("USERPROFILE", "C:/Users")) / (
    "Documents/My Games/Borderlands 2/WillowGame/SaveData"
)

#: 允许通过环境变量覆盖，方便测试或其它机器
SAVE_ROOT_ENV = "BL2_SAVE_ROOT"

#: Borderlands2.exe 的常见安装位置（用于写存档前的"游戏是否在运行"检查）
GAME_EXE_NAME = "Borderlands2.exe"


def find_save_root() -> Optional[Path]:
    """返回有效的 SaveData 根目录；找不到时返回 None。"""
    env = os.environ.get(SAVE_ROOT_ENV)
    if env:
        p = Path(env)
        if p.is_dir():
            return p
        return None
    if DEFAULT_SAVE_ROOT.is_dir():
        return DEFAULT_SAVE_ROOT
    return None


def find_steam_id_dir(save_root: Optional[Path] = None) -> Optional[Path]:
    """返回 Steam 账号存档目录（SaveData 下名字像 SteamID 的子目录）。"""
    root = save_root or find_save_root()
    if root is None:
        return None
    candidates = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        if child.name.isdigit() and len(child.name) >= 10:
            candidates.append(child)
    if not candidates:
        return None
    # 多个账号时取最近写入过的那个
    candidates.sort(key=lambda p: _newest_mtime(p), reverse=True)
    return candidates[0]


def _newest_mtime(path: Path) -> float:
    newest = 0.0
    for f in path.glob("*.sav"):
        try:
            newest = max(newest, f.stat().st_mtime)
        except OSError:
            pass
    return newest


def list_saves(save_dir: Optional[Path] = None) -> list[Path]:
    """列出所有角色存档文件（SaveXXXX.sav，排除 .bak）。"""
    d = save_dir or find_steam_id_dir()
    if d is None:
        return []
    saves = [p for p in d.glob("*.sav") if p.is_file() and not p.name.endswith(".bak")]
    saves.sort(key=lambda p: p.name.lower())
    return saves


def resolve_save(spec: str, save_dir: Optional[Path] = None) -> Path:
    """把用户给的槽位（如 1 / 0001 / Save0001）解析成真实文件路径。

    也接受直接的绝对/相对路径。
    """
    p = Path(spec)
    if p.is_file():
        return p

    d = save_dir or find_steam_id_dir()
    if d is None:
        raise FileNotFoundError("找不到无主之地2存档目录，可用环境变量 BL2_SAVE_ROOT 指定")

    raw = str(spec).strip()
    raw = raw.removeprefix("Save").removeprefix("save")
    if raw.upper().endswith(".SAV"):
        raw = raw[:-4]
    if raw.isdigit() or (len(raw) == 4 and all(c in "0123456789ABCDEFabcdef" for c in raw)):
        if raw.isdigit():
            name = f"Save{int(raw):04d}.sav"
        else:
            name = f"Save{raw.upper()}.sav"
        candidate = d / name
        if candidate.is_file():
            return candidate

    # 模糊匹配文件名
    matches = [x for x in d.glob("*.sav") if raw.lower() in x.name.lower()]
    if len(matches) == 1:
        return matches[0]
    raise FileNotFoundError(f"找不到存档 {spec!r}（目录 {d}）")


def is_game_running() -> bool:
    """检查 Borderlands2.exe 是否在运行（写存档时游戏会覆盖，需提醒）。"""
    try:
        import subprocess

        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {GAME_EXE_NAME}", "/NH"],
            capture_output=True,
            text=True,
            encoding="mbcs",
            errors="replace",
            timeout=10,
        ).stdout
        return GAME_EXE_NAME.lower() in out.lower()
    except Exception:
        return False


# --------------------------------------------------------------------------
# 备份
# --------------------------------------------------------------------------


def backup_save(path: Path, note: str = "") -> Path:
    """把存档复制到 .local/backups/<时间戳>-<原文件名>/ 下，返回备份目录。"""
    path = Path(path)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = BACKUP_DIR / f"{stamp}-{path.name}"
    # 同一秒内多次备份时加后缀
    seq = 1
    while target.exists():
        seq += 1
        target = BACKUP_DIR / f"{stamp}-{path.name}-{seq}"
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target / path.name)
    meta = {
        "source": str(path),
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "note": note,
        "size": path.stat().st_size,
    }
    (target / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def list_backups() -> list[dict]:
    """列出所有备份目录，最新的在前。"""
    result = []
    if not BACKUP_DIR.is_dir():
        return result
    for d in sorted(BACKUP_DIR.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        meta = {}
        meta_file = d / "meta.json"
        if meta_file.is_file():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                meta = {}
        sav = next((p for p in d.glob("*.sav") if not p.name.endswith(".bak")), None)
        result.append(
            {
                "backup_dir": str(d),
                "save_file": str(sav) if sav else None,
                "source": meta.get("source", ""),
                "created": meta.get("created", ""),
                "note": meta.get("note", ""),
            }
        )
    return result


def restore_backup(backup_dir: str | Path, target: Optional[Path] = None, *, force: bool = False) -> Path:
    """把备份写回原存档（默认回到 meta.json 记录的源路径），写前先备份当前文件。

    与其他写工具一致：检测到游戏运行中默认拒绝；force 仅供离线测试/明确场景使用。
    """
    if is_game_running() and not force:
        raise RuntimeError("检测到 Borderlands2.exe 正在运行：禁止恢复备份，请先退出游戏")
    d = Path(backup_dir)
    if not d.is_dir():
        raise FileNotFoundError(f"备份目录不存在: {d}")
    meta_file = d / "meta.json"
    meta = {}
    if meta_file.is_file():
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
    sav = next((p for p in d.glob("*.sav") if not p.name.endswith(".bak")), None)
    if sav is None:
        raise FileNotFoundError(f"备份目录里没有 .sav: {d}")
    dest = Path(target) if target else Path(meta.get("source", ""))
    if not dest.name:
        raise ValueError("无法确定恢复目标，请显式指定 target")
    if dest.is_file():
        backup_save(dest, note=f"恢复前自动备份（来自 {d.name}）")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(sav, dest)
    return dest


def write_bytes_atomic(path: Path, data: bytes) -> None:
    """原子写文件（先写临时文件再替换），避免写一半损坏存档。"""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
