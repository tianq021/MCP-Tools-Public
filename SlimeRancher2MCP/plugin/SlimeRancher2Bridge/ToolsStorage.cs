using System;
using System.Collections.Generic;
using System.Linq;
using Il2Cpp;
using Il2CppMonomiPark.SlimeRancher.DataModel;
using Il2CppMonomiPark.SlimeRancher.Player;
using UnityEngine;
using Row = System.Collections.Generic.Dictionary<string, object>;

namespace SlimeRancher2Bridge
{
    internal static partial class ToolsStorage
    {
        internal sealed class Silo
        {
            public string Key;
            public string SceneKey;
            public string Selector => Key ?? SceneKey;
            public string Type;
            public string ObjectName;
            public string ParentName;
            public bool Loaded;
            public GameObject Object;
            public Vector3? Position;
            public float? Distance;
            public AmmoSlotManager Manager;
            public AmmoModel Model;
            public AmmoSlot[] Live;
            public AmmoSlot[] Rules;
            public bool[] OpenPanels;
            public bool Ready => Manager != null;
            // A silo always has 12 slots in the save model, but only the panels the plot
            // has bought are usable in game (6 by default here). The panel mapping is the
            // only source of truth, both for loaded and unloaded silos.
            public bool Unlocked(int slot) => Rules[slot]?.Definition != null && Rules[slot].IsUnlocked
                && (OpenPanels == null || OpenPanels[slot]);
            public string Gate => OpenPanels == null ? (Loaded ? "live" : "save") : "panel";
        }

        static string Id(AmmoSlot slot) => slot?.Id?.ReferenceId ?? slot?.Id?.name;
        internal static string IdOf(AmmoSlot slot) => Id(slot);
        static object Coordinates(Vector3? p) => p.HasValue
            ? new { x = Math.Round(p.Value.x, 2), y = Math.Round(p.Value.y, 2), z = Math.Round(p.Value.z, 2) }
            : null;

        // A native AmmoSlotManager consults its model for Slots. Bind to the existing
        // model without calling InitModel/SetModel, which can initialize or resize slots.
        static AmmoSlotManager Bind(AmmoModel model, AmmoSlotDefinition[] definitions)
            => new AmmoSlotManager(definitions) { _ammoModel = model };

        static SiloStorage PrefabStorage(GameObject prefab, string setKey, int slots)
        {
            if (prefab == null) return null;
            foreach (var storage in prefab.GetComponentsInChildren<SiloStorage>(true))
            {
                var set = storage.AmmoSetReference;
                if (set == null || (setKey != set.ReferenceId && setKey != set.Guid && setKey != set.name)) continue;
                var definitions = storage.AmmoSlotDefinitions;
                if (definitions != null && definitions.Length == slots) return storage;
            }
            return null;
        }

        static bool[] OpenPanels(GameObject root, SiloStorage storage, LandPlotModel plot, int count)
        {
            if (root == null) return null;
            var open = new bool[count];
            bool singleInventory = root.GetComponentsInChildren<SiloStorage>(true).Length == 1;
            foreach (var panel in root.GetComponentsInChildren<SiloStorageActivator>(true))
            {
                bool purchased = RanchControls.HasSiloPanel(plot, panel.ActivatorIdx);
                if (!purchased || panel.SiloSlotUIs == null) continue;
                foreach (var ui in panel.SiloSlotUIs)
                {
                    if (ui == null || ui.slotIdx < 0 || ui.slotIdx >= count) continue;
                    // SiloSlotUI.Awake normally resolves this parent. Prefab assets
                    // have not run Awake, so ui.storage is still null in the field.
                    var owner = ui.storage ?? ui.GetComponentInParent<SiloStorage>(true);
                    if (owner == storage || (owner == null && singleInventory)) open[ui.slotIdx] = true;
                }
            }
            return open;
        }

