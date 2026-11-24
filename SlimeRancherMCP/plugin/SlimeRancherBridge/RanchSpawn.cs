// RanchSpawn.cs —— 生成史莱姆（作弊/测试用）
//
//   sr_spawn_slime   在玩家面前生成 N 只指定种类的史莱姆
//
// 用途：金史莱姆等稀有史莱姆太难等刷新（约 1/200 每天每点），需要测试或做成就时直接生成。
// 写法与游戏内生成一致：LookupDirector.GetPrefab + SRBehaviour.InstantiateActor（走 GameModel，能正常休眠/存档）。
using System;
using System.Collections.Generic;
using UnityEngine;
using MonomiPark.SlimeRancher.Regions;

namespace SlimeRancherBridge
{
    static partial class RanchTools
    {
        static void RegisterSpawn(BridgeServer server)
        {
            Action(server, "sr_spawn_slime", SpawnSlime);
        }

        static Dictionary<string, object> SpawnSlime(SceneContext scene, Dictionary<string, object> args)
        {
            string idText = Arg(args, "slime_id");
            if (string.IsNullOrEmpty(idText))
                return Fail("INVALID_SLIME", "Pass slime_id such as GOLD_SLIME.");

            Identifiable.Id id;
            try
            {
                id = (Identifiable.Id)Enum.Parse(typeof(Identifiable.Id), idText.Trim().ToUpperInvariant());
            }
            catch (Exception)
            {
                return Fail("UNKNOWN_SLIME", "Unknown slime_id: " + idText);
            }
            if (!Identifiable.IsSlime(id))
                return Fail("NOT_A_SLIME", idText + " is not a slime.");

            int count = Number(args, "count", 1);
            if (count < 1 || count > 20)
                return Fail("INVALID_COUNT", "count must be 1..20.");

            int distance = Number(args, "distance", 3);
            if (distance < 1) distance = 1;
            if (distance > 12) distance = 12;

            LookupDirector lookup = SRSingleton<GameContext>.Instance.LookupDirector;
            GameObject prefab = lookup == null ? null : lookup.GetPrefab(id);
            if (prefab == null)
                return Fail("NO_PREFAB", "No prefab for " + idText);

            Transform player = scene.Player.transform;
            Vector3 forward = player.forward;
            forward.y = 0f;
            if (forward.sqrMagnitude < 0.01f)
                forward = Vector3.forward;
            forward.Normalize();
            Vector3 basePos = player.position + forward * distance;

            var result = Row(
                "slime_id", id.ToString(),
                "count", count,
                "position", new object[] { Math.Round(basePos.x, 1), Math.Round(basePos.y, 1), Math.Round(basePos.z, 1) });
            if (Preview(args))
                return result;

            RegionRegistry.RegionSetId region = scene.RegionRegistry.GetCurrentRegionSetId();
            var spawned = new List<object>();
            for (int i = 0; i < count; i++)
            {
                float offset = count > 1 ? ((float)i - (count - 1) * 0.5f) * 0.8f : 0f;
                Vector3 p = basePos + player.right * offset + Vector3.up * 0.4f;
                GameObject obj = SRBehaviour.InstantiateActor(prefab, region, p, Quaternion.identity);
                if (obj == null)
                    continue;
                spawned.Add(Row(
                    "index", i,
                    "position", new object[] { Math.Round(p.x, 1), Math.Round(p.y, 1), Math.Round(p.z, 1) }));
            }
            result["spawned"] = spawned;
            return result;
        }
    }
}
