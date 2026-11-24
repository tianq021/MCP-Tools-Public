extern alias GameAssembly;
extern alias UnityGame;
using System.Security.Cryptography;
using System.Text;
using GameAssembly::com.ndemiccreations.survivors;
using GameAssembly::com.ndemiccreations.survivors.scenarioscreator;
using HarmonyLib;
using UnityGame::UnityEngine;
using Path = System.IO.Path;

namespace AfterIncLevelTraits;

internal static class ProceduralWorldLoader
{
    private sealed record Prepared(string Map, MapLayout Layout);
    private static readonly Dictionary<int, Prepared> PreparedMaps = new();
    private static readonly Dictionary<int, Prepared> Accepted = new();
    private static readonly string SavedMaps = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "AfterIncTrainer", "added_levels", "saved_maps_v1");
    [ThreadStatic] private static string? pending;

    internal static void Register(Harmony harmony)
    {
        var load = AccessTools.DeclaredMethod(typeof(WorldLoadSaver), nameof(WorldLoadSaver.LoadFromLaunchData));
        var parse = AccessTools.DeclaredMethod(typeof(WorldLoadSaver), "LoadFromJSON");
        harmony.Patch(load, prefix: Hook(nameof(Begin)), finalizer: Hook(nameof(End)));
        harmony.Patch(parse, prefix: Hook(nameof(SupplyGeometry)));
    }

    private static HarmonyMethod Hook(string name) => new(AccessTools.DeclaredMethod(typeof(ProceduralWorldLoader), name));

    internal static MapLayout Prepare(int seed, string map)
    {
        if (PreparedMaps.TryGetValue(seed, out var saved) && saved.Map == map) return saved.Layout;
        var original = Resources.Load<TextAsset>(map);
        if (original == null) throw new InvalidDataException($"找不到地图资源 {map}");
        var layout = ProceduralMap.Generate(original.text, seed);
        var parsed = new ScenarioCreatorWorldLoader().LoadMapFromJson(layout.Json);
        if (!parsed.success || parsed.zones.Count != layout.Land)
            throw new InvalidDataException($"游戏未能解析新地块：{parsed.errorMessage}");
        if (PreparedMaps.Count >= 8) PreparedMaps.Remove(PreparedMaps.Keys.First());
        PreparedMaps[seed] = new(map, layout);
        return layout;
    }

    internal static void Record(int seed, string map, MapLayout? verified)
    {
        if (verified is null) { Accepted.Remove(seed); return; }
        if (Accepted.Count >= 8 && !Accepted.ContainsKey(seed)) Accepted.Remove(Accepted.Keys.First());
        Accepted[seed] = new(map, verified);
    }

    private static void Begin(LaunchData __0, out string? __state)
    {
        __state = pending;
        pending = null;
        var level = __0.builder;
        if (level == null || !level.isRandomLevel) return;
        var seed = level.baseSeed;
        var map = level.mapfile;
        var identity = $"{level.ident}\n{seed}\n{map}\n{__0.settlementName}";
        var key = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(identity)));
        var path = Path.Combine(SavedMaps, key + ".json");
        if (__0.isResumeGame || __0.loadContext != null)
        {
            if (File.Exists(path)) pending = File.ReadAllText(path);
            if (pending is not null) Plugin.MapLog($"恢复已存地图 {level.ident} {key[..10]}");
            if (pending is not null || !Accepted.ContainsKey(seed)) return;
        }
        if (Accepted.TryGetValue(seed, out var active) && active.Map == map &&
            AddedLevels.ForSeed(seed)?.ProceduralMap == true)
        {
            Directory.CreateDirectory(SavedMaps);
            var temp = path + ".tmp";
            File.WriteAllText(temp, active.Layout.Json);
            File.Move(temp, path, true);
            pending = active.Layout.Json;
            Plugin.MapLog($"加载随机地块 {level.ident} {active.Layout.Fingerprint[..12]}");
        }
        else if (!__0.isResumeGame && __0.loadContext == null && File.Exists(path))
        {
            File.Delete(path);
        }
    }

    private static void SupplyGeometry(ref string __0)
    {
        if (pending != null) __0 = pending;
    }

    private static void End(string? __state) => pending = __state;
}

