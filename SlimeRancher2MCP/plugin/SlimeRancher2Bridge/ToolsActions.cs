using System;
using System.Collections.Generic;
using Il2Cpp;
using Il2CppMonomiPark.SlimeRancher.DataModel;
using Il2CppMonomiPark.SlimeRancher.RecipePinning;
using Il2CppMonomiPark.SlimeRancher.UI.Fabricator;
using UnityEngine;
using Row = System.Collections.Generic.Dictionary<string, object>;

namespace SlimeRancher2Bridge
{
    internal static class ToolsActions
    {
        internal static void Register(BridgeServer server)
        {
            void Add(string name, Func<SceneContext, Row, bool, Row> action) =>
                server.Register(name, args => ToolsNavigation.WithScene(raw =>
                {
                    var scene = (SceneContext)raw;
                    if (scene.GameModel?.isInitialized != true) return Blocked("game_model_unavailable");
                    if (args.GetValueOrDefault("dry_run", true) is not bool preview) return Blocked("invalid_dry_run");
                    var result = action(scene, args, preview);
                    result["dry_run"] = preview;
                    return result;
                }), mainThread: true, write: true);
            Add("sr2_craft_gadget", Craft);
            Add("sr2_set_recipe_pin", Pin);
            Add("sr2_set_feeder_speed", FeederSpeed);
            Add("sr2_restore_health", (scene, args, preview) => ToolsRescue.Restore(scene, args, preview, health: true));
            Add("sr2_restore_energy", (scene, args, preview) => ToolsRescue.Restore(scene, args, preview, health: false));
            Add("sr2_select_vacpack_slot", ToolsRescue.SelectSlot);
            Add("sr2_mark_return_point", ToolsRescue.Mark);
            Add("sr2_return_to_mark", ToolsRescue.Return);
            server.Register("sr2_ranch_controls", args => ToolsRanch.WithGame(game => RanchControls.List(game, args)), mainThread: true);
            Add("sr2_dispense_feeder_food", (scene, args, preview) => RanchControls.Use(scene, args, preview, "feeder"));
            Add("sr2_activate_plort_collector", (scene, args, preview) => RanchControls.Use(scene, args, preview, "plort_collector"));
            Add("sr2_activate_resource_collector", (scene, args, preview) => RanchControls.Use(scene, args, preview, "resource_collector"));
            Add("sr2_cycle_silo_port", (scene, args, preview) => RanchControls.Use(scene, args, preview, "silo_port"));
        }

        static Row Blocked(string reason) => new() { ["applied"] = false, ["reason"] = reason };
        static string Text(Row args, string key) => (args.GetValueOrDefault(key) as string)?.Trim() ?? "";

        static string ResolveOwned(GameModel game, string id, out GadgetDefinition gadget)
        {
            gadget = null;
            var model = game.GadgetsModel;
            var catalog = ToolsFabrication.Catalog(model);
            if (catalog == null) return "blueprint_catalog_unavailable";
            if (!catalog.TryGetValue(id, out gadget)) return "unknown_gadget_id";
            return model.blueprints.Contains(gadget) ? null : "missing_blueprint";
        }

        static IFabricatableItem FabricatorItem(SceneContext scene, GadgetDefinition gadget)
        {
            var director = scene.GadgetDirector;
            var factory = scene.PinnedRecipeDirector?.RecipeFactory;
            if (director?._model == null || factory == null) return null;
            foreach (var category in Resources.FindObjectsOfTypeAll<GadgetFabricateCategory>())
            {
                if (category?._gadgetSubGroup == null || !category.Contains(gadget)) continue;
                var helper = new GadgetDirectorHelper(director).Cast<IFabricatorGadgetHelper>();
                var items = category.GetItems(helper, factory.Cast<IRecipeFactory>());
                if (items == null) continue;
                foreach (var item in items)
                    if (item?.TryCast<GadgetFabricatableItem>()?.Definition?.ReferenceId == gadget.ReferenceId)
                        return item;
            }
            return null;
        }

