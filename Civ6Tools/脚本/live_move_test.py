r"""Live ACTION test — the watershed: does a unit actually move?

Moves warrior idx=1 from (39,16) to (38,15) [grass+cattle, move cost 1, empty],
then re-reads the unit list to confirm the position really changed.

Run:  E:\Civ6\civ6-mcp\.venv\Scripts\python.exe E:\Civ6\脚本\live_move_test.py
"""

import asyncio
import os
import sys

os.environ.setdefault("PYTHONUTF8", "1")
sys.path.insert(0, r"E:\Civ6\civ6-mcp\src")

from civ_mcp.connection import GameConnection  # noqa: E402
from civ_mcp.game_state import GameState  # noqa: E402

UNIT_INDEX = 1
TARGET = (38, 15)


async def main() -> int:
    conn = GameConnection()
    await conn.connect()
    gs = GameState(conn)

    try:
        before = await gs.get_units()
        w = next((u for u in before if u.unit_index == UNIT_INDEX), None)
        if w is None:
            print(f"[FAIL] 找不到 idx={UNIT_INDEX} 的单位")
            return 1
        print(f"移动前: idx={UNIT_INDEX} {w.name} at ({w.x},{w.y}) moves={w.moves_remaining}")
        ox, oy = w.x, w.y

        print(f"\n下达指令: move unit idx={UNIT_INDEX} -> {TARGET}")
        result = await gs.move_unit(UNIT_INDEX, TARGET[0], TARGET[1])
        print("move_unit 返回:", result)

        # Give the engine a moment (moves resolve asynchronously in InGame)
        await asyncio.sleep(1.5)

        after = await gs.get_units()
        w2 = next((u for u in after if u.unit_index == UNIT_INDEX), None)
        if w2 is None:
            print("[FAIL] 移动后找不到该单位（可能被销毁）")
            return 1
        print(f"移动后: idx={UNIT_INDEX} {w2.name} at ({w2.x},{w2.y}) moves={w2.moves_remaining}")
        print(f"消耗移动力: {w.moves_remaining} -> {w2.moves_remaining}")

        moved = (w2.x, w2.y) != (ox, oy)
        print()
        print(f"[{'PASS' if moved else 'FAIL'}] 单位位置改变 — ({ox},{oy}) -> ({w2.x},{w2.y})")
        if moved and (w2.x, w2.y) == TARGET:
            print(f"[PASS] 精确到达目标格 {TARGET}")
        elif moved:
            print(f"[WARN] 移动了但不在目标格，实际 {w2.x},{w2.y}")
        return 0 if moved else 1
    finally:
        await conn.disconnect()


sys.exit(asyncio.run(main()))
