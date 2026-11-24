using System;
using System.Collections.Generic;
using System.Linq;
using MonomiPark.SlimeRancher.DataModel;
using MonomiPark.SlimeRancher.Regions;
using UnityEngine;
using Object = UnityEngine.Object;

namespace SlimeRancherBridge
{
    static partial class RanchTools
    {
        public static void Register(BridgeServer server)
        {
            Read(server, "sr_player", Player);
            Read(server, "sr_time", Clock);
            Read(server, "sr_vacpack", Vacpack);
            Read(server, "sr_market", Market);
            Read(server, "sr_exchange", Exchange);
            Read(server, "sr_plots", Plots);
            Read(server, "sr_storage", Storage);
            Read(server, "sr_feeders", Feeders);
            Read(server, "sr_collectors", Collectors);
            Read(server, "sr_refinery", Refinery);
            Read(server, "sr_extractors", Extractors);
            Read(server, "sr_drones", Drones);
            Read(server, "sr_drone_options", DroneOptions);
            Read(server, "sr_gadgets", Gadgets);
            Read(server, "sr_gadget_sites", GadgetSites);
            Read(server, "sr_loose_items", LooseItems);
            Read(server, "sr_find_items", FindItems);
            Read(server, "sr_zones", Zones);
            Read(server, "sr_progress", Progress);
            Read(server, "sr_mail", Mail);
            Read(server, "sr_pedia", Pedia);
            Read(server, "sr_player_shop", PlayerShop);
            Read(server, "sr_item_catalog", ItemCatalog);
            RegisterActions(server);
        }

        static void Read(BridgeServer server, string name,
            Func<SceneContext, Dictionary<string, object>, Dictionary<string, object>> handler, bool write = false)
        {
            server.Register(name, args =>
            {
                SceneContext scene = SceneContext.Instance;
                return scene == null || scene.GameModel == null || scene.GameModel.expectingPush || scene.PlayerState == null
                    || scene.Player == null || Levels.isMainMenu()
                    ? Fail("NOT_READY", "Load a save and enter the game before calling this tool.")
                    : handler(scene, args);
            }, write);
        }

        static Dictionary<string, object> Row(params object[] fields)
        {
            var result = new Dictionary<string, object>(StringComparer.Ordinal);
            for (int i = 0; i < fields.Length; i += 2)
                result.Add((string)fields[i], fields[i + 1]);
            return result;
        }

        static Dictionary<string, object> Fail(string code, string message)
        {
            return Row("__error", BridgeServer.Error(code, message));
        }

        static string Arg(Dictionary<string, object> args, string key)
        {
            object value;
            return args.TryGetValue(key, out value) ? (value as string ?? "").Trim() : "";
        }

        static int Number(Dictionary<string, object> args, string key, int fallback)
        {
            object value;
            return args.TryGetValue(key, out value) ? Json.AsInt(value, fallback) : fallback;
        }

        static bool Flag(Dictionary<string, object> args, string key, bool fallback)
        {
            object value;
            return args.TryGetValue(key, out value) && value is bool ? (bool)value : fallback;
        }

        static bool Same(string left, string right)
        {
            return string.Equals(left, right, StringComparison.OrdinalIgnoreCase);
        }

        static bool Includes(string text, string query)
        {
            return String.IsNullOrEmpty(query) || (text ?? "").IndexOf(query, StringComparison.OrdinalIgnoreCase) >= 0;
        }

        static bool NamedEnum<T>(string text, out T result) where T : struct
        {
            return Enum.TryParse(text, true, out result) && Same(result.ToString(), text);
        }

        static int Limit(Dictionary<string, object> args, int fallback)
        {
            return Math.Min(200, Math.Max(1, Number(args, "limit", fallback)));
        }

        static Dictionary<string, object> Page<T>(string key, IEnumerable<T> source, Dictionary<string, object> args, int fallback)
        {
            var items = source.ToArray();
            int start = Math.Max(0, Number(args, "offset", 0));
            var page = items.Skip(start).Take(Limit(args, fallback)).ToArray();
            return Row(key, page, "total", items.Length, "offset", start,
                "next_offset", (long)start + page.Length < items.Length ? (object)(start + page.Length) : null);
        }

