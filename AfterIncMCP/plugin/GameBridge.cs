extern alias GameAssembly;
extern alias FoundationAssembly;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using FoundationAssembly::com.ndemiccreations.foundation;
using GameAssembly::com.ndemiccreations.survivors;
using UnityEngine;
using UnityEngine.SceneManagement;


namespace AfterIncBridge;

public sealed partial class GameBridge
{
    
    private GameController? game;
    private string session = Guid.NewGuid().ToString("N");
    private IntPtr gamePointer;
    private string? owner;
    private string ownerName = "";
    private DateTime leaseUntil;
    private long sequence;
    private readonly Dictionary<string, ActionHandle> handles = new();
    private Work? advancing;
    private int targetTurn, startTurn, lastMarkerTurn;
    private bool stopOnMarker;
    private DateTime advanceDeadline;
    
    private sealed record ActionHandle(string Client, string Session, int Zone, string Ident, string Signature, string Revision, DateTime Created);

    private static void Trace(string message) { try { Plugin.LogSource?.LogInfo("[bridge] " + message); } catch (Exception) { } }

    private static float PauseMultiplier(Simulator simulator)
    {
        try { return simulator.pauseMultiplier; } catch (Exception) { return 1f; }
    }

    public void Tick(IntPtr currentInstance)
    {
        // Stay completely off the Unity/IL2CPP API while idle. Calling interop every frame
        // (FindObjectOfType during scene loads) coincided with game crashes on settlement load.
        var idle = owner == null && advancing == null && Broker.Queue.IsEmpty;
        if (idle) return;
        try {
            // Reuse the wrapper while the game keeps handing us the same GameController instance.
            // Allocating one every frame burned GC and multiplied interop calls at 60+ fps.
            if (game == null || currentInstance == IntPtr.Zero || game.Pointer != currentInstance)
                game = currentInstance == IntPtr.Zero ? null : new GameController(currentInstance);
            RefreshGame();
            if (owner != null && DateTime.UtcNow >= leaseUntil) {
                if (Ready()) SetBridgeSpeed(Simulator.Speed.Paused);
                FinishAdvance("lease_expired"); owner = null; handles.Clear(); sequence++;
            }
            if (advancing != null) {
                if (game == null) FinishAdvance("scene_changed");
                else if (stopOnMarker && game.simulator.gameTurn != lastMarkerTurn && CheckAdvanceMarkers()) FinishAdvance("map_marker");
                else if (game.simulator.gameTurn >= targetTurn) FinishAdvance("target_reached");
                else if (RealDialog(AutopauseController.SharedInstance)) FinishAdvance("paused_by_game");
                else if (PauseMultiplier(game.simulator) <= 0f) FinishAdvance("auto_paused");
                else if (DateTime.UtcNow >= advanceDeadline) FinishAdvance("time_limit");
            }
            for (var i = 0; i < 8 && Broker.Queue.TryDequeue(out var work); i++) {
                if (Interlocked.CompareExchange(ref work.Status, 1, 0) != 0) continue;
                Trace("dispatch " + work.Command);
                try {
                    var result = Dispatch(work);
                    if (result != null) work.Done.TrySetResult(Api.Ok(result));
                    Trace("done " + work.Command);
                }
                catch (BridgeError e) { work.Done.TrySetResult(Api.Fail(e.Code, e.Message)); Trace("rejected " + work.Command + ": " + e.Code); }
                catch (Exception e) { work.Done.TrySetResult(Api.Fail("GAME_ERROR", e.Message, Broker.Mutations.Contains(work.Command) ? null : false)); Trace("failed " + work.Command + ": " + e.Message); }
            }
        } catch (Exception e) {
            if (advancing != null) { advancing.Done.TrySetResult(Api.Fail("GAME_ERROR", e.Message, null)); advancing = null; }
        }
    }

    private void RefreshGame()
    {
        
        var pointer = IntPtr.Zero;
        if (game != null) { try { pointer = game.Pointer; } catch (Exception) { game = null; pointer = IntPtr.Zero; } }
        if (pointer == gamePointer) return;
        FinishAdvance("scene_changed");
        gamePointer = pointer; session = Guid.NewGuid().ToString("N"); handles.Clear(); sequence++;
    }

