using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Threading.Tasks;
using Il2Cpp;
using UnityEngine;

namespace SlimeRancher2Bridge
{
    internal static class QuickTidy
    {
        static Task running = Task.CompletedTask;

        internal static void HandleKey()
        {
            var e = Event.current;
            if (e == null || e.type != EventType.KeyUp || e.keyCode != KeyCode.F8
                || !Application.isFocused || ChatOverlay.IsOpen) return;

            var loader = SystemContext.Instance?.SceneLoader;
            if (loader == null || loader.IsSceneLoadInProgress || loader.CurrentSceneGroup == null
                || !loader.IsCurrentSceneGroupGameplay()) return;
            e.Use();
            if (!running.IsCompleted) return;

            string id = "f8-" + Guid.NewGuid().ToString("N");
            // The Python workflows call back into the game's main-thread RPC pump.
            running = Task.Run(() =>
            {
                Run("tidy", "背包", id);
                Run("plorts", "收集器", id);
            });
        }

        static void Run(string command, string label, string id)
        {
            try
            {
                var root = Path.GetDirectoryName(Path.GetDirectoryName(BridgeConfig.ConfigPath));
                var start = new ProcessStartInfo("python.exe")
                {
                    WorkingDirectory = root, UseShellExecute = false, CreateNoWindow = true,
                    RedirectStandardOutput = true, RedirectStandardError = true,
                    StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8
                };
                start.ArgumentList.Add("-B");
                start.ArgumentList.Add(Path.Combine(root, "tool", "ranch.py"));
                start.ArgumentList.Add(command);
                start.ArgumentList.Add("--apply");
                start.ArgumentList.Add(id + "-" + command);
                using var child = Process.Start(start);
                var errors = child.StandardError.ReadToEndAsync();
                var summary = child.StandardOutput.ReadToEnd().Trim();
                child.WaitForExit();
                string detail = errors.GetAwaiter().GetResult().Trim();
                if (summary.Length == 0)
                    summary = detail.Length > 0 ? detail : "整理命令退出，代码 " + child.ExitCode;
                ChatBus.AddIncoming(label + " · " + summary);
            }
            catch (Exception ex)
            {
                ChatBus.AddIncoming(label + "整理失败 · " + ex.GetBaseException().Message);
            }
        }
    }
}
