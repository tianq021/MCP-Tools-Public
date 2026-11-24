# -*- coding: utf-8 -*-
"""用 dnfile 导出 Slime Rancher 1 (Mono) Assembly-CSharp.dll 的类型/方法/字段清单。

只读脚本，不修改游戏文件。用途：阶段 0 逆向确认，验证目标类与方法是否存在。
用法:
    python dump_sr1_symbols.py                # 摘要 + 关键字命中
    python dump_sr1_symbols.py --all          # 导出全部类型清单到 out/sr1_types.txt
    python dump_sr1_symbols.py --type Corral  # 打印某类型的方法/字段
"""
import argparse
import io
import sys
from pathlib import Path

import dnfile

DLL = Path(
    r"F:\SteamLibrary\steamapps\common\Slime Rancher"
    r"\SlimeRancher_Data\Managed\Assembly-CSharp.dll"
)
OUT = Path(__file__).resolve().parent.parent / "out"

KEYWORDS = [
    "PlayerState", "PlortMarket", "Currency", "Exchange", "Corral", "Feeder",
    "LandPlot", "Shop", "Purchase", "GoldSlime", "Vacpack", "Achievement",
    "SceneContext", "Ledger", "Market", "Inventory", "Plot", "Gate", "Ranch",
    "AutoFeeder", "Silo", "Vac", "Spawn", "Save", "Money",
]


def s(x):
    return str(x) if x is not None else ""


def type_fullname(t):
    ns = s(t.TypeNamespace)
    name = s(t.TypeName)
    return f"{ns}.{name}" if ns else name


def iter_methods(t):
    for idx in t.MethodList:
        r = getattr(idx, "row", None)
        if r is not None:
            yield r


def iter_fields(t):
    for idx in t.FieldList:
        r = getattr(idx, "row", None)
        if r is not None:
            yield r


def row_name(r):
    for attr in ("Name", "TypeName"):
        v = getattr(r, attr, None)
        if v is not None:
            return s(v)
    return "?"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="导出全部类型清单")
    ap.add_argument("--type", default="", help="只打印该类型（子串匹配）")
    args = ap.parse_args()

    if not DLL.exists():
        sys.exit(f"找不到 Assembly-CSharp.dll: {DLL}")

    pe = dnfile.dnPE(str(DLL))
    if pe.net is None:
        sys.exit("不是 .NET 程序集")

    tables = pe.net.mdtables
    types = list(tables.TypeDef)
    methods = list(tables.MethodDef)
    fields = list(tables.Field)

    print(f"dll        : {DLL}")
    print(f"size       : {DLL.stat().st_size:,} bytes")
    print(f"TypeDef    : {len(types)}")
    print(f"MethodDef  : {len(methods)}")
    print(f"Field      : {len(fields)}")
    print(f"AssemblyRef: {len(tables.AssemblyRef)}")
    refs = []
    for r in tables.AssemblyRef:
        row = getattr(r, "row", r)
        refs.append(row_name(row))
    print("refs       :", ", ".join(refs))

    print("\n=== 关键字命中（类型） ===")
    for kw in KEYWORDS:
        hits = [type_fullname(t) for t in types
                if kw.lower() in s(t.TypeName).lower()]
        if hits:
            print(f"[{kw}] {len(hits)}")
            for h in hits[:25]:
                print(f"    {h}")

    print("\n=== 关键字命中（方法名） ===")
    for kw in KEYWORDS:
        hits = []
        for t in types:
            for m in iter_methods(t):
                if kw.lower() in s(m.Name).lower():
                    hits.append(f"{type_fullname(t)}::{s(m.Name)}")
        if hits:
            print(f"[{kw}] {len(hits)}")
            for h in hits[:25]:
                print(f"    {h}")

    if args.type:
        print(f"\n=== 类型详情: *{args.type}* ===")
        for t in types:
            if args.type.lower() in s(t.TypeName).lower():
                print(f"\n-- {type_fullname(t)}  (flags=0x{t.Flags:08X})")
                fs = list(iter_fields(t))
                ms = list(iter_methods(t))
                print(f"   fields({len(fs)}):", ", ".join(row_name(f) for f in fs[:40]))
                print(f"   methods({len(ms)}):")
                for m in ms[:80]:
                    print(f"     {row_name(m)}")

    if args.all:
        OUT.mkdir(parents=True, exist_ok=True)
        dst = OUT / "sr1_types.txt"
        buf = io.StringIO()
        for t in types:
            buf.write(f"\n### {type_fullname(t)}\n")
            for f in iter_fields(t):
                buf.write(f"  F {row_name(f)}\n")
            for m in iter_methods(t):
                buf.write(f"  M {row_name(m)}\n")
        dst.write_text(buf.getvalue(), encoding="utf-8")
        print(f"\n已导出: {dst}")


if __name__ == "__main__":
    main()
