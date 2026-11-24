"""Run the one-command player workflows against an isolated loopback bridge."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Thread


def main():
    calls = []
    brine = "IdentifiableType.DeepBrineCraft"
    brine_row = {"id": brine, "name": "DeepBrineCraft", "count": 3}
    cost = {"kind": "material", "id": brine, "stock": 3, "required": 8, "missing": 5}
    pump = {"id": "Gadget.SimplePump", "name": "SimplePump", "title": "简易水泵", "owned": True}
    other = {"id": "Gadget.AdvancedPump", "name": "AdvancedPump", "title": "高级水泵", "owned": True}
    def blueprints(request):
        return {"items": [other], "next_offset": 1} if request["args"]["offset"] == 0 else {"items": [pump], "next_offset": None}

    def craft(request):
        data = {"costs_before": [dict(cost, stock=9, missing=0)], "stock_before": 1, "can_craft": True, "applied": False}
        if not request["args"]["dry_run"]:
            data.update(applied=True, stock_after=3, stock_delta=2)
        return data

    fixtures = {
        "sr2_state": {"available": True, "phase": "gameplay", "scene_group": "测试区域"},
        "sr2_time": {"available": True, "day": 3, "hour": 12.75, "paused": False},
        "sr2_player": {"available": True, "position": {"x": 1.2, "y": 3, "z": 4}, "zone": None,
                       "health": 25, "max_health": 100, "energy": 90, "max_energy": 100, "coins": 43},
        "sr2_vacpack": {"available": True, "slots": [
            {"index": 1, "name": "胡萝卜", "id": "carrot", "unlocked": True, "count": 5},
            {"index": 2, "name": "", "id": None, "unlocked": True, "count": 0}]},
        "sr2_ranch_controls": {"matched_count": 1, "items": [{"control_id": "feeder:p1"}]},
        "sr2_dispense_feeder_food": {"applied": True, "food_id": "carrot", "food_before": 8, "food_after": 7},
        "sr2_activate_plort_collector": {"applied": True},
        "sr2_activate_resource_collector": {"applied": True},
        "sr2_cycle_silo_port": {"applied": True, "slot_before": 0, "slot_after": 1},
        "sr2_set_feeder_speed": {"applied": True, "already_set": False, "speed_before": "NORMAL", "speed_after": "FAST"},
        "sr2_collect": {"plan_count": 2, "found_total": 3, "vacpack_delta": 2},
        "sr2_set_recipe_pin": {"applied": True, "already_set": False},
        "sr2_save": {"save_name": "test-save", "saved": True, "newest_after": "test-save.sav @ 12:00:01"},
        "sr2_restore_health": {"already_full": False, "before": 25, "target": 55, "maximum": 100},
        "sr2_restore_energy": {"already_full": False, "before": 90, "target": 100, "maximum": 100, "after": 100},
        "sr2_select_vacpack_slot": {"already_selected": False, "before": 0, "index": 1, "after": 1,
                                    "item_id": "carrot", "count": 5},
        "sr2_chat_send": {"delivered": True},
        "sr2_storage_list": {"items": [{"kind": "plot_silo", "index": 2, "slots": [dict(brine_row, count=4, index=5)]}]},
        "sr2_refinery": {"items": [brine_row, brine_row]},
        "sr2_nearby": {"available": True, "groups": [
            {"id": brine, "name": "DeepBrineCraft", "category": "craft", "count": 2, "nearest": 5.0}]},
        "sr2_resource_nodes": {"items": [
            {"state": "READY", "loaded": True, "id": "node-1", "position": {"x": 5.2, "y": 3, "z": 4},
             "resources_to_spawn": [brine]}], "next_offset": None},
        "sr2_blueprints": blueprints, "sr2_craft_gadget": craft,
        "sr2_gadget_recipe": {"blueprint_owned": True, "costs": [cost], "costs_covered": False},
    }

    class FakeBridge(BaseHTTPRequestHandler):
        def do_POST(self):
            assert self.path == "/rpc" and self.headers["X-Bridge-Token"] == "fixture"
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(request)
            self.send_response(200)
            self.end_headers()
            value = fixtures[request["tool"]]
            self.wfile.write(json.dumps({"ok": True, "data": value(request) if callable(value) else value}).encode())

        def log_message(self, *args):
            pass

    with HTTPServer(("127.0.0.1", 0), FakeBridge) as server, tempfile.TemporaryDirectory() as tmp:
        Thread(target=server.serve_forever, daemon=True).start()
        config = Path(tmp) / "fixture.json"
        config.write_text(json.dumps({"port": server.server_port, "token": "fixture"}))

        def run(*args, code=0):
            calls.clear()
            result = subprocess.run([sys.executable, "-B", str(Path(__file__).with_name("ranch.py")), *args],
                                    env={**os.environ, "SR2_BRIDGE_CONFIG": str(config)}, cwd=tmp,
                                    capture_output=True, encoding="utf-8", timeout=10)
            assert result.returncode == code, (args, result.stdout, result.stderr)
            return result.stdout.strip()

        try:
            assert run("clock").startswith("北京时间") and not calls
            assert "12:45" in run("time") and len(calls) == 1
            assert "1号 胡萝卜×5" in run("bag") and len(calls) == 1
            summary = run("status", "--reply")
            assert len(summary.splitlines()) == 1 and all(s in summary for s in ("12:45", "生命 25/100", "胡萝卜×5"))
            assert [r["tool"] for r in calls] == ["sr2_state", "sr2_player", "sr2_time", "sr2_vacpack", "sr2_chat_send"]
            assert calls[-1]["args"]["text"] == summary
            fixtures["sr2_state"]["phase"] = "main_menu"
            assert "主菜单" in run("status") and len(calls) == 1
            assert run("heal", "30").startswith("预演") and calls[0]["args"] == {"points": 30, "dry_run": True}
            assert "90 → 100" in run("energy", "--apply", "energy-1") and calls[0]["args"]["points"] == 0
            assert "0 号槽 → 1 号槽" in run("slot", "1", "--apply", "slot-1")
            assert not run("feed", "--apply", "message-12", "--reply").startswith("预演")
            assert calls[1] == {"tool": "sr2_dispense_feeder_food", "args": {"control_id": "feeder:p1", "dry_run": False},
                                "request_id": "message-12"}
            first_reply_id = calls[2]["request_id"]
            run("feed", "feeder:p1", "--apply", "message-12", "--reply")
            assert calls[0]["request_id"] == "message-12" and calls[1]["request_id"] == first_reply_id
            fixtures["sr2_ranch_controls"] = {"matched_count": 2, "items": [{"control_id": "one"}, {"control_id": "two"}]}
            assert "请指定" in run("feed", "--apply", "other", "--reply", code=1)
            assert [r["tool"] for r in calls] == ["sr2_ranch_controls", "sr2_chat_send"]
            fixtures["sr2_dispense_feeder_food"] = {"reason": "no_food", "applied": False}
            assert "no_food" in run("feed", "feeder:p1", "--apply", "empty", code=1)
            # Explicit targets go straight to the action. Auto-selection requires exactly one device.
            assert "请指定" in run("start-plort-collector", "--apply", "ambiguous-collector", code=1)
            assert len(calls) == 1 and calls[0]["args"]["kind"] == "plort_collector"
            for command, tool, control in (
                ("start-plort-collector", "sr2_activate_plort_collector", "plort_collector:p1"),
                ("start-resource-collector", "sr2_activate_resource_collector", "resource_collector:17"),
                ("silo-next", "sr2_cycle_silo_port", "silo_port:p3:0"),
            ):
                assert run(command, control).startswith("预演")
                assert calls == [{"tool": tool, "args": {"control_id": control, "dry_run": True}}]
                assert not run(command, control, "--apply", command).startswith("预演")
                assert calls == [{"tool": tool, "args": {"control_id": control, "dry_run": False}, "request_id": command}]
            fixtures["sr2_ranch_controls"] = {"matched_count": 1, "items": [{"control_id": "feeder:p1", "owner_id": "p1"}]}
            assert "普通档调到快档" in run("feeder-speed", "快", "--apply", "speed-1")
            assert calls[1] == {"tool": "sr2_set_feeder_speed",
                                "args": {"plot_id": "p1", "speed": "fast", "dry_run": False}, "request_id": "speed-1"}
            assert run("feeder-speed", "slow", "p1").startswith("预演")
            assert len(calls) == 1 and calls[0]["args"]["speed"] == "slow"
            assert "计划收取 2 件" in run("collect", "盐水", "2", "--radius", "12")
            assert calls == [{"tool": "sr2_collect",
                              "args": {"item": "DeepBrineCraft", "radius": "12", "limit": "2", "dry_run": True}}]
            assert "2 件到背包" in run("collect", "盐水", "2", "--apply", "collect-1")
            assert calls[0]["request_id"] == "collect-1" and not calls[0]["args"]["dry_run"]
            fixtures["sr2_collect"]["vacpack_delta"] = 1
            assert "背包增加 1 件" in run("collect", "盐水", "2", "--apply", "collect-partial", code=1)
            assert run("pin-recipe", "简易水泵").startswith("预演")
            assert calls[-1]["args"] == {"action": "pin", "gadget_id": pump["id"], "dry_run": True}
            assert "已追踪" in run("pin-recipe", "简易水泵", "--apply", "pin-1")
            assert calls[-1]["request_id"] == "pin-1"
            assert "已取消配方追踪" in run("clear-recipe", "--apply", "clear-1")
            assert calls == [{"tool": "sr2_set_recipe_pin", "args": {"action": "clear", "dry_run": False},
                              "request_id": "clear-1"}]
            assert run("save").startswith("预演")
            assert calls == [{"tool": "sr2_save", "args": {"dry_run": True}}]
            assert "test-save.sav" in run("save", "--apply", "save-1", "--reply")
            assert calls[0] == {"tool": "sr2_save", "args": {"dry_run": False}, "request_id": "save-1"}
            fixtures["sr2_save"] = {"saved": False, "reason": "newest_file_unchanged"}
            assert "保存未确认" in run("save", "--apply", "save-unconfirmed", code=1)
            run("slot", "-1", code=2)
            assert not calls
            fixtures["sr2_vacpack"]["slots"].append(dict(brine_row, count=1, index=3, unlocked=True))
            result = run("find", "盐水", "--reply")
            assert all(x in result for x in ("合计 8", "背包3格×1", "筒仓2的5格×4", "精炼炉×3",
                                             "地表可直接吸：附近 2、本区域料点 1"))
            assert [r["tool"] for r in calls] == ["sr2_vacpack", "sr2_storage_list", "sr2_refinery",
                                                  "sr2_nearby", "sr2_player", "sr2_resource_nodes", "sr2_chat_send"]
            result = run("materials")
            assert all(x in result for x in ("60 米内可吸", "DeepBrineCraft×2（最近 5.0 米）",
                                             "READY 料点", "DeepBrineCraft×1（最近 4.0 米）"))
            assert "没有找到" in run("materials", "水晶")
            fixtures["sr2_refinery"]["items"] = [dict(brine_row, count=None)]
            assert "精炼炉数量未知" in run("find", brine)
            fixtures["sr2_refinery"] = {"available": False, "reason": "refinery_unavailable"}
            result = run("find", "deepbrine")
            assert "已核实 5" in result and "未能核对" in result
            result = run("recipe", "简易水泵", "2")
            assert all(x in result for x in ("简易水泵×2", "DeepBrineCraft 3/8", "尚缺：DeepBrineCraft×5",
                                             "筒仓2的5格", "地表可直接吸：DeepBrineCraft（附近 2，本区域料点 1）"))
            assert [r["args"]["offset"] for r in calls if r["tool"] == "sr2_blueprints"] == [0, 1]
            assert calls[2]["args"] == {"gadget_id": pump["id"], "count": 2}
            assert "预演" in run("craft", "SimplePump", "2") and calls[-1]["args"]["dry_run"] is True
            result = run("craft", "简易水泵", "2", "--apply", "make-two", "--reply")
            assert "成品库存 1 → 3" in result and calls[-1]["args"]["text"] == result
            assert calls[-2] == {"tool": "sr2_craft_gadget", "args": {"gadget_id": pump["id"], "count": 2, "dry_run": False},
                                 "request_id": "make-two"}
            run("craft", "水泵", "--apply", "ambiguous", code=1)
            assert all(r["tool"] == "sr2_blueprints" for r in calls)
            fixtures["sr2_craft_gadget"] = {"reason": "INSUFFICIENT_COST_ITEMS", "costs_before": [cost]}
            assert "材料不足" in run("craft", pump["id"], "--apply", "short", code=1)
            run("craft", "Pump", "100", code=2)
            assert not calls
            run("find", " ", code=2)
            assert not calls
            market = fixtures["sr2_market"] = {"market_closed": False, "prices": [
                {"id": "IdentifiableType.CottonPlort", "name": "CottonPlort", "price": 23, "change": -2},
                {"id": "IdentifiableType.RockPlort", "name": "RockPlort", "price": None, "change": None}]}
            result = run("quote", "棉花结晶", "20", "--reply")
            assert all(s in result for s in ("棉花结晶", "每颗 23", "-2", "估算 460"))
            assert [r["tool"] for r in calls] == ["sr2_market", "sr2_chat_send"]
            market["market_closed"] = True
            assert "休市" in run("quote", "棉花") and "暂无报价" in run("quote", "岩石")
            run("quote", "Plort", code=1)
            run("quote", "CottonPlort", "0", code=2)
            assert not calls
            main_key = "plot:plot0031824558/58d5bd4fc903e1c49aba61495aa74014"
            fixtures["sr2_storage_list"] = {"items": [
                {"index": 2, "kind": "plot_silo", "storage_key": main_key, "selector": main_key, "total_items": 4,
                 "writable": True, "slots": [dict(brine_row, count=4, index=5, unlocked=True),
                 {"index": 6, "count": 0, "unlocked": True}, {"index": 7, "count": 0, "unlocked": False}]},
                {"index": 1, "kind": "scene_storage", "selector": "scene:123", "total_items": 0,
                 "writable": False, "slots": []}]}
            assert "scene:123" in run("stores") and "空 1 格" in run("stores", "main")
            assert "5格 DeepBrineCraft×4" in run("stores", main_key)
            assert "容量未知" in run("stores", "scene:123")
            assert "没有找到" in run("stores", "scene:456", code=1)
            fixtures["sr2_consolidate_storage"] = {"checked_silos": 3, "skipped_silos": ["plot:unavailable"],
                "planned_items": 4, "planned_freed_slots": 2, "moved_items": 4, "freed_slots": 2}
            assert "可归堆 4 件，腾出 2 格" in run("consolidate")
            assert calls == [{"tool": "sr2_consolidate_storage", "args": {"storage": "", "dry_run": True}}]
            assert "已归堆 4 件，腾出 2 格" in run("consolidate", "--storage", "main", "--apply", "organize-main")
            assert calls == [{"tool": "sr2_consolidate_storage", "args": {"storage": main_key, "dry_run": False},
                              "request_id": "organize-main"}]
            fixtures["sr2_consolidate_storage"].update(moved_items=1, freed_slots=0, failure={"reason": "debit_failed"})
            assert "已归堆 1/4 件" in run("consolidate", "--apply", "organize-partial", code=1)
            fixtures["sr2_move_amount"] = {"planned": 5, "moved": 0}
            assert "预演" in run("store", "虎斑结晶", "5")
            assert calls == [{"tool": "sr2_move_amount", "args": {"direction": "store", "item": "TabbyPlort",
                             "quantity": 5, "storage": main_key, "dry_run": True}}]
            fixtures["sr2_move_amount"] = {"planned": 5, "moved": 5}
            result = run("take", "盐水", "5", "--storage", "scene:123", "--apply", "pickup-4", "--reply")
            assert "已从场景容器 123取回盐水 5 件" in result and calls[-1]["args"]["text"] == result
            first_move = calls.copy()
            assert first_move[0] == {"tool": "sr2_move_amount", "args": {"direction": "take", "item": "DeepBrineCraft",
                "quantity": 5, "storage": "scene:123", "dry_run": False}, "request_id": "pickup-4"}
            run("take", "盐水", "5", "--storage", "scene:123", "--apply", "pickup-4", "--reply")
            assert calls == first_move
            fixtures["sr2_move_amount"] = {"planned": 3, "moved": 0}
            assert "可搬 3 件，本次未搬动" in run("store", "盐水", "5", "--apply", "too-many", code=1)
            fixtures["sr2_move_amount"] = {"planned": 5, "moved": 2, "failure": "native_error"}
            assert "已核实搬动 2/5" in run("take", "盐水", "5", "--apply", "partial", code=1)
            fixtures["sr2_move_amount"] = {"available": False, "reason": "storage_not_found"}
            assert "没找到指定仓库" in run("take", "盐水", "5", "--storage", "scene:456", code=1)
            fixtures["sr2_move_amount"] = {"requested": 7, "planned": 7, "moved": 0}
            assert "可向主仓存入盐水 7 件" in run("store-all", "盐水")
            assert calls[0]["args"] == {"direction": "store", "item": "DeepBrineCraft", "quantity": "all",
                                        "storage": main_key, "dry_run": True}
            fixtures["sr2_move_amount"] = {"requested": 7, "planned": 6, "moved": 0}
            assert "可搬 6 件，本次未搬动" in run("take-all", "盐水", "--apply", "no-room", code=1)
            assert calls[0]["args"]["direction"] == "take" and calls[0]["args"]["quantity"] == "all"
            fixtures["sr2_move_amount"] = {"requested": 7, "planned": 7, "moved": 7}
            assert "已从主仓取回盐水 7 件" in run("take-all", "盐水", "--apply", "retrieve-all")
            fixtures["sr2_move_amount"] = {"requested": 0, "planned": 0, "moved": 0}
            assert "没有可搬" in run("store-all", "盐水", "--apply", "empty-bag")
            fixtures["sr2_move_amount"] = {"requested": 3, "planned": 3, "moved": 3}
            transfer = ("transfer", "虎斑结晶", "3", "--from", "scene:123", "--to", "main", "--apply", "move-3")
            assert "从场景容器 123搬到主仓" in run(*transfer)
            assert calls == [{"tool": "sr2_move_amount", "args": {"direction": "transfer", "item": "TabbyPlort",
                               "quantity": 3, "storage": main_key, "from": "scene:123", "dry_run": False},
                               "request_id": "move-3"}]
            first_transfer = calls.copy()
            run(*transfer)
            assert calls == first_transfer
            assert "同一个仓库" in run("transfer", "盐水", "1", "--from", "main", "--to", main_key, code=1)
            assert not calls
            fixtures["sr2_move_amount"] = {"available": False, "reason": "bad_quantity"}
            assert "0.5.27" in run("store-all", "盐水", code=1)
            fixtures["sr2_refine"] = {
                "plan": [{"id": brine, "count": 4, "deposit": 3}], "skipped": [],
                "deposited": [{"id": brine, "deposited": 3, "source_count_after": 1, "space_before": 3, "space_after": 0}],
            }
            assert "可把盐水 3 件从背包存入精炼炉" in run("refine-bag", "盐水")
            assert calls == [{"tool": "sr2_refine", "args": {"item": "DeepBrineCraft", "dry_run": True}}]
            assert "另有 1 件留在背包" in run("refine-bag", "盐水", "--apply", "deposit-bag")
            assert calls[0]["request_id"] == "deposit-bag"
            fixtures["sr2_refine"]["deposited"][0]["source_count_after"] = 4
            assert "双边数量未完全核对" in run("refine-bag", "盐水", "--apply", "deposit-mismatch", code=1)
            run("store", "盐水", "-2", code=2)
            assert not calls
            # 按区域选仓库：收集器在 zoneB，就搬进 zoneB 的筒仓，不跨区搬去 zoneA
            fixtures["sr2_storage_list"] = {
                "player_position": {"x": 0, "y": 0, "z": 0},
                "items": [
                    {"index": 0, "kind": "plot_silo", "storage_key": "plot:zoneA", "loaded": True,
                     "zone": "zoneA", "position": {"x": 5, "y": 0, "z": 0}, "slots": []},
                    {"index": 1, "kind": "plot_silo", "storage_key": "plot:zoneB", "loaded": True,
                     "zone": "zoneB", "position": {"x": 40, "y": 0, "z": 0}, "slots": []},
                    {"index": 2, "kind": "scene_storage", "selector": "scene:77", "loaded": True,
                     "zone": "zoneB", "position": {"x": 0, "y": 0, "z": 0},
                     "slots": [{"index": 0, "id": "IdentifiableType.CottonPlort", "name": "CottonPlort",
                                "count": 5, "max": 200, "unlocked": True}]}]}
            result = run("plorts")
            assert "CottonPlort×5→筒仓 zoneB" in result and "zoneA" not in result
            fixtures["sr2_transfer"] = {"available": True, "plan": [{"name": "CottonPlort", "move": 5}],
                                        "moved_count": 1,
                                        "moved": [{"name": "CottonPlort", "moved": 5, "target_slot": 3}]}
            result = run("plorts", "--apply", "zone-batch")
            assert "CottonPlort×5→筒仓 zoneB" in result
            executes = [c for c in calls if c["tool"] == "sr2_transfer" and c["args"].get("dry_run") is False]
            assert executes and executes[0]["args"]["to"] == "plot:zoneB"
            fixtures["sr2_chat_send"] = {"delivered": False}
            assert "北京时间" in run("clock", "--reply", code=1)
        finally:
            server.shutdown()
    print("PASS ranch workflows, device controls, collection, recipe tracking, saves, inventory and bounded moves")


if __name__ == "__main__":
    main()
