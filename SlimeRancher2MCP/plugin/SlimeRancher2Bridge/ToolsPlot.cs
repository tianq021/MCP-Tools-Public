// ToolsPlot —— 地块升级（仓库格子等）的查看与购买
//
//   sr2_plot_upgrades  只读：列出地块（默认只列仓库）已买升级、各 STORAGE 升级状态、
//                      以及游戏里能读到的购买项价格（PlotUpgradePurchaseItemModel）。
//   sr2_plot_upgrade   写：购买地块升级（默认买下一个未购买的 STORAGE 升级，用来开仓库
//                      剩下的格子）。走游戏原生 LandPlotUIRoot.Upgrade(Upgrade, PurchaseCost)，
//                      价格取自游戏自己的购买项资产，默认预演。
//
// 游戏 API（1.3.0 实测类型名）：
//   LandPlotModel.HasUpgrade(LandPlot.Upgrade) / plot.gameObj
//   LandPlotUIRoot.Upgrade(Upgrade upgrade, PurchaseCost cost) -> bool（protected，反射调用）
//   PlotUpgradePurchaseItemModel.Upgrade + MenuPurchaseItemModel.PurchaseCost.NewbuckCost
//   新币：CurrencyUtility.DefaultCurrency + PlayerState.GetCurrency
using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using Il2Cpp;
using Il2CppMonomiPark.SlimeRancher.DataModel;
using Il2CppMonomiPark.SlimeRancher.Economy;
using Il2CppMonomiPark.SlimeRancher.UI.Plot;
using Il2CppMonomiPark.SlimeRancher.Player;
using UnityEngine;
using Row = System.Collections.Generic.Dictionary<string, object>;

namespace SlimeRancher2Bridge
{
    internal static class ToolsPlot
    {
        internal static void Register(BridgeServer server)
        {
            server.Register("sr2_plot_upgrades", List, mainThread: true);
            server.Register("sr2_plot_upgrade", Buy, mainThread: true, write: true);
            server.Register("sr2_plot_catalog", Catalog, mainThread: true);
            server.Register("sr2_plot_buy", BuyPlot, mainThread: true, write: true);
        }

        static readonly LandPlot.Upgrade[] StorageUpgrades =
        {
            LandPlot.Upgrade.STORAGE2, LandPlot.Upgrade.STORAGE3, LandPlot.Upgrade.STORAGE4
        };

        static string Str(Row args, string key, string fallback)
            => args.TryGetValue(key, out var value) && value != null ? Convert.ToString(value).Trim() : fallback;

        static object Coordinates(Vector3 p)
            => new { x = Math.Round(p.x, 2), y = Math.Round(p.y, 2), z = Math.Round(p.z, 2) };

        static string UpgradeName(object upgrade) => upgrade == null ? null : Convert.ToString(upgrade);

        static bool HasUpgrade(LandPlotModel plot, LandPlot.Upgrade upgrade)
        {
            try { return plot != null && plot.HasUpgrade(upgrade); }
            catch { return false; }
        }

        internal static List<Row> ReadUpgrades(LandPlotModel plot)
        {
            var rows = new List<Row>();
            try
            {
                var upgrades = Il2CppUtil.Try(plot, "upgrades");
                foreach (var upgrade in Il2CppUtil.Enumerate(upgrades))
                    rows.Add(new Row { ["upgrade"] = UpgradeName(upgrade) });
            }
            catch (Exception e)
            {
                rows.Add(new Row { ["error"] = e.GetBaseException().Message });
            }
            return rows;
        }

        // Purchase items are ScriptableObject assets held by the plot UI category; they carry
        // the upgrade and the real newbuck cost the game charges.
        static List<object> PurchaseModels(out string diagnostic)
        {
            var found = new List<object>();
            diagnostic = "not_started";
            try
            {
                var type = typeof(PlotUpgradePurchaseItemModel);
                foreach (var item in Il2CppUtil.Enumerate(UnityEngine.Resources.FindObjectsOfTypeAll<PlotUpgradePurchaseItemModel>()))
                {
                    if (Il2CppUtil.Try(item, "Upgrade") == null) continue;
                    found.Add(item);
                }
                diagnostic = "ok count=" + found.Count;
            }
            catch (Exception e)
            {
                diagnostic = "failed: " + e.GetBaseException().Message;
            }
            return found;
        }

