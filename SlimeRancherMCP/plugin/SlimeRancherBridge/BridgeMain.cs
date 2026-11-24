// BridgeMain —— SRML mod 入口
// 职责：启动本地 HTTP 通道、在主线程泵任务、提供最小工具集（sr_ping / sr_health / sr_state / sr_echo）
// 后续加工具只需两步：① 这里 Register("sr_xxx", Handler)；② mcp\tools.json 里加一条定义
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using UnityEngine;
using SRML;
using MonomiPark.SlimeRancher.DataModel;

namespace SlimeRancherBridge
{
    public class BridgeMain : ModEntryPoint
    {
        public const string OutDir = @"E:\SlimeRancherMCP\out\bridge";

        static BridgeServer server;
        static BridgeConfig config;
        static float nextSnapshot;
        static float nextServerRetry;
        static string bootInfo = "";

        public override void PreLoad()
        {
            LogLine("PreLoad");
        }

        public override void Load()
        {
            LogLine("Load: GameContext=" + (GameContext.Instance != null));
            // 记录"当前正在玩的存档文件名"，供 sr_saves 精确标记（失败不影响其它工具）。
            SaveSlotTracker.TryInstall(LogLine);
            DronePlacement.TryInstall(LogLine);
            DroneBatteryDuration.TryInstall(LogLine);
        }

        public override void PostLoad()
        {
            LogLine("PostLoad");
            EnsureServer();
            // 游戏内隐藏聊天框（回车呼出）；失败不影响其它功能。
            try
            {
                ChatOverlay.Ensure();
                LogLine("ChatOverlay ready: " + (ChatOverlay.Instance != null));
            }
            catch (Exception e)
            {
                LogLine("ChatOverlay EX: " + e);
            }
            try
            {
                var clock = new GameObject("SlimeRancherSystemClock");
                UnityEngine.Object.DontDestroyOnLoad(clock);
                clock.AddComponent<SystemClockOverlay>();
                LogLine("SystemClockOverlay ready");
            }
            catch (Exception e)
            {
                LogLine("SystemClockOverlay EX: " + e);
            }
            try
            {
                var miniMap = new GameObject("SlimeRancherMiniMap", typeof(RectTransform), typeof(Canvas),
                    typeof(UnityEngine.UI.CanvasScaler), typeof(MiniMapOverlay));
                UnityEngine.Object.DontDestroyOnLoad(miniMap);
            }
            catch (Exception e)
            {
                LogLine("MiniMapOverlay EX: " + e);
            }
        }

        public override void Update()
        {
            try
            {
                if (server == null || !server.IsRunning)
                {
                    if (Time.time >= nextServerRetry)
                    {
                        nextServerRetry = Time.time + 5f;
                        EnsureServer();
                    }
                    return;
                }
                server.Pump(8);
                if (Time.time >= nextSnapshot)
                {
                    nextSnapshot = Time.time + 0.5f;
                    RefreshSnapshot();
                }
            }
            catch (Exception e)
            {
                LogLine("Update EX: " + e);
                if (server != null)
                {
                    server.SetLastError(e.GetType().Name + ": " + e.Message);
                }
            }
        }

        static void EnsureServer()
        {
            if (server != null && server.IsRunning)
            {
                return;
            }
            try
            {
                if (config == null)
                {
                    config = BridgeConfig.LoadOrCreate();
                    LogLine("config loaded: " + BridgeConfig.ConfigPath + " port=" + config.port
                        + " token=" + ShortToken(config.token));
                }
                if (server == null)
                {
                    server = new BridgeServer(config);
                    RegisterTools(server);
                }
                server.Start();
                if (server.IsRunning)
                {
                    RefreshSnapshot();
                    try
                    {
                        Directory.CreateDirectory(OutDir);
                        bootInfo = "bridge " + BridgeServer.Version
                            + "\nstarted at " + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss")
                            + "\nendpoint http://" + config.host + ":" + config.port
                            + "\nconfig " + BridgeConfig.ConfigPath
                            + "\ngame " + Application.unityVersion
                            + "\n";
                        File.WriteAllText(Path.Combine(OutDir, "server.txt"), bootInfo, new UTF8Encoding(false));
                    }
                    catch
                    {
                    }
                }
            }
            catch (Exception e)
            {
                LogLine("EnsureServer EX: " + e);
            }
        }

