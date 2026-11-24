extern alias GameAssembly;
using GameAssembly::com.ndemiccreations.survivors;

namespace AfterIncBridge;

public sealed partial class GameBridge
{
    private object ZoneDetails(Work work)
    {
        RequireReady();
        var zone = Zone(work.Args.GetProperty("zone_id").GetInt32());
        var local = zone.local;
        if (local?.intel == null || !(local.intel.hasFullIntel || zone.isNexusZone))
            throw new BridgeError("ZONE_UNEXPLORED", "Explore this zone before inspecting its improvements.");
        var dev = local.localDevSim;
        var buildings = new List<object>();
        var seenBuildings = new HashSet<IntPtr>();
        var resources = new Dictionary<string, ZoneOutput>();
        foreach (var building in local.buildings.localBuildings) {
            if (building == null || !seenBuildings.Add(building.Pointer)) continue;
            var player = building.TryCast<PlayerBuilding>();
            var utility = building.utility.HasValue() ? building.utility.value : null;
            buildings.Add(new { id = building.typeIdent, name = Text(building.name), level = player?.level,
                upgrades = building.upgrades, paused = building.isPaused, blocked = building.isBlocked,
                game_production_report = utility?.debugYieldString, production = Outputs(utility, resources) });
        }
        var wagon = game!.global.settlement.nexusWagon;
        var wagonProduction = wagon != null && wagon.isSettled && wagon.currentZone?.index == zone.index ?
            Outputs(wagon.utility, resources) : null;
        var construction = new List<object>();
        var seenJobs = new HashSet<IntPtr>();
        void ReadJob(ZoneConstruction job)
        {
            if (job == null || job.zone?.index != zone.index || job.hasBeenCompleted || !seenJobs.Add(job.Pointer)) return;
            construction.Add(new { id = job.GetConceptIdent(), name = Text(job.GetConceptTitle()), category = job.category?.ToString(),
                progress = job.currentProgress, amount_built = job.amountBuilt, total_work = job.maxAmountToBuild, paused = job.wasPaused });
        }
        var localQueue = local.buildings.techBuildActionManager?.buildActions;
        if (localQueue != null) foreach (var job in localQueue) ReadJob(job);
        var queues = game.global.unifiedConstructionZoneJobManager?.buildActions;
        if (queues != null) foreach (var pair in queues)
            foreach (var job in pair.Value) ReadJob(job);
        return new { session_id = session, turn = game.simulator.gameTurn, zone_id = zone.index,
            terrain = zone.info.terrainType.ToString(), resource = zone.info.resourceType.ToString(),
            claimed = local.claim.claimed, nexus = zone.isNexusZone, high_yield = dev.IsHighYield,
            improvements = new { swamp_walkway = dev.hasSwampWalkway, scarecrow = dev.hasScarecrow,
                fire_torches = dev.hasFireTorches, smoke_torches = dev.hasSmokeTorches,
                fortifications = dev.fortifications, pending_fortifications = dev.fortificationsPending,
                road_connected = local.roadConnectedToNexus, road_value = local.roadValue },
            markers = VisibleMapMarkers().Where(m => m.zone_id == zone.index),
            buildings, construction, wagon_production = wagonProduction, production_by_resource = resources,
            building_actions = ListActions(work.Client, zone.index, true) };
    }

    private sealed class ZoneOutput
    {
        public float stored { get; set; }
        public float active_potential { get; set; }
    }

    private List<object>? Outputs(ProductionUtility? utility, Dictionary<string, ZoneOutput> totals)
    {
        if (utility?.yields == null) return null;
        var outputs = new List<object>();
        foreach (var y in utility.yields) {
            var recipe = y?.recipe;
            if (y == null || recipe?.output == null) continue;
            var product = recipe.output;
            var id = product.resource.ToString();
            var consumption = recipe.is_consumption || recipe.is_all_consumption || recipe.is_basic_consumption || recipe.is_amenity_consumption;
            var active = y.IsActive() && !utility.IsPaused() && !utility.IsBlocked();
            var potential = y.CalculatePotential(game!, true, false);
            outputs.Add(new { resource = id, name = Text(product.name), recipe = recipe.ident, active, consumption_recipe = consumption,
                stored = y.yieldBucket, estimated_potential = potential, status = y.yieldStatus?.current.ToString() });
            if (consumption) continue;
            if (!totals.TryGetValue(id, out var total)) totals[id] = total = new ZoneOutput();
            total.stored += y.yieldBucket;
            if (active) total.active_potential += potential;
        }
        return outputs;
    }

    private static List<object> CostItems(Commodities? costs)
    {
        var items = new List<object>();
        if (costs?.commodities == null) return items;
        foreach (var c in costs.commodities) {
            if (c?.production == null) continue;
            items.Add(new { resource = c.production.resource.ToString(), name = Text(c.production.name),
                cost = c.GetCommodityCost(), owned = c.production.GetTotal(), soft = c.isSoft });
        }
        return items;
    }