        static object FindPurchaseModel(List<object> models, LandPlot.Upgrade upgrade)
        {
            foreach (var model in models)
            {
                var value = Il2CppUtil.Try(model, "Upgrade");
                if (value == null) continue;
                if (Convert.ToString(value) == Convert.ToString(upgrade)) return model;
            }
            return null;
        }

        static int NewbuckCostOf(object purchaseModel)
        {
            var cost = Il2CppUtil.Try(purchaseModel, "PurchaseCost");
            if (cost == null) return -1;
            return Il2CppUtil.IntOf(Il2CppUtil.Try(cost, "NewbuckCost"), -1);
        }

        static LandPlotUIRoot FindUiRoot(LandPlotModel plot)
        {
            if (plot == null) return null;
            try
            {
                if (plot.gameObj != null)
                {
                    foreach (var candidate in plot.gameObj.GetComponentsInChildren<LandPlotUIRoot>(true))
                        if (candidate != null && PlotMatches(candidate, plot)) return candidate;
                    // The terminal UI can hang off a LandPlotUIActivator instead of the plot root.
                    foreach (var activator in plot.gameObj.GetComponentsInChildren<Il2CppMonomiPark.SlimeRancher.UI.LandPlotUIActivator>(true))
                    {
                        var landPlot = Il2CppUtil.Try(activator, "landPlot") as LandPlot;
                        var model = landPlot == null ? null : Il2CppUtil.Try(landPlot, "_model") as LandPlotModel;
                        if (model == null || model.Pointer != plot.Pointer) continue;
                        var root = activator.gameObject.GetComponentInChildren<LandPlotUIRoot>(true);
                        if (root != null) return root;
                    }
                }
                var all = UnityEngine.Object.FindObjectsByType<LandPlotUIRoot>(FindObjectsSortMode.None);
                foreach (var candidate in all)
                    if (candidate != null && PlotMatches(candidate, plot)) return candidate;
                // Last resort: nearest UI root to the plot position.
                if (plot.gameObj == null) return null;
                var plotPos = plot.gameObj.transform.position;
                LandPlotUIRoot nearest = null;
                float best = 25f;
                foreach (var candidate in all)
                {
                    if (candidate == null) continue;
                    float distance = Vector3.Distance(candidate.transform.position, plotPos);
                    if (distance < best) { best = distance; nearest = candidate; }
                }
                return nearest;
            }
            catch
            {
                return null;
            }
        }

        static LandPlot FindLandPlot(LandPlotModel plot)
        {
            if (plot == null) return null;
            try
            {
                foreach (var candidate in UnityEngine.Object.FindObjectsByType<LandPlot>(FindObjectsSortMode.None))
                {
                    if (candidate == null) continue;
                    var model = Il2CppUtil.Try(candidate, "_model") as LandPlotModel;
                    if (model != null && model.Pointer == plot.Pointer) return candidate;
                }
            }
            catch
            {
            }
            return null;
        }

        // Fallback when the plot terminal UI is not instantiated: charge newbucks through the
        // native currency API, add the upgrade to the plot model and let the storage upgrader
        // apply it. Reports exactly what it did and whether HasUpgrade confirms it.
        static Row NativeUpgrade(SceneContext scene, LandPlotModel plotModel, LandPlot.Upgrade target, int price)
        {
            var row = new Row();
            var landPlot = FindLandPlot(plotModel);
            if (landPlot == null)
            {
                row["blocked"] = true;
                row["reason"] = "land_plot_unavailable";
                return row;
            }
            var playerState = Il2CppUtil.Try(scene, "PlayerState");
            if (price > 0)
            {
                var currency = CurrencyUtility.DefaultCurrency;
                if (playerState == null || currency == null)
                {
                    row["blocked"] = true;
                    row["reason"] = "currency_unavailable";
                    return row;
                }
                Il2CppUtil.Invoke(playerState, "SpendCurrency", new object[] { currency, price, null });
                row["charged"] = price;
                row["spend_error"] = Il2CppUtil.LastInvokeError;
            }
            Il2CppUtil.Invoke(landPlot, "AddUpgrade", new object[] { target });
            row["add_upgrade_error"] = Il2CppUtil.LastInvokeError;
            try
            {
                foreach (var upgrader in landPlot.gameObject.GetComponentsInChildren<StorageUpgrader>(true))
                    upgrader.Apply(target);
            }
            catch (Exception e)
            {
                row["apply_error"] = e.GetBaseException().Message;
            }
            row["path"] = "native";
            row["purchased_ok"] = HasUpgrade(plotModel, target);
            row["purchased_after"] = ReadUpgrades(plotModel);
            row["coins_after"] = ReadCoins(playerState);
            row["storage_after"] = new Row
            {
                ["STORAGE2"] = HasUpgrade(plotModel, LandPlot.Upgrade.STORAGE2),
                ["STORAGE3"] = HasUpgrade(plotModel, LandPlot.Upgrade.STORAGE3),
                ["STORAGE4"] = HasUpgrade(plotModel, LandPlot.Upgrade.STORAGE4),
            };
            return row;
        }

