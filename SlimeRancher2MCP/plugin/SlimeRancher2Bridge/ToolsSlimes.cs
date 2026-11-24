// ToolsSlimes —— 列出附近的史莱姆（含饥饿/躁动/恐惧/睡眠与狂暴状态）
//
//   sr2_slimes  按 radius 过滤史莱姆，默认按到玩家的距离排序。
//               数据来自 GameModel.identifiables 里的 SlimeModel，因此已卸载区域的史莱姆
//               也能列出（位置取存档里的 lastPosition，loaded=false）。
// 只读，不修改存档。
using System;
using System.Collections.Generic;
using System.Linq;
using Il2CppMonomiPark.SlimeRancher.DataModel;
using Row = System.Collections.Generic.Dictionary<string, object>;

namespace SlimeRancher2Bridge
{
    internal static class ToolsSlimes
    {
        internal static void Register(BridgeServer server)
            => server.Register("sr2_slimes", Run, mainThread: true);

        static Row Unavailable(string reason) => new() { ["available"] = false, ["reason"] = reason };

        static string Str(Row args, string key, string fallback)
            => args.TryGetValue(key, out var value) && value != null ? Convert.ToString(value).Trim() : fallback;

        // 情绪值 0~1，保留 3 位；非有限值按 0 处理（float4 不会有 null）。
        static double Round3(float value)
            => double.IsFinite(value) ? Math.Round((double)value, 3) : 0.0;

