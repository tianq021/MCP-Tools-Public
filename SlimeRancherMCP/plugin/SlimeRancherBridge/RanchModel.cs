// RanchModel.cs —— 跨区域仓库读写（数据模型层，不需要加载区域）
//
//   sr_model_storage   读取任意地块的仓库内容（未加载区域也能读；LandPlotModel.siloAmmo）
//   sr_model_move      在仓库之间搬运物品（未加载区域之间也能搬）
//
// 原理：GameModel.AllLandPlots() 返回全部 LandPlotModel（含未加载），仓库内容存在
// LandPlotModel.siloAmmo[StorageType] 的 AmmoModel.slots 里；区域加载时游戏会按模型重建
// 仓库对象（SiloStorage.SetModel），所以改模型后加载即一致。
//
// 安全策略：
//   - 两边都未加载 → 纯模型读写；
//   - 某一边已加载 → 优先调用已加载 SiloStorage 的正常接口（Ammo 与模型双向同步）。
using System;
using System.Collections.Generic;
using System.Linq;
using MonomiPark.SlimeRancher.DataModel;
using UnityEngine;
using Object = UnityEngine.Object;

namespace SlimeRancherBridge
{
    static partial class RanchTools
    {
        static void RegisterModelStorage(BridgeServer server)
        {
            Read(server, "sr_model_storage", ModelStorage);
            Action(server, "sr_model_move", ModelMove);
        }

        // ---------------- sr_model_storage ----------------

        static Dictionary<string, object> ModelStorage(SceneContext s, Dictionary<string, object> args)
        {
            string plotFilter = Arg(args, "plot_id");
            string typeFilter = Arg(args, "storage_type");
            bool includeEmpty = Flag(args, "include_empty", true);

            var loaded = new HashSet<string>(StringComparer.Ordinal);
            foreach (var loc in Object.FindObjectsOfType<LandPlotLocation>())
            {
                if (loc != null && !string.IsNullOrEmpty(loc.id)) loaded.Add(loc.id);
            }

            var plots = new List<object>();
            foreach (var pair in s.GameModel.AllLandPlots().OrderBy(x => x.Key, StringComparer.Ordinal))
            {
                if (!Includes(pair.Key, plotFilter)) continue;
                var model = pair.Value;
                if (model == null) continue;

                var stores = new List<object>();
                foreach (var kv in model.siloAmmo)
                {
                    string storeName = kv.Key.ToString();
                    if (!Includes(storeName, typeFilter)) continue;
                    stores.Add(ModelStorageRow(kv.Key, kv.Value, includeEmpty));
                }

                plots.Add(Row(
                    "plot_id", pair.Key,
                    "plot_type", model.typeId.ToString(),
                    "loaded", loaded.Contains(pair.Key),
                    "storages", stores.ToArray()));
            }

            return Row("total", plots.Count, "plots", plots.ToArray());
        }

        static Dictionary<string, object> ModelStorageRow(SiloStorage.StorageType typeId, AmmoModel ammo, bool includeEmpty)
        {
            var slots = new List<object>();
            int total = 0;
            if (ammo != null && ammo.slots != null)
            {
                for (int i = 0; i < ammo.slots.Length; i++)
                {
                    var slot = ammo.slots[i];
                    int count = slot == null ? 0 : slot.count;
                    total += count;
                    if (!includeEmpty && count <= 0) continue;
                    slots.Add(Row("slot", i,
                        "item_id", slot == null ? "NONE" : slot.id.ToString(),
                        "count", count));
                }
            }
            return Row("storage_type", typeId.ToString(), "usable_slots", ammo == null ? 0 : ammo.usableSlots,
                "total_count", total, "slots", slots.ToArray());
        }

        // ---------------- sr_model_move ----------------

