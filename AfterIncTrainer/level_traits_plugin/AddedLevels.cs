extern alias GameAssembly;

using System.Diagnostics;
using System.Text.Json;
using GameAssembly::com.ndemiccreations.survivors;
using Path = System.IO.Path;

namespace AfterIncLevelTraits;

internal static class AddedLevels
{
    private static readonly string DirectoryPath = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "AfterIncTrainer", "added_levels");
    private static readonly long Start = Process.GetCurrentProcess().StartTime.ToUniversalTime().ToFileTimeUtc();
    private static readonly string SessionFile = Path.Combine(DirectoryPath, $"levels-{Environment.ProcessId}-{Start}.json");
    private static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web);
    private static readonly Dictionary<int, object> Results = new();
    private static readonly AddedTraitPlanner Planner = new();
    private static Dictionary<string, Choice>? catalogue;

    private static void Publish(string path, object value)
    {
        var temp = path + ".tmp";
        File.WriteAllText(temp, JsonSerializer.Serialize(value, Json));
        File.Move(temp, path, overwrite: true);
    }

    internal static void Ready()
    {
        Directory.CreateDirectory(DirectoryPath);
        Publish(Path.Combine(DirectoryPath, "ready.json"), new {
            version = "26049821.14", build = "26049821", pid = Environment.ProcessId, start = Start
        });
    }

    internal static AddOptions? ForSeed(int seed)
    {
        if (!File.Exists(SessionFile)) return null;
        using var stream = new FileStream(SessionFile, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
        var settings = JsonSerializer.Deserialize<Dictionary<int, AddOptions>>(stream, Json);
        if (settings is not null) Planner.KeepActive(settings);
        return settings?.GetValueOrDefault(seed);
    }

    private static Choice Describe(MapMod mod) =>
        new(mod.title?.ToLocalisedString() ?? mod.ident, mod.family,
            mod.required_modifiers_and_not?.ToArray() ?? Array.Empty<string>(),
            mod.ambition_min, mod.ambition_max, mod.danger_level_min, mod.danger_level_max, mod.odds > 0);

    internal static void PublishCatalogue(MapModManager manager)
    {
        if (catalogue is not null) return;
        var items = new Dictionary<string, Choice>(StringComparer.Ordinal);
        for (int i = 0; i < manager.all.Count; i++)
        {
            var mod = manager.all[i];
            if (!mod.disabled && !mod.needs_infestation && !mod.ident.StartsWith("sc_custom_", StringComparison.Ordinal))
                items[mod.ident] = Describe(mod);
        }
        Publish(Path.Combine(DirectoryPath, "catalogue.json"), items);
        catalogue = items;
    }

    internal static string[] Apply(LevelBuilder builder, AddOptions options, MapModManager manager, int seed)
    {
        if (options.Modifiers is null || options.Modifiers.Distinct().Count() != options.Modifiers.Length ||
            options.Danger is < 0 or > 4 || options.Ambition is < 0 or > 16777216)
            throw new InvalidDataException("关卡配置无效");
        var gameDanger = builder.startingDangerLevel;
        var gameAmbition = builder.GetAmbitionLevel();
        var pool = catalogue ?? throw new InvalidDataException("游戏特性目录尚未就绪");
        var selected = new Dictionary<string, Choice>();
        foreach (var id in options.Modifiers)
        {
            if (!pool.TryGetValue(id, out var choice))
                throw new InvalidDataException($"找不到特性 {id}");
            if (selected.Any(p => choice.Conflicts(id, p.Key, p.Value)))
                throw new InvalidDataException($"「{choice.Title}」与其他手选特性互斥");
            selected.Add(id, choice);
        }
        var (ambition, danger) = options.ResolveLevels(gameAmbition, gameDanger, selected.Values);
        var active = builder.mapMods;
        var originals = new List<KeyValuePair<string, Choice>>();
        if (active is not null)
        {
            for (int i = 0; i < active.Count; i++)
            {
                var mod = active[i];
                originals.Add(new(mod.ident, Describe(mod)));
            }
        }

        var plan = Planner.Choose(seed, options, ambition, danger, selected, originals, pool);
        var kept = new HashSet<string>();
        var remove = new List<int>();
        for (int i = 0; i < originals.Count; i++)
            if (!plan.Final.ContainsKey(originals[i].Key) || !kept.Add(originals[i].Key)) remove.Add(i);
        var additions = plan.Final.Keys.Where(id => !kept.Contains(id))
            .Select(id => manager.lookup[id]).ToArray();
        if (active is not null)
            for (int i = remove.Count - 1; i >= 0; i--) active.RemoveAt(remove[i]);
        if (ambition != gameAmbition) builder.SetAmbitionLevel(ambition);
        if (danger != gameDanger) builder.SetDangerLevel(danger);
        foreach (var mod in additions) builder.AddInitialMapMod(mod);
        Planner.Remember(seed, options, plan);
        return plan.Drawn;
    }

    internal static void Report(int seed, AddOptions options, LevelBuilder builder, string[] randomIds, MapLayout? layout, string? error)
    {
        var mods = builder.GetMapMods();
        var ids = new HashSet<string>();
        var traits = new List<string>();
        var actual = new Dictionary<string, Choice>();
        for (int i = 0; i < mods.Count; i++)
        {
            var mod = mods[i];
            var choice = Describe(mod);
            if (!ids.Add(mod.ident) || actual.Any(p => choice.Conflicts(mod.ident, p.Key, p.Value)))
                error ??= $"游戏生成的「{choice.Title}」重复或互斥";
            actual[mod.ident] = choice;
            traits.Add(choice.Title);
        }
        var ambition = builder.GetAmbitionLevel();
        var danger = builder.startingDangerLevel;
        if ((options.Ambition.HasValue && options.Ambition != ambition) ||
            (options.Danger.HasValue && options.Danger != danger) || !ids.IsSupersetOf(options.Modifiers) ||
            !ids.IsSupersetOf(randomIds) || options.Modifiers.Any(id => actual.TryGetValue(id, out var item) && !item.Fits(ambition, danger)) ||
            (options.TotalMax.HasValue
                ? traits.Count < options.TotalMin || traits.Count > options.TotalMax
                : randomIds.Length < options.RandomMin || randomIds.Length > options.RandomMax))
            error ??= "实际生成结果与设置不符";
        var randomTraits = randomIds.Where(actual.ContainsKey).Select(id => actual[id].Title).ToArray();
        if (options.ProceduralMap && layout is null) error ??= "未能生成地块";
        if (options.BatchId is not null && !Planner.Matches(seed, ids)) error ??= "游戏最终特性与本批分配结果不符";
        if (error is not null) Planner.Forget(seed);
        var validMap = error is null ? layout : null;
        ProceduralWorldLoader.Record(seed, builder.mapfile, validMap);
        var map = validMap is null ? null : new { land = validMap.Land, links = validMap.Links, rivers = validMap.Rivers, bridges = validMap.Bridges, fingerprint = validMap.Fingerprint };
        Results[seed] = new { options, ambition, danger, traits, traitIds = actual.Keys.ToArray(), randomTraits, randomIds, map, error };
        Publish(Path.ChangeExtension(SessionFile, ".results.json"), Results);
    }
}

