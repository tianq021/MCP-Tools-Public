// ToolsPlortDoors —— 查"结晶门"需要哪种结晶、要几颗
//
//   sr2_plort_doors  列出场景里的 PlortDepositor（结晶投料口）与 PuzzleSlot（解密槽），
//                    返回需要投入的结晶种类、填入进度、坐标、到玩家的距离和锁定状态。
//
// 原生字段（读 Assembly-CSharp.dll 元数据确认）：
//   PlortDepositor.IdentifiableTypeToCatch -> IdentifiableType   需要的结晶
//   PlortDepositor.FillAmount            -> Int32                已填入数量
//   PuzzleSlot.IdentifiableTypeToCatch   -> IdentifiableType
//   PuzzleSlot._fillAmount / _model.filled                       填入状态
//   PuzzleSlotLockable._slots / _unlockedEvent                   锁定与解锁
// 只读，不修改存档。
using System;
using System.Collections.Generic;
using System.Linq;
using Row = System.Collections.Generic.Dictionary<string, object>;

namespace SlimeRancher2Bridge
{
    internal static class ToolsPlortDoors
    {
        internal static void Register(BridgeServer server)
            => server.Register("sr2_plort_doors", Run, mainThread: true);

        static string Str(Row args, string key, string fallback)
            => args.TryGetValue(key, out var value) && value != null ? Convert.ToString(value).Trim() : fallback;

        static float[] Position(object component)
        {
            try { return Il2CppUtil.PositionOf(component); } catch { return null; }
        }

        static string IdentId(object depositor)
        {
            object identType = Il2CppUtil.Try(depositor, "IdentifiableTypeToCatch");
            if (identType == null) return null;
            return Il2CppUtil.Try(identType, "ReferenceId") as string
                ?? Il2CppUtil.Try(identType, "name") as string;
        }

        static Row RowOf(object component, string kind, float[] playerPos, float radius)
        {
            float[] pos = Position(component);
            float distance = -1f;
            if (playerPos != null && pos != null)
            {
                float dx = pos[0] - playerPos[0];
                float dy = pos[1] - playerPos[1];
                float dz = pos[2] - playerPos[2];
                distance = (float)Math.Sqrt(dx * dx + dy * dy + dz * dz);
            }
            if (radius > 0 && distance >= 0 && distance > radius) return null;

            object model = Il2CppUtil.Try(component, "_model");
            var row = new Row
            {
                ["kind"] = kind,
                ["required"] = IdentId(component),
                ["target_text"] = Il2CppUtil.Try(Il2CppUtil.Try(component, "TargetString"), "mTerm") as string,
                ["fill_amount"] = Il2CppUtil.Try(component, "FillAmount") is object f && f != null
                    ? (object)Il2CppUtil.IntOf(f, -1) : null,
                ["filled"] = model == null ? null : Il2CppUtil.Try(model, "filled"),
                ["loaded"] = true,
                ["distance"] = distance < 0 ? (float?)null : (float)Math.Round(distance, 2),
                ["position"] = pos == null ? null : new
                {
                    x = Math.Round(pos[0], 2),
                    y = Math.Round(pos[1], 2),
                    z = Math.Round(pos[2], 2),
                },
            };
            var lockable = Il2CppUtil.Try(component, "_puzLockable");
            row["lockable"] = lockable != null;
            return row;
        }

        internal static Row Run(Row args) => ToolsNavigation.WithScene(raw =>
        {
            var scene = (Il2Cpp.SceneContext)raw;
            float radius = 0f;
            if (args.TryGetValue("radius", out var r) && r != null && !float.TryParse(Convert.ToString(r), out radius)) radius = 0f;
            if (radius < 0) radius = 0f;
            int limit = 40;
            if (args.TryGetValue("limit", out var l) && l != null && !int.TryParse(Convert.ToString(l), out limit)) limit = 40;
            limit = Math.Clamp(limit, 1, 200);

            object player = Il2CppUtil.Try(scene, "Player");
            float[] playerPos = player == null ? null : Position(player);

            var rows = new List<Row>();
            string[] kinds = { "PlortDepositor", "PuzzleSlot" };
            var findInfo = new Row();
            foreach (var kind in kinds)
            {
                var objects = Il2CppUtil.FindSceneObjects(kind, out string diagnostic);
                findInfo[kind] = diagnostic;
                foreach (var component in objects)
                {
                    try
                    {
                        var row = RowOf(component, kind, playerPos, radius);
                        if (row != null) rows.Add(row);
                    }
                    catch
                    {
                        // 单个对象读取失败不影响其它对象
                    }
                }
            }
            rows.Sort((a, b) =>
            {
                float da = a["distance"] is float x ? x : float.MaxValue;
                float db = b["distance"] is float y ? y : float.MaxValue;
                return da.CompareTo(db);
            });
            var counts = new SortedDictionary<string, int>(StringComparer.Ordinal);
            foreach (var row in rows)
            {
                string key = Convert.ToString(row["required"]) ?? "unknown";
                counts.TryGetValue(key, out int count);
                counts[key] = count + 1;
            }
            return new Row
            {
                ["available"] = true,
                ["radius"] = radius <= 0 ? null : (object)radius,
                ["matched_count"] = rows.Count,
                ["required_kinds"] = counts,
                ["items"] = rows.Take(limit).ToList(),
                ["debug"] = findInfo,
                ["note"] = "required 是必须投入的结晶 IdentifiableType；fill_amount 是已填入数量，filled 来自存档模型。",
            };
        });
    }
}
