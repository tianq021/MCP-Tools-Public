using System;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using Il2Cpp;
using Il2CppMonomiPark.SlimeRancher.DataModel;
using UnityEngine;

namespace UnityEngine
{
    public class MonoBehaviour : Component { public bool isActiveAndEnabled = true; }
}

namespace Il2Cpp
{
    public class SiloStorage
    {
        public AmmoFixture Ammo = new();
        public AmmoFixture GetRelevantAmmo() => Ammo;
    }
    public partial class SlimeFeeder
    {
        public SiloStorage _storage;
        public IdentifiableType GetFoodId() => _storage.Ammo.Slots[0].Id;
        public int GetFoodCount() => _storage.Ammo.Slots[0].Count;
        public void ProcessFeedOperation(bool eject)
        {
            var slot = _storage.Ammo.Slots[0];
            if (!eject || slot.Count <= 0) return;
            _storage.Ammo.Slots.Values[0] = new(slot.Count == 1 ? null : slot.Id, slot.Count - 1, true);
        }
    }
    public class PlortCollector : MonoBehaviour { public LandPlotModel _model; public double _forceCollectUntil; }
    public class ResourceCollector : MonoBehaviour { public GadgetModel _model; public double _forceCollectUntil; }
    public class PlortCollectorActivator : MonoBehaviour
    {
        public PlortCollector Collector;
        public double _nextAllowedActivationTime;
        public void Activate() { _nextAllowedActivationTime = 10; Collector._forceCollectUntil = 15; }
    }
    public class ResourceCollectorActivator : MonoBehaviour
    {
        public ResourceCollector Collector;
        public double _nextAllowedActivationTime;
        public void Activate() { _nextAllowedActivationTime = 20; Collector._forceCollectUntil = 30; }
    }
    public class SiloCatcher { public int SlotIdx; }
    public class SiloStorageActivator : MonoBehaviour
    {
        public LandPlotModel _landPlotModel;
        public SiloCatcher SiloCatcher = new();
        public int ActivatorIdx;
        public void Activate() => SiloCatcher.SlotIdx++;
    }
}

internal static partial class Program
{
    static async Task TestRanchControls(GameModel game, Func<string, object, string, Task<JsonElement>> write)
    {
        async Task<JsonElement> Data(string name, object args, string id = null) =>
            (await write(name, args, id)).GetProperty("data");
        var plot = game.landPlots["pen/a:b"] = new(LandPlot.Id.CORRAL, null, null, new(), new());
        var silo = game.landPlots["silo-c"] = new(LandPlot.Id.SILO, null, null, new(), new());
        var feed = new SlimeFeeder { _model = plot, _storage = new() };
        feed._storage.Ammo.Slots.Values = new SlotFixture[] { new(new IdentifiableType { ReferenceId = "carrot" }, 2, true) };
        var plort = new PlortCollectorActivator { Collector = new() { _model = plot } };
        var resource = new ResourceCollectorActivator { Collector = new() { _model = new() { actorId = new(149) } } };
        var open = new SiloStorageActivator { _landPlotModel = silo };
        var locked = new SiloStorageActivator { _landPlotModel = silo, ActivatorIdx = 1 };
        var devices = new Component[] { feed, plort, resource, open, locked };
        UnityEngine.Object.Active = devices;
        const string list = "sr2_ranch_controls", dispense = "sr2_dispense_feeder_food", collect = "sr2_activate_plort_collector",
            gather = "sr2_activate_resource_collector", cycle = "sr2_cycle_silo_port";
        var found = await Data(list, new { });
        var items = found.GetProperty("items").EnumerateArray().ToDictionary(
            r => r.GetProperty("kind").GetString(), r => r.GetProperty("control_id").GetString());
        Check("discover loaded controls and exclude unpurchased silo panel", items.Count == 4 && found.GetProperty("record_count").GetInt32() == 4);
        var filtered = await Data(list, new { kind = "feeder" });
        Check("control list reports stock and exact owner id", filtered.GetProperty("matched_count").GetInt32() == 1 &&
            filtered.GetProperty("items")[0].GetProperty("owner_id").GetString() == "pen/a:b" &&
            filtered.GetProperty("items")[0].GetProperty("food_count").GetInt32() == 2);
        string feeder = items["feeder"];
        var doFeed = new { control_id = feeder, dry_run = false };
        var feedPlan = await Data(dispense, new { control_id = feeder });
        Check("manual feed preview reports contents", feedPlan.GetProperty("food_id").GetString() == "carrot" && feed.GetFoodCount() == 2);
        var first = await Data(dispense, doFeed, "feed-1");
        Check("manual feed debits exactly one", first.GetProperty("applied").GetBoolean() && first.GetProperty("food_after").GetInt32() == 1);
        Check("feed replay spends once", (await write(dispense, doFeed, "feed-1")).GetProperty("cached").GetBoolean() && feed.GetFoodCount() == 1);
        await Data(dispense, doFeed, "feed-2");
        Check("empty feeder rejects dispense", (await Data(dispense, doFeed, "feed-empty")).GetProperty("reason").GetString() == "no_food");
        var wrong = await Data(collect, new { control_id = items["resource_collector"], dry_run = false }, "wrong-control");
        Check("wrong control kind leaves both collectors idle", wrong.GetProperty("reason").GetString() == "loaded_control_not_found"
            && resource._nextAllowedActivationTime == 0 && plort._nextAllowedActivationTime == 0);
        var plan = await Data(collect, new { control_id = items["plort_collector"] });
        Check("button preview leaves collector idle", !plan.GetProperty("applied").GetBoolean() && plort._nextAllowedActivationTime == 0);
        var doCollect = new { control_id = items["plort_collector"], dry_run = false };
        var initiated = await Data(collect, doCollect, "plort-start");
        Check("collector acknowledges activation window", initiated.GetProperty("applied").GetBoolean()
            && initiated.GetProperty("collection_until_after").GetInt32() == 15 && resource._nextAllowedActivationTime == 0);
        Check("cooldown no-op is not reported as activation", !(await Data(collect, doCollect, "plort-cooldown")).GetProperty("applied").GetBoolean());
        var doGather = new { control_id = items["resource_collector"], dry_run = false };
        Check("resource collection targets placed gadget", (await Data(gather, doGather, "resource-start")).GetProperty("applied").GetBoolean()
            && resource.Collector._forceCollectUntil == 30);
        var doCycle = new { control_id = items["silo_port"], dry_run = false };
        var cycled = await Data(cycle, doCycle, "silo-next");
        Check("silo button advances selected panel only", cycled.GetProperty("slot_after").GetInt32() == 1 && locked.SiloCatcher.SlotIdx == 0);
        Check("silo replay does not skip another slot", (await write(cycle, doCycle, "silo-next")).GetProperty("cached").GetBoolean() && open.SiloCatcher.SlotIdx == 1);
        UnityEngine.Object.Active = devices.Append(new PlortCollectorActivator { Collector = plort.Collector }).ToArray();
        Check("ambiguous control is refused", (await Data(collect, doCollect, "duplicate-button")).GetProperty("reason").GetString() == "ambiguous_control_id");
        silo.upgrades.Add("STORAGE2");
        Check("purchased second panel becomes reachable", (await Data(list, new { kind = "silo_port" })).GetProperty("matched_count").GetInt32() == 2);
        resource.isActiveAndEnabled = false;
        Check("a control unloaded after discovery is refused", (await Data(gather, doGather, "inactive-resource")).GetProperty("reason").GetString() == "loaded_control_not_found");
    }
}