        static string ShortToken(string token)
        {
            if (string.IsNullOrEmpty(token) || token.Length < 8)
            {
                return "(unset)";
            }
            return token.Substring(0, 4) + "..." + token.Substring(token.Length - 4);
        }

        static void LogLine(string message)
        {
            try
            {
                Directory.CreateDirectory(OutDir);
                File.AppendAllText(Path.Combine(OutDir, "mod.log"),
                    DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss.fff") + "  " + message + "\r\n",
                    new UTF8Encoding(false));
            }
            catch
            {
            }
        }

        // ---------------- 工具注册（后续加工具就在这里加一行） ----------------

        static void RegisterTools(BridgeServer srv)
        {
            srv.Register("sr_ping", Ping);
            srv.Register("sr_health", Health);
            srv.Register("sr_state", State);
            srv.Register("sr_echo", Echo);
            RanchTools.Register(srv);
            ChatTools.Register(srv);
            MapDataTools.Register(srv);
        }

        static Dictionary<string, object> Ping(Dictionary<string, object> args)
        {
            var data = new Dictionary<string, object>(StringComparer.Ordinal);
            data["pong"] = true;
            data["bridge"] = BridgeServer.Version;
            data["now"] = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss");
            return data;
        }

        static Dictionary<string, object> Health(Dictionary<string, object> args)
        {
            var data = new Dictionary<string, object>(StringComparer.Ordinal);
            if (server != null)
            {
                foreach (var kv in server.Snapshot)
                {
                    data[kv.Key] = kv.Value;
                }
            }
            data["bridge_version"] = BridgeServer.Version;
            return data;
        }

        static Dictionary<string, object> Echo(Dictionary<string, object> args)
        {
            var data = new Dictionary<string, object>(StringComparer.Ordinal);
            data["args"] = args;
            return data;
        }

        static Dictionary<string, object> State(Dictionary<string, object> args)
        {
            SceneContext sc = SceneContext.Instance;
            var data = new Dictionary<string, object>(StringComparer.Ordinal);
            data["scene"] = UnityEngine.SceneManagement.SceneManager.GetActiveScene().name;
            data["main_menu"] = Levels.isMainMenu();
            data["game_loaded"] = sc != null;
            if (sc == null)
            {
                if (args.ContainsKey("required") && Json.AsInt(args["required"], 0) != 0)
                {
                    var err = new Dictionary<string, object>();
                    err["__error"] = BridgeServer.Error("NOT_READY", "SceneContext not available (loading/main menu?)");
                    return err;
                }
                data["note"] = "SceneContext is null: no save loaded";
                return data;
            }
            data["text"] = DumpText(sc);
            try
            {
                PlayerState ps = sc.PlayerState;
                if (ps != null)
                {
                    data["currency"] = ps.GetCurrency();
                    data["keys"] = ps.GetKeys();
                }
                TimeDirector td = sc.TimeDirector;
                if (td != null)
                {
                    data["day"] = td.CurrDay();
                    data["hour"] = Math.Round(td.CurrHour(), 2);
                }
                if (sc.Player != null)
                {
                    Vector3 p = sc.Player.transform.position;
                    data["player_pos"] = new List<object> { Math.Round(p.x, 1), Math.Round(p.y, 1), Math.Round(p.z, 1) };
                }
            }
            catch (Exception e)
            {
                data["read_error"] = e.GetType().Name + ": " + e.Message;
            }
            return data;
        }

        // ---------------- 快照（供 /health 用，不依赖主线程应答） ----------------

