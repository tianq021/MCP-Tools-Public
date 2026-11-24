using System;
using System.Collections.Generic;
using System.Linq;
using Il2Cpp;
using Il2CppMonomiPark.SlimeRancher.DataModel;
using Il2CppMonomiPark.SlimeRancher.Economy;
using Row = System.Collections.Generic.Dictionary<string, object>;

namespace SlimeRancher2Bridge
{
    internal static class ToolsFabrication
    {
        internal static void Register(BridgeServer server)
        {
            server.Register("sr2_blueprints", args => ToolsRanch.WithGame(game => Blueprints(game, args)), mainThread: true);
            server.Register("sr2_gadget_stock", args => ToolsRanch.WithGame(game => Stock(game, args)), mainThread: true);
            server.Register("sr2_gadget_recipe", args => ToolsRanch.WithGame(game => Recipe(game, args)), mainThread: true);
            server.Register("sr2_upgrade_components", args => ToolsRanch.WithGame(game => Components(game, args)), mainThread: true);
            server.Register("sr2_pinned_recipe", Pinned, mainThread: true);
        }

        static Row Unavailable(string reason) => new() { ["available"] = false, ["reason"] = reason };
        static string Query(Row args) => args.TryGetValue("query", out var q) ? Convert.ToString(q).Trim() : "";
        static bool Matches(string text, string query) => (text ?? "").Contains(query, StringComparison.OrdinalIgnoreCase);

        internal static SortedDictionary<string, GadgetDefinition> Catalog(GadgetsModel model)
        {
            if (model?.registeredBlueprints == null || model.blueprints == null || model.availBlueprints == null) return null;
            var rows = new SortedDictionary<string, GadgetDefinition>(StringComparer.Ordinal);
            foreach (var set in new[] { model.registeredBlueprints, model.availBlueprints, model.blueprints })
                foreach (var def in set)
                    if (def != null && !string.IsNullOrEmpty(def.ReferenceId)) rows[def.ReferenceId] = def;
            return rows;
        }

        static Row Blueprints(GameModel game, Row args)
        {
            var model = game.GadgetsModel;
            var catalog = Catalog(model);
            if (catalog == null) return Unavailable("blueprint_catalog_unavailable");
            string query = Query(args);
            bool owned = args.TryGetValue("owned_only", out var flag) && flag is true;
            var matches = catalog.Values.Where(d => (!owned || model.blueprints.Contains(d))
                && (Matches(d.ReferenceId, query) || Matches(d.name, query))).ToList();
            return ToolsWorld.Page(args, catalog.Count, matches, d => new Row
            {
                ["id"] = d.ReferenceId, ["name"] = d.name, ["title"] = Il2CppUtil.Localized(d.LocalizedName),
                ["type"] = d.Type.ToString(), ["owned"] = model.blueprints.Contains(d),
                ["in_available_set"] = model.availBlueprints.Contains(d), ["blueprint_cost"] = d.BlueprintCost
            });
        }

        static Row Stock(GameModel game, Row args)
        {
            var model = game.GadgetsModel;
            if (model?._itemCounts == null) return Unavailable("gadget_stock_unavailable");
            string query = Query(args);
            var rows = new List<(GadgetDefinition Def, int Count)>();
            int total = 0;
            foreach (var entry in model._itemCounts)
            {
                if (entry.Value <= 0) continue;
                var d = entry.Key?.TryCast<GadgetDefinition>();
                if (d == null) continue;
                total++;
                if (Matches(d.ReferenceId, query) || Matches(d.name, query)) rows.Add((d, entry.Value));
            }
            rows.Sort((a, b) => string.CompareOrdinal(a.Def.ReferenceId, b.Def.ReferenceId));
            return ToolsWorld.Page(args, total, rows, r => new Row
            {
                ["id"] = r.Def.ReferenceId, ["name"] = r.Def.name, ["in_stock"] = r.Count,
                ["type"] = r.Def.Type.ToString()
            });
        }

        static Row Components(GameModel game, Row args)
        {
            var catalog = game.upgradeComponents?.items;
            var model = game.PlayerModel?.UpgradeComponentsModel;
            if (catalog == null || model?._ownedComponents == null) return Unavailable("components_unavailable");
            string query = Query(args);
            bool owned = args.TryGetValue("owned_only", out var flag) && flag is true;
            var matches = new List<(string Id, string Name, int Count)>();
            foreach (var c in catalog)
            {
                if (c == null || (!Matches(c.ReferenceId, query) && !Matches(c.name, query))) continue;
                int count = model.GetComponentQuantity(c);
                if (!owned || count > 0) matches.Add((c.ReferenceId, c.name, count));
            }
            matches.Sort((a, b) => string.CompareOrdinal(a.Id, b.Id));
            return ToolsWorld.Page(args, catalog.Count, matches, r => new Row { ["id"] = r.Id, ["name"] = r.Name, ["count"] = r.Count });
        }

