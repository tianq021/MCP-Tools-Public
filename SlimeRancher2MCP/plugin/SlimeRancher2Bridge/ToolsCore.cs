// ToolsCore —— P0 工具集：加载器/安装/存档的「文件与元数据」层。
// 这些 handler 都在后台线程执行（不碰 Unity API），因此游戏加载中也能应答。
// P1 起新增「需要主线程」的游戏状态工具时，注册时传 mainThread:true。
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;
using MelonLoader;

namespace SlimeRancher2Bridge
{
    public static class ToolsCore
    {
        static string cachedGameDir;
        static string cachedSaveDir;

        public static string GameDirectory
        {
            get
            {
                if (cachedGameDir != null) return cachedGameDir;
                // 测试/特殊场景可用环境变量覆盖（冒烟测试会在无游戏进程时读真实游戏目录）
                string overridden = Environment.GetEnvironmentVariable("SR2_GAME_DIR");
                if (!string.IsNullOrEmpty(overridden))
                {
                    cachedGameDir = overridden;
                    return cachedGameDir;
                }
                try
                {
                    string exe = Process.GetCurrentProcess().MainModule.FileName;
                    cachedGameDir = Path.GetDirectoryName(exe);
                }
                catch
                {
                    cachedGameDir = AppDomain.CurrentDomain.BaseDirectory;
                }
                return cachedGameDir;
            }
        }

        public static string SaveDirectory
        {
            get
            {
                if (cachedSaveDir != null) return cachedSaveDir;
                // Unity persistentDataPath = %USERPROFILE%\AppData\LocalLow\<company>\<product>，
                // company/product 以游戏本体 SlimeRancher2_Data\app.info 为准（本机实测 MonomiPark / SlimeRancher2）。
                string localLow = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "AppData", "LocalLow");
                var candidates = new List<string>();
                string[] appInfo = ReadAppInfo();
                if (appInfo != null) candidates.Add(Path.Combine(localLow, appInfo[0], appInfo[1]));
                candidates.Add(Path.Combine(localLow, "MonomiPark", "SlimeRancher2"));
                candidates.Add(Path.Combine(localLow, "Monomi Park", "Slime Rancher 2"));
                foreach (string candidate in candidates)
                {
                    if (Directory.Exists(candidate))
                    {
                        cachedSaveDir = candidate;
                        return cachedSaveDir;
                    }
                }
                // 目录尚未生成（首次启动前）时，返回按 app.info 推导的那个。
                cachedSaveDir = candidates[0];
                return cachedSaveDir;
            }
        }

        static string[] ReadAppInfo()
        {
            try
            {
                string info = Path.Combine(GameDirectory, "SlimeRancher2_Data", "app.info");
                if (!File.Exists(info)) return null;
                string[] lines = File.ReadAllLines(info, Encoding.UTF8);
                if (lines.Length < 2) return null;
                string company = lines[0].Trim();
                string product = lines[1].Trim();
                return company.Length > 0 && product.Length > 0 ? new[] { company, product } : null;
            }
            catch
            {
                return null;
            }
        }

        public static void Register(BridgeServer srv)
        {
            srv.Register("sr2_health", Health);
            srv.Register("sr2_real_time", WallClock.Read);
            srv.Register("sr2_gameinfo", GameInfo);
            srv.Register("sr2_saves", Saves);
            srv.Register("sr2_echo", Echo);
        }

        public static void RefreshSnapshot(BridgeServer server)
        {
            var snap = new Dictionary<string, object>(StringComparer.Ordinal);
            snap["snapshot_at"] = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss.fff");
            snap["snapshot_unix"] = (long)(DateTime.UtcNow - new DateTime(1970, 1, 1)).TotalSeconds;
            snap["mod_version"] = SR2BridgeMod.ModVersion;
            snap["game_dir"] = GameDirectory;
            snap["melonloader_version"] = MelonLoaderVersion();
            snap["started_at"] = server.StartedAt.ToString("yyyy-MM-dd HH:mm:ss");
            snap["uptime_s"] = (int)(DateTime.Now - server.StartedAt).TotalSeconds;
            snap["queue_depth"] = server.PendingJobs;
            snap["requests_total"] = server.TotalRequests;
            snap["requests_failed"] = server.FailedRequests;
            snap["last_error"] = server.LastError;
            snap["save_dir"] = SaveDirectory;
            snap["save_dir_exists"] = Directory.Exists(SaveDirectory);
            snap["saves_count"] = CountSaves();
            snap["game_state_available"] = false;
            snap["note"] = "静态健康快照仅含加载器与文件信息。用 sr2_player_position 在主线程查询当前玩家位置。";
            server.Snapshot = snap;
        }