        static object Finite(double value)
        {
            return double.IsNaN(value) || double.IsInfinity(value) ? null : (object)Math.Round(value, 3);
        }

        static object[] Position(Vector3 p)
        {
            return new[] { Finite(p.x), Finite(p.y), Finite(p.z) };
        }

        static string Zone(Transform obj)
        {
            var zone = obj.GetComponentInParent<ZoneDirector>();
            return zone == null ? null : zone.zone.ToString();
        }

        static double Distance(SceneContext scene, Transform obj)
        {
            return Math.Round(Vector3.Distance(scene.Player.transform.position, obj.position), 2);
        }

        static Dictionary<string, object> Slot(Ammo ammo, int index)
        {
            Identifiable.Id item = ammo.GetSlotName(index);
            return Row("slot", index, "item_id", item.ToString(), "count", ammo.GetSlotCount(index),
                "capacity", ammo.GetSlotMaxCount(index));
        }

        static List<Dictionary<string, object>> Slots(Ammo ammo, bool includeEmpty)
        {
            var slots = new List<Dictionary<string, object>>();
            for (int i = 0; i < ammo.GetUsableSlotCount(); i++)
                if (includeEmpty || ammo.GetSlotCount(i) > 0)
                    slots.Add(Slot(ammo, i));
            return slots;
        }

        static Dictionary<string, object> Player(SceneContext s, Dictionary<string, object> args)
        {
            var p = s.PlayerState;
            var member = s.Player.GetComponent<RegionMember>();
            return Row("currency", p.GetCurrency(), "keys", p.GetKeys(), "health", p.GetCurrHealth(),
                "max_health", p.GetMaxHealth(), "energy", p.GetCurrEnergy(), "max_energy", p.GetMaxEnergy(),
                "radiation", p.GetCurrRad(), "ammo_mode", p.GetAmmoMode().ToString(),
                "position", Position(s.Player.transform.position), "zone", s.PlayerZoneTracker == null ?
                    Zone(s.Player.transform) : s.PlayerZoneTracker.GetCurrentZone().ToString(),
                "region_set", member == null ? null : member.setId.ToString(),
                "game_mode", s.GameModel.currGameMode.ToString(), "scene", s.gameObject.scene.name);
        }

        static Dictionary<string, object> Clock(SceneContext s, Dictionary<string, object> args)
        {
            var t = s.TimeDirector;
            return Row("day", t.CurrDay(), "hour", Finite(t.CurrHour()), "world_time", Finite(t.WorldTime()),
                "paused", t.HasPauser(), "fast_forwarding", t.IsFastForwarding(), "game_focused", Application.isFocused);
        }

        static Dictionary<string, object> Vacpack(SceneContext s, Dictionary<string, object> args)
        {
            var ammo = s.PlayerState.Ammo;
            return Row("mode", s.PlayerState.GetAmmoMode().ToString(), "selected_slot", ammo.GetSelectedAmmoIdx(),
                "usable_slots", ammo.GetUsableSlotCount(), "slots", Slots(ammo, Flag(args, "include_empty", true)));
        }

        static Dictionary<string, object> Market(SceneContext s, Dictionary<string, object> args)
        {
            var market = s.EconomyDirector;
            string query = Arg(args, "item_id");
            var prices = new List<Dictionary<string, object>>();
            foreach (Identifiable.Id id in Identifiable.PLORT_CLASS.OrderBy(v => v.ToString()))
            {
                int? price = market.GetCurrValue(id);
                if (price.HasValue && (query.Length == 0 || Same(id.ToString(), query)))
                    prices.Add(Row("item_id", id.ToString(), "name", Identifiable.GetName(id, false),
                        "unit_price", price.Value, "change", market.GetChangeInValue(id)));
            }
            return Row("market_closed", market.IsMarketShutdown(), "prices", prices,
                "loaded_deposit_points", Object.FindObjectsOfType<ScorePlort>().Count(x => x.isActiveAndEnabled));
        }

