"""Back up Civ6 state and disable only the mod identified in the 2026-11-07 dump."""
import json
from pathlib import Path
import shutil
import sqlite3
from datetime import datetime

ROOT = Path(__file__).resolve().parent
CIV = Path(r"C:\Users\hp\AppData\Local\Firaxis Games\Sid Meier's Civilization VI")
DB = CIV / "Mods.sqlite"
MOD_ID = "202b3c03-181f-4f52-ba68-de0f21ead438"
REPORT = ROOT / "diagnostics" / ("20261107-load-crash-" + datetime.now().strftime("%H%M%S"))
REPORT.mkdir(parents=True, exist_ok=False)

with sqlite3.connect(DB.as_uri() + "?mode=ro", uri=True) as source:
    before = source.execute("SELECT * FROM ModGroupItems ORDER BY ModGroupRowId,ModRowId").fetchall()
    targets = source.execute("""
        SELECT i.ModGroupRowId,i.ModRowId,i.Disabled
        FROM ModGroupItems i JOIN Mods m USING(ModRowId)
        JOIN ModGroups g USING(ModGroupRowId)
        WHERE lower(m.ModId)=? AND g.Selected=1
    """, (MOD_ID,)).fetchall()
    assert targets == [(1, 989, 0)], f"Target state changed; no edit: {targets}"
    with sqlite3.connect(REPORT / "Mods.before.sqlite") as backup:
        source.backup(backup)

dump = Path(r"H:\SteamLibrary\steamapps\common\Sid Meier's Civilization VI\Base\Binaries\Win64Steam\CivilizationVI_DX12.exe.dmp")
shutil.copy2(dump, REPORT / dump.name)
for filename in ("Modding.log", "Database.log", "Renderer.log", "LoadGameViewState.log", "Lua.log"):
    shutil.copy2(CIV / "Logs" / filename, REPORT / filename)

with sqlite3.connect(DB.as_uri() + "?mode=rw", uri=True, timeout=3) as database:
    database.execute("BEGIN IMMEDIATE")
    current = database.execute("SELECT * FROM ModGroupItems ORDER BY ModGroupRowId,ModRowId").fetchall()
    assert current == before, "Mod selection changed during backup; no edit"
    changed = database.execute("""
        UPDATE ModGroupItems SET Disabled=1
        WHERE ModGroupRowId=1 AND ModRowId=989 AND Disabled=0
        AND EXISTS (SELECT 1 FROM Mods WHERE ModRowId=989 AND lower(ModId)=?)
    """, (MOD_ID,)).rowcount
    assert changed == 1, changed
    after = database.execute("SELECT * FROM ModGroupItems ORDER BY ModGroupRowId,ModRowId").fetchall()
    expected = [(g, m, 1 if (g, m) == (1, 989) else d) for g, m, d in before]
    assert after == expected, "Unexpected change outside target mod"
    assert database.execute("PRAGMA quick_check").fetchone()[0] == "ok"

result = {
    "mod": "Districts Dont Remove Yields / 区域不移除产出",
    "mod_id": MOD_ID, "workshop_id": "3258262281",
    "fault_module": "GameCore_XP2_Loader_FinalRelease.dll",
    "fault_offset": "0x4f73", "exception": "0xc0000005", "write_address": "0x0",
    "disabled_before": 0, "disabled_after": 1, "changed_rows": 1,
    "other_mod_selections_unchanged": True, "database_check": "ok",
    "game_retest_completed": False,
    "backup": str(REPORT / "Mods.before.sqlite"),
    "restore": "作者更新兼容版本后，在游戏的额外内容中重新启用此模组。",
}
(REPORT / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False, indent=2))
