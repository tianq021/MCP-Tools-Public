// RanchExtras —— 扩展工具（备份/存档、围栏与史莱姆视角、仓口切换、批量收集、自动投喂、总览）
// 同一 partial class：可直接复用 RanchTools / RanchActions 的私有 helper（Read/Action/Row/Fail/Page/Slots/...）
// 注意：C# 5 语法（无字符串插值、无 ?.、无表达式体成员）
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using MonomiPark.SlimeRancher.DataModel;
using MonomiPark.SlimeRancher.Regions;
using UnityEngine;
using Object = UnityEngine.Object;

namespace SlimeRancherBridge
{
    static partial class RanchTools
    {
        const string BackupRoot = @"E:\SlimeRancherMCP\backups";

        // ---------------------------------------------------------------- 注册

        static void RegisterExtras(BridgeServer server)
        {
            // 备份不依赖游戏场景，直接注册（主菜单/未读档也能用）
            server.Register("sr_backup", Backup, false);
            Read(server, "sr_save", SaveNow, true);
            Action(server, "sr_silo_port", SiloPort);
            Read(server, "sr_slimes", Slimes);
            Read(server, "sr_corrals", Corrals);
            Action(server, "sr_collect_all", CollectAll);
            Action(server, "sr_feed", Feed);
            Read(server, "sr_overview", Overview);
        }

        // ---------------------------------------------------------------- 通用小工具

        static string SaveDir()
        {
            return Application.persistentDataPath;
        }

        static string[] SaveFiles()
        {
            try
            {
                var dir = SaveDir();
                if (!Directory.Exists(dir)) return new string[0];
                var files = Directory.GetFiles(dir, "*.sav");
                Array.Sort(files, StringComparer.Ordinal);
                return files;
            }
            catch
            {
                return new string[0];
            }
        }

        static string Sha256(string path)
        {
            try
            {
                using (var sha = SHA256.Create())
                using (var stream = File.OpenRead(path))
                {
                    byte[] hash = sha.ComputeHash(stream);
                    var sb = new StringBuilder(hash.Length * 2);
                    for (int i = 0; i < hash.Length; i++) sb.Append(hash[i].ToString("x2"));
                    return sb.ToString();
                }
            }
            catch (Exception e)
            {
                return "ERROR:" + e.GetType().Name;
            }
        }

        static object[] SaveRows(bool withHash)
        {
            var rows = new List<Dictionary<string, object>>();
            foreach (var file in SaveFiles())
            {
                var info = new FileInfo(file);
                var row = Row("file", Path.GetFileName(file), "size", info.Length,
                    "modified", info.LastWriteTime.ToString("yyyy-MM-dd HH:mm:ss"));
                if (withHash) row["sha256"] = Sha256(file);
                rows.Add(row);
            }
            return rows.ToArray();
        }

        static Dictionary<string, object> Backup(Dictionary<string, object> args)
        {
            string prefix = Arg(args, "label");
            if (prefix.Length == 0) prefix = "auto";
            prefix = prefix.Replace("\\", "_").Replace("/", "_").Replace(":", "_");
            string stamp = DateTime.Now.ToString("yyyyMMdd_HHmmss");
            string dest = Path.Combine(BackupRoot, prefix + "_" + stamp);
            var files = SaveFiles();
            if (files.Length == 0)
                return Fail("NO_SAVES", "No .sav files found in " + SaveDir());
            Directory.CreateDirectory(dest);
            var copied = new List<Dictionary<string, object>>();
            foreach (var file in files)
            {
                string target = Path.Combine(dest, Path.GetFileName(file));
                File.Copy(file, target, true);
                copied.Add(Row("file", Path.GetFileName(file), "size", new FileInfo(target).Length,
                    "sha256", Sha256(target)));
            }
            return Row("backup_dir", dest, "files", copied.ToArray(), "count", copied.Count,
                "source_dir", SaveDir());
        }