        static Dictionary<string, object> Exchange(SceneContext s, Dictionary<string, object> args)
        {
            var director = s.ExchangeDirector;
            var offers = new List<Dictionary<string, object>>();
            foreach (ExchangeDirector.OfferType type in Enum.GetValues(typeof(ExchangeDirector.OfferType)))
            {
                string id = director.GetOfferId(type);
                if (id == null) continue;
                var requests = director.GetOfferRequests(type);
                var rewards = director.GetOfferRewards(type);
                var expires = director.GetOfferExpirationTime(type);
                offers.Add(Row("offer_type", type.ToString(), "offer_id", id, "rancher", director.GetOfferRancherId(type),
                    "expires_in_world_seconds", expires.HasValue ? Finite(expires.Value) : null,
                    "expires_at_world_time", expires.HasValue ? Finite(s.TimeDirector.WorldTime() + expires.Value) : null,
                    "complete", requests != null && requests.All(x => x.IsComplete()),
                    "requests", (requests ?? new List<ExchangeDirector.RequestedItemEntry>()).Select(x =>
                        Row("item_id", x.id.ToString(), "progress", x.progress, "required", x.count,
                            "remaining", Math.Max(0, x.count - x.progress))).ToArray(),
                    "rewards", (rewards ?? new List<ExchangeDirector.ItemEntry>()).Select(x =>
                        Row("item_id", x.id.ToString(), "special_reward", x.specReward.ToString(), "count", x.count)).ToArray()));
            }
            return Row("offers", offers);
        }

        static Dictionary<string, object> Plots(SceneContext s, Dictionary<string, object> args)
        {
            string filter = Arg(args, "plot_id");
            var locations = Object.FindObjectsOfType<LandPlotLocation>()
                .GroupBy(x => x.id).ToDictionary(group => group.Key, group => group.First());
            var plots = new List<Dictionary<string, object>>();
            foreach (var pair in s.GameModel.AllLandPlots().OrderBy(x => x.Key, StringComparer.Ordinal))
            {
                if (filter.Length != 0 && !Same(filter, pair.Key)) continue;
                LandPlotLocation location;
                locations.TryGetValue(pair.Key, out location);
                var model = pair.Value;
                plots.Add(Row("plot_id", pair.Key, "type", model.typeId.ToString(),
                    "loaded", location != null, "position", location == null ? null : Position(location.transform.position),
                    "zone", location == null ? null : Zone(location.transform),
                    "crop", model.attachedResourceId.ToString(), "crop_expires_at_world_time", Finite(model.attachedDeathTime),
                    "upgrades", model.upgrades.Select(x => x.ToString()).OrderBy(x => x).ToArray(),
                    "feeding_operations_remaining", model.remainingFeedOperations, "feeder_speed", model.feederCycleSpeed.ToString()));
            }
            return Row("plots", plots, "total", plots.Count);
        }

        static string PlotId(Component component)
        {
            var plot = component.GetComponentInParent<LandPlotLocation>();
            return plot == null ? null : plot.id;
        }

        static IEnumerable<SiloStorage> ActiveStorage()
        {
            return Object.FindObjectsOfType<SiloStorage>().Where(x => x.isActiveAndEnabled && x.GetRelevantAmmo() != null);
        }

        // A silo's Ammo exposes every slot even before the upgrade opens its physical port.
        static SiloStorageActivator[] SiloPorts(SiloStorage storage)
        {
            var plot = storage.GetComponentInParent<LandPlot>();
            if (plot == null || plot.typeId != LandPlot.Id.SILO) return null;
            return plot.GetComponentsInChildren<SiloStorageActivator>()
                .Where(port => port.isActiveAndEnabled && port.siloCatcher != null && port.siloCatcher.isActiveAndEnabled
                    && port.siloCatcher.GetComponentInParent<SiloStorage>() == storage && port.siloSlotUIs != null)
                .OrderBy(port => port.activatorIdx).ToArray();
        }

        static IEnumerable<int> PortSlots(SiloStorageActivator port)
        {
            return port.siloSlotUIs.Where(ui => ui != null).Select(ui => ui.slotIdx);
        }

        static bool CanUseStorageSlot(SiloStorage storage, int slot)
        {
            var ports = SiloPorts(storage);
            return ports == null || ports.Any(port => PortSlots(port).Contains(slot));
        }