    private static bool Training(Tech t) => t.is_soldier_training || t.train_militia > 0 || t.train_soldier > 0 || t.train_bowman > 0;

    private object Military()
    {
        RequireReady();
        var manager = game!.global.soldiers;
        var units = new List<object>();
        foreach (var u in manager.soldiers) {
            if (u == null || u.isDead) continue;
            units.Add(new { id = u.ident, type = u.soldierType.ToString(), zone_id = u.currentZone?.index,
                state = u.currentState.ToString(), size = u.squadSize, health = u.health, max_health = u.maxhealth,
                experience = u.exp, is_fighting = u.isFighting, is_moving = u.isMoving,
                kills_this_fight = u.numkilledThisFight, time_in_combat = u.timeInCombat });
        }
        var catalog = new List<object>();
        foreach (var t in game.global.techManager.all) {
            if (t == null || !Training(t)) continue;
            var locked = t.IsConceptLocked();
            catalog.Add(new { id = t.ident, name = Text(t.name), trains_militia = t.train_militia,
                trains_soldiers = t.train_soldier, trains_bowmen = t.train_bowman, required_research = t.required_research_level,
                required_population = t.required_settlement_size,
                requires_all = t.required_initiatives_and_techs?.Where(x => x != null).Select(x => x.ident).ToArray(),
                requires_any = t.required_initiatives_or_techs?.Where(x => x != null).Select(x => x.ident).ToArray(),
                locked_reason = locked.HasValue() ? Text(locked.value.longDescription) : null });
        }
        return new { turn = game.simulator.gameTurn, capacity = manager.sharedMaxSoldierCapacity,
            zombies_killed = manager.numZombiesKilled, soldiers_lost = manager.numSoldiersKilled,
            training_catalog = catalog,
            training = manager.numTrainingTotal, waiting = manager.numWaitingTotal, units,
            recruitment = DevelopmentOffers(true) };
    }

    private object Technology()
    {
        RequireReady();
        var global = game!.global;
        var applied = new List<object>();
        foreach (var tech in global.techManager.all) {
            if (tech == null || !tech.is_applied) continue;
            applied.Add(new { id = tech.ident, name = Text(tech.name), times_applied = tech.timesApplied,
                constructed = global.techManager.IsAppliedAndConstructed(tech.ident) });
        }
        return new { turn = game.simulator.gameTurn, research_level = global.settlement.settlementResearchLevel,
            pending_research_level = global.settlement.settlementResearchLevelPending,
            knowledge = global.knowledge.amount, needed_for_card = global.knowledge.knowledgeRequiredForNewCard,
            can_choose_card = global.knowledge.canChooseNewCard, applied, offers = DevelopmentOffers(false) };
    }

    // Use the game's own settlement shop rows; the tech database includes entries not offered this run.
    private List<object> DevelopmentOffers(bool recruitmentOnly)
    {
        var screen = game!.gameView.settlementScreen;
        var controller = screen?.actionController;
        if (controller == null || screen!.overviewPanel?.rowTemplate == null)
            throw new BridgeError("SHOP_NOT_READY", "The settlement shop has not finished initialising.");
        var categories = controller.GetDisplayClassifications();
        var seen = new HashSet<string>();
        var groups = new Dictionary<string, DisplayClassification>();
        var result = new List<object>();
        var count = categories.Cast<Il2CppSystem.Collections.Generic.ICollection<DisplayClassification>>().Count;
        for (var i = 0; i < count; i++) {
            var category = categories[i];
            if (category == null) continue;
            groups[category.ident] = category;
        }
        // Research and training also appear in class-specific panels outside the resource tabs.
        foreach (var tech in game!.global.techManager.all) {
            if (tech == null || (!Training(tech) && !tech.research_level_increase) || tech.classifications == null) continue;
            foreach (var group in tech.classifications)
                if (group != null) groups.TryAdd(group.ident, group);
        }
        foreach (var category in groups.Values) {
            var rows = controller.CalculateRows(category, null, screen.overviewPanel.rowTemplate, null);
            foreach (var row in rows) {
                var tech = row.concept?.TryCast<Tech>();
                if (tech == null || row.isDevolving || (recruitmentOnly && !Training(tech)) || !seen.Add(tech.ident)) continue;
                var reason = tech.IsConceptLocked();
                var affordable = row.commodities?.CanAfford() ?? true;
                result.Add(new { id = tech.ident, name = Text(tech.name), description = Text(tech.GetConceptDescription()),
                    category = category.ident, recruitment = Training(tech), militia = tech.train_militia,
                    soldiers = tech.train_soldier, bowmen = tech.train_bowman, required_research = tech.required_research_level,
                    in_construction = row.constructionCount, available = row.onActivation != null && !reason.HasValue() && affordable,
                    locked_reason = reason.HasValue() ? Text(reason.value.longDescription) : affordable ? null : "资源不足",
                    costs = CostItems(row.commodities) });
            }
        }
        return result;
    }
}