        static Dictionary<string, object> SaveNow(SceneContext scene, Dictionary<string, object> args)
        {
            var result = Row("save_dir", SaveDir(), "files_before", SaveRows(false));
            if (Preview(args))
            {
                result["note"] = "dry_run: 仅列出当前磁盘存档，未触发保存。执行请传 dry_run=false + request_id。";
                return result;
            }
            var gameContext = SRSingleton<GameContext>.Instance;
            var director = gameContext == null ? null : gameContext.AutoSaveDirector;
            if (director == null)
                return Fail("SAVE_UNAVAILABLE", "AutoSaveDirector 不可用（当前不在存档中？）");
            bool ok = director.SaveAllNow();
            result["ok"] = ok;
            result["files_after"] = SaveRows(true);
            result["save_name"] = director.SavedGame == null ? null : director.SavedGame.GetName();
            return result;
        }

        // ---------------------------------------------------------------- 筒仓仓口切换

        // 筒仓相关：包含休眠（inactive）对象，因为仓口设置是持久状态，不需要地块处于活动区域
        static IEnumerable<SiloStorage> AllStorages()
        {
            foreach (var storage in Resources.FindObjectsOfTypeAll<SiloStorage>())
            {
                if (storage == null || storage.gameObject == null) continue;
                if (!storage.gameObject.scene.IsValid()) continue;
                yield return storage;
            }
        }

        static SiloStorageActivator[] SiloPortsAll(SiloStorage storage)
        {
            var plot = storage.GetComponentInParent<LandPlot>();
            if (plot == null || plot.typeId != LandPlot.Id.SILO) return null;
            return plot.GetComponentsInChildren<SiloStorageActivator>(true)
                .Where(port => port != null && port.siloCatcher != null && port.siloSlotUIs != null)
                .OrderBy(port => port.activatorIdx).ToArray();
        }

        static Dictionary<string, object> SiloPort(SceneContext scene, Dictionary<string, object> args)
        {
            int slot = Number(args, "slot", -1);
            int storageId = Number(args, "storage_id", 0);
            string plotId = Arg(args, "plot_id");
            SiloStorage storage = null;
            if (storageId != 0)
            {
                storage = AllStorages().FirstOrDefault(x => x.GetInstanceID() == storageId);
            }
            else if (plotId.Length != 0)
            {
                storage = AllStorages().FirstOrDefault(x => Same(PlotId(x), plotId));
            }
            else
            {
                return Fail("BAD_ARGS", "提供 plot_id 或 storage_id（来自 sr_storage）。");
            }
            if (storage == null)
                return Fail("TARGET_UNAVAILABLE", "该筒仓不在当前存档内存里，或 storage_id 已过期（切区域/读档后会变）。");
            bool hibernated = !storage.gameObject.activeInHierarchy;
            var ports = SiloPortsAll(storage);
            if (ports == null)
                return Fail("NOT_A_SILO", "该容器不是筒仓（没有仓口）。");
            if (ports.Length == 0)
                return Fail("NO_PORT", "该筒仓尚未解锁任何仓口。");

            var accessible = ports.SelectMany(PortSlots).Distinct().OrderBy(x => x).ToArray();
            if (slot < 0)
            {
                return Row("storage_id", storage.GetInstanceID(), "plot_id", PlotId(storage),
                    "accessible_slots", accessible,
                    "ports", ports.Select(p => Row("port", p.activatorIdx, "active_slot", p.siloCatcher == null ? -1 : p.siloCatcher.slotIdx,
                        "slots", PortSlots(p).ToArray())).ToArray());
            }
            if (!accessible.Contains(slot))
                return Fail("INVALID_SLOT", "该槽不可通过已解锁仓口访问。可用槽: " + string.Join(",", accessible.Select(x => x.ToString()).ToArray()));

            var port = ports.FirstOrDefault(p => PortSlots(p).Contains(slot));
            if (port == null || port.siloCatcher == null || port.siloSlotUIs == null)
                return Fail("TARGET_UNAVAILABLE", "找不到该槽对应的仓口组件。");
            int uiIndex = -1;
            for (int i = 0; i < port.siloSlotUIs.Count; i++)
                if (port.siloSlotUIs[i] != null && port.siloSlotUIs[i].slotIdx == slot) { uiIndex = i; break; }
            if (uiIndex < 0)
                return Fail("TARGET_UNAVAILABLE", "仓口 UI 列表里找不到该槽位。");
            var model = scene.GameModel.GetLandPlotModel(PlotId(storage));
            if (model == null || model.siloStorageIndices == null || port.activatorIdx < 0 || port.activatorIdx >= model.siloStorageIndices.Length)
                return Fail("TARGET_UNAVAILABLE", "筒仓模型不可用。");

            int beforeSlot = port.siloCatcher.slotIdx;
            int beforeIndex = model.siloStorageIndices[port.activatorIdx];
            var result = Row("storage_id", storage.GetInstanceID(), "plot_id", PlotId(storage),
                "port", port.activatorIdx, "slot_before", beforeSlot, "slot_after_target", slot,
                "ui_index_before", beforeIndex, "ui_index_after_target", uiIndex,
                "accessible_slots", accessible);
            if (Preview(args)) return result;

            model.siloStorageIndices[port.activatorIdx] = uiIndex;
            port.siloCatcher.slotIdx = slot;
            if (port.siloSlotAnimator != null && port.siloSlotAnimator.gameObject.activeInHierarchy)
                port.siloSlotAnimator.SetInteger("slot", uiIndex);
            result["slot_after"] = port.siloCatcher.slotIdx;
            result["note"] = "仓口已切换；游戏里对着仓口看/取就会显示该槽内容。";
            return result;
        }

