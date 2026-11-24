// Stub —— 让 BridgeServer / BridgeConfig / ToolsCore 能在「没有 MelonLoader、没有游戏」的进程里编译。
// 真实插件里 SR2BridgeMod 是 MelonLoader.MelonMod 子类（见 plugin/.../MelonMain.cs）。
namespace MelonLoader
{
    // 只为通过编译的替身；真实 MelonLoader 提供同名类型
    public class MelonMod
    {
        public static MelonMod[] RegisteredMelons => System.Array.Empty<MelonMod>();
        public (string Name, string Version) Info { get; set; }
    }
}

namespace SlimeRancher2Bridge
{
    public class SR2BridgeMod
    {
        public const string ModVersion = "0.1.0-smoketest";
        internal static BridgeServer Server;
        internal static BridgeConfig Config;
    }
}
