extern alias GameAssembly;
using GameAssembly::com.ndemiccreations.survivors;

namespace AfterIncBridge;

public sealed partial class GameBridge
{
    private sealed record OutpostSite(int zone_id, bool claimed, bool established, bool valid_site,
        string? blocked_reason, string[] applied_upgrades, string[] pending_upgrades);

    private List<OutpostSite> OutpostSites()
    {
        var sites = new List<OutpostSite>();
        var zones = game!.zoneManager.GetAllZones();
        for (var i = 0; i < zones.Count; i++) {
            var zone = zones[i];
            var local = zone?.local;
            if (zone == null || local?.intel == null || !(local.intel.hasFullIntel || zone.isNexusZone)) continue;
            var post = local.outpost;
            if (post == null) continue;
            bool built = post.IsOutposted();
            bool valid = !built && post.IsValidOutpostZone();
            string? reason = valid ? null : built ? "already_built" : zone.isNexusZone ? "headquarters" :
                !local.claim.claimed ? "unclaimed" : post.invalidOutpostZone ? "scenario_restriction" :
                local.enemies.GetVisualEnemyCount(true) > 0 ? "enemies_present" : "game_disallows_placement";
            var upgrades = new List<string>();
            if (built) foreach (var upgrade in post.appliedUpgrades)
                if (upgrade != null) upgrades.Add(upgrade.GetConceptIdent());
            var pending = new List<string>();
            if (built && post.pending != null) foreach (var id in post.pending) pending.Add(id);
            sites.Add(new OutpostSite(zone.index, local.claim.claimed, built, valid, reason, upgrades.ToArray(), pending.ToArray()));
        }
        return sites;
    }

    private string? OutpostDeploymentBlocker()
    {
        if (!game!.global.outposts.HasAvailable()) return "no_outpost_ready";
        if (game.optionalScoreCard.HasValue()) return "settlement_finished";
        if (BlockingDialog(AutopauseController.SharedInstance)) return "menu_or_dialog";
        var hud = game.gameView?.outpostDeployment;
        return hud != null && hud.isActiveAndEnabled && hud.IsAllowedTouchBegin() ? null : "deployment_unavailable";
    }

    private string OutpostRevision(List<OutpostSite> sites, string? blocker) =>
        Hash(new { state = Revision(), available = game!.global.outposts.numAvailable, blocker, sites });

    // Tech definitions show prerequisites even before the shop has an offer.
    // CalculateRows supplies current affordability, including discounts and repeat-purchase costs.
    private List<object> OutpostTechnology()
    {
        var result = new List<object>();
        var global = game!.global;
        var screen = game.gameView?.settlementScreen;
        foreach (var tech in global.techManager.all) {
            if (tech?.ident == null || !tech.ident.StartsWith("outpost_", StringComparison.Ordinal)) continue;
            var categories = new List<string>();
            var offers = new List<object>();
            if (tech.classifications != null) foreach (var category in tech.classifications) {
                if (category == null || categories.Contains(category.ident)) continue;
                categories.Add(category.ident);
                if (screen?.actionController == null || screen.overviewPanel?.rowTemplate == null) continue;
                var rows = screen.actionController.CalculateRows(category, null, screen.overviewPanel.rowTemplate, null);
                foreach (var row in rows) {
                    var item = row.concept?.TryCast<Tech>();
                    if (item?.Pointer != tech.Pointer || row.isDevolving) continue;
                    var locked = item.IsConceptLocked();
                    var affordable = row.commodities?.CanAfford() ?? true;
                    offers.Add(new { category = category.ident, affordable,
                        available = row.onActivation != null && !locked.HasValue() && affordable,
                        costs = CostItems(row.commodities), in_construction = row.constructionCount });
                }
            }
            var reason = tech.IsConceptLocked();
            result.Add(new { id = tech.ident, name = Text(tech.name), description = Text(tech.GetConceptDescription()),
                required_research = tech.required_research_level, required_population = tech.required_settlement_size,
                base_construction_turns = tech.turns_to_construct,
                base_costs = new { manpower = tech.manpower_cost, wood = tech.wood_cost, metal = tech.metal_cost,
                    extra = CostItems(tech.custom_cost_type) },
                requires_all = tech.required_initiatives_and_techs?.Where(x => x != null).Select(x => x.ident).ToArray(),
                requires_any = tech.required_initiatives_or_techs?.Where(x => x != null).Select(x => x.ident).ToArray(),
                locked_reason = reason.HasValue() ? Text(reason.value.longDescription) : null, applied = tech.is_applied,
                constructed = global.techManager.IsAppliedAndConstructed(tech.ident), categories, offers });
        }
        return result;
    }

    private object Outposts(Work work)
    {
        RequireReady();
        var sites = OutpostSites();
        var blocker = OutpostDeploymentBlocker();
        var manager = game!.global.outposts;
        var statistics = game.global.statisticTypes?.lookup;
        var quota = statistics != null && statistics.TryGetValue("statOutposts", out var stock) && stock != null
            ? new { statistic = stock.ident, available = stock.available,
                available_without_incomplete = stock.availableWithoutIncomplete } : null;
        return new { session_id = session, revision = OutpostRevision(sites, blocker), turn = game.simulator.gameTurn,
            available = manager.numAvailable, established = manager.GetNumOutposts(false), deployment_blocker = blocker,
            purchase_quota = quota,
            research_level = game.global.settlement.settlementResearchLevel, population = game.global.settlement.settlementSize,
            acquisition = OutpostTechnology(), sites };
    }

    private object BuildOutpost(Work work)
    {
        RequireReady(); RequireOwner(work); RequireActiveSettlement();
        var sites = OutpostSites();
        var blocker = OutpostDeploymentBlocker();
        if (work.Args.GetProperty("expected_revision").GetString() != OutpostRevision(sites, blocker))
            throw new BridgeError("STALE_STATE", "Read get_outposts again before placing an outpost.");
        if (blocker != null) throw new BridgeError("OUTPOST_UNAVAILABLE", blocker);
        int id = work.Args.GetProperty("zone_id").GetInt32();
        var site = sites.Find(s => s.zone_id == id);
        if (site == null || !site.valid_site)
            throw new BridgeError("OUTPOST_SITE_UNAVAILABLE", site?.blocked_reason ?? "unexplored_or_unknown_zone");
        var manager = game!.global.outposts;
        var post = Zone(id).local.outpost;
        int before = manager.numAvailable;
        // OutpostUfo.TouchEnd builds the verified target, then consumes exactly one deployable.
        // TryPlaceOutpost handles only the build portion of that native drop.
        if (!post.BuildOutpost()) throw new BridgeError("OUTPOST_SITE_UNAVAILABLE", "The game declined this placement.");
        manager.DecreaseNumAvailable();
        sequence++; handles.Clear();
        return new { applied = post.IsOutposted(), zone_id = id, available_before = before,
            available_after = manager.numAvailable, established = manager.GetNumOutposts(false) };
    }
}
