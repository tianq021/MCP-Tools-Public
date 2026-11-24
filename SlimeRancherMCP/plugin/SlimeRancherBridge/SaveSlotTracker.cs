// SaveSlotTracker —— 记录"当前正在玩的存档文件名"（形如 20250628093547_1_3）。
//
// 为什么需要它：游戏自己不保留已加载的 .sav 文件名。
//   - SavedGame.GetName() 只返回"游戏名"（20250628093547_1），同一局的 5 个自动存档都同前缀；
//   - AutoSaveDirector.LoadSave 里的 saveName 只是局部变量；
//   - FileStorageProvider.GetGameId() 恒返回空串，currentGameId 因此也是空串。
// 所以用 Harmony 监听两个加载入口，把 saveName 记下来：
//   AutoSaveDirector.BeginLoad(string gameName, string saveName, Action onError)
//   AutoSaveDirector.LoadFallbackSave(string gameName, string saveName, bool promptDLCPurgedException, Action onError)
//
// C# 5 语法；Harmony 2.2.1（SRML\Libs\0Harmony.dll，build.ps1 已引用）。
using System;
using System.Reflection;
using HarmonyLib;

namespace SlimeRancherBridge
{
    static class SaveSlotTracker
    {
        const string HarmonyId = "SlimeRancherBridge.saveSlotTracker";

        static string currentSave;
        static bool installed;
        static string installError;

        /// <summary>最近一次被游戏加载的 .sav 文件名；未加载/未捕获时为 null。</summary>
        public static string CurrentSave
        {
            get { return currentSave; }
        }

        public static bool Installed
        {
            get { return installed; }
        }

        public static string InstallError
        {
            get { return installError; }
        }

        public static void TryInstall(Action<string> log)
        {
            if (installed)
            {
                return;
            }
            try
            {
                var harmony = new Harmony(HarmonyId);
                Patch(harmony, "BeginLoad", "BeginLoadPrefix");
                Patch(harmony, "LoadFallbackSave", "FallbackLoadPrefix");
                installed = true;
                if (log != null)
                {
                    log("save slot tracker: installed (BeginLoad + LoadFallbackSave)");
                }
            }
            catch (Exception e)
            {
                installError = e.GetType().Name + ": " + e.Message;
                if (log != null)
                {
                    log("save slot tracker: install FAILED " + installError);
                }
            }
        }

        static void Patch(Harmony harmony, string methodName, string prefixName)
        {
            MethodInfo target = AccessTools.Method(typeof(AutoSaveDirector), methodName);
            if (target == null)
            {
                throw new MissingMethodException("AutoSaveDirector." + methodName);
            }
            MethodInfo prefix = typeof(SaveSlotTracker).GetMethod(prefixName,
                BindingFlags.Static | BindingFlags.NonPublic);
            if (prefix == null)
            {
                throw new MissingMethodException("SaveSlotTracker." + prefixName);
            }
            harmony.Patch(target, new HarmonyMethod(prefix), null, null);
        }

        // Harmony 按位置注入：__1 = 第二个参数 saveName（不用参数名匹配，避免改名失效）。
        static void BeginLoadPrefix(string __1)
        {
            Note(__1);
        }

        static void FallbackLoadPrefix(string __1)
        {
            Note(__1);
        }

        static void Note(string saveName)
        {
            if (!String.IsNullOrEmpty(saveName))
            {
                currentSave = saveName;
            }
        }
    }
}
