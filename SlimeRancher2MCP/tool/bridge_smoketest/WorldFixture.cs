using System.Collections.Generic;
using UnityEngine;

namespace Il2CppSystem.Collections.Generic
{
    public class Dictionary<K, V> : System.Collections.Generic.Dictionary<K, V> { }
}

namespace UnityEngine
{
    public class GameObject
    {
        public GameObject transform => this;
        public GameObject gameObject => this;
        public Vector3 position => new(3, 4, 5);
        public string ZoneId { get; init; }
    }
}

namespace SlimeRancher2Bridge
{
    internal static class ToolsZone
    {
        internal static void AddZone(Dictionary<string, object> row, GameObject obj, Vector3? position)
        {
            row["zone"] = obj?.ZoneId ?? (position?.x == 123 ? "home" : null);
            row["zone_title"] = row["zone"];
        }

        internal static bool IsLoaded(string id) => false;
    }
}

namespace Il2CppMonomiPark.SlimeRancher.DataModel
{
    using Map = Il2CppSystem.Collections.Generic;

    public partial class GameModel
    {
        public bool isInitialized;
        public Map.Dictionary<string, Gordo> gordos = new();
        public Map.Dictionary<string, Pod> pods = new();
        public Map.Dictionary<string, Door> doors = new();
        public Map.Dictionary<string, LandPlotModel> landPlots = new();
    }

    public class Gordo
    {
        public GameObject gameObj { get; set; }
        public Il2Cpp.Group identifiableType => new("CottonGordo");
        public bool GordoSeen => true;
        public bool HasPopped() => false;
        public int GordoEatenCount => 7;
        public int targetCount => 30;
    }

    public enum State { LOCKED, OPEN, CLOSED }
    public record PodState(State Value);
    public record Pod(PodState state, GameObject gameObj);
    public record Door(State state, GameObject gameObj);
    public record Grower(Il2Cpp.Group PrimaryResourceType);
    public record Actors(HashSet<ActorId> trackedActorIds);
    public record LandPlotModel(Il2Cpp.LandPlot.Id typeId, Grower resourceGrowerDefinition, Actors trackedActors,
        HashSet<string> upgrades, GameObject gameObj)
    {
        static int nextPointer;
        public bool HasUpgrade(Il2Cpp.LandPlot.Upgrade upgrade) => upgrades?.Contains(upgrade.ToString()) == true;
        public System.IntPtr Pointer { get; } = new(System.Threading.Interlocked.Increment(ref nextPointer));
    }
}
