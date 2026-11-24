using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Il2Cpp;
using Il2CppMonomiPark.SlimeRancher.DataModel;
using UnityEngine;
using Row = System.Collections.Generic.Dictionary<string, object>;

namespace SlimeRancher2Bridge
{
    internal static class RanchControls
    {
        sealed class Control
        {
            public readonly string Kind, OwnerId;
            public readonly MonoBehaviour Device;
            public readonly int? Panel;
            public Control(string kind, string ownerId, MonoBehaviour device, int? panel = null)
            {
                Kind = kind;
                OwnerId = ownerId;
                Device = device;
                Panel = panel;
            }
            public string Id => Kind + ":" + Uri.EscapeDataString(OwnerId) +
                (Panel.HasValue ? ":" + Panel.Value.ToString(CultureInfo.InvariantCulture) : "");
        }

        internal static bool HasSiloPanel(LandPlotModel plot, int panel) => panel switch
        {
            0 => true,
            1 => plot.HasUpgrade(LandPlot.Upgrade.STORAGE2),
            2 => plot.HasUpgrade(LandPlot.Upgrade.STORAGE3),
            3 => plot.HasUpgrade(LandPlot.Upgrade.STORAGE4),
            _ => false
        };

        static IEnumerable<T> Active<T>() where T : MonoBehaviour =>
            UnityEngine.Object.FindObjectsByType<T>(FindObjectsSortMode.None).Where(c => c != null && c.isActiveAndEnabled);

        static IEnumerable<Control> Find(GameModel game, string kind)
        {
            if (kind == "" || kind == "resource_collector")
            {
                foreach (var button in Active<ResourceCollectorActivator>())
                {
                    var collector = button.Collector;
                    var model = collector?._model;
                    if (model != null && collector.isActiveAndEnabled)
                        yield return new("resource_collector", model.actorId.Value.ToString(CultureInfo.InvariantCulture), button);
                }
            }
            if (kind == "resource_collector" || game.landPlots == null) yield break;
            var ids = new Dictionary<IntPtr, string>();
            foreach (var pair in game.landPlots)
                if (pair.Value != null) ids[pair.Value.Pointer] = pair.Key;

            if (kind == "" || kind == "feeder")
                foreach (var feeder in Active<SlimeFeeder>())
                    if (feeder._model?.typeId == LandPlot.Id.CORRAL && ids.TryGetValue(feeder._model.Pointer, out var id))
                        yield return new("feeder", id, feeder);
            if (kind == "" || kind == "plort_collector")
                foreach (var button in Active<PlortCollectorActivator>())
                {
                    var collector = button.Collector;
                    var model = collector?._model;
                    if (model?.typeId == LandPlot.Id.CORRAL && collector.isActiveAndEnabled && ids.TryGetValue(model.Pointer, out var id))
                        yield return new("plort_collector", id, button);
                }
            if (kind == "" || kind == "silo_port")
                foreach (var button in Active<SiloStorageActivator>())
                {
                    var model = button._landPlotModel;
                    if (model?.typeId == LandPlot.Id.SILO && ids.TryGetValue(model.Pointer, out var id)
                        && button.SiloCatcher != null && HasSiloPanel(model, button.ActivatorIdx))
                        yield return new("silo_port", id, button, button.ActivatorIdx);
                }
        }

        internal static Row List(GameModel game, Row args)
        {
            var all = Find(game, "").OrderBy(c => c.Id, StringComparer.Ordinal).ToList();
            var kind = (args.GetValueOrDefault("kind") as string)?.Trim() ?? "";
            var matching = all.Where(c => kind.Length == 0 || c.Kind == kind).ToList();
            return ToolsWorld.Page(args, all.Count, matching, control =>
            {
                var p = control.Device.transform.position;
                var row = new Row { ["control_id"] = control.Id, ["kind"] = control.Kind, ["owner_id"] = control.OwnerId,
                    ["position"] = new { p.x, p.y, p.z } };
                if (control.Panel.HasValue) row["panel"] = control.Panel.Value;
                if (control.Device is SiloStorageActivator panel) row["slot"] = panel.SiloCatcher.SlotIdx;
                if (control.Device is SlimeFeeder feeder && feeder._storage?.GetRelevantAmmo()?.Slots is { Length: > 0 })
                {
                    row["food_count"] = feeder.GetFoodCount();
                    row["food_id"] = feeder.GetFoodId()?.ReferenceId;
                }
                return row;
            });
        }

        internal static Row Use(SceneContext scene, Row args, bool preview, string kind)
        {
            var id = (args.GetValueOrDefault("control_id") as string)?.Trim();
            var result = new Row { ["control_id"] = id, ["applied"] = false };
            var matches = Find(scene.GameModel, kind).Where(c => c.Id == id).Take(2).ToList();
            if (matches.Count != 1)
            {
                result["reason"] = matches.Count == 0 ? "loaded_control_not_found" : "ambiguous_control_id";
                return result;
            }
            switch (matches[0].Device)
            {
                case SlimeFeeder feeder:
                    var ammo = feeder._storage?.GetRelevantAmmo();
                    if (ammo?.Slots is not { Length: > 0 }) { result["reason"] = "feeder_storage_unavailable"; break; }
                    int food = feeder.GetFoodCount();
                    var foodId = feeder.GetFoodId()?.ReferenceId;
                    result["food_id"] = foodId;
                    result["food_before"] = food;
                    if (food <= 0 || string.IsNullOrEmpty(foodId)) { result["reason"] = "no_food"; break; }
                    if (preview) break;
                    // The full native operation ejects, debits slot zero, and updates pending feeds.
                    feeder.ProcessFeedOperation(true);
                    int remaining = feeder.GetFoodCount();
                    result["food_after"] = remaining;
                    result["food_id_after"] = feeder.GetFoodId()?.ReferenceId;
                    result["dispensed"] = food - remaining;
                    result["applied"] = remaining == food - 1;
                    if (remaining != food - 1) result["reason"] = "dispense_unconfirmed";
                    break;
                case PlortCollectorActivator plort:
                    PressCollector(result, preview, plort.Activate, () => plort._nextAllowedActivationTime,
                        () => plort.Collector._forceCollectUntil);
                    break;
                case ResourceCollectorActivator resource:
                    PressCollector(result, preview, resource.Activate, () => resource._nextAllowedActivationTime,
                        () => resource.Collector._forceCollectUntil);
                    break;
                case SiloStorageActivator panel:
                    int before = panel.SiloCatcher.SlotIdx;
                    result["panel"] = panel.ActivatorIdx;
                    result["slot_before"] = before;
                    if (preview) break;
                    panel.Activate();
                    int after = panel.SiloCatcher.SlotIdx;
                    result["slot_after"] = after;
                    result["applied"] = after != before;
                    if (after == before) result["reason"] = "slot_unchanged";
                    break;
            }
            return result;
        }

        static void PressCollector(Row result, bool preview, Action press, Func<double> cooldown, Func<double> collection)
        {
            double before = cooldown(), window = collection();
            result["cooldown_before"] = Finite(before);
            result["collection_until_before"] = Finite(window);
            if (preview) return;
            press();
            double after = cooldown(), until = collection();
            result["cooldown_after"] = Finite(after);
            result["collection_until_after"] = Finite(until);
            bool changed = (double.IsFinite(after) && after > before) || (double.IsFinite(until) && until > window);
            result["applied"] = changed;
            if (!changed) result["reason"] = "activation_unconfirmed";
        }

        static double? Finite(double value) => double.IsFinite(value) ? value : null;
    }
}
