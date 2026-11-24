using System;
using System.Collections.Generic;
using System.Linq;
using Il2Cpp;
using Il2CppMonomiPark.SlimeRancher.Player;
using Row = System.Collections.Generic.Dictionary<string, object>;

namespace SlimeRancher2Bridge
{
    internal static partial class ToolsStorage
    {
        // Snapshot all per-stack fields before changing either side.
        sealed class Contents
        {
            public readonly int Count;
            public readonly AmmoSlot.AmmoMetadata Metadata;
            public readonly SlimeAppearance.AppearanceSaveSet Appearance;
            public Contents(AmmoSlot slot)
            {
                Count = slot.Count;
                Metadata = slot.Id == null ? null : slot.Metadata;
                Appearance = slot.Appearance;
            }
            public void Restore(AmmoSlot slot)
            {
                if (Metadata == null) { slot.Clear(); return; }
                slot.SetFromMetadata(Metadata);
                slot.Appearance = Appearance;
                slot.Count = Count;
            }
        }

        readonly struct Move
        {
            public readonly int Source, Warehouse, Slot, Count, Remaining;
            public Move(int source, int warehouse, int slot, int count, int remaining)
            {
                Source = source;
                Warehouse = warehouse;
                Slot = slot;
                Count = count;
                Remaining = remaining;
            }
        }
        // Accept a slot number, the full id (IdentifiableType.TabbyPlort), the bare id
        // (TabbyPlort) or the display name, so callers never have to guess the prefix.
        internal static bool Matches(string filter, int slotIndex, AmmoSlot slot)
        {
            if (string.IsNullOrEmpty(filter)) return true;
            if (string.Equals(filter, slotIndex.ToString(), StringComparison.Ordinal)) return true;
            string id = Id(slot);
            if (id == null) return false;
            if (string.Equals(filter, id, StringComparison.OrdinalIgnoreCase)) return true;
            int dot = id.LastIndexOf('.');
            if (dot >= 0 && string.Equals(filter, id.Substring(dot + 1), StringComparison.OrdinalIgnoreCase)) return true;
            string name = slot.Id?.name;
            return name != null && string.Equals(filter, name, StringComparison.OrdinalIgnoreCase);
        }

        static List<Move> Plan(AmmoSlot[] sources, List<Silo> silos, int only, string item, List<Row> unplaced,
            int? total = null, Silo boundedSource = null)
        {
            var moves = new List<Move>();
            // Reservations also protect a previously empty slot from two unlike stacks.
            var reserved = new Dictionary<(int, int), (int Source, int Count)>();
            int remaining = total ?? 0;
            for (int src = 0; src < sources.Length && (!total.HasValue || remaining > 0); ++src)
            {
                var source = sources[src];
                if (source?.Id == null || source.Count <= 0) continue;
                if (boundedSource != null && !boundedSource.Unlocked(src)) continue;
                string id = Id(source);
                if (item.Length > 0 && !Matches(item, src, source)) continue;
                int requested = total.HasValue ? Math.Min(remaining, source.Count) : source.Count;
                int left = requested;
                for (int si = 0; si < silos.Count && left > 0; ++si)
                {
                    var silo = silos[si];
                    if (!silo.Ready || (only >= 0 && si != only)) continue;
                    // Use compatible existing stacks before occupying an empty slot.
                    foreach (int n in Enumerable.Range(0, silo.Rules.Length).OrderBy(i => silo.Rules[i]?.Count == 0))
                    {
                        var slot = silo.Rules[n];
                        if (!silo.Unlocked(n) || !silo.Manager.CouldAddToSlot(source.Metadata, n, false)) continue;
                        if (slot.Count > 0 && (Id(slot) != id || slot.Appearance != source.Appearance)) continue;
                        reserved.TryGetValue((si, n), out var prior);
                        if (prior.Count > 0 && (!sources[prior.Source].Metadata.Matches(source.Metadata) ||
                            sources[prior.Source].Appearance != source.Appearance)) continue;
                        int quantity = Math.Min(left, slot.MaxCount - slot.Count - prior.Count);
                        if (quantity <= 0) continue;
                        left -= quantity;
                        reserved[(si, n)] = (src, prior.Count + quantity);
                        if (total.HasValue) remaining -= quantity;
                        moves.Add(new(src, si, n, quantity, source.Count - requested + left));
                        if (left == 0) break;
                    }
                }
                if (left > 0) unplaced.Add(new() { ["source_slot"] = src, ["id"] = id, ["count"] = left,
                    ["reason"] = "no_compatible_space" });
            }
            return moves;
        }