        static int FreeSpaceForItem(Ammo ammo, Identifiable.Id item, int slot)
        {
            if (!ammo.CouldAddToSlot(item, slot, false)) return 0;
            return Math.Max(0, ammo.GetSlotMaxCount(item, slot) - ammo.GetSlotCount(slot));
        }

        static Dictionary<string, object> StorageRow(SceneContext s, SiloStorage storage, bool includeEmpty,
            bool includeLocked = false, Identifiable.Id item = Identifiable.Id.NONE)
        {
            var ammo = storage.GetRelevantAmmo();
            var ports = SiloPorts(storage);
            var unlocked = ports == null ? null : new HashSet<int>(ports.SelectMany(PortSlots));
            int total = ammo.GetUsableSlotCount();
            int usable = 0;
            var slots = new List<Dictionary<string, object>>();
            for (int i = 0; i < total; i++)
            {
                bool accessible = unlocked == null || unlocked.Contains(i);
                if (accessible) usable++;
                if ((!accessible && !includeLocked) || (!includeEmpty && ammo.GetSlotCount(i) == 0)) continue;
                var slot = Slot(ammo, i);
                slot["accessible"] = accessible;
                if (item != Identifiable.Id.NONE)
                {
                    int space = accessible ? FreeSpaceForItem(ammo, item, i) : 0;
                    slot["can_receive"] = space > 0;
                    slot["free_space_for_item"] = space;
                }
                slots.Add(slot);
            }
            var row = Row("storage_id", storage.GetInstanceID(), "storage_type", storage.type.ToString(),
                "name", storage.name, "plot_id", PlotId(storage), "zone", Zone(storage.transform),
                "position", Position(storage.transform.position), "distance", Distance(s, storage.transform),
                "slots", slots.ToArray(), "usable_slot_count", usable, "locked_slot_count", total - usable);
            if (ports != null)
                row["ports"] = ports.Select(port => Row("port", port.activatorIdx, "position", Position(port.transform.position),
                    "facing", Position(port.siloCatcher.transform.forward), "active_slot", port.siloCatcher.slotIdx,
                    "slots", PortSlots(port).Where(slot => slot >= 0 && slot < total).ToArray())).ToArray();
            return row;
        }

        static Dictionary<string, object> Storage(SceneContext s, Dictionary<string, object> args)
        {
            string type = Arg(args, "storage_type");
            string plot = Arg(args, "plot_id");
            string itemId = Arg(args, "item_id");
            Identifiable.Id item = Identifiable.Id.NONE;
            if (itemId.Length > 0 && !ParseItem(itemId, out item))
                return Fail("INVALID_ITEM", "Use an item_id from sr_item_catalog.");
            var selection = ActiveStorage().Where(x => (type.Length == 0 || Same(x.type.ToString(), type))
                && (plot.Length == 0 || Same(PlotId(x), plot))).OrderBy(x => Distance(s, x.transform)).ThenBy(x => x.GetInstanceID());
            bool includeEmpty = Flag(args, "include_empty", true);
            bool includeLocked = Flag(args, "include_locked", false);
            var result = Page("storage", selection.Select(x => StorageRow(s, x, includeEmpty, includeLocked, item)), args, 45);
            if (item != Identifiable.Id.NONE) result["checked_item_id"] = item.ToString();
            return result;
        }

        static Dictionary<string, object> Feeders(SceneContext s, Dictionary<string, object> args)
        {
            string plot = Arg(args, "plot_id");
            var feeders = Object.FindObjectsOfType<SlimeFeeder>()
                .Where(x => x.isActiveAndEnabled && (plot.Length == 0 || Same(PlotId(x), plot)));
            return Row("feeders", feeders.Select(x => Row("feeder_id", x.GetInstanceID(), "plot_id", PlotId(x),
                "position", Position(x.transform.position), "zone", Zone(x.transform), "food_id", x.GetFoodId().ToString(),
                "food_count", x.GetFoodCount(), "speed", x.GetFeedingCycleSpeed().ToString(),
                "items_per_feeding", x.itemsPerFeeding)).ToArray());
        }

