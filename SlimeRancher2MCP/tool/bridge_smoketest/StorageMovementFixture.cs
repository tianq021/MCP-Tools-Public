using System;
using System.Linq;
using Il2Cpp;
using Il2CppMonomiPark.SlimeRancher.Player;
using SlimeRancher2Bridge;

namespace Il2Cpp
{
    public class SlimeAppearance { public class AppearanceSaveSet { } }
}

namespace Il2CppMonomiPark.SlimeRancher.Player
{
    public record StoredItem(string name, int Pointer);
    public class AmmoSlot
    {
        public record AmmoMetadata(StoredItem Item)
        {
            public bool Matches(AmmoMetadata other) => Item == other.Item;
        }
        static int nextPointer;
        public int Pointer = ++nextPointer;
        public int Count, MaxCount = 8;
        public bool Open = true;
        public AmmoMetadata Metadata;
        public StoredItem Id => Metadata?.Item;
        public SlimeAppearance.AppearanceSaveSet Appearance;
        public void SetFromMetadata(AmmoMetadata value) => Metadata = value;
        public void Clear() { Metadata = null; Appearance = null; Count = 0; }
    }
    public class AmmoSlotManager
    {
        public AmmoSlot[] Slots;
        public bool FailDebit;
        public bool CouldAddToSlot(AmmoSlot.AmmoMetadata item, int n, bool unused) =>
            Slots[n].Open && (Slots[n].Id == null || Slots[n].Metadata.Matches(item)) && Slots[n].Count < Slots[n].MaxCount;
        public void MaybeAddToSpecificSlot(AmmoSlot.AmmoMetadata item, int n, int count, bool unused)
        {
            Slots[n].Metadata = item;
            Slots[n].Count += Math.Min(count, Slots[n].MaxCount - Slots[n].Count);
        }
        public void Decrement(int n, int count)
        {
            Slots[n].Count -= count;
            if (Slots[n].Count == 0) Slots[n].Clear();
            if (FailDebit) throw new InvalidOperationException("debit_failed");
        }
    }
}

namespace SlimeRancher2Bridge
{
    internal static partial class ToolsStorage
    {
        internal sealed class Silo
        {
            public AmmoSlot[] Live, Rules;
            public AmmoSlotManager Manager;
            public string Selector;
            public bool Ready => Manager != null;
            public bool Unlocked(int n) => Rules[n]?.Open == true;
        }
        static string Id(AmmoSlot slot) => slot?.Id == null ? null : "IdentifiableType." + slot.Id.name;
    }
}

