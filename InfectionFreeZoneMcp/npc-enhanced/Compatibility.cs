using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using Controllers;
using Gameplay.Buildings;
using Gameplay.Units;
using Gameplay.Units.Characters;
using Gameplay.Units.Orders;
using Gameplay.Units.Player;
using Gameplay.Units.Player.Workers;
using Gameplay.Units.Workers.WorkSystem;
using HarmonyLib;
using ScriptableObjectScripts.UI;
using TMPro;
using UnityEngine;

namespace IfzNpcEnhanced;

// Native automatic cargo returns run outside the director's owner context.
// Cover all registered NPC factions, including those still using the original brain.
internal static class Compatibility
{
    static Plugin plugin;
    static float nextAudit;
    static readonly AccessTools.FieldRef<OrdersHandlers, Group> GroupField =
        AccessTools.FieldRefAccess<OrdersHandlers, Group>("_group");
    static readonly AccessTools.FieldRef<MoveToBuildingOrder, bool> ExchangeField =
        AccessTools.FieldRefAccess<MoveToBuildingOrder, bool>("_isForExchange");
    static readonly AccessTools.FieldRef<SquadsCreator, WorkController> SquadsWorkField =
        AccessTools.FieldRefAccess<SquadsCreator, WorkController>("_workController");
    static readonly AccessTools.FieldRef<WorkersPanelController, Dictionary<Employment, WorkersLabel>> LabelsField =
        AccessTools.FieldRefAccess<WorkersPanelController, Dictionary<Employment, WorkersLabel>>("_labels");
    static readonly FieldInfo UnemployedTextField = AccessTools.Field(typeof(WorkersPanelController), "unemployedCount");

    internal static void Install(Plugin value, Harmony harmony)
    {
        plugin = value;
        harmony.Patch(AccessTools.Method(typeof(OrdersHandlers), "AddOrderMoveToNearestStockroom"),
            prefix: new HarmonyMethod(typeof(Compatibility), nameof(BeforeNearestStockroom)));
        harmony.Patch(AccessTools.Method(typeof(MoveToBuildingOrder), "StartOrder"),
            prefix: new HarmonyMethod(typeof(Compatibility), nameof(BeforeBuildingOrder)));
        harmony.Patch(AccessTools.Method(typeof(MoveToBuildingOrder), "GetState"),
            prefix: new HarmonyMethod(typeof(Compatibility), nameof(BeforeBuildingState)));
        harmony.Patch(AccessTools.Method(typeof(Controllers.Time.TimeController).Assembly.GetType("Analytics.AnalyticsSender", true), "OnMissionStart"),
            prefix: new HarmonyMethod(typeof(Compatibility), nameof(BeforeMissionAnalytics)));
        // Native squad creation conscripts from the GLOBAL worker pool with no faction filter.
        // A second settlement's starter squads therefore used to steal the NPC's workers and turn
        // them into the host's soldiers (observed live: NPC workers 4 -> 0 at founding).
        harmony.Patch(AccessTools.Method(typeof(CharactersConverter), "ConvertTo", new[] { typeof(Character), typeof(CharacterDraft) }),
            prefix: new HarmonyMethod(typeof(Compatibility), nameof(BeforeConvertForeignWorker)));
        harmony.Patch(AccessTools.Method(typeof(SquadsCreator), "SelectBestWorkersToSquad", new[] { typeof(int) }),
            prefix: new HarmonyMethod(typeof(Compatibility), nameof(BeforeSelectSquadWorkers)));
        harmony.Patch(AccessTools.Method(typeof(WorkController), "GetWorkerForSquad"),
            postfix: new HarmonyMethod(typeof(Compatibility), nameof(AfterGetWorkerForSquad)));
        // The vanilla citizens/employment panel sums every faction's people (global statics).
        // Show the host's own people so an NPC settlement never inflates the player's numbers.
        harmony.Patch(AccessTools.Method(typeof(WorkersPanelController), "SetAllCitizensCount"),
            prefix: new HarmonyMethod(typeof(Compatibility), nameof(BeforeAllCitizensCount)));
        harmony.Patch(AccessTools.Method(typeof(WorkersPanelController), "SetUnemployedCount"),
            prefix: new HarmonyMethod(typeof(Compatibility), nameof(BeforeUnemployedCount)));
        harmony.Patch(AccessTools.Method(typeof(WorkersPanelController), "SetSquadMembers"),
            prefix: new HarmonyMethod(typeof(Compatibility), nameof(BeforeSquadMembers)));
        harmony.Patch(AccessTools.Method(typeof(WorkersPanelController), "UpdateUnemployedText"),
            prefix: new HarmonyMethod(typeof(Compatibility), nameof(BeforeUnemployedText)));
        // The mod's own brain sends squads at nearby infected groups even when the faction is spent,
        // then replaces them with freshly spawned squads: a wasteful death loop. Skip losing attacks.
        var brainAttack = AccessTools.Method(plugin.Adapter.Brain, "ExecuteAttackInfected");
        if (brainAttack != null)
            harmony.Patch(brainAttack, prefix: new HarmonyMethod(typeof(Compatibility), nameof(BeforeBrainAttack)));
    }