        static Row Recipe(GameModel game, Row args)
        {
            var model = game.GadgetsModel;
            var catalog = Catalog(model);
            if (catalog == null) return Unavailable("blueprint_catalog_unavailable");
            string id = args.TryGetValue("gadget_id", out var value) ? Convert.ToString(value).Trim() : "";
            if (!catalog.TryGetValue(id, out var gadget)) return Unavailable("unknown_gadget_id");
            int batch = args.TryGetValue("count", out var count) ? Convert.ToInt32(count) : 1;
            if (batch < 1 || batch > 99) return Unavailable("invalid_count");
            return Recipe(game, gadget, batch);
        }

        internal static Row Recipe(GameModel game, GadgetDefinition gadget, int batch)
        {
            var model = game.GadgetsModel;
            var cost = gadget.CraftingCosts;
            if (cost == null) return Unavailable("recipe_unavailable");
            var player = game.PlayerModel;
            var components = player?.UpgradeComponentsModel;
            var totals = new Dictionary<(string Kind, string Id), (long Required, int? Stock)>();
            bool Add(string kind, string key, int amount, int? stock)
            {
                if (string.IsNullOrEmpty(key) || amount < 0) return false;
                totals.TryGetValue((kind, key), out var old);
                totals[(kind, key)] = (old.Required + (long)amount * batch, stock);
                return true;
            }
            if (cost.IdentCosts != null)
                foreach (var entry in cost.IdentCosts)
                {
                    var item = entry?.IdentType;
                    if (item == null || !Add("material", item.ReferenceId, entry.Amount,
                        model._itemCounts == null ? null : model.GetCount(item))) return Unavailable("invalid_material_cost");
                }
            if (cost._currencyCosts != null)
                foreach (var entry in cost._currencyCosts)
                {
                    var currency = entry?.AssignedCurrency;
                    if (currency == null || !Add("currency", currency.ReferenceId, entry.Amount,
                        player?.GetCurrencyAmount(currency.Cast<ICurrency>()))) return Unavailable("invalid_currency_cost");
                }
            if (cost.ComponentCosts != null)
                foreach (var c in cost.ComponentCosts)
                    if (c == null || !Add("component", c.ReferenceId, 1,
                        components?._ownedComponents == null ? null : components.GetComponentQuantity(c))) return Unavailable("invalid_component_cost");

            var rows = new List<Row>();
            bool? covered = true;
            foreach (var pair in totals.OrderBy(p => p.Key.Kind, StringComparer.Ordinal).ThenBy(p => p.Key.Id, StringComparer.Ordinal))
            {
                var (required, stock) = pair.Value;
                long? missing = stock.HasValue ? Math.Max(0L, required - stock.Value) : null;
                if (missing > 0) covered = false;
                else if (missing == null && covered == true) covered = null;
                rows.Add(new Row { ["kind"] = pair.Key.Kind, ["id"] = pair.Key.Id, ["required"] = required, ["stock"] = stock, ["missing"] = missing });
            }
            return new Row { ["available"] = true, ["id"] = gadget.ReferenceId, ["count"] = batch, ["blueprint_owned"] = model.blueprints.Contains(gadget),
                ["costs_covered"] = covered, ["costs"] = rows };
        }

        static Row Pinned(Row args) => ToolsNavigation.WithScene(raw =>
        {
            var director = ((SceneContext)raw).PinnedRecipeDirector;
            if (director?._playerModel == null) return Unavailable("pinned_recipe_unavailable");
            if (director._playerModel.PinnedRecipe == null) return new() { ["available"] = true, ["pinned"] = false };
            var state = director.GetState();
            if (state?.Recipe == null) return Unavailable("recipe_state_unavailable");
            var ingredients = new List<Row>();
            for (int i = 0; i < state.NumberOfIngredients; i++)
            {
                var s = state.GetIngredientState(i);
                var ingredient = s?.Ingredient;
                bool visible = ingredient != null && s.CanShow;
                ingredients.Add(new Row
                {
                    ["visible"] = visible, ["title"] = visible ? Il2CppUtil.Localized(ingredient.Name) : null,
                    ["required"] = visible ? ingredient.CountRequired : null, ["current"] = visible ? s.CurrentCount : null,
                    ["met"] = visible ? s.RequirementMet : null
                });
            }
            return new Row { ["available"] = true, ["pinned"] = true, ["title"] = Il2CppUtil.Localized(state.Recipe.Name),
                ["requirements_met"] = state.RecipeRequirementsMet, ["ingredients"] = ingredients };
        });
    }
}