    private object? Dispatch(Work work)
    {
        var a = work.Args;
        switch (work.Command) {
            case "status": return new { version = "0.6.8", protocol = 1, scene = SceneManager.GetActiveScene().name, ready = Ready(), unity = Application.unityVersion, session_id = session, control = Control(), focused = Application.isFocused, autopause = AutoState(), capabilities = new[] { "get_state", "get_map", "get_map_markers", "activate_map_marker", "get_zone_details", "get_military", "get_soldier_orders", "deploy_soldier", "move_soldier", "get_outposts", "build_outpost", "get_settlement_purchases", "purchase_settlement", "get_technology", "get_tech_catalog", "list_actions", "execute_action", "scout_zone", "claim_zone", "open_settlement", "close_settlement", "open_morale", "buy_native", "set_speed", "advance", "get_campaign_map", "open_campaign_map", "select_campaign_region", "enter_campaign_level", "choose_campaign_option", "get_objectives", "get_game_controls", "activate_game_control", "get_settlement_sites", "settle_zone" } };
            case "get_settlement_sites": return SettlementSites();
            case "settle_zone": return SettleZone(work);
            case "get_objectives": return Objectives();
            case "get_game_controls": return ReadControls().view;
            case "activate_game_control": return ClickControl(work);
            case "get_campaign_map": return CampaignMap();
            case "open_campaign_map":
            case "select_campaign_region":
            case "enter_campaign_level":
            case "choose_campaign_option":
                return NavigateCampaign(work);
            case "get_state": return State();
            case "get_map_markers": return ReadMapMarkers();
            case "activate_map_marker": return ActivateMapMarker(work);
            case "get_zone_details": return ZoneDetails(work);
            case "get_military": return Military();
            case "get_soldier_orders": return SoldierOrders(work);
            case "deploy_soldier": return DeploySoldier(work);
            case "move_soldier": return MoveSoldier(work);
            case "get_outposts": return Outposts(work);
            case "build_outpost": return BuildOutpost(work);
            case "get_settlement_purchases": return GetSettlementPurchases(work);
            case "purchase_settlement": return PurchaseSettlement(work);
            case "get_technology": return Technology();
            case "get_tech_catalog": return TechCatalog();
            case "get_map": RequireReady(); return Map();
            case "acquire_control":
                if (owner != null && owner != work.Client) throw new BridgeError("CONTROL_BUSY", "Another client holds control: " + ownerName);
                var ttl = a.TryGetProperty("ttl_seconds", out var t) ? t.GetInt32() : 120;
                if (ttl is < 15 or > 300) throw new BridgeError("INVALID_ARGUMENT", "ttl_seconds must be 15..300.");
                owner = work.Client; ownerName = a.TryGetProperty("label", out var l) ? (l.GetString() ?? "MCP client") : "MCP client";
                if (ownerName.Length > 80) ownerName = ownerName[..80];
                leaseUntil = DateTime.UtcNow.AddSeconds(ttl);
                return Control();
            case "release_control":
                RequireOwner(work); FinishAdvance("control_released");
                if (Ready() && (!a.TryGetProperty("pause", out var onRelease) || onRelease.GetBoolean())) SetBridgeSpeed(Simulator.Speed.Paused);
                else ReleaseBridgePauses();
                owner = null; handles.Clear(); sequence++; return Control();
            case "set_speed":
                RequireReady(); RequireOwner(work);
                var speedText = a.GetProperty("speed").GetString();
                if (speedText is not ("Paused" or "Normal" or "Faster" or "Fastest")) throw new BridgeError("INVALID_ARGUMENT", "Unknown speed.");
                FinishAdvance("speed_changed");
                SetBridgeSpeed(Enum.Parse<Simulator.Speed>(speedText)); sequence++; handles.Clear();
                return State();
            case "list_actions": RequireReady(); return ListActions(work.Client, a.GetProperty("zone_id").GetInt32(), a.TryGetProperty("kind", out var kind) && kind.GetString() == "building");
            case "execute_action": RequireReady(); RequireOwner(work); return Execute(work);
            case "scout_zone": return ScoutZone(work);
            case "open_settlement": return OpenSettlement(work);
            case "open_morale": return OpenMorale(work);
            case "buy_native": return BuyNative(work);
            case "close_settlement": return CloseSettlement(work);
            case "claim_zone": return ClaimZone(work);
            case "debug_pause": return DebugPause();
            case "unpause": throw new BridgeError("UNSAFE_OPERATION_DISABLED", "Use the game UI to resolve automatic pause.");
            case "advance":
                RequireReady(); RequireOwner(work); RequirePaused();
                if (advancing != null) throw new BridgeError("BUSY", "A bounded advance is already active.");
                var turns = a.GetProperty("turns").GetInt32();
                if (turns is < 1 or > 20) throw new BridgeError("INVALID_ARGUMENT", "turns must be 1..20.");
                startTurn = game!.simulator.gameTurn; targetTurn = startTurn + turns;
                stopOnMarker = a.TryGetProperty("stop_on_marker", out var markerFlag) && markerFlag.GetBoolean();
                if (stopOnMarker && CheckAdvanceMarkers())
                    return new { reason = "map_marker", start_turn = startTurn, target_turn = targetTurn, state = State() };
                advanceDeadline = DateTime.UtcNow.AddSeconds(20); advancing = work; handles.Clear(); sequence++;
                Trace("advance start turn=" + startTurn + " target=" + targetTurn + " pause_multiplier=" + PauseMultiplier(game.simulator));
                // Resume requested speed before observing automatic pause on the next frame.
                SetBridgeSpeed(Simulator.Speed.Normal); return null;
            case "step_turn":
                RequireReady(); RequireOwner(work);
                FinishAdvance("step_turn");
                var stepSim = game!.simulator;
                var stepBefore = stepSim.gameTurn;
                SetBridgeSpeed(Simulator.Speed.Paused);
                stepSim.AdvanceGameTurn();
                sequence++; handles.Clear();
                Trace("step_turn " + stepBefore + " -> " + stepSim.gameTurn);
                return new { before_turn = stepBefore, after_turn = stepSim.gameTurn, speed = stepSim.GetGameSpeed().ToString(), state = State() };
            default: throw new BridgeError("UNKNOWN_COMMAND", work.Command);
        }
    }

