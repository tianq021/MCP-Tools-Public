"""备份《史莱姆牧场 2》存档文件（纯文件操作，不需要游戏在跑）。

桥接里的 sr2_backup 需要新版 DLL 部署后才能用；本脚本不依赖游戏进程，
任何时候都能跑，用来在批量写操作前留一份可回滚的存档。

用法：
    python -B tool/backup_saves.py                 # 备份（默认）
    python -B tool/backup_saves.py --label before-buy
    python -B tool/backup_saves.py --list          # 只列出当前存档，不复制
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "backups" / "saves"


def save_dirs() -> list[Path]:
    """按 app.info 推导 + 两条兜底候选，取所有存在的目录。"""
    local_low = Path.home() / "AppData" / "LocalLow"
    candidates: list[Path] = []
    for company, product in (("MonomiPark", "SlimeRancher2"), ("Monomi Park", "Slime Rancher 2")):
        base = local_low / company / product
        info = base / "SlimeRancher2_Data" / "app.info"
        if info.exists():
            try:
                lines = [line.strip() for line in info.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
            except OSError:
                lines = []
            # app.info 两行分别是 company / product
            if len(lines) >= 2:
                derived = local_low / lines[0] / lines[1]
                if derived.exists() and derived not in candidates:
                    candidates.append(derived)
        if base.exists() and base not in candidates:
            candidates.append(base)
    return candidates


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def main() -> int:
    ap = argparse.ArgumentParser(description="备份史莱姆牧场 2 存档文件")
    ap.add_argument("--label", default="", help="备份目录后缀标签，例如 before-buy")
    ap.add_argument("--list", action="store_true", help="只列出现有存档，不复制")
    args = ap.parse_args()

    directories = save_dirs()
    if not directories:
        print("没找到存档目录（本机可能还没开过档）。")
        return 2

    total = 0
    for directory in directories:
        saves = sorted(directory.rglob("*.sav"), key=lambda p: p.stat().st_mtime, reverse=True)
        print(f"存档目录 {directory}")
        if not saves:
            print("  （这里没有 .sav）")
            continue
        for path in saves:
            stat = path.stat()
            print(f"  {path.name}  {stat.st_size} B  {datetime.fromtimestamp(stat.st_mtime):%Y-%m-%d %H:%M:%S}")
        total += len(saves)
        if args.list:
            continue

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = TARGET / (stamp + (f"_{args.label}" if args.label else ""))
        target.mkdir(parents=True, exist_ok=True)
        for path in saves:
            destination = target / path.name
            shutil.copy2(path, destination)
            print(f"    ✔ {path.name} → {sha256(destination)[:16]}…")
        print(f"  备份到 {target}")

    if not total:
        print("没有可备份的 .sav。")
        return 2
    if not args.list:
        print(f"\n共 {total} 个存档文件已备份。")
    return 0


if __name__ == "__main__":
    # Windows 控制台默认 GBK，直接 print ✔/中文会抛 UnicodeEncodeError 中断备份。
    if not sys.stdout.isatty():
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