        static Dictionary<string, object> Collectors(SceneContext s, Dictionary<string, object> args)
        {
            string plot = Arg(args, "plot_id");
            var collectors = Object.FindObjectsOfType<PlortCollector>()
                .Where(x => x.isActiveAndEnabled && (plot.Length == 0 || Same(PlotId(x), plot))).ToArray();
            var storage = ActiveStorage().Where(x => x.type == SiloStorage.StorageType.PLORT
                && (plot.Length == 0 || Same(PlotId(x), plot)));
            return Row("collectors", collectors.Select(x => Row("collector_id", x.GetInstanceID(),
                "plot_id", PlotId(x), "position", Position(x.transform.position), "zone", Zone(x.transform))).ToArray(),
                "plort_storage", storage.Select(x => StorageRow(s, x, true)).ToArray());
        }

        static Dictionary<string, object> Refinery(SceneContext s, Dictionary<string, object> args)
        {
            var d = s.GadgetDirector;
            var ids = Identifiable.PLORT_CLASS.Concat(Identifiable.CRAFT_CLASS).Distinct().OrderBy(x => x.ToString());
            string query = Arg(args, "item_id");
            return Row("resources", ids.Where(x => query.Length == 0
                ? (Flag(args, "include_empty", false) || d.GetRefineryCount(x) > 0) : Same(query, x.ToString()))
                .Select(x => Row("item_id", x.ToString(), "count", d.GetRefineryCount(x),
                    "free_space", d.GetRefinerySpaceAvailable(x))).ToArray());
        }

        static T AtSite<T>(GadgetSiteModel site) where T : Component
        {
            if (site.attached == null || site.attached.transform == null) return null;
            return site.attached.transform.GetComponentInChildren<T>();
        }

        static Dictionary<string, object> Extractors(SceneContext s, Dictionary<string, object> args)
        {
            string filter = Arg(args, "site_id");
            var rows = new List<Dictionary<string, object>>();
            foreach (var site in s.GameModel.AllGadgetSites().Values.OrderBy(x => x.id))
            {
                var model = site.attached as ExtractorModel;
                if (model == null || (filter.Length != 0 && !Same(filter, site.id))) continue;
                var component = AtSite<Extractor>(site);
                bool loaded = component != null && component.isActiveAndEnabled;
                rows.Add(Row("site_id", site.id, "gadget_id", model.ident.ToString(), "loaded", loaded,
                    "position", Position(site.transform.position), "zone", Zone(site.transform),
                    "ready", loaded && component.CanInteract(), "queued_output", model.queuedToProduce,
                    "cycles_remaining_after_current", model.cyclesRemaining,
                    "cycle_ends_at_world_time", Finite(model.cycleEndTime),
                    "possible_output", !loaded || component.produces == null ? null :
                        component.produces.Select(x => Row("item_id", x.id.ToString(), "weight", Finite(x.weight),
                            "restricted_to_zone", x.restrictZone ? x.zone.ToString() : null)).ToArray()));
            }
            return Row("extractors", rows);
        }

        static Dictionary<string, object> Program(DroneMetadata.Program program, int index)
        {
            if (program == null) return Row("program", index, "complete", false);
            return Row("program", index, "target", program.target == null ? null : program.target.id,
                "source", program.source == null ? null : program.source.id,
                "destination", program.destination == null ? null : program.destination.id,
                "complete", program.target != null && program.source != null && program.destination != null && program.IsComplete());
        }

        static Dictionary<string, object> Drones(SceneContext s, Dictionary<string, object> args)
        {
            string filter = Arg(args, "site_id");
            var rows = new List<Dictionary<string, object>>();
            foreach (var site in s.GameModel.AllGadgetSites().Values.OrderBy(x => x.id))
            {
                if (site.attached == null || !Gadget.IsDrone(site.attached.ident)
                    || (filter.Length != 0 && !Same(filter, site.id))) continue;
                var g = AtSite<DroneGadget>(site);
                var b = g == null || g.station == null ? null : g.station.battery;
                var cargo = g == null || g.drone == null ? null : g.drone.ammo;
                rows.Add(Row("site_id", site.id, "gadget_id", site.attached.ident.ToString(), "position", Position(site.transform.position),
                    "zone", Zone(site.transform), "loaded", g != null, "has_charge", b == null ? null : (object)b.HasAny(),
                    "battery_expires_at_world_time", b == null ? null : Finite(b.Time),
                    "program_count", g == null ? null : (object)g.programCount,
                    "cargo", cargo == null ? null : Slot(cargo, 0),
                    "programs", g == null || g.programs == null ? null : g.programs.Select(Program).ToArray()));
            }
            return Row("drones", rows);
        }