        static Silo Remote(string key, string setKey, AmmoModel model, LandPlotModel plot)
        {
            var slots = model.Slots.ToArray();
            var silo = new Silo { Key = key, Type = "SiloStorage", Model = model, Live = slots, Rules = slots };
            if (slots.Length == 0 || slots.Any(s => s == null)) return silo;
            // Unloaded save slots retain counts, but even a non-null Definition can
            // have the AmmoSlot constructor's default MaxCount of 1. Use prefab rules.
            var prefab = GameContext.Instance?.LookupDirector?.GetPlotPrefab(LandPlot.Id.SILO);
            var storage = PrefabStorage(prefab, setKey, slots.Length);
            if (storage == null) return silo;
            var defs = storage.AmmoSlotDefinitions.ToArray();
            if (defs.Any(d => d == null)) return silo;
            silo.OpenPanels = OpenPanels(prefab, storage, plot, slots.Length);
            var upgrade = prefab.GetComponentInChildren<StorageUpgrader>(true);
            bool hasCapacityUpgrade = plot.HasUpgrade(LandPlot.Upgrade.STORAGE_CAPACITY_INCREASE);
            if (hasCapacityUpgrade && upgrade == null) return silo;
            var tracker = model._unitPropertyTracker;
            var copies = new AmmoSlot[slots.Length];
            for (int i = 0; i < copies.Length; ++i)
            {
                var def = defs[i];
                if (tracker == null && (def.MaxAmountProperty != null || def.IsUnlockedProperty != null)) return silo;
                var copy = new AmmoSlot { Definition = def };
                copy.MaxCount = def.MaxAmount;
                if (def.MaxAmountProperty != null)
                    copy._maxCountValue = tracker.GetFloatPropertyValue(def.MaxAmountProperty, def.MaxAmount);
                if (def.IsUnlockedProperty != null)
                    copy._isUnlockedValue = tracker.GetFloatPropertyValue(def.IsUnlockedProperty, 0);
                if (hasCapacityUpgrade) copy.MaxCount = upgrade.StorageCapacityUpgradeAmount;
                new Contents(slots[i]).Restore(copy);
                if (copy.MaxCount < slots[i].Count) return silo;
                copies[i] = copy;
            }
            var projection = new AmmoModel(tracker) { Slots = copies };
            silo.Rules = copies;
            silo.Manager = Bind(projection, defs);
            return silo;
        }

        internal static List<Silo> Warehouses(SceneContext scene, Vector3? player)
        {
            var silos = new List<Silo>();
            var byModel = new Dictionary<IntPtr, Silo>();
            // Map every silo inventory in the save to its land plot and stable key. Loaded
            // and unloaded silos then share one source of truth for slot availability.
            var plotsOfModel = new Dictionary<IntPtr, (AmmoModel Model, LandPlotModel Plot, string Key, string SetKey)>();
            var plots = scene.GameModel?.landPlots;
            if (plots != null)
            {
                foreach (var plotEntry in plots)
                {
                    var plot = plotEntry.Value;
                    if (plot == null || plot.typeId != LandPlot.Id.SILO || plot.siloAmmo == null) continue;
                    foreach (var entry in plot.siloAmmo)
                    {
                        var model = entry.Value;
                        if (model?.Slots == null || plotsOfModel.ContainsKey(model.Pointer)) continue;
                        plotsOfModel[model.Pointer] = (model, plot,
                            "plot:" + Uri.EscapeDataString(plotEntry.Key) + "/" + Uri.EscapeDataString(entry.Key),
                            entry.Key);
                    }
                }
            }
            foreach (var storage in UnityEngine.Object.FindObjectsByType<SiloStorage>(FindObjectsSortMode.None))
            {
                var manager = storage.GetRelevantAmmo();
                var model = manager?._ammoModel;
                if (model?.Slots == null) continue;
                var pos = storage.transform.position;
                float? distance = player.HasValue ? Vector3.Distance(pos, player.Value) : null;
                var slots = model.Slots.ToArray();
                var silo = new Silo { Loaded = true, Object = storage.gameObject, Position = pos, Distance = distance,
                    Type = storage.GetIl2CppType().Name, Manager = manager, Model = model, Rules = slots, Live = slots,
                    SceneKey = "scene:" + storage.GetInstanceID(), ObjectName = storage.gameObject?.name,
                    ParentName = storage.transform?.parent?.gameObject?.name };
                if (plotsOfModel.TryGetValue(model.Pointer, out var plotInfo))
                {
                    silo.Key = plotInfo.Key;
                    if (plotInfo.Plot.gameObj != null && plotInfo.Plot.gameObj.activeInHierarchy)
                    {
                        var panels = OpenPanels(plotInfo.Plot.gameObj, storage, plotInfo.Plot, slots.Length);
                        // Keep the panel gate only when it proves at least one usable slot.
                        if (panels != null && panels.Any(open => open)) silo.OpenPanels = panels;
                    }
                }
                // Paired depots can expose one inventory via two game objects.
                if (byModel.TryGetValue(model.Pointer, out var other))
                {
                    if (distance < other.Distance) { other.Distance = distance; other.Position = pos; }
                    continue;
                }
                byModel.Add(model.Pointer, silo);
                silos.Add(silo);
            }
            silos.Sort((a, b) => Nullable.Compare(a.Distance, b.Distance));
            var remote = new List<Silo>();
            foreach (var plotInfo in plotsOfModel.Values)
            {
                if (byModel.ContainsKey(plotInfo.Model.Pointer)) continue;
                var plot = plotInfo.Plot;
                // If the plot is instantiated, its active silo components above define the
                // accessible banks. Inactive upgrade objects stay excluded.
                if (plot.gameObj != null && plot.gameObj.activeInHierarchy &&
                    plot.gameObj.GetComponentsInChildren<SiloStorage>(false).Length > 0) continue;
                var silo = Remote(plotInfo.Key, plotInfo.SetKey, plotInfo.Model, plot);
                byModel.Add(plotInfo.Model.Pointer, silo);
                remote.Add(silo);
            }
            silos.AddRange(remote.OrderBy(s => s.Key, StringComparer.Ordinal));
            return silos;
        }