        static bool PlotMatches(LandPlotUIRoot uiRoot, LandPlotModel plot)
        {
            try
            {
                var activator = Il2CppUtil.Try(uiRoot, "Activator") as LandPlot;
                if (activator == null) return false;
                var model = Il2CppUtil.Try(activator, "_model") as LandPlotModel;
                return model != null && model.Pointer == plot.Pointer;
            }
            catch
            {
                return false;
            }
        }

        static List<(string Key, LandPlotModel Plot)> SiloPlots(SceneContext scene)
        {
            var list = new List<(string Key, LandPlotModel Plot)>();
            var plots = scene.GameModel?.landPlots;
            if (plots == null) return list;
            foreach (var entry in plots)
            {
                var plot = entry.Value;
                if (plot == null || plot.typeId != LandPlot.Id.SILO) continue;
                list.Add(("plot:" + Uri.EscapeDataString(entry.Key), plot));
            }
            list.Sort((a, b) => string.CompareOrdinal(a.Key, b.Key));
            return list;
        }

        // 选择器：列表编号、"plot:<地块键>" 前缀或完整 storage_key
        static (string Key, LandPlotModel Plot, int Index) Resolve(List<(string Key, LandPlotModel Plot)> plots, string selector)
        {
            if (plots.Count == 0) return (null, null, -1);
            if (string.IsNullOrEmpty(selector)) return (plots[0].Key, plots[0].Plot, 0);
            if (int.TryParse(selector, out int index) && index >= 0 && index < plots.Count)
                return (plots[index].Key, plots[index].Plot, index);
            for (int i = 0; i < plots.Count; i++)
                if (selector == plots[i].Key || selector.StartsWith(plots[i].Key, StringComparison.Ordinal))
                    return (plots[i].Key, plots[i].Plot, i);
            return (null, null, -1);
        }

        static Row DescribePlot(int index, string key, LandPlotModel plot, List<object> models)
        {
            var storage = new Row();
            foreach (var upgrade in StorageUpgrades)
            {
                storage[UpgradeName(upgrade)] = HasUpgrade(plot, upgrade);
            }
            var prices = new List<Row>();
            foreach (var upgrade in StorageUpgrades)
            {
                var model = FindPurchaseModel(models, upgrade);
                prices.Add(new Row
                {
                    ["upgrade"] = UpgradeName(upgrade),
                    ["purchased"] = HasUpgrade(plot, upgrade),
                    ["newbuck_cost"] = model == null ? null : (object)NewbuckCostOf(model),
                    ["purchase_model"] = model != null,
                });
            }
            return new Row
            {
                ["index"] = index,
                ["plot_key"] = key,
                ["loaded"] = plot.gameObj != null && plot.gameObj.activeInHierarchy,
                ["ui_root_found"] = FindUiRoot(plot) != null,
                ["land_plot_found"] = FindLandPlot(plot) != null,
                ["purchased"] = ReadUpgrades(plot),
                ["storage"] = storage,
                ["storage_prices"] = prices,
            };
        }