        static void RefreshSnapshot()
        {
            var snap = new Dictionary<string, object>(StringComparer.Ordinal);
            snap["snapshot_at"] = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss.fff");
            snap["snapshot_unix"] = (long)(DateTime.UtcNow - new DateTime(1970, 1, 1)).TotalSeconds;
            bool mainMenu = true;
            string scene = "?";
            try
            {
                scene = UnityEngine.SceneManagement.SceneManager.GetActiveScene().name;
                mainMenu = Levels.isMainMenu();
            }
            catch (Exception e)
            {
                snap["scene_error"] = e.GetType().Name;
            }
            snap["scene"] = scene;
            snap["main_menu"] = mainMenu;
            SceneContext sc = null;
            try
            {
                sc = SceneContext.Instance;
            }
            catch (Exception e)
            {
                snap["sc_error"] = e.GetType().Name;
            }
            snap["game_loaded"] = sc != null && !mainMenu;
            if (sc != null)
            {
                try
                {
                    PlayerState ps = sc.PlayerState;
                    if (ps != null)
                    {
                        snap["currency"] = ps.GetCurrency();
                        snap["keys"] = ps.GetKeys();
                    }
                }
                catch (Exception e)
                {
                    snap["player_error"] = e.GetType().Name;
                }
                try
                {
                    TimeDirector td = sc.TimeDirector;
                    if (td != null)
                    {
                        snap["day"] = td.CurrDay();
                        snap["hour"] = Math.Round(td.CurrHour(), 2);
                        snap["paused"] = td.HasPauser();
                    }
                }
                catch (Exception e)
                {
                    snap["time_error"] = e.GetType().Name;
                }
                try
                {
                    if (sc.Player != null)
                    {
                        Vector3 p = sc.Player.transform.position;
                        snap["player_pos"] = new List<object> { Math.Round(p.x, 1), Math.Round(p.y, 1), Math.Round(p.z, 1) };
                    }
                }
                catch (Exception e)
                {
                    snap["pos_error"] = e.GetType().Name;
                }
            }
            if (server != null)
            {
                snap["queue_depth"] = server.PendingJobs;
            }
            server.Snapshot = snap;
        }

        // ---------------- 状态文本（沿用阶段 1 的探针输出） ----------------