        // ---------------------------------------------------------------- 史莱姆清单

        static bool IsAnySlime(Identifiable.Id id)
        {
            return Identifiable.IsSlime(id) || Identifiable.IsLargo(id) || Identifiable.IsGordo(id);
        }

        static List<LandPlotLocation> CorralLocations(SceneContext scene)
        {
            var list = new List<LandPlotLocation>();
            foreach (var location in Object.FindObjectsOfType<LandPlotLocation>())
            {
                if (location == null) continue;
                var model = scene.GameModel.GetLandPlotModel(location.id);
                if (model == null || model.typeId != LandPlot.Id.CORRAL) continue;
                list.Add(location);
            }
            return list;
        }

        static string NearestCorral(List<LandPlotLocation> corrals, Vector3 pos, out double distance)
        {
            distance = double.MaxValue;
            string best = null;
            for (int i = 0; i < corrals.Count; i++)
            {
                var location = corrals[i];
                if (location == null) continue;
                double d = Vector3.Distance(pos, location.transform.position);
                if (d < distance) { distance = d; best = location.id; }
            }
            return best;
        }

        static double PlayerDistance(SceneContext scene, Vector3 pos)
        {
            return scene.Player == null ? -1 : Math.Round(Vector3.Distance(scene.Player.transform.position, pos), 2);
        }

        static Dictionary<string, object> Slimes(SceneContext scene, Dictionary<string, object> args)
        {
            int radius = Number(args, "radius", 0);
            string plotFilter = Arg(args, "plot_id");
            bool includeFeral = Flag(args, "include_feral", true);
            var actors = scene.GameModel.AllActors();
            var corrals = CorralLocations(scene);
            var rows = new List<Dictionary<string, object>>();
            var counts = new Dictionary<string, int>(StringComparer.Ordinal);
            var skipped = 0;
            foreach (var pair in actors)
            {
                var actor = pair.Value;
                if (actor == null) { skipped++; continue; }
                Identifiable.Id ident;
                try { ident = actor.ident; } catch { skipped++; continue; }
                if (!IsAnySlime(ident)) continue;
                var slime = actor as SlimeModel;
                bool feral = slime != null && slime.isFeral;
                if (!includeFeral && feral) continue;
                Vector3 pos;
                try { pos = actor.GetPos(); } catch { skipped++; continue; }
                double playerDist = PlayerDistance(scene, pos);
                if (radius > 0 && (playerDist < 0 || playerDist > radius)) continue;
                string corralId = null;
                double corralDist = double.MaxValue;
                if (plotFilter.Length != 0 || radius == 0)
                {
                    corralId = NearestCorral(corrals, pos, out corralDist);
                    // 只有确实在围栏附近（<=25）才算归属，避免把野外史莱姆硬绑到最近的围栏
                    if (corralDist > 25) corralId = null;
                    if (plotFilter.Length != 0 && !Same(corralId, plotFilter)) continue;
                }
                var row = Row("actor_id", pair.Key, "item_id", ident.ToString(),
                    "name", Identifiable.GetName(ident, false),
                    "position", Position(pos),
                    "player_distance", playerDist < 0 ? null : (object)playerDist,
                    "region_set", actor.currRegionSetId.ToString(),
                    "nearest_corral", corralId,
                    "nearest_corral_distance", corralId == null ? null : (object)Math.Round(corralDist, 2),
                    "feral", feral,
                    "glitch", slime != null && slime.isGlitch);
                if (slime != null)
                {
                    row["hunger"] = Math.Round(slime.emotionHunger.currVal, 3);
                    row["agitation"] = Math.Round(slime.emotionAgitation.currVal, 3);
                    row["fear"] = Math.Round(slime.emotionFear.currVal, 3);
                }
                rows.Add(row);
                string key = ident.ToString();
                counts[key] = counts.ContainsKey(key) ? counts[key] + 1 : 1;
            }
            var summary = counts.OrderByDescending(x => x.Value).Take(15)
                .Select(x => Row("item_id", x.Key, "count", x.Value)).ToArray();
            var page = Page("slimes", rows, args, 60);
            page["summary"] = summary;
            page["total_in_memory"] = rows.Count;
            page["skipped_actors"] = skipped;
            return page;
        }

