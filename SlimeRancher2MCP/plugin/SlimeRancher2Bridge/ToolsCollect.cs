// ToolsCollect —— 收集附近一定范围内的某一种东西（真空吸收）
//
//   sr2_collect  在玩家周围 radius 米内找指定物品的 Vacuumable（地上的结晶/食物/资源等），
//                预览列表 → 执行时调用游戏自己的 Vacuumable.TryConsume() 吸进背包
//
// 游戏 API（1.3.0 实测类型名）：
//   MonomiPark.SlimeRancher.Vacuumable（MonoBehaviour）
//     _identifiable : Identifiable → identType : IdentifiableType
//     CanCapture() / TryConsume() -> bool / _held
//   PlayerState.Ammo : AmmoSlotManager
//     MaybeAddResource(id, slotIdx, count, overflow) / GetSlotCount / Decrement
//   UnityEngine.Object.FindObjectsByType(Type, FindObjectsSortMode) 枚举场景对象
//
// 安全：默认预演；执行后对比"背包里该物品数量"的实际增量，和成功消费的对象数核对。
//       如果 TryConsume 返回成功但背包没增加，会在结果里明确标出（可能是被其它路径处理/销毁）。
using System;
using System.Collections.Generic;
using System.Reflection;
using AmmoSlot = Il2CppMonomiPark.SlimeRancher.Player.AmmoSlot;

namespace SlimeRancher2Bridge
{
    internal static class ToolsCollect
    {
        static void PlayerAndAmmo(out object player, out object ammo, out float[] playerPos)
        {
            object p = null, a = null;
            float[] pos = null;
            ToolsNavigation.WithScene(scene =>
            {
                p = Il2CppUtil.Try(scene, "Player");
                object state = Il2CppUtil.Try(scene, "PlayerState");
                a = state == null ? null : Il2CppUtil.Try(state, "Ammo");
                pos = p == null ? null : Il2CppUtil.PositionOf(p);
                return new Dictionary<string, object>();
            });
            player = p;
            ammo = a;
            playerPos = pos;
        }

        static string IdOf(object identifiableType)
        {
            if (identifiableType == null) return null;
            return Il2CppUtil.Try(identifiableType, "ReferenceId") as string
                ?? Il2CppUtil.Try(identifiableType, "name") as string;
        }

        static object IdentTypeOf(object vacuumable)
        {
            object identifiable = Il2CppUtil.Try(vacuumable, "_identifiable")
                ?? Il2CppUtil.Try(vacuumable, "Identifiable");
            return identifiable == null ? null : Il2CppUtil.Try(identifiable, "identType");
        }

        // Native vacuum pieces: add the object's AmmoMetadata to the vacpack and drop the
        // world object. Used when TryConsume() throws because the object was not reached
        // through the vacuum weapon. Scene objects often have AmmoMetadata == null, so the
        // metadata is rebuilt from the identifiable type when needed.
        static void ConsumeFallback(object vacuumable, object ammo)
        {
            if (ammo == null) throw new InvalidOperationException("ammo_unavailable");
            object identifiable = Il2CppUtil.Try(vacuumable, "_identifiable")
                ?? Il2CppUtil.Try(vacuumable, "Identifiable");
            object metadata = identifiable == null ? null : Il2CppUtil.Try(identifiable, "AmmoMetadata");
            object identType = identifiable == null ? null
                : (Il2CppUtil.Try(identifiable, "identType") ?? Il2CppUtil.Try(identifiable, "IdentType"));
            if (metadata == null && identType != null)
            {
                try { metadata = Activator.CreateInstance(typeof(AmmoSlot.AmmoMetadata), identType); }
                catch { }
            }
            if (metadata == null) throw new InvalidOperationException("ammo_metadata_unavailable");
            object added = Il2CppUtil.Invoke(ammo, "MaybeAddToAnySlot", new[] { metadata, (object)false });
            if (!(added is bool ok && ok))
            {
                // Retry slot by slot; Some slots only accept explicit adds.
                object slots = Il2CppUtil.Try(ammo, "Slots");
                int slotCount = slots == null ? 0 : Il2CppUtil.Enumerate(slots).Count;
                for (int i = 0; i < slotCount && !(added is bool okSlot && okSlot); i++)
                    added = Il2CppUtil.Invoke(ammo, "MaybeAddToSpecificSlot", new object[] { metadata, i, 1, false });
                if (!(added is bool okAny && okAny)) throw new InvalidOperationException("vacpack_rejected");
            }
            object gameObject = Il2CppUtil.Try(vacuumable, "gameObject");
            var target = gameObject as UnityEngine.Object ?? vacuumable as UnityEngine.Object;
            if (target == null) throw new InvalidOperationException("gameobject_unavailable");
            UnityEngine.Object.Destroy(target);
        }