        static string MelonLoaderVersion()
        {
            try
            {
                return typeof(MelonMod).Assembly.GetName().Version.ToString();
            }
            catch
            {
                return "?";
            }
        }

        static int CountSaves()
        {
            try
            {
                if (!Directory.Exists(SaveDirectory)) return 0;
                return Directory.GetFiles(SaveDirectory, "*.sav", SearchOption.AllDirectories).Length;
            }
            catch
            {
                return 0;
            }
        }

        // ---------------- handlers ----------------

        static Dictionary<string, object> Health(Dictionary<string, object> args)
        {
            var data = new Dictionary<string, object>(StringComparer.Ordinal);
            var server = SR2BridgeMod.Server;
            if (server != null)
            {
                foreach (var kv in server.Snapshot) data[kv.Key] = kv.Value;
                data["bridge_started_at"] = server.StartedAt.ToString("yyyy-MM-dd HH:mm:ss");
                data["queue_depth"] = server.PendingJobs;
                data["requests_total"] = server.TotalRequests;
                data["requests_failed"] = server.FailedRequests;
                data["last_error"] = server.LastError;
            }
            data["bridge_version"] = BridgeServer.Version;
            data["mod_version"] = SR2BridgeMod.ModVersion;
            data["process_id"] = Process.GetCurrentProcess().Id;
            data["process_uptime_s"] = (int)(DateTime.Now - Process.GetCurrentProcess().StartTime).TotalSeconds;
            return data;
        }

        static Dictionary<string, object> GameInfo(Dictionary<string, object> args)
        {
            var data = new Dictionary<string, object>(StringComparer.Ordinal);
            string game = GameDirectory;
            data["game_dir"] = game;
            data["game_dir_exists"] = Directory.Exists(game);

            // 游戏本体标识（Unity 的 app.info：公司 / 产品名）
            try
            {
                string info = Path.Combine(game, "SlimeRancher2_Data", "app.info");
                if (File.Exists(info))
                {
                    string[] lines = File.ReadAllLines(info, Encoding.UTF8);
                    data["app_info"] = lines;
                    if (lines.Length > 0) data["company"] = lines[0];
                    if (lines.Length > 1) data["product"] = lines[1];
                }
            }
            catch (Exception e)
            {
                data["app_info_error"] = e.GetType().Name + ": " + e.Message;
            }

            // 引擎 / 脚本后端
            try
            {
                string up = Path.Combine(game, "UnityPlayer.dll");
                if (File.Exists(up))
                {
                    FileVersionInfo vi = FileVersionInfo.GetVersionInfo(up);
                    data["unity_version"] = vi.ProductVersion;
                    data["unity_file_version"] = vi.FileVersion;
                }
                string meta = Path.Combine(game, "SlimeRancher2_Data", "il2cpp_data", "Metadata", "global-metadata.dat");
                data["il2cpp_metadata_exists"] = File.Exists(meta);
                if (File.Exists(meta)) data["il2cpp_metadata_bytes"] = new FileInfo(meta).Length;
                data["game_assembly_exists"] = File.Exists(Path.Combine(game, "GameAssembly.dll"));
                data["managed_assembly_csharp_exists"] = File.Exists(Path.Combine(game, "SlimeRancher2_Data", "Managed", "Assembly-CSharp.dll"));
                data["scripting_backend"] = File.Exists(Path.Combine(game, "GameAssembly.dll")) ? "IL2CPP" : "Mono";
            }
            catch (Exception e)
            {
                data["engine_error"] = e.GetType().Name + ": " + e.Message;
            }

            // Steam 清单（buildid / 更新时间），从 <library>\steamapps\appmanifest_1657630.acf 找
            try
            {
                string manifest = FindAppManifest(game, "1657630");
                data["steam_manifest"] = manifest;
                if (manifest != null && File.Exists(manifest))
                {
                    string acf = File.ReadAllText(manifest, Encoding.UTF8);
                    data["buildid"] = MatchVdf(acf, "buildid");
                    data["steam_installdir"] = MatchVdf(acf, "installdir");
                    data["size_on_disk"] = MatchVdf(acf, "SizeOnDisk");
                    data["steam_last_updated_unix"] = MatchVdf(acf, "LastUpdated");
                    long updated;
                    if (long.TryParse(MatchVdf(acf, "LastUpdated"), out updated) && updated > 0)
                    {
                        data["steam_last_updated"] = DateTimeOffset.FromUnixTimeSeconds(updated).ToLocalTime()
                            .ToString("yyyy-MM-dd HH:mm:ss");
                    }
                }
            }
            catch (Exception e)
            {
                data["steam_error"] = e.GetType().Name + ": " + e.Message;
            }

            // MelonLoader / 互操作程序集
            try
            {
                string ml = Path.Combine(game, "MelonLoader");
                data["melonloader_installed"] = Directory.Exists(ml);
                data["melonloader_version"] = MelonLoaderVersion();
                string asm = Path.Combine(ml, "Il2CppAssemblies");
                int count = 0;
                if (Directory.Exists(asm)) count = Directory.GetFiles(asm, "Il2Cpp*.dll", SearchOption.TopDirectoryOnly).Length;
                data["il2cpp_assemblies_dir"] = asm;
                data["il2cpp_assemblies_count"] = count;
                data["il2cpp_assemblies_ready"] = count > 0;
                string mods = Path.Combine(game, "Mods");
                data["mods_dir"] = mods;
                if (Directory.Exists(mods))
                {
                    var list = new List<object>();
                    foreach (string d in Directory.GetDirectories(mods)) list.Add(Path.GetFileName(d));
                    foreach (string f in Directory.GetFiles(mods, "*.dll")) list.Add(Path.GetFileName(f));
                    data["mods"] = list;
                }
                data["dotnet_dir_exists"] = Directory.Exists(Path.Combine(game, "dotnet"));
            }
            catch (Exception e)
            {
                data["melon_error"] = e.GetType().Name + ": " + e.Message;
            }
            return data;
        }