        internal static Row List(Row args)
        {
            string selector = Str(args, "storage", "");
            return ToolsNavigation.WithScene(raw =>
            {
                var scene = (SceneContext)raw;
                var plots = SiloPlots(scene);
                if (plots.Count == 0) return new() { ["available"] = false, ["reason"] = "no_silo_plot_found" };
                var models = PurchaseModels(out string modelDiagnostic);
                int limit = 20;
                if (int.TryParse(Str(args, "limit", ""), out int parsed) && parsed > 0) limit = parsed;
                var rows = new List<Row>();
                var selected = Resolve(plots, selector);
                if (selector.Length > 0 && selected.Plot == null)
                    return new() { ["available"] = false, ["reason"] = "bad_storage" };
                for (int i = 0; i < plots.Count && rows.Count < limit; i++)
                {
                    if (selector.Length > 0 && i != selected.Index) continue;
                    rows.Add(DescribePlot(i, plots[i].Key, plots[i].Plot, models));
                }
                var result = new Row
                {
                    ["available"] = true,
                    ["plot_count"] = plots.Count,
                    ["count"] = rows.Count,
                    ["plots"] = rows,
                    ["debug"] = new Row { ["purchase_models"] = modelDiagnostic },
                    ["note"] = "storage_prices 来自游戏自己的购买项资产；newbuck_cost=null 表示没读到该升级的价格",
                };
                string diagnose = Str(args, "diagnose", "");
                if (diagnose == "1" || diagnose.Equals("true", StringComparison.OrdinalIgnoreCase))
                {
                    var roots = new List<Row>();
                    foreach (var root in UnityEngine.Object.FindObjectsByType<LandPlotUIRoot>(FindObjectsSortMode.None))
                    {
                        if (root == null) continue;
                        object activator = Il2CppUtil.Try(root, "Activator");
                        var activatorModel = activator == null ? null : Il2CppUtil.Try(activator, "_model");
                        roots.Add(new Row
                        {
                            ["name"] = root.gameObject?.name,
                            ["active"] = root.gameObject != null && root.gameObject.activeInHierarchy,
                            ["activator"] = activatorModel == null ? null : "has_model",
                            ["position"] = Coordinates(root.transform.position),
                        });
                    }
                    result["diagnose"] = new Row
                    {
                        ["ui_roots"] = roots,
                        ["land_plots"] = UnityEngine.Object.FindObjectsByType<LandPlot>(FindObjectsSortMode.None)
                            .Where(p => p != null)
                            .Select(p => new Row { ["name"] = p.gameObject?.name, ["has_model"] = Il2CppUtil.Try(p, "_model") != null })
                            .ToList(),
                    };
                }
                return result;
            });
        }

        static object ReadCoins(object playerState)
        {
            try
            {
                var currency = CurrencyUtility.DefaultCurrency;
                if (currency == null || playerState == null) return null;
                return Il2CppUtil.Invoke(playerState, "GetCurrency", new object[] { currency });
            }
            catch
            {
                return null;
            }
        }

        // ---------------- 地块类型目录 / 购买空地 ----------------

        static List<object> PatchModels(out string diagnostic)
        {
            var found = new List<object>();
            diagnostic = "not_started";
            try
            {
                foreach (var item in Il2CppUtil.Enumerate(UnityEngine.Resources.FindObjectsOfTypeAll<PlotPatchPurchaseItemModel>()))
                {
                    if (Il2CppUtil.Try(item, "PlotPrefab") == null) continue;
                    found.Add(item);
                }
                diagnostic = "ok count=" + found.Count;
            }
            catch (Exception e)
            {
                diagnostic = "failed: " + e.GetBaseException().Message;
            }
            return found;
        }

        // LocalizedString lives in UnityEngine.Localization; try the common accessors and
        // fall back to null rather than inventing text.
        static string LocalizedText(object localizedString)
        {
            if (localizedString == null) return null;
            foreach (var method in new[] { "GetLocalizedString", "GetLocalizedStringAsync" })
            {
                try
                {
                    var text = Il2CppUtil.Invoke(localizedString, method) as string;
                    if (!string.IsNullOrEmpty(text)) return text;
                }
                catch
                {
                }
            }
            try
            {
                var raw = Il2CppUtil.Try(localizedString, "m_Localized") as string;
                if (!string.IsNullOrEmpty(raw)) return raw;
            }
            catch
            {
            }
            try
            {
                var tableEntry = Il2CppUtil.Try(localizedString, "TableEntryReference");
                var key = tableEntry == null ? null : Convert.ToString(Il2CppUtil.Try(tableEntry, "Key"));
                if (!string.IsNullOrEmpty(key)) return "key:" + key;
            }
            catch
            {
            }
            return null;
        }

