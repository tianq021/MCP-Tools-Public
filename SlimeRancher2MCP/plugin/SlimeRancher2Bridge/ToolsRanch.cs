using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Il2Cpp;
using Il2CppMonomiPark.SlimeRancher.DataModel;
using Row = System.Collections.Generic.Dictionary<string, object>;

namespace SlimeRancher2Bridge
{
    internal static class ToolsRanch
    {
        internal static void Register(BridgeServer server)
        {
            server.Register("sr2_corrals", args => WithGame(game => Corrals(game, args)), mainThread: true);
            server.Register("sr2_slime_diet", args => WithGame(game => Diets(game, args)), mainThread: true);
            server.Register("sr2_gadgets", args => WithGame(game => Gadgets(game, args, false)), mainThread: true);
            server.Register("sr2_drones", args => WithGame(game => Gadgets(game, args, true)), mainThread: true);
        }

        internal static Row WithGame(Func<GameModel, Row> read) => ToolsNavigation.WithScene(raw =>
        {
            var game = ((SceneContext)raw).GameModel;
            return game == null || !game.isInitialized ? Unavailable("game_model_unavailable") : read(game);
        });

        static Row Unavailable(string reason) => new() { ["available"] = false, ["reason"] = reason };

        static Row Corrals(GameModel game, Row args)
        {
            var plots = game.landPlots;
            if (plots == null) return Unavailable("plots_unavailable");
            var pens = new List<(string Id, LandPlotModel Plot)>();
            foreach (var entry in plots)
                if (entry.Value != null && entry.Value.typeId == LandPlot.Id.CORRAL)
                    pens.Add((entry.Key, entry.Value));
            pens.Sort((a, b) => string.CompareOrdinal(a.Id, b.Id));
            return ToolsWorld.Page(args, pens.Count, pens, pen =>
            {
                var tracked = pen.Plot.trackedActors?.trackedActorIds;
                var counts = new SortedDictionary<string, int>(StringComparer.Ordinal);
                int unresolved = 0, other = 0;
                if (tracked != null)
                {
                    foreach (var id in tracked)
                    {
                        if (!game.TryGetIdentifiableModel(id, out IdentifiableModel actor) || actor?.ident == null)
                        {
                            unresolved++;
                            continue;
                        }
                        var slime = actor.ident.TryCast<SlimeDefinition>();
                        if (slime == null) { other++; continue; }
                        string type = slime.ReferenceId;
                        if (string.IsNullOrEmpty(type)) { unresolved++; continue; }
                        counts.TryGetValue(type, out int count);
                        counts[type] = count + 1;
                    }
                }
                return new Row
                {
                    ["id"] = pen.Id, ["loaded"] = pen.Plot.gameObj != null,
                    ["tracked_count"] = tracked?.Count, ["slimes"] = tracked == null ? null : counts,
                    ["other_count"] = tracked == null ? null : other,
                    ["unresolved_count"] = tracked == null ? null : unresolved
                };
            });
        }

        static Row Diets(GameModel game, Row args)
        {
            var definitions = game.slimeDefinitions?.Slimes;
            if (definitions == null) return Unavailable("slime_catalog_unavailable");
            string query = args.TryGetValue("query", out var q) ? Convert.ToString(q).Trim() : "";
            bool includeLargos = !args.TryGetValue("include_largos", out var largos) || largos is true;
            var matches = new List<SlimeDefinition>();
            foreach (var slime in definitions)
            {
                if (slime == null || (!includeLargos && slime.IsLargo)) continue;
                if (query.Length == 0 || (slime.ReferenceId ?? "").Contains(query, StringComparison.OrdinalIgnoreCase)
                    || (slime.name ?? "").Contains(query, StringComparison.OrdinalIgnoreCase)) matches.Add(slime);
            }
            matches.Sort((a, b) => string.CompareOrdinal(a.ReferenceId, b.ReferenceId));
            return ToolsWorld.Page(args, definitions.Length, matches, slime =>
            {
                var diet = slime.Diet;
                return new Row
                {
                    ["id"] = slime.ReferenceId, ["name"] = slime.name, ["is_largo"] = slime.IsLargo,
                    ["food_groups"] = diet?.MajorFoodIdentifiableTypeGroups?.Where(v => v != null).Select(v => v.name).ToArray(),
                    ["additional_foods"] = diet?.AdditionalFoodIdents?.Where(v => v != null).Select(v => v.ReferenceId).ToArray(),
                    ["favorites"] = diet?.FavoriteIdents?.Where(v => v != null).Select(v => v.ReferenceId).ToArray(),
                    ["products"] = diet?.ProduceIdents?.Where(v => v != null).Select(v => v.ReferenceId).ToArray()
                };
            });
        }

        static Row Gadgets(GameModel game, Row args, bool stationsOnly)
        {
            var gadgets = game.AllGadgets();
            if (gadgets == null) return Unavailable("gadgets_unavailable");
            string query = args.TryGetValue("query", out var q) ? Convert.ToString(q).Trim() : "";
            var matches = new List<GadgetModel>();
            int total = 0;
            foreach (var gadget in gadgets)
            {
                if (gadget == null) continue;
                GadgetModel model = gadget;
                if (stationsOnly)
                {
                    model = gadget.TryCast<DroneStationGadgetModel>();
                    if (model == null) continue;
                }
                total++;
                if (query.Length == 0 || (model.ident?.ReferenceId ?? "").Contains(query, StringComparison.OrdinalIgnoreCase))
                    matches.Add(model);
            }
            matches.Sort((a, b) => a.actorId.Value.CompareTo(b.actorId.Value));
            return ToolsWorld.Page(args, total, matches, gadget =>
            {
                var transform = gadget.Transform;
                var pos = transform != null ? transform.position : gadget.lastPosition;
                var row = new Row
                {
                    ["id"] = gadget.actorId.Value.ToString(CultureInfo.InvariantCulture),
                    ["gadget"] = gadget.ident?.ReferenceId, ["scene_group"] = gadget.SceneGroup?.ReferenceId,
                    ["position"] = new { pos.x, pos.y, pos.z }, ["position_source"] = transform != null ? "live" : "last_position",
                    ["pre_placed"] = gadget.IsPrePlaced
                };
                if (gadget is DroneStationGadgetModel station && stationsOnly)
                {
                    row["initialized"] = station.Initialized;
                    row["station_type"] = station.Type.ToString();
                    row["at_station"] = station.IsDroneAtStation?.Value;
                    double? until = station.EnergyDepleteTime?.Value;
                    row["energy_deplete_time"] = until.HasValue && double.IsFinite(until.Value) ? until : null;
                    var task = station.TaskData;
                    row["task"] = task == null ? null : new
                    {
                        source = task.SourceType.ToString(), target = task.TargetType.ToString(),
                        item = task.TargetIdentType?.ReferenceId, sink = task.SinkType.ToString()
                    };
                    row["explorer_state"] = station.Program?.State.ToString();
                }
                return row;
            });
        }
    }
}