    static float nextGuardLog;
    // Called for every NPC faction still on the original brain (ours is blocked from it entirely).
    static bool BeforeBrainAttack(object __0)
    {
        try
        {
            var a = plugin?.Adapter;
            if (a == null || !plugin.BrainGuards) return true;
            var owner = CoopAdapter.Id(CoopAdapter.Get(__0, "Id"));
            if (owner == 0) return true;
            var container = a.Container;
            if (container == null) return true;
            var squads = container.Resolve<SquadsController>().Squads
                .Where(s => s != null && !s.IsDestroying && a.OwnerOfEntity(s.Id) == owner).ToArray();
            if (squads.Length == 0) return true;
            var members = squads.Sum(s => s.Characters.Count);
            var weakest = squads.Min(s => s.Characters.Count == 0 ? 0f : s.Characters.Min(c => c.Health.Hp / Math.Max(1f, c.Health.MaxHp)));
            if (members >= 8 && weakest >= .7f) return true;
            if (Time.realtimeSinceStartup >= nextGuardLog)
            {
                nextGuardLog = Time.realtimeSinceStartup + 60;
                plugin.Log($"NPC brain guard: skipped a losing attack (owner {owner}, squads {squads.Length}, members {members}, weakest {weakest:P0})");
            }
            return false;
        }
        catch (Exception ex) { plugin?.Error(ex); return true; }
    }

    static int NpcWorkers()
    {
        var a = plugin?.Adapter;
        if (a == null) return 0;
        return a.Container.Resolve<WorkersController>().Workers.Count(w => w != null && ForeignWorker(w));
    }
    static int NpcSoldiers()
    {
        var a = plugin?.Adapter;
        if (a == null) return 0;
        return a.Container.Resolve<SquadsController>().Squads
            .Where(s => s != null && !s.IsDestroying && IsRegisteredNpcOwner(a.OwnerOfEntity(s.Id))).Sum(s => s.Characters.Count);
    }
    static int PlayerWorkers() { var a = plugin?.Adapter; return a == null ? WorkersController.WorkersCount : Math.Max(0, WorkersController.WorkersCount - NpcWorkers()); }
    static int PlayerSoldiers() { var a = plugin?.Adapter; return a == null ? SquadsController.SoldiersCount : Math.Max(0, SquadsController.SoldiersCount - NpcSoldiers()); }
    static int PlayerAvailable()
    {
        var a = plugin?.Adapter;
        if (a == null) return WorkController.AvailableWorkersCount;
        try { return a.Container.Resolve<WorkController>().AvailableWorkers.Count(c => c != null && !ForeignWorker(c)); }
        catch { return Math.Max(0, WorkController.AvailableWorkersCount - NpcWorkers()); }
    }
    static void RefreshUnemployedText(WorkersPanelController panel)
    {
        var tmp = UnemployedTextField?.GetValue(panel) as TextMeshProUGUI;
        if (tmp != null) tmp.text = $"{PlayerAvailable()}/{PlayerWorkers() + PlayerSoldiers()} ";
    }
    static bool BeforeAllCitizensCount(WorkersPanelController __instance, ref int count)
    {
        try { count = Math.Max(0, count - NpcWorkers() - NpcSoldiers()); }
        catch (Exception ex) { plugin?.Error(ex); }
        return true;
    }
    static bool BeforeUnemployedCount(WorkersPanelController __instance, int count)
    {
        try
        {
            var labels = LabelsField(__instance);
            if (labels != null && labels.TryGetValue(Employment.Unemployed, out var label))
                label.RefreshCounter(PlayerAvailable(), PlayerWorkers() + PlayerSoldiers());
            RefreshUnemployedText(__instance);
        }
        catch (Exception ex) { plugin?.Error(ex); return true; }
        return false;
    }
    static bool BeforeSquadMembers(WorkersPanelController __instance)
    {
        try
        {
            var labels = LabelsField(__instance);
            if (labels != null && labels.TryGetValue(Employment.SquadMember, out var label)) label.RefreshCounter(PlayerSoldiers(), -1);
            RefreshUnemployedText(__instance);
        }
        catch (Exception ex) { plugin?.Error(ex); return true; }
        return false;
    }
    static bool BeforeUnemployedText(WorkersPanelController __instance)
    {
        try { RefreshUnemployedText(__instance); } catch (Exception ex) { plugin?.Error(ex); }
        return false;
    }

