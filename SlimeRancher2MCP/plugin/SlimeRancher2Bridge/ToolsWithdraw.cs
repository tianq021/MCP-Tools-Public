// ToolsWithdraw —— 从仓库/收集器把物品取回背包（存仓的反向操作）
//
//   sr2_withdraw  storage 可用 sr2_storage_list 的 storage_key 或编号；留空=所有容器。
//                 item 支持完整 id、短名或槽位号；count 限定每种取回数量。
//                 走原生 AmmoSlotManager.MaybeAddToAnySlot(metadata) 放进背包，
//                 成功后从来源槽位扣减（落在 0 时 Clear），并双向回读核对。
// 默认预演，执行需 dry_run=false + request_id。
using System;
using System.Collections.Generic;
using System.Linq;
using AmmoSlot = Il2CppMonomiPark.SlimeRancher.Player.AmmoSlot;
using Row = System.Collections.Generic.Dictionary<string, object>;

namespace SlimeRancher2Bridge
{
    internal static class ToolsWithdraw
    {
        internal static void Register(BridgeServer server)
            => server.Register("sr2_withdraw", Run, mainThread: true, write: true);

        static string Str(Row args, string key, string fallback)
            => args.TryGetValue(key, out var value) && value != null ? Convert.ToString(value).Trim() : fallback;

        static int CountInBag(object bag, string id)
        {
            int total = 0;
            foreach (var slot in Il2CppUtil.Enumerate(Il2CppUtil.Try(bag, "Slots")))
            {
                var slotId = Il2CppUtil.Try(slot, "Id");
                if (slotId == null) continue;
                string text = Il2CppUtil.Try(slotId, "ReferenceId") as string ?? Il2CppUtil.Try(slotId, "name") as string;
                if (!string.Equals(text, id, StringComparison.OrdinalIgnoreCase)) continue;
                total += Il2CppUtil.IntOf(Il2CppUtil.Try(slot, "Count"));
            }
            return total;
        }