    private bool Ready() => game != null && game.hasSetup && game.global != null && game.simulator != null;
    private static readonly string[] BridgePauseKeys = {
        "incmcp", "incmcp-lease-expired", "incmcp-release", "incmcp-step", "incmcp-advance-finished"
    };

    // The game counts "speed pause" by named retain. Keeping the retain after a paused step leaves
    // pauseMultiplier at 0 forever, which looks like a stuck game. Release every name this bridge owns.
    private void ReleaseBridgePauses()
    {
        var counter = AutopauseController.SharedInstance?.IsSpeedPaused;
        if (counter == null) return;
        foreach (var key in BridgePauseKeys) {
            try {
                if (counter.IsNamedRetainRetained(key)) { counter.Release(key); Trace("released speed pause " + key); }
            } catch (Exception e) { Trace("release pause " + key + ": " + e.Message); }
        }
    }

    private void SetBridgeSpeed(Simulator.Speed speed)
    {
        // The game retains pauses by caller name. Every bridge pause and resume must share one owner.
        var sim = game!.simulator;
        var counter = AutopauseController.SharedInstance?.IsSpeedPaused;
        if (speed == Simulator.Speed.Paused) {
            if (sim.GetGameSpeed() == speed && counter?.IsNamedRetainRetained("incmcp") == true) return;
        } else ReleaseBridgePauses();
        sim.SetSpeed(speed, "incmcp");
    }