        // ---------------------------------------------------------------- 围栏体检

        static Dictionary<string, object> Corrals(SceneContext scene, Dictionary<string, object> args)
        {
            string filter = Arg(args, "plot_id");
            int radius = Math.Min(60, Math.Max(3, Number(args, "radius", 14)));
            var locations = new Dictionary<string, LandPlotLocation>(StringComparer.Ordinal);
            foreach (var location in Object.FindObjectsOfType<LandPlotLocation>())
                if (location != null && !locations.ContainsKey(location.id)) locations[location.id] = location;

            var collectors = Object.FindObjectsOfType<PlortCollector>().Where(x => x.isActiveAndEnabled).ToArray();
            var feeders = Object.FindObjectsOfType<SlimeFeeder>().Where(x => x.isActiveAndEnabled).ToArray();
            var storages = ActiveStorage().ToArray();
            var actors = scene.GameModel.AllActors();
            var loose = new List<KeyValuePair<Identifiable.Id, Vector3>>();
            foreach (var entry in CellDirector.GetAllRegistered())
            {
                try
                {
                    if (entry.GameObject == null) continue;
                    if (!Identifiable.IsPlort(entry.Id)) continue;
                    loose.Add(new KeyValuePair<Identifiable.Id, Vector3>(entry.Id, entry.GameObject.transform.position));
                }
                catch { }
            }

            var rows = new List<Dictionary<string, object>>();
            foreach (var pair in scene.GameModel.AllLandPlots().OrderBy(x => x.Key, StringComparer.Ordinal))
            {
                var model = pair.Value;
                if (model == null || model.typeId != LandPlot.Id.CORRAL) continue;
                if (filter.Length != 0 && !Same(filter, pair.Key)) continue;
                LandPlotLocation location;
                locations.TryGetValue(pair.Key, out location);
                var row = Row("plot_id", pair.Key, "loaded", location != null,
                    "position", location == null ? null : Position(location.transform.position),
                    "zone", location == null ? null : Zone(location.transform),
                    "upgrades", model.upgrades.Select(x => x.ToString()).OrderBy(x => x).ToArray(),
                    "feeding_operations_remaining", model.remainingFeedOperations,
                    "feeder_speed", model.feederCycleSpeed.ToString());
                var warnings = new List<string>();
                int slimeCount = 0, feralCount = 0, hungryCount = 0;
                var slimeTypes = new Dictionary<string, int>(StringComparer.Ordinal);
                var looseTypes = new Dictionary<string, int>(StringComparer.Ordinal);
                int looseActive = 0;
                if (location != null)
                {
                    Vector3 center = location.transform.position;
                    foreach (var actorPair in actors)
                    {
                        var actor = actorPair.Value;
                        if (actor == null) continue;
                        Identifiable.Id ident;
                        Vector3 pos;
                        try { ident = actor.ident; pos = actor.GetPos(); } catch { continue; }
                        if (!IsAnySlime(ident)) continue;
                        if (Vector3.Distance(pos, center) > radius) continue;
                        slimeCount++;
                        string key = ident.ToString();
                        slimeTypes[key] = slimeTypes.ContainsKey(key) ? slimeTypes[key] + 1 : 1;
                        var slime = actor as SlimeModel;
                        if (slime == null) continue;
                        if (slime.isFeral) feralCount++;
                        if (slime.emotionHunger.currVal >= 0.85f) hungryCount++;
                    }
                    foreach (var item in loose)
                    {
                        if (Vector3.Distance(item.Value, center) > radius) continue;
                        string key = item.Key.ToString();
                        looseTypes[key] = looseTypes.ContainsKey(key) ? looseTypes[key] + 1 : 1;
                        looseActive++;
                    }
                    row["slime_count"] = slimeCount;
                    row["slime_types"] = slimeTypes.OrderByDescending(x => x.Value)
                        .Select(x => Row("item_id", x.Key, "count", x.Value)).Take(10).ToArray();
                    row["feral_count"] = feralCount;
                    row["hungry_slime_count"] = hungryCount;
                    row["loose_plorts"] = looseTypes.OrderByDescending(x => x.Value)
                        .Select(x => Row("item_id", x.Key, "count", x.Value)).ToArray();
                    row["loose_plort_count"] = looseActive;
                    var plotCollectors = collectors.Where(x => Same(PlotId(x), pair.Key)).ToArray();
                    row["collectors"] = plotCollectors.Select(x => Row("collector_id", x.GetInstanceID())).ToArray();
                    var plotFeeders = feeders.Where(x => Same(PlotId(x), pair.Key)).ToArray();
                    row["feeders"] = plotFeeders.Select(x => Row("feeder_id", x.GetInstanceID(),
                        "food_id", x.GetFoodId().ToString(), "food_count", x.GetFoodCount(),
                        "speed", x.GetFeedingCycleSpeed().ToString())).ToArray();
                    var plotStores = storages.Where(x => Same(PlotId(x), pair.Key))
                        .Select(x => Row("storage_id", x.GetInstanceID(), "storage_type", x.type.ToString(),
                            "slots", Slots(x.GetRelevantAmmo(), false).ToArray())).ToArray();
                    row["storages"] = plotStores;
                    if (plotFeeders.Length > 0 && plotFeeders.All(x => x.GetFoodCount() == 0)) warnings.Add("FEEDER_EMPTY");
                    if (slimeCount == 0) warnings.Add("NO_SLIMES_IN_RANGE");
                    if (hungryCount > 0) warnings.Add("HUNGRY_SLIMES");
                    if (feralCount > 0) warnings.Add("FERAL_SLIMES");
                    if (looseActive > 0) warnings.Add("PLORTS_ON_GROUND");
                    if (plotCollectors.Length == 0) warnings.Add("NO_COLLECTOR");
                }
                else
                {
                    warnings.Add("PLOT_NOT_LOADED");
                }
                row["warnings"] = warnings.ToArray();
                rows.Add(row);
            }
            return Row("corrals", rows.ToArray(), "total", rows.Count, "radius", radius);
        }

