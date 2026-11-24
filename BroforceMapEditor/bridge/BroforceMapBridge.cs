using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

[assembly: AssemblyVersion("1.0.0.0")]

namespace BroforceLifeTrainer
{
    /// <summary>
    /// Broforce 地图桥接（V0.1 / M0）。
    /// 由 mono_injector 注入后调用 Bootstrap.Start()，通过一次性窗口钩子把常驻
    /// MonoBehaviour 建到 Unity 主线程，然后从文件队列读命令，用游戏自身的
    /// MapData / Campaign / FileIO 生成、保存并加载自定义关卡。
    /// </summary>
    public static class Bootstrap
    {
        public const uint InstallMessage = 0x8453; // 与修改器 (0x8452) 区分
        private const int GwlWndProc = -4;
        private const uint GwOwner = 4;

        private static readonly UTF8Encoding Utf8NoBom = new UTF8Encoding(false);
        private static readonly int CurrentPid = System.Diagnostics.Process.GetCurrentProcess().Id;
        private static readonly string AppDataDirectory = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "BroforceMapEditor");
        private static readonly string IpcDirectory = Path.Combine(AppDataDirectory, "ipc");
        private static readonly string StatusPath = Path.Combine(AppDataDirectory, "bridge_status.txt");

        private static IntPtr gameWindow = IntPtr.Zero;
        private static IntPtr originalWindowProcedure = IntPtr.Zero;
        private static WndProcDelegate windowProcedure;
        private static bool windowHookInstalled;
        private static bool monitorInstalled;

        private delegate IntPtr WndProcDelegate(IntPtr hWnd, uint message, IntPtr wParam, IntPtr lParam);
        private delegate bool EnumWindowsDelegate(IntPtr hWnd, IntPtr lParam);