    private void RequireReady() { if (!Ready()) throw new BridgeError("NOT_IN_GAME", "Open a settlement first; the game is at a menu or still loading."); }
    private void RequireOwner(Work work) { if (owner != work.Client || DateTime.UtcNow >= leaseUntil) throw new BridgeError("CONTROL_REQUIRED", "Acquire control before changing the game."); }
    private void RequireActiveSettlement()
    {
        if (game!.optionalScoreCard.HasValue()) throw new BridgeError("SCENARIO_FINISHED", "This settlement has reached its outcome.");
        if (advancing != null) throw new BridgeError("BUSY", "Wait for the current bounded advance to finish.");
    }

    private void RequirePlayableMap()
    {
        RequireActiveSettlement();
        if (BlockingDialog(AutopauseController.SharedInstance))
            throw new BridgeError("MENU_OR_DIALOG", "Resolve the open game menu or dialog first.");
    }

    private void RequirePaused() { if (game!.simulator.GetGameSpeed() != Simulator.Speed.Paused) throw new BridgeError("PAUSE_REQUIRED", "Pause, read fresh actions, then execute."); }
    private object Control() => new { held = owner != null, owner = owner == null ? null : ownerName, expires_at = owner != null ? leaseUntil.ToString("O") : null };

    private static AutopauseController? Autopause()
    {
        if (!Plugin.ProbeAutopause) return null;
        try { return AutopauseController.SharedInstance; } catch (Exception) { return null; }
    }

    private static int PauseCount(object? value)
    {
        if (value is bool flag) return flag ? 1 : 0;
        if (value is Counter counter) {
            try { return counter.Count(); } catch (Exception) { return 0; }
        }
        return 0;
    }

    private static bool BlockingDialog(AutopauseController? autopause)
    {
        if (autopause == null) return false;
        try {
            return PauseCount(autopause.IsDialogShown) > 0 || PauseCount(autopause.IsMenuShown) > 0;
        } catch (Exception) { return false; }
    }

    // A real decision dialog must stop a bounded advance; leftover menu/forced flags must not.
    private static bool RealDialog(AutopauseController? autopause)
    {
        if (autopause == null) return false;
        try { return PauseCount(autopause.IsDialogShown) > 0; } catch (Exception) { return false; }
    }

    private static object? AutoState()
    {
        var autopause = Autopause();
        if (autopause == null) return null;
        try {
            return new {
                dialog = PauseCount(autopause.IsDialogShown),
                menu = PauseCount(autopause.IsMenuShown),
                speed_paused = PauseCount(autopause.IsSpeedPaused),
                ability = PauseCount(autopause.IsActiveAbility),
                forced = PauseCount(autopause.ForcedPause),
            };
        } catch (Exception e) { return new { error = e.Message }; }
    }

    private object DebugPause()
    {
        var result = new Dictionary<string, object?>
        {
            ["focused"] = Application.isFocused,
            ["run_in_background"] = Application.runInBackground,
            ["time_scale"] = Time.timeScale,
            ["autopause"] = AutoState(),
            ["owner"] = owner,
            ["advancing"] = advancing != null,
        };
        var sim = game?.simulator;
        if (sim != null) {
            result["speed"] = sim.GetGameSpeed().ToString();
            {
                try { result["pause_multiplier"] = sim.pauseMultiplier; } catch (Exception e) { result["pause_multiplier_error"] = e.Message; }
                try { result["paused_seconds"] = sim.paused.TotalSeconds; } catch (Exception e) { result["paused_error"] = e.Message; }
            }
        }
        if (Ready()) {
            var ap = AutopauseController.SharedInstance;
            if (ap != null) {
                var causes = new Dictionary<string, object>();
                foreach (var entry in new (string,Counter)[] { ("speed",ap.IsSpeedPaused),("menu",ap.IsMenuShown),("dialog",ap.IsDialogShown),("ability",ap.IsActiveAbility),("forced",ap.ForcedPause),("limited",ap.LimitedInteraction) }) {
                    var c = entry.Item2;
                    if (c == null || c.Count() == 0) continue;
                    var names = new List<string>();
                    if (c.namedRetains != null) foreach (var pair in c.namedRetains.Sorted(false,false)) names.Add(pair.Key);
                    causes[entry.Item1] = new { count = c.Count(), reasons = names };
                }
                result["pause_causes"] = causes;
            }
        }
        return result;
    }
    private static string Text(EnglishText? text) => text == null ? "" : text.ToLocalisedString();
    private static string Hash(object obj) => Convert.ToHexString(SHA256.HashData(JsonSerializer.SerializeToUtf8Bytes(obj, Api.JsonOptions)))[..24].ToLowerInvariant();