        static GadgetSiteModel FindSite(SceneContext scene, string id)
        {
            GadgetSiteModel site;
            return scene.GameModel.AllGadgetSites().TryGetValue(id, out site) && site.transform != null ? site : null;
        }

        static bool Available(DroneMetadata.Program.Behaviour behaviour)
        {
            var gadget = behaviour as DroneMetadata.Program.GadgetBehaviour;
            return gadget == null || gadget.gadgetPredicate == null || gadget.gadgetPredicate();
        }

        static Dictionary<string, object> Options(DroneMetadata.Program.BaseComponent item)
        {
            return Row("id", item.id, "name", item.GetName());
        }

        static Dictionary<string, object> DroneOptions(SceneContext s, Dictionary<string, object> args)
        {
            string id = Arg(args, "site_id");
            var site = FindSite(s, id);
            var gadget = site == null ? null : AtSite<DroneGadget>(site);
            if (gadget == null) return Fail("TARGET_UNAVAILABLE", "A loaded drone at site_id is required.");
            var m = gadget.metadata;
            return Row("site_id", id, "targets", m.targets.Select(Options).ToArray(),
                "sources", m.sources.Where(Available).Select(Options).ToArray(),
                "destinations", m.destinations.Where(Available).Select(Options).ToArray());
        }

        static LookupDirector Lookup()
        {
            return GameContext.Instance.LookupDirector;
        }

        static Dictionary<string, object> Gadgets(SceneContext s, Dictionary<string, object> args)
        {
            string filter = Arg(args, "query");
            bool owned = Flag(args, "owned_only", true);
            var director = s.GadgetDirector;
            var entries = Lookup().GadgetDefinitions.Where(d => d != null && Includes(d.id.ToString(), filter)
                && (!owned || director.HasBlueprint(d.id) || director.GetGadgetCount(d.id) > 0)).OrderBy(x => x.id.ToString());
            return Page("gadgets", entries.Select(d => Row("gadget_id", d.id.ToString(), "name", Gadget.GetName(d.id, false),
                "blueprint_owned", director.HasBlueprint(d.id),
                "blueprint_unlocked", director.HasBlueprint(d.id) || director.IsBlueprintUnlocked(d.id),
                "blueprint_price", d.blueprintCost, "inventory", director.GetGadgetCount(d.id),
                "can_fabricate", director.HasBlueprint(d.id) && director.CanAddGadget(d)
                    && d.craftCosts != null && director.HasInRefinery(d.craftCosts),
                "costs", d.craftCosts == null ? null : d.craftCosts.Select(x => Row("item_id", x.id.ToString(),
                    "required", x.amount, "in_refinery", director.GetRefineryCount(x.id))).ToArray())), args, 35);
        }

        static Dictionary<string, object> GadgetSites(SceneContext s, Dictionary<string, object> args)
        {
            string zone = Arg(args, "zone");
            bool occupied = Flag(args, "occupied_only", false);
            var entries = s.GameModel.AllGadgetSites().Values.Where(x => x.transform != null
                && (!occupied || x.attached != null) && (zone.Length == 0 || Same(zone, Zone(x.transform))));
            return Page("sites", entries.OrderBy(x => Distance(s, x.transform)).ThenBy(x => x.id)
                .Select(x => Row("site_id", x.id, "position", Position(x.transform.position), "zone", Zone(x.transform),
                    "distance", Distance(s, x.transform), "active", x.transform.gameObject.activeInHierarchy,
                    "gadget_id", x.attached == null ? null : x.attached.ident.ToString())), args, 40);
        }

