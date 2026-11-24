namespace Il2Cpp
{
    public record Group(string ReferenceId);

    public class GameContext
    {
        public static GameContext Instance { get; } = new();
        public GameContext AutoSaveDirector => this;
        public string SaveName = "test-save";
        public string CurrentSaveGameName() => SaveName;
    }

    public class ZoneTrackerFixture
    {
        public Group GetCurrentZone() => new("zone-test");
    }

    public class PlayerFixture
    {
        public object _model { get; set; } = new();
        public int Health = 37, Energy = 82;
        public AmmoFixture Ammo { get; } = new();
        public void SetHealth(int target) => Health = target;
        public void SetEnergy(int target) => Energy = target;
        public int GetCurrHealth() => Health;
        public int GetMaxHealth() => 100;
        public int GetCurrEnergy() => Energy;
        public int GetMaxEnergy() => 150;
    }

    public class ClockFixture
    {
        public object _worldModel { get; set; } = new();
        public int CurrDay() => 4;
        public float CurrHour() => 13.75f;
        public bool isPaused() => true;
        public bool IsFastForwarding() => false;
    }
}

namespace UnityEngine
{
    public record struct Vector3(float x, float y, float z);
}

namespace SlimeRancher2Bridge
{
    static class ToolsCurrency
    {
        internal static int? ReadDefaultAmount(object state) => 123;
    }

    static class ToolsLanding
    {
        internal static bool Suitable = true;
        internal static System.Collections.Generic.Dictionary<string, object> Probe(UnityEngine.Vector3 point)
            => new() { ["suitable"] = Suitable };
    }
}