        static Row Craft(SceneContext scene, Row args, bool preview)
        {
            if (args.GetValueOrDefault("count", 1L) is not long count || count < 1 || count > 99)
                return Blocked("invalid_count");
            var game = scene.GameModel;
            string reason = ResolveOwned(game, Text(args, "gadget_id"), out var gadget);
            if (reason != null) return Blocked(reason);
            var model = game.GadgetsModel;
            if (model._itemCounts == null) return Blocked("gadget_stock_unavailable");
            int batch = (int)count;
            var recipe = ToolsFabrication.Recipe(game, gadget, batch);
            if (recipe["available"] is false) return Blocked((string)recipe["reason"]);
            int before = model.GetCount(gadget);
            var result = new Row { ["gadget_id"] = gadget.ReferenceId, ["count"] = batch, ["applied"] = false,
                ["stock_before"] = before, ["costs_before"] = recipe["costs"], ["can_craft"] = false };
            var item = FabricatorItem(scene, gadget);
            if (item == null) { result["reason"] = "fabricator_item_unavailable"; return result; }
            var check = item.CheckCanFabricate(batch);
            if (check != FabricationBlockedReason.NONE) { result["reason"] = check.ToString(); return result; }
            result["can_craft"] = true;
            if (preview) return result;

            var outcome = item.FabricateAndSpendCost(batch);
            int after = model.GetCount(gadget);
            result["native_result"] = outcome.ToString();
            result["stock_after"] = after;
            result["stock_delta"] = after - before;
            result["costs_after"] = ToolsFabrication.Recipe(game, gadget, batch).GetValueOrDefault("costs");
            if (outcome != FabricationBlockedReason.NONE) result["reason"] = outcome.ToString();
            else if (after - before < batch) result["reason"] = "inventory_change_unconfirmed";
            else result["applied"] = true;
            return result;
        }

        static Row Pin(SceneContext scene, Row args, bool preview)
        {
            string action = Text(args, "action").ToLowerInvariant(), id = Text(args, "gadget_id");
            if (action != "pin" && action != "clear") return Blocked("invalid_action");
            if (action == "pin" && id.Length == 0) return Blocked("gadget_id_required");
            if (action == "clear" && id.Length != 0) return Blocked("unexpected_gadget_id");
            var director = scene.PinnedRecipeDirector;
            if (director?._playerModel == null) return Blocked("pinned_recipe_unavailable");
            var manager = director.Cast<IPinnedRecipeManager>();
            IRecipe target = null;
            if (action == "pin")
            {
                string reason = ResolveOwned(scene.GameModel, id, out var gadget);
                if (reason != null) return Blocked(reason);
                target = director.RecipeFactory?.RecipeForGadget(gadget);
                if (target == null) return Blocked("recipe_unavailable");
            }
            var before = manager.GetPinnedRecipe();
            bool matches = target == null ? before == null : manager.IsPinned(target);
            var result = new Row { ["action"] = action, ["gadget_id"] = target == null ? null : id,
                ["pinned_before"] = before != null, ["title_before"] = Il2CppUtil.Localized(before?.Name),
                ["already_set"] = matches, ["applied"] = false };
            if (preview || matches) return result;
            if (target == null) manager.RemovePinnedRecipe();
            else manager.SetPinnedRecipe(target);
            var after = manager.GetPinnedRecipe();
            bool confirmed = target == null ? after == null : after != null && manager.IsPinned(target);
            result["pinned_after"] = after != null;
            result["title_after"] = Il2CppUtil.Localized(after?.Name);
            result["applied"] = confirmed;
            if (!confirmed) result["reason"] = "pin_change_unconfirmed";
            return result;
        }

        static Row FeederSpeed(SceneContext scene, Row args, bool preview)
        {
            string id = Text(args, "plot_id");
            SlimeFeeder.FeedSpeed speed;
            switch (Text(args, "speed").ToLowerInvariant())
            {
                case "slow": speed = SlimeFeeder.FeedSpeed.SLOW; break;
                case "normal": speed = SlimeFeeder.FeedSpeed.NORMAL; break;
                case "fast": speed = SlimeFeeder.FeedSpeed.FAST; break;
                default: return Blocked("invalid_speed");
            }
            var plots = scene.GameModel.landPlots;
            if (plots == null) return Blocked("plots_unavailable");
            if (!plots.TryGetValue(id, out var plot) || plot == null) return Blocked("unknown_plot_id");
            if (plot.typeId != LandPlot.Id.CORRAL) return Blocked("not_a_corral");
            SlimeFeeder feeder = null;
            foreach (var candidate in UnityEngine.Object.FindObjectsByType<SlimeFeeder>(FindObjectsSortMode.None))
            {
                if (candidate?._model?.Pointer != plot.Pointer || !candidate.isActiveAndEnabled) continue;
                if (feeder != null) return Blocked("ambiguous_feeder");
                feeder = candidate;
            }
            if (feeder == null) return Blocked("loaded_feeder_required");
            var before = feeder.GetFeedingCycleSpeed();
            var result = new Row { ["plot_id"] = id, ["speed_before"] = before.ToString(), ["requested_speed"] = speed.ToString(),
                ["already_set"] = before == speed, ["applied"] = false };
            if (preview || before == speed) return result;
            feeder.SetFeederSpeed(speed);
            var after = feeder.GetFeedingCycleSpeed();
            result["speed_after"] = after.ToString();
            result["applied"] = after == speed;
            if (after != speed) result["reason"] = "speed_change_unconfirmed";
            return result;
        }
    }
}