        static string Category(Identifiable.Id item)
        {
            if (Identifiable.IsPlort(item)) return "plort";
            if (Identifiable.IsCraft(item)) return "craft";
            if (Identifiable.IsFood(item)) return "food";
            if (Identifiable.IsSlime(item) || Identifiable.IsLargo(item) || Identifiable.IsGordo(item)) return "slime";
            if (Identifiable.IsLiquid(item)) return "liquid";
            return "other";
        }

        static IEnumerable<Dictionary<string, object>> Nearby(SceneContext s, Identifiable.Id item, string category, int radius)
        {
            foreach (var candidate in Object.FindObjectsOfType<Identifiable>().Where(x => x.isActiveAndEnabled
                && x.id != Identifiable.Id.NONE && (item == Identifiable.Id.NONE || x.id == item)
                && (category.Length == 0 || Same(Category(x.id), category))
                && Vector3.Distance(s.Player.transform.position, x.transform.position) <= radius)
                .OrderBy(x => Distance(s, x.transform)).ThenBy(x => x.GetInstanceID()))
            {
                var member = candidate.GetComponent<RegionMember>();
                var zones = member == null ? new string[0] : ZoneDirector.Zones(member).Select(x => x.ToString()).OrderBy(x => x).ToArray();
                var vacuumable = candidate.GetComponent<Vacuumable>();
                yield return Row("instance_id", candidate.GetInstanceID(), "item_id", candidate.id.ToString(),
                    "category", Category(candidate.id), "distance", Distance(s, candidate.transform),
                    "position", Position(candidate.transform.position), "zones", zones, "plot_id", PlotId(candidate),
                    "region_set", member == null ? null : member.setId.ToString(),
                    "pickup_ready", IsPickupMaterial(candidate.id) && CanPickUp(vacuumable)
                        && s.GameModel.AllActors().ContainsKey(candidate.GetActorId()));
            }
        }

        static int Radius(Dictionary<string, object> args)
        {
            return Math.Min(500, Math.Max(1, Number(args, "radius", 100)));
        }

        static Dictionary<string, object> LooseItems(SceneContext s, Dictionary<string, object> args)
        {
            Identifiable.Id item = Identifiable.Id.NONE;
            string filter = Arg(args, "item_id");
            if (filter.Length != 0 && !ParseItem(filter, out item))
                return Fail("INVALID_ITEM", "Use an item_id from sr_item_catalog.");
            int radius = Radius(args);
            var page = Page("items", Nearby(s, item, Arg(args, "category"), radius), args, 40);
            page["radius"] = radius;
            return page;
        }

        static bool ParseItem(string id, out Identifiable.Id item)
        {
            return NamedEnum(id, out item) && item != Identifiable.Id.NONE;
        }

        static Dictionary<string, object> FindItems(SceneContext s, Dictionary<string, object> args)
        {
            Identifiable.Id item;
            if (!ParseItem(Arg(args, "item_id"), out item))
                return Fail("INVALID_ITEM", "Use an item_id from sr_item_catalog.");
            var sources = new List<Dictionary<string, object>>();
            var pack = s.PlayerState.Ammo;
            var packSlots = Slots(pack, false).Where(x => (string)x["item_id"] == item.ToString()).ToArray();
            var seen = new HashSet<Ammo>();
            foreach (var box in ActiveStorage().OrderBy(x => Distance(s, x.transform)))
            {
                var ammo = box.GetRelevantAmmo();
                if (!seen.Add(ammo)) continue;
                var row = StorageRow(s, box, false, true);
                var slots = ((Dictionary<string, object>[])row["slots"])
                    .Where(x => (string)x["item_id"] == item.ToString()).ToArray();
                if (slots.Length == 0) continue;
                row["slots"] = slots;
                sources.Add(row);
            }
            var loose = Page("items", Nearby(s, item, "", Radius(args)), args, 40);
            return Row("item_id", item.ToString(), "vacpack_count", packSlots.Sum(x => (int)x["count"]),
                "vacpack_slots", packSlots, "storage", sources,
                "storage_count", sources.Sum(x => ((Dictionary<string, object>[])x["slots"]).Sum(v => (int)v["count"])),
                "accessible_storage_count", sources.Sum(x => ((Dictionary<string, object>[])x["slots"])
                    .Where(v => (bool)v["accessible"]).Sum(v => (int)v["count"])),
                "refinery_count", GadgetDirector.IsRefineryResource(item) ? s.GadgetDirector.GetRefineryCount(item) : 0,
                "nearby", loose, "radius", Radius(args));
        }

