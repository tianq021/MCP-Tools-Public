extern alias GameAssembly;
using GameAssembly::com.ndemiccreations.survivors;

namespace AfterIncBridge;

public sealed partial class GameBridge
{
    private IEnumerable<(ZoneController zone, IZoneActionProposal proposal)> SettlementProposals()
    {
        var manager = game!.global.zoneActions;
        var actions = new Il2CppSystem.Collections.Generic.List<ZoneAction>();
        foreach (var a in manager.all)
            if (a.TryCast<WagonSettleZoneAction>() != null) actions.Add(a);
        var wagon = game.global.settlement.nexusWagon;
        if (wagon == null || wagon.isSettled || actions.Count == 0) yield break;
        var zones = game.zoneManager.GetAllZones();
        for (var i = 0; i < zones.Count; i++) {
            var z = zones[i];
            if (z.local == null || !(z.local.intel.hasSemiIntel || z.local.intel.hasFullIntel || z.isNexusZone)) continue;
            if (!wagon.IsZoneValid(z)) continue;
            var proposals = manager.GetProposals(game, z, actions.Cast<Il2CppSystem.Collections.Generic.IList<ZoneAction>>());
            foreach (var p in proposals) yield return (z,p);
        }
    }

    private object SettlementSites()
    {
        RequireReady();
        var wagon = game!.global.settlement.nexusWagon;
        var candidates = SettlementProposals().Select(s => new { zone_id = s.zone.index,
            terrain = s.zone.local.intel.hasFullIntel ? s.zone.info.terrainType.ToString() : null,
            resource = s.zone.local.intel.hasFullIntel ? s.zone.info.resourceType.ToString() : null,
            action = Describe(s.proposal) }).ToArray();
        return new { revision = Revision(), has_wagon = wagon != null, settled = wagon?.isSettled,
            wagon_zone_id = wagon?.currentZone?.index, nexus_zone_id = game.zoneManager.nexusZone?.index, candidates };
    }

    private object SettleZone(Work work)
    {
        RequireReady(); RequireOwner(work); RequirePlayableMap();
        var id = work.Args.GetProperty("zone_id").GetInt32();
        if (Revision() != work.Args.GetProperty("expected_revision").GetString())
            throw new BridgeError("STALE_STATE", "Read settlement sites again.");
        var options = SettlementProposals().Where(s => s.zone.index == id).ToList();
        if (options.Count != 1) throw new BridgeError("SITE_UNAVAILABLE", "Choose a listed available settlement site.");
        var p = options[0].proposal;
        if (p.usable.HasValue() || (p.commodities != null && !p.commodities.CanAfford()))
            throw new BridgeError("SITE_UNAVAILABLE", "The game rejected this site.");
        var result = game!.global.zoneActions.PerformAction(p);
        handles.Clear(); sequence++;
        if (result != null && result.TryGetError(out var error)) return new { applied = false, game_error = Text(error.GetEnglishText()) };
        var wagon = game.global.settlement.nexusWagon;
        return new { submitted = true, settled = wagon?.isSettled, actual_zone_id = wagon?.currentZone?.index, requested_zone_id = id };
    }
}