    private List<object> Resources()
    {
        var result = new List<object>();
        var all = game!.global.production.all;
        for (var i = 0; i < all.Count; i++) {
            var p = all[i];
            if (!p.IsActive() || !p.hasUI) continue;
            result.Add(new { id = p.resource.ToString(), name = Text(p.name), total = p.GetTotal(), capacity = p.trueMax,
                produced = p.production?.actual, consumed = p.consumption?.actual, deficit = p.deficit });
        }
        return result;
    }

    private string Revision() => Hash(new { session, sequence, turn = game!.simulator.gameTurn, speed = game.simulator.GetGameSpeed().ToString(), resources = Resources(), population = game.global.settlement.settlementSize });
    private object State()
    {
        if (!Ready()) return new { ready = false, session_id = session, scene = SceneManager.GetActiveScene().name, control = Control() };
        var s = game!.global.settlement;
        return new { ready = true, session_id = session, revision = Revision(), scene = SceneManager.GetActiveScene().name,
            turn = game.simulator.gameTurn, speed = game.simulator.GetGameSpeed().ToString(), population = s.settlementSize,
            authority = s.Authority, morale = s.moraleProd.GetTotal(), resources = Resources(), control = Control(),
            nexus_placed = game.zoneManager.isNexusPlaced, wagon_settled = game.global.settlement.nexusWagon?.isSettled, wagon_zone_id = game.global.settlement.nexusWagon?.currentZone?.index, advance_pending = advancing != null };
    }

    private ZoneController Zone(int index)
    {
        var zones = game!.zoneManager.GetAllZones();
        for (var i = 0; i < zones.Count; i++) if (zones[i].index == index) return zones[i];
        throw new BridgeError("ZONE_NOT_FOUND", "Unknown zone_id.");
    }

    private object Map()
    {
        var list = new List<object>();
        var zones = game!.zoneManager.GetAllZones();
        for (var i = 0; i < zones.Count; i++) {
            var z = zones[i]; var local = z.local;
            if (local == null || (!local.intel.hasSemiIntel && !local.intel.hasFullIntel && !z.isNexusZone)) continue;
            var full = local.intel.hasFullIntel;
            var neighbours = new List<int>();
            for (var j = 0; j < z.neighbours.Length; j++) {
                var n = z.neighbours[j].zone;
                if (n != null && n.local != null && (n.local.intel.hasSemiIntel || n.local.intel.hasFullIntel)) neighbours.Add(n.index);
            }
            list.Add(new { zone_id = z.index, name = z.ident, explored = full, scoutable = local.intel.CanBeScouted,
                claimed = local.claim.claimed, nexus = z.isNexusZone, terrain = full ? z.info.terrainType.ToString() : null,
                resource = full ? z.info.resourceType.ToString() : null,
                visible_active_enemies = full && local.enemies != null ? (int?)local.enemies.GetVisualEnemyCount(false) : null,
                visible_enemies = full && local.enemies != null ? (int?)local.enemies.GetVisualEnemyCount(true) : null, neighbours });
        }
        return new { session_id = session, revision = Revision(), visibility = "player_visible", zones = list, markers = VisibleMapMarkers() };
    }

    private List<IZoneActionProposal> Proposals(ZoneController zone)
    {
        var actions = game!.global.zoneActions;
        var list = actions.GetProposals(game, zone, actions.all.Cast<Il2CppSystem.Collections.Generic.IList<ZoneAction>>());
        var result = new List<IZoneActionProposal>();
        for (var i = 0; i < list.Count; i++) result.Add(list[i]);
        return result;
    }