        // ---------------------------------------------------------------- 批量收集

        static Dictionary<string, object> CollectAll(SceneContext scene, Dictionary<string, object> args)
        {
            string filter = Arg(args, "plot_id");
            var collectors = Object.FindObjectsOfType<PlortCollector>()
                .Where(x => x.isActiveAndEnabled && (filter.Length == 0 || Same(PlotId(x), filter)))
                .OrderBy(x => Distance(scene, x.transform)).ToArray();
            var targets = collectors.Select(x =>
            {
                var storage = x.GetComponentInParent<SiloStorage>();
                var row = Row("collector_id", x.GetInstanceID(), "plot_id", PlotId(x),
                    "position", Position(x.transform.position), "distance", Distance(scene, x.transform));
                if (storage != null)
                {
                    row["storage_id"] = storage.GetInstanceID();
                    row["storage_slots_before"] = Slots(storage.GetRelevantAmmo(), false).ToArray();
                }
                return row;
            }).ToArray();
            var result = Row("collectors", targets, "total", targets.Length);
            if (Preview(args)) return result;
            var triggered = new List<Dictionary<string, object>>();
            foreach (var collector in collectors)
            {
                try
                {
                    collector.StartCollection();
                    triggered.Add(Row("collector_id", collector.GetInstanceID(), "plot_id", PlotId(collector)));
                }
                catch (Exception e)
                {
                    triggered.Add(Row("collector_id", collector.GetInstanceID(),
                        "error", e.GetType().Name + ": " + e.Message));
                }
            }
            result["triggered"] = triggered.ToArray();
            result["note"] = "吸附是动画过程；稍后用 sr_collectors 或 sr_storage 查看实际入库。";
            return result;
        }

