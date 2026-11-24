using System;
using System.Collections.Generic;
using System.Text.Json;
using System.Threading.Tasks;
using Il2Cpp;
using Il2CppMonomiPark.SlimeRancher.DataModel;

namespace Il2CppMonomiPark.SlimeRancher.DataModel
{
    public partial class GameModel
    {
        public MetaModel metaGameDirectorModel;
        public WeatherFixture weatherModel;
        public PrismaFixture prismaDisruptionModel;
        public WorldClock world;
    }
    public record WorldClock(double worldTime);
    public record AchievementEntry(string AchievementIdentifier, string SteamAchievementName, int CurrentProgress, int MaxProgress);
    public record MetaDirector(bool _achievementDataLoaded, List<AchievementEntry> Achievements);
    public record MetaModel(MetaDirector participant);
    public record Upgrade(int UniqueId, string name, int LevelCount)
    {
        public bool UpgradeLevelExist(int level) => level >= 1 && level <= LevelCount;
        public string GetTitle(int level) => name + level;
    }
    public record UpgradeList(List<Upgrade> items);
    public record UpgradeFixture(UpgradeList upgradeDefinitions, Dictionary<int, int> upgradeLevels)
    {
        public int GetUpgradeLevel(Upgrade d) => upgradeLevels.GetValueOrDefault(d.UniqueId);
    }
    public record Zone(string ReferenceId, string Name, bool IsRanchZone);
    public record Asset(string name);
    public record MapEntry(Zone Zone, Asset Map);
    public record MapFixture(List<MapEntry> ZoneMappings);
    public record WeatherState(string Name) { public string GetName() => Name; }
    public record WeatherEntry(Asset Pattern, WeatherState State, bool Started, double StartTime, bool IsEndTimeKnown, double EndTime);
    public record WeatherParameters(UnityEngine.Vector3 WindDirection);
    public record WeatherZone(List<WeatherEntry> Forecast, WeatherParameters Parameters);
    public record WeatherFixture(Dictionary<Zone, WeatherZone> _zoneDatas);
    public record Area(string ReferenceId, string name);
    public record Change(double StartTime, string Level);
    public record Pattern(List<Change> Entries);
    public record AreaData(bool ForecastUnlocked, double NextChangeTime, List<Pattern> Forecast);
    public record PrismaFixture(Dictionary<Area, AreaData> _areaDatas)
    {
        public string GetStateAtTime(Area area, double time) => "LOW";
    }
}

