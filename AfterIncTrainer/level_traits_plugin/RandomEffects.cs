namespace AfterIncLevelTraits;

internal sealed record AddOptions(string[] Modifiers, int? Ambition, int? Danger, int RandomMin = 0, int RandomMax = 0,
    bool ProceduralMap = false, int? TotalMin = null, int? TotalMax = null, string? BatchId = null)
{
    internal (float Ambition, int Danger) ResolveLevels(float gameAmbition, int gameDanger, IEnumerable<Choice> selected)
    {
        float minAmbition = 0, maxAmbition = float.MaxValue;
        float minDanger = 0, maxDanger = 4;
        foreach (var choice in selected)
        {
            minAmbition = Math.Max(minAmbition, choice.AmbitionMin);
            maxAmbition = Math.Min(maxAmbition, choice.AmbitionMax);
            minDanger = Math.Max(minDanger, MathF.Ceiling(choice.DangerMin));
            maxDanger = Math.Min(maxDanger, MathF.Floor(choice.DangerMax));
            if (minAmbition > maxAmbition || minDanger > maxDanger)
                throw new InvalidDataException($"「{choice.Title}」与其他手选特性没有共同的雄心或危险度范围");
            if (Ambition.HasValue && (Ambition < minAmbition || Ambition > maxAmbition))
                throw new InvalidDataException($"「{choice.Title}」不适用于指定雄心 {Ambition}");
            if (Danger.HasValue && (Danger < minDanger || Danger > maxDanger))
                throw new InvalidDataException($"「{choice.Title}」不适用于指定危险度 {Danger}");
        }
        return (Ambition ?? Math.Clamp(gameAmbition, minAmbition, maxAmbition),
            Danger ?? Math.Clamp(gameDanger, (int)minDanger, (int)maxDanger));
    }
}

internal sealed record Choice(string Title, string? Family, string[] Excludes, float AmbitionMin,
    float AmbitionMax, float DangerMin, float DangerMax, bool CanRoll = true)
{
    internal bool Conflicts(string id, string otherId, Choice other) =>
        id != otherId && (Title == other.Title || (!string.IsNullOrEmpty(Family) && Family == other.Family) ||
            Excludes.Contains(otherId) || other.Excludes.Contains(id));

    internal bool Fits(float ambition, int danger) =>
        ambition >= AmbitionMin && ambition <= AmbitionMax && danger >= DangerMin && danger <= DangerMax;
}

internal sealed record TraitPlan(Dictionary<string, Choice> Final, string[] Drawn);

internal sealed class AddedTraitPlanner
{
    private readonly Dictionary<int, (string Batch, TraitPlan Plan)> used = new();

    internal void KeepActive(Dictionary<int, AddOptions> settings)
    {
        foreach (var seed in used.Keys.ToArray())
            if (!settings.TryGetValue(seed, out var options) || used[seed].Batch != options.BatchId)
                used.Remove(seed);
    }

    internal void Remember(int seed, AddOptions options, TraitPlan plan)
    {
        if (options.BatchId is not null) used[seed] = (options.BatchId, plan);
    }

    internal void Forget(int seed) => used.Remove(seed);

    internal bool Matches(int seed, HashSet<string> ids) =>
        used.TryGetValue(seed, out var entry) && ids.SetEquals(entry.Plan.Final.Keys);