        internal static Row Run(Row args)
        {
            bool preview = (bool)args.GetValueOrDefault("dry_run", true);
            string itemArg = Str(args, "item", "");
            string storageArg = Str(args, "storage", "");
            int countLimit = -1;
            string countArg = Str(args, "count", "");
            if (countArg.Length > 0 && (!int.TryParse(countArg, out countLimit) || countLimit <= 0))
                return new() { ["available"] = false, ["reason"] = "bad_count" };

            return ToolsNavigation.WithScene(raw =>
            {
                var scene = (Il2Cpp.SceneContext)raw;
                var bag = scene.PlayerState?.Ammo;
                if (bag?.Slots == null) return new() { ["available"] = false, ["reason"] = "ammo_unavailable" };
                var silos = ToolsStorage.Warehouses(scene, scene.Player?.transform.position);
                if (silos.Count == 0) return new() { ["available"] = false, ["reason"] = "no_storage_found" };

                if (storageArg.Length > 0)
                {
                    List<ToolsStorage.Silo> picked = null;
                    if (int.TryParse(storageArg, out int only) && only >= 0 && only < silos.Count)
                        picked = new() { silos[only] };
                    else
                    {
                        var found = silos.Find(s => s.Key == storageArg);
                        if (found != null) picked = new() { found };
                    }
                    if (picked == null) return new() { ["available"] = false, ["reason"] = "bad_storage" };
                    silos = picked;
                }

                var plan = new List<Row>();
                for (int si = 0; si < silos.Count; si++)
                {
                    var silo = silos[si];
                    if (silo?.Live == null) continue;
                    for (int i = 0; i < silo.Live.Length; i++)
                    {
                        var slot = silo.Live[i];
                        object id = Il2CppUtil.Try(slot, "Id");
                        if (id == null) continue;
                        int count = Il2CppUtil.IntOf(Il2CppUtil.Try(slot, "Count"));
                        if (count <= 0) continue;
                        if (itemArg.Length > 0 && !ToolsStorage.Matches(itemArg, -1, slot)) continue;
                        int take = countLimit > 0 ? Math.Min(countLimit, count) : count;
                        plan.Add(new Row
                        {
                            ["storage_index"] = si,
                            ["storage_key"] = silo.Key,
                            ["container"] = silo.Key ?? silo.ObjectName,
                            ["slot"] = i,
                            ["id"] = ToolsStorage.IdOf(slot),
                            ["name"] = slot.Id?.name,
                            ["count"] = count,
                            ["take"] = take,
                        });
                    }
                }
                var result = new Row
                {
                    ["available"] = true,
                    ["dry_run"] = preview,
                    ["plan"] = plan,
                    ["plan_count"] = plan.Count,
                    ["note"] = "把容器里的物品按原生槽位规则放回背包，成功后从容器扣减，并双向回读",
                };
                if (preview) return result;

                var moved = new List<object>();
                var failures = new List<object>();
                foreach (var row in plan)
                {
                    int si = (int)row["storage_index"];
                    int slotIndex = (int)row["slot"];
                    string id = Convert.ToString(row["id"]);
                    int take = (int)row["take"];
                    try
                    {
                        var silo = silos[si];
                        var slot = silo.Live[slotIndex];
                        object metadata = Il2CppUtil.Try(slot, "Metadata");
                        if (metadata == null) throw new InvalidOperationException("metadata_unavailable");
                        int beforeBag = CountInBag(bag, id);
                        int beforeSlot = Il2CppUtil.IntOf(Il2CppUtil.Try(slot, "Count"));
                        int requested = take;
                        int added = 0;
                        while (added < requested)
                        {
                            object ok = Il2CppUtil.Invoke(bag, "MaybeAddToAnySlot", new[] { metadata, (object)false });
                            if (!(ok is bool accepted && accepted)) break;
                            int nowBag = CountInBag(bag, id);
                            int delta = nowBag - (beforeBag + added);
                            if (delta <= 0) break;
                            added += delta;
                            if (added >= requested) break;
                        }
                        if (added <= 0)
                        {
                            failures.Add(new Row { ["id"] = id, ["slot"] = slotIndex,
                                ["reason"] = "vacpack_rejected_or_full",
                                ["invoke_error"] = Il2CppUtil.LastInvokeError });
                            continue;
                        }
                        int effective = Math.Min(added, beforeSlot);
                        int target = beforeSlot - effective;
                        try
                        {
                            ((AmmoSlot)slot).Count = target;
                            if (target == 0) ((AmmoSlot)slot).Clear();
                        }
                        catch (Exception e)
                        {
                            failures.Add(new Row { ["id"] = id, ["slot"] = slotIndex,
                                ["reason"] = "decrement_failed", ["error"] = e.GetBaseException().Message });
                            continue;
                        }
                        moved.Add(new Row
                        {
                            ["id"] = id,
                            ["slot"] = slotIndex,
                            ["moved"] = effective,
                            ["bag_before"] = beforeBag,
                            ["bag_after"] = CountInBag(bag, id),
                            ["storage_before"] = beforeSlot,
                            ["storage_after"] = Il2CppUtil.IntOf(Il2CppUtil.Try(slot, "Count")),
                        });
                    }
                    catch (Exception e)
                    {
                        failures.Add(new Row { ["id"] = id, ["slot"] = slotIndex,
                            ["error"] = e.GetBaseException().Message });
                    }
                }
                result["moved"] = moved;
                result["moved_count"] = moved.Count;
                result["failures"] = failures;
                result["vacpack_after"] = Il2CppUtil.Enumerate(Il2CppUtil.Try(bag, "Slots"))
                    .Where(s => Il2CppUtil.Try(s, "Id") != null && Il2CppUtil.IntOf(Il2CppUtil.Try(s, "Count")) > 0)
                    .Select(s => (object)new Row
                    {
                        ["id"] = ToolsStorage.IdOf((AmmoSlot)s),
                        ["count"] = Il2CppUtil.IntOf(Il2CppUtil.Try(s, "Count")),
                    }).ToList();
                return result;
            });
        }
    }
}