        static string PlotTypeOf(object patchModel)
        {
            try
            {
                var definition = Il2CppUtil.Try(patchModel, "_plotDefinition");
                var type = definition == null ? null : Il2CppUtil.Try(definition, "Type");
                return type == null ? null : Convert.ToString(type);
            }
            catch
            {
                return null;
            }
        }

        static string PrefabName(object patchModel)
        {
            try
            {
                var prefab = Il2CppUtil.Try(patchModel, "PlotPrefab") as GameObject;
                return prefab == null ? null : prefab.name;
            }
            catch
            {
                return null;
            }
        }

        static Row DescribePatch(object patchModel)
        {
            return new Row
            {
                ["plot_type"] = PlotTypeOf(patchModel),
                ["prefab"] = PrefabName(patchModel),
                ["title"] = LocalizedText(Il2CppUtil.Try(patchModel, "Title")),
                ["description"] = LocalizedText(Il2CppUtil.Try(patchModel, "Description")),
                ["newbuck_cost"] = NewbuckCostOf(patchModel),
            };
        }

        static Row DescribeUpgradeModel(object model)
        {
            var upgrade = Il2CppUtil.Try(model, "Upgrade");
            return new Row
            {
                ["upgrade"] = UpgradeName(upgrade),
                ["title"] = LocalizedText(Il2CppUtil.Try(model, "Title")),
                ["description"] = LocalizedText(Il2CppUtil.Try(model, "Description")),
                ["newbuck_cost"] = NewbuckCostOf(model),
            };
        }

        internal static Row Catalog(Row args)
        {
            string selector = Str(args, "storage", "");
            return ToolsNavigation.WithScene(raw =>
            {
                var scene = (SceneContext)raw;
                var patches = PatchModels(out string patchDiagnostic);
                var upgradeModels = PurchaseModels(out string upgradeDiagnostic);
                var plots = SiloPlots(scene);
                var allPlots = new List<Row>();
                var lands = scene.GameModel?.landPlots;
                if (lands != null)
                {
                    foreach (var entry in lands)
                    {
                        var plot = entry.Value;
                        if (plot == null) continue;
                        allPlots.Add(new Row
                        {
                            ["plot_key"] = "plot:" + Uri.EscapeDataString(entry.Key),
                            ["type"] = Convert.ToString(plot.typeId),
                            ["loaded"] = plot.gameObj != null && plot.gameObj.activeInHierarchy,
                        });
                    }
                }
                var selected = Resolve(plots, selector);
                _ = selected;
                return new Row
                {
                    ["available"] = true,
                    ["plot_types"] = patches.Select(DescribePatch).ToList(),
                    ["upgrades"] = upgradeModels.Select(DescribeUpgradeModel).ToList(),
                    ["current_plots"] = allPlots,
                    ["debug"] = new Row
                    {
                        ["patch_models"] = patchDiagnostic,
                        ["upgrade_models"] = upgradeDiagnostic,
                    },
                    ["note"] = "plot_types=可建地块（含仓库 SILO）；upgrades=可买的地块改良设施，title/description 来自游戏本地化文本（读不到则为 null）；current_plots 里 type=EMPTY 表示空地",
                };
            });
        }