        static Row Inventory(List<Silo> silos)
        {
            var rows = new List<Row>();
            foreach (var silo in silos)
            {
                var slots = new List<Row>();
                int total = 0;
                int usable = 0;
                for (int i = 0; i < silo.Live.Length; ++i)
                {
                    var actual = silo.Live[i];
                    var rule = silo.Rules[i];
                    int count = actual?.Count ?? 0;
                    int? max = silo.Ready && rule?.Definition != null ? rule.MaxCount : null;
                    bool unlocked = silo.Unlocked(i);
                    if (unlocked) usable++;
                    total += count;
                    slots.Add(new() { ["index"] = i, ["id"] = Id(actual), ["name"] = actual?.Id?.name,
                        ["count"] = count, ["max"] = max, ["space"] = max - count,
                        ["unlocked"] = max.HasValue ? unlocked : null });
                }
                var row = new Row { ["index"] = rows.Count, ["storage_key"] = silo.Key, ["selector"] = silo.Selector,
                    ["kind"] = silo.Key != null && silo.Key.StartsWith("plot:", StringComparison.Ordinal) ? "plot_silo" : "scene_storage",
                    ["type"] = silo.Type, ["loaded"] = silo.Loaded, ["position"] = Coordinates(silo.Position),
                    ["distance"] = silo.Distance, ["slot_count"] = slots.Count,
                    ["usable_slots"] = usable, ["locked_slots"] = slots.Count - usable,
                    ["total_items"] = total,
                    ["object_name"] = silo.ObjectName, ["parent_name"] = silo.ParentName, ["gate"] = silo.Gate,
                    ["writable"] = silo.Ready, ["slots"] = slots };
                // 已加载容器才能从 RegionMember / Region 包围盒拿到游戏自己的区域；未加载记 null。
                if (silo.Loaded) ToolsZone.AddZone(row, silo.Object, silo.Position);
                else { row["zone"] = null; row["zone_title"] = null; }
                rows.Add(row);
            }
            return new() { ["available"] = true, ["count"] = rows.Count, ["items"] = rows };
        }

        internal static Row List(Row args) => ToolsNavigation.WithScene(raw =>
        {
            var scene = (SceneContext)raw;
            var pos = scene.Player?.transform.position;
            var result = Inventory(Warehouses(scene, pos));
            result["player_position"] = Coordinates(pos);
            var zone = scene.PlayerZoneTracker?.GetCurrentZone();
            result["player_zone"] = zone?.ReferenceId;
            result["player_zone_title"] = zone == null ? null : Il2CppUtil.Localized(zone.Name);
            return result;
        });

        static Row Describe(Move m, AmmoSlot source, Silo silo) => new()
        {
            ["id"] = Id(source), ["name"] = source.Id.name, ["source_slot"] = m.Source, ["move"] = m.Count,
            ["remaining_in_source"] = m.Remaining, ["storage"] = m.Warehouse, ["storage_key"] = silo.Key,
            ["loaded"] = silo.Loaded, ["storage_type"] = silo.Type, ["storage_slot"] = m.Slot,
            ["storage_position"] = Coordinates(silo.Position), ["mode"] = "metadata"
        };

