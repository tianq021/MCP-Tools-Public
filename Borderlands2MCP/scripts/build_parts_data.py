"""从 Gibbed Borderlands2Dumps 生成离线部件/资产库数据。

数据来源（一手）：
  https://github.com/gibbed/Borderlands2Dumps
本脚本只读取本地下载的 dumps 目录，输出 `data/parts/*.json`，不接触游戏文件。

用法：
  python scripts\\build_parts_data.py --dumps .local\\tmp\\dumps
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "parts"

SOURCE_FILES = [
    "Asset Library Manager.json",
    "Weapon Parts.json",
    "Item Parts.json",
    "Weapon Name Parts.json",
    "Item Name Parts.json",
    "Weapon Types.json",
    "Weapon Part Lists.json",
    "Item Part Lists.json",
    "Item Balance.json",
    "Weapon Balance.json",
    "Items.json",
]

GIBBED_DUMPS_URL = "https://github.com/gibbed/Borderlands2Dumps"
SKIP_KEYS = {"description", "type"}


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _trim_library(mgr: dict) -> dict:
    """只保留解析 lib:asset 需要的字段（sublibrary_bits/asset_bits/package/assets）。"""
    configs = {
        group: {
            "sublibrary_bits": cfg["sublibrary_bits"],
            "asset_bits": cfg["asset_bits"],
            "type": cfg.get("type", ""),
        }
        for group, cfg in mgr["configs"].items()
    }
    sets = []
    for s in mgr["sets"]:
        libs = {}
        for group, lib in (s.get("libraries") or {}).items():
            subs = []
            for sub in lib.get("sublibraries") or []:
                subs.append({"package": sub.get("package", ""), "assets": sub.get("assets") or []})
            libs[group] = {"sublibraries": subs}
        sets.append({"id": s["id"], "libraries": libs})
    return {"version": mgr.get("version"), "configs": configs, "sets": sets}


def _categorize_item_balance(ib: dict) -> Counter:
    def cat(v: dict) -> str:
        p = (v.get("item") or "").lower()
        for key, c in (
            ("shield", "shield"),
            ("grenademod", "grenade"),
            ("classmod", "classmod"),
            ("artifact", "relic"),
            ("_weap", "weapon"),
            ("weapons", "weapon"),
        ):
            if key in p:
                return c
        return "other"

    return Counter(cat(v) for v in ib.values() if isinstance(v, dict))


def _resolve_dump(dumps: Path, name: str) -> Path:
    for candidate in (name, name.replace(" ", "_")):
        path = dumps / candidate
        if path.is_file():
            return path
    raise FileNotFoundError(f"缺少 dump 文件: {dumps / name}")


def build(dumps: Path) -> dict:
    files = {name: _resolve_dump(dumps, name) for name in SOURCE_FILES}

    mgr = _load(files["Asset Library Manager.json"])
    wp = _load(files["Weapon Parts.json"])
    ip = _load(files["Item Parts.json"])
    wnp = _load(files["Weapon Name Parts.json"])
    inp = _load(files["Item Name Parts.json"])
    wtypes = _load(files["Weapon Types.json"])
    wpl = _load(files["Weapon Part Lists.json"])
    ipl = _load(files["Item Part Lists.json"])
    ib = _load(files["Item Balance.json"])
    wb = _load(files["Weapon Balance.json"])
    items = _load(files["Items.json"])

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    lib_trim = _trim_library(mgr)
    (OUT_DIR / "asset_library.json").write_text(
        json.dumps(lib_trim, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )

    def meta(parts: dict) -> dict:
        out = {}
        for path, v in parts.items():
            item = {k: val for k, val in v.items() if k not in SKIP_KEYS}
            item["type"] = v.get("type", "")
            out[path] = item
        return out

    name_parts = {}
    for path, v in wnp.items():
        name_parts[path] = {"name": v.get("name", ""), "unique": bool(v.get("unique"))}
    for path, v in inp.items():
        name_parts[path] = {"name": v.get("name", ""), "unique": False}

    part_meta = {
        "weapon_parts": meta(wp),
        "item_parts": meta(ip),
        "name_parts": name_parts,
        "weapon_types": wtypes,
        "weapon_part_lists": wpl,
        "item_part_lists": ipl,
    }
    (OUT_DIR / "part_meta.json").write_text(
        json.dumps(part_meta, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )

    totals = {
        "source": GIBBED_DUMPS_URL,
        "asset_library_sets": sorted(s["id"] for s in mgr["sets"]),
        "asset_library_version": mgr.get("version"),
        "weapon_type_defs": len(wtypes),
        "weapon_part_defs": len(wp),
        "item_part_defs": len(ip),
        "weapon_name_part_defs": len(wnp),
        "item_name_part_defs": len(inp),
        "weapon_balance_defs": len(wb),
        "weapon_balance_unique_bases": len({v.get("base") for v in wb.values() if isinstance(v, dict)}),
        "item_balance_defs": len(ib),
        "item_defs": len(items),
        "weapon_part_assets_set0": sum(len(x.get("assets") or []) for x in lib_trim["sets"][0]["libraries"]["WeaponParts"]["sublibraries"]),
        "item_part_assets_set0": sum(len(x.get("assets") or []) for x in lib_trim["sets"][0]["libraries"]["ItemParts"]["sublibraries"]),
        "weapon_type_assets_set0": sum(len(x.get("assets") or []) for x in lib_trim["sets"][0]["libraries"]["WeaponTypes"]["sublibraries"]),
        "item_type_assets_set0": sum(len(x.get("assets") or []) for x in lib_trim["sets"][0]["libraries"]["ItemTypes"]["sublibraries"]),
        "item_balance_by_category": dict(_categorize_item_balance(ib)),
        "source_sha256": {},
    }
    for name in SOURCE_FILES:
        totals["source_sha256"][name] = hashlib.sha256(files[name].read_bytes()).hexdigest()
    (OUT_DIR / "game_totals.json").write_text(
        json.dumps(totals, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return totals


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dumps", default=str(ROOT / ".local" / "tmp" / "dumps"))
    args = ap.parse_args()
    totals = build(Path(args.dumps))
    print(json.dumps(totals, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