        static Row BuyPlot(Row args)
        {
            bool preview = (bool)args.GetValueOrDefault("dry_run", true);
            string requested = Str(args, "type", "").ToUpperInvariant().Replace("_", "").Replace(" ", "");
            string plotSelector = Str(args, "plot", "");
            return ToolsNavigation.WithScene(raw =>
            {
                var scene = (SceneContext)raw;
                var patches = PatchModels(out string patchDiagnostic);
                if (patches.Count == 0)
                    return new() { ["available"] = false, ["reason"] = "no_plot_patch_found",
                        ["debug"] = new Row { ["patch_models"] = patchDiagnostic } };

                // 目标地块类型：按枚举名匹配（SILO / GARDEN / COOP / CORRAL / POND / INCINERATOR）
                object patch = null;
                foreach (var candidate in patches)
                {
                    if (string.Equals(PlotTypeOf(candidate), requested, StringComparison.OrdinalIgnoreCase))
                    {
                        patch = candidate;
                        break;
                    }
                }
                if (patch == null)
                    return new() { ["available"] = false, ["reason"] = "unknown_plot_type",
                        ["hint"] = "可选：" + string.Join(" / ", patches.Select(PlotTypeOf).Where(t => t != null)) };
                var prefab = Il2CppUtil.Try(patch, "PlotPrefab") as GameObject;
                if (prefab == null) return new() { ["available"] = false, ["reason"] = "plot_prefab_unavailable" };
                object cost = Il2CppUtil.Try(patch, "PurchaseCost");
                if (cost == null) return new() { ["available"] = false, ["reason"] = "purchase_cost_unavailable" };

                // 空地（typeId=EMPTY）是购买地块的位置
                var empties = new List<(string Key, LandPlotModel Plot)>();
                var lands = scene.GameModel?.landPlots;
                if (lands != null)
                {
                    foreach (var entry in lands)
                    {
                        var model = entry.Value;
                        if (model == null || model.typeId != LandPlot.Id.EMPTY) continue;
                        empties.Add(("plot:" + Uri.EscapeDataString(entry.Key), model));
                    }
                }
                empties.Sort((a, b) => string.CompareOrdinal(a.Key, b.Key));
                (string Key, LandPlotModel Plot) selected = (null, null);
                if (string.IsNullOrEmpty(plotSelector))
                {
                    if (empties.Count > 0) selected = empties[0];
                }
                else
                {
                    if (int.TryParse(plotSelector, out int index) && index >= 0 && index < empties.Count)
                        selected = empties[index];
                    else
                        selected = empties.FirstOrDefault(e => e.Key == plotSelector || plotSelector.StartsWith(e.Key, StringComparison.Ordinal));
                }
                var playerState = Il2CppUtil.Try(scene, "PlayerState");
                object coins = ReadCoins(playerState);
                int price = NewbuckCostOf(patch);
                var result = new Row
                {
                    ["available"] = true,
                    ["dry_run"] = preview,
                    ["plot_type"] = PlotTypeOf(patch),
                    ["prefab"] = prefab.name,
                    ["title"] = LocalizedText(Il2CppUtil.Try(patch, "Title")),
                    ["newbuck_cost"] = price < 0 ? null : (object)price,
                    ["coins"] = coins,
                    ["empty_plot_count"] = empties.Count,
                    ["empty_plots"] = empties.Select(e => e.Key).ToList(),
                    ["debug"] = new Row { ["patch_models"] = patchDiagnostic },
                };
                if (selected.Plot == null)
                {
                    result["blocked"] = true;
                    result["reason"] = empties.Count == 0 ? "no_empty_plot" : "bad_plot";
                    return result;
                }
                result["plot_key"] = selected.Key;
                if (price > 0 && coins != null && Convert.ToInt32(coins) < price)
                {
                    result["blocked"] = true;
                    result["reason"] = "insufficient_coins";
                    return result;
                }
                if (preview) return result;
                var uiRoot = FindUiRoot(selected.Plot);
                if (uiRoot == null)
                {
                    result["blocked"] = true;
                    result["reason"] = "ui_root_unavailable";
                    result["hint"] = "空地所在场景未加载（需要回到牧场）";
                    return result;
                }
                var method = typeof(LandPlotUIRoot).GetMethod("BuyPlot",
                    BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                if (method == null)
                {
                    result["blocked"] = true;
                    result["reason"] = "unsupported_game_api";
                    return result;
                }
                object ok;
                try
                {
                    ok = method.Invoke(uiRoot, new object[] { cost, prefab });
                }
                catch (Exception e)
                {
                    result["blocked"] = true;
                    result["reason"] = "purchase_failed";
                    result["error"] = (e is TargetInvocationException tie ? tie.GetBaseException() : e).Message;
                    return result;
                }
                result["purchased_ok"] = ok is bool bought && bought;
                result["type_after"] = Convert.ToString(selected.Plot.typeId);
                result["coins_after"] = ReadCoins(playerState);
                return result;
            });
        }

        internal static Row Buy(Row args)
        {
            bool preview = (bool)args.GetValueOrDefault("dry_run", true);
            string selector = Str(args, "storage", "");
            string requested = Str(args, "upgrade", "").ToUpperInvariant().Replace("_", "").Replace(" ", "");
            return ToolsNavigation.WithScene(raw =>
            {
                var scene = (SceneContext)raw;
                var plots = SiloPlots(scene);
                if (plots.Count == 0) return new() { ["available"] = false, ["reason"] = "no_silo_plot_found" };
                var selected = Resolve(plots, selector);
                if (selected.Plot == null) return new() { ["available"] = false, ["reason"] = "bad_storage" };
                var plot = selected.Plot;
                var models = PurchaseModels(out string modelDiagnostic);

                LandPlot.Upgrade target;
                if (requested.Length == 0)
                {
                    target = StorageUpgrades.FirstOrDefault(u => !HasUpgrade(plot, u));
                    if (Convert.ToString(target) != "STORAGE2" && Convert.ToString(target) != "STORAGE3"
                        && Convert.ToString(target) != "STORAGE4")
                        return new() { ["available"] = false, ["reason"] = "all_storage_upgrades_purchased",
                            ["plot_key"] = selected.Key };
                }
                else
                {
                    var match = StorageUpgrades.FirstOrDefault(u =>
                        Convert.ToString(u).Equals(requested, StringComparison.OrdinalIgnoreCase));
                    if (Convert.ToString(match) != "STORAGE2" && Convert.ToString(match) != "STORAGE3"
                        && Convert.ToString(match) != "STORAGE4")
                        return new() { ["available"] = false, ["reason"] = "unsupported_upgrade",
                            ["hint"] = "目前只支持 STORAGE2 / STORAGE3 / STORAGE4" };
                    target = match;
                }
                string targetName = Convert.ToString(target);
                bool already = HasUpgrade(plot, target);

                var purchaseModel = FindPurchaseModel(models, target);
                object cost = purchaseModel == null ? null : Il2CppUtil.Try(purchaseModel, "PurchaseCost");
                int price = purchaseModel == null ? -1 : NewbuckCostOf(purchaseModel);
                var playerState = Il2CppUtil.Try(scene, "PlayerState");
                object coins = ReadCoins(playerState);
                var uiRoot = FindUiRoot(plot);

                var result = new Row
                {
                    ["available"] = true,
                    ["dry_run"] = preview,
                    ["plot_key"] = selected.Key,
                    ["plot_index"] = selected.Index,
                    ["upgrade"] = targetName,
                    ["already_purchased"] = already,
                    ["newbuck_cost"] = price < 0 ? null : (object)price,
                    ["coins"] = coins,
                    ["plot_loaded"] = plot.gameObj != null && plot.gameObj.activeInHierarchy,
                    ["ui_root_found"] = uiRoot != null,
                    ["debug"] = new Row { ["purchase_models"] = modelDiagnostic },
                };
                if (already) return result;
                if (purchaseModel == null || cost == null)
                {
                    result["blocked"] = true;
                    result["reason"] = "purchase_cost_unavailable";
                    return result;
                }
                if (price > 0 && coins != null && Convert.ToInt32(coins) < price)
                {
                    result["blocked"] = true;
                    result["reason"] = "insufficient_coins";
                    return result;
                }
                if (preview) return result;
                if (uiRoot == null)
                {
                    // Terminal UI not instantiated: fall back to charging + applying natively.
                    foreach (var pair in NativeUpgrade(scene, plot, target, price))
                        result[pair.Key] = pair.Value;
                    return result;
                }
                var method = typeof(LandPlotUIRoot).GetMethod("Upgrade",
                    BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                if (method == null)
                {
                    result["blocked"] = true;
                    result["reason"] = "unsupported_game_api";
                    return result;
                }
                object ok;
                try
                {
                    ok = method.Invoke(uiRoot, new object[] { target, cost });
                }
                catch (Exception e)
                {
                    result["blocked"] = true;
                    result["reason"] = "purchase_failed";
                    result["error"] = (e is TargetInvocationException tie ? tie.GetBaseException() : e).Message;
                    return result;
                }
                result["purchased_ok"] = ok is bool purchased && purchased;
                result["purchased_after"] = ReadUpgrades(plot);
                result["coins_after"] = ReadCoins(playerState);
                result["storage_after"] = new Row
                {
                    ["STORAGE2"] = HasUpgrade(plot, LandPlot.Upgrade.STORAGE2),
                    ["STORAGE3"] = HasUpgrade(plot, LandPlot.Upgrade.STORAGE3),
                    ["STORAGE4"] = HasUpgrade(plot, LandPlot.Upgrade.STORAGE4),
                };
                return result;
            });
        }
    }
}