    internal TraitPlan Choose(int seed, AddOptions options, float ambition, int danger,
        Dictionary<string, Choice> manual, IEnumerable<KeyValuePair<string, Choice>> originals,
        IReadOnlyDictionary<string, Choice> pool)
    {
        bool bounded = options.TotalMax.HasValue;
        int min = options.TotalMin ?? 0, max = options.TotalMax ?? int.MaxValue;
        if (bounded != options.TotalMin.HasValue || min < 0 || max < min || manual.Count > max)
            throw new InvalidDataException("每关特性总数范围与手选数量不符");

        if (options.BatchId is not null && used.TryGetValue(seed, out var prior) &&
            prior.Batch == options.BatchId && prior.Plan.Final.Values.All(item => item.Fits(ambition, danger)))
            return prior.Plan;

        var reserved = used.Where(p => p.Key != seed && p.Value.Batch == options.BatchId)
            .SelectMany(p => p.Value.Plan.Final.Values.Select(item => item.Title)).ToHashSet(StringComparer.Ordinal);
        var final = new Dictionary<string, Choice>(manual);
        foreach (var (id, item) in originals)
        {
            if (final.Count >= max) break;
            if (final.ContainsKey(id) || reserved.Contains(item.Title) || !item.Fits(ambition, danger) ||
                final.Any(p => item.Conflicts(id, p.Key, p.Value))) continue;
            final.Add(id, item);
        }
        int need = bounded ? Math.Max(0, min - final.Count) : options.RandomMin;
        int room = bounded ? max - final.Count : options.RandomMax;
        var eligible = pool.Where(p => !reserved.Contains(p.Value.Title)).ToDictionary(p => p.Key, p => p.Value);
        var drawn = RandomEffects.Draw(seed, need, room, ambition, danger, eligible, final);
        foreach (var id in drawn) final.Add(id, pool[id]);
        return new(final, drawn);
    }
}

internal static class RandomEffects
{
    internal static string[] Draw(int seed, int minimum, int maximum, float ambition, int danger,
        IReadOnlyDictionary<string, Choice> catalogue, IReadOnlyDictionary<string, Choice> occupied)
    {
        if (minimum < 0 || maximum < minimum) throw new InvalidDataException("随机特性数量区间无效");
        if (maximum == 0) return Array.Empty<string>();

        var candidates = catalogue.Where(p => p.Value.CanRoll && p.Value.Fits(ambition, danger) &&
            !occupied.ContainsKey(p.Key) && !occupied.Any(o => p.Value.Conflicts(p.Key, o.Key, o.Value)))
            .Select(p => p.Key).OrderBy(id => id, StringComparer.Ordinal).ToList();
        var random = new Random(seed);
        Shuffle(candidates, random);
        bool Conflict(string a, string b) => catalogue[a].Conflicts(a, b, catalogue[b]);

        // Disconnected conflict groups can be solved independently. This finds the actual upper
        // bound even when picking a single effect would rule out several compatible alternatives.
        var largest = new List<string>();
        var visited = new HashSet<string>();
        foreach (var id in candidates)
        {
            if (!visited.Add(id)) continue;
            var group = new List<string> { id };
            for (int i = 0; i < group.Count; i++)
                foreach (var other in candidates)
                    if (!visited.Contains(other) && Conflict(group[i], other))
                    {
                        visited.Add(other);
                        group.Add(other);
                    }
            // At most one effect from each named family. Unrelated singletons keep their own group.
            var slots = group.GroupBy(key => (Family: !string.IsNullOrEmpty(catalogue[key].Family),
                                              Key: string.IsNullOrEmpty(catalogue[key].Family) ? key : catalogue[key].Family))
                             .Select(items => items.ToArray()).ToArray();
            var best = new List<string>();
            var picked = new List<string>();
            void Search(int i)
            {
                if (picked.Count + slots.Length - i <= best.Count) return;
                if (i == slots.Length)
                {
                    best = new List<string>(picked);
                    return;
                }
                foreach (var candidate in slots[i])
                {
                    if (picked.Any(p => Conflict(candidate, p))) continue;
                    picked.Add(candidate);
                    Search(i + 1);
                    picked.RemoveAt(picked.Count - 1);
                    if (best.Count == slots.Length) return;
                }
                Search(i + 1);
            }
            Search(0);
            largest.AddRange(best);
        }
        if (largest.Count < minimum)
            throw new InvalidDataException($"需要至少 {minimum} 项随机特性，本关最多可追加 {largest.Count} 项相容特性");
        int count = random.Next(minimum, Math.Min(maximum, largest.Count) + 1);
        var rolled = new List<string>();
        foreach (var id in candidates)
        {
            if (rolled.Count == count) break;
            if (!rolled.Any(p => Conflict(id, p))) rolled.Add(id);
        }
        if (rolled.Count == count) return rolled.ToArray();
        Shuffle(largest, random);
        return largest.Take(count).ToArray();
    }

    private static void Shuffle<T>(IList<T> items, Random random)
    {
        for (int i = items.Count - 1; i > 0; i--)
        {
            int other = random.Next(i + 1);
            (items[i], items[other]) = (items[other], items[i]);
        }
    }
}

