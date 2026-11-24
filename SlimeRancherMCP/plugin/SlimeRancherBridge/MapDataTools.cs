// MapDataTools —— 定位地图数据装置（MapDataEntry）+ 科技激活器
//
//   sr_map_data   列出所有 MapDataEntry（区域地图解锁装置）的坐标、是否已解锁、全息特效是否可见
//
// 用途：玩家找不到本区域的「地图数据」时，直接给出坐标和距离；也用于确认 map_unlocked 状态。
// 只读，不修改任何状态。
using System;
using System.Collections.Generic;
using UnityEngine;
using Object = UnityEngine.Object;

namespace SlimeRancherBridge
{
    static class MapDataTools
    {
        public static void Register(BridgeServer server)
        {
            server.Register("sr_map_data", MapData);
        }

        static Dictionary<string, object> Row(params object[] fields)
        {
            var result = new Dictionary<string, object>(StringComparer.Ordinal);
            for (int i = 0; i < fields.Length; i += 2)
            {
                result.Add((string)fields[i], fields[i + 1]);
            }
            return result;
        }

        static Dictionary<string, object> Fail(string code, string message)
        {
            return Row("__error", BridgeServer.Error(code, message));
        }

        static Dictionary<string, object> MapData(Dictionary<string, object> args)
        {
            var scene = SceneContext.Instance;
            if (scene == null || scene.PlayerState == null || scene.Player == null)
            {
                return Fail("NOT_READY", "Load a save and enter the game first.");
            }

            Vector3 player = scene.Player.transform.position;
            var rows = new List<object>();

            MapDataEntry[] entries;
            try
            {
                entries = Object.FindObjectsOfType<MapDataEntry>();
            }
            catch (Exception)
            {
                entries = new MapDataEntry[0];
            }

            for (int i = 0; i < entries.Length; i++)
            {
                var entry = entries[i];
                if (entry == null)
                {
                    continue;
                }
                Vector3 p = entry.transform.position;
                float dist = Vector3.Distance(player, p);
                bool unlocked = false;
                try
                {
                    unlocked = scene.PlayerState.HasUnlockedMap(entry.zone);
                }
                catch (Exception)
                {
                }
                bool hol = false;
                try
                {
                    hol = entry.hologram != null && entry.hologram.activeSelf;
                }
                catch (Exception)
                {
                }
                rows.Add(Row(
                    "zone", entry.zone.ToString(),
                    "position", new object[] { Math.Round(p.x, 1), Math.Round(p.y, 1), Math.Round(p.z, 1) },
                    "player_distance", Math.Round(dist, 1),
                    "map_unlocked", unlocked,
                    "hologram_active", hol,
                    "active", entry.gameObject.activeInHierarchy));
            }

            rows.Sort(delegate(object a, object b)
            {
                float da = (float)((Dictionary<string, object>)a)["player_distance"];
                float db = (float)((Dictionary<string, object>)b)["player_distance"];
                return da.CompareTo(db);
            });

            return Row("total", rows.Count, "player_position",
                new object[] { Math.Round(player.x, 1), Math.Round(player.y, 1), Math.Round(player.z, 1) },
                "items", rows);
        }
    }
}
