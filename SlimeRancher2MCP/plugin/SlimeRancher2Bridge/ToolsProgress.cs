using System;
using System.Collections.Generic;
using System.Linq;
using Il2Cpp;
using Il2CppMonomiPark.SlimeRancher.DataModel;
using Row = System.Collections.Generic.Dictionary<string, object>;

namespace SlimeRancher2Bridge
{
    internal static class ToolsProgress
    {
        internal static void Register(BridgeServer server)
        {
            server.Register("sr2_achievements", args => ToolsRanch.WithGame(game => Achievements(game, args)), mainThread: true);
            server.Register("sr2_player_upgrades", args => ToolsRanch.WithGame(game => Upgrades(game, args)), mainThread: true);
            server.Register("sr2_map_zones", Zones, mainThread: true);
            server.Register("sr2_weather", args => ToolsRanch.WithGame(game => Weather(game, args)), mainThread: true);
            server.Register("sr2_prisma_forecast", args => ToolsRanch.WithGame(game => Prisma(game, args)), mainThread: true);
        }

        static Row Unavailable(string reason) => new() { ["available"] = false, ["reason"] = reason };
        static string Query(Row args) => args.TryGetValue("query", out var q) ? Convert.ToString(q).Trim() : "";
        static bool Matches(string text, string query) => (text ?? "").Contains(query, StringComparison.OrdinalIgnoreCase);
        static double? Finite(double time) => double.IsFinite(time) ? time : null;

        static Row Page(Row args, int total, List<Row> rows)
        {
            rows.Sort((a, b) => string.CompareOrdinal((string)a["id"], (string)b["id"]));
            return ToolsWorld.Page(args, total, rows, row => row);
        }

        static Row Achievements(GameModel game, Row args)
        {
            var director = game.metaGameDirectorModel?.participant;
            var definitions = director?.Achievements;
            if (director == null || !director._achievementDataLoaded || definitions == null) return Unavailable("achievements_unavailable");
            string query = Query(args);
            var rows = new List<Row>();
            int met = 0;
            foreach (var entry in definitions)
            {
                if (entry == null) continue;
                string id = entry.AchievementIdentifier.ToString();
                int progress = entry.CurrentProgress, target = entry.MaxProgress;
                bool? reached = target > 0 ? progress >= target : null;
                if (reached == true) met++;
                if (!Matches(id, query) && !Matches(entry.SteamAchievementName, query)) continue;
                rows.Add(new Row { ["id"] = id, ["steam_name"] = entry.SteamAchievementName, ["progress"] = progress,
                    ["target"] = target, ["target_met"] = reached });
            }
            var result = Page(args, definitions.Count, rows);
            result["targets_met_count"] = met;
            return result;
        }

        static Row Upgrades(GameModel game, Row args)
        {
            var model = game.PlayerModel?.upgradeModel;
            var definitions = model?.upgradeDefinitions?.items;
            if (definitions == null || model.upgradeLevels == null) return Unavailable("upgrades_unavailable");
            string query = Query(args);
            var rows = new List<Row>();
            foreach (var d in definitions)
            {
                if (d == null) continue;
                string id = d.UniqueId.ToString(System.Globalization.CultureInfo.InvariantCulture);
                if (!Matches(id, query) && !Matches(d.name, query)) continue;
                int level = model.GetUpgradeLevel(d);
                bool hasNext = d.UpgradeLevelExist(level + 1);
                rows.Add(new Row { ["id"] = id, ["name"] = d.name, ["level"] = level, ["level_count"] = d.LevelCount,
                    ["next_level"] = hasNext ? level + 1 : null,
                    ["next_title"] = hasNext ? Il2CppUtil.Localized(d.GetTitle(level + 1)) : null });
            }
            return Page(args, definitions.Count, rows);
        }