        static Dictionary<string, object> Zones(SceneContext s, Dictionary<string, object> args)
        {
            var entries = ((ZoneDirector.Zone[])Enum.GetValues(typeof(ZoneDirector.Zone)))
                .Where(x => x != ZoneDirector.Zone.NONE).Select(x => Row(
                "zone", x.ToString(), "map_unlocked", s.PlayerState.HasUnlockedMap(x),
                "region_set", ZoneDirector.GetRegionSetId(x).ToString()));
            return Row("current_zone", s.PlayerZoneTracker == null ? Zone(s.Player.transform)
                : s.PlayerZoneTracker.GetCurrentZone().ToString(), "zones", entries.ToArray());
        }

        static Dictionary<string, object> Progress(SceneContext s, Dictionary<string, object> args)
        {
            var entries = ((ProgressDirector.ProgressType[])Enum.GetValues(typeof(ProgressDirector.ProgressType)))
                .Select(x => Row("progress_id", x.ToString(), "count", s.ProgressDirector.GetProgress(x)))
                .Where(x => Flag(args, "include_zero", false) || (int)x["count"] > 0);
            return Row("progress", entries.ToArray());
        }

        static Dictionary<string, object> Mail(SceneContext s, Dictionary<string, object> args)
        {
            var entries = s.MailDirector.GetMailRecentFirst().Where(x => !Flag(args, "unread_only", false) || !x.read);
            var page = Page("mail", entries.Select(x => Row("key", x.key, "type", x.type.ToString(), "read", x.read)), args, 35);
            page["unread_count"] = s.MailDirector.GetNewMailCount();
            return page;
        }

        static Dictionary<string, object> Pedia(SceneContext s, Dictionary<string, object> args)
        {
            string query = Arg(args, "query");
            var entries = s.PediaDirector.entries.Where(x => Includes(x.id.ToString(), query))
                .Select(x => Row("pedia_id", x.id.ToString(), "unlocked", s.PediaDirector.IsUnlocked(x.id)))
                .Where(x => !Flag(args, "unlocked_only", true) || (bool)x["unlocked"]);
            return Page("entries", entries, args, 50);
        }

        static UpgradeDefinition PlayerUpgradeDefinition(PlayerState.Upgrade upgrade)
        {
            try { return Lookup().GetUpgradeDefinition(upgrade); }
            catch (KeyNotFoundException) { return null; }
        }

        static Dictionary<string, object> PlayerShop(SceneContext s, Dictionary<string, object> args)
        {
            var entries = new List<Dictionary<string, object>>();
            foreach (PlayerState.Upgrade upgrade in Enum.GetValues(typeof(PlayerState.Upgrade)))
            {
                var definition = PlayerUpgradeDefinition(upgrade);
                if (definition == null) continue;
                var player = s.PlayerState;
                bool owned = player.HasUpgrade(upgrade);
                bool available = !owned && player.CanGetUpgrade(upgrade);
                if (!available && Flag(args, "available_only", false)) continue;
                entries.Add(Row("upgrade_id", upgrade.ToString(), "price", definition.Cost, "owned", owned,
                    "available", available, "affordable", available && definition.Cost <= player.GetCurrency()));
            }
            return Row("currency", s.PlayerState.GetCurrency(), "upgrades", entries);
        }

        static Dictionary<string, object> ItemCatalog(SceneContext s, Dictionary<string, object> args)
        {
            string query = Arg(args, "query");
            string category = Arg(args, "category");
            var ids = ((Identifiable.Id[])Enum.GetValues(typeof(Identifiable.Id))).Distinct()
                .Where(x => x != Identifiable.Id.NONE && (category.Length == 0 || Same(Category(x), category)));
            var rows = ids.Select(x => Row("item_id", x.ToString(), "name", Identifiable.GetName(x, false),
                "category", Category(x))).Where(x => Includes((string)x["item_id"], query) || Includes((string)x["name"], query));
            return Page("items", rows, args, 60);
        }
    }
}
