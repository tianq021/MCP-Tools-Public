using System;
using System.Collections.Generic;
using System.Linq;
using Il2Cpp;
using Il2CppMonomiPark.SlimeRancher.DataModel;
using Il2CppMonomiPark.SlimeRancher.Pedia;
using Map = Il2CppSystem.Collections.Generic;

namespace UnityEngine
{
    public class Component { public GameObject transform { get; } = new(); }
    public enum FindObjectsSortMode { None }
    public static class Object
    {
        public static Component[] Active = Array.Empty<Component>();
        public static T[] FindObjectsByType<T>(FindObjectsSortMode mode) => Active.OfType<T>().ToArray();
    }
}

namespace Il2Cpp
{
    public class ResourceNodeSpawner : UnityEngine.Component { public ResourceNodeSpawnerModel _model; }
    public class PediaDirector
    {
        public PediaModel _pediaModel;
        public List<PediaEntry> catalog = new();
        public IEnumerable<PediaEntry> AllEntries() => catalog;
        public bool IsUnlocked(PediaEntry entry) => _pediaModel.unlocked.Contains(entry.PersistenceId);
    }
}

namespace Il2CppMonomiPark.SlimeRancher.World.ResearchDrone
{
    public record LogEntry(string ReferenceId, string[] Pages);
    public class ResearchDroneController : UnityEngine.Component
    {
        public ResearchDroneModel _model;
        public LogEntry ResearchDroneEntry;
    }
}

namespace Il2CppMonomiPark.SlimeRancher.DataModel
{
    public partial class GameModel
    {
        public Map.Dictionary<string, ResourceNodeSpawnerModel> resourceNodeSpawnerModels = new();
        public Map.Dictionary<string, ResearchDroneModel> researchDrones = new();
        public Map.Dictionary<string, TeleporterModel> teleporters = new();
    }
    public record PediaModel(HashSet<string> unlocked);
    public record ResourceNodeSpawnerModel(IntPtr Pointer, string nodeState, IdentifiableType resourceNodeDefinition, List<IdentifiableType> resourcesToSpawn);
    public record ResearchDroneModel(IntPtr Pointer, bool read);
    public record TeleporterNodeModel(string NodeId);
    public record NodeInfo(TeleporterNodeModel TeleporterNodeModel, Group SceneGroup);
    public record TeleporterModel(NodeInfo source, Dictionary<string, NodeInfo> destinations, bool ForceSceneTransition);
}

namespace Il2CppMonomiPark.SlimeRancher.Pedia
{
    public record PediaEntry(string PersistenceId, string name, string Title);
}

namespace SlimeRancher2Bridge
{
    static class Il2CppUtil
    {
        public static IEnumerable<T> Items<T>(IEnumerable<T> sequence) => sequence;
        public static string Localized(string text) => text;
    }
}