    static bool IsRegisteredNpcOwner(ulong owner)
    {
        var a = plugin?.Adapter;
        return a != null && owner != 0 && a.Records.Any(r => (bool)CoopAdapter.Get(r, "Founded") && CoopAdapter.Id(a.Owner(r)) == owner);
    }
    // True only for characters that belong to another (NPC) faction; unknown ownership stays allowed.
    static bool ForeignWorker(Character character)
    {
        try { return character != null && IsRegisteredNpcOwner(plugin.Adapter.OwnerOf(character)); }
        catch (Exception ex) { plugin?.Error(ex); return false; }
    }
    // Hard stop: a host-side conversion must never turn an NPC faction's worker into a player soldier.
    static bool BeforeConvertForeignWorker(Character __0)
    {
        if (!ForeignWorker(__0)) return true;
        plugin.Log($"NPC worker guard: blocked converting faction worker {__0.Id} into a host soldier");
        return false;
    }
    // Make the native squad builder only see workers the local player is allowed to conscript.
    static bool BeforeSelectSquadWorkers(SquadsCreator __instance, int __0, ref Character[] __result)
    {
        try
        {
            var controller = SquadsWorkField(__instance);
            if (controller == null) return true;
            __result = controller.AvailableWorkers.Where(c => c != null && !ForeignWorker(c))
                .OrderByDescending(c => c.GetSquadCreationPriority()).Take(__0).ToArray();
            return false;
        }
        catch (Exception ex) { plugin?.Error(ex); return true; }
    }
    static void AfterGetWorkerForSquad(ref Character __result)
    {
        if (!ForeignWorker(__result)) return;
        plugin.Log($"NPC worker guard: fallback squad-worker pick refused faction worker {__result.Id}");
        __result = null;
    }

    // Missions suppressed by the multiplayer mod can emit a null mission to analytics.
    // Skip that one invalid notification; real missions retain their original behavior.
    static bool BeforeMissionAnalytics(object __0) => __0 != null;

    static object NpcRecord(Group squad)
    {
        if (squad == null || plugin?.Adapter == null) return null;
        var a = plugin.Adapter;
        var id = a.OwnerOfEntity(squad.Id);
        return a.Records.FirstOrDefault(r => (bool)CoopAdapter.Get(r, "Founded") && CoopAdapter.Id(a.Owner(r)) == id);
    }
    static Building Home(object record)
    {
        var a = plugin.Adapter;
        var id = (string)CoopAdapter.Get(record, "HqBuildingId");
        var owner = CoopAdapter.Id(a.Owner(record));
        return a.Container.Resolve<BuildingsController>().Buildings.FirstOrDefault(b =>
            b != null && b.Id == id && !b.IsRuined && a.OwnerOf(b) == owner);
    }
    static bool ForeignDepot(MoveToBuildingOrder order, object record)
    {
        var b = order.Building;
        return b != null && Policy.RejectDepot(CoopAdapter.Id(plugin.Adapter.Owner(record)),
            plugin.Adapter.OwnerOf(b), b.IsStockroom() || b.IsHq, ExchangeField(order));
    }
    static bool BeforeNearestStockroom(OrdersHandlers __instance)
    {
        var record = NpcRecord(GroupField(__instance));
        if (record == null) return true;
        var home = Home(record);
        if (home != null)
            using (plugin.Adapter.Scope(plugin.Adapter.Owner(record)))
                __instance.OrdersQueue.AddFirstOrder(new MoveToBuildingOrder(home));
        // Never fall back to the host's stockroom if this faction lost its HQ.
        return false;
    }
    static bool BeforeBuildingOrder(MoveToBuildingOrder __instance, ref Order __result)
    {
        var record = NpcRecord(__instance.Group);
        if (record == null || !ForeignDepot(__instance, record)) return true;
        // StartOrder's non-null return inserts a prerequisite and requeues the original,
        // so reject the foreign order instead of returning a replacement there.
        __result = null;
        return false;
    }
    static bool BeforeBuildingState(MoveToBuildingOrder __instance, ref OrderState __result)
    {
        var record = NpcRecord(__instance.Group);
        if (record == null || !ForeignDepot(__instance, record)) return true;
        __result = OrderState.Failed;
        return false;
    }
    internal static void AuditExistingOrders()
    {
        if (Time.realtimeSinceStartup < nextAudit) return;
        nextAudit = Time.realtimeSinceStartup + 2;
        foreach (var squad in plugin.Adapter.Container.Resolve<SquadsController>().Squads.ToArray())
        {
            if (squad == null || squad.IsDestroying) continue;
            var record = NpcRecord(squad);
            if (record == null || !(squad.OrdersHandlers.OrdersQueue.CurrentOrder is MoveToBuildingOrder order) || !ForeignDepot(order, record)) continue;
            var home = Home(record);
            if (home == null) { squad.OrdersHandlers.ClearOrders(); continue; }
            using (plugin.Adapter.Scope(plugin.Adapter.Owner(record)))
                squad.OrdersHandlers.GiveOrder(new MoveToBuildingOrder(home), clearOrders: true);
            plugin.Log($"NPC depot correction: squad {squad.Id}, foreign building {order.Building.Id} -> own HQ {home.Id}");
        }
    }
}