        internal static Row Store(Row args) => ToolsNavigation.WithScene(raw =>
        {
            var scene = (SceneContext)raw;
            var bag = scene.PlayerState?.Ammo;
            if (bag?.Slots == null) return new() { ["available"] = false, ["reason"] = "ammo_unavailable" };
            var silos = Warehouses(scene, scene.Player?.transform.position);
            if (silos.Count == 0) return new() { ["available"] = false, ["reason"] = "no_storage_found" };
            string target = Convert.ToString(args.GetValueOrDefault("storage", ""));
            int only = -1;
            if (target.Length > 0)
            {
                if (!int.TryParse(target, out only)) only = silos.FindIndex(s => s.Key == target);
                if (only < 0 || only >= silos.Count) return new() { ["available"] = false, ["reason"] = "bad_storage" };
                if (!silos[only].Ready) return new() { ["available"] = false, ["reason"] = "slot_rules_unavailable" };
            }
            bool preview = (bool)args.GetValueOrDefault("dry_run", true);
            var unplaced = new List<Row>();
            var moves = Plan(bag.Slots.ToArray(), silos, only, Convert.ToString(args.GetValueOrDefault("item", "")), unplaced);
            var plan = moves.Select(m => Describe(m, bag.Slots[m.Source], silos[m.Warehouse])).ToList();
            var result = new Row { ["available"] = true, ["dry_run"] = preview, ["storage_count"] = silos.Count,
                ["plan_count"] = moves.Count, ["plan"] = plan, ["unplaced"] = unplaced };
            if (preview) return result;
            var completed = new List<Row>();
            var failures = new List<Row>(unplaced);
            for (int i = 0; i < moves.Count; ++i)
            {
                var m = moves[i];
                try
                {
                    int moved = Transfer(bag, m, silos[m.Warehouse]);
                    var row = new Row(plan[i]) { ["moved"] = moved, ["source_count_after"] = bag.Slots[m.Source].Count };
                    completed.Add(row);
                    if (moved != m.Count) throw new InvalidOperationException("partial_deposit");
                }
                catch (Exception e)
                {
                    failures.Add(new Row(plan[i]) { ["error"] = e.GetBaseException().Message });
                    break;
                }
            }
            result["moved"] = completed;
            result["moved_count"] = completed.Count;
            result["failures"] = failures;
            result["vacpack_after"] = bag.Slots.Where(s => s?.Id != null && s.Count > 0)
                .Select(s => new Row { ["id"] = Id(s), ["name"] = s.Id.name, ["count"] = s.Count }).ToList();
            result["storage_after"] = Inventory(silos);
            return result;
        });

        // Direct CLI RPC. An older bridge rejects the method before any inventory mutation.
        internal static Row MoveAmount(Row args) => ToolsNavigation.WithScene(raw =>
        {
            string direction = Convert.ToString(args.GetValueOrDefault("direction"));
            string item = Convert.ToString(args.GetValueOrDefault("item"))?.Trim();
            string selector = Convert.ToString(args.GetValueOrDefault("storage"));
            if (direction is not ("store" or "take" or "transfer") || string.IsNullOrEmpty(item) || int.TryParse(item, out _))
                return new() { ["available"] = false, ["reason"] = "bad_item_or_direction" };
            string quantity = Convert.ToString(args.GetValueOrDefault("quantity"));
            bool all = quantity == "all";
            int total = 0;
            if (!all && (!int.TryParse(quantity, out total) || total <= 0))
                return new() { ["available"] = false, ["reason"] = "bad_quantity" };
            var scene = (SceneContext)raw;
            var warehouses = Warehouses(scene, scene.Player?.transform.position);
            var warehouse = warehouses.Find(s => s.Selector == selector);
            if (warehouse == null) return new() { ["available"] = false, ["reason"] = "storage_not_found" };
            if (!warehouse.Ready) return new() { ["available"] = false, ["reason"] = "slot_rules_unavailable" };
            bool preview = (bool)args.GetValueOrDefault("dry_run", true);
            int? limit = all ? null : total;
            if (direction == "transfer")
            {
                string from = Convert.ToString(args.GetValueOrDefault("from"));
                var other = warehouses.Find(s => s.Selector == from);
                if (other == null) return new() { ["available"] = false, ["reason"] = "source_storage_not_found" };
                if (other == warehouse) return new() { ["available"] = false, ["reason"] = "same_storage" };
                if (!other.Ready) return new() { ["available"] = false, ["reason"] = "slot_rules_unavailable" };
                return MoveExact(other, warehouse, item, limit, preview);
            }
            var bag = scene.PlayerState?.Ammo;
            if (bag?.Slots == null) return new() { ["available"] = false, ["reason"] = "ammo_unavailable" };
            var slots = bag.Slots.ToArray();
            var pack = new Silo { Manager = bag, Live = slots, Rules = slots };
            var source = direction == "store" ? pack : warehouse;
            var target = direction == "store" ? warehouse : pack;
            return MoveExact(source, target, item, limit, preview);
        });

