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
        static void RegisterActions(BridgeServer server)
        {
            Action(server, "sr_sell", Sell);
            Action(server, "sr_move", Move);
            Action(server, "sr_deposit_refinery", DepositRefinery);
            Action(server, "sr_deliver", Deliver);
            Action(server, "sr_harvest", Harvest);
            Action(server, "sr_set_feeder_speed", SetFeederSpeed);
            Action(server, "sr_start_collector", StartCollector);
            Action(server, "sr_buy_player_upgrade", BuyPlayerUpgrade);
            Action(server, "sr_buy_blueprint", BuyBlueprint);
            Action(server, "sr_fabricate", Fabricate);
            Action(server, "sr_place_gadget", PlaceGadget);
            Action(server, "sr_program_drone", ProgramDrone);
            Action(server, "sr_pickup_loose", PickUpLoose);
            Action(server, "sr_recharge_drone", RechargeDrone);
            RegisterPlotShop(server);
            RegisterExtras(server);
            RegisterExtras2(server);
            RegisterExtras3(server);
            RegisterExtras4(server);
            RegisterModelStorage(server);
            RegisterExpansionDoors(server);
            RegisterSpawn(server);
            RegisterPediaTools(server);
        }

        static bool Preview(Dictionary<string, object> args)
        {
            return Flag(args, "dry_run", true);
        }

        static void Action(BridgeServer server, string name,
            Func<SceneContext, Dictionary<string, object>, Dictionary<string, object>> handler)
        {
            Read(server, name, (scene, args) =>
            {
                bool preview = Preview(args);
                if (!preview && (scene.TimeDirector.HasPauser() || scene.TimeDirector.IsFastForwarding()))
                    return Fail("GAME_PAUSED", "Resume the game and wait for any time skip to finish.");
                var response = handler(scene, args);
                response["dry_run"] = preview;
                return response;
            }, true);
        }

        sealed class InventorySlot
        {
            public readonly Ammo Ammo;
            public readonly SiloStorage Storage;
            public readonly int Index;
            public readonly int StorageId;

            public InventorySlot(Ammo ammo, SiloStorage storage, int index, int storageId)
            {
                Ammo = ammo;
                Storage = storage;
                Index = index;
                StorageId = storageId;
            }

            public int Count { get { return Ammo.GetSlotCount(Index); } }
            public Identifiable.Id Item { get { return Ammo.GetSlotName(Index); } }

            public bool Add(Identifiable.Id item, int count)
            {
                return Storage == null ? Ammo.MaybeAddToSpecificSlot(item, null, Index, count)
                    : Storage.MaybeAddIdentifiable(item, Index, count);
            }
        }

        static InventorySlot LocateSlot(SceneContext scene, int storageId, int index)
        {
            var storage = storageId == 0 ? null : ActiveStorage().FirstOrDefault(x => x.GetInstanceID() == storageId);
            Ammo ammo;
            if (storageId == 0)
            {
                if (scene.PlayerState.GetAmmoMode() != PlayerState.AmmoMode.DEFAULT) return null;
                ammo = scene.PlayerState.Ammo;
            }
            else
            {
                if (storage == null) return null;
                ammo = storage.GetRelevantAmmo();
            }
            return index >= 0 && index < ammo.GetUsableSlotCount() ? new InventorySlot(ammo, storage, index, storageId) : null;
        }

        static Dictionary<string, object> Source(SceneContext scene, Dictionary<string, object> args,
            out InventorySlot source, out Identifiable.Id item, out int quantity)
        {
            source = null;
            quantity = Number(args, "count", -1);
            if (!ParseItem(Arg(args, "item_id"), out item))
                return Fail("INVALID_ITEM", "Use a concrete item_id from sr_item_catalog.");
            if (quantity < 1 || quantity > 999)
                return Fail("INVALID_COUNT", "count must be between 1 and 999.");
            source = LocateSlot(scene, Number(args, "source_storage_id", 0), Number(args, "source_slot", -1));
            if (source == null)
                return Fail("SOURCE_UNAVAILABLE", "Use a valid slot from sr_vacpack or sr_storage. The normal Vacpack must be active.");
            if (source.Item != item || source.Count < quantity)
                return Fail("SOURCE_CHANGED", "The source slot has a different item or fewer items than requested.");
            return null;
        }

        static Dictionary<string, object> InventoryResult(InventorySlot source, Identifiable.Id item, int count)
        {
            return Row("item_id", item.ToString(), "count", count, "source_storage_id", source.StorageId,
                "source_slot", source.Index, "source_count_before", source.Count);
        }

        static Dictionary<string, object> Sell(SceneContext scene, Dictionary<string, object> args)
        {
            InventorySlot source;
            Identifiable.Id item;
            int quantity;
            var error = Source(scene, args, out source, out item, out quantity);
            if (error != null) return error;
            if (!Identifiable.IsPlort(item)) return Fail("INVALID_ITEM", "The market only accepts plorts.");
            var market = Object.FindObjectsOfType<ScorePlort>().Where(x => x.isActiveAndEnabled)
                .OrderBy(x => Distance(scene, x.transform)).FirstOrDefault();
            if (market == null) return Fail("TARGET_UNAVAILABLE", "No loaded plort market deposit point.");
            var economy = scene.EconomyDirector;
            if (economy.IsMarketShutdown()) return Fail("MARKET_CLOSED", "The plort market is currently closed.");
            int? price = economy.GetCurrValue(item);
            if (!price.HasValue || !market.CanDeposit(item)) return Fail("NOT_MARKETABLE", "The market has no price for this plort.");
            int unitPrice = Mathf.RoundToInt(price.Value * scene.ModDirector.PlortPriceFactor(item));
            if (unitPrice <= 0) return Fail("NOT_MARKETABLE", "The current sale price is zero.");
            if (unitPrice < Number(args, "min_unit_price", 0))
                return Fail("PRICE_CHANGED", "The current sale price is below min_unit_price.");
            var result = InventoryResult(source, item, quantity);
            var player = scene.PlayerState;
            int balance = player.GetCurrency();
            result["base_unit_price"] = unitPrice;
            result["estimated_currency"] = (long)unitPrice * quantity;
            result["currency_before"] = balance;
            if (Preview(args)) return result;
            ScorePlort.Deposit_Response receipt;
            try { receipt = market.Deposit(item, quantity); }
            catch (Exception)
            {
                if (player.GetCurrency() != balance) source.Ammo.Decrement(source.Index, quantity);
                throw;
            }
            if (receipt.deposits == 0) return Fail("MARKET_REJECTED", "The market rejected the sale; the item remains in its source slot.");
            source.Ammo.Decrement(source.Index, receipt.deposits);
            result["sold"] = receipt.deposits;
            result["credited"] = receipt.currency;
            result["currency_after"] = player.GetCurrency();
            result["source_count_after"] = source.Count;
            return result;
        }

        static Dictionary<string, object> Move(SceneContext scene, Dictionary<string, object> args)
        {
            InventorySlot source;
            Identifiable.Id item;
            int quantity;
            var error = Source(scene, args, out source, out item, out quantity);
            if (error != null) return error;
            var destination = LocateSlot(scene, Number(args, "destination_storage_id", 0), Number(args, "destination_slot", -1));
            if (destination == null)
                return Fail("DESTINATION_UNAVAILABLE", "Use a valid destination slot returned by sr_vacpack or sr_storage.");
            if (destination.Storage != null && !CanUseStorageSlot(destination.Storage, destination.Index))
                return Fail("DESTINATION_LOCKED", "This silo slot belongs to a locked port. Use an accessible slot from sr_storage.");
            if (ReferenceEquals(source.Ammo, destination.Ammo)
                && (source.Storage == null || source.Storage != destination.Storage || source.Index == destination.Index))
                return Fail("SAME_INVENTORY", "Use a different slot in the same storage, or a different inventory.");
            if (Identifiable.IsLiquid(item) || Identifiable.IsSlime(item) || Identifiable.IsLargo(item))
                return Fail("INVALID_ITEM", "Liquids and live slimes require the game's physical Vacpack transfer.");
            if (destination.Storage == null && destination.Item != item && destination.Ammo.GetCount(item) > 0)
                return Fail("WRONG_SLOT", "Select the Vacpack slot that already holds this item.");
            int room = FreeSpaceForItem(destination.Ammo, item, destination.Index);
            if (room < quantity)
                return Fail("DESTINATION_FULL_OR_INCOMPATIBLE", "The destination slot does not accept this item or cannot fit the full count.");
            var result = InventoryResult(source, item, quantity);
            int before = destination.Count;
            result["destination_storage_id"] = destination.StorageId;
            result["destination_slot"] = destination.Index;
            result["destination_count_before"] = before;
            result["destination_free_space"] = room;
            if (Preview(args)) return result;
            try
            {
                if (!destination.Add(item, quantity))
                    return Fail("DESTINATION_REJECTED", "The destination rejected the transfer.");
            }
            finally
            {
                int credited = destination.Item == item ? destination.Count - before : 0;
                if (credited > 0) source.Ammo.Decrement(source.Index, credited);
            }
            result["source_count_after"] = source.Count;
            result["destination_count_after"] = destination.Count;
            return result;
        }

        static Dictionary<string, object> DepositRefinery(SceneContext scene, Dictionary<string, object> args)
        {
            InventorySlot source;
            Identifiable.Id item;
            int quantity;
            var error = Source(scene, args, out source, out item, out quantity);
            if (error != null) return error;
            var refinery = scene.GadgetDirector;
            if (!GadgetDirector.IsRefineryResource(item)) return Fail("INVALID_ITEM", "The refinery only accepts plorts and crafting resources.");
            if (refinery.GetRefinerySpaceAvailable(item) < quantity) return Fail("REFINERY_FULL", "The full count would not fit in the refinery.");
            var result = InventoryResult(source, item, quantity);
            int before = refinery.GetRefineryCount(item);
            result["refinery_count_before"] = before;
            if (Preview(args)) return result;
            try
            {
                if (refinery.AddToRefinery(item, quantity) == 0)
                    return Fail("REFINERY_REJECTED", "The refinery rejected the deposit.");
            }
            finally
            {
                int credited = refinery.GetRefineryCount(item) - before;
                if (credited > 0) source.Ammo.Decrement(source.Index, credited);
            }
            result["refinery_count_after"] = refinery.GetRefineryCount(item);
            result["source_count_after"] = source.Count;
            return result;
        }

        static Dictionary<string, object> Deliver(SceneContext scene, Dictionary<string, object> args)
        {
            InventorySlot source;
            Identifiable.Id item;
            int quantity;
            var error = Source(scene, args, out source, out item, out quantity);
            if (error != null) return error;
            ExchangeDirector.OfferType type;
            if (!NamedEnum(Arg(args, "offer_type"), out type))
                return Fail("INVALID_OFFER", "Use offer_type and offer_id from sr_exchange.");
            string id = Arg(args, "offer_id");
            var director = scene.ExchangeDirector;
            if (id.Length == 0 || id != director.GetOfferId(type))
                return Fail("OFFER_CHANGED", "The requested offer ID does not match the current offer.");
            double? expires = director.GetOfferExpirationTime(type);
            if (!expires.HasValue || expires.Value <= 0)
                return Fail("OFFER_EXPIRED", "The current offer has expired.");
            var requests = director.GetOfferRequests(type);
            if (requests == null || requests.Where(x => x.id == item).Sum(x => Math.Max(0, x.count - x.progress)) < quantity)
                return Fail("NOT_REQUESTED", "The offer does not need the full requested quantity of that item.");
            var acceptor = Object.FindObjectsOfType<ExchangeAcceptor>().FirstOrDefault(x => x.isActiveAndEnabled
                && x.transform.parent != null && x.offerTypes != null && x.offerTypes.Contains(type));
            if (acceptor == null)
                return Fail("TARGET_UNAVAILABLE", "The exchange receiver for this offer is not loaded.");
            var awarders = acceptor.transform.parent.GetComponentsInChildren<ExchangeDirector.Awarder>();
            if (awarders.Length == 0)
                return Fail("TARGET_UNAVAILABLE", "No loaded reward handler is connected to the offer receiver.");
            var result = InventoryResult(source, item, quantity);
            result["offer_id"] = id;
            if (Preview(args)) return result;

            int delivered = 0;
            for (int i = 0; i < quantity; i++)
            {
                var request = requests.FirstOrDefault(x => x.id == item && !x.IsComplete());
                if (request == null) break;
                int before = request.progress;
                try
                {
                    if (!director.TryAccept(type, item, awarders)) break;
                }
                finally
                {
                    if (request.progress > before)
                    {
                        source.Ammo.Decrement(source.Index, 1);
                        delivered++;
                    }
                }
            }
            result["delivered"] = delivered;
            result["source_count_after"] = source.Count;
            result["complete"] = requests.All(x => x.IsComplete());
            return result;
        }

        static Dictionary<string, object> Harvest(SceneContext scene, Dictionary<string, object> args)
        {
            string id = Arg(args, "site_id");
            var site = FindSite(scene, id);
            var model = site == null ? null : site.attached as ExtractorModel;
            var extractor = model == null ? null : AtSite<Extractor>(site);
            if (extractor == null || !extractor.isActiveAndEnabled)
                return Fail("TARGET_UNAVAILABLE", "Use the site_id of a loaded extractor from sr_extractors.");
            if (!extractor.CanInteract() || model.queuedToProduce != 0)
                return Fail("EXTRACTOR_NOT_READY", "The current cycle is running or has already started ejecting its output.");
            var result = Row("site_id", id, "gadget_id", model.ident.ToString(), "output_placement", "ground",
                "minimum_output", extractor.produceMin, "maximum_output", extractor.produceMax);
            if (Preview(args)) return result;
            extractor.OnInteract();
            result["queued_output"] = model.queuedToProduce;
            return result;
        }

        static Dictionary<string, object> SetFeederSpeed(SceneContext scene, Dictionary<string, object> args)
        {
            int id = Number(args, "feeder_id", 0);
            var feeder = Object.FindObjectsOfType<SlimeFeeder>().FirstOrDefault(x => x.isActiveAndEnabled && x.GetInstanceID() == id);
            if (feeder == null) return Fail("TARGET_UNAVAILABLE", "Use the feeder_id returned by sr_feeders.");
            SlimeFeeder.FeedSpeed speed;
            if (!NamedEnum(Arg(args, "speed"), out speed) || !feeder.hoursByFeedSpeed.ContainsKey(speed))
                return Fail("INVALID_SPEED", "Use SLOW, NORMAL, or FAST.");
            var result = Row("feeder_id", id, "previous_speed", feeder.GetFeedingCycleSpeed().ToString(), "speed", speed.ToString());
            if (!Preview(args)) feeder.SetFeederSpeed(speed);
            return result;
        }

        static Dictionary<string, object> StartCollector(SceneContext scene, Dictionary<string, object> args)
        {
            int id = Number(args, "collector_id", 0);
            var collector = Object.FindObjectsOfType<PlortCollector>().FirstOrDefault(x => x.isActiveAndEnabled && x.GetInstanceID() == id);
            if (collector == null) return Fail("TARGET_UNAVAILABLE", "Use the collector_id returned by sr_collectors.");
            if (!Preview(args)) collector.StartCollection();
            return Row("collector_id", id, "plot_id", PlotId(collector), "trigger_called", !Preview(args));
        }

        static Dictionary<string, object> CheckPrice(PlayerState player, int cost, Dictionary<string, object> args)
        {
            int cap = Number(args, "max_price", -1);
            if (cost < 0 || (cap >= 0 && cost > cap)) return Fail("PRICE_CHANGED", "The in-game price exceeds max_price or is unavailable.");
            if (player.GetCurrency() < cost) return Fail("INSUFFICIENT_FUNDS", "Not enough Newbucks for this purchase.");
            return null;
        }

        static Dictionary<string, object> BuyPlayerUpgrade(SceneContext scene, Dictionary<string, object> args)
        {
            PlayerState.Upgrade id;
            if (!NamedEnum(Arg(args, "upgrade_id"), out id))
                return Fail("INVALID_UPGRADE", "Use the upgrade_id returned by sr_player_shop.");
            var definition = PlayerUpgradeDefinition(id);
            var player = scene.PlayerState;
            if (definition == null || player.HasUpgrade(id) || !player.CanGetUpgrade(id))
                return Fail("UPGRADE_UNAVAILABLE", "The upgrade is owned, locked, or missing from the shop.");
            var error = CheckPrice(player, definition.Cost, args);
            if (error != null) return error;
            var result = Row("upgrade_id", id.ToString(), "price", definition.Cost, "currency_before", player.GetCurrency());
            if (!Preview(args))
            {
                player.SpendCurrency(definition.Cost);
                player.AddUpgrade(id, true);
                result["owned"] = player.HasUpgrade(id);
                result["currency_after"] = player.GetCurrency();
            }
            return result;
        }

        static GadgetDefinition GadgetDefinitionFor(Dictionary<string, object> args)
        {
            Gadget.Id id;
            return NamedEnum(Arg(args, "gadget_id"), out id) && id != Gadget.Id.NONE && Lookup().HasGadgetDefinition(id)
                ? Lookup().GetGadgetDefinition(id) : null;
        }

        static Dictionary<string, object> BuyBlueprint(SceneContext scene, Dictionary<string, object> args)
        {
            var definition = GadgetDefinitionFor(args);
            if (definition == null) return Fail("INVALID_GADGET", "Use the gadget_id returned by sr_gadgets.");
            var director = scene.GadgetDirector;
            if (!scene.GameModeConfig.GetModeSettings().blueprintsEnabled
                || director.HasBlueprint(definition.id) || !director.IsBlueprintUnlocked(definition.id))
                return Fail("BLUEPRINT_UNAVAILABLE", "The blueprint is already owned or has not unlocked.");
            var player = scene.PlayerState;
            var error = CheckPrice(player, definition.blueprintCost, args);
            if (error != null) return error;
            var result = Row("gadget_id", definition.id.ToString(), "price", definition.blueprintCost, "currency_before", player.GetCurrency());
            if (!Preview(args))
            {
                player.SpendCurrency(definition.blueprintCost);
                result["owned"] = director.AddBlueprint(definition.id);
                result["currency_after"] = player.GetCurrency();
            }
            return result;
        }

        static Dictionary<string, object> Fabricate(SceneContext scene, Dictionary<string, object> args)
        {
            var definition = GadgetDefinitionFor(args);
            if (definition == null || definition.craftCosts == null)
                return Fail("INVALID_GADGET", "Use a fabricatable gadget_id returned by sr_gadgets.");
            var director = scene.GadgetDirector;
            if (!director.HasBlueprint(definition.id) || !director.CanAddGadget(definition))
                return Fail("GADGET_UNAVAILABLE", "The blueprint is missing or the game inventory limit is reached.");
            if (!director.HasInRefinery(definition.craftCosts))
                return Fail("INSUFFICIENT_RESOURCES", "Check craft costs with sr_gadgets and refinery counts with sr_refinery.");
            var result = Row("gadget_id", definition.id.ToString(), "output", definition.buyInPairs ? 2 : 1,
                "costs", definition.craftCosts.Select(x => Row("item_id", x.id.ToString(), "count", x.amount)).ToArray(),
                "inventory_before", director.GetGadgetCount(definition.id));
            if (Preview(args)) return result;
            if (!director.TryToSpendFromRefinery(definition.craftCosts))
                return Fail("INSUFFICIENT_RESOURCES", "The refinery could not supply the recipe.");
            director.AddGadget(definition.id);
            if (definition.buyInPairs) director.AddGadget(definition.id);
            scene.AchievementsDirector.AddToStat(AchievementsDirector.GameIntStat.FABRICATED_GADGETS, 1);
            result["inventory_after"] = director.GetGadgetCount(definition.id);
            return result;
        }

        static Dictionary<string, object> PlaceGadget(SceneContext scene, Dictionary<string, object> args)
        {
            string siteId = Arg(args, "site_id");
            var model = FindSite(scene, siteId);
            var site = model == null ? null : model.transform.GetComponent<GadgetSite>();
            if (site == null || !site.isActiveAndEnabled || model.HasAttached() || site.HasAttached())
                return Fail("TARGET_UNAVAILABLE", "Use an active, empty gadget site from sr_gadget_sites.");
            var definition = GadgetDefinitionFor(args);
            if (definition == null || definition.prefab == null)
                return Fail("INVALID_GADGET", "Use a placeable gadget_id returned by sr_gadgets.");
            var director = scene.GadgetDirector;
            if (director.GetGadgetCount(definition.id) < 1)
                return Fail("INSUFFICIENT_GADGETS", "Fabricate the gadget before placing it.");
            if (!director.CanPlaceGadget(site, definition.id))
                return Fail("PLACEMENT_REJECTED", "The game does not allow this gadget at the specified site.");
            var result = Row("site_id", siteId, "gadget_id", definition.id.ToString(), "inventory_before", director.GetGadgetCount(definition.id));
            if (!Preview(args))
            {
                try { scene.GameModel.InstantiateGadget(definition.prefab, model); }
                finally
                {
                    if (model.attached != null && model.attached.ident == definition.id)
                        director.SpendGadget(definition.id);
                }
                if (model.attached == null) return Fail("PLACEMENT_FAILED", "The site did not attach the requested gadget.");
                result["inventory_after"] = director.GetGadgetCount(definition.id);
            }
            return result;
        }

        static Dictionary<string, object> ProgramDrone(SceneContext scene, Dictionary<string, object> args)
        {
            string siteId = Arg(args, "site_id");
            var site = FindSite(scene, siteId);
            var gadget = site == null ? null : AtSite<DroneGadget>(site);
            if (gadget == null || gadget.programs == null)
                return Fail("TARGET_UNAVAILABLE", "Use the site_id of a loaded drone from sr_drones.");
            int index = Number(args, "program", -1);
            if (index < 0 || index >= gadget.programs.Length)
                return Fail("INVALID_PROGRAM", "Use a zero-based program slot from sr_drones.");
            if (gadget.drone != null && gadget.drone.ammo != null && gadget.drone.ammo.Any())
                return Fail("DRONE_CARRYING", "Wait for the drone to unload its cargo before reprogramming.");
            var metadata = gadget.metadata;
            var target = metadata.targets.FirstOrDefault(x => Same(x.id, Arg(args, "target")));
            var source = metadata.sources.FirstOrDefault(x => Same(x.id, Arg(args, "source")));
            var destination = metadata.destinations.FirstOrDefault(x => Same(x.id, Arg(args, "destination")));
            if (target == null || source == null || destination == null)
                return Fail("INVALID_PROGRAM", "Get the target, source, and destination IDs from sr_drone_options.");
            var program = new DroneMetadata.Program(target, source, destination);
            if (!program.IsComplete() || !Available(source) || !Available(destination)
                || !source.isCompatible(program) || !destination.isCompatible(program)
                || destination.id == source.id.Replace("source", "destination"))
                return Fail("INCOMPATIBLE_PROGRAM", "The selected target, source, and destination are incompatible.");
            var result = Row("site_id", siteId, "previous", Program(gadget.programs[index], index), "planned", Program(program, index));
            if (!Preview(args))
            {
                var programs = (DroneMetadata.Program[])gadget.programs.Clone();
                programs[index] = program;
                gadget.SetPrograms(programs);
            }
            return result;
        }

        static bool CanPickUp(Vacuumable item)
        {
            return item != null && item.isActiveAndEnabled && item.size == Vacuumable.Size.NORMAL
                && item.canCapture() && !item.isCaptive() && !item.isHeld() && !item.isLaunched() && !item.IsTornadoed();
        }

        static bool IsPickupMaterial(Identifiable.Id item)
        {
            return Identifiable.IsPlort(item) || Identifiable.IsFood(item) || Identifiable.IsCraft(item);
        }

        static Dictionary<string, object> PickUpLoose(SceneContext scene, Dictionary<string, object> args)
        {
            Identifiable.Id item;
            if (!ParseItem(Arg(args, "item_id"), out item) || !IsPickupMaterial(item))
                return Fail("INVALID_ITEM", "Use a loose food, plort or crafting item from sr_loose_items.");
            if (scene.PlayerState.GetAmmoMode() != PlayerState.AmmoMode.DEFAULT)
                return Fail("VACPACK_MODE", "Switch back to the normal Vacpack.");
            int id = Number(args, "instance_id", 0);
            var target = Object.FindObjectsOfType<Identifiable>().FirstOrDefault(x => x.isActiveAndEnabled
                && x.id == item && x.GetInstanceID() == id);
            var vacuumable = target == null ? null : target.GetComponent<Vacuumable>();
            if (!CanPickUp(vacuumable) || !scene.GameModel.AllActors().ContainsKey(target.GetActorId()))
                return Fail("TARGET_UNAVAILABLE", "That loose item is gone, moving or captured.");
            var playerRegion = scene.Player.GetComponent<RegionMember>();
            var itemRegion = target.GetComponent<RegionMember>();
            if (playerRegion == null || itemRegion == null || playerRegion.setId != itemRegion.setId)
                return Fail("OUT_OF_REGION", "The item must be in the player's region set.");
            int radius = Math.Min(500, Math.Max(1, Number(args, "radius", 30)));
            if (Vector3.Distance(scene.Player.transform.position, target.transform.position) > radius)
                return Fail("OUT_OF_RANGE", "Item moved outside the requested pickup radius.");
            var ammo = scene.PlayerState.Ammo;
            if (!ammo.CouldAddToSlot(item)) return Fail("VACPACK_FULL", "No compatible Vacpack space.");
            int before = ammo.GetCount(item);
            var result = Row("instance_id", id, "item_id", item.ToString(), "position", Position(target.transform.position),
                "distance", Distance(scene, target.transform), "vacpack_count_before", before);
            if (!Preview(args))
            {
                if (!vacuumable.TryConsume()) return Fail("PICKUP_REJECTED", "The game rejected the pickup.");
                if (vacuumable != null) vacuumable.PreventCaptureFor(1f);
                result["vacpack_count_after"] = ammo.GetCount(item);
                result["picked_up"] = ammo.GetCount(item) - before;
            }
            return result;
        }

        static Dictionary<string, object> RechargeDrone(SceneContext scene, Dictionary<string, object> args)
        {
            string id = Arg(args, "site_id");
            var site = FindSite(scene, id);
            var gadget = site == null ? null : AtSite<DroneGadget>(site);
            var battery = gadget == null || gadget.station == null ? null : gadget.station.battery;
            if (battery == null || !battery.isActiveAndEnabled)
                return Fail("TARGET_UNAVAILABLE", "Use the site_id of a loaded drone with a battery.");
            var water = LocateSlot(scene, 0, Number(args, "water_slot", -1));
            if (water == null || !Identifiable.IsWater(water.Item) || water.Count < 1)
                return Fail("WATER_REQUIRED", "Use the Vacpack water slot with at least one unit of water.");
            double before = battery.Time;
            var result = Row("site_id", id, "water_count_before", water.Count,
                "battery_expires_before", Finite(before), "water_cost", 1);
            if (!Preview(args))
            {
                try { battery.AddLiquid(water.Item, 1f); }
                finally
                {
                    if (battery.Time > before) water.Ammo.Decrement(water.Index, 1);
                }
                result["water_count_after"] = water.Count;
                result["battery_expires_after"] = Finite(battery.Time);
            }
            return result;
        }
    }
}
