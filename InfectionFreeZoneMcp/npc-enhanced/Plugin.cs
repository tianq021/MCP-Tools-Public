using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using BepInEx;
using BepInEx.Configuration;
using Controllers.Time;
using Gameplay.Units.Characters;
using Gameplay.Units.Player.Workers.WorkSystem;
using HarmonyLib;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace IfzNpcEnhanced;

[BepInPlugin("local.ifz.npc-enhanced", "IFZ Enhanced NPC", "0.2.0")]
public sealed class Plugin : BaseUnityPlugin
{
    internal const string CoopHash = "A36C10726AA1B127BF1F06F5F6B87F974A7869CA342387ACE3CBA640C64CAD8B";
    internal const string GameHash = "56464F9C355C350D3EB274CA2E56455CA629A00A7DC02774F4E99A125009DB8F";
    static Plugin self;
    internal static Plugin Instance => self;
    internal CoopAdapter Adapter;
    Director director;
    Harmony harmony;
    ConfigEntry<bool> featureEnabled, autoSpawn, hud, brainGuards;
    ConfigEntry<int> slot;
    float retryAt;
    bool rejected;
    float nextHookError;
    string status = "等待联机模组加载。请通过“创建房间”开始新战役。";
    GUIStyle panelStyle, bodyStyle;
    Font panelFont;
    internal ulong Selected => director?.FactionId ?? 0;
    internal bool BrainGuards => brainGuards == null || brainGuards.Value;
    internal Director McpDirector(string faction)
    {
        if (!featureEnabled.Value || Adapter == null || !Adapter.Ready || SaveHandler.LoadingData)
            throw new NpcFault("npc_not_ready", "等待联机地图和 NPC 总部载入。");
        var record = SelectRecord();
        if (record == null || CoopAdapter.Id(Adapter.Owner(record)).ToString() != faction)
            throw new NpcFault("faction_forbidden", "只能控制配置中选定的 NPC 阵营。");
        if (director == null || director.FactionId.ToString() != faction || director.Campaign != Adapter.Campaign)
            director = new Director(this, Adapter, record);
        return director;
    }
    internal string SelectedFaction => Adapter == null ? null : SelectRecord() is object record ? CoopAdapter.Id(Adapter.Owner(record)).ToString() : null;
    internal string DataDirectory => Path.Combine(Paths.ConfigPath, "IfzNpcEnhanced");

