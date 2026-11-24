// RanchExtras4 —— 第四批扩展工具：告警巡检、市场建议、传送（作弊）、备份还原
// C# 5 语法；同一 partial class 复用 helper（包括 RanchExtras3 的 WorldModelOf）。
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using MonomiPark.SlimeRancher.DataModel;
using MonomiPark.SlimeRancher.Regions;
using UnityEngine;
using Object = UnityEngine.Object;

namespace SlimeRancherBridge
{
    static partial class RanchTools
    {
        static void RegisterExtras4(BridgeServer server)
        {
            Read(server, "sr_watch", Watch);
            Read(server, "sr_market_advice", MarketAdvice);
            Action(server, "sr_teleport", Teleport);
            server.Register("sr_restore_backup", RestoreBackup, true);
        }

        // ---------------------------------------------------------------- 告警巡检

        static Dictionary<string, object> Watch(SceneContext scene, Dictionary<string, object> args)
        {
            var alerts = new List<Dictionary<string, object>>();
            var clock = scene.TimeDirector;
            double worldTime = clock.WorldTime();

            var feeders = Object.FindObjectsOfType<SlimeFeeder>().Where(x => x.isActiveAndEnabled).ToArray();
            foreach (var feeder in feeders)
            {
                if (feeder.GetFoodCount() == 0)
                    alerts.Add(Row("severity", "warning", "code", "FEEDER_EMPTY",
                        "plot_id", PlotId(feeder), "feeder_id", feeder.GetInstanceID(),
                        "message", "喂食器没有食物"));
            }

            var collectors = Object.FindObjectsOfType<PlortCollector>().Where(x => x.isActiveAndEnabled).ToArray();
            foreach (var collector in collectors)
            {
                var storage = collector.GetComponentInParent<SiloStorage>();
                if (storage == null) continue;
                var ammo = storage.GetRelevantAmmo();
                bool full = true;
                for (int i = 0; i < ammo.GetUsableSlotCount(); i++)
                    if (ammo.GetSlotCount(i) < ammo.GetSlotMaxCount(i)) { full = false; break; }
                if (full)
                    alerts.Add(Row("severity", "warning", "code", "PLORT_STORAGE_FULL",
                        "plot_id", PlotId(collector), "storage_id", storage.GetInstanceID(),
                        "message", "收集器仓储已满，结晶会落到地上"));
            }

            int loosePlorts = 0;
            foreach (var entry in CellDirector.GetAllRegistered())
            {
                try
                {
                    if (entry.GameObject == null) continue;
                    if (Identifiable.IsPlort(entry.Id)) loosePlorts++;
                }
                catch { }
            }
            if (loosePlorts > 0)
                alerts.Add(Row("severity", "info", "code", "PLORTS_ON_GROUND", "count", loosePlorts,
                    "message", "当前内存中有 " + loosePlorts + " 颗结晶掉在地上"));

            int hungry = 0, feral = 0, totalSlimes = 0;
            foreach (var pair in scene.GameModel.AllActors())
            {
                var actor = pair.Value;
                if (actor == null) continue;
                Identifiable.Id ident;
                try { ident = actor.ident; } catch { continue; }
                if (!IsAnySlime(ident)) continue;
                totalSlimes++;
                var slime = actor as SlimeModel;
                if (slime == null) continue;
                if (slime.emotionHunger.currVal >= 0.85f) hungry++;
                if (slime.isFeral) feral++;
            }
            if (hungry > 0)
                alerts.Add(Row("severity", "warning", "code", "HUNGRY_SLIMES", "count", hungry,
                    "message", hungry + " 只史莱姆处于饥饿状态（可能逃栏）"));
            if (feral > 0)
                alerts.Add(Row("severity", "info", "code", "FERAL_SLIMES", "count", feral,
                    "message", feral + " 只野生狂暴史莱姆在内存中"));

            int extractorsReady = 0;
            foreach (var extractor in Object.FindObjectsOfType<Extractor>())
            {
                if (!extractor.isActiveAndEnabled) continue;
                try { if (extractor.CanInteract()) extractorsReady++; } catch { }
            }
            if (extractorsReady > 0)
                alerts.Add(Row("severity", "info", "code", "EXTRACTOR_READY", "count", extractorsReady,
                    "message", extractorsReady + " 台钻井/泵机/蜂房可以收获"));

            int droneLow = 0, droneEmpty = 0;
            foreach (var battery in Object.FindObjectsOfType<DroneStationBattery>())
            {
                if (!battery.isActiveAndEnabled) continue;
                bool has = false;
                try { has = battery.HasAny(); } catch { }
                if (!has) { droneEmpty++; continue; }
                double until = 0;
                try { until = battery.Time; } catch { }
                if (until > 0 && until - worldTime < 2 * 3600) droneLow++;
            }
            if (droneEmpty > 0)
                alerts.Add(Row("severity", "warning", "code", "DRONE_BATTERY_EMPTY", "count", droneEmpty,
                    "message", droneEmpty + " 个无人机电站没电（需要加水充电）"));
            if (droneLow > 0)
                alerts.Add(Row("severity", "info", "code", "DRONE_BATTERY_LOW", "count", droneLow,
                    "message", droneLow + " 个无人机电站电量不足 2 游戏小时"));

            foreach (ExchangeDirector.OfferType type in Enum.GetValues(typeof(ExchangeDirector.OfferType)))
            {
                string id = scene.ExchangeDirector.GetOfferId(type);
                if (id == null) continue;
                double? expires = scene.ExchangeDirector.GetOfferExpirationTime(type);
                if (expires.HasValue && expires.Value > 0 && expires.Value < 7200)
                    alerts.Add(Row("severity", "warning", "code", "OFFER_EXPIRING",
                        "offer_type", type.ToString(), "offer_id", id,
                        "expires_in_world_seconds", Math.Round(expires.Value, 0),
                        "message", "委托将在 2 游戏小时内到期"));
            }

            if (scene.EconomyDirector.IsMarketShutdown())
                alerts.Add(Row("severity", "warning", "code", "MARKET_CLOSED",
                    "message", "结晶市场当前闭市"));
            if (scene.PlayerState.GetKeys() > 0)
                alerts.Add(Row("severity", "info", "code", "KEYS_AVAILABLE",
                    "count", scene.PlayerState.GetKeys(), "message", "背包里有钥匙可用于开门/宝箱"));

            var order = new Dictionary<string, int>(StringComparer.Ordinal);
            order["warning"] = 0;
            order["info"] = 1;
            var sorted = alerts.OrderBy(x => order[(string)x["severity"]]).ToArray();
            return Row("alerts", sorted, "count", sorted.Length,
                "summary", Row("feeder_empty", feeders.Count(x => x.GetFoodCount() == 0),
                    "hungry_slimes", hungry, "feral_slimes", feral, "slimes_in_memory", totalSlimes,
                    "loose_plorts", loosePlorts, "extractors_ready", extractorsReady,
                    "drone_battery_low", droneLow, "drone_battery_empty", droneEmpty,
                    "day", clock.CurrDay(), "hour", Math.Round(clock.CurrHour(), 2),
                    "market_closed", scene.EconomyDirector.IsMarketShutdown()));
        }

