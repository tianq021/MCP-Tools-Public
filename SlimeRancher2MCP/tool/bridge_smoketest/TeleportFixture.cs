// Stand-ins for the generated game types used by the navigation tool.
namespace Il2Cpp
{
    public class SystemContext
    {
        public static SystemContext Instance { get; set; }
        public SystemContext SceneLoader => this;
        public bool IsSceneLoadInProgress { get; set; }
        public bool MainMenu { get; set; }
        public Group CurrentSceneGroup { get; set; } = new("group-test");
        public bool IsCurrentSceneGroupGameplay() => !MainMenu;
        public bool IsCurrentSceneGroupMainMenu() => MainMenu;
    }

    public class SceneContext
    {
        public static SceneContext Instance { get; } = new();
        public Il2CppMonomiPark.SlimeRancher.DataModel.GameModel GameModel { get; } = new();
        public SceneContext Player => this;
        public PlayerFixture PlayerState { get; } = new();
        public SceneContext transform => this;
        public UnityEngine.Vector3 position { get; set; } = new(1, 2, -3);
        public ZoneTrackerFixture PlayerZoneTracker { get; } = new();
        public ClockFixture TimeDirector { get; } = new();
        public MarketFixture PlortEconomyDirector { get; } = new();
        public PediaDirector PediaDirector { get; } = new();
        public PinnedDirector PinnedRecipeDirector { get; } = new();
        public GadgetDirector GadgetDirector { get; } = new();
        public Il2CppMonomiPark.SlimeRancher.DataModel.MapFixture MapDirector;
        public readonly System.Collections.Generic.HashSet<Il2CppMonomiPark.SlimeRancher.DataModel.Zone> UnlockedZones = new();
        public bool IsZoneUnlocked(Il2CppMonomiPark.SlimeRancher.DataModel.Zone zone) => UnlockedZones.Contains(zone);
        public T GetComponentInChildren<T>(bool includeInactive) where T : class =>
            Controller as T;
        public readonly Il2CppMonomiPark.SlimeRancher.Player.CharacterController.SRCharacterController Controller = new();
    }
}

namespace Il2CppMonomiPark.SlimeRancher.Player.CharacterController
{
    public record struct Vector(float x, float y, float z);
    public class SRCharacterController
    {
        public SRCharacterController Motor => this;
        public Vector TransientPosition { get; private set; }
        public int MoveCount { get; private set; }
        public void SetPosition(Vector position, bool bypassInterpolation)
        {
            MoveCount++;
            TransientPosition = position;
        }
        public void ResetVelocity(bool flag) { }
        public void StopAbilitiesForTeleport() { }
    }
}
