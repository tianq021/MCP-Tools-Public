using System;
using System.Collections.Generic;
using System.Linq;
using Il2Cpp;
using Il2CppMonomiPark.SlimeRancher.DataModel;
using UnityEngine;
using Row = System.Collections.Generic.Dictionary<string, object>;

namespace SlimeRancher2Bridge
{
    internal static class ToolsWorld
    {
        internal static void Register(BridgeServer server)
        {
            server.Register("sr2_gordos", Gordos, mainThread: true);
            server.Register("sr2_pods", Pods, mainThread: true);
            server.Register("sr2_doors", Doors, mainThread: true);
            server.Register("sr2_plots", Plots, mainThread: true);
            server.Register("sr2_zone_buildings", ZoneBuildings, mainThread: true);
        }

        static Row Gordos(Row args) => ListModels(args, "state", g => g.gordos, m => m.gameObj,
            m => m.HasPopped() ? "popped" : m.GordoSeen ? "seen" : "unseen",
            m => new Row
            {
                ["type"] = m.identifiableType?.ReferenceId,
                ["seen"] = m.GordoSeen,
                ["feed_count"] = m.GordoEatenCount >= 0 ? m.GordoEatenCount : null,
                ["feed_target"] = m.targetCount > 0 ? m.targetCount : null
            });

        static Row Pods(Row args) => ListModels(args, "state", g => g.pods, m => m.gameObj,
            m => m.state?.Value.ToString(), m => new Row());

        static Row Doors(Row args) => ListModels(args, "state", g => g.doors, m => m.gameObj,
            m => m.state.ToString(), m => new Row());

        static Row Plots(Row args) => ListModels(args, "type", g => g.landPlots, m => m.gameObj,
            m => m.typeId.ToString(), DescribePlot);

        static Row DescribePlot(LandPlotModel model)
        {
            var upgrades = new List<string>();
            if (model.upgrades != null)
                foreach (var upgrade in model.upgrades) upgrades.Add(upgrade.ToString());
            upgrades.Sort(StringComparer.Ordinal);
            return new Row
            {
                ["upgrades"] = model.upgrades == null ? null : upgrades,
                ["crop"] = model.resourceGrowerDefinition?.PrimaryResourceType?.ReferenceId,
                ["tracked_actor_count"] = model.trackedActors?.trackedActorIds?.Count
            };
        }

        static Row ZoneBuildings(Row args) => ToolsNavigation.WithScene(raw =>
        {
            var scene = (SceneContext)raw;
            var game = scene.GameModel;
            var mappings = scene.MapDirector?.ZoneMappings;
            if (game?.landPlots == null || !game.isInitialized || mappings == null)
                return new Row { ["available"] = false, ["reason"] = "buildings_unavailable" };
            var gadgets = game.AllGadgets();
            if (gadgets == null) return new Row { ["available"] = false, ["reason"] = "gadgets_unavailable" };

            string current = scene.PlayerZoneTracker?.GetCurrentZone()?.ReferenceId;
            string query = Convert.ToString(args.GetValueOrDefault("zone", "")).Trim();
            if (query.Length == 0) query = current;
            if (string.IsNullOrEmpty(query))
                return new Row { ["available"] = false, ["reason"] = "current_zone_unknown" };
            var titles = new Dictionary<string, Row>(StringComparer.OrdinalIgnoreCase);
            Row selected = null;
            foreach (var mapping in mappings)
            {
                var zone = mapping?.Zone;
                if (string.IsNullOrEmpty(zone?.ReferenceId)) continue;
                string title = Il2CppUtil.Localized(zone.Name);
                bool exact = string.Equals(zone.ReferenceId, query, StringComparison.OrdinalIgnoreCase);
                if (!exact && !string.Equals(title, query, StringComparison.OrdinalIgnoreCase)) continue;
                var match = new Row { ["id"] = zone.ReferenceId, ["title"] = title,
                    ["unlocked"] = scene.IsZoneUnlocked(zone), ["ranch_zone"] = zone.IsRanchZone };
                if (exact) { selected = match; break; }
                titles[zone.ReferenceId] = match;
            }
            if (selected == null && titles.Count == 1) selected = titles.Values.First();
            if (selected == null)
                return new Row { ["available"] = false, ["reason"] = titles.Count > 1 ? "ambiguous_zone" : "zone_not_found",
                    ["candidates"] = titles.Values.ToList() };
            string selectedId = (string)selected["id"];
            bool includeEmpty = args.GetValueOrDefault("include_empty") is true;
            string currentGroup = SystemContext.Instance?.SceneLoader?.CurrentSceneGroup?.ReferenceId;
            var rows = new List<Row>();
            int unassignedPlots = 0, unassignedGadgets = 0, total = 0;

            foreach (var entry in game.landPlots)
            {
                var plot = entry.Value;
                if (plot == null || (!includeEmpty && plot.typeId == LandPlot.Id.EMPTY)) continue;
                total++;
                var obj = plot.gameObj;
                Vector3? position = obj == null ? null : obj.transform.position;
                var row = new Row();
                ToolsZone.AddZone(row, obj, position);
                string id = row["zone"] as string;
                if (string.IsNullOrEmpty(id)) unassignedPlots++;
                if (id != selectedId) continue;
                foreach (var field in DescribePlot(plot)) row[field.Key] = field.Value;
                row["kind"] = "plot";
                row["id"] = entry.Key;
                row["type"] = plot.typeId.ToString();
                row["loaded"] = obj != null;
                row["position"] = position.HasValue ? new { position.Value.x, position.Value.y, position.Value.z } : null;
                rows.Add(row);
            }
            foreach (var gadget in gadgets)
            {
                if (gadget == null) continue;
                total++;
                var transform = gadget.Transform;
                var sceneGroup = gadget.SceneGroup?.ReferenceId;
                bool useLastPosition = transform == null && !string.IsNullOrEmpty(currentGroup) && sceneGroup == currentGroup;
                Vector3? position = transform != null ? transform.position : useLastPosition ? gadget.lastPosition : null;
                var row = new Row();
                ToolsZone.AddZone(row, transform != null ? transform.gameObject : null, position);
                string id = row["zone"] as string;
                if (string.IsNullOrEmpty(id)) unassignedGadgets++;
                if (id != selectedId) continue;
                row["kind"] = "gadget";
                row["id"] = gadget.actorId.Value.ToString(System.Globalization.CultureInfo.InvariantCulture);
                row["type"] = gadget.ident?.ReferenceId;
                row["loaded"] = transform != null;
                row["zone_source"] = useLastPosition ? "last_position" : "live";
                row["pre_placed"] = gadget.IsPrePlaced;
                row["position"] = position.HasValue ? new { position.Value.x, position.Value.y, position.Value.z } : null;
                rows.Add(row);
            }
            var ordered = rows.OrderBy(row => (string)row["kind"], StringComparer.Ordinal)
                .ThenBy(row => (string)row["id"], StringComparer.Ordinal).ToList();
            var result = Page(args, total, ordered, row => row);
            result["zone"] = selected;
            result["zone_loaded"] = selectedId == current || rows.Count > 0 || ToolsZone.IsLoaded(selectedId);
            result["unassigned_plot_count_worldwide"] = unassignedPlots;
            result["unassigned_gadget_count_worldwide"] = unassignedGadgets;
            return result;
        });