        static int CountInVacpack(object ammo, string idOrName)
        {
            if (ammo == null) return -1;
            int total = 0;
            foreach (var slot in Il2CppUtil.Enumerate(Il2CppUtil.Try(ammo, "Slots")))
            {
                object id = Il2CppUtil.Try(slot, "Id");
                if (id == null) continue;
                string text = Il2CppUtil.Try(id, "ReferenceId") as string ?? Il2CppUtil.Try(id, "name") as string;
                if (!string.Equals(text, idOrName, StringComparison.OrdinalIgnoreCase)
                    && !string.Equals(Il2CppUtil.Try(id, "name") as string, idOrName, StringComparison.OrdinalIgnoreCase))
                    continue;
                total += Il2CppUtil.IntOf(Il2CppUtil.Try(slot, "Count"));
            }
            return total;
        }

        sealed class Match
        {
            public object Vacuumable;
            public string Id;
            public string Name;
            public float[] Position;
            public float Distance;
        }

        static List<Match> FindNearby(string itemFilter, float radius, out string findDiagnostic)
        {
            var matches = new List<Match>();
            PlayerAndAmmo(out _, out _, out float[] playerPos);
            var all = Il2CppUtil.FindSceneObjects("Vacuumable", out string findInfo);
            findDiagnostic = findInfo + " player_pos=" + (playerPos == null ? "null" : "ok");

            foreach (var vacuumable in all)
            {
                try
                {
                    if (Il2CppUtil.BoolOf(Il2CppUtil.Try(vacuumable, "_held"))) continue;
                    object identType = IdentTypeOf(vacuumable);
                    if (identType == null) continue;
                    string id = IdOf(identType);
                    string name = Il2CppUtil.Try(identType, "name") as string;
                    bool matchesFilter = string.Equals(itemFilter, id, StringComparison.OrdinalIgnoreCase)
                        || string.Equals(itemFilter, name, StringComparison.OrdinalIgnoreCase)
                        || (id != null && id.EndsWith("." + itemFilter, StringComparison.OrdinalIgnoreCase));
                    if (!matchesFilter) continue;
                    float[] position = Il2CppUtil.PositionOf(vacuumable);
                    float distance = -1f;
                    if (playerPos != null && position != null)
                    {
                        float dx = position[0] - playerPos[0];
                        float dy = position[1] - playerPos[1];
                        float dz = position[2] - playerPos[2];
                        distance = (float)Math.Sqrt(dx * dx + dy * dy + dz * dz);
                    }
                    if (radius > 0 && (distance < 0 || distance > radius)) continue;
                    matches.Add(new Match
                    {
                        Vacuumable = vacuumable,
                        Id = id,
                        Name = name,
                        Position = position,
                        Distance = distance,
                    });
                }
                catch
                {
                    // 单个对象读取失败不影响其它对象
                }
            }
            matches.Sort((a, b) => a.Distance.CompareTo(b.Distance));
            return matches;
        }