internal static partial class Program
{
    static async Task TestProgress(GameModel game, Func<string, object, Task<JsonElement>> query)
    {
        Check("achievement data not initialized", !(await query("sr2_achievements", new { })).GetProperty("available").GetBoolean());
        game.metaGameDirectorModel = new(new(true, new() { new("A", "steam-one", 4, 4), new("B", "steam-two", 3, 10) }));
        var achievements = await query("sr2_achievements", new { query = "TWO" });
        var achievement = achievements.GetProperty("items")[0];
        Check("achievement counter and unfiltered target total", achievement.GetProperty("progress").GetInt32() == 3
            && !achievement.GetProperty("target_met").GetBoolean() && achievements.GetProperty("targets_met_count").GetInt32() == 1);

        game.PlayerModel.upgradeModel = new(new(new() { new(7, "Energy", 2), new(8, "Health", 3) }), new() { [7] = 2, [8] = 1 });
        var upgrades = (await query("sr2_player_upgrades", new { })).GetProperty("items");
        Check("last and next upgrade levels", upgrades[0].GetProperty("next_level").ValueKind == JsonValueKind.Null
            && upgrades[1].GetProperty("next_level").GetInt32() == 2 && upgrades[1].GetProperty("next_title").GetString() == "Health2");

        var zone = new Zone("remote", "Test Zone", false);
        var home = new Zone("home", "Home", true);
        var scene = SceneContext.Instance;
        scene.MapDirector = new(new() { new(zone, new("Island")), new(home, new("Ranch")) });
        scene.UnlockedZones.Add(home);
        var zones = (await query("sr2_map_zones", new { locked_only = true })).GetProperty("items");
        Check("zone filter uses unlock state", zones.GetArrayLength() == 1 && zones[0].GetProperty("id").GetString() == "remote");

        int oldGadgetCount = game.gadgets.Count;
        var obj = new UnityEngine.GameObject { ZoneId = "home" };
        game.landPlots["zone-buildings-silo"] = new(LandPlot.Id.SILO, null, null, new() { "STORAGE2" }, obj);
        game.landPlots["zone-buildings-empty"] = new(LandPlot.Id.EMPTY, null, null, new(), obj);
        game.landPlots["zone-buildings-unassigned"] = new(LandPlot.Id.CORRAL, null, null, new(), null);
        game.gadgets.Add(new() { actorId = new(8035), ident = new GadgetDefinition { ReferenceId = "Gadget.Station" },
            SceneGroup = SystemContext.Instance.CurrentSceneGroup, lastPosition = new(123, 0, 0) });
        var buildings = await query("sr2_zone_buildings", new { zone = "Home" });
        var buildingsItems = buildings.GetProperty("items");
        Check("regional buildings include purchased upgrades and a gadget located by same-group last position",
            buildings.GetProperty("zone_loaded").GetBoolean() && buildingsItems.GetArrayLength() == 2 &&
            buildingsItems[0].GetProperty("zone_source").GetString() == "last_position" &&
            buildingsItems[1].GetProperty("upgrades")[0].GetString() == "STORAGE2" &&
            buildings.GetProperty("unassigned_plot_count_worldwide").GetInt32() >= 1);
        var emptyPlots = await query("sr2_zone_buildings", new { zone = "home", include_empty = true, limit = 1 });
        Check("regional buildings include empty plots on request and paginate",
            emptyPlots.GetProperty("matched_count").GetInt32() == 3 && emptyPlots.GetProperty("next_offset").GetInt32() == 1);
        var unloaded = await query("sr2_zone_buildings", new { zone = "remote" });
        Check("unloaded region does not present its empty result as complete",
            !unloaded.GetProperty("zone_loaded").GetBoolean() && unloaded.GetProperty("items").GetArrayLength() == 0);
        Check("unknown regional query reports zone_not_found",
            (await query("sr2_zone_buildings", new { zone = "missing" })).GetProperty("reason").GetString() == "zone_not_found");
        game.landPlots.Remove("zone-buildings-silo");
        game.landPlots.Remove("zone-buildings-empty");
        game.landPlots.Remove("zone-buildings-unassigned");
        game.gadgets.RemoveRange(oldGadgetCount, game.gadgets.Count - oldGadgetCount);

        game.world = new(120);
        game.weatherModel = new(new() { [zone] = new(new() { new(new("Rain"), new("Heavy"), true, 114, false, double.NaN) },
            new(new(1, 0, 0))) });
        var weather = await query("sr2_weather", new { query = "MOTE" });
        var forecast = weather.GetProperty("items")[0].GetProperty("forecast")[0];
        Check("regional weather preserves unknown end time", weather.GetProperty("world_time").GetDouble() == 120
            && forecast.GetProperty("state").GetString() == "Heavy" && forecast.GetProperty("end_time").ValueKind == JsonValueKind.Null);

        var pattern = new Pattern(new() { new(270, "HIGH"), new(60, "OLD"), new(135, "MID") });
        game.prismaDisruptionModel = new(new() { [new("A", "First")] = new(false, 135, new() { pattern }),
            [new("B", "Second")] = new(true, 135, new() { pattern }) });
        var areas = (await query("sr2_prisma_forecast", new { })).GetProperty("items");
        var upcoming = areas[1].GetProperty("upcoming");
        Check("locked forecasts stay hidden and upcoming changes are chronological", areas[0].GetProperty("upcoming").ValueKind == JsonValueKind.Null
            && upcoming.GetArrayLength() == 2 && upcoming[0].GetProperty("start_time").GetDouble() == 135
            && areas[1].GetProperty("current_level").GetString() == "LOW");
    }
}
