r"""Live test of get_civ_abilities against a running Civ 6.

Precondition: Civ 6 is running WITH a game loaded (not the main menu).
The InGame Lua state only exists once a save/新游戏 is actually in progress.

Shows raw pipe-delimited lines (so DIAG counters are visible) plus the
narrated text the model would receive.

Run:  E:\Civ6\civ6-mcp\.venv\Scripts\python.exe E:\Civ6\脚本\live_civ_test.py
"""

import asyncio
import os
import sys

os.environ.setdefault("PYTHONUTF8", "1")
sys.path.insert(0, r"E:\Civ6\civ6-mcp\src")

from civ_mcp import lua as lq  # noqa: E402
from civ_mcp.connection import GameConnection  # noqa: E402
from civ_mcp.narrate import narrate_civ_abilities  # noqa: E402


async def main() -> int:
    conn = GameConnection()
    print("1) 连接 127.0.0.1:4318 ...")
    await conn.connect()
    print(f"   OK — {len(conn.lua_states)} 个 Lua 状态")
    print(f"   GameCore={conn.gamecore_index}  InGame={conn.ingame_index}")

    if conn.ingame_index is None:
        print("\n[BLOCKED] 没有 InGame 状态 → 游戏在主菜单，未载入存档。")
        print("           请进入游戏（载入存档或开新局）后重跑本脚本。")
        await conn.disconnect()
        return 2

    try:
        print("\n2) 原生查询 get_civ_abilities ...")
        lines = await conn.execute_write(lq.build_civ_abilities_query())
        print(f"   原始输出 {len(lines)} 行:")
        for ln in lines:
            print("     " + ln)

        print("\n3) 解析 + 播报 ...")
        ab = lq.parse_civ_abilities_response(lines)
        print()
        print(narrate_civ_abilities(ab))

        print("\n4) 关键指标判定")
        d = ab.diagnostics
        verdict = []
        verdict.append(("特性表命中", d.get("TRAITS", 0) > 0, f"TRAITS={d.get('TRAITS', 0)}"))
        verdict.append(("特性含描述文本", any(t.description for t in ab.traits),
                        f"{sum(1 for t in ab.traits if t.description)}/{len(ab.traits)}"))
        verdict.append(("Units.TraitType 可用", d.get("UNITS_WITH_TRAITTYPE", 0) > 0,
                        f"{d.get('UNITS_WITH_TRAITTYPE', 0)}"))
        verdict.append(("独特套件命中", len(ab.uniques) > 0, f"{len(ab.uniques)} 项"))
        for label, ok, detail in verdict:
            print(f"   [{'PASS' if ok else 'FAIL'}] {label} — {detail}")
        return 0 if all(ok for _, ok, _ in verdict) else 1
    finally:
        await conn.disconnect()


sys.exit(asyncio.run(main()))
