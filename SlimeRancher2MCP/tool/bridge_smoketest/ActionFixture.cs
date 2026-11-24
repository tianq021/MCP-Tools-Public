using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using Il2Cpp;
using Il2CppMonomiPark.SlimeRancher.DataModel;
using Il2CppMonomiPark.SlimeRancher.RecipePinning;
using Il2CppMonomiPark.SlimeRancher.UI.Fabricator;

namespace UnityEngine
{
    public static class Resources
    {
        public static object[] Assets = Array.Empty<object>();
        public static T[] FindObjectsOfTypeAll<T>() => Assets.OfType<T>().ToArray();
    }
}

namespace Il2Cpp
{
    public class GadgetDirector { public GadgetsModel _model => SceneContext.Instance.GameModel.GadgetsModel; }
    public partial class PinnedDirector : IPinnedRecipeManager
    {
        public RecipeFactory RecipeFactory = new();
        public int Changes;
        public T Cast<T>() where T : class => this as T;
        public IRecipe GetPinnedRecipe() => _playerModel.PinnedRecipe;
        public bool IsPinned(IRecipe recipe) => Equals(recipe, GetPinnedRecipe());
        public void SetPinnedRecipe(IRecipe recipe) { _playerModel.PinnedRecipe = (TrackedRecipe)recipe; Changes++; }
        public void RemovePinnedRecipe() { _playerModel.PinnedRecipe = null; Changes++; }
    }
    public partial class SlimeFeeder : UnityEngine.MonoBehaviour
    {
        public enum FeedSpeed { NORMAL, SLOW, FAST }
        public LandPlotModel _model;
        public int Changes;
        FeedSpeed speed = FeedSpeed.NORMAL;
        public FeedSpeed GetFeedingCycleSpeed() => speed;
        public void SetFeederSpeed(FeedSpeed value) { speed = value; Changes++; }
    }
}

namespace Il2CppMonomiPark.SlimeRancher.RecipePinning
{
    public interface IRecipe { string Name { get; } }
    public interface IRecipeFactory { }
    public class RecipeFactory : IRecipeFactory
    {
        public T Cast<T>() where T : class => this as T;
        public IRecipe RecipeForGadget(GadgetDefinition gadget) => new TrackedRecipe(gadget.ReferenceId);
    }
    public interface IPinnedRecipeManager
    {
        IRecipe GetPinnedRecipe();
        bool IsPinned(IRecipe recipe);
        void SetPinnedRecipe(IRecipe recipe);
        void RemovePinnedRecipe();
    }
}

namespace Il2CppMonomiPark.SlimeRancher.UI.Fabricator
{
    public interface IFabricatorGadgetHelper { }
    public class GadgetDirectorHelper : IFabricatorGadgetHelper
    {
        public GadgetDirectorHelper(GadgetDirector director) { }
        public T Cast<T>() where T : class => this as T;
    }
    public enum FabricationBlockedReason { NONE, INSUFFICIENT_COST_ITEMS, OPERATION_FAILED }
    public interface IFabricatableItem
    {
        T TryCast<T>() where T : class;
        FabricationBlockedReason CheckCanFabricate(int count);
        FabricationBlockedReason FabricateAndSpendCost(int count);
    }
    public class GadgetFabricatableItem : IFabricatableItem
    {
        public GadgetDefinition Definition;
        public bool FailOnSpend;
        public int Calls;
        GadgetsModel model => SceneContext.Instance.GameModel.GadgetsModel;
        public T TryCast<T>() where T : class => this as T;
        public FabricationBlockedReason CheckCanFabricate(int count) =>
            Definition.CraftingCosts.IdentCosts.All(c => model.GetCount(c.IdentType) >= count * c.Amount)
                ? FabricationBlockedReason.NONE : FabricationBlockedReason.INSUFFICIENT_COST_ITEMS;
        public FabricationBlockedReason FabricateAndSpendCost(int count)
        {
            Calls++;
            if (FailOnSpend) return FabricationBlockedReason.OPERATION_FAILED;
            foreach (var c in Definition.CraftingCosts.IdentCosts) model._itemCounts[c.IdentType] -= count * c.Amount;
            model._itemCounts[Definition] = model.GetCount(Definition) + count;
            return FabricationBlockedReason.NONE;
        }
    }
    public class GadgetFabricateCategory
    {
        public object _gadgetSubGroup = new();
        public List<IFabricatableItem> Items = new();
        public bool Contains(GadgetDefinition g) => Items.OfType<GadgetFabricatableItem>().Any(i => i.Definition == g);
        public List<IFabricatableItem> GetItems(IFabricatorGadgetHelper helper, IRecipeFactory factory) => Items;
    }
}

