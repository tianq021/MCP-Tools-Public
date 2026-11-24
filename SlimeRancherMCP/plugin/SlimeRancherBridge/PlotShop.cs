using System;
using System.Collections.Generic;
using System.Linq;
using UnityEngine;
using Object = UnityEngine.Object;

namespace SlimeRancherBridge
{
    static partial class RanchTools
    {
        static void RegisterPlotShop(BridgeServer server)
        {
            Read(server, "sr_plot_shop", PlotShop);
            Action(server, "sr_buy_plot", BuyPlot);
            Action(server, "sr_buy_plot_upgrade", BuyPlotUpgrade);
        }

        static LandPlotLocation PlotLocation(string id)
        {
            return id.Length == 0 ? null : Object.FindObjectsOfType<LandPlotLocation>()
                .FirstOrDefault(x => x.isActiveAndEnabled && x.id == id);
        }

        static T PlotMenu<T>(LandPlot plot) where T : LandPlotUI
        {
            return plot.GetComponentsInChildren<UIActivator>(true)
                .Where(x => x.uiPrefab != null).Select(x => x.uiPrefab.GetComponent<T>())
                .FirstOrDefault(x => x != null);
        }

        static IEnumerable<LandPlotUI.PlotPurchaseItem> PlotTypes(LandPlot plot)
        {
            var menu = PlotMenu<EmptyPlotUI>(plot);
            return menu == null ? new LandPlotUI.PlotPurchaseItem[0]
                : new[] { menu.corral, menu.garden, menu.coop, menu.silo, menu.incinerator, menu.pond }
                    .Where(x => x != null && x.plotPrefab != null && x.plotPrefab.GetComponent<LandPlot>() != null);
        }

        sealed class PlotUpgradeOffer
        {
            public readonly LandPlot.Upgrade Id;
            public readonly int Price;
            public readonly bool Owned;
            public readonly bool Unlocked;

            public PlotUpgradeOffer(LandPlot plot, LandPlot.Upgrade id, LandPlotUI.UpgradePurchaseItem item, bool unlocked)
            {
                Id = id;
                Price = item == null ? -1 : item.cost;
                Owned = plot.HasUpgrade(id);
                Unlocked = unlocked;
            }
        }

        static IEnumerable<PlotUpgradeOffer> PlotUpgrades(SceneContext scene, LandPlot plot)
        {
            switch (plot.typeId)
            {
                case LandPlot.Id.CORRAL:
                    var corral = PlotMenu<CorralUI>(plot);
                    if (corral == null) break;
                    yield return new PlotUpgradeOffer(plot, LandPlot.Upgrade.WALLS, corral.walls, true);
                    yield return new PlotUpgradeOffer(plot, LandPlot.Upgrade.MUSIC_BOX, corral.musicBox, true);
                    yield return new PlotUpgradeOffer(plot, LandPlot.Upgrade.AIR_NET, corral.airNet, true);
                    yield return new PlotUpgradeOffer(plot, LandPlot.Upgrade.SOLAR_SHIELD, corral.solarShield, true);
                    yield return new PlotUpgradeOffer(plot, LandPlot.Upgrade.PLORT_COLLECTOR, corral.plortCollector, true);
                    yield return new PlotUpgradeOffer(plot, LandPlot.Upgrade.FEEDER, corral.feeder, true);
                    break;
                case LandPlot.Id.GARDEN:
                    var garden = PlotMenu<GardenUI>(plot);
                    if (garden == null) break;
                    int ogden = scene.ProgressDirector.GetProgress(ProgressDirector.ProgressType.OGDEN_REWARDS);
                    yield return new PlotUpgradeOffer(plot, LandPlot.Upgrade.SOIL, garden.soil, true);
                    yield return new PlotUpgradeOffer(plot, LandPlot.Upgrade.SPRINKLER, garden.sprinkler, true);
                    yield return new PlotUpgradeOffer(plot, LandPlot.Upgrade.SCARESLIME, garden.scareslime, true);
                    yield return new PlotUpgradeOffer(plot, LandPlot.Upgrade.MIRACLE_MIX, garden.miracleMix, ogden >= 1);
                    yield return new PlotUpgradeOffer(plot, LandPlot.Upgrade.DELUXE_GARDEN, garden.deluxe, ogden >= 2);
                    break;
                case LandPlot.Id.COOP:
                    var coop = PlotMenu<CoopUI>(plot);
                    if (coop == null) break;
                    int mochi = scene.ProgressDirector.GetProgress(ProgressDirector.ProgressType.MOCHI_REWARDS);
                    yield return new PlotUpgradeOffer(plot, LandPlot.Upgrade.WALLS, coop.walls, true);
                    yield return new PlotUpgradeOffer(plot, LandPlot.Upgrade.FEEDER, coop.feeder, true);
                    yield return new PlotUpgradeOffer(plot, LandPlot.Upgrade.VITAMIZER, coop.vitamizer, true);
                    yield return new PlotUpgradeOffer(plot, LandPlot.Upgrade.DELUXE_COOP, coop.deluxe, mochi >= 2);
                    break;
                case LandPlot.Id.SILO:
                    var silo = PlotMenu<SiloUI>(plot);
                    if (silo == null) break;
                    yield return new PlotUpgradeOffer(plot, LandPlot.Upgrade.STORAGE2, silo.storage2, true);
                    yield return new PlotUpgradeOffer(plot, LandPlot.Upgrade.STORAGE3, silo.storage3, plot.HasUpgrade(LandPlot.Upgrade.STORAGE2));
                    yield return new PlotUpgradeOffer(plot, LandPlot.Upgrade.STORAGE4, silo.storage4, plot.HasUpgrade(LandPlot.Upgrade.STORAGE3));
                    break;
                case LandPlot.Id.INCINERATOR:
                    var incinerator = PlotMenu<IncineratorUI>(plot);
                    if (incinerator != null)
                        yield return new PlotUpgradeOffer(plot, LandPlot.Upgrade.ASH_TROUGH, incinerator.ashTrough, true);
                    break;
            }
        }

