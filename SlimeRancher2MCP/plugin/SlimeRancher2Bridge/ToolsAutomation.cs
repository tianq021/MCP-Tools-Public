// ToolsAutomation —— 自动化设备（无人机/无人机站等）盘点
//
//   sr2_automation  只读：从存档数据与当前场景读取
//     · 已拥有/可用的 Gadget 蓝图（重点标出 DRONE / DRONE_STATION / TELEPORTER 等自动化类）
//     · 已放置的 gadget 数量（placedGadgetCounts）
//     · 牧场无人机（droneModel._ranchDrones）与探索无人机数量、站位坐标
//     · 当前场景里加载的 DroneStation / RanchDrone 组件数量
// 用于回答“有没有小蜜蜂之类的自动化设备、我现在有几个”。
using System;
using System.Collections.Generic;
using System.Linq;
using Il2Cpp;
using Il2CppMonomiPark.SlimeRancher.Drone;
using Il2CppMonomiPark.SlimeRancher.DataModel;
using UnityEngine;
using Row = System.Collections.Generic.Dictionary<string, object>;

namespace SlimeRancher2Bridge
{
    internal static class ToolsAutomation
    {
        internal static void Register(BridgeServer server)
            => server.Register("sr2_automation", Run, mainThread: true);

        static object Coordinates(object vector)
        {
            if (vector == null) return null;
            try
            {
                return new
                {
                    x = Math.Round(Il2CppUtil.Float(vector, "x"), 2),
                    y = Math.Round(Il2CppUtil.Float(vector, "y"), 2),
                    z = Math.Round(Il2CppUtil.Float(vector, "z"), 2),
                };
            }
            catch
            {
                return null;
            }
        }

        static string TypeName(object gadgetDefinition)
        {
            var type = Il2CppUtil.Try(gadgetDefinition, "Type");
            return type == null ? null : Convert.ToString(type);
        }

        static bool IsAutomation(string type)
            => type == "DRONE" || type == "DRONE_STATION" || type == "TELEPORTER"
            || type == "WARP_DEPOT" || type == "PRISMA_DETECTOR";

        internal static Row Run(Row args) => ToolsNavigation.WithScene(raw =>
        {
            var scene = (SceneContext)raw;
            var gameModel = scene.GameModel;
            var gadgets = gameModel == null ? null : Il2CppUtil.Try(gameModel, "GadgetsModel");
            var rows = new Row { ["available"] = true };

            // 1) 蓝图：已拥有 / 可用
            var owned = new List<Row>();
            var available = new List<Row>();
            foreach (var set in new[] { ("blueprints", owned), ("availBlueprints", available) })
            {
                var source = gadgets == null ? null : Il2CppUtil.Try(gadgets, set.Item1);
                foreach (var definition in Il2CppUtil.Enumerate(source))
                {
                    if (definition == null) continue;
                    string type = TypeName(definition);
                    bool automation = IsAutomation(type);
                    set.Item2.Add(new Row
                    {
                        ["type"] = type,
                        ["automation"] = automation,
                        ["blueprint_cost"] = Il2CppUtil.Try(definition, "BlueprintCost"),
                        ["count_limit"] = Il2CppUtil.Try(definition, "CountLimit"),
                    });
                }
            }
            rows["owned_blueprints"] = owned;
            rows["available_blueprints"] = available;
            rows["owned_automation_blueprints"] = owned.Where(r => (bool)r["automation"]).ToList();
            rows["available_automation_blueprints"] = available.Where(r => (bool)r["automation"]).ToList();

            // 2) 已放置 gadget 数量
            var placed = new List<Row>();
            var counts = gadgets == null ? null : Il2CppUtil.Try(gadgets, "placedGadgetCounts");
            foreach (var entry in Il2CppUtil.Enumerate(counts))
            {
                if (entry == null) continue;
                var key = Il2CppUtil.Try(entry, "Key");
                var value = Il2CppUtil.Try(entry, "Value");
                if (key == null) continue;
                string id = Il2CppUtil.Try(key, "ReferenceId") as string ?? Il2CppUtil.Try(key, "name") as string;
                placed.Add(new Row { ["id"] = id, ["count"] = value });
            }
            rows["placed_gadgets"] = placed;

            // 3) 无人机
            var droneModel = gameModel == null ? null : Il2CppUtil.Try(gameModel, "droneModel");
            var ranch = new List<Row>();
            var ranchDict = droneModel == null ? null : Il2CppUtil.Try(droneModel, "_ranchDrones");
            foreach (var entry in Il2CppUtil.Enumerate(ranchDict))
            {
                var model = entry == null ? null : Il2CppUtil.Try(entry, "Value");
                if (model == null) continue;
                object pos = null;
                try { pos = Coordinates(Il2CppUtil.Invoke(model, "GetPos")); }
                catch { }
                ranch.Add(new Row
                {
                    ["station_id"] = Il2CppUtil.Try(model, "StationId"),
                    ["position"] = pos,
                });
            }
            int explorerCount = Il2CppUtil.Enumerate(droneModel == null ? null : Il2CppUtil.Try(droneModel, "_explorerDrones")).Count;
            rows["ranch_drones"] = ranch;
            rows["ranch_drone_count"] = ranch.Count;
            rows["explorer_drone_count"] = explorerCount;

            // 4) 当前场景里加载的无人机站/无人机组件
            int stationComponents = 0;
            int droneComponents = 0;
            try { stationComponents = UnityEngine.Object.FindObjectsByType<DroneStation>(FindObjectsSortMode.None).Length; }
            catch { }
            try { droneComponents = UnityEngine.Object.FindObjectsByType<RanchDrone>(FindObjectsSortMode.None).Length; }
            catch { }
            rows["loaded_drone_stations"] = stationComponents;
            rows["loaded_ranch_drones"] = droneComponents;

            rows["note"] = "automation=true 的蓝图包括 DRONE（小蜜蜂/无人机）、DRONE_STATION（无人机站）、TELEPORTER 等；已放置数量来自存档 placedGadgetCounts";
            return rows;
        });
    }
}