    void Awake()
    {
        self = this;
        featureEnabled = Config.Bind("General", "Enabled", true, "Enable the enhanced NPC controller.");
        autoSpawn = Config.Bind("General", "EnsureOneNpcOnNewCampaign", true, "If a NEW hosted campaign has zero NPC slots, request one. Existing saves unchanged.");
        slot = Config.Bind("General", "NpcSlot", 0, new ConfigDescription("Zero-based NPC slot, ordered by faction ID.", new AcceptableValueRange<int>(0, 31)));
        hud = Config.Bind("General", "ShowPanel", true, "Show faction location and live activity. F8 toggles the panel.");
        brainGuards = Config.Bind("General", "BrainGuards", true, "Skip clearly losing attacks for every NPC faction so squads stop dying for nothing.");
        SceneManager.activeSceneChanged += SceneChanged;
        Directory.CreateDirectory(DataDirectory);
    }
    void SceneChanged(Scene oldScene, Scene newScene) { director = null; status = "等待主机聚落载入……"; }
    internal void Log(string message) { Logger.LogInfo(message); }
    internal void Error(Exception error) { Logger.LogError(error); }
    void Update()
    {
        if (Input.GetKeyDown(KeyCode.F8)) hud.Value = !hud.Value;
        if (!featureEnabled.Value || Adapter != null || rejected || Time.realtimeSinceStartup < retryAt) return;
        retryAt = Time.realtimeSinceStartup + 5;
        var assembly = AppDomain.CurrentDomain.GetAssemblies().FirstOrDefault(a => a.GetName().Name == "IFZ.Multiplayer.GameIntegration");
        if (assembly == null) return;
        try
        {
            if (Hash(assembly.Location) != CoopHash || Hash(typeof(TimeController).Assembly.Location) != GameHash)
                throw new InvalidOperationException("Unsupported game/mod binary. Controller disabled; expected supplied RC7-P7 and game build 26104777.");
            Adapter = new CoopAdapter(assembly);
            harmony = new Harmony("local.ifz.npc-enhanced");
            Patch(Adapter.Brain, "Update", null, nameof(AfterBrainUpdate));
            Patch(Adapter.Brain, "TickFaction", nameof(BeforeFactionTick), null);
            Patch(Adapter.Lifecycle, "BootstrapIfNeeded", nameof(BeforeBootstrap), null);
            Patch(typeof(WorkBase), "AddWorker", nameof(BeforeAddWorker), null);
            Compatibility.Install(this, harmony);
            status = "已就绪。创建联机战役并选择你的总部后，NPC 将自动建立聚落。";
            Log(status);
        }
        catch (Exception ex)
        { harmony?.UnpatchSelf(); Adapter = null; rejected = true; status = "已停用：" + ex.Message; Error(ex); }
    }
    void Patch(Type type, string method, string prefix, string postfix) => harmony.Patch(AccessTools.Method(type, method),
        prefix: prefix == null ? null : new HarmonyMethod(typeof(Plugin), prefix),
        postfix: postfix == null ? null : new HarmonyMethod(typeof(Plugin), postfix));
    static string Hash(string path) { using var stream = File.OpenRead(path); using var sha = SHA256.Create(); return BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", ""); }
    static void BeforeBootstrap()
    {
        if (self?.featureEnabled.Value != true || !self.autoSpawn.Value) return;
        var a = self.Adapter;
        if (CoopAdapter.Get(a.Persistence, "PendingLoadedCampaign") == null && a.Records.Length == 0 &&
            (int)CoopAdapter.Get(a.Settings, "Count") == 0)
            AccessTools.Field(a.Settings, "Count").SetValue(null, 1);
    }
    object SelectRecord() => Adapter.Records.Where(r => (bool)CoopAdapter.Get(r, "Founded"))
        .OrderBy(r => CoopAdapter.Id(Adapter.Owner(r))).Skip(slot.Value).FirstOrDefault();
    static bool BeforeFactionTick(object __1)
    {
        if (self?.featureEnabled.Value != true || self.Adapter == null) return true;
        var record = self.SelectRecord();
        return record == null || CoopAdapter.Id(self.Adapter.Owner(record)) != CoopAdapter.Id(self.Adapter.Owner(__1));
    }
    static void AfterBrainUpdate(object __0)
    {
        if (self?.featureEnabled.Value != true || self.Adapter == null) return;
        try
        {
            var a = self.Adapter;
            if (CoopAdapter.Get(__0, "Host") == null || !a.Ready || SaveHandler.LoadingData || TimeController.IsPaused) return;
            Compatibility.AuditExistingOrders();
            var record = self.SelectRecord();
            if (record == null) { self.status = "尚未建立 NPC 聚落。请创建新联机战役，并选择你的总部。"; return; }
            var id = CoopAdapter.Id(a.Owner(record));
            if (self.director == null || self.director.FactionId != id || self.director.Campaign != a.Campaign)
                self.director = new Director(self, a, record);
            self.director.Tick();
            self.status = self.director.Summary;
        }
        catch (Exception ex)
        {
            self.status = "控制器异常，请查看 BepInEx 日志：" + ex.GetBaseException().Message;
            if (Time.realtimeSinceStartup >= self.nextHookError) { self.nextHookError = Time.realtimeSinceStartup + 30; self.Error(ex); }
        }
    }
    // Defense in depth: native auto-allocation must not move this faction's people across ownership boundaries.
    static bool BeforeAddWorker(WorkBase __instance, Character worker, ref bool __result)
    {
        if (self?.featureEnabled.Value != true || self.Selected == 0) return true;
        try
        {
            var a = self.Adapter; var workOwner = a.OwnerOf(__instance.GetRelatedStructure()); var workerOwner = a.OwnerOf(worker);
            if (workOwner != self.Selected && workerOwner != self.Selected) return true;
            if (Policy.CanAssign(self.Selected, workerOwner, workOwner)) return true;
        }
        catch (Exception ex) { self.Error(ex); }
        __result = false; return false;
    }
    void OnGUI()
    {
        if (!hud.Value || !featureEnabled.Value || Adapter?.Ready != true) return;
        if (panelStyle == null)
        {
            panelFont = Font.CreateDynamicFontFromOSFont(new[] { "Microsoft YaHei", "SimHei", "SimSun" }, 16);
            panelStyle = new GUIStyle(GUI.skin.box) { font = panelFont, fontSize = 16 };
            bodyStyle = new GUIStyle(GUI.skin.label) { font = panelFont, fontSize = 15, wordWrap = true };
        }
        GUI.Box(new Rect(12, 170, 440, 210), "加强版 NPC 0.2.0  |  F8 显示/隐藏", panelStyle);
        GUI.Label(new Rect(24, 198, 415, 175), status, bodyStyle);
    }
    void OnDestroy() { SceneManager.activeSceneChanged -= SceneChanged; harmony?.UnpatchSelf(); if (self == this) self = null; }
}