        static int ResolveSilo(List<Silo> silos, string selector)
        {
            if (string.IsNullOrEmpty(selector)) return -1;
            if (int.TryParse(selector, out int index) && index >= 0 && index < silos.Count) return index;
            return silos.FindIndex(s => s.Key == selector);
        }

        internal static Row Consolidate(Row args) => ToolsNavigation.WithScene(raw =>
        {
            var scene = (SceneContext)raw;
            string selector = Convert.ToString(args.GetValueOrDefault("storage", ""));
            var silos = Warehouses(scene, scene.Player?.transform.position)
                .Where(s => s.Key != null && (selector.Length == 0 || s.Key == selector)).ToList();
            if (selector.Length > 0 && silos.Count == 0)
                return new() { ["available"] = false, ["reason"] = "plot_storage_not_found" };
            var groups = silos.Where(s => s.Ready).GroupBy(s =>
            {
                string zone = s.Loaded ? ToolsZone.Of(s.Object, s.Position)?.ReferenceId : null;
                // Loaded silos can share a region. A silo with an unknown region stays independent.
                return (Known: !string.IsNullOrEmpty(zone), Id: string.IsNullOrEmpty(zone) ? s.Key : zone);
            });
            var result = ConsolidateStacks(groups, (bool)args.GetValueOrDefault("dry_run", true));
            result["checked_silos"] = silos.Count;
            result["skipped_silos"] = silos.Where(s => !s.Ready).Select(s => s.Key).ToArray();
            return result;
        });

