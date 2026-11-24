using System.Collections.Generic;
using Il2Cpp;
using UnityEngine;

namespace Il2Cpp
{
    public class LandPlot
    {
        public enum Id { CORRAL, GARDEN, SILO, EMPTY }
        public enum Upgrade { STORAGE2, STORAGE3, STORAGE4 }
    }

    public class IdentifiableType
    {
        public string ReferenceId { get; init; }
        public string name { get; init; }
        public T TryCast<T>() where T : class => this as T;
    }

    public class SlimeDefinition : IdentifiableType
    {
        public bool IsLargo { get; init; }
        public DietFixture Diet { get; init; }
    }

    public class DietFixture
    {
        public IdentifiableType[] MajorFoodIdentifiableTypeGroups, AdditionalFoodIdents, FavoriteIdents, ProduceIdents;
    }
    public record SlimeDefinitions(SlimeDefinition[] Slimes);
}

namespace Il2CppMonomiPark.SlimeRancher.DataModel
{
    public partial class GameModel
    {
        public SlimeDefinitions slimeDefinitions;
        public readonly List<GadgetModel> gadgets = new();
        public readonly Dictionary<ActorId, IdentifiableModel> actors = new();
        public List<GadgetModel> AllGadgets() => gadgets;
        public bool TryGetIdentifiableModel(ActorId id, out IdentifiableModel model) => actors.TryGetValue(id, out model);
    }

    public record struct ActorId(long Value);
    public class IdentifiableModel
    {
        public ActorId actorId;
        public IdentifiableType ident;
        public Vector3 lastPosition;
        public Group SceneGroup;
        public T TryCast<T>() where T : class => this as T;
    }
    public class GadgetModel : IdentifiableModel
    {
        public GameObject Transform;
        public bool IsPrePlaced;
    }
    public record Observable<T>(T Value);
    public record TaskFixture(string SourceType, string TargetType, IdentifiableType TargetIdentType, string SinkType);
    public record ExplorerFixture(string State);
    public class DroneStationGadgetModel : GadgetModel
    {
        public string Type;
        public bool Initialized;
        public Observable<bool> IsDroneAtStation;
        public Observable<double> EnergyDepleteTime;
        public TaskFixture TaskData;
        public ExplorerFixture Program;
    }
}