        // ---------------------------------------------------------------- 自动投喂

        static Identifiable.Id BestFoodInStores(int excludeStorageId)
        {
            var totals = new Dictionary<Identifiable.Id, int>(Identifiable.idComparer);
            foreach (var storage in ActiveStorage())
            {
                if (storage.GetInstanceID() == excludeStorageId) continue;
                if (storage.type != SiloStorage.StorageType.FOOD) continue;
                var ammo = storage.GetRelevantAmmo();
                for (int i = 0; i < ammo.GetUsableSlotCount(); i++)
                {
                    var id = ammo.GetSlotName(i);
                    int count = ammo.GetSlotCount(i);
                    if (count <= 0 || id == Identifiable.Id.NONE) continue;
                    if (!Identifiable.IsFood(id)) continue;
                    totals[id] = totals.ContainsKey(id) ? totals[id] + count : count;
                }
            }
            if (totals.Count == 0) return Identifiable.Id.NONE;
            return totals.OrderByDescending(x => x.Value).First().Key;
        }

        static Dictionary<string, object> Feed(SceneContext scene, Dictionary<string, object> args)
        {
            int feederId = Number(args, "feeder_id", -1);
            string plotId = Arg(args, "plot_id");
            var feeders = Object.FindObjectsOfType<SlimeFeeder>().Where(x => x.isActiveAndEnabled).ToArray();
            var feeder = feeders.FirstOrDefault(x => feederId > 0
                ? x.GetInstanceID() == feederId
                : (plotId.Length != 0 && Same(PlotId(x), plotId)));
            if (feeder == null)
                return Fail("TARGET_UNAVAILABLE", "找不到已加载的喂食器（用 sr_feeders 拿 feeder_id）。");
            var target = feeder.GetComponentInParent<SiloStorage>();
            if (target == null || target.type != SiloStorage.StorageType.FOOD)
                return Fail("NOT_FEEDER_STORAGE", "该喂食器没有 FOOD 仓储。");
            int targetId = target.GetInstanceID();
            Identifiable.Id food;
            string foodArg = Arg(args, "item_id");
            if (foodArg.Length != 0)
            {
                if (!ParseItem(foodArg, out food)) return Fail("INVALID_ITEM", "用 sr_item_catalog 里的 food item_id。");
            }
            else
            {
                food = feeder.GetFoodId();
                if (food == Identifiable.Id.NONE) food = BestFoodInStores(targetId);
                if (food == Identifiable.Id.NONE)
                    return Fail("NO_FOOD", "喂食器为空且附近没有 FOOD 仓储；先用 sr_move 手动放食物，或传 item_id。");
            }
            if (!Identifiable.IsFood(food)) return Fail("INVALID_ITEM", food + " 不是食物。");
            Ammo targetAmmo = target.GetRelevantAmmo();
            int free = 0;
            int targetSlot = -1;
            for (int i = 0; i < targetAmmo.GetUsableSlotCount(); i++)
            {
                if (!targetAmmo.CouldAddToSlot(food, i, false)) continue;
                int space = FreeSpaceForItem(targetAmmo, food, i);
                if (space <= 0) continue;
                free += space;
                if (targetSlot < 0) targetSlot = i;
            }
            if (targetSlot < 0 || free <= 0)
                return Fail("DESTINATION_FULL", "该喂食器的 FOOD 仓储放不下 " + food + "。");
            int want = Number(args, "count", -1);
            if (want <= 0 || want > free) want = free;
            var result = Row("feeder_id", feeder.GetInstanceID(), "plot_id", PlotId(feeder),
                "food_id", food.ToString(), "target_storage_id", targetId,
                "target_slot", targetSlot, "free_space", free, "planned", want,
                "food_count_before", feeder.GetFoodCount());
            if (Preview(args)) return result;

            int remaining = want;
            var moved = new List<Dictionary<string, object>>();
            foreach (var storage in ActiveStorage()
                .Where(x => x.GetInstanceID() != targetId && x.type == SiloStorage.StorageType.FOOD)
                .OrderBy(x => Distance(scene, x.transform)))
            {
                if (remaining <= 0) break;
                Ammo ammo = storage.GetRelevantAmmo();
                for (int i = 0; i < ammo.GetUsableSlotCount() && remaining > 0; i++)
                {
                    if (ammo.GetSlotName(i) != food) continue;
                    int available = ammo.GetSlotCount(i);
                    if (available <= 0) continue;
                    int take = Math.Min(available, remaining);
                    var moveArgs = new Dictionary<string, object>(StringComparer.Ordinal);
                    moveArgs["item_id"] = food.ToString();
                    moveArgs["source_storage_id"] = storage.GetInstanceID();
                    moveArgs["source_slot"] = i;
                    moveArgs["count"] = take;
                    moveArgs["destination_storage_id"] = targetId;
                    moveArgs["destination_slot"] = targetSlot;
                    moveArgs["dry_run"] = false;
                    moveArgs["request_id"] = "feed-" + feeder.GetInstanceID() + "-" + storage.GetInstanceID() + "-" + i + "-" + DateTime.Now.Ticks;
                    var movedResult = Move(scene, moveArgs);
                    object errorObject;
                    if (movedResult.TryGetValue("__error", out errorObject))
                    {
                        moved.Add(Row("source_storage_id", storage.GetInstanceID(), "source_slot", i,
                            "error", errorObject));
                        continue;
                    }
                    moved.Add(Row("source_storage_id", storage.GetInstanceID(), "source_slot", i,
                        "destination_storage_id", targetId, "moved", take,
                        "destination_count_after", movedResult.ContainsKey("destination_count_after")
                            ? movedResult["destination_count_after"] : null));
                    remaining -= take;
                }
            }
            result["moved"] = moved.ToArray();
            result["moved_count"] = want - remaining;
            result["remaining"] = remaining;
            result["food_count_after"] = feeder.GetFoodCount();
            return result;
        }