        static Row Zones(Row args) => ToolsNavigation.WithScene(raw =>
        {
            var scene = (SceneContext)raw;
            var mappings = scene.MapDirector?.ZoneMappings;
            if (mappings == null || scene.GameModel == null || !scene.GameModel.isInitialized) return Unavailable("map_unavailable");
            string query = Query(args);
            bool lockedOnly = args.TryGetValue("locked_only", out var flag) && flag is true;
            var rows = new List<Row>();
            foreach (var mapping in mappings)
            {
                var zone = mapping?.Zone;
                if (zone == null || !Matches(zone.ReferenceId, query)) continue;
                bool unlocked = scene.IsZoneUnlocked(zone);
                if (lockedOnly && unlocked) continue;
                rows.Add(new Row { ["id"] = zone.ReferenceId, ["title"] = Il2CppUtil.Localized(zone.Name),
                    ["map"] = mapping.Map?.name, ["ranch_zone"] = zone.IsRanchZone, ["unlocked"] = unlocked });
            }
            return Page(args, mappings.Count, rows);
        });

        static Row Weather(GameModel game, Row args)
        {
            var zones = game.weatherModel?._zoneDatas;
            if (zones == null || game.world == null) return Unavailable("weather_unavailable");
            string query = Query(args);
            var rows = new List<Row>();
            foreach (var entry in zones)
            {
                var zone = entry.Key;
                if (zone == null || !Matches(zone.ReferenceId, query)) continue;
                var data = entry.Value;
                var forecasts = data?.Forecast;
                List<Row> schedule = null;
                if (forecasts != null)
                {
                    schedule = new List<Row>();
                    foreach (var f in forecasts)
                    {
                        if (f == null) continue;
                        schedule.Add(new Row { ["pattern"] = f.Pattern?.name, ["state"] = f.State?.GetName(),
                            ["started"] = f.Started, ["start_time"] = Finite(f.StartTime),
                            ["end_time"] = f.IsEndTimeKnown ? Finite(f.EndTime) : null });
                    }
                }
                var wind = data?.Parameters?.WindDirection;
                rows.Add(new Row { ["id"] = zone.ReferenceId, ["forecast"] = schedule,
                    ["wind_direction"] = wind.HasValue ? new { wind.Value.x, wind.Value.y, wind.Value.z } : null });
            }
            var result = Page(args, zones.Count, rows);
            result["world_time"] = Finite(game.world.worldTime);
            return result;
        }

        static Row Prisma(GameModel game, Row args)
        {
            var model = game.prismaDisruptionModel;
            var areas = model?._areaDatas;
            if (areas == null || game.world == null || !double.IsFinite(game.world.worldTime)) return Unavailable("prisma_unavailable");
            double now = game.world.worldTime;
            string query = Query(args);
            var rows = new List<Row>();
            foreach (var entry in areas)
            {
                var area = entry.Key;
                var data = entry.Value;
                if (area == null || data == null || !Matches(area.ReferenceId, query)) continue;
                var row = new Row { ["id"] = area.ReferenceId, ["name"] = area.name, ["forecast_unlocked"] = data.ForecastUnlocked,
                    ["current_level"] = null, ["next_change_time"] = null, ["upcoming"] = null };
                if (data.ForecastUnlocked && data.Forecast != null)
                {
                    var changes = new List<(double Time, string Level)>();
                    foreach (var pattern in data.Forecast)
                    {
                        if (pattern?.Entries == null) continue;
                        foreach (var change in pattern.Entries)
                            if (change != null && double.IsFinite(change.StartTime) && change.StartTime > now)
                                changes.Add((change.StartTime, change.Level.ToString()));
                    }
                    row["current_level"] = model.GetStateAtTime(area, now).ToString();
                    double next = data.NextChangeTime;
                    row["next_change_time"] = next > now ? Finite(next) : null;
                    row["upcoming"] = changes.OrderBy(c => c.Time).Select(c => new { start_time = c.Time, level = c.Level }).ToArray();
                }
                rows.Add(row);
            }
            var result = Page(args, areas.Count, rows);
            result["world_time"] = now;
            return result;
        }
    }
}
