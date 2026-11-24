r"""Live read-only state test: overview, cities, units.

Step 1 of the operation proof chain. Changes nothing in the game.

Run:  E:\Civ6\civ6-mcp\.venv\Scripts\python.exe E:\Civ6\脚本\live_state_test.py
"""

import asyncio
import os
import sys

os.environ.setdefault("PYTHONUTF8", "1")
sys.path.insert(0, r"E:\Civ6\civ6-mcp\src")

from civ_mcp.connection import GameConnection  # noqa: E402
from civ_mcp.game_state import GameState  # noqa: E402
from civ_mcp.narrate import narrate_overview, narrate_units  # noqa: E402


async def main() -> int:
    conn = GameConnection()
    await conn.connect()
    gs = GameState(conn)

    try:
        print("=== 1) get_game_overview ===")
        try:
            ov = await gs.get_game_overview()
            print(narrate_overview(ov))
        except Exception as e:  # noqa: BLE001
            print(f"FAILED: {type(e).__name__}: {e}")
            return 1

        print("\n=== 2) get_units ===")
        try:
            units = await gs.get_units()
            print(f"单位数: {len(units)}")
            print(narrate_units(units, None, None))
        except Exception as e:  # noqa: BLE001
            print(f"FAILED: {type(e).__name__}: {e}")
            return 1

        print("\n=== 3) 可用于指令的单位（id/index/x/y/移动力）===")
        for u in units:
            print(
                f"  id={u.unit_id} idx={u.unit_index} {u.name:<16} "
                f"({u.x},{u.y}) moves={u.moves_remaining}/{u.max_moves} "
                f"hp={u.health}/{u.max_health} targets={u.targets or '-'}"
            )
        return 0
    finally:
        await conn.disconnect()


sys.exit(asyncio.run(main()))
