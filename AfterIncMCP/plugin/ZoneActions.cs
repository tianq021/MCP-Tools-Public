extern alias GameAssembly;
using GameAssembly::com.ndemiccreations.survivors;

namespace AfterIncBridge;

public sealed partial class GameBridge
{
    // The map's zone tokens are 3D world objects, so the UI-button bridge cannot click them.
    // These commands call the same ZoneIntel / ZoneClaimManager entry points the tokens use.
    private object ScoutZone(Work work)
    {
        RequireReady(); RequireOwner(work); RequirePlayableMap();
        var zone = Zone(work.Args.GetProperty("zone_id").GetInt32());
        var local = zone.local;
        var intel = local?.intel;
        if (intel == null) throw new BridgeError("ZONE_UNAVAILABLE", "This zone has no intel state yet.");
        if (intel.hasFullIntel) throw new BridgeError("ALREADY_EXPLORED", "This zone is already fully explored.");
        if (intel.isBeingScouted) throw new BridgeError("ALREADY_SCOUTING", "This zone is already being scouted.");
        if (!intel.CanBeScouted) throw new BridgeError("NOT_SCOUTABLE", "The game does not allow scouting this zone right now.");
        sequence++; handles.Clear();
        intel.ExploreZone();
        return new { submitted = true, zone_id = zone.index, next = "get_map",
            after = new { has_semi_intel = zone.local.intel.hasSemiIntel, has_full_intel = zone.local.intel.hasFullIntel,
                is_being_scouted = zone.local.intel.isBeingScouted } };
    }

    private object ClaimZone(Work work)
    {
        RequireReady(); RequireOwner(work); RequirePlayableMap();
        var zone = Zone(work.Args.GetProperty("zone_id").GetInt32());
        var local = zone.local;
        var claim = local?.claim;
        if (claim == null) throw new BridgeError("ZONE_UNAVAILABLE", "This zone has no claim state yet.");
        if (claim.claimed) throw new BridgeError("ALREADY_CLAIMED", "This zone is already claimed.");
        var blocked = claim.CanClaim();
        if (blocked.HasValue()) throw new BridgeError("NOT_CLAIMABLE", Text(blocked.value.longDescription));
        var cost = claim.GetClaimCost();
        sequence++; handles.Clear();
        claim.ClaimZone(false, false, true, false);
        return new { submitted = true, zone_id = zone.index, claim_cost = cost, claimed = zone.local.claim.claimed, next = "get_map" };
    }
    // The settlement (town) screen is opened by clicking the town in 3D; expose it to the bridge.
    private object OpenSettlement(Work work)
    {
        RequireReady(); RequireOwner(work); RequirePlayableMap();
        var screen = game.gameView?.settlementScreen;
        if (screen == null) throw new BridgeError("SETTLEMENT_UI_UNAVAILABLE", "Settlement screen is not ready yet.");
        sequence++; handles.Clear();
        screen.Open();
        return new { submitted = true, opened = true, next = "get_game_controls" };
    }

    private object CloseSettlement(Work work)
    {
        RequireReady(); RequireOwner(work); RequirePlayableMap();
        var screen = game.gameView?.settlementScreen;
        if (screen == null) throw new BridgeError("SETTLEMENT_UI_UNAVAILABLE", "Settlement screen is not ready yet.");
        screen.Close();
        return new { submitted = true, closed = true, next = "get_game_controls" };
    }
}