    private object Describe(IZoneActionProposal p) => new { ident = p.GetIdent(), title = Text(p.title), description = Text(p.description),
        available = !p.usable.HasValue() && (p.commodities == null || p.commodities.CanAfford()),
        blocked_reason = p.usable.HasValue() ? Text(p.usable.value.longDescription) : p.commodities != null && !p.commodities.CanAfford() ? "资源不足" : "",
        kind = p.GetAction().TryCast<PlayerBuildingZoneAction>() != null ? "building" : p.GetAction().GetActionType().ToString(),
        cost_items = CostItems(p.commodities), costs = p.commodities?.GetLocalisedDescription(true, false, true), consumes_resources = p.consumeCommodities };

    private object ListActions(string client, int zoneId, bool buildingsOnly = false)
    {
        var zone = Zone(zoneId);
        if (!zone.local.intel.hasSemiIntel && !zone.local.intel.hasFullIntel) throw new BridgeError("ZONE_HIDDEN", "This zone is not visible to the player.");
        foreach (var id in handles.Where(p => DateTime.UtcNow - p.Value.Created > TimeSpan.FromSeconds(60)).Select(p => p.Key).ToArray()) handles.Remove(id);
        if (handles.Count > 2000) handles.Clear();
        var revision = Revision(); var result = new List<object>();
        foreach (var p in Proposals(zone)) {
            if (buildingsOnly && p.GetAction().TryCast<PlayerBuildingZoneAction>() == null) continue;
            var detail = Describe(p); var id = Guid.NewGuid().ToString("N");
            handles[id] = new ActionHandle(client, session, zoneId, p.GetIdent(), Hash(detail), revision, DateTime.UtcNow);
            result.Add(new { action_id = id, details = detail });
        }
        return new { session_id = session, revision, zone_id = zoneId, actions = result, expires_in_seconds = 60 };
    }

    private object Execute(Work work)
    {
        RequirePlayableMap();
        var id = work.Args.GetProperty("action_id").GetString() ?? "";
        var revision = work.Args.GetProperty("expected_revision").GetString();
        if (!handles.TryGetValue(id, out var h) || h.Client != work.Client || h.Session != session || DateTime.UtcNow - h.Created > TimeSpan.FromSeconds(60))
            throw new BridgeError("STALE_ACTION", "Read a fresh action list from this MCP client.");
        if (revision != h.Revision || Revision() != h.Revision) throw new BridgeError("STALE_STATE", "The game changed. Read a fresh action list.");
        var matches = Proposals(Zone(h.Zone)).Where(p => p.GetIdent() == h.Ident && Hash(Describe(p)) == h.Signature).ToList();
        if (matches.Count != 1) throw new BridgeError("STALE_ACTION", "The action changed or is ambiguous. Read a fresh list.");
        var proposal = matches[0];
        if (proposal.usable.HasValue() || (proposal.commodities != null && !proposal.commodities.CanAfford())) throw new BridgeError("ACTION_UNAVAILABLE", "The game does not allow this action.");
        var before = State(); handles.Clear(); sequence++;
        var error = game!.global.zoneActions.PerformAction(proposal);
        if (error != null && error.TryGetError(out var actual))
            return new { applied = false, game_error = Text(actual.GetEnglishText()), before, after = State() };
        return new { applied = true, action = h.Ident, zone_id = h.Zone, before, after = State() };
    }

    private bool CheckAdvanceMarkers()
    {
        lastMarkerTurn = game!.simulator.gameTurn;
        return VisibleMapMarkers().Count > 0;
    }

    private void FinishAdvance(string reason)
    {
        if (advancing == null) return;
        var work = advancing; advancing = null;
        if (game != null) SetBridgeSpeed(Simulator.Speed.Paused);
        sequence++;
        Trace("advance finished " + reason + " turn=" + (game?.simulator.gameTurn.ToString() ?? "n/a"));
        work.Done.TrySetResult(Api.Ok(new { reason, start_turn = startTurn, target_turn = targetTurn, state = State() }));
    }
}