        static string DumpText(SceneContext sc)
        {
            var sb = new StringBuilder();
            sb.AppendLine("state at " + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss"));
            sb.AppendLine("scene=" + UnityEngine.SceneManagement.SceneManager.GetActiveScene().name
                + "  mainMenu=" + Levels.isMainMenu()
                + "  gameMode=" + sc.GameModel.currGameMode);

            PlayerState ps = sc.PlayerState;
            if (ps == null)
            {
                sb.AppendLine("PlayerState=null");
            }
            else
            {
                sb.AppendLine("-- player --");
                sb.AppendLine("currency=" + ps.GetCurrency() + "  keys=" + ps.GetKeys());
                sb.AppendLine("health=" + ps.GetCurrHealth() + "/" + ps.GetMaxHealth()
                    + "  energy=" + ps.GetCurrEnergy() + "/" + ps.GetMaxEnergy()
                    + "  ammoMode=" + ps.GetAmmoMode());
                TimeDirector td = sc.TimeDirector;
                if (td != null)
                {
                    sb.AppendLine("worldTime=" + td.WorldTime().ToString("F3")
                        + "  day=" + td.CurrDay()
                        + "  hour=" + td.CurrHour().ToString("F2"));
                }
                Ammo ammo = ps.Ammo;
                if (ammo != null)
                {
                    int usable = ammo.GetUsableSlotCount();
                    sb.AppendLine("vacpack usableSlots=" + usable + " selectedIdx=" + ammo.GetSelectedAmmoIdx());
                    for (int i = 0; i < usable; i++)
                    {
                        try
                        {
                            Identifiable.Id id = ammo.GetSlotName(i);
                            int cnt = 0;
                            try { cnt = ammo.GetSlotCount(i); } catch { }
                            sb.AppendLine("  slot" + i + ": " + id + " x" + cnt);
                        }
                        catch (Exception e)
                        {
                            sb.AppendLine("  slot" + i + ": EX " + e.GetType().Name);
                        }
                    }
                }
            }

            EconomyDirector econ = sc.EconomyDirector;
            if (econ != null)
            {
                sb.AppendLine("-- market --");
                sb.AppendLine("marketShutdown=" + econ.IsMarketShutdown());
                foreach (Identifiable.Id id in Enum.GetValues(typeof(Identifiable.Id)))
                {
                    if (!Identifiable.IsPlort(id))
                    {
                        continue;
                    }
                    int? v = econ.GetCurrValue(id);
                    if (v.HasValue)
                    {
                        sb.AppendLine("  price " + id + " = " + v.Value);
                    }
                }
            }

            ExchangeDirector ex = sc.ExchangeDirector;
            if (ex != null)
            {
                sb.AppendLine("-- exchange --");
                foreach (ExchangeDirector.OfferType ot in Enum.GetValues(typeof(ExchangeDirector.OfferType)))
                {
                    string oid = null;
                    try { oid = ex.GetOfferId(ot); } catch { }
                    if (oid == null)
                    {
                        continue;
                    }
                    string rancher = null;
                    try { rancher = ex.GetOfferRancherId(ot); } catch { }
                    sb.AppendLine("  offer " + ot + " id=" + oid + " rancher=" + rancher);
                    try
                    {
                        foreach (ExchangeDirector.RequestedItemEntry r in ex.GetOfferRequests(ot))
                        {
                            sb.AppendLine("    req " + r.id + " " + r.progress + "/" + r.count + " complete=" + r.IsComplete());
                        }
                    }
                    catch (Exception e) { sb.AppendLine("    req EX " + e.GetType().Name); }
                    try
                    {
                        foreach (ExchangeDirector.ItemEntry r in ex.GetOfferRewards(ot))
                        {
                            sb.AppendLine("    reward " + (r.id != Identifiable.Id.NONE ? r.id.ToString() : r.specReward.ToString()) + " x" + r.count);
                        }
                    }
                    catch (Exception e) { sb.AppendLine("    reward EX " + e.GetType().Name); }
                }
            }

            GameModel gm = sc.GameModel;
            if (gm != null)
            {
                sb.AppendLine("-- land plots --");
                Dictionary<string, LandPlotModel> plots = gm.AllLandPlots();
                sb.AppendLine("count=" + plots.Count);
                foreach (KeyValuePair<string, LandPlotModel> kv in plots)
                {
                    LandPlotModel m = kv.Value;
                    var ups = new List<string>();
                    foreach (LandPlot.Upgrade u in m.upgrades)
                    {
                        ups.Add(u.ToString());
                    }
                    sb.AppendLine("  " + kv.Key
                        + " type=" + m.typeId
                        + " upgrades=[" + string.Join(",", ups.ToArray()) + "]"
                        + " feedOps=" + m.remainingFeedOperations
                        + " attached=" + m.attachedResourceId);
                }
            }

            sb.AppendLine("-- scene components --");
            Count<ScorePlort>(sb, "ScorePlort");
            Count<SiloStorage>(sb, "SiloStorage");
            Count<SlimeFeeder>(sb, "SlimeFeeder");
            Count<PlortCollector>(sb, "PlortCollector");
            Count<LandPlot>(sb, "LandPlot");
            Count<ExchangeEjector>(sb, "ExchangeEjector");
            Count<GoldSlimeProducePlorts>(sb, "GoldSlimeProducePlorts");
            Count<Extractor>(sb, "Extractor");

            SiloStorage[] silos = UnityEngine.Object.FindObjectsOfType<SiloStorage>();
            for (int s = 0; s < silos.Length; s++)
            {
                SiloStorage st = silos[s];
                sb.AppendLine("  silo[" + s + "] " + st.name + " type=" + st.type);
                try
                {
                    Ammo a = st.GetRelevantAmmo();
                    int n = a.GetUsableSlotCount();
                    for (int i = 0; i < n; i++)
                    {
                        string id;
                        try { id = a.GetSlotName(i).ToString(); } catch { id = "?"; }
                        int cnt = 0;
                        try { cnt = a.GetSlotCount(i); } catch { }
                        if (cnt > 0 || id != "NONE")
                        {
                            sb.AppendLine("      slot" + i + ": " + id + " x" + cnt);
                        }
                    }
                }
                catch (Exception e) { sb.AppendLine("      silo EX " + e.GetType().Name); }
            }

            return sb.ToString();
        }

        static void Count<T>(StringBuilder sb, string label) where T : Component
        {
            int n = 0;
            try
            {
                n = UnityEngine.Object.FindObjectsOfType<T>().Length;
            }
            catch (Exception e)
            {
                sb.AppendLine("  " + label + " EX " + e.GetType().Name);
                return;
            }
            sb.AppendLine("  " + label + "=" + n);
        }
    }
}
