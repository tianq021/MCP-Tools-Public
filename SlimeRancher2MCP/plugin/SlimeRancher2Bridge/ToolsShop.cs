using System;
using System.Collections.Generic;
using System.Linq;
using Il2CppMonomiPark.SlimeRancher.Event;
using Il2CppMonomiPark.SlimeRancher.Shop.Runtime;
using Il2CppMonomiPark.SlimeRancher.UI.Shop;
using UnityEngine;
using static SlimeRancher2Bridge.Il2CppUtil;
using Row = System.Collections.Generic.Dictionary<string, object>;

namespace SlimeRancher2Bridge
{
    internal static class ToolsShop
    {
        static Row ItemRow(ShopRuntimeItem item, int index)
        {
            var cost = item.PurchaseCost;
            var other = new List<Row>();
            if (cost.IdentCosts != null)
                foreach (var entry in cost.IdentCosts)
                    other.Add(new() { ["kind"] = "ident", ["id"] = entry.IdentType?.ReferenceId, ["count"] = entry.Amount });
            if (cost.CurrencyCosts != null)
            {
                var currencyCosts = cost.CurrencyCosts.Cast<Il2CppSystem.Collections.Generic.IReadOnlyCollection<Il2CppMonomiPark.SlimeRancher.Economy.CurrencyCostEntry>>();
                for (int i = 0; i < currencyCosts.Count; ++i)
                {
                    var entry = cost.CurrencyCosts[i];
                    other.Add(new() { ["kind"] = "currency", ["id"] = entry.AssignedCurrency?.ReferenceId, ["count"] = entry.Amount });
                }
            }
            if (cost.ComponentCosts != null)
                foreach (var component in cost.ComponentCosts)
                    other.Add(new() { ["kind"] = "component", ["id"] = component.ReferenceId, ["count"] = 1 });
            return new() { ["index"] = index, ["id"] = item.UniqueId.Id, ["asset_guid"] = item.AssetGuid,
                ["name"] = item.IsDoneLoading ? Localized(item.Title) : null,
                ["newbuck_cost"] = cost.NewbuckCost, ["other_costs"] = other, ["sold_out"] = item.IsSoldOut,
                ["owned"] = item.GetOwnedCount(), ["acquirable"] = item.GetAcquirableCount(),
                ["max_own"] = item.GetMaxCanOwnCount(), ["purchased"] = item.GetCountPurchased() };
        }


        internal static Row List(Row args)
        {
            var filter = args.TryGetValue("shop", out var value) ? Convert.ToString(value) : "";
            int index = -1;
            if (!string.IsNullOrEmpty(filter) && (!int.TryParse(filter, out index) || index < 0))
                return new() { ["available"] = false, ["reason"] = "bad_shop" };

            return ToolsNavigation.WithScene(raw =>
            {
                var scene = (Il2Cpp.SceneContext)raw;
                var runtimes = scene.ShopDirector?._runtimes;
                var events = scene.eventDirector?.Cast<IEventDirector>();
                if (runtimes == null || events == null)
                    return new() { ["available"] = false, ["reason"] = "shop_director_unavailable" };
                var shops = new List<Row>();
                for (int i = 0; i < runtimes.Count; ++i)
                {
                    if (index >= 0 && i != index) continue;
                    var shop = runtimes[i];
                    var categories = new List<Row>();
                    var row = new Row { ["index"] = i, ["tag"] = shop.Definition.ShopAssetTag,
                        ["unlocked"] = shop.IsShopAvailable, ["loaded"] = shop.IsDoneLoading, ["categories"] = categories };
                    shops.Add(row);
                    try
                    {
                        foreach (var category in Snapshot(shop.GetActiveCategories(events)))
                        {
                            var items = new List<Row>();
                            var cat = new Row { ["name"] = Localized(category.DisplayName) ?? category.AnalyticsName,
                                ["analytics"] = category.AnalyticsName, ["tag"] = category.SourceShopAssetTag,
                                ["sort_order"] = category.DisplaySortOrder, ["items"] = items };
                            categories.Add(cat);
                            int count = 0;
                            foreach (var item in Snapshot(category.GetRuntimeItems(events)))
                            {
                                if (count < 80) items.Add(ItemRow(item, count));
                                ++count;
                            }
                            cat["item_count"] = items.Count;
                            if (count > 80) cat["items_truncated"] = count - 80;
                        }
                    }
                    catch (Exception ex) { row["error"] = ex.GetBaseException().Message; }
                    row["category_count"] = categories.Count;
                }
                return new() { ["available"] = true, ["shop_count"] = shops.Count, ["shops"] = shops };
            });
        }

        internal static Row Buy(Row args)
        {
            string shopTag = args.GetValueOrDefault("shop_tag") as string;
            string id = args.GetValueOrDefault("item_id") as string;
            if (string.IsNullOrWhiteSpace(shopTag) || string.IsNullOrWhiteSpace(id))
                throw new ArgumentException("shop_tag and item_id required");
            var countValue = args.GetValueOrDefault("count", 1);
            if (countValue is not (long or int) || Convert.ToInt64(countValue) is < 1 or > 99)
                throw new ArgumentException("count must be 1..99");
            int count = Convert.ToInt32(countValue);
            bool preview = (bool)args.GetValueOrDefault("dry_run", true);
            return ToolsNavigation.WithScene(raw =>
            {
                var scene = (Il2Cpp.SceneContext)raw;
                var events = scene.eventDirector?.Cast<IEventDirector>();
                var shops = scene.ShopDirector?._runtimes;
                if (events == null || shops == null) return new() { ["available"] = false, ["reason"] = "shop_director_unavailable" };
                foreach (var shop in shops)
                {
                    if (shop.Definition.ShopAssetTag != shopTag || !shop.IsShopAvailable || !shop.IsDoneLoading) continue;
                    ShopRuntimeItem match = null;
                    foreach (var category in Snapshot(shop.GetActiveCategories(events)))
                        foreach (var item in Snapshot(category.GetRuntimeItems(events)))
                        {
                            if (item.UniqueId.Id != id) continue;
                            if (match != null) return new() { ["available"] = false, ["reason"] = "ambiguous_item" };
                            match = item;
                        }
                    if (match == null) break;
                    bool eligible = !match.IsSoldOut && match.CanAcquireIgnoringCosts(count) && match.CanAfford(count);
                    var result = new Row { ["available"] = true, ["dry_run"] = preview, ["eligible"] = eligible,
                        ["item"] = ItemRow(match, 0), ["count"] = count, ["purchased"] = false };
                    if (preview || !eligible) return result;
                    // Use the active shop's delivery point for physical purchases.
                    foreach (var ui in UnityEngine.Object.FindObjectsByType<ShopUIBase>(FindObjectsSortMode.None))
                    {
                        if (!ui.isActiveAndEnabled || ui._runtimeData?.Pointer != shop.Pointer ||
                            string.IsNullOrEmpty(ui._spawnHandlerId)) continue;
                        result["purchased"] = match.TryPurchase(count, ui._spawnHandlerId, Il2CppSystem.Guid.NewGuid());
                        return result;
                    }
                    result["reason"] = "open_matching_shop_for_delivery";
                    return result;
                }
                return new() { ["available"] = false, ["reason"] = "shop_or_item_unavailable" };
            });
        }
    }
}