        static Dictionary<string, object> Saves(Dictionary<string, object> args)
        {
            int limit = BridgeServer.AsInt(args.ContainsKey("limit") ? args["limit"] : null, 20);
            if (limit < 1) limit = 1;
            if (limit > 200) limit = 200;

            var data = new Dictionary<string, object>(StringComparer.Ordinal);
            string dir = SaveDirectory;
            data["save_dir"] = dir;
            data["save_dir_exists"] = Directory.Exists(dir);
            if (!Directory.Exists(dir))
            {
                data["count"] = 0;
                data["note"] = "存档目录不存在：本机还没有启动过 SR2（或存档从未落盘）。";
                return data;
            }
            var files = new List<FileInfo>();
            try
            {
                foreach (string f in Directory.GetFiles(dir, "*.sav", SearchOption.AllDirectories))
                {
                    files.Add(new FileInfo(f));
                }
            }
            catch (Exception e)
            {
                data["error"] = e.GetType().Name + ": " + e.Message;
                return data;
            }
            files.Sort(delegate (FileInfo a, FileInfo b) { return b.LastWriteTimeUtc.CompareTo(a.LastWriteTimeUtc); });
            var items = new List<object>();
            for (int i = 0; i < files.Count && i < limit; i++)
            {
                FileInfo f = files[i];
                var row = new Dictionary<string, object>(StringComparer.Ordinal);
                row["name"] = f.Name;
                row["bytes"] = f.Length;
                row["modified"] = f.LastWriteTime.ToString("yyyy-MM-dd HH:mm:ss");
                row["modified_unix"] = (long)(f.LastWriteTimeUtc - new DateTime(1970, 1, 1)).TotalSeconds;
                row["slot_hint"] = SlotHint(f.Name);
                items.Add(row);
            }
            data["count"] = files.Count;
            data["items"] = items;
            return data;
        }

        static string SlotHint(string name)
        {
            // SR2 存档命名形如 <时间戳>_<槽>_<序号>.sav；这里只做展示用提示
            Match m = Regex.Match(name, @"^(\d{14})_(\d+)(?:_(\d+))?\.sav$");
            if (!m.Success) return null;
            return "slot=" + m.Groups[2].Value + (m.Groups[3].Success ? " index=" + m.Groups[3].Value : "");
        }

        static Dictionary<string, object> Echo(Dictionary<string, object> args)
        {
            var data = new Dictionary<string, object>(StringComparer.Ordinal);
            data["args"] = args;
            data["now"] = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss");
            data["mod_version"] = SR2BridgeMod.ModVersion;
            return data;
        }

        // ---------------- 辅助 ----------------

        static string FindAppManifest(string gameDir, string appid)
        {
            string cur = gameDir;
            for (int i = 0; i < 5 && !string.IsNullOrEmpty(cur); i++)
            {
                string candidate = Path.Combine(cur, "steamapps", "appmanifest_" + appid + ".acf");
                if (File.Exists(candidate)) return candidate;
                cur = Path.GetDirectoryName(cur);
            }
            return null;
        }

        static string MatchVdf(string text, string key)
        {
            Match m = Regex.Match(text, "\"" + Regex.Escape(key) + "\"\\s+\"([^\"]*)\"");
            return m.Success ? m.Groups[1].Value : null;
        }
    }
}
