using System;
using System.Collections.Generic;
using Il2Cpp;
using Il2CppMonomiPark.SlimeRancher.DataModel;
using Il2CppMonomiPark.SlimeRancher.Pedia;
using Il2CppMonomiPark.SlimeRancher.World.ResearchDrone;
using UnityEngine;
using Row = System.Collections.Generic.Dictionary<string, object>;

namespace SlimeRancher2Bridge
{
    internal static class ToolsExploration
    {
        internal static void Register(BridgeServer server)
        {
            server.Register("sr2_resource_nodes", args => ToolsRanch.WithGame(game => Nodes(game, args)), mainThread: true);
            server.Register("sr2_research_logs", args => ToolsRanch.WithGame(game => Logs(game, args)), mainThread: true);
            server.Register("sr2_teleporters", args => ToolsRanch.WithGame(game => Teleporters(game, args)), mainThread: true);
            server.Register("sr2_pedia", Pedia, mainThread: true);
        }

        static string Text(Row args, string key) => args.TryGetValue(key, out var value) ? Convert.ToString(value).Trim() : "";
        static bool Enabled(Row args, string key) => args.TryGetValue(key, out var value) && value is true;

        static object Position(Component component)
        {
            if (component == null) return null;
            var p = component.transform.position;
            return new { p.x, p.y, p.z };
        }

        // Join active components to the exact save model through their native pointer.
        static Dictionary<IntPtr, T> Loaded<T>(Func<T, IntPtr> key) where T : Component
        {
            var index = new Dictionary<IntPtr, T>();
            foreach (var component in UnityEngine.Object.FindObjectsByType<T>(FindObjectsSortMode.None))
            {
                if (component == null) continue;
                var ptr = key(component);
                if (ptr != IntPtr.Zero) index[ptr] = component;
            }
            return index;
        }

        static Row Nodes(GameModel game, Row args)
        {
            var loaded = Loaded<ResourceNodeSpawner>(s => s._model?.Pointer ?? IntPtr.Zero);
            string state = Text(args, "state");
            bool loadedOnly = Enabled(args, "loaded_only");
            return Records(args, game.resourceNodeSpawnerModels,
                m => (!loadedOnly || loaded.ContainsKey(m.Pointer))
                    && (state.Length == 0 || string.Equals(state, m.nodeState.ToString(), StringComparison.OrdinalIgnoreCase)),
                m =>
                {
                    loaded.TryGetValue(m.Pointer, out var spawner);
                    var drops = m.resourcesToSpawn;
                    List<string> items = null;
                    if (drops != null)
                    {
                        items = new List<string>();
                        foreach (var item in drops) items.Add(item?.ReferenceId);
                    }
                    return new Row
                    {
                        ["state"] = m.nodeState.ToString(), ["definition"] = m.resourceNodeDefinition?.name,
                        ["resources_to_spawn"] = items, ["loaded"] = spawner != null, ["position"] = Position(spawner)
                    };
                });
        }

        static Row Logs(GameModel game, Row args)
        {
            var loaded = Loaded<ResearchDroneController>(c => c._model?.Pointer ?? IntPtr.Zero);
            bool unreadOnly = Enabled(args, "unread_only");
            return Records(args, game.researchDrones, m => !unreadOnly || !m.read, m =>
            {
                loaded.TryGetValue(m.Pointer, out var drone);
                var entry = drone?.ResearchDroneEntry;
                return new Row
                {
                    ["read"] = m.read, ["entry_id"] = entry?.ReferenceId, ["page_count"] = entry?.Pages?.Length,
                    ["loaded"] = drone != null, ["position"] = Position(drone)
                };
            });
        }

        static Row Teleporters(GameModel game, Row args)
        {
            string group = Text(args, "source_group");
            return Records(args, game.teleporters, m => group.Length == 0 ||
                string.Equals(m.source?.SceneGroup?.ReferenceId, group, StringComparison.OrdinalIgnoreCase), m =>
                {
                    SortedDictionary<string, object> destinations = null;
                    var links = m.destinations;
                    if (links != null)
                    {
                        destinations = new(StringComparer.Ordinal);
                        foreach (var link in links)
                            destinations[link.Key] = new { node = link.Value?.TeleporterNodeModel?.NodeId, scene_group = link.Value?.SceneGroup?.ReferenceId };
                    }
                    return new Row
                    {
                        ["source_node"] = m.source?.TeleporterNodeModel?.NodeId, ["source_group"] = m.source?.SceneGroup?.ReferenceId,
                        ["force_scene_transition"] = m.ForceSceneTransition, ["destinations"] = destinations
                    };
                });
        }

        static Row Records<T>(Row args, Il2CppSystem.Collections.Generic.Dictionary<string, T> records,
            Func<T, bool> include, Func<T, Row> describe) where T : class
        {
            if (records == null) return new() { ["available"] = false, ["reason"] = "records_unavailable" };
            var matches = new List<(string Id, T Model)>();
            foreach (var entry in records)
                if (entry.Value != null && include(entry.Value)) matches.Add((entry.Key, entry.Value));
            matches.Sort((a, b) => string.CompareOrdinal(a.Id, b.Id));
            return ToolsWorld.Page(args, records.Count, matches, match =>
            {
                var row = describe(match.Model);
                row["id"] = match.Id;
                return row;
            });
        }

        static Row Pedia(Row args) => ToolsNavigation.WithScene(raw =>
        {
            var director = ((SceneContext)raw).PediaDirector;
            var catalog = director?._pediaModel?.unlocked == null ? null : director.AllEntries();
            if (catalog == null) return new() { ["available"] = false, ["reason"] = "pedia_unavailable" };
            string query = Text(args, "query");
            bool lockedOnly = Enabled(args, "locked_only");
            int total = 0, unlocked = 0;
            var matches = new List<(string Id, string Name, bool Unlocked, PediaEntry Entry)>();
            foreach (var entry in Il2CppUtil.Items(catalog))
            {
                if (entry == null) continue;
                bool known = director.IsUnlocked(entry);
                total++;
                if (known) unlocked++;
                if (known && lockedOnly) continue;
                string id = entry.PersistenceId, name = entry.name;
                if (query.Length == 0 || (id ?? "").Contains(query, StringComparison.OrdinalIgnoreCase)
                    || (name ?? "").Contains(query, StringComparison.OrdinalIgnoreCase))
                    matches.Add((id, name, known, entry));
            }
            matches.Sort((a, b) => string.CompareOrdinal(a.Id, b.Id));
            var result = ToolsWorld.Page(args, total, matches, m => new Row
            {
                ["id"] = m.Id, ["name"] = m.Name, ["title"] = Il2CppUtil.Localized(m.Entry.Title), ["unlocked"] = m.Unlocked
            });
            result["unlocked_count"] = unlocked;
            return result;
        });
    }
}
