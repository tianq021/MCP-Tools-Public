// ToolsScan —— 扫描玩家附近可真空收集的东西（含矿物/材料）
//
//   sr2_nearby  列出 radius 米内场景里的 Vacuumable，按物品分组：id、名称、类别、数量、
//               最近距离和少量坐标。category=craft 就是 2 代可以直接吸进背包的矿物/材料
//               （钻石、细沙、熔岩尘、深卤水等 IdentifiableType.*Craft）。
// 只读，不修改存档。
using System;
using System.Collections.Generic;
using System.Linq;

namespace SlimeRancher2Bridge
{
    internal static class ToolsScan
    {
        internal static void Register(BridgeServer server)
            => server.Register("sr2_nearby", Run, mainThread: true);

        static string Str(Dictionary<string, object> args, string key, string fallback)
            => args.TryGetValue(key, out var value) && value != null ? Convert.ToString(value).Trim() : fallback;

        static string CategoryOf(string id, string name)
        {
            string text = id ?? name ?? "";
            if (text.IndexOf("Plort", StringComparison.OrdinalIgnoreCase) >= 0) return "plort";
            if (text.StartsWith("SlimeDefinition.", StringComparison.OrdinalIgnoreCase)) return "slime";
            if (text.IndexOf("Craft", StringComparison.OrdinalIgnoreCase) >= 0) return "craft";
            if (text.IndexOf("Fruit", StringComparison.OrdinalIgnoreCase) >= 0
                || text.IndexOf("Veggie", StringComparison.OrdinalIgnoreCase) >= 0
                || text.IndexOf("Hen", StringComparison.OrdinalIgnoreCase) >= 0
                || text.IndexOf("Egg", StringComparison.OrdinalIgnoreCase) >= 0
                || text.IndexOf("Food", StringComparison.OrdinalIgnoreCase) >= 0) return "food";
            return "other";
        }

        internal static Dictionary<string, object> Run(Dictionary<string, object> args)
        {
            float radius = 20f;
            if (args.TryGetValue("radius", out var r) && r != null
                && !float.TryParse(Convert.ToString(r), out radius)) radius = 20f;
            if (radius <= 0) radius = 20f;
            int limit = 30;
            if (args.TryGetValue("limit", out var l) && l != null
                && !int.TryParse(Convert.ToString(l), out limit)) limit = 30;
            if (limit < 1) limit = 1;
            string category = Str(args, "category", "all").ToLowerInvariant();
            if (category.Length == 0) category = "all";
            bool includeHeld = args.TryGetValue("include_held", out var ih) && ih is bool held && held;

            return ToolsNavigation.WithScene(scene =>
            {
                var player = Il2CppUtil.Try(scene, "Player");
                float[] playerPos = player == null ? null : Il2CppUtil.PositionOf(player);
                var all = Il2CppUtil.FindSceneObjects("Vacuumable", out string findInfo);
                var groups = new Dictionary<string, Dictionary<string, object>>(StringComparer.OrdinalIgnoreCase);
                int scanned = 0;
                foreach (var vacuumable in all)
                {
                    try
                    {
                        if (!includeHeld && Il2CppUtil.BoolOf(Il2CppUtil.Try(vacuumable, "_held"))) continue;
                        object identifiable = Il2CppUtil.Try(vacuumable, "_identifiable")
                            ?? Il2CppUtil.Try(vacuumable, "Identifiable");
                        object identType = identifiable == null ? null : Il2CppUtil.Try(identifiable, "identType");
                        if (identType == null) continue;
                        string id = Il2CppUtil.Try(identType, "ReferenceId") as string
                            ?? Il2CppUtil.Try(identType, "name") as string;
                        string name = Il2CppUtil.Try(identType, "name") as string;
                        string cat = CategoryOf(id, name);
                        if (category != "all" && cat != category) continue;
                        float[] pos = Il2CppUtil.PositionOf(vacuumable);
                        float distance = -1f;
                        if (playerPos != null && pos != null)
                        {
                            float dx = pos[0] - playerPos[0];
                            float dy = pos[1] - playerPos[1];
                            float dz = pos[2] - playerPos[2];
                            distance = (float)Math.Sqrt(dx * dx + dy * dy + dz * dz);
                        }
                        if (radius > 0 && (distance < 0 || distance > radius)) continue;
                        scanned++;
                        string key = id ?? name ?? "unknown";
                        if (!groups.TryGetValue(key, out var row))
                        {
                            row = new Dictionary<string, object>(StringComparer.Ordinal)
                            {
                                ["id"] = id,
                                ["name"] = name,
                                ["category"] = cat,
                                ["count"] = 0,
                                ["nearest"] = null,
                                ["positions"] = new List<object>(),
                            };
                            groups[key] = row;
                        }
                        row["count"] = (int)row["count"] + 1;
                        object nearest = row["nearest"];
                        if (nearest == null || distance < (float)nearest)
                            row["nearest"] = (float)Math.Round(distance, 2);
                        var positions = (List<object>)row["positions"];
                        if (pos != null && positions.Count < 5)
                            positions.Add(new Dictionary<string, object>(StringComparer.Ordinal)
                            {
                                ["x"] = Math.Round(pos[0], 2),
                                ["y"] = Math.Round(pos[1], 2),
                                ["z"] = Math.Round(pos[2], 2),
                            });
                    }
                    catch
                    {
                        // 单个对象读取失败不影响其它对象
                    }
                }
                var rows = groups.Values
                    .OrderBy(g => g["nearest"] is float f ? f : float.MaxValue)
                    .Take(limit)
                    .ToList();
                return new Dictionary<string, object>(StringComparer.Ordinal)
                {
                    ["available"] = true,
                    ["radius"] = radius,
                    ["category"] = category,
                    ["matched_count"] = scanned,
                    ["group_count"] = rows.Count,
                    ["groups"] = rows,
                    ["debug"] = new Dictionary<string, object>(StringComparer.Ordinal)
                    {
                        ["find"] = findInfo,
                        ["player_pos"] = playerPos == null ? "null" : "ok",
                    },
                    ["note"] = "category=craft 就是 2 代能直接用真空枪吸进背包的矿物/材料；结晶=plort，食物=food，史莱姆=slime",
                };
            });
        }
    }
}