        static Dictionary<string, object> PlotShop(SceneContext scene, Dictionary<string, object> args)
        {
            string id = Arg(args, "plot_id");
            var location = PlotLocation(id);
            var plot = location == null ? null : location.GetComponentInChildren<LandPlot>();
            if (plot == null) return Fail("TARGET_UNAVAILABLE", "Use a loaded plot_id from sr_plots.");
            int coins = scene.PlayerState.GetCurrency();
            var types = plot.typeId != LandPlot.Id.EMPTY ? new Dictionary<string, object>[0]
                : PlotTypes(plot).Select(x => Row("plot_type", x.plotPrefab.GetComponent<LandPlot>().typeId.ToString(),
                    "price", x.cost, "affordable", x.cost >= 0 && x.cost <= coins)).ToArray();
            var upgrades = PlotUpgrades(scene, plot).Where(x => x.Price >= 0)
                .Select(x => Row("upgrade_id", x.Id.ToString(), "price", x.Price, "owned", x.Owned,
                    "available", x.Unlocked && !x.Owned, "affordable", x.Unlocked && !x.Owned && x.Price <= coins)).ToArray();
            return Row("plot_id", id, "plot_type", plot.typeId.ToString(), "currency", coins, "plot_types", types, "upgrades", upgrades);
        }

        static Dictionary<string, object> BuyPlot(SceneContext scene, Dictionary<string, object> args)
        {
            string id = Arg(args, "plot_id");
            var location = PlotLocation(id);
            var plot = location == null ? null : location.GetComponentInChildren<LandPlot>();
            if (plot == null || plot.typeId != LandPlot.Id.EMPTY || plot.transform.parent != location.transform)
                return Fail("TARGET_UNAVAILABLE", "Use a loaded, empty plot from sr_plots.");
            string type = Arg(args, "plot_type");
            var item = PlotTypes(plot).FirstOrDefault(x => Same(x.plotPrefab.GetComponent<LandPlot>().typeId.ToString(), type));
            if (item == null) return Fail("INVALID_PLOT_TYPE", "Use a plot_type from sr_plot_shop.");
            var player = scene.PlayerState;
            var error = CheckPrice(player, item.cost, args);
            if (error != null) return error;
            var target = item.plotPrefab.GetComponent<LandPlot>().typeId;
            var result = Row("plot_id", id, "plot_type", target.ToString(), "price", item.cost, "currency_before", player.GetCurrency());
            if (!Preview(args))
            {
                location.Replace(plot, item.plotPrefab);
                player.SpendCurrency(item.cost);
                if (target == LandPlot.Id.GARDEN) scene.TutorialDirector.MaybeShowPopup(TutorialDirector.Id.GARDEN);
                result["currency_after"] = player.GetCurrency();
                result["plot_type_after"] = scene.GameModel.GetLandPlotModel(id).typeId.ToString();
            }
            return result;
        }

        static Dictionary<string, object> BuyPlotUpgrade(SceneContext scene, Dictionary<string, object> args)
        {
            string id = Arg(args, "plot_id");
            var location = PlotLocation(id);
            var plot = location == null ? null : location.GetComponentInChildren<LandPlot>();
            if (plot == null) return Fail("TARGET_UNAVAILABLE", "Use a loaded plot_id from sr_plots.");
            string upgrade = Arg(args, "upgrade_id");
            var item = PlotUpgrades(scene, plot).FirstOrDefault(x => Same(x.Id.ToString(), upgrade));
            if (item == null || item.Price < 0 || item.Owned || !item.Unlocked)
                return Fail("UPGRADE_UNAVAILABLE", "Use an available upgrade_id from sr_plot_shop.");
            var player = scene.PlayerState;
            var error = CheckPrice(player, item.Price, args);
            if (error != null) return error;
            var result = Row("plot_id", id, "upgrade_id", item.Id.ToString(), "price", item.Price, "currency_before", player.GetCurrency());
            if (!Preview(args))
            {
                player.SpendCurrency(item.Price);
                plot.AddUpgrade(item.Id);
                result["owned"] = plot.HasUpgrade(item.Id);
                result["currency_after"] = player.GetCurrency();
            }
            return result;
        }
    }
}