        // ---------------------------------------------------------------- 市场建议

        static Dictionary<string, object> MarketAdvice(SceneContext scene, Dictionary<string, object> args)
        {
            var economy = scene.EconomyDirector;
            var world = WorldModelOf(scene);
            var baseValues = new Dictionary<Identifiable.Id, float>(Identifiable.idComparer);
            var fullSaturation = new Dictionary<Identifiable.Id, float>(Identifiable.idComparer);
            if (economy.baseValueMap != null)
            {
                foreach (var entry in economy.baseValueMap)
                {
                    if (entry == null || entry.accept == null) continue;
                    baseValues[entry.accept.id] = entry.value;
                    fullSaturation[entry.accept.id] = entry.fullSaturation;
                }
            }
            var rows = new List<Dictionary<string, object>>();
            foreach (var pair in baseValues.OrderBy(x => x.Key.ToString(), StringComparer.Ordinal))
            {
                var id = pair.Key;
                int? price = economy.GetCurrValue(id);
                if (!price.HasValue) continue;
                int? change = economy.GetChangeInValue(id);
                float baseValue = pair.Value;
                float saturation = 0;
                if (world != null && world.marketSaturation != null)
                {
                    float value;
                    if (world.marketSaturation.TryGetValue(id, out value)) saturation = value;
                }
                float full = 0;
                if (fullSaturation.ContainsKey(id)) full = fullSaturation[id];
                double priceRatio = baseValue > 0 ? Math.Round(price.Value / (double)baseValue, 3) : 0;
                double satRatio = full > 0 ? Math.Round(saturation / (double)full, 3) : 0;
                string advice;
                if (satRatio >= 0.5 && (change ?? 0) <= 0) advice = "WAIT";
                else if (priceRatio >= 1.15) advice = "SELL_NOW";
                else if ((change ?? 0) > 0) advice = "SELL_NOW";
                else advice = "HOLD";
                rows.Add(Row("item_id", id.ToString(), "name", Identifiable.GetName(id, false),
                    "unit_price", price.Value, "base_value", Math.Round(baseValue, 2),
                    "price_ratio", priceRatio, "change", change,
                    "saturation", Math.Round(saturation, 2),
                    "saturation_ratio", satRatio, "advice", advice));
            }
            var sorted = rows.OrderBy(x => (string)x["advice"] == "SELL_NOW" ? 0
                : (string)x["advice"] == "HOLD" ? 1 : 2).ThenByDescending(x => (double)x["price_ratio"]).ToArray();
            return Row("advice", sorted, "total", sorted.Length,
                "market_closed", economy.IsMarketShutdown(),
                "note", "SELL_NOW=价格高于基准或正在上涨；WAIT=饱和度高且价跌；HOLD=其余情况。");
        }