        static int Transfer(AmmoSlotManager sourceManager, Move move, Silo silo, Silo sourceSilo = null)
        {
            var debit = sourceManager.Slots[move.Source];
            var src = sourceSilo?.Live[move.Source] ?? debit;
            var dst = silo.Live[move.Slot];
            var rule = silo.Rules[move.Slot];
            var before = new Contents(src);
            var old = new Contents(dst);
            if (before.Count < move.Count || debit.Count != before.Count || Id(debit) != Id(src) || old.Count != rule.Count ||
                !silo.Manager.CouldAddToSlot(before.Metadata, move.Slot, false))
                throw new InvalidOperationException("inventory_changed");
            try
            {
                silo.Manager.MaybeAddToSpecificSlot(before.Metadata, move.Slot, move.Count, false);
                int added = rule.Count - old.Count;
                if (added <= 0 || added > move.Count || rule.Id?.Pointer != src.Id.Pointer)
                    throw new InvalidOperationException("native_deposit_mismatch");
                rule.Appearance = before.Appearance;
                if (dst.Pointer != rule.Pointer) new Contents(rule).Restore(dst);
                sourceManager.Decrement(move.Source, added);
                if (src.Pointer != debit.Pointer) new Contents(debit).Restore(src);
                if (src.Count != before.Count - added || dst.Count != old.Count + added)
                    throw new InvalidOperationException("transfer_readback_mismatch");
                return added;
            }
            catch
            {
                try
                {
                    old.Restore(dst);
                    if (dst.Pointer != rule.Pointer) old.Restore(rule);
                }
                finally
                {
                    before.Restore(src);
                    if (src.Pointer != debit.Pointer) before.Restore(debit);
                }
                throw;
            }
        }

        internal static Row MoveExact(Silo source, Silo target, string item, int? total, bool preview)
        {
            int requested = total ?? Enumerable.Range(0, source.Live.Length)
                .Where(i => source.Live[i]?.Id != null && source.Unlocked(i) && Matches(item, i, source.Live[i]))
                .Sum(i => source.Live[i].Count);
            var moves = Plan(source.Live, new() { target }, 0, item, new(), requested, source);
            int planned = moves.Sum(m => m.Count);
            var result = new Row { ["requested"] = requested, ["planned"] = planned, ["moved"] = 0, ["dry_run"] = preview };
            if (preview || planned != requested) return result;
            int moved = 0;
            foreach (var m in moves)
            {
                try
                {
                    int added = Transfer(source.Manager, m, target, source);
                    moved += added;
                    if (added != m.Count) { result["failure"] = "partial_move"; break; }
                }
                catch (Exception e) { result["failure"] = e.GetBaseException().Message; break; }
            }
            result["moved"] = moved;
            return result;
        }

        sealed class Stack
        {
            public readonly Silo Silo;
            public readonly int Index;
            public AmmoSlot Slot => Silo.Live[Index];
            public int Remaining;

            public Stack(Silo silo, int index)
            {
                Silo = silo;
                Index = index;
                Remaining = Slot.Count;
            }
        }

        internal static Row ConsolidateStacks(IEnumerable<IEnumerable<Silo>> groups, bool preview)
        {
            var all = new List<Stack>();
            var moves = new List<(Stack From, Stack To, int Count)>();
            foreach (var group in groups)
            {
                var occupied = new List<Stack>();
                foreach (var silo in group)
                    for (int i = 0; i < silo.Live.Length; i++)
                    {
                        var slot = silo.Live[i];
                        if (silo.Unlocked(i) && slot?.Id != null && slot.Metadata != null && slot.Count > 0)
                            occupied.Add(new Stack(silo, i));
                    }
                var stacks = occupied.OrderByDescending(s => s.Silo.Rules[s.Index].MaxCount)
                    .ThenByDescending(s => s.Remaining).ThenBy(s => s.Silo.Selector, StringComparer.Ordinal)
                    .ThenBy(s => s.Index).ToArray();
                all.AddRange(stacks);
                for (int i = 0; i < stacks.Length; i++)
                {
                    var target = stacks[i];
                    if (target.Remaining == 0) continue;
                    var rules = target.Silo.Rules[target.Index];
                    int space = rules.MaxCount - target.Remaining;
                    for (int j = stacks.Length - 1; j > i && space > 0; j--)
                    {
                        var source = stacks[j];
                        if (source.Remaining == 0 || source.Slot.Id.Pointer != target.Slot.Id.Pointer
                            || source.Slot.Appearance != target.Slot.Appearance
                            || !target.Slot.Metadata.Matches(source.Slot.Metadata)
                            || !target.Silo.Manager.CouldAddToSlot(source.Slot.Metadata, target.Index, false)) continue;
                        int count = Math.Min(space, source.Remaining);
                        moves.Add((source, target, count));
                        target.Remaining += count;
                        source.Remaining -= count;
                        space -= count;
                    }
                }
            }

            var plan = moves.Select(move => new Row
            {
                ["id"] = Id(move.From.Slot),
                ["from"] = move.From.Silo.Selector, ["from_slot"] = move.From.Index,
                ["to"] = move.To.Silo.Selector, ["to_slot"] = move.To.Index,
                ["count"] = move.Count,
            }).ToList();
            var result = new Row
            {
                ["available"] = true, ["dry_run"] = preview, ["plan"] = plan,
                ["planned_items"] = moves.Sum(m => m.Count), ["planned_freed_slots"] = all.Count(s => s.Remaining == 0),
            };
            if (preview) return result;

            int moved = 0;
            for (int i = 0; i < moves.Count; i++)
            {
                var (from, to, count) = moves[i];
                try
                {
                    int added = Transfer(from.Silo.Manager, new Move(from.Index, 0, to.Index, count, 0), to.Silo, from.Silo);
                    moved += added;
                    if (added != count) throw new InvalidOperationException("partial_move");
                }
                catch (Exception e)
                {
                    result["failure"] = new Row(plan[i]) { ["reason"] = e.GetBaseException().Message };
                    break;
                }
            }
            result["moved_items"] = moved;
            result["freed_slots"] = all.Count(s => s.Slot.Count == 0);
            return result;
        }
    }
}