        static Dictionary<string, object> ModelMove(SceneContext s, Dictionary<string, object> args)
        {
            string fromPlot = Arg(args, "from_plot");
            string toPlot = Arg(args, "to_plot");
            int fromSlot = Number(args, "from_slot", -1);
            int toSlot = Number(args, "to_slot", -1);
            int count = Number(args, "count", 0);

            if (fromPlot.Length == 0 || toPlot.Length == 0)
                return Fail("INVALID_ARGS", "from_plot and to_plot are required (plot_id from sr_model_storage).");
            if (!IncludePlot(s.GameModel, fromPlot)) return Fail("TARGET_UNAVAILABLE", "Source plot model not found.");
            if (!IncludePlot(s.GameModel, toPlot)) return Fail("TARGET_UNAVAILABLE", "Destination plot model not found.");
            if (fromSlot < 0 || toSlot < 0) return Fail("INVALID_ARGS", "from_slot and to_slot are required.");
            if (count <= 0) return Fail("INVALID_ARGS", "count must be positive.");

            SiloStorage.StorageType fromType;
            SiloStorage.StorageType toType;
            if (!NamedEnum(Arg(args, "from_store"), out fromType))
                return Fail("INVALID_STORAGE", "from_store must be NON_SLIMES, PLORT, FOOD, CRAFTING or ELDER.");
            if (!NamedEnum(Arg(args, "to_store"), out toType))
                return Fail("INVALID_STORAGE", "to_store must be NON_SLIMES, PLORT, FOOD, CRAFTING or ELDER.");

            if (Same(fromPlot, toPlot) && fromSlot == toSlot && fromType == toType)
                return Fail("INVALID_ARGS", "Source and destination are the same slot.");

            var srcModel = s.GameModel.GetLandPlotModel(fromPlot);
            var dstModel = s.GameModel.GetLandPlotModel(toPlot);
            AmmoModel srcAmmo, dstAmmo;
            if (!srcModel.siloAmmo.TryGetValue(fromType, out srcAmmo) || srcAmmo == null || srcAmmo.slots == null)
                return Fail("SOURCE_STORAGE_MISSING", "Source plot has no such storage (from_store).");
            if (!dstModel.siloAmmo.TryGetValue(toType, out dstAmmo) || dstAmmo == null || dstAmmo.slots == null)
                return Fail("TARGET_STORAGE_MISSING", "Destination plot has no such storage (to_store).");
            if (fromSlot >= srcAmmo.slots.Length)
                return Fail("INVALID_SLOT", "from_slot is out of range for the source storage.");
            if (toSlot >= dstAmmo.slots.Length)
                return Fail("INVALID_SLOT", "to_slot is out of range for the destination storage.");

            var srcSlot = srcAmmo.slots[fromSlot];
            var dstSlot = dstAmmo.slots[toSlot];
            int srcCount = srcSlot == null ? 0 : srcSlot.count;
            int dstCount = dstSlot == null ? 0 : dstSlot.count;
            if (srcCount <= 0 || srcSlot == null)
                return Fail("SOURCE_EMPTY", "The source slot is empty.");
            var item = srcSlot.id;
            if (count > srcCount)
                return Fail("NOT_ENOUGH", "The source slot does not contain the full count.");
            if (dstCount > 0 && dstSlot.id.ToString() != item.ToString())
                return Fail("TARGET_OCCUPIED", "The destination slot holds a different item.");
            if (dstCount > 0 && dstSlot.id != item)
                return Fail("TARGET_OCCUPIED", "The destination slot holds a different item.");

            var srcLoaded = LoadedStorage(fromPlot, fromType);
            var dstLoaded = LoadedStorage(toPlot, toType);

            int capacity = Capacity(dstLoaded, dstAmmo, toType, item, toSlot);
            if (dstCount + count > capacity)
                return Fail("TARGET_FULL", "The destination slot does not have space for the full count (capacity " + capacity + ").");

            Dictionary<string, object> result = Row(
                "item_id", item.ToString(),
                "count", count,
                "from_plot", fromPlot,
                "from_store", fromType.ToString(),
                "from_slot", fromSlot,
                "from_count_before", srcCount,
                "from_loaded", srcLoaded != null,
                "to_plot", toPlot,
                "to_store", toType.ToString(),
                "to_slot", toSlot,
                "to_count_before", dstCount,
                "to_loaded", dstLoaded != null,
                "capacity", capacity);
            if (Preview(args)) return result;

            // ---- 源：扣减（已加载走正常接口，未加载改模型） ----
            if (srcLoaded != null)
            {
                srcLoaded.GetRelevantAmmo().Decrement(fromSlot, count);
            }
            else
            {
                srcSlot.count -= count;
                if (srcSlot.count <= 0) srcAmmo.slots[fromSlot] = null;
            }

            // ---- 目标：增加（已加载走正常接口，未加载改模型） ----
            if (dstLoaded != null)
            {
                if (!dstLoaded.MaybeAddIdentifiable(item, toSlot, count))
                {
                    // 回滚源
                    if (srcLoaded != null) srcLoaded.GetRelevantAmmo().MaybeAddToSpecificSlot(item, null, fromSlot, count, true);
                    else
                    {
                        var again = srcAmmo.slots[fromSlot];
                        if (again == null) srcAmmo.slots[fromSlot] = new Ammo.Slot(item, count);
                        else again.count += count;
                    }
                    return Row("__error", BridgeServer.Error("TARGET_REJECTED", "The destination storage rejected the item; source was restored."));
                }
            }
            else
            {
                if (dstSlot == null || dstCount <= 0)
                {
                    dstAmmo.slots[toSlot] = new Ammo.Slot(item, count);
                }
                else
                {
                    dstSlot.count += count;
                }
            }

            // ---- 回读 ----
            result["from_count_after"] = ReadCount(s.GameModel, fromPlot, fromType, fromSlot);
            result["to_count_after"] = ReadCount(s.GameModel, toPlot, toType, toSlot);
            return result;
        }

