extern alias GameAssembly;
using GameAssembly::com.ndemiccreations.survivors;

namespace AfterIncBridge;

public sealed partial class GameBridge
{
    // Read-only dump of every live Tech record with the numbers that actually drive the settlement,
    // so building effects can be inspected without hovering each row in the game UI.
    private object TechCatalog()
    {
        RequireReady();
        var list = new List<object>();
        foreach (var t in game!.global.techManager.all) {
            if (t == null) continue;
            var locked = t.IsConceptLocked();
            var classifications = new List<string>();
            if (t.classifications != null)
                for (var i = 0; i < t.classifications.Count; i++) {
                    var page = t.classifications[i];
                    if (page != null && page.ident != null) classifications.Add(page.ident);
                }
            var recipes = new List<string>();
            if (t.recipes != null)
                for (var i = 0; i < t.recipes.Length; i++) {
                    var recipe = t.recipes[i];
                    if (recipe.HasValue() && recipe.value?.ident != null) recipes.Add(recipe.value.ident);
                }
            list.Add(new {
                ident = t.ident, name = Text(t.GetConceptTitle()), description = Text(t.GetConceptDescription()),
                classifications,
                research = new { required = t.required_research_level, max = t.max_research_level,
                    requires_research_level_increase = t.research_level_increase },
                applied = t.is_applied, times_applied = t.timesApplied, repeatable = t.repeatable,
                locked_reason = locked.HasValue() ? Text(locked.value.longDescription) : null,
                building = t.building_ident,
                costs = new { wood = t.wood_cost, manpower = t.manpower_cost, metal = t.metal_cost, custom = CostItems(t.custom_cost_type) },
                effects = new { housing = t.housing, morale_change = t.morale_change, morale_income = t.morale_income,
                    morale_mod = t.morale_mod, max_morale_change = t.max_morale_change, authority_change = t.authority_change,
                    power_level = t.power_level, turns_to_construct = t.turns_to_construct,
                    resource = t.resource_type.ToString(), resource_production = t.resource_type_production,
                    resource_total = t.resource_type_total, true_max_change = t.true_max_change,
                    military_strength = t.military_strength, military_capacity = t.military_capacity },
                recipes,
                requires = new { population = t.required_settlement_size,
                    techs = t.required_initiatives_and_techs == null ? Array.Empty<string>() :
                        t.required_initiatives_and_techs.Where(x => x != null).Select(x => x.ident).ToArray(),
                    any = t.required_initiatives_or_techs == null ? Array.Empty<string>() :
                        t.required_initiatives_or_techs.Where(x => x != null).Select(x => x.ident).ToArray() }
            });
        }
        return new { session_id = session, turn = game.simulator.gameTurn, count = list.Count, techs = list };
    }
}