        internal static Row Run(Row args) => ToolsNavigation.WithScene(raw =>
        {
            var scene = (Il2Cpp.SceneContext)raw;
            var game = scene.GameModel;
            if (game == null || !game.isInitialized) return Unavailable("game_model_unavailable");
            var models = game.identifiables;
            if (models == null) return Unavailable("identifiables_unavailable");

            float radius = 50f;
            if (args.TryGetValue("radius", out var r) && r != null && !float.TryParse(Convert.ToString(r), out radius))
                radius = 50f;
            if (radius <= 0) radius = 50f;
            int limit = 30;
            if (args.TryGetValue("limit", out var l) && l != null && !int.TryParse(Convert.ToString(l), out limit))
                limit = 30;
            limit = Math.Clamp(limit, 1, 200);
            string query = Str(args, "query", "");
            string state = Str(args, "state", "").ToLowerInvariant();
            bool includeUnloaded = args.TryGetValue("include_unloaded", out var iu) && iu is true;

            object player = Il2CppUtil.Try(scene, "Player");
            float[] playerPos = player == null ? null : Il2CppUtil.PositionOf(player);

            // 围栏位置，用来给每只史莱姆标出最近的围栏地块。
            var pens = new List<(string Id, UnityEngine.Vector3 Position)>();
            if (game.landPlots != null)
            {
                foreach (var entry in game.landPlots)
                {
                    var plot = entry.Value;
                    if (plot == null || plot.typeId != Il2Cpp.LandPlot.Id.CORRAL) continue;
                    if (plot.gameObj == null) continue;
                    pens.Add((entry.Key, plot.gameObj.transform.position));
                }
            }

            var rows = new List<(float Distance, Row Row)>();
            int slimeTotal = 0, loadedTotal = 0;
            foreach (var entry in models)
            {
                var model = entry.Value;
                if (model == null) continue;
                SlimeModel slime;
                try { slime = model.TryCast<SlimeModel>(); }
                catch { continue; }
                if (slime == null) continue;
                slimeTotal++;

                var transform = slime.Transform;
                bool loaded = transform != null;
                if (loaded) loadedTotal++;
                if (!loaded && !includeUnloaded) continue;

                // lastPosition 是 Unity.Mathematics 的 float4，和 UnityEngine.Vector3 不能直接互换。
                float posX, posY, posZ;
                if (loaded)
                {
                    var p = transform.position;
                    posX = p.x; posY = p.y; posZ = p.z;
                }
                else
                {
                    var p = slime.lastPosition;
                    posX = p.x; posY = p.y; posZ = p.z;
                }
                float distance = -1f;
                if (playerPos != null)
                {
                    float dx = posX - playerPos[0];
                    float dy = posY - playerPos[1];
                    float dz = posZ - playerPos[2];
                    distance = (float)Math.Sqrt(dx * dx + dy * dy + dz * dz);
                }
                if (distance >= 0 && distance > radius) continue;

                var definition = slime.ident == null ? null : slime.ident.TryCast<Il2Cpp.SlimeDefinition>();
                string id = definition?.ReferenceId;
                string name = definition?.name ?? id;
                if (query.Length > 0
                    && (id ?? "").IndexOf(query, StringComparison.OrdinalIgnoreCase) < 0
                    && (name ?? "").IndexOf(query, StringComparison.OrdinalIgnoreCase) < 0) continue;

                // SlimeModel.Emotions 是 Unity.Mathematics.float4，分量顺序与 SlimeEmotions
                // 的属性顺序一致：x=饥饿 y=躁动 z=恐惧 w=睡眠。
                var emotionVector = slime.Emotions;
                bool feral = Il2CppUtil.BoolOf(Il2CppUtil.Try(slime, "isFeral"));
                bool sleeping = Il2CppUtil.BoolOf(Il2CppUtil.Try(slime, "isSleeping"));
                bool statue = Il2CppUtil.BoolOf(Il2CppUtil.Try(slime, "isStatue"));
                bool radiant = Il2CppUtil.BoolOf(Il2CppUtil.Try(slime, "IsRadiant"));
                if (state == "feral" && !feral) continue;
                if (state == "loaded" && !loaded) continue;

                string penId = null;
                float penDistance = float.MaxValue;
                foreach (var pen in pens)
                {
                    float dx = posX - pen.Position.x;
                    float dz = posZ - pen.Position.z;
                    float d = (float)Math.Sqrt(dx * dx + dz * dz);
                    if (d < penDistance) { penDistance = d; penId = pen.Id; }
                }

                var row = new Row
                {
                    ["id"] = entry.Key,
                    ["slime"] = id,
                    ["name"] = name,
                    ["largo"] = definition?.IsLargo,
                    ["loaded"] = loaded,
                    ["position"] = new { x = Math.Round(posX, 2), y = Math.Round(posY, 2), z = Math.Round(posZ, 2) },
                    ["distance"] = distance < 0 ? (float?)null : (float)Math.Round(distance, 2),
                    ["hunger"] = Round3(emotionVector.x),
                    ["agitation"] = Round3(emotionVector.y),
                    ["fear"] = Round3(emotionVector.z),
                    ["sleepiness"] = Round3(emotionVector.w),
                    ["feral"] = feral,
                    ["sleeping"] = sleeping,
                    ["statue"] = statue,
                    ["radiant"] = radiant,
                    ["nearest_corral"] = penId,
                    ["nearest_corral_distance"] = penId == null ? (float?)null : (float)Math.Round(penDistance, 2),
                };
                rows.Add((distance < 0 ? float.MaxValue : distance, row));
            }

            rows.Sort((a, b) => a.Distance.CompareTo(b.Distance));
            var species = new SortedDictionary<string, int>(StringComparer.Ordinal);
            foreach (var pair in rows)
            {
                string key = Convert.ToString(pair.Row["slime"]) ?? "unknown";
                species.TryGetValue(key, out int count);
                species[key] = count + 1;
            }
            return new Row
            {
                ["available"] = true,
                ["radius"] = radius,
                ["query"] = query.Length == 0 ? null : query,
                ["state"] = state.Length == 0 ? null : state,
                ["include_unloaded"] = includeUnloaded,
                ["slime_models_total"] = slimeTotal,
                ["loaded_total"] = loadedTotal,
                ["matched_count"] = rows.Count,
                ["species"] = species,
                ["items"] = rows.Take(limit).Select(pair => pair.Row).ToList(),
                ["debug"] = new Row
                {
                    ["player_pos"] = playerPos == null ? null : new { x = playerPos[0], y = playerPos[1], z = playerPos[2] },
                    ["corrals"] = pens.Count,
                },
                ["note"] = "位置来自 SlimeModel.Transform（已加载）或存档 lastPosition（未加载）。情绪值 0~1：hunger/agitation/fear/sleepiness 按 float4 分量 x/y/z/w 映射。",
            };
        });
    }
}