        static bool IncludePlot(GameModel model, string plotId)
        {
            return model.GetLandPlotModel(plotId) != null;
        }

        static int ReadCount(GameModel model, string plotId, SiloStorage.StorageType type, int slot)
        {
            var plot = model.GetLandPlotModel(plotId);
            AmmoModel ammo;
            if (plot == null || !plot.siloAmmo.TryGetValue(type, out ammo) || ammo == null || ammo.slots == null)
                return -1;
            if (slot < 0 || slot >= ammo.slots.Length) return -1;
            var s = ammo.slots[slot];
            return s == null ? 0 : s.count;
        }

        static SiloStorage LoadedStorage(string plotId, SiloStorage.StorageType type)
        {
            SiloStorage best = null;
            foreach (var storage in Object.FindObjectsOfType<SiloStorage>())
            {
                if (storage == null || !storage.isActiveAndEnabled || storage.type != type) continue;
                var loc = storage.GetComponentInParent<LandPlotLocation>();
                if (loc == null || !Same(loc.id, plotId)) continue;
                best = storage;
                break;
            }
            return best;
        }

        static int Capacity(SiloStorage loaded, AmmoModel ammo, SiloStorage.StorageType type, Identifiable.Id item, int slot)
        {
            if (loaded != null)
            {
                try
                {
                    return loaded.GetRelevantAmmo().GetSlotMaxCount(item, slot);
                }
                catch (Exception)
                {
                }
            }
            if (ammo != null)
            {
                try
                {
                    return ammo.GetSlotMaxCount(item, slot);
                }
                catch (Exception)
                {
                }
            }
            switch (type)
            {
                case SiloStorage.StorageType.PLORT: return 100;
                case SiloStorage.StorageType.NON_SLIMES: return 300;
                case SiloStorage.StorageType.FOOD: return 60;
                case SiloStorage.StorageType.CRAFTING: return 100;
                case SiloStorage.StorageType.ELDER: return 100;
            }
            return 100;
        }
    }
}