internal static partial class Program
{
    static void TestStorageMovement()
    {
        var item = new AmmoSlot.AmmoMetadata(new("CottonPlort", 1));
        var appearance = new SlimeAppearance.AppearanceSaveSet();
        AmmoSlot Slot(int count) => new() { Count = count, Metadata = count > 0 ? item : null,
            Appearance = count > 0 ? appearance : null };
        ToolsStorage.Silo Inventory(bool remote, params int[] counts)
        {
            var live = counts.Select(Slot).ToArray();
            var rules = remote ? counts.Select(Slot).ToArray() : live;
            return new() { Live = live, Rules = rules, Manager = new() { Slots = rules } };
        }
        var bag = Inventory(false, 9, 2, 5);
        bag.Live[0].Open = false;
        var silo = Inventory(true, 7, 0, 0);
        silo.Rules[1].Open = false;
        var result = ToolsStorage.MoveExact(bag, silo, "CottonPlort", 5, true);
        Check("store preview across stacks leaves both sides untouched", (int)result["planned"] == 5 &&
            bag.Live[1].Count == 2 && silo.Live[0].Count == 7);
        result = ToolsStorage.MoveExact(bag, silo, "CottonPlort", 5, false);
        Check("store respects total and locked slots", (int)result["moved"] == 5 &&
            bag.Live.Select(s => s.Count).SequenceEqual(new[] { 9, 0, 2 }) &&
            silo.Live.Select(s => s.Count).SequenceEqual(new[] { 8, 0, 4 }) && silo.Live[2].Appearance == appearance);
        result = ToolsStorage.MoveExact(silo, bag, "IdentifiableType.CottonPlort", 10, false);
        Check("take across two remote slots syncs both models", (int)result["moved"] == 10 &&
            silo.Live.Select(s => s.Count).SequenceEqual(new[] { 0, 0, 2 }) &&
            silo.Rules[2].Count == 2 && bag.Live[1].Count == 4 && bag.Live[2].Count == 8);
        result = ToolsStorage.MoveExact(silo, bag, "CottonPlort", 3, false);
        Check("insufficient source moves zero", (int)result["planned"] == 2 && (int)result["moved"] == 0 && silo.Live[2].Count == 2);
        bag.Live[1].MaxCount = 5;
        result = ToolsStorage.MoveExact(silo, bag, "CottonPlort", 2, false);
        Check("insufficient destination moves zero", (int)result["planned"] == 1 && (int)result["moved"] == 0 && bag.Live[1].Count == 4);
        silo.Manager.FailDebit = true;
        result = ToolsStorage.MoveExact(silo, bag, "CottonPlort", 1, false);
        Check("debit failure restores source, projection and target", (string)result["failure"] == "debit_failed" &&
            (int)result["moved"] == 0 && silo.Live[2].Count == 2 && silo.Rules[2].Count == 2 && bag.Live[1].Count == 4);

        var source = Inventory(true, 4, 5, 6);
        source.Rules[2].Open = false;
        var destination = Inventory(true, 7, 0);
        result = ToolsStorage.MoveExact(source, destination, "CottonPlort", 5, false);
        Check("exact transfer between remote silos spans source slots and syncs both projections",
            (int)result["moved"] == 5 && source.Live.Select(s => s.Count).SequenceEqual(new[] { 0, 4, 6 }) &&
            source.Rules[1].Count == 4 && destination.Live.Select(s => s.Count).SequenceEqual(new[] { 8, 4 }) &&
            destination.Rules[1].Count == 4);
        var tight = Inventory(true, 7);
        result = ToolsStorage.MoveExact(source, tight, "CottonPlort", null, false);
        Check("whole-item move leaves both silos unchanged when the unlocked batch cannot fit",
            (int)result["requested"] == 4 && (int)result["planned"] == 1 && (int)result["moved"] == 0 &&
            source.Live[1].Count == 4 && tight.Live[0].Count == 7);
        result = ToolsStorage.MoveExact(source, destination, "CottonPlort", null, false);
        Check("whole-item move empties unlocked matching slots and preserves locked ones",
            (int)result["requested"] == 4 && (int)result["moved"] == 4 &&
            source.Live[1].Count == 0 && source.Live[2].Count == 6 && destination.Live[1].Count == 8);
        result = ToolsStorage.MoveExact(source, destination, "CottonPlort", null, false);
        Check("whole-item move of an empty unlocked source is a no-op",
            (int)result["requested"] == 0 && (int)result["moved"] == 0);

        var homeA = Inventory(true, 5, 1, 2);
        homeA.Selector = "plot:a";
        homeA.Live[2].Appearance = homeA.Rules[2].Appearance = new();
        var homeB = Inventory(true, 2);
        homeB.Selector = "plot:b";
        var otherRegion = Inventory(true, 3, 1);
        otherRegion.Selector = "plot:c";
        var groups = new[] { new[] { homeA, homeB }, new[] { otherRegion } };
        result = ToolsStorage.ConsolidateStacks(groups, true);
        Check("consolidation preview groups compatible occupied stacks and leaves stored counts untouched",
            (int)result["planned_items"] == 4 && (int)result["planned_freed_slots"] == 3 &&
            homeA.Live[0].Count == 5 && homeB.Live[0].Count == 2);
        result = ToolsStorage.ConsolidateStacks(groups, false);
        Check("consolidation fills existing stacks and preserves separate regions and appearances",
            (int)result["moved_items"] == 4 && (int)result["freed_slots"] == 3 &&
            homeA.Live.Select(s => s.Count).SequenceEqual(new[] { 8, 0, 2 }) &&
            homeA.Rules[0].Count == 8 && homeB.Live[0].Count == 0 && homeB.Rules[0].Count == 0 &&
            otherRegion.Live.Select(s => s.Count).SequenceEqual(new[] { 4, 0 }));
        Check("already consolidated groups have no moves",
            (int)ToolsStorage.ConsolidateStacks(groups, true)["planned_items"] == 0);
        var packed = Inventory(true, 8);
        var larger = Inventory(true, 2);
        larger.Rules[0].MaxCount = 16;
        result = ToolsStorage.ConsolidateStacks(new[] { new[] { packed, larger } }, false);
        Check("a larger compatible occupied slot can absorb a smaller full stack",
            (int)result["freed_slots"] == 1 && packed.Live[0].Count == 0 && larger.Live[0].Count == 10);
    }
}