        internal static Dictionary<string, object> Run(Dictionary<string, object> args)
        {
            bool preview = true;
            if (args.TryGetValue("dry_run", out var mode) && mode is bool flag) preview = flag;

            string item = args.TryGetValue("item", out var it) && it != null ? Convert.ToString(it).Trim() : "";
            if (item.Length == 0)
                return new() { ["available"] = false, ["reason"] = "item_required", ["hint"] = "必须指定要收集的物品 id 或名称，例如 PinkPlort" };

            float radius = 20f;
            if (args.TryGetValue("radius", out var r) && r != null
                && !float.TryParse(Convert.ToString(r), out radius)) radius = 20f;
            if (radius <= 0) radius = 20f;

            int limit = 10;
            if (args.TryGetValue("limit", out var l) && l != null
                && !int.TryParse(Convert.ToString(l), out limit)) limit = 10;
            if (limit < 1) limit = 1;

            var matches = FindNearby(item, radius, out string findDiagnostic);
            var rows = new List<object>();
            for (int i = 0; i < matches.Count && i < limit; i++)
            {
                var m = matches[i];
                rows.Add(new Dictionary<string, object>(StringComparer.Ordinal)
                {
                    ["index"] = i,
                    ["id"] = m.Id,
                    ["name"] = m.Name,
                    ["distance"] = m.Distance < 0 ? null : Math.Round(m.Distance, 2),
                    ["position"] = m.Position == null ? null : new Dictionary<string, object>
                    {
                        ["x"] = Math.Round(m.Position[0], 2),
                        ["y"] = Math.Round(m.Position[1], 2),
                        ["z"] = Math.Round(m.Position[2], 2),
                    },
                });
            }

            PlayerAndAmmo(out _, out object ammo, out _);
            int before = CountInVacpack(ammo, item);

            var data = new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["available"] = true,
                ["dry_run"] = preview,
                ["item_filter"] = item,
                ["radius"] = radius,
                ["limit"] = limit,
                ["found_total"] = matches.Count,
                ["plan_count"] = rows.Count,
                ["targets"] = rows,
                ["vacpack_before"] = before,
                ["debug"] = new Dictionary<string, object>(StringComparer.Ordinal) { ["find"] = findDiagnostic },
                ["note"] = "优先调用游戏原生 Vacuumable.TryConsume()；它抛异常时退回 AmmoMetadata + MaybeAddToAnySlot 再加销毁世界对象，并用背包实际增量核对",
            };
            if (preview) return data;

            int consumeOk = 0;
            int consumeFallback = 0;
            PlayerAndAmmo(out _, out object bagManager, out _);
            var failures = new List<object>();
            for (int i = 0; i < matches.Count && i < limit; i++)
            {
                var m = matches[i];
                try
                {
                    object canCapture = Il2CppUtil.Invoke(m.Vacuumable, "CanCapture");
                    if (canCapture is bool captureable && !captureable)
                    {
                        failures.Add(new Dictionary<string, object>(StringComparer.Ordinal)
                        { ["index"] = i, ["id"] = m.Id, ["reason"] = "cannot_capture" });
                        continue;
                    }
                    object result = Il2CppUtil.Invoke(m.Vacuumable, "TryConsume");
                    if (result is bool ok && ok)
                    {
                        consumeOk++;
                        continue;
                    }
                    // TryConsume throws inside the game when the object was not reached
                    // through the vacuum weapon (NullReferenceException). Fall back to the
                    // same native pieces: add AmmoMetadata to the vacpack, drop the object.
                    string nativeError = Il2CppUtil.LastInvokeError;
                    var fallback = new Dictionary<string, object>(StringComparer.Ordinal)
                    { ["index"] = i, ["id"] = m.Id };
                    try
                    {
                        ConsumeFallback(m, bagManager);
                        consumeOk++;
                        consumeFallback++;
                        fallback["path"] = "metadata_then_destroy";
                        fallback["native_error"] = nativeError;
                        failures.Add(fallback);
                    }
                    catch (Exception fe)
                    {
                        fallback["path"] = "none";
                        fallback["reason"] = "try_consume_failed";
                        fallback["native_error"] = nativeError;
                        fallback["fallback_error"] = fe is TargetInvocationException tie
                            ? tie.GetBaseException().Message : fe.Message;
                        failures.Add(fallback);
                    }
                }
                catch (Exception e)
                {
                    failures.Add(new Dictionary<string, object>(StringComparer.Ordinal)
                    { ["index"] = i, ["id"] = m.Id, ["error"] = e.GetBaseException().Message });
                }
            }

            PlayerAndAmmo(out _, out object ammoAfter, out _);
            int after = CountInVacpack(ammoAfter, item);
            var remaining = FindNearby(item, radius, out _);

            data["consumed_ok"] = consumeOk;
            data["consume_fallback"] = consumeFallback;
            data["vacpack_after"] = after;
            data["vacpack_delta"] = before >= 0 && after >= 0 ? after - before : (object)null;
            data["remaining_nearby"] = remaining.Count;
            data["failures"] = failures;
            if (before >= 0 && after >= 0 && after - before < consumeOk)
                data["warning"] = "成功消费的对象数多于背包实际增量：可能有物品被销毁但没进背包，请人工检查";
            return data;
        }
    }
}