        // 仓库之间直接搬运：from → to，不必经过背包。
        internal static Row TransferBetween(Row args) => ToolsNavigation.WithScene(raw =>
        {
            var scene = (SceneContext)raw;
            var silos = Warehouses(scene, scene.Player?.transform.position);
            if (silos.Count < 2)
                return new() { ["available"] = false, ["reason"] = "need_two_storages", ["storage_count"] = silos.Count };
            string fromArg = Convert.ToString(args.GetValueOrDefault("from", ""));
            string toArg = Convert.ToString(args.GetValueOrDefault("to", ""));
            int fromIdx = ResolveSilo(silos, fromArg);
            if (fromIdx < 0) return new() { ["available"] = false, ["reason"] = "bad_from" };
            int toIdx = ResolveSilo(silos, toArg);
            if (toArg.Length == 0)
            {
                toIdx = -1;
                for (int i = 0; i < silos.Count; ++i)
                    if (i != fromIdx && silos[i].Ready) { toIdx = i; break; }
            }
            if (toIdx < 0 || toIdx >= silos.Count) return new() { ["available"] = false, ["reason"] = "bad_to" };
            if (toIdx == fromIdx) return new() { ["available"] = false, ["reason"] = "same_storage" };
            var from = silos[fromIdx];
            var to = silos[toIdx];
            if (!from.Ready || !to.Ready) return new() { ["available"] = false, ["reason"] = "slot_rules_unavailable" };

            bool preview = (bool)args.GetValueOrDefault("dry_run", true);
            string item = Convert.ToString(args.GetValueOrDefault("item", ""));
            string countArg = Convert.ToString(args.GetValueOrDefault("count", ""));
            int countLimit = -1;
            if (countArg.Length > 0 && (!int.TryParse(countArg, out countLimit) || countLimit <= 0))
                return new() { ["available"] = false, ["reason"] = "bad_count" };

            // 目标槽剩余空间按计划递减，避免同一目标槽被两个来源撑爆。
            var space = new Dictionary<int, int>();
            for (int d = 0; d < to.Rules.Length; ++d)
                space[d] = to.Rules[d] == null ? 0 : to.Rules[d].MaxCount - to.Rules[d].Count;
            var moves = new List<(int Source, int Target, int Count)>();
            var unplaced = new List<Row>();
            for (int s = 0; s < from.Live.Length; ++s)
            {
                var slot = from.Live[s];
                if (slot?.Id == null || slot.Count <= 0) continue;
                if (item.Length > 0 && !Matches(item, s, slot)) continue;
                int left = countLimit > 0 ? Math.Min(countLimit, slot.Count) : slot.Count;
                string id = Id(slot);
                for (int d = 0; d < to.Rules.Length && left > 0; ++d)
                {
                    var rule = to.Rules[d];
                    if (space[d] <= 0) continue;
                    if (!to.Unlocked(d) || !to.Manager.CouldAddToSlot(slot.Metadata, d, false)) continue;
                    if (rule.Count > 0 && (Id(rule) != id || rule.Appearance != slot.Appearance)) continue;
                    int take = Math.Min(left, space[d]);
                    if (take <= 0) continue;
                    moves.Add((s, d, take));
                    space[d] -= take;
                    left -= take;
                }
                if (left > 0)
                    unplaced.Add(new() { ["source_slot"] = s, ["id"] = id, ["count"] = left, ["reason"] = "no_compatible_space" });
            }

            var plan = moves.Select(m => new Row
            {
                ["source_slot"] = m.Source,
                ["id"] = Id(from.Live[m.Source]),
                ["name"] = from.Live[m.Source].Id?.name,
                ["move"] = m.Count,
                ["target_slot"] = m.Target,
            }).ToList();
            var result = new Row
            {
                ["available"] = true,
                ["dry_run"] = preview,
                ["from"] = new Row { ["index"] = fromIdx, ["key"] = from.Key, ["container"] = from.Key ?? from.ObjectName },
                ["to"] = new Row { ["index"] = toIdx, ["key"] = to.Key, ["container"] = to.Key ?? to.ObjectName },
                ["plan"] = plan,
                ["plan_count"] = moves.Count,
                ["unplaced"] = unplaced,
                ["note"] = "仓库之间直接搬运，不经过背包；按原生槽位规则和剩余空间分配",
            };
            if (preview) return result;

            var completed = new List<Row>();
            var failures = new List<Row>(unplaced);
            foreach (var move in moves)
            {
                try
                {
                    var src = from.Live[move.Source];
                    var dst = to.Live[move.Target];
                    var rule = to.Rules[move.Target];
                    var beforeSrc = new Contents(src);
                    var beforeDst = new Contents(dst);
                    if (beforeSrc.Count < move.Count
                        || !to.Manager.CouldAddToSlot(beforeSrc.Metadata, move.Target, false))
                        throw new InvalidOperationException("inventory_changed");
                    to.Manager.MaybeAddToSpecificSlot(beforeSrc.Metadata, move.Target, move.Count, false);
                    int added = rule.Count - beforeDst.Count;
                    if (added <= 0 || added > move.Count)
                        throw new InvalidOperationException("native_deposit_mismatch");
                    rule.Appearance = beforeSrc.Appearance;
                    if (dst.Pointer != rule.Pointer) new Contents(rule).Restore(dst);
                    int sourceTarget = beforeSrc.Count - added;
                    src.Count = sourceTarget;
                    if (sourceTarget == 0) src.Clear();
                    if (src.Count != sourceTarget || rule.Count != beforeDst.Count + added)
                        throw new InvalidOperationException("transfer_readback_mismatch");
                    completed.Add(new Row
                    {
                        ["source_slot"] = move.Source,
                        ["target_slot"] = move.Target,
                        ["id"] = Id(rule),
                        ["name"] = rule.Id?.name,
                        ["moved"] = added,
                        ["source_count_after"] = src.Count,
                        ["target_count_after"] = rule.Count,
                    });
                }
                catch (Exception e)
                {
                    failures.Add(new Row { ["source_slot"] = move.Source, ["target_slot"] = move.Target,
                        ["error"] = e.GetBaseException().Message });
                    break;
                }
            }
            result["moved"] = completed;
            result["moved_count"] = completed.Count;
            result["failures"] = failures;
            result["storage_after"] = Inventory(silos);
            return result;
        });
    }
}
