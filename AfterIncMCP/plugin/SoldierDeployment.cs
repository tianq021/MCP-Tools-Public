extern alias GameAssembly;
using GameAssembly::com.ndemiccreations.survivors;
using UnityEngine;

namespace AfterIncBridge;

public sealed partial class GameBridge
{
    private sealed record SoldierDestination(int zone_id, bool explored, bool claimed);
    private sealed record SoldierOrderView(string revision, string mode, int waiting, string? unit_id,
        string? blocked_reason, List<SoldierDestination> candidates, object[] units);

    private static object FieldUnit(MovingSoldier unit) => new {
        unit_id = unit.ident, zone_id = unit.currentZone?.index, from_zone_id = unit.fromZone?.index,
        state = unit.currentState.ToString(), moving = unit.isMoving, fighting = unit.isFighting,
        turns_to_arrival = unit.isMoving ? unit.turnsToArrival : (float?)null,
        health = unit.health, max_health = unit.maxhealth
    };

    private List<MovingSoldier> FieldUnits()
    {
        var units = new List<MovingSoldier>();
        foreach (var unit in game!.global.soldiers.soldiers)
            if (unit != null && !unit.isDead) units.Add(unit);
        return units;
    }

    private SoldierOrderView SoldierOrderOptions(string? unitId)
    {
        RequireReady();
        var manager = game!.global.soldiers;
        var living = FieldUnits();
        var units = living.OrderBy(u => u.ident, StringComparer.Ordinal).Select(FieldUnit).ToArray();
        var moving = unitId != null;
        var unit = moving ? living.SingleOrDefault(u => u.ident == unitId) : null;
        var waiting = manager.numWaitingTotal;
        var hud = game.gameView.soldierDeployment;
        string? blocker = null;
        if (game.optionalScoreCard.HasValue()) blocker = "scenario_finished";
        else if (BlockingDialog(AutopauseController.SharedInstance)) blocker = "menu_or_dialog";
        else if (manager.beingDragged) blocker = "drag_in_progress";
        else if (!moving && waiting == 0) blocker = "no_waiting_soldier";
        else if (!moving && (hud == null || !hud.isActiveAndEnabled || !hud.IsAllowedTouchBegin())) blocker = "deployment_unavailable";
        else if (moving && unit == null) blocker = "unit_unavailable";
        else if (unit != null && (unit.currentZone == null || unit.originZone == null ||
            unit.tokenMoveable == null || !unit.tokenMoveable.isActiveAndEnabled || unit.tokenMoveable.blockMovement))
            blocker = "movement_unavailable";

        var candidates = new List<SoldierDestination>();
        var zones = game.zoneManager.GetAllZones();
        for (var i = 0; blocker == null && i < zones.Count; i++) {
            var zone = zones[i];
            var intel = zone?.local?.intel;
            if (zone == null || intel == null || (!intel.hasSemiIntel && !intel.hasFullIntel && !zone.isNexusZone)) continue;
            if (unit?.currentZone?.index == zone.index) continue;
            if (!manager.IsZoneValid(zone, unit?.currentZone, unit?.isMoving ?? false)) continue;
            if (unit != null) {
                var origin = unit.originZone!;
                // StartJourney uses the origin of the current trip and the game's scout pathing rules.
                var route = game.zonesPathFinder.FindPath(origin.position, origin, zone.position, zone,
                    new PathFinder.PathingConditions { isScout = true });
                if (route == null) continue;
            }
            candidates.Add(new(zone.index, intel.hasFullIntel, zone.local.claim.claimed));
        }
        candidates.Sort((a,b) => a.zone_id.CompareTo(b.zone_id));
        var revision = Hash(new { session, sequence, turn = game.simulator.gameTurn, unitId, waiting, blocker, candidates, units });
        return new(revision, moving ? "move" : "deploy", waiting, unitId, blocker, candidates, units);
    }

    private object SoldierOrders(Work work) =>
        SoldierOrderOptions(work.Args.TryGetProperty("unit_id", out var id) ? id.GetString() : null);

    private ZoneController SoldierDestinationFor(Work work, string? unitId)
    {
        RequireReady(); RequireOwner(work); RequireActiveSettlement();
        var orders = SoldierOrderOptions(unitId);
        if (work.Args.GetProperty("expected_revision").GetString() != orders.revision)
            throw new BridgeError("STALE_STATE", "Read current soldier orders again.");
        var requested = work.Args.GetProperty("zone_id").GetInt32();
        if (!orders.candidates.Any(c => c.zone_id == requested))
            throw new BridgeError("ORDER_UNAVAILABLE", orders.blocked_reason ?? "Choose a destination from current soldier orders.");
        return Zone(requested);
    }

    private object DeploySoldier(Work work)
    {
        var zone = SoldierDestinationFor(work, null);
        var manager = game!.global.soldiers;
        var position = zone.position;
        if (game.zoneManager.GetZoneAt(position)?.index != zone.index) position = zone.RandomSafeCentralPosition();
        if (game.zoneManager.GetZoneAt(position)?.index != zone.index)
            throw new BridgeError("ORDER_UNAVAILABLE", "No confirmed drop position in the selected zone.");
        var screenPosition = game.gameCamera.cam.WorldToScreenPoint(position);
        var before = manager.numWaitingTotal;
        // HudSoldierDeployment creates a trained militia; SoldierUfo drops it through TrySpawnSoldier.
        var soldier = new Soldiers.MilitiaSoldier(true);
        var accepted = manager.TrySpawnSoldier(screenPosition, soldier);
        manager.RefreshDeploymentUI();
        sequence++; handles.Clear();
        var placed = FieldUnits().FirstOrDefault(u => u.Pointer == soldier.Pointer);
        return new { submitted = true, accepted,
            deployed = accepted && placed?.currentZone?.index == zone.index && manager.numWaitingTotal == before - 1,
            requested_zone_id = zone.index, waiting_before = before, waiting_after = manager.numWaitingTotal,
            unit = placed == null ? null : FieldUnit(placed) };
    }

    private object MoveSoldier(Work work)
    {
        var id = work.Args.GetProperty("unit_id").GetString();
        if (string.IsNullOrEmpty(id)) throw new BridgeError("INVALID_ARGUMENT", "Select a unit_id from soldier orders.");
        var zone = SoldierDestinationFor(work, id);
        var unit = FieldUnits().Single(u => u.ident == id);
        var before = unit.currentZone.index;
        unit.StartJourney(zone);
        sequence++; handles.Clear();
        return new { submitted = true, accepted = unit.currentZone?.index == zone.index,
            requested_zone_id = zone.index, previous_zone_id = before, unit = FieldUnit(unit) };
    }
}
