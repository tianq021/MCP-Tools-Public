"""Exercise the configured stdio transport with an isolated catalog and chat journal."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import ToolListChangedNotification

ROOT = Path(__file__).resolve().parents[1]


async def check(directory):
    mcp = directory / "mcp"
    shutil.copytree(ROOT / "mcp", mcp)
    journal = directory / "chat"
    journal.mkdir()
    msg = '{"seq":1,"text":"玩家"}\n'.encode()
    (journal / "inbox.20261121_123400.jsonl").write_bytes(msg)
    (journal / "inbox.jsonl").write_bytes(msg + b'{"seq":')
    cfg = directory / "bridge.json"
    cfg.write_text(json.dumps({"log_path": str(directory / "bridge" / "server.log")}))
    params = StdioServerParameters(command=sys.executable, args=["-B", str(mcp / "server.py")],
        env={**os.environ, "SR2_BRIDGE_CONFIG": str(cfg), "PYTHONUTF8": "1"})
    changed = asyncio.Event()

    async def notice(message):
        if isinstance(getattr(message, "root", None), ToolListChangedNotification):
            changed.set()

    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer, message_handler=notice) as client:
            hello = await client.initialize()
            assert hello.capabilities.tools.listChanged
            catalog = {t.name: t for t in (await client.list_tools()).tools}
            names = set(catalog)
            assert {"sr2_shop_buy", "sr2_landing_check", "sr2_chat_history",
                    "sr2_state", "sr2_time", "sr2_player", "sr2_plots",
                    "sr2_gordos", "sr2_pods", "sr2_doors", "sr2_corrals", "sr2_slime_diet",
                    "sr2_gadgets", "sr2_drones", "sr2_resource_nodes", "sr2_research_logs",
                    "sr2_teleporters", "sr2_pedia", "sr2_blueprints", "sr2_gadget_stock",
                    "sr2_gadget_recipe", "sr2_pinned_recipe", "sr2_upgrade_components",
                    "sr2_achievements", "sr2_player_upgrades", "sr2_map_zones",
                    "sr2_weather", "sr2_prisma_forecast", "sr2_ranch_controls"} <= names
            result = await client.call_tool("sr2_chat_history", {"cursor":len(msg)})
            data = json.loads(result.content[0].text)["data"]
            assert data["count"] == 1 and data["next_cursor"] == 2 * len(msg)
            result = await client.call_tool("sr2_shop_buy", {"shop_tag":"s","item_id":"x","dry_run":False})
            assert json.loads(result.content[0].text)["error"]["code"] == "REQUEST_ID_REQUIRED"
            for name, args in {
                "sr2_craft_gadget": {"gadget_id": "g"},
                "sr2_set_recipe_pin": {"action": "clear"},
                "sr2_set_feeder_speed": {"plot_id": "p", "speed": "fast"},
                "sr2_restore_health": {}, "sr2_restore_energy": {},
                "sr2_select_vacpack_slot": {"index": 0},
                "sr2_mark_return_point": {}, "sr2_return_to_mark": {},
                "sr2_dispense_feeder_food": {"control_id": "f"},
                "sr2_activate_plort_collector": {"control_id": "p"},
                "sr2_activate_resource_collector": {"control_id": "r"},
                "sr2_cycle_silo_port": {"control_id": "s"},
            }.items():
                assert catalog[name].inputSchema["properties"]["dry_run"]["default"] is True
                result = await client.call_tool(name, {**args, "dry_run": False})
                assert json.loads(result.content[0].text)["error"]["code"] == "REQUEST_ID_REQUIRED"
            path = mcp / "tools.json"
            spec = json.loads(path.read_text(encoding="utf-8"))
            spec["tools"].append({"name":"sr2_catalog_test","kind":"read","args":[]})
            path.write_text(json.dumps(spec), encoding="utf-8")
            await asyncio.wait_for(changed.wait(), 8)
            assert {t.name for t in (await client.list_tools()).tools} == names | {"sr2_catalog_test"}
            print(f"PASS {len(names)} tools, replay after rotation, purchase guard, live catalog reload")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        asyncio.run(check(Path(tmp)))

