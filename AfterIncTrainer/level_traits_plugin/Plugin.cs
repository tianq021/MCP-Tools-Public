extern alias GameAssembly;

using System.Security.Cryptography;
using System.Text.Json;
using BepInEx;
using BepInEx.Logging;
using BepInEx.Unity.IL2CPP;
using HarmonyLib;
using GameAssembly::com.ndemiccreations.survivors;
using GameAssembly::com.ndemiccreations.survivors.objectives;
using Path = System.IO.Path;

namespace AfterIncLevelTraits;

[BepInPlugin(Id, "After Inc Expedition Traits", "1.6.2")]
public sealed class Plugin : BasePlugin
{
    private const string Id = "local.afterinc.expeditiontraits";
    private const string Build = "26049821";
    private const string ExpectedHash = "08719A4423F94C5563CEC3D82BDC647FF44A09345BC2C9E88F222CDD5EAE782A";
    private const string Version = "26049821.7";
    private static readonly string DataDirectory = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "AfterIncTrainer", "level_traits");
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);
    private static readonly object SnapshotLock = new();
    private static readonly Dictionary<int, Expedition> Expeditions = new();
    private static ManualLogSource logger = null!;
    private static TraitProfile? profile;
    private static JsonElement? profileDocument;
    private static string? profileError;
    private static DateTimeOffset startedAt;
    private static Modifier[] catalogue = Array.Empty<Modifier>();
    private static bool catalogueReady;
    private static bool snapshotDirty;
    private Harmony? harmony;

    [ThreadStatic]
    private static Generation? current;

    internal static void MapLog(string message) => logger.LogInfo(message);

    public override void Load()
    {
        logger = Log;
        startedAt = DateTimeOffset.Now;
        Directory.CreateDirectory(DataDirectory);
        using var game = File.OpenRead(Path.Combine(Paths.GameRootPath, "GameAssembly.dll"));
        using var digest = SHA256.Create();
        if (Convert.ToHexString(digest.ComputeHash(game)) != ExpectedHash)
        {
            TryWriteSnapshot("unsupported_build");
            Log.LogWarning("Expedition traits disabled for an unknown GameAssembly.dll.");
            return;
        }

        ReadProfile();
        try
        {
            var randomLevel = AccessTools.DeclaredMethod(
                typeof(LevelParameters), "RandomLevel", new[] { typeof(ObjectiveLoader), typeof(int) })
                ?? throw new MissingMethodException("LevelParameters.RandomLevel");
            var preApply = AccessTools.DeclaredMethod(
                typeof(MapModDelegates), nameof(MapModDelegates.OnMapModsPreApply))
                ?? throw new MissingMethodException("MapModDelegates.OnMapModsPreApply");
            harmony = new Harmony(Id);
            ProceduralWorldLoader.Register(harmony);
            harmony.Patch(randomLevel, prefix: PatchMethod(nameof(BeginGeneration)),
                postfix: PatchMethod(nameof(PublishGeneration)),
                finalizer: PatchMethod(nameof(EndGeneration)));
            harmony.Patch(preApply, prefix: PatchMethod(nameof(ApplyTraits)));
        }
        catch
        {
            harmony?.UnpatchSelf();
            TryWriteSnapshot("hook_failed");
            throw;
        }
        AddedLevels.Ready();
        TryWriteSnapshot("ready");
    }

    private static HarmonyMethod PatchMethod(string name) =>
        new(AccessTools.DeclaredMethod(typeof(Plugin), name));

    private static void ReadProfile()
    {
        try
        {
            var file = Path.Combine(DataDirectory, "profile.json");
            if (!File.Exists(file))
                return;
            using var document = JsonDocument.Parse(File.ReadAllText(file));
            profile = document.RootElement.Deserialize<TraitProfile>(JsonOptions);
            if (profile is null || !profile.IsValid(Build))
                throw new InvalidDataException("Expedition profile is invalid.");
            profileDocument = document.RootElement.Clone();
        }
        catch (Exception error)
        {
            profile = null;
            profileError = error.Message;
            logger.LogWarning(profileError);
        }
    }

    private static void BeginGeneration(int __1, out Generation? __state)
    {
        __state = current;
        current = new Generation(__1);
        try { current.Options = AddedLevels.ForSeed(__1); }
        catch (Exception error) { logger.LogWarning($"读取新增关卡配置失败：{error.Message}"); }
    }

    private static void EndGeneration(Generation? __state) => current = __state;

    private static void ApplyTraits(LevelBuilder __0)
    {
        var generation = current;
        if (generation is null || generation.Attempted || __0 is null ||
            !__0.isRandomLevel || __0.baseSeed != generation.Seed)
            return;
        generation.Attempted = true;

        try
        {
            var mods = MapModManager.sharedInstance;
            AddedLevels.PublishCatalogue(mods);
            if (generation.Options is not null)
            {
                var seeding = GameSettings.sharedInstance.randLevelSeeding;
                var seed = generation.Seed;
                if (seed == seeding.SeedOne || seed == seeding.SeedTwo || seed == seeding.SeedThree ||
                    seed == seeding.SeedFourLocked || seed == seeding.SeedFiveExtra || seed == seeding.SeedFivePrevious)
                    throw new InvalidDataException("种子与游戏原有远征重合，请清除后重新添加");
                if (generation.Options.ProceduralMap)
                    generation.Layout = ProceduralWorldLoader.Prepare(seed, __0.mapfile);
                generation.RandomIds = AddedLevels.Apply(__0, generation.Options, mods, seed);
                return;
            }
            PublishCatalogue(mods);
            if (profile is null || profile.Seed != generation.Seed)
                return;

            var dangerLimit = profile.Dangers?.Max == 4 &&
                GameSettings.sharedInstance.randLevelSeeding.IsExtraExpedition(generation.Seed) ? 3 : 4;
            var eligible = new Dictionary<string, MapMod>(StringComparer.Ordinal);
            for (var i = 0; i < mods.all.Count; i++)
            {
                var mod = mods.all[i];
                if (IsSelectable(mod))
                    eligible.TryAdd(mod.ident, mod);
            }
            var active = __0.GetMapMods();
            var original = new Effect[active.Count];
            for (var i = 0; i < original.Length; i++)
                original[i] = Describe(active[i]);

            var (roll, error, availableCount) = TraitLottery.Pick(profile, __0.GetAmbitionLevel(),
                __0.startingDangerLevel, dangerLimit, eligible.Values.Select(Describe).ToArray(), original);
            generation.Error = error;
            generation.AvailableCount = availableCount;
            if (roll is null)
                return;

            foreach (var id in roll.AddedModifiers)
                __0.AddInitialMapMod(eligible[id]);
            if (profile.Dangers is not null)
                __0.SetDangerLevel(roll.Danger);
            if (profile.Ambitions is not null)
                __0.SetAmbitionLevel(roll.Ambition);
            generation.Roll = roll;
        }
        catch (Exception error)
        {
            generation.Error = generation.Options is null ? "native_error" : error.Message;
            logger.LogWarning($"Expedition {generation.Seed}: {error}");
        }
    }

    private static bool IsSelectable(MapMod mod) =>
        !mod.disabled && !mod.needs_infestation &&
        (mod.required_modifiers_and_not is null || mod.required_modifiers_and_not.Length == 0);

    private static Effect Describe(MapMod mod) =>
        new(mod.ident, mod.family, mod.ambition_min, mod.ambition_max, mod.danger_level_min, mod.danger_level_max);

    private static void PublishCatalogue(MapModManager manager)
    {
        lock (SnapshotLock)
        {
            if (catalogueReady)
                return;
            var result = new List<Modifier>();
            for (var i = 0; i < manager.all.Count; i++)
            {
                var mod = manager.all[i];
                if (IsSelectable(mod))
                    result.Add(new Modifier(mod.ident, mod.title?.ToLocalisedString() ?? mod.ident,
                        mod.ambition_min, mod.ambition_max, mod.danger_level_min, mod.danger_level_max));
            }
            catalogue = result.OrderBy(mod => mod.Id, StringComparer.Ordinal).ToArray();
            catalogueReady = true;
            snapshotDirty = true;
        }
    }

    private static void PublishGeneration(LevelBuilder __result)
    {
        var generation = current;
        if (generation is null || __result is null)
            return;
        try
        {
            if (generation.Options is not null)
            {
                AddedLevels.Report(generation.Seed, generation.Options, __result, generation.RandomIds, generation.Layout,
                    generation.Attempted ? generation.Error : "未进入关卡特性生成步骤");
                return;
            }
            var mods = __result.GetMapMods();
            var active = new string[mods.Count];
            for (var i = 0; i < active.Length; i++)
                active[i] = mods[i].ident;
            var requested = profile is not null && profile.Seed == generation.Seed;
            var roll = generation.Roll;
            var ambition = __result.GetAmbitionLevel();
            var danger = __result.startingDangerLevel;
            var added = roll?.AddedModifiers.Where(id => active.Contains(id, StringComparer.Ordinal)).ToArray()
                ?? Array.Empty<string>();
            var applied = roll is not null && added.Length == roll.AddedModifiers.Length &&
                (profile?.Ambitions is null || ambition == roll.Ambition) &&
                (profile?.Dangers is null || danger == roll.Danger);
            var result = new Expedition(generation.Seed, __result.title ?? __result.ident,
                ambition, danger, active, added, applied, generation.AvailableCount,
                generation.Error ?? (requested && !applied ? "generation_mismatch" : null));
            lock (SnapshotLock)
            {
                if (Expeditions.TryGetValue(result.Seed, out var existing) &&
                    existing with { Modifiers = active, AddedModifiers = added } == result &&
                    existing.Modifiers.SequenceEqual(active) && existing.AddedModifiers.SequenceEqual(added))
                {
                    if (snapshotDirty)
                        TryWriteSnapshot("ready");
                }
                else
                {
                    Expeditions.Remove(result.Seed);
                    if (Expeditions.Count >= 40)
                        Expeditions.Remove(Expeditions.Keys.First());
                    Expeditions.Add(result.Seed, result);
                    TryWriteSnapshot("ready");
                }
            }
        }
        catch (Exception error)
        {
            logger.LogWarning($"Could not report expedition {generation.Seed}: {error.Message}");
        }
    }

    private static void TryWriteSnapshot(string state)
    {
        var path = Path.Combine(DataDirectory, "observed.json");
        var temp = Path.Combine(DataDirectory, $"observed.{Environment.ProcessId}.tmp");
        var data = new
        {
            schema = 1, build = Build, version = Version, state, pid = Environment.ProcessId,
            startedAt, profile = profileDocument, profileError, catalogue, expeditions = Expeditions.Values.ToArray()
        };
        snapshotDirty = true;
        try
        {
            File.WriteAllText(temp, JsonSerializer.Serialize(data, JsonOptions));
            File.Move(temp, path, overwrite: true);
            snapshotDirty = false;
        }
        catch (Exception error)
        {
            logger.LogWarning($"Could not publish expedition data: {error.Message}");
        }
    }

    private sealed class Generation
    {
        internal Generation(int seed) => Seed = seed;

        internal readonly int Seed;
        internal AddOptions? Options;
        internal MapLayout? Layout;
        internal string[] RandomIds = Array.Empty<string>();
        internal bool Attempted;
        internal TraitRoll? Roll;
        internal int AvailableCount;
        internal string? Error;
    }

    private sealed record Modifier(string Id, string Title, float AmbitionMin, float AmbitionMax, float DangerMin, float DangerMax);
    private sealed record Expedition(int Seed, string Title, float Ambition, int Danger, string[] Modifiers,
        string[] AddedModifiers, bool Applied, int AvailableCount, string? Error);
}