        [DllImport("user32.dll")] private static extern bool EnumWindows(EnumWindowsDelegate callback, IntPtr lParam);
        [DllImport("user32.dll")] private static extern bool IsWindowVisible(IntPtr hWnd);
        [DllImport("user32.dll")] private static extern IntPtr GetWindow(IntPtr hWnd, uint command);
        [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);
        [DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW", SetLastError = true)] private static extern IntPtr SetWindowLongPtr64(IntPtr hWnd, int index, IntPtr newValue);
        [DllImport("user32.dll", EntryPoint = "SetWindowLongW", SetLastError = true)] private static extern IntPtr SetWindowLong32(IntPtr hWnd, int index, IntPtr newValue);
        [DllImport("user32.dll")] private static extern IntPtr CallWindowProc(IntPtr previousWindowProcedure, IntPtr hWnd, uint message, IntPtr wParam, IntPtr lParam);
        [DllImport("kernel32.dll")] private static extern void SetLastError(uint errorCode);

        // 内存中的当前工程；load 时交给游戏。
        private static MapData currentMap;
        private static Campaign currentCampaign;
        private static float pendingPlayerAt = -1f;
        private static bool skipCredits;
        private static string lastCompletedMap = "";

        public static void Start()
        {
            try
            {
                Directory.CreateDirectory(IpcDirectory);
                WriteStatus("STARTING");
                gameWindow = WaitForGameWindow();
                if (gameWindow == IntPtr.Zero)
                {
                    WriteStatus("ERROR|no game window");
                    return;
                }

                windowProcedure = new WndProcDelegate(WindowProcedure);
                IntPtr functionPointer = Marshal.GetFunctionPointerForDelegate(windowProcedure);
                SetLastError(0);
                originalWindowProcedure = SetWindowLongPtr(gameWindow, GwlWndProc, functionPointer);
                int error = Marshal.GetLastWin32Error();
                if (originalWindowProcedure == IntPtr.Zero && error != 0)
                {
                    WriteStatus("ERROR|SetWindowLongPtr " + error);
                    return;
                }
                windowHookInstalled = true;

                // 让 Unity 主动跑一次窗口过程，从而在主线程创建常驻组件。
                PostInstallMessage(gameWindow);
                WriteStatus("HOOKED");
            }
            catch (Exception exception)
            {
                WriteStatus("ERROR|" + exception.GetType().Name + ": " + exception.Message);
            }
        }

        private static void PostInstallMessage(IntPtr hWnd)
        {
            // SetWindowLongPtr 之后，用 PostMessage 触发一次；若失败则退化为直接调用。
            if (PostMessage(hWnd, InstallMessage, IntPtr.Zero, IntPtr.Zero))
            {
                return;
            }
            EnsureMonitorOnMainThread();
            RestoreOriginalWindowProcedure(hWnd);
        }

        [DllImport("user32.dll", SetLastError = true)]
        private static extern bool PostMessage(IntPtr hWnd, uint message, IntPtr wParam, IntPtr lParam);

        private static IntPtr SetWindowLongPtr(IntPtr hWnd, int index, IntPtr newValue)
        {
            return IntPtr.Size == 8
                ? SetWindowLongPtr64(hWnd, index, newValue)
                : SetWindowLong32(hWnd, index, newValue);
        }

        private static IntPtr WaitForGameWindow()
        {
            int attempts;
            for (attempts = 0; attempts < 150; attempts++)
            {
                IntPtr found = FindGameWindow();
                if (found != IntPtr.Zero) return found;
                Thread.Sleep(100);
            }
            return IntPtr.Zero;
        }

        private static IntPtr FindGameWindow()
        {
            IntPtr result = IntPtr.Zero;
            EnumWindows(
                delegate(IntPtr hWnd, IntPtr unused)
                {
                    uint processId;
                    GetWindowThreadProcessId(hWnd, out processId);
                    if (processId == (uint)CurrentPid && IsWindowVisible(hWnd) && GetWindow(hWnd, GwOwner) == IntPtr.Zero)
                    {
                        result = hWnd;
                        return false;
                    }
                    return true;
                },
                IntPtr.Zero);
            return result;
        }

        private static IntPtr WindowProcedure(IntPtr hWnd, uint message, IntPtr wParam, IntPtr lParam)
        {
            if (message == InstallMessage)
            {
                try
                {
                    EnsureMonitorOnMainThread();
                    RestoreOriginalWindowProcedure(hWnd);
                    return new IntPtr(1);
                }
                catch (Exception exception)
                {
                    WriteStatus("ERROR|" + exception.GetType().Name + ": " + exception.Message);
                    return IntPtr.Zero;
                }
            }
            return CallWindowProc(originalWindowProcedure, hWnd, message, wParam, lParam);
        }

        private static void EnsureMonitorOnMainThread()
        {
            if (monitorInstalled) return;
            // Retire earlier injected versions before installing one queue consumer.
            UnityEngine.GameObject previous = UnityEngine.GameObject.Find("BroforceMapBridge");
            if (previous != null) UnityEngine.Object.DestroyImmediate(previous);
            UnityEngine.Application.runInBackground = true;
            UnityEngine.GameObject host = new UnityEngine.GameObject("BroforceMapBridge");
            UnityEngine.Object.DontDestroyOnLoad(host);
            host.AddComponent(typeof(MapBridgeBehaviour));
            monitorInstalled = true;
            WriteStatus("READY");
        }

        private static bool RestoreOriginalWindowProcedure(IntPtr hWnd)
        {
            if (!windowHookInstalled) return true;
            SetLastError(0);
            SetWindowLongPtr(hWnd, GwlWndProc, originalWindowProcedure);
            int error = Marshal.GetLastWin32Error();
            if (error != 0)
            {
                WriteStatus("ERROR|restore hook " + error);
                return false;
            }
            windowHookInstalled = false;
            return true;
        }

        internal static void OnDestroyed()
        {
            monitorInstalled = false;
            WriteStatus("DESTROYED");
        }

        // ---------------- 命令循环 ----------------

        internal static void ProcessCommands()
        {
            try
            {
                Directory.CreateDirectory(IpcDirectory);
                WriteHeartbeat();
                // Detect our offline files too; loading via the menu does not call LoadCurrentCampaign.
                if (LevelSelectionController.IsCampaignScene && Map.MapData != null &&
                    Map.MapData.levelDescription != null &&
                    (Map.MapData.levelDescription.Contains("跳过片尾") || Map.MapData.levelDescription.Contains("不播放片尾")))
                    skipCredits = true;
                if (skipCredits && LevelSelectionController.IsCampaignScene && GameModeController.LevelFinished)
                {
                    object controller = GameModeController.Instance;
                    if (controller != null)
                    {
                        object outcome = typeof(GameModeController).GetField("levelResult", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic).GetValue(controller);
                        if (Convert.ToInt32(outcome) == 1)
                        {
                            skipCredits = false;
                            lastCompletedMap = LevelSelectionController.currentCampaign == null ? "" : LevelSelectionController.currentCampaign.name;
                            UnityEngine.Time.timeScale = 1f;
                            UnityEngine.SceneManagement.SceneManager.LoadScene(LevelSelectionController.MainMenuScene);
                        }
                    }
                }
                else if (!LevelSelectionController.IsCampaignScene) skipCredits = false;
                if (pendingPlayerAt >= 0 && UnityEngine.Time.realtimeSinceStartup >= pendingPlayerAt)
                {
                    pendingPlayerAt = -1f;
                    EnsurePlayer();
                }
                string commandPath = Path.Combine(IpcDirectory, "command.txt");
                if (!File.Exists(commandPath)) return;
                string runningPath = Path.Combine(IpcDirectory, "command.running");
                try
                {
                    File.Move(commandPath, runningPath);
                }
                catch (IOException)
                {
                    return; // 客户端正在写，下一帧再取
                }

                Dictionary<string, string> command = ParseKeyValues(File.ReadAllLines(runningPath, Utf8NoBom));
                string op = Get(command, "op");
                string result;
                try
                {
                    result = Execute(op, command);
                }
                catch (Exception exception)
                {
                    Exception inner = exception;
                    while (inner is TargetInvocationException && inner.InnerException != null)
                        inner = inner.InnerException;
                    result = "ok=0\nerror=" + OneLine(inner.GetType().Name + ": " + inner.Message);
                }

                File.WriteAllText(Path.Combine(IpcDirectory, "result.tmp"), result, Utf8NoBom);
                File.Delete(Path.Combine(IpcDirectory, "result.txt"));
                File.Move(Path.Combine(IpcDirectory, "result.tmp"), Path.Combine(IpcDirectory, "result.txt"));
                File.Delete(runningPath);
            }
            catch (Exception exception)
            {
                WriteStatus("CMD_ERROR|" + exception.GetType().Name + ": " + exception.Message);
            }
        }

        private static string Execute(string op, Dictionary<string, string> command)
        {
            switch (op)
            {
                case "ping":
                    return "ok=1\npid=" + CurrentPid + "\nversion=0.1";
                case "state":
                    return DescribeState();
                case "runtime_entities":
                    return RuntimeEntities();
                case "ensure_player":
                    return EnsurePlayer();
                case "player_pos":
                    return PlayerPos();
                case "probe_solid":
                    return ProbeSolid(command);
                case "probe_blocks":
                    return ProbeBlocks(command);
                case "test_move_player":
                    Player testPlayer = HeroController.players[0];
                    if (testPlayer == null || testPlayer.character == null) return "ok=0\nerror=no hero";
                    testPlayer.character.SetPositionAndVelocity(GetInt(command, "x", 4) * 16, GetInt(command, "y", 7) * 16, 0, 0);
                    return "ok=1";
                case "make_test_map":
                    return MakeTestMap(command);
                case "save_bfg":
                    return SaveBfg(command);
                case "load":
                    return LoadCurrentCampaign();
                case "list_levels":
                    return ListLevels(command);
                case "inspect_level":
                    return InspectLevel(command);
                case "dump_map":
                    if (currentMap == null) return "ok=0\nerror=no map";
                    StringBuilder dump = new StringBuilder();
                    dump.Append(currentMap.Width).Append(',').Append(currentMap.Height).Append('\n');
                    for (int r = currentMap.Height - 1; r >= 0; r--)
                    {
                        for (int c = 0; c < currentMap.Width; c++)
                        { if (c > 0) dump.Append(','); dump.Append((int)currentMap.foregroundBlocks[c,r]); }
                        dump.Append('\n');
                    }
                    dump.Append("DOODADS\n");
                    foreach (DoodadInfo d in currentMap.DoodadList)
                        dump.Append(d.position.collumn).Append(',').Append(d.position.row).Append(',').Append(d.type).Append(',').Append(d.variation).Append('\n');
                    string dumpPath = Get(command, "output");
                    File.WriteAllText(dumpPath, dump.ToString(), Utf8NoBom);
                    return "ok=1\npath=" + dumpPath;
                case "remix_reference":
                    if (currentMap == null) return "ok=0\nerror=no reference";
                    currentCampaign.name = Get(command, "name");
                    currentCampaign.header.name = currentCampaign.name;
                    currentCampaign.header.author = "BROFORCEMAPBRIDGE";
                    currentCampaign.header.description = Get(command, "description");
                    currentMap.levelDescription = Get(command, "description");
                    int enemies = 0, supplies = 0;
                    foreach (DoodadInfo d in currentMap.DoodadList)
                    {
                        if (d.type == DoodadType.Mook)
                        {
                            // Keep trusted authored positions; progress from ordinary troops to scouts.
                            d.variation = d.position.row > 75 && enemies % 4 == 0 ? 4 : 0;
                            enemies++;
                        }
                        if (d.type == DoodadType.Crate && supplies++ % 3 == 0)
                            d.type = DoodadType.AmmoCrate;
                    }
                    currentMap.heroSpawnMode = HeroSpawnMode.SpawnPoint;
                    return "ok=1\nenemies=" + enemies + "\n" + DescribeMap(currentMap);
                case "load_offline_test":
                    // Exercise FileIO and LoadFromFile, without arming the MCP-only skip flag.
                    skipCredits = false;
                    LevelSelectionController.levelFileNameToLoad = Get(command, "file");
                    LevelSelectionController.campaignToLoad = Get(command, "file");
                    GameState.Instance.loadCustomCampaign = true;
                    GameState.Instance.loadMode = MapLoadMode.LoadFromFile;
                    GameState.Instance.gameMode = GameMode.Campaign;
                    GameState.Instance.levelNumber = 0;
                    UnityEngine.SceneManagement.SceneManager.LoadScene(LevelSelectionController.CampaignScene);
                    pendingPlayerAt = UnityEngine.Time.realtimeSinceStartup + 2f;
                    return "ok=1";
                case "clone_level":
                    return CloneLevel(command);
                case "new_map":
                    return NewMap(command);
                case "set_tiles":
                    return SetTiles(command);
                case "add_doodads":
                    return AddDoodads(command);
                case "dump_triggers":
                    return DumpTriggers(command);
                case "runtime_triggers":
                    return RuntimeTriggers();
                case "add_trigger":
                    return AddTrigger(command);
                case "probe_doodads":
                    return ProbeDoodads(command);
                default:
                    return "ok=0\nerror=unknown op: " + op;
            }
        }

        private static string DescribeState()
        {
            Type lsc = GameType("LevelSelectionController");
            Type gs = GameType("GameState");
            string scene = lsc != null ? Convert.ToString(lsc.GetProperty("levelFileNameToLoad", Static).GetValue(null, null)) : "?";
            object instance = gs != null ? gs.GetProperty("Instance", Static).GetValue(null, null) : null;
            string gameMode = instance != null ? Convert.ToString(gs.GetField("gameMode").GetValue(instance)) : "?";
            string builder = "ok=1\npid=" + CurrentPid;
            builder += "\nlevelFileName=" + OneLine(scene);
            builder += "\ngameMode=" + OneLine(gameMode);
            if (lsc != null)
            {
                builder += "\nisCampaignScene=" + Convert.ToString(lsc.GetProperty("IsCampaignScene", Static).GetValue(null, null));
                builder += "\nisWorldMap=" + Convert.ToString(lsc.GetProperty("IsWorldMapScene", Static).GetValue(null, null));
                builder += "\ncurrentLevel=" + Convert.ToString(lsc.GetProperty("CurrentLevelNum", Static).GetValue(null, null));
            }
            builder += "\ninMemory=" + (currentMap == null ? "none" : currentMap.Width + "x" + currentMap.Height);
            builder += "\nactiveScene=" + UnityEngine.SceneManagement.SceneManager.GetActiveScene().name;
            builder += "\nlastCompletedMap=" + OneLine(lastCompletedMap);
            return builder;
        }

        private static string MakeTestMap(Dictionary<string, string> command)
        {
            int width = GetInt(command, "width", 40);
            int height = GetInt(command, "height", 20);
            int theme = GetInt(command, "theme", 0);
            string name = Get(command, "name");
            if (string.IsNullOrEmpty(name)) name = "AI Test Map";
            string author = Get(command, "author");
            if (string.IsNullOrEmpty(author)) author = "BroforceMapBridge";
            string note = Get(command, "description");
            if (note == null) note = "";
            if (width < 8 || height < 8 || width > 512 || height > 256)
                return "ok=0\nerror=size out of range";

            MapData map = new MapData(width, height);
            map.ResetMap(width, height);
            TerrainType[,] fg = map.foregroundBlocks;
            int first = fg.GetLength(0);
            int second = fg.GetLength(1);
            // 行号 0 = 底部（Map.GetBlockCenterY 用 row 向上），所以地板放在 0..1 行。
            bool columnFirst = first == width;
            int columns = columnFirst ? first : second;
            int rows = columnFirst ? second : first;
            for (int c = 0; c < columns; c++)
            {
                for (int r = 0; r < rows; r++)
                {
                    bool floor = r <= 1;
                    bool wall = c == 0 || c == columns - 1;
                    TerrainType value = (floor || wall) ? TerrainType.Earth : TerrainType.Empty;
                    if (columnFirst) fg[c, r] = value;
                    else fg[r, c] = value;
                }
            }
            // 中间加一段平台，方便肉眼确认方向与比例。
            int midColumn = columns / 2;
            int platformRow = rows / 2;
            for (int c = midColumn - 3; c <= midColumn + 3; c++)
            {
                if (c <= 0 || c >= columns - 1) continue;
                if (columnFirst) fg[c, platformRow] = TerrainType.Earth;
                else fg[platformRow, c] = TerrainType.Earth;
            }

            map.theme = (LevelTheme)theme;
            map.musicType = MusicType.JungleBlueSky;
            map.weatherType = WeatherType.Day;
            map.cameraFollowMode = CameraFollowMode.Normal;
            map.heroSpawnMode = HeroSpawnMode.Auto;
            map.levelDescription = note;

            CampaignHeader header = new CampaignHeader();
            header.name = name;
            header.author = author;
            header.description = note;
            header.length = 1;
            header.gameMode = GameMode.Campaign;

            Campaign campaign = new Campaign();
            campaign.name = name;
            campaign.header = header;
            campaign.levels = new MapData[] { map };
            campaign.brodownLevel = 0;

            currentMap = map;
            currentCampaign = campaign;
            return "ok=1\nwidth=" + columns + "\nheight=" + rows
                + "\narray=" + first + "x" + second + "\ncolumnFirst=" + columnFirst
                + "\nname=" + OneLine(name) + "\nauthor=" + OneLine(author)
                + "\ndescription=" + OneLine(note);
        }

        private static string SaveBfg(Dictionary<string, string> command)
        {
            if (currentCampaign == null) return "ok=0\nerror=no map in memory";
            bool workshopFormat = Get(command, "format") == "bfg";
            string path = Get(command, "path");
            if (string.IsNullOrEmpty(path))
                path = Path.Combine(Path.Combine(AppDataDirectory, "maps"),
                    workshopFormat ? "ai_test.bfg" : "ai_test.bfc");
            Directory.CreateDirectory(Path.GetDirectoryName(path));

            string expectedExtension = workshopFormat ? ".bfg" : ".bfc";
            if (!path.EndsWith(expectedExtension, StringComparison.OrdinalIgnoreCase))
                return "ok=0\nerror=extension/format mismatch: expected " + expectedExtension
                    + " for format=" + (workshopFormat ? "bfg" : "bfc") + ", got " + OneLine(path);

            MethodInfo serialize = GameType("FileIO").GetMethod("SerializeCampaign", Static | BindingFlags.NonPublic);
            if (serialize == null) return "ok=0\nerror=FileIO.SerializeCampaign not found";
            // .bfc（本地自制战役）= 不加密 + LZF；.bfg（工坊发布）= 加密 + LZF。
            byte[] bytes = (byte[])serialize.Invoke(null, new object[] { currentCampaign, workshopFormat, true });
            if (bytes == null || bytes.Length == 0) return "ok=0\nerror=empty result";
            File.WriteAllBytes(path, bytes);

            string extra;
            MethodInfo loadCampaignBytes = GameType("FileIO").GetMethod("LoadCampaignBytes", Static);
            Campaign roundTrip = (Campaign)loadCampaignBytes.Invoke(null, new object[] { bytes, workshopFormat, true });
            if (roundTrip == null || roundTrip.levels == null || roundTrip.levels.Length == 0)
                return "ok=0\nerror=roundtrip returned no levels\npath=" + path;
            extra = "\nroundtrip=" + roundTrip.levels[0].Width + "x" + roundTrip.levels[0].Height
                + "\nlevels=" + roundTrip.levels.Length;

            bool copyToGame = Get(command, "copy_to_levels") == "1";
            if (copyToGame)
            {
                string levels = Path.Combine(Path.GetDirectoryName(UnityEngine.Application.dataPath), "Levels");
                Directory.CreateDirectory(levels);
                string fileName = Path.GetFileNameWithoutExtension(path);
                string extension = workshopFormat ? ".bfg" : ".bfc";
                string target = Path.Combine(levels, fileName + extension);
                File.Copy(path, target, true);
                MethodInfo loadCampaignFromDisk = GameType("FileIO").GetMethod("LoadCampaignFromDisk", Static);
                Campaign disk = (Campaign)loadCampaignFromDisk.Invoke(null, new object[] { fileName });
                if (disk == null || disk.levels == null || disk.levels.Length == 0)
                    return "ok=0\nerror=game failed to reopen copied campaign\ncopied=" + target;
                extra += "\ncopied=" + OneLine(target)
                    + "\ndiskRoundtrip=" + disk.levels[0].Width + "x" + disk.levels[0].Height;
            }
            return "ok=1\npath=" + OneLine(path) + "\nformat=" + (workshopFormat ? "bfg" : "bfc")
                + "\nsize=" + bytes.Length + extra;
        }

        private static string LoadCurrentCampaign()
        {
            if (currentCampaign == null) return "ok=0\nerror=no map in memory";
            Type lsc = GameType("LevelSelectionController");
            Type gs = GameType("GameState");
            if (lsc == null || gs == null) return "ok=0\nerror=types missing";

            object instance = gs.GetProperty("Instance", Static).GetValue(null, null);
            if (instance == null) return "ok=0\nerror=GameState.Instance missing";

            PropertyInfo campaignProperty = lsc.GetProperty("currentCampaign", Static);
            if (campaignProperty != null && campaignProperty.CanWrite)
                campaignProperty.SetValue(null, currentCampaign, null);
            else
            {
                MethodInfo setter = lsc.GetMethod("set_currentCampaign", Static);
                if (setter == null) return "ok=0\nerror=currentCampaign setter missing";
                setter.Invoke(null, new object[] { currentCampaign });
            }

            gs.GetField("loadCustomCampaign").SetValue(instance, true);
            LevelSelectionController.MapDataToLoad = currentMap;
            gs.GetField("levelNumber").SetValue(instance, 0);
            gs.GetField("loadMode").SetValue(instance, Enum.ToObject(GameType("MapLoadMode"), 1)); // LoadFromMapdata
            gs.GetField("gameMode").SetValue(instance, Enum.ToObject(GameType("GameMode"), 0));     // Campaign
            gs.GetField("returnToWorldMap").SetValue(instance, false);
            gs.GetField("immediatelyGoToCustomCampaign").SetValue(instance, true);

            string scene = Convert.ToString(lsc.GetField("CampaignScene", Static | BindingFlags.Public).GetValue(null));
            if (string.IsNullOrEmpty(scene)) return "ok=0\nerror=CampaignScene empty";

            Type loader = GameType("Utility.SceneLoader");
            if (loader == null) return "ok=0\nerror=Utility.SceneLoader missing";
            MethodInfo loadScene = null;
            foreach (MethodInfo candidate in loader.GetMethods(Static))
            {
                ParameterInfo[] ps = candidate.GetParameters();
                if (candidate.Name == "LoadScene" && ps.Length == 2 && ps[0].ParameterType == typeof(string))
                { loadScene = candidate; break; }
            }
            if (loadScene == null) return "ok=0\nerror=SceneLoader.LoadScene missing";
            ParameterInfo[] parameters = loadScene.GetParameters();
            object mode = parameters.Length > 1
                ? Enum.Parse(parameters[1].ParameterType, "Single")
                : null;
            loadScene.Invoke(null, new object[] { scene, mode });
            skipCredits = true;
            pendingPlayerAt = UnityEngine.Time.realtimeSinceStartup + 2f;
            return "ok=1\nscene=" + OneLine(scene);
        }

        private static string NewMap(Dictionary<string, string> command)
        {
            int width = GetInt(command, "width", 100);
            int height = GetInt(command, "height", 40);
            if (width < 8 || height < 8 || width > 512 || height > 256)
                return "ok=0\nerror=size out of range";
            string name = Get(command, "name");
            if (string.IsNullOrEmpty(name)) name = "AI MAP";
            string author = Get(command, "author");
            if (string.IsNullOrEmpty(author)) author = "BROFORCEMAPBRIDGE";
            string note = Get(command, "description");
            if (note == null) note = "";

            MapData map = new MapData(width, height);
            map.ResetMap(width, height);
            map.theme = (LevelTheme)GetInt(command, "theme", 0);
            map.musicType = (MusicType)GetInt(command, "music", 3);
            map.weatherType = (WeatherType)GetInt(command, "weather", 2);
            map.cameraFollowMode = (CameraFollowMode)GetInt(command, "camera", 0);
            map.heroSpawnMode = (HeroSpawnMode)GetInt(command, "spawn", 0);
            map.levelDescription = note;

            CampaignHeader header = new CampaignHeader();
            header.name = name;
            header.author = author;
            header.description = note;
            header.length = 1;
            header.gameMode = GameMode.Campaign;
            header.hasTimeScoreBoard = true;

            Campaign campaign = new Campaign();
            campaign.name = name;
            campaign.header = header;
            campaign.levels = new MapData[] { map };
            campaign.brodownLevel = 0;

            currentMap = map;
            currentCampaign = campaign;
            return "ok=1\nwidth=" + map.Width + "\nheight=" + map.Height
                + "\nname=" + OneLine(name) + "\ndescription=" + OneLine(note);
        }

        private static string RuntimeEntities()
        {
            StringBuilder b = new StringBuilder("ok=1");
            b.Append("\nplayers=").Append(HeroController.GetPlayersPlayingCount());
            b.Append("\nplayersAlive=").Append(HeroController.GetPlayersAliveCount());
            b.Append("\nfinished=").Append(GameModeController.LevelFinished);
            if (HeroController.players != null)
                foreach (Player player in HeroController.players)
                    if (player != null) b.Append("\nplayer.").Append(player.playerNum).Append("=lives:").Append(player.Lives)
                        .Append(" hero:").Append(player.character == null ? "none" : player.character.name);
            UnityEngine.Object[] objects = UnityEngine.Object.FindObjectsOfType(typeof(Mook));
            b.Append("\ncount=").Append(objects.Length);
            int i = 0;
            foreach (UnityEngine.Object obj in objects)
            {
                UnityEngine.Component c = obj as UnityEngine.Component;
                if (c == null) continue;
                UnityEngine.Vector3 p = c.transform.position;
                FieldInfo health = obj.GetType().GetField("health", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                b.Append("\nentity.").Append(i++).Append('=').Append(obj.name)
                    .Append(" x:").Append(p.x).Append(" y:").Append(p.y)
                    .Append(" health:").Append(health == null ? "?" : Convert.ToString(health.GetValue(obj)));
            }
            return b.ToString();
        }

        private static string PlayerPos()
        {
            if (HeroController.players == null || HeroController.players.Length == 0)
                return "ok=0\nerror=no players";
            Player player = HeroController.players[0];
            if (player == null || player.character == null) return "ok=0\nerror=no hero";
            UnityEngine.Vector3 p = player.character.transform.position;
            int cx = (int)System.Math.Floor(p.x / 16.0 + 0.5);
            int cy = (int)System.Math.Floor(p.y / 16.0 + 0.5);
            bool solid = Map.IsBlockSolid(cx, cy);
            bool below = Map.IsBlockSolid(cx, cy - 1);
            return "ok=1\nx=" + p.x + "\ny=" + p.y + "\ncellX=" + cx + "\ncellY=" + cy
                + "\nunitColumn=" + player.character.collumn + "\nunitRow=" + player.character.row
                + "\nsolidHere=" + solid + "\nsolidBelow=" + below
                + "\nmapSize=" + Map.GetMaxCollumns() + "x" + Map.GetMaxRows();
        }

        private static string RuntimeTriggers()
        {
            if (Map.MapData == null || TriggerManager.Triggers == null)
                return "ok=0\nerror=no live map triggers";
            StringBuilder output = new StringBuilder("ok=1");
            output.Append("\nmapOffset=").Append(Map.lastXLoadOffset).Append(',').Append(Map.lastYLoadOffset);
            int index = 0;
            foreach (Trigger trigger in TriggerManager.Triggers.Values)
            {
                output.Append("\ntrigger.").Append(index++).Append('=').Append(trigger.info.type)
                    .Append(" name:").Append(OneLine(trigger.info.name)).Append(" enabled:").Append(trigger.enabled)
                    .Append(" fired:").Append(trigger.hasBeenTriggered).Append(" tag:").Append(OneLine(trigger.info.tag));
            }
            index = 0;
            foreach (DoodadInfo doodad in Map.MapData.DoodadList)
            {
                if (string.IsNullOrEmpty(doodad.tag)) continue;
                BroforceObject entity = doodad.entity == null ? null : doodad.entity.GetComponent<BroforceObject>();
                output.Append("\ntagged.").Append(index++).Append('=').Append(OneLine(doodad.tag))
                    .Append(" object:").Append(doodad.entity == null ? "missing" : OneLine(doodad.entity.name))
                    .Append(" alive:").Append(entity != null && entity.IsAlive());
            }
            return output.ToString();
        }

        private static string ProbeSolid(Dictionary<string, string> command)
        {
            string path = Get(command, "cells");
            if (string.IsNullOrEmpty(path) || !File.Exists(path))
                return "ok=0\nerror=cells file missing";
            StringBuilder builder = new StringBuilder("ok=1");
            string[] lines = File.ReadAllLines(path, Utf8NoBom);
            for (int index = 0; index < lines.Length; index++)
            {
                string line = lines[index].Trim();
                if (line.Length == 0 || line.StartsWith("#")) continue;
                string[] parts = line.Split(',');
                if (parts.Length < 2) continue;
                int x, y;
                if (!Int32.TryParse(parts[0].Trim(), out x)) continue;
                if (!Int32.TryParse(parts[1].Trim(), out y)) continue;
                builder.Append('\n').Append(x).Append(',').Append(y).Append('=')
                    .Append(Map.IsBlockSolid(x, y) ? "1" : "0");
            }
            return builder.ToString();
        }

        private static string ProbeBlocks(Dictionary<string, string> command)
        {
            if (currentMap == null) return "ok=0\nerror=no map";
            string path = Get(command, "cells");
            if (string.IsNullOrEmpty(path) || !File.Exists(path))
                return "ok=0\nerror=cells file missing";
            StringBuilder builder = new StringBuilder("ok=1");
            string[] lines = File.ReadAllLines(path, Utf8NoBom);
            for (int index = 0; index < lines.Length; index++)
            {
                string line = lines[index].Trim();
                if (line.Length == 0 || line.StartsWith("#")) continue;
                string[] parts = line.Split(',');
                int x, y;
                if (parts.Length < 2 || !Int32.TryParse(parts[0].Trim(), out x)
                    || !Int32.TryParse(parts[1].Trim(), out y)) continue;
                TerrainType terrain = currentMap.foregroundBlocks[x, y];
                builder.Append('\n').Append(x).Append(',').Append(y).Append('=')
                    .Append(terrain.ToString())
                    .Append(",").Append(Map.IsBlockSolid(x, y) ? "1" : "0")
                    .Append(",").Append(Map.IsBlockLadder(x, y) ? "1" : "0")
                    .Append(",").Append(Map.IsBlockQuicksand(x, y) ? "1" : "0")
                    .Append(",").Append(Map.IsBlockDestroyed(x, y) ? "1" : "0");
            }
            return builder.ToString();
        }

        private static string EnsurePlayer()
        {
            if (HeroController.GetPlayersPlayingCount() == 0)
                typeof(HeroController).GetMethod("AutoJoinLocalHost", Static).Invoke(null, null);
            HeroController.SpawnJoinedPlayers();
            HeroController.isCountdownFinished = true;
            return "ok=1\nplayers=" + HeroController.GetPlayersPlayingCount();
        }

        // tiles 文件格式：首行可选的 "legend=char:TerrainTypeName,char:..."，其余每行一个字符串。
        // 行从上到下书写（第一行 = 顶部），桥接自动翻转为 row 0 = 底部。
        private static string SetTiles(Dictionary<string, string> command)
        {
            if (currentMap == null) return "ok=0\nerror=no map; call new_map first";
            string tilesPath = Get(command, "tiles");
            if (string.IsNullOrEmpty(tilesPath) || !File.Exists(tilesPath))
                return "ok=0\nerror=tiles file missing: " + OneLine(tilesPath);
            string legendText = Get(command, "legend");
            Dictionary<char, TerrainType> legend = new Dictionary<char, TerrainType>();
            legend[' '] = TerrainType.Empty;
            legend['.'] = TerrainType.Empty;
            if (!string.IsNullOrEmpty(legendText))
            {
                string[] pairs = legendText.Split(',');
                for (int index = 0; index < pairs.Length; index++)
                {
                    string pair = pairs[index].Trim();
                    int split = pair.IndexOf(':');
                    if (split <= 0) continue;
                    string key = pair.Substring(0, split);
                    if (key.Length == 0) continue;
                    string valueName = pair.Substring(split + 1).Trim();
                    try { legend[key[0]] = (TerrainType)Enum.Parse(typeof(TerrainType), valueName, true); }
                    catch (Exception) { return "ok=0\nerror=unknown TerrainType: " + OneLine(valueName); }
                }
            }

            string[] lines = File.ReadAllLines(tilesPath, Utf8NoBom);
            TerrainType[,] foreground = Get(command, "layer") == "background" ? currentMap.backgroundBlocks : currentMap.foregroundBlocks;
            bool columnFirst = foreground.GetLength(0) == currentMap.Width;
            int columns = columnFirst ? foreground.GetLength(0) : foreground.GetLength(1);
            int rows = columnFirst ? foreground.GetLength(1) : foreground.GetLength(0);
            int painted = 0;
            for (int lineIndex = 0; lineIndex < lines.Length; lineIndex++)
            {
                string line = lines[lineIndex];
                if (line.StartsWith("legend=")) continue;
                if (line.Length == 0) continue;
                int row = rows - 1 - lineIndex; // 文件第一行是顶部
                if (row < 0) break;
                for (int column = 0; column < line.Length && column < columns; column++)
                {
                    TerrainType value;
                    if (!legend.TryGetValue(line[column], out value)) value = TerrainType.Empty;
                    if (columnFirst) foreground[column, row] = value;
                    else foreground[row, column] = value;
                    painted++;
                }
            }
            return "ok=1\npainted=" + painted + "\ncolumns=" + columns + "\nrows=" + rows;
        }

        // doodads 文件：每行 x,y,DoodadTypeName[,variation]
        private static string AddDoodads(Dictionary<string, string> command)
        {
            if (currentMap == null) return "ok=0\nerror=no map; call new_map first";
            string path = Get(command, "doodads");
            if (string.IsNullOrEmpty(path) || !File.Exists(path))
                return "ok=0\nerror=doodads file missing: " + OneLine(path);
            if (currentMap.DoodadList == null)
                currentMap.DoodadList = new List<DoodadInfo>();
            string[] lines = File.ReadAllLines(path, Utf8NoBom);
            int added = 0;
            for (int index = 0; index < lines.Length; index++)
            {
                string line = lines[index].Trim();
                if (line.Length == 0 || line.StartsWith("#")) continue;
                string[] parts = line.Split(',');
                if (parts.Length < 3) continue;
                int x, y, variation = 0;
                if (!Int32.TryParse(parts[0].Trim(), out x)) continue;
                if (!Int32.TryParse(parts[1].Trim(), out y)) continue;
                if (parts.Length > 3) Int32.TryParse(parts[3].Trim(), out variation);
                DoodadType type;
                try { type = (DoodadType)Enum.Parse(typeof(DoodadType), parts[2].Trim(), true); }
                catch (Exception) { continue; }
                currentMap.DoodadList.Add(new DoodadInfo(new GridPoint(x, y), type, variation));
                added++;
            }
            return "ok=1\nadded=" + added + "\ntotal=" + currentMap.DoodadList.Count;
        }

        // add_trigger：type,blx,bly,urx,ury[,action=ActionTypeName:actionName][,action=...]
        private static string ProbeDoodads(Dictionary<string, string> command)
        {
            if (currentMap == null) return "ok=0\nerror=no map in memory";
            string typeName = Get(command, "type");
            if (string.IsNullOrEmpty(typeName)) return "ok=0\nerror=type required";
            DoodadType wanted;
            try { wanted = (DoodadType)Enum.Parse(typeof(DoodadType), typeName, true); }
            catch (Exception) { return "ok=0\nerror=unknown DoodadType: " + OneLine(typeName); }
            int limit = GetInt(command, "limit", 8);
            TerrainType[,] foreground = currentMap.foregroundBlocks;
            bool columnFirst = foreground.GetLength(0) == currentMap.Width;
            int columns = columnFirst ? foreground.GetLength(0) : foreground.GetLength(1);
            int rows = columnFirst ? foreground.GetLength(1) : foreground.GetLength(0);
            int[] tops = new int[columns];
            for (int column = 0; column < columns; column++)
            {
                int top = -1;
                for (int row = rows - 1; row >= 0; row--)
                {
                    TerrainType value = columnFirst ? foreground[column, row] : foreground[row, column];
                    if (value != TerrainType.Empty) { top = row; break; }
                }
                tops[column] = top;
            }
            StringBuilder builder = new StringBuilder("ok=1");
            List<DoodadInfo> doodads = currentMap.DoodadList;
            int found = 0;
            if (doodads != null)
            {
                for (int index = 0; index < doodads.Count && found < limit; index++)
                {
                    DoodadInfo info = doodads[index];
                    if (info == null || info.type != wanted || info.position == null) continue;
                    int column = info.position.collumn;
                    int row = info.position.row;
                    int top = (column >= 0 && column < columns) ? tops[column] : -2;
                    builder.Append("\n").Append(column).Append(',').Append(row)
                        .Append(" top=").Append(top).Append(" delta=").Append(row - top);
                    found++;
                }
            }
            builder.Append("\nmatched=").Append(found);
            return builder.ToString();
        }

        private static string AddTrigger(Dictionary<string, string> command)
        {
            if (currentMap == null) return "ok=0\nerror=no map in memory";
            if (currentMap.TriggerList == null) currentMap.TriggerList = new List<TriggerInfo>();
            TriggerInfo info = new TriggerInfo();
            info.type = (TriggerType)GetInt(command, "type", 0);
            info.bottomLeft = new GridPoint(GetInt(command, "blx", 0), GetInt(command, "bly", 0));
            info.upperRight = new GridPoint(GetInt(command, "urx", 0), GetInt(command, "ury", 0));
            info.startEnabled = true;
            info.onlyTriggerOnce = Get(command, "persist_on_reload") != "0";
            info.actions = new List<TriggerActionInfo>();
            for (int index = 0; ; index++)
            {
                string raw = Get(command, "action" + (index == 0 ? "" : index.ToString()));
                if (string.IsNullOrEmpty(raw)) break;
                string[] parts = raw.Split(':');
                TriggerActionType actionType;
                try { actionType = (TriggerActionType)Enum.Parse(typeof(TriggerActionType), parts[0].Trim(), true); }
                catch (Exception) { return "ok=0\nerror=unknown TriggerActionType: " + OneLine(parts[0]); }
                string infoTypeName = actionType.ToString() + "ActionInfo";
                if (actionType == TriggerActionType.CameraMove) infoTypeName = "CameraActionInfo";
                Type infoType = GameType(infoTypeName);
                if (infoType == null || infoType.IsAbstract)
                    return "ok=0\nerror=no concrete info class for " + actionType;
                TriggerActionInfo action = (TriggerActionInfo)Activator.CreateInstance(infoType);
                action.type = actionType;
                action.name = parts.Length > 1 ? parts[1].Trim() : "";
                action.timeOffset = 0f;
                if (action is CollapseActionInfo)
                {
                    CollapseActionInfo collapse = (CollapseActionInfo)action;
                    collapse.targetPoint = new GridPoint(GetInt(command, "target_x", 0), GetInt(command, "target_y", 0));
                    collapse.damage = GetInt(command, "damage", 100);
                }
                info.actions.Add(action);
            }
            currentMap.TriggerList.Add(info);
            return "ok=1\ntriggers=" + currentMap.TriggerList.Count
                + "\nlast.type=" + info.type + "\nlast.actions=" + info.actions.Count;
        }

        private static string DumpTriggers(Dictionary<string, string> command)
        {
            if (currentMap == null) return "ok=0\nerror=no map in memory";
            StringBuilder builder = new StringBuilder("ok=1");
            List<TriggerInfo> triggers = currentMap.TriggerList;
            builder.Append("\ntriggers=").Append(triggers == null ? 0 : triggers.Count);
            if (triggers != null)
            {
                for (int index = 0; index < triggers.Count; index++)
                {
                    TriggerInfo info = triggers[index];
                    if (info == null) continue;
                    builder.Append("\ntrigger.").Append(index)
                        .Append("=type:").Append(info.type)
                        .Append(" bl:").Append(info.bottomLeft == null ? "?" : info.bottomLeft.collumn + "," + info.bottomLeft.row)
                        .Append(" ur:").Append(info.upperRight == null ? "?" : info.upperRight.collumn + "," + info.upperRight.row)
                        .Append(" name:").Append(OneLine(info.name))
                        .Append(" actions:").Append(info.actions == null ? 0 : info.actions.Count);
                    if (info.actions != null)
                    {
                        for (int actionIndex = 0; actionIndex < info.actions.Count; actionIndex++)
                        {
                            TriggerActionInfo action = info.actions[actionIndex];
                            if (action == null) continue;
                            builder.Append(" [").Append(action.type).Append(':').Append(OneLine(action.name)).Append(']');
                        }
                    }
                }
            }
            List<DoodadInfo> doodads = currentMap.DoodadList;
            if (doodads != null)
            {
                for (int index = 0; index < doodads.Count && index < 20; index++)
                {
                    DoodadInfo info = doodads[index];
                    if (info == null) continue;
                    builder.Append("\ndoodad.").Append(index).Append('=').Append(info.type)
                        .Append(" at ").Append(info.position == null ? "?" : info.position.collumn + "," + info.position.row)
                        .Append(" var ").Append(info.variation);
                }
            }
            return builder.ToString();
        }

        private static string InspectLevel(Dictionary<string, string> command)
        {
            MapData map;
            if (!TryLoadLevel(command, out map)) return "ok=0\nerror=level not found";
            currentMap = map;
            string cacheName = Get(command, "name");
            if (string.IsNullOrEmpty(cacheName)) cacheName = "INSPECTED LEVEL";
            CampaignHeader cacheHeader = new CampaignHeader();
            cacheHeader.name = cacheName;
            cacheHeader.author = "BROFORCEMAPBRIDGE";
            cacheHeader.description = Get(command, "description") ?? "";
            cacheHeader.length = 1;
            cacheHeader.gameMode = GameMode.Campaign;
            Campaign cacheCampaign = new Campaign();
            cacheCampaign.name = cacheName;
            cacheCampaign.header = cacheHeader;
            cacheCampaign.levels = new MapData[] { map };
            cacheCampaign.brodownLevel = 0;
            currentCampaign = cacheCampaign;
            return "ok=1\n" + DescribeMap(map);
        }

        private static string CloneLevel(Dictionary<string, string> command)
        {
            MapData map;
            if (!TryLoadLevel(command, out map)) return "ok=0\nerror=level not found";
            string resource = Get(command, "resource");
            string name = Get(command, "name");
            if (string.IsNullOrEmpty(name)) name = "AI REAL MAP " + resource;
            string author = Get(command, "author");
            if (string.IsNullOrEmpty(author)) author = "BROFORCEMAPBRIDGE";
            string note = Get(command, "description");
            if (note == null) note = "克隆关卡范本，2026-11-15 V0.1";
            if (string.IsNullOrEmpty(Get(command, "path")))
            {
                string safeName = name;
                char[] invalid = Path.GetInvalidFileNameChars();
                for (int index = 0; index < invalid.Length; index++)
                    safeName = safeName.Replace(invalid[index], '_');
                command["path"] = Path.Combine(Path.Combine(AppDataDirectory, "maps"), safeName + ".bfc");
            }
            map.levelDescription = note;

            CampaignHeader header = new CampaignHeader();
            header.name = name;
            header.author = author;
            header.description = note;
            header.length = 1;
            header.gameMode = GameMode.Campaign;
            header.hasTimeScoreBoard = true;

            Campaign campaign = new Campaign();
            campaign.name = name;
            campaign.header = header;
            campaign.levels = new MapData[] { map };
            campaign.brodownLevel = 0;

            currentMap = map;
            currentCampaign = campaign;
            return SaveBfg(command) + "\n" + DescribeMap(map);
        }

        private static bool TryLoadLevel(Dictionary<string, string> command, out MapData map)
        {
            map = null;
            string resource = Get(command, "resource");
            string path = Get(command, "path");
            try
            {
                if (!string.IsNullOrEmpty(resource))
                {
                    MethodInfo load = GameType("FileIO").GetMethod("LoadLevelFromResources", Static);
                    if (load == null) return false;
                    map = load.Invoke(null, new object[] { resource }) as MapData;
                    return map != null;
                }
                if (!string.IsNullOrEmpty(path))
                {
                    MethodInfo loadCampaignBytes = GameType("FileIO").GetMethod("LoadCampaignBytes", Static);
                    if (loadCampaignBytes == null) return false;
                    byte[] bytes = File.ReadAllBytes(path);
                    // .bfc = 不加密；.bfg = 加密。两种都试一次。
                    bool[] encryptedOptions = new bool[] { false, true };
                    for (int index = 0; index < encryptedOptions.Length; index++)
                    {
                        try
                        {
                            Campaign campaign = loadCampaignBytes.Invoke(null,
                                new object[] { bytes, encryptedOptions[index], true }) as Campaign;
                            if (campaign == null || campaign.levels == null || campaign.levels.Length == 0) continue;
                            map = campaign.levels[0];
                            if (map != null) return true;
                        }
                        catch (Exception) { }
                    }
                    return false;
                }
            }
            catch (Exception)
            {
                return false;
            }
            return false;
        }

        private static string DescribeMap(MapData map)
        {
            TerrainType[,] foreground = map.foregroundBlocks;
            int first = foreground == null ? 0 : foreground.GetLength(0);
            int second = foreground == null ? 0 : foreground.GetLength(1);
            Dictionary<TerrainType, int> terrain = new Dictionary<TerrainType, int>();
            for (int a = 0; a < first; a++)
            {
                for (int b = 0; b < second; b++)
                {
                    TerrainType value = foreground[a, b];
                    int count;
                    terrain.TryGetValue(value, out count);
                    terrain[value] = count + 1;
                }
            }
            Dictionary<DoodadType, int> doodads = new Dictionary<DoodadType, int>();
            List<DoodadInfo> doodadList = map.DoodadList;
            if (doodadList != null)
            {
                for (int index = 0; index < doodadList.Count; index++)
                {
                    DoodadInfo info = doodadList[index];
                    if (info == null) continue;
                    int count;
                    doodads.TryGetValue(info.type, out count);
                    doodads[info.type] = count + 1;
                }
            }
            int triggerCount = map.TriggerList == null ? 0 : map.TriggerList.Count;
            StringBuilder builder = new StringBuilder();
            builder.Append("width=").Append(map.Width).Append("\nheight=").Append(map.Height);
            builder.Append("\narray=").Append(first).Append('x').Append(second);
            builder.Append("\ndoodads=").Append(doodadList == null ? 0 : doodadList.Count);
            builder.Append("\ntriggers=").Append(triggerCount);
            builder.Append("\nentities=").Append(map.entityTriggers == null ? 0 : map.entityTriggers.Count);
            foreach (KeyValuePair<TerrainType, int> pair in terrain)
                builder.Append("\nterrain.").Append(pair.Key).Append('=').Append(pair.Value);
            foreach (KeyValuePair<DoodadType, int> pair in doodads)
                builder.Append("\ndoodad.").Append(pair.Key).Append('=').Append(pair.Value);
            return builder.ToString();
        }

        private static string ListLevels(Dictionary<string, string> command)
        {
            StringBuilder builder = new StringBuilder("ok=1");
            string gameLevels = Path.Combine(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, ".."), "Levels");
            AppendFiles(builder, "game", gameLevels);
            AppendFiles(builder, "local", Path.Combine(AppDataDirectory, "maps"));
            return builder.ToString();
        }

        private static void AppendFiles(StringBuilder builder, string key, string directory)
        {
            try
            {
                if (!Directory.Exists(directory)) return;
                string[] files = Directory.GetFiles(directory, "*.bfc");
                string[] bfgFiles = Directory.GetFiles(directory, "*.bfg");
                if (bfgFiles.Length > 0) { string[] merged = new string[files.Length + bfgFiles.Length]; Array.Copy(files, merged, files.Length); Array.Copy(bfgFiles, 0, merged, files.Length, bfgFiles.Length); files = merged; }
                Array.Sort(files);
                for (int index = 0; index < files.Length; index++)
                {
                    builder.Append('\n').Append(key).Append('.').Append(index).Append('=')
                        .Append(OneLine(Path.GetFileName(files[index])));
                }
            }
            catch (Exception) { }
        }

        // ---------------- 帮助方法 ----------------

        private const BindingFlags Static = BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.FlattenHierarchy;

        private static Type GameType(string name)
        {
            Type type = Type.GetType(name + ", Assembly-CSharp") ?? Type.GetType(name);
            if (type != null) return type;
            Assembly assembly = typeof(MapData).Assembly;
            Type[] types;
            try { types = assembly.GetTypes(); }
            catch (ReflectionTypeLoadException exception) { types = exception.Types; }
            for (int index = 0; index < types.Length; index++)
            {
                Type candidate = types[index];
                if (candidate == null) continue;
                if (candidate.FullName == name || candidate.Name == name) return candidate;
            }
            return null;
        }

        private static Dictionary<string, string> ParseKeyValues(string[] lines)
        {
            Dictionary<string, string> values = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            for (int index = 0; index < lines.Length; index++)
            {
                string line = lines[index];
                if (line == null) continue;
                line = line.Trim();
                if (line.Length == 0 || line[0] == '#') continue;
                int split = line.IndexOf('=');
                if (split <= 0) continue;
                values[line.Substring(0, split).Trim()] = line.Substring(split + 1).Trim();
            }
            return values;
        }

        private static string Get(Dictionary<string, string> values, string key)
        {
            string value;
            return values.TryGetValue(key, out value) ? value : null;
        }

        private static int GetInt(Dictionary<string, string> values, string key, int fallback)
        {
            int parsed;
            return Int32.TryParse(Get(values, key), NumberStyles.Integer, CultureInfo.InvariantCulture, out parsed)
                ? parsed : fallback;
        }

        private static string OneLine(string value)
        {
            if (value == null) return "";
            return value.Replace('\r', ' ').Replace('\n', ' ');
        }

        private static void WriteHeartbeat()
        {
            try
            {
                string text = "pid=" + CurrentPid + "\ntime=" + DateTime.UtcNow.ToString("o")
                    + "\ninMemory=" + (currentMap == null ? "none" : currentMap.Width + "x" + currentMap.Height);
                File.WriteAllText(Path.Combine(IpcDirectory, "heartbeat.txt"), text, Utf8NoBom);
            }
            catch (Exception) { }
        }

        private static void WriteStatus(string line)
        {
            try
            {
                Directory.CreateDirectory(AppDataDirectory);
                File.WriteAllText(StatusPath, DateTime.UtcNow.ToString("o") + "|" + CurrentPid + "|" + OneLine(line), Utf8NoBom);
            }
            catch (Exception) { }
        }
    }

    public sealed class MapBridgeBehaviour : UnityEngine.MonoBehaviour
    {
        private int frameCounter;

        private void Awake()
        {
            BootstrapSelf();
        }

        private static void BootstrapSelf() { }

        private void Update()
        {
            // 每 6 帧轮询一次文件队列，避免每帧 IO。
            if (++frameCounter % 6 != 0) return;
            Flush();
        }

        private void Flush()
        {
            try
            {
                typeof(Bootstrap).GetMethod("ProcessCommands", BindingFlags.Static | BindingFlags.NonPublic | BindingFlags.Public)
                    .Invoke(null, null);
            }
            catch (Exception) { }
        }

        private void OnDestroy()
        {
            try
            {
                typeof(Bootstrap).GetMethod("OnDestroyed", BindingFlags.Static | BindingFlags.NonPublic | BindingFlags.Public)
                    .Invoke(null, null);
            }
            catch (Exception) { }
        }
    }
}