internal static partial class Program
{
    static async Task TestActions(GameModel game, Func<string, object, string, Task<JsonElement>> write)
    {
        async Task<JsonElement> Data(string tool, object args, string id = null) =>
            (await write(tool, args, id)).GetProperty("data");
        const string craft = "sr2_craft_gadget", pin = "sr2_set_recipe_pin", speed = "sr2_set_feeder_speed";
        var model = game.GadgetsModel = new();
        var gadget = new GadgetDefinition { ReferenceId = "Pump" };
        var ore = new IdentifiableType { ReferenceId = "Ore" };
        gadget.CraftingCosts.IdentCosts.Add(new(ore, 3));
        model._itemCounts[ore] = 9;
        model.registeredBlueprints.Add(gadget);
        var item = new GadgetFabricatableItem { Definition = gadget };
        var category = new GadgetFabricateCategory();
        category.Items.Add(item);
        var batch = new { gadget_id = "Pump", count = 2, dry_run = false };
        Check("craft rejects unowned blueprint", (await Data(craft, batch, "unowned")).GetProperty("reason").GetString() == "missing_blueprint");
        model.blueprints.Add(gadget);
        Check("craft needs loaded fabrication category", (await Data(craft, batch, "no-category")).GetProperty("reason").GetString() == "fabricator_item_unavailable");
        UnityEngine.Resources.Assets = new object[] { category };
        var plan = await Data(craft, new { gadget_id = "Pump", count = 2 });
        Check("craft preview reports full cost without spending", plan.GetProperty("can_craft").GetBoolean()
            && plan.GetProperty("dry_run").GetBoolean() && plan.GetProperty("costs_before")[0].GetProperty("required").GetInt32() == 6
            && model.GetCount(ore) == 9 && item.Calls == 0);
        Check("craft requires request id", (await write(craft, batch, null)).GetProperty("error").GetProperty("code").GetString() == "REQUEST_ID_REQUIRED");
        Check("craft rejects fractional batch", (await Data(craft, new { gadget_id = "Pump", count = 1.5, dry_run = false }, "bad-count"))
            .GetProperty("reason").GetString() == "invalid_count" && item.Calls == 0);
        var made = await Data(craft, batch, "craft-two");
        Check("craft reads back output and materials", made.GetProperty("applied").GetBoolean() && made.GetProperty("stock_delta").GetInt32() == 2
            && made.GetProperty("costs_after")[0].GetProperty("stock").GetInt32() == 3 && model.GetCount(gadget) == 2);
        Check("craft replay spends once", (await write(craft, batch, "craft-two")).GetProperty("cached").GetBoolean() && item.Calls == 1 && model.GetCount(ore) == 3);
        Check("native eligibility blocks unaffordable batch", (await Data(craft, batch, "short")).GetProperty("reason").GetString() == "INSUFFICIENT_COST_ITEMS"
            && item.Calls == 1);
        item.FailOnSpend = true;
        var failed = await Data(craft, new { gadget_id = "Pump", dry_run = false }, "failed-spend");
        Check("native craft failure reports unchanged stock", !failed.GetProperty("applied").GetBoolean() && failed.GetProperty("stock_delta").GetInt32() == 0
            && failed.GetProperty("reason").GetString() == "OPERATION_FAILED");

        var director = SceneContext.Instance.PinnedRecipeDirector;
        director._playerModel = game.PlayerModel;
        director._playerModel.PinnedRecipe = new("Old");
        var target = new { action = "pin", gadget_id = "Pump", dry_run = false };
        var pinPlan = await Data(pin, new { action = "pin", gadget_id = "Pump" });
        Check("pin preview preserves old recipe", pinPlan.GetProperty("title_before").GetString() == "Old" && director.Changes == 0);
        var pinned = await Data(pin, target, "pin");
        Check("pin replaces current recipe", pinned.GetProperty("title_after").GetString() == "Pump" && pinned.GetProperty("applied").GetBoolean());
        Check("pin same recipe avoids rewrite", (await Data(pin, target, "pin-again")).GetProperty("already_set").GetBoolean() && director.Changes == 1);
        Check("clear rejects target argument", (await Data(pin, new { action = "clear", gadget_id = "Pump", dry_run = false }, "bad-clear"))
            .GetProperty("reason").GetString() == "unexpected_gadget_id" && director.Changes == 1);
        var clear = new { action = "clear", dry_run = false };
        Check("clear unpins current recipe", !(await Data(pin, clear, "clear")).GetProperty("pinned_after").GetBoolean() && director.Changes == 2);
        Check("clearing empty pin skips write", (await Data(pin, clear, "clear-again")).GetProperty("already_set").GetBoolean() && director.Changes == 2);

        var pen = game.landPlots["pen"] = new(LandPlot.Id.CORRAL, null, null, null, null);
        var other = game.landPlots["other-pen"] = new(LandPlot.Id.CORRAL, null, null, null, null);
        var feeder = new SlimeFeeder { _model = pen };
        var neighbor = new SlimeFeeder { _model = other };
        UnityEngine.Object.Active = new UnityEngine.Component[] { neighbor };
        var fast = new { plot_id = "pen", speed = "fast", dry_run = false };
        Check("unloaded feeder does not target neighbor", (await Data(speed, fast, "no-feeder")).GetProperty("reason").GetString() == "loaded_feeder_required" && neighbor.Changes == 0);
        UnityEngine.Object.Active = new UnityEngine.Component[] { neighbor, feeder };
        var speedPlan = await Data(speed, new { plot_id = "pen", speed = "fast" });
        Check("feeder preview reads current speed", speedPlan.GetProperty("speed_before").GetString() == "NORMAL" && feeder.Changes == 0);
        var fed = await Data(speed, fast, "speed");
        Check("feeder changes only selected plot", fed.GetProperty("speed_after").GetString() == "FAST" && fed.GetProperty("applied").GetBoolean() && neighbor.Changes == 0);
        Check("same speed preserves feeding timer", (await Data(speed, fast, "speed-again")).GetProperty("already_set").GetBoolean() && feeder.Changes == 1);
        Check("invalid speed does not change timer", (await Data(speed, new { plot_id = "pen", speed = "1", dry_run = false }, "bad-speed"))
            .GetProperty("reason").GetString() == "invalid_speed" && feeder.Changes == 1);
    }
}
