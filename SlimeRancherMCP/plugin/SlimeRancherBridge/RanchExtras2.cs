// RanchExtras2 —— 第二批扩展工具：探索类（gordo/宝箱/钥匙门）、存档摘要、批量收获/出售/入仓
// 同一 partial class，复用 RanchTools/RanchActions 的私有 helper。
// C# 5 语法。
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
        static void RegisterExtras2(BridgeServer server)
        {
            Read(server, "sr_gordos", Gordos);
            Read(server, "sr_pods", Pods);
            Read(server, "sr_doors", Doors);
            server.Register("sr_saves", SaveList, false);
            Action(server, "sr_harvest_all", HarvestAll);
            Action(server, "sr_sell_all", SellAll);
            Action(server, "sr_store_all", StoreAll);
        }

        // ---------------------------------------------------------------- 探索：Gordo

        static Dictionary<string, object> Gordos(SceneContext scene, Dictionary<string, object> args)
        {
            string zoneFilter = Arg(args, "zone");
            int radius = Number(args, "radius", 0);
            bool includePopped = Flag(args, "include_popped", true);
            var loaded = new Dictionary<string, GordoEat>(StringComparer.Ordinal);
            foreach (var eat in Object.FindObjectsOfType<GordoEat>())
                if (eat != null && eat.isActiveAndEnabled && !loaded.ContainsKey(eat.id)) loaded[eat.id] = eat;

            var rows = new List<Dictionary<string, object>>();
            int allTotal = 0;
            foreach (var pair in scene.GameModel.AllGordos().OrderBy(x => x.Key, StringComparer.Ordinal))
            {
                var model = pair.Value;
                if (model == null) continue;
                allTotal++;
                bool popped = false;
                bool dropsKey = false;
                try { popped = model.HasPopped(); } catch { }
                try { dropsKey = model.DropsKey(); } catch { }
                if (!includePopped && popped) continue;
                GordoEat eat;
                loaded.TryGetValue(pair.Key, out eat);
                string zone = eat == null ? null : Zone(eat.transform);
                if (zoneFilter.Length != 0 && !Same(zone, zoneFilter)) continue;
                double distance = -1;
                if (eat != null && scene.Player != null)
                    distance = Math.Round(Vector3.Distance(scene.Player.transform.position, eat.transform.position), 2);
                if (radius > 0 && (distance < 0 || distance > radius)) continue;
                rows.Add(Row("gordo_id", pair.Key,
                    "loaded", eat != null,
                    "position", eat == null ? null : Position(eat.transform.position),
                    "zone", zone,
                    "player_distance", distance < 0 ? null : (object)distance,
                    "eaten", model.gordoEatenCount,
                    "popped", popped,
                    "drops_key", dropsKey,
                    "target_count", eat == null ? null : (object)eat.targetCount,
                    "slime", eat == null || eat.slimeDefinition == null ? null : eat.slimeDefinition.name));
            }
            return Row("gordos", rows.ToArray(), "total", rows.Count,
                "all_total", allTotal,
                "popped_total", rows.Count(x => (bool)x["popped"]),
                "loaded_total", rows.Count(x => (bool)x["loaded"]));
        }

        // ---------------------------------------------------------------- 探索：宝箱

        static Dictionary<string, object> Pods(SceneContext scene, Dictionary<string, object> args)
        {
            string zoneFilter = Arg(args, "zone");
            bool includeOpen = Flag(args, "include_open", true);
            var loaded = new Dictionary<string, TreasurePod>(StringComparer.Ordinal);
            foreach (var pod in Object.FindObjectsOfType<TreasurePod>())
                if (pod != null && pod.isActiveAndEnabled && !loaded.ContainsKey(pod.id)) loaded[pod.id] = pod;

            var rows = new List<Dictionary<string, object>>();
            int allTotal = 0;
            foreach (var pair in scene.GameModel.AllPods().OrderBy(x => x.Key, StringComparer.Ordinal))
            {
                var model = pair.Value;
                if (model == null) continue;
                allTotal++;
                string state = model.state.ToString();
                bool opened = Same(state, "OPEN");
                if (!includeOpen && opened) continue;
                TreasurePod pod;
                loaded.TryGetValue(pair.Key, out pod);
                string zone = pod == null ? null : Zone(pod.transform);
                if (zoneFilter.Length != 0 && !Same(zone, zoneFilter)) continue;
                rows.Add(Row("pod_id", pair.Key, "state", state, "opened", opened,
                    "loaded", pod != null,
                    "position", pod == null ? null : Position(pod.transform.position),
                    "zone", zone,
                    "player_distance", pod == null || scene.Player == null ? null : (object)Distance(scene, pod.transform),
                    "blueprint", pod == null ? null : pod.blueprint.ToString(),
                    "needs_upgrade", pod == null ? null : (object)pod.needsUpgrade,
                    "required_upgrade", pod == null ? null : pod.requiredUpgrade.ToString(),
                    "has_appearance", pod == null ? null : (object)(pod.unlockedSlimeAppearance != null)));
            }
            return Row("pods", rows.ToArray(), "total", rows.Count,
                "all_total", allTotal,
                "open_total", rows.Count(x => (bool)x["opened"]),
                "locked_total", rows.Count(x => !(bool)x["opened"]),
                "loaded_total", rows.Count(x => (bool)x["loaded"]));
        }

        // ---------------------------------------------------------------- 探索：钥匙门

        static Dictionary<string, object> Doors(SceneContext scene, Dictionary<string, object> args)
        {
            string zoneFilter = Arg(args, "zone");
            var loaded = new Dictionary<string, AccessDoor>(StringComparer.Ordinal);
            foreach (var door in Object.FindObjectsOfType<AccessDoor>())
                if (door != null && door.isActiveAndEnabled && !loaded.ContainsKey(door.id)) loaded[door.id] = door;

            var rows = new List<Dictionary<string, object>>();
            int allTotal = 0;
            foreach (var pair in scene.GameModel.AllDoors().OrderBy(x => x.Key, StringComparer.Ordinal))
            {
                var model = pair.Value;
                if (model == null) continue;
                allTotal++;
                string state = model.state.ToString();
                AccessDoor door;
                loaded.TryGetValue(pair.Key, out door);
                string zone = door == null ? null : Zone(door.transform);
                if (zoneFilter.Length != 0 && !Same(zone, zoneFilter)) continue;
                rows.Add(Row("door_id", pair.Key, "state", state, "loaded", door != null,
                    "position", door == null ? null : Position(door.transform.position),
                    "zone", zone,
                    "player_distance", door == null || scene.Player == null ? null : (object)Distance(scene, door.transform),
                    "locked_region", door == null ? null : door.lockedRegionId.ToString(),
                    "cost", door == null || door.doorPurchase == null ? null : (object)door.doorPurchase.cost,
                    "progress", door == null || door.progress == null ? new object[0]
                        : door.progress.Select(x => x.ToString()).ToArray()));
            }
            return Row("doors", rows.ToArray(), "total", rows.Count,
                "all_total", allTotal,
                "locked_total", rows.Count(x => Same((string)x["state"], "LOCKED")),
                "open_total", rows.Count(x => Same((string)x["state"], "OPEN")),
                "closed_total", rows.Count(x => !Same((string)x["state"], "LOCKED") && !Same((string)x["state"], "OPEN")),
                "loaded_total", rows.Count(x => (bool)x["loaded"]));
        }

        // ---------------------------------------------------------------- 存档摘要（磁盘，不依赖读档）

        static Dictionary<string, object> SaveList(Dictionary<string, object> args)
        {
            var gameContext = SRSingleton<GameContext>.Instance;
            var director = gameContext == null ? null : gameContext.AutoSaveDirector;
            var rows = new List<Dictionary<string, object>>();
            List<string> names;
            if (director != null && director.StorageProvider != null)
            {
                try { names = director.StorageProvider.GetAvailableGames(); }
                catch { names = null; }
            }
            else
            {
                names = null;
            }
            if (names == null)
            {
                names = SaveFiles().Select(System.IO.Path.GetFileNameWithoutExtension).ToList();
            }
            names.Sort(StringComparer.Ordinal);
            string gameName = null;
            try { if (director != null && director.SavedGame != null) gameName = director.SavedGame.GetName(); }
            catch { }
            // 正在玩的存档文件名（Harmony 从 AutoSaveDirector.BeginLoad 捕获）；捕获不到时为空。
            string loadedSave = SaveSlotTracker.CurrentSave;
            string latestName = null;
            DateTimeOffset latestStamp = DateTimeOffset.MinValue;
            foreach (var name in names)
            {
                // SavedGame.GetName() 返回"游戏名"（如 20250628093547_1），
                // 磁盘存档是 <游戏名>_<自动存档序号>，按前缀匹配判断"是否同一局"。
                bool sameGame = !String.IsNullOrEmpty(gameName)
                    && (string.Equals(name, gameName, StringComparison.Ordinal)
                        || name.StartsWith(gameName + "_", StringComparison.Ordinal));
                bool exact = !String.IsNullOrEmpty(loadedSave)
                    && string.Equals(name, loadedSave, StringComparison.Ordinal);
                var row = Row("save_name", name, "current", exact, "same_game", sameGame);
                try
                {
                    if (director != null)
                    {
                        var summary = director.LoadSummary(name);
                        if (summary != null)
                        {
                            row["display_name"] = summary.displayName;
                            // Summary.day 是 0 基：floor(worldTime / 86400)；
                            // 游戏内 TimeDirector.CurrDay() 是 1 + floor(...)，这里统一成 1 基。
                            row["day"] = summary.day + 1;
                            row["currency"] = summary.currency;
                            row["pedia_count"] = summary.pediaCount;
                            row["game_mode"] = summary.gameMode.ToString();
                            row["version"] = summary.version;
                            row["game_over"] = summary.gameOver;
                            // saveTimestamp 存的是 DateTimeOffset.UtcNow，输出前转本机时区。
                            row["saved_at"] = summary.saveTimestamp.ToLocalTime().ToString("yyyy-MM-dd HH:mm:ss");
                            row["saved_at_utc"] = summary.saveTimestamp.ToUniversalTime().ToString("yyyy-MM-dd HH:mm:ss");
                            row["invalid"] = summary.isInvalid;
                            if (summary.saveTimestamp > latestStamp)
                            {
                                latestStamp = summary.saveTimestamp;
                                latestName = name;
                            }
                        }
                    }
                }
                catch (Exception e)
                {
                    row["summary_error"] = e.GetType().Name;
                }
                rows.Add(row);
            }
            foreach (var row in rows)
            {
                row["latest"] = latestName != null && string.Equals((string)row["save_name"], latestName, StringComparison.Ordinal);
            }
            return Row("saves", rows.ToArray(), "count", rows.Count,
                "current", gameName,
                "current_save", loadedSave,
                "slot_tracking", SaveSlotTracker.Installed ? "harmony" : "unavailable",
                "slot_tracking_error", SaveSlotTracker.InstallError,
                "backup_root", BackupRoot);
        }

        // ---------------------------------------------------------------- 批量收获钻井

        static Dictionary<string, object> HarvestAll(SceneContext scene, Dictionary<string, object> args)
        {
            string zoneFilter = Arg(args, "zone");
            var extractors = Object.FindObjectsOfType<Extractor>()
                .Where(x => x.isActiveAndEnabled).ToArray();
            var candidates = new List<Dictionary<string, object>>();
            var readyList = new List<Extractor>();
            foreach (var extractor in extractors)
            {
                bool ready = false;
                try { ready = extractor.CanInteract(); } catch { }
                string zone = Zone(extractor.transform);
                if (zoneFilter.Length != 0 && !Same(zone, zoneFilter)) continue;
                var site = extractor.GetComponentInParent<GadgetSite>();
                string siteId = null;
                try { siteId = site == null ? null : site.id; } catch { }
                candidates.Add(Row("site_id", siteId, "zone", zone, "ready", ready,
                    "position", Position(extractor.transform.position),
                    "distance", Distance(scene, extractor.transform)));
                if (ready) readyList.Add(extractor);
            }
            var result = Row("extractors", candidates.ToArray(), "total", candidates.Count,
                "ready", readyList.Count);
            if (Preview(args)) return result;
            var triggered = new List<Dictionary<string, object>>();
            foreach (var extractor in readyList)
            {
                try
                {
                    extractor.OnInteract();
                    triggered.Add(Row("position", Position(extractor.transform.position), "ok", true));
                }
                catch (Exception e)
                {
                    triggered.Add(Row("position", Position(extractor.transform.position),
                        "ok", false, "error", e.GetType().Name + ": " + e.Message));
                }
            }
            result["triggered"] = triggered.ToArray();
            result["note"] = "已触发原生吐料；产出会逐颗喷到地上，稍后用 sr_loose_items 查看。";
            return result;
        }

        // ---------------------------------------------------------------- 批量出售

        static List<Dictionary<string, object>> CollectSourceSlots(SceneContext scene, Identifiable.Id item,
            bool includeVacpack, bool includeStorages)
        {
            var sources = new List<Dictionary<string, object>>();
            if (includeVacpack)
            {
                Ammo pack = scene.PlayerState.Ammo;
                for (int i = 0; i < pack.GetUsableSlotCount(); i++)
                {
                    if (pack.GetSlotName(i) != item) continue;
                    int count = pack.GetSlotCount(i);
                    if (count <= 0) continue;
                    sources.Add(Row("storage_id", 0, "slot", i, "count", count,
                        "distance", 0.0, "name", "vacpack"));
                }
            }
            if (includeStorages)
            {
                foreach (var storage in ActiveStorage().OrderBy(x => Distance(scene, x.transform)))
                {
                    Ammo ammo = storage.GetRelevantAmmo();
                    for (int i = 0; i < ammo.GetUsableSlotCount(); i++)
                    {
                        if (ammo.GetSlotName(i) != item) continue;
                        int count = ammo.GetSlotCount(i);
                        if (count <= 0) continue;
                        sources.Add(Row("storage_id", storage.GetInstanceID(), "slot", i, "count", count,
                            "distance", Distance(scene, storage.transform), "name", storage.name,
                            "plot_id", PlotId(storage)));
                    }
                }
            }
            return sources;
        }

        static Dictionary<string, object> SellAll(SceneContext scene, Dictionary<string, object> args)
        {
            Identifiable.Id item;
            if (!ParseItem(Arg(args, "item_id"), out item))
                return Fail("INVALID_ITEM", "用 sr_item_catalog / sr_market 返回的结晶 item_id。");
            if (!Identifiable.IsPlort(item)) return Fail("INVALID_ITEM", "只支持结晶（plort）。");
            string scope = Arg(args, "scope");
            if (scope.Length == 0) scope = "both";
            bool includeVacpack = Same(scope, "both") || Same(scope, "vacpack");
            bool includeStorages = Same(scope, "both") || Same(scope, "storage");
            if (!includeVacpack && !includeStorages)
                return Fail("BAD_ARGS", "scope 只能是 vacpack/storage/both。");
            int minUnitPrice = Number(args, "min_unit_price", 0);
            int wanted = Number(args, "count", -1);
            var sources = CollectSourceSlots(scene, item, includeVacpack, includeStorages);
            var result = Row("item_id", item.ToString(), "scope", scope,
                "currency_before", scene.PlayerState.GetCurrency(),
                "sources", sources.Select(x => Row("storage_id", x["storage_id"], "slot", x["slot"],
                    "count", x["count"], "name", x["name"],
                    "plot_id", x.ContainsKey("plot_id") ? x["plot_id"] : null,
                    "distance", x["distance"])).ToArray());
            int available = sources.Sum(x => (int)x["count"]);
            int planned = wanted <= 0 || wanted > available ? available : wanted;
            result["available"] = available;
            result["planned"] = planned;
            if (Preview(args)) return result;

            int remaining = planned;
            long credited = 0;
            int sold = 0;
            var receipts = new List<Dictionary<string, object>>();
            foreach (var source in sources)
            {
                if (remaining <= 0) break;
                int take = Math.Min(remaining, (int)source["count"]);
                var saleArgs = new Dictionary<string, object>(StringComparer.Ordinal);
                saleArgs["item_id"] = item.ToString();
                saleArgs["source_storage_id"] = source["storage_id"];
                saleArgs["source_slot"] = source["slot"];
                saleArgs["count"] = take;
                saleArgs["min_unit_price"] = minUnitPrice;
                saleArgs["dry_run"] = false;
                var receipt = Sell(scene, saleArgs);
                object error;
                if (receipt.TryGetValue("__error", out error))
                {
                    receipts.Add(Row("storage_id", source["storage_id"], "slot", source["slot"],
                        "error", error));
                    continue;
                }
                int soldCount = receipt.ContainsKey("sold") ? (int)receipt["sold"] : 0;
                long gained = receipt.ContainsKey("credited") ? Convert.ToInt64(receipt["credited"]) : 0;
                sold += soldCount;
                credited += gained;
                remaining -= soldCount;
                receipts.Add(Row("storage_id", source["storage_id"], "slot", source["slot"],
                    "sold", soldCount, "credited", gained,
                    "unit_price", receipt.ContainsKey("base_unit_price") ? receipt["base_unit_price"] : null));
            }
            result["receipts"] = receipts.ToArray();
            result["sold"] = sold;
            result["credited"] = credited;
            result["remaining"] = remaining;
            result["currency_after"] = scene.PlayerState.GetCurrency();
            return result;
        }

        // ---------------------------------------------------------------- 背包入仓 / 入精炼机

        static Dictionary<string, object> StoreAll(SceneContext scene, Dictionary<string, object> args)
        {
            string mode = Arg(args, "mode");
            if (mode.Length == 0) mode = "storage";
            bool toRefinery = Same(mode, "refinery");
            int maxPerItem = Number(args, "max_per_item", 999);
            Ammo pack = scene.PlayerState.Ammo;
            var plan = new List<Dictionary<string, object>>();
            for (int i = 0; i < pack.GetUsableSlotCount(); i++)
            {
                Identifiable.Id item = pack.GetSlotName(i);
                int count = pack.GetSlotCount(i);
                if (count <= 0 || item == Identifiable.Id.NONE) continue;
                if (Identifiable.IsLiquid(item) || Identifiable.IsSlime(item) || Identifiable.IsLargo(item)) continue;
                if (toRefinery)
                {
                    if (!GadgetDirector.IsRefineryResource(item)) continue;
                }
                else
                {
                    if (!Identifiable.IsPlort(item) && !Identifiable.IsCraft(item)) continue;
                }
                plan.Add(Row("slot", i, "item_id", item.ToString(), "count", count));
            }
            var result = Row("mode", toRefinery ? "refinery" : "storage", "planned", plan.ToArray(),
                "total_slots", plan.Count);
            if (Preview(args)) return result;

            var done = new List<Dictionary<string, object>>();
            foreach (var entry in plan)
            {
                Identifiable.Id item = (Identifiable.Id)Enum.Parse(typeof(Identifiable.Id), (string)entry["item_id"]);
                int count = Math.Min((int)entry["count"], maxPerItem);
                int slot = (int)entry["slot"];
                var moved = MoveOrDeposit(scene, item, count, slot, toRefinery);
                done.Add(moved);
            }
            result["results"] = done.ToArray();
            return result;
        }

        static Dictionary<string, object> MoveOrDeposit(SceneContext scene, Identifiable.Id item, int count,
            int sourceSlot, bool toRefinery)
        {
            if (toRefinery)
            {
                var dir = scene.GadgetDirector;
                int space = dir.GetRefinerySpaceAvailable(item);
                int take = Math.Min(count, space);
                var depArgs = new Dictionary<string, object>(StringComparer.Ordinal);
                depArgs["item_id"] = item.ToString();
                depArgs["source_storage_id"] = 0;
                depArgs["source_slot"] = sourceSlot;
                depArgs["count"] = take;
                depArgs["dry_run"] = false;
                var receipt = DepositRefinery(scene, depArgs);
                return Row("kind", "refinery", "item_id", item.ToString(), "requested", count,
                    "result", receipt);
            }
            int remaining = count;
            var results = new List<Dictionary<string, object>>();
            foreach (var storage in ActiveStorage().Where(x => Same(x.type.ToString(), "PLORT")
                || Same(x.type.ToString(), "NON_SLIMES")).OrderBy(x => Distance(scene, x.transform)))
            {
                if (remaining <= 0) break;
                Ammo ammo = storage.GetRelevantAmmo();
                int destSlot = -1;
                int space = 0;
                for (int i = 0; i < ammo.GetUsableSlotCount(); i++)
                {
                    if (!CanUseStorageSlot(storage, i)) continue;
                    if (!ammo.CouldAddToSlot(item, i, false)) continue;
                    int free = FreeSpaceForItem(ammo, item, i);
                    if (free <= 0) continue;
                    destSlot = i;
                    space = free;
                    break;
                }
                if (destSlot < 0) continue;
                int take = Math.Min(remaining, space);
                var moveArgs = new Dictionary<string, object>(StringComparer.Ordinal);
                moveArgs["item_id"] = item.ToString();
                moveArgs["source_storage_id"] = 0;
                moveArgs["source_slot"] = sourceSlot;
                moveArgs["count"] = take;
                moveArgs["destination_storage_id"] = storage.GetInstanceID();
                moveArgs["destination_slot"] = destSlot;
                moveArgs["dry_run"] = false;
                var receipt = Move(scene, moveArgs);
                object error;
                if (receipt.TryGetValue("__error", out error))
                {
                    results.Add(Row("storage_id", storage.GetInstanceID(), "error", error));
                    continue;
                }
                int creditedHere = receipt.ContainsKey("destination_count_after") && receipt.ContainsKey("destination_count_before")
                    ? Convert.ToInt32(receipt["destination_count_after"]) - Convert.ToInt32(receipt["destination_count_before"])
                    : take;
                remaining -= creditedHere;
                results.Add(Row("storage_id", storage.GetInstanceID(), "plot_id", PlotId(storage),
                    "slot", destSlot, "moved", creditedHere));
            }
            return Row("kind", "storage", "item_id", item.ToString(), "requested", count,
                "remaining", remaining, "results", results.ToArray());
        }
    }
}