        // ---------------------------------------------------------------- 传送（作弊）

        static Dictionary<string, object> Teleport(SceneContext scene, Dictionary<string, object> args)
        {
            if (scene.Player == null) return Fail("NOT_READY", "玩家对象不存在。");
            Vector3 current = scene.Player.transform.position;
            Vector3 target = current;
            string plotId = Arg(args, "plot_id");
            if (plotId.Length != 0)
            {
                var location = Object.FindObjectsOfType<LandPlotLocation>().FirstOrDefault(x => x != null && Same(x.id, plotId));
                if (location == null) return Fail("TARGET_UNAVAILABLE", "该地块不在已加载区域，无法作为传送目标（可传 x/y/z 坐标）。");
                target = location.transform.position + new Vector3(0f, 1.8f, 0f);
            }
            else
            {
                int x = Number(args, "x", 0);
                int z = Number(args, "z", 0);
                int y = Number(args, "y", -99999);
                target = new Vector3(x, y == -99999 ? current.y : y, z);
            }
            var result = Row("from", Position(current), "to", Position(target),
                "distance", Math.Round(Vector3.Distance(current, target), 2));
            if (Preview(args)) return result;
            scene.Player.transform.position = target;
            try { Physics.SyncTransforms(); } catch { }
            result["position_after"] = Position(scene.Player.transform.position);
            result["note"] = "作弊类操作：区域会在后续帧加载；请用 sr_player 确认位置，卡地形可用 sr_teleport 传回原坐标。";
            return result;
        }

        // ---------------------------------------------------------------- 备份还原（仅主菜单）

        static Dictionary<string, object> RestoreBackup(Dictionary<string, object> args)
        {
            bool mainMenu = true;
            try { mainMenu = Levels.isMainMenu(); } catch { }
            if (!mainMenu)
                return Fail("MUST_BE_AT_MENU", "请先退出到主菜单（或退出游戏）再还原，游戏运行中覆盖会被存档覆盖。");
            string dirArg = Arg(args, "backup_dir");
            if (dirArg.Length == 0) return Fail("BAD_ARGS", "提供 backup_dir（sr_backup 返回的目录）。");
            string root;
            string dir;
            try
            {
                root = Path.GetFullPath(BackupRoot);
                dir = Path.GetFullPath(dirArg);
            }
            catch (Exception e)
            {
                return Fail("BAD_ARGS", e.GetType().Name);
            }
            if (!dir.StartsWith(root, StringComparison.OrdinalIgnoreCase))
                return Fail("FORBIDDEN", "只允许还原 " + root + " 下的备份目录。");
            if (!Directory.Exists(dir)) return Fail("NOT_FOUND", "目录不存在: " + dir);
            var files = Directory.GetFiles(dir, "*.sav");
            if (files.Length == 0) return Fail("NO_SAVES", "该备份目录里没有 .sav。");
            var result = Row("backup_dir", dir, "save_dir", SaveDir(),
                "files", files.Select(x => Row("file", Path.GetFileName(x), "size", new FileInfo(x).Length,
                    "sha256", Sha256(x))).ToArray());
            if (Preview(args)) return result;

            string stamp = DateTime.Now.ToString("yyyyMMdd_HHmmss");
            string safety = Path.Combine(BackupRoot, "before-restore_" + stamp);
            Directory.CreateDirectory(safety);
            foreach (var file in SaveFiles())
                File.Copy(file, Path.Combine(safety, Path.GetFileName(file)), true);
            int copied = 0;
            foreach (var file in files)
            {
                File.Copy(file, Path.Combine(SaveDir(), Path.GetFileName(file)), true);
                copied++;
            }
            result["safety_backup_dir"] = safety;
            result["copied"] = copied;
            result["note"] = "还原完成；重启游戏后再读档。";
            return result;
        }
    }
}