        // The save-model dictionaries also contain records without a current Unity object.
        static Row ListModels<T>(Row args, string filterKey,
            Func<GameModel, Il2CppSystem.Collections.Generic.Dictionary<string, T>> select,
            Func<T, GameObject> gameObject, Func<T, string> category, Func<T, Row> details) where T : class
            => ToolsNavigation.WithScene(raw =>
            {
                var game = ((SceneContext)raw).GameModel;
                if (game == null || !game.isInitialized)
                    return new Row { ["available"] = false, ["reason"] = "game_model_unavailable" };
                var records = select(game);
                if (records == null)
                    return new Row { ["available"] = false, ["reason"] = "records_unavailable" };

                string filter = args.TryGetValue(filterKey, out var f) ? Convert.ToString(f).Trim() : "";
                bool loadedOnly = args.TryGetValue("loaded_only", out var loaded) && loaded is true;
                var matches = new List<(string Id, T Model, GameObject Object, string Category)>();
                foreach (var entry in records)
                {
                    var model = entry.Value;
                    if (model == null) continue;
                    var obj = gameObject(model);
                    if (loadedOnly && obj == null) continue;
                    string value = category(model);
                    if (filter.Length > 0 && !string.Equals(value, filter, StringComparison.OrdinalIgnoreCase)) continue;
                    matches.Add((entry.Key, model, obj, value));
                }
                matches.Sort((a, b) => string.CompareOrdinal(a.Id, b.Id));
                return Page(args, records.Count, matches, match =>
                {
                    var row = details(match.Model);
                    row["id"] = match.Id;
                    row[filterKey] = match.Category;
                    row["loaded"] = match.Object != null;
                    row["position"] = null;
                    if (match.Object != null)
                    {
                        var p = match.Object.transform.position;
                        row["position"] = new { p.x, p.y, p.z };
                    }
                    return row;
                });
            });

        internal static Row Page<T>(Row args, int recordCount, List<T> sorted, Func<T, Row> describe)
        {
            int offset = args.TryGetValue("offset", out var o) ? Math.Max(0, Convert.ToInt32(o)) : 0;
            int limit = args.TryGetValue("limit", out var l) ? Math.Clamp(Convert.ToInt32(l), 1, 100) : 25;
            var rows = sorted.Skip(offset).Take(limit).Select(describe).ToList();
            int end = offset + rows.Count;
            return new Row
            {
                ["available"] = true, ["record_count"] = recordCount, ["matched_count"] = sorted.Count,
                ["next_offset"] = end < sorted.Count ? end : null, ["items"] = rows
            };
        }
    }
}
