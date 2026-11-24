using System;
using System.Collections.Generic;
using System.Text.Json;
using System.Threading.Tasks;
using Il2Cpp;
using Il2CppMonomiPark.SlimeRancher.DataModel;
using Il2CppMonomiPark.SlimeRancher.Economy;

namespace Il2Cpp
{
    public class GadgetDefinition : IdentifiableType
    {
        public string Type => "TEST";
        public string LocalizedName => name;
        public int BlueprintCost => 17;
        public FixtureCost CraftingCosts = new();
    }
    public record IdentCost(IdentifiableType IdentType, int Amount);
    public record CurrencyCost(Currency AssignedCurrency, int Amount);
    public record UpgradePart(string ReferenceId, string name);
    public class FixtureCost
    {
        public List<IdentCost> IdentCosts = new();
        public List<CurrencyCost> _currencyCosts = new();
        public List<UpgradePart> ComponentCosts = new();
    }
    public record Ingredient(string Name, int CountRequired);
    public record IngredientState(Ingredient Ingredient, bool CanShow, int CurrentCount, bool RequirementMet);
    public record TrackedRecipe(string Name) : Il2CppMonomiPark.SlimeRancher.RecipePinning.IRecipe;
    public record RecipeState(TrackedRecipe Recipe, bool RecipeRequirementsMet, IngredientState[] Rows)
    {
        public int NumberOfIngredients => Rows.Length;
        public IngredientState GetIngredientState(int index) => Rows[index];
    }
    public partial class PinnedDirector
    {
        public PlayerModel _playerModel;
        public RecipeState State;
        public RecipeState GetState() => State;
    }
}

namespace Il2CppMonomiPark.SlimeRancher.Economy
{
    public interface ICurrency { }
    public record Currency(string ReferenceId) : ICurrency
    {
        public T Cast<T>() where T : class => this as T;
    }
}

namespace Il2CppMonomiPark.SlimeRancher.DataModel
{
    public partial class GameModel
    {
        public GadgetsModel GadgetsModel = new();
        public PlayerModel PlayerModel = new();
        public PartList upgradeComponents = new();
    }
    public class GadgetsModel
    {
        public HashSet<GadgetDefinition> registeredBlueprints = new(), blueprints = new(), availBlueprints = new();
        public Dictionary<IdentifiableType, int> _itemCounts = new();
        public int GetCount(IdentifiableType item) => _itemCounts.GetValueOrDefault(item);
    }
    public class PartList { public List<UpgradePart> items = new(); }
    public class ComponentModel
    {
        public Dictionary<UpgradePart, int> _ownedComponents = new();
        public int GetComponentQuantity(UpgradePart c) => _ownedComponents.GetValueOrDefault(c);
    }
    public class PlayerModel
    {
        public UpgradeFixture upgradeModel;
        public ComponentModel UpgradeComponentsModel = new();
        public TrackedRecipe PinnedRecipe;
        public int GetCurrencyAmount(ICurrency c) => 50;
    }
}

internal static partial class Program
{
    static async Task TestFabrication(GameModel world, Func<string, object, Task<JsonElement>> query)
    {
        var gadgets = world.GadgetsModel;
        var gadget = new GadgetDefinition { ReferenceId = "TEST_GADGET", name = "GadgetTest" };
        var ore = new IdentifiableType { ReferenceId = "ORE" };
        var part = new UpgradePart("PART", "Part");
        gadgets.registeredBlueprints.Add(gadget);
        gadgets.blueprints.Add(gadget);
        gadgets.availBlueprints.Add(new GadgetDefinition { ReferenceId = "OTHER" });
        gadgets._itemCounts[gadget] = 2;
        gadgets._itemCounts[ore] = 6;
        world.upgradeComponents.items.Add(part);
        world.PlayerModel.UpgradeComponentsModel._ownedComponents[part] = 1;
        var cost = gadget.CraftingCosts;
        cost.IdentCosts.Add(new(ore, 1));
        cost.IdentCosts.Add(new(ore, 3));
        cost._currencyCosts.Add(new(new("COINS"), 20));
        cost.ComponentCosts.Add(part);
        var blueprints = await query("sr2_blueprints", new { owned_only = true });
        Check("blueprints deduplicate sources and filter ownership", blueprints.GetProperty("record_count").GetInt32() == 2
            && blueprints.GetProperty("items").GetArrayLength() == 1 && blueprints.GetProperty("items")[0].GetProperty("id").GetString() == "TEST_GADGET");
        var stock = await query("sr2_gadget_stock", new { });
        Check("gadget stock excludes crafting materials", stock.GetProperty("items").GetArrayLength() == 1
            && stock.GetProperty("items")[0].GetProperty("in_stock").GetInt32() == 2);
        var parts = await query("sr2_upgrade_components", new { owned_only = true });
        Check("component quantities", parts.GetProperty("items")[0].GetProperty("count").GetInt32() == 1);
        var recipe = await query("sr2_gadget_recipe", new { gadget_id = "TEST_GADGET", count = 2 });
        var costs = recipe.GetProperty("costs");
        Check("batch scales all costs and combines duplicate materials", !recipe.GetProperty("costs_covered").GetBoolean()
            && costs.GetArrayLength() == 3 && costs[0].GetProperty("missing").GetInt32() == 1
            && costs[1].GetProperty("missing").GetInt32() == 0 && costs[2].GetProperty("required").GetInt32() == 8
            && costs[2].GetProperty("missing").GetInt32() == 2 && gadgets._itemCounts[ore] == 6);
        var single = await query("sr2_gadget_recipe", new { gadget_id = "TEST_GADGET" });
        Check("single craft costs are covered", single.GetProperty("costs_covered").GetBoolean());
        gadgets._itemCounts = null;
        var unknown = await query("sr2_gadget_recipe", new { gadget_id = "TEST_GADGET" });
        Check("unknown stock yields unknown coverage", unknown.GetProperty("costs_covered").ValueKind == JsonValueKind.Null);
        var director = SceneContext.Instance.PinnedRecipeDirector;
        director._playerModel = world.PlayerModel;
        Check("no pinned recipe", !(await query("sr2_pinned_recipe", new { })).GetProperty("pinned").GetBoolean());
        director._playerModel.PinnedRecipe = new("Device");
        director.State = new(director._playerModel.PinnedRecipe, false, new IngredientState[] {
            new(new("Known", 4), true, 3, false), new(new("Hidden", 2), false, 1, false) });
        var pinned = await query("sr2_pinned_recipe", new { });
        var ingredients = pinned.GetProperty("ingredients");
        Check("pinned recipe follows HUD visibility and count", ingredients[0].GetProperty("current").GetInt32() == 3
            && ingredients[1].GetProperty("title").ValueKind == JsonValueKind.Null && !pinned.GetProperty("requirements_met").GetBoolean());
    }
}