        // ---------------------------------------------------------------- 总览

        static Dictionary<string, object> Overview(SceneContext scene, Dictionary<string, object> args)
        {
            var warnings = new List<string>();
            var player = scene.PlayerState;
            var clock = scene.TimeDirector;
            var market = scene.EconomyDirector;
            var actorCounts = new Dictionary<string, int>(StringComparer.Ordinal);
            var actors = scene.GameModel.AllActors();
            int slimeTotal = 0, feralTotal = 0, hungryTotal = 0;
            foreach (var pair in actors)
            {
                var actor = pair.Value;
                if (actor == null) continue;
                Identifiable.Id ident;
                try { ident = actor.ident; } catch { continue; }
                if (!IsAnySlime(ident)) continue;
                slimeTotal++;
                string key = ident.ToString();
                actorCounts[key] = actorCounts.ContainsKey(key) ? actorCounts[key] + 1 : 1;
                var slime = actor as SlimeModel;
                if (slime == null) continue;
                if (slime.isFeral) feralTotal++;
                if (slime.emotionHunger.currVal >= 0.85f) hungryTotal++;
            }

            var prices = new List<Dictionary<string, object>>();
            foreach (var id in Identifiable.PLORT_CLASS)
            {
                int? price = market.GetCurrValue(id);
                if (price.HasValue) prices.Add(Row("item_id", id.ToString(), "unit_price", price.Value,
                    "change", market.GetChangeInValue(id)));
            }
            var top = prices.OrderByDescending(x => (int)x["unit_price"]).Take(3).ToArray();
            var low = prices.OrderBy(x => (int)x["unit_price"]).Take(3).ToArray();
            if (market.IsMarketShutdown()) warnings.Add("MARKET_CLOSED");

            var offers = new List<Dictionary<string, object>>();
            foreach (ExchangeDirector.OfferType type in Enum.GetValues(typeof(ExchangeDirector.OfferType)))
            {
                string id = scene.ExchangeDirector.GetOfferId(type);
                if (id == null) continue;
                var expires = scene.ExchangeDirector.GetOfferExpirationTime(type);
                double? seconds = expires.HasValue ? (double?)Math.Round(expires.Value, 0) : null;
                if (seconds.HasValue && seconds.Value > 0 && seconds.Value < 7200) warnings.Add("OFFER_EXPIRING:" + type);
                offers.Add(Row("offer_type", type.ToString(), "offer_id", id,
                    "rancher", scene.ExchangeDirector.GetOfferRancherId(type),
                    "expires_in_world_seconds", seconds));
            }

            var storages = ActiveStorage().ToArray();
            var storeTypeCounts = storages.GroupBy(x => x.type.ToString())
                .Select(g => Row("storage_type", g.Key, "count", g.Count())).ToArray();
            var itemTotals = new Dictionary<string, int>(StringComparer.Ordinal);
            foreach (var storage in storages)
            {
                var ammo = storage.GetRelevantAmmo();
                for (int i = 0; i < ammo.GetUsableSlotCount(); i++)
                {
                    int count = ammo.GetSlotCount(i);
                    if (count <= 0) continue;
                    string key = ammo.GetSlotName(i).ToString();
                    itemTotals[key] = itemTotals.ContainsKey(key) ? itemTotals[key] + count : count;
                }
            }
            var topItems = itemTotals.OrderByDescending(x => x.Value).Take(8)
                .Select(x => Row("item_id", x.Key, "count", x.Value)).ToArray();

            var feeders = Object.FindObjectsOfType<SlimeFeeder>().Where(x => x.isActiveAndEnabled).ToArray();
            foreach (var feeder in feeders)
                if (feeder.GetFoodCount() == 0) warnings.Add("FEEDER_EMPTY:" + PlotId(feeder));
            var collectors = Object.FindObjectsOfType<PlortCollector>().Where(x => x.isActiveAndEnabled).ToArray();
            int collectorsWithLoose = 0;
            foreach (var collector in collectors)
            {
                var storage = collector.GetComponentInParent<SiloStorage>();
                if (storage == null) continue;
                var ammo = storage.GetRelevantAmmo();
                bool full = true;
                for (int i = 0; i < ammo.GetUsableSlotCount(); i++)
                    if (ammo.GetSlotCount(i) < ammo.GetSlotMaxCount(i)) { full = false; break; }
                if (full) warnings.Add("PLORT_STORAGE_FULL:" + PlotId(collector));
                else collectorsWithLoose++;
            }
            if (hungryTotal > 0) warnings.Add("HUNGRY_SLIMES:" + hungryTotal);
            if (feralTotal > 0) warnings.Add("FERAL_SLIMES:" + feralTotal);

            var extractors = Object.FindObjectsOfType<Extractor>().Where(x => x.isActiveAndEnabled).ToArray();
            int readyExtractors = 0;
            foreach (var extractor in extractors)
                try { if (extractor.CanInteract()) readyExtractors++; } catch { }
            if (readyExtractors > 0) warnings.Add("EXTRACTOR_READY:" + readyExtractors);

            int drones = Object.FindObjectsOfType<Drone>().Length;
            int corrals = scene.GameModel.AllLandPlots().Values.Count(x => x != null && x.typeId == LandPlot.Id.CORRAL);
            int loadedCorrals = Object.FindObjectsOfType<LandPlotLocation>()
                .Count(x => x != null && scene.GameModel.GetLandPlotModel(x.id) != null
                    && scene.GameModel.GetLandPlotModel(x.id).typeId == LandPlot.Id.CORRAL);

            return Row(
                "player", Row("currency", player.GetCurrency(), "keys", player.GetKeys(),
                    "position", Position(scene.Player.transform.position),
                    "zone", scene.PlayerZoneTracker == null ? Zone(scene.Player.transform) : scene.PlayerZoneTracker.GetCurrentZone().ToString()),
                "time", Row("day", clock.CurrDay(), "hour", Finite(clock.CurrHour()),
                    "paused", clock.HasPauser(), "fast_forwarding", clock.IsFastForwarding()),
                "market", Row("closed", market.IsMarketShutdown(), "top_prices", top, "low_prices", low,
                    "deposit_points", Object.FindObjectsOfType<ScorePlort>().Count(x => x.isActiveAndEnabled)),
                "offers", offers.ToArray(),
                "slimes", Row("in_memory", slimeTotal, "feral", feralTotal, "hungry", hungryTotal,
                    "types", actorCounts.OrderByDescending(x => x.Value).Take(10)
                        .Select(x => Row("item_id", x.Key, "count", x.Value)).ToArray()),
                "storages", Row("by_type", storeTypeCounts, "top_items", topItems, "total_containers", storages.Length),
                "ranch", Row("corrals", corrals, "corrals_loaded", loadedCorrals,
                    "collectors", collectors.Length, "feeders", feeders.Length,
                    "drones_loaded", drones, "extractors_loaded", extractors.Length,
                    "extractors_ready", readyExtractors),
                "warnings", warnings.Distinct().ToArray());
        }
    }
}
