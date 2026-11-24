namespace AfterIncLevelTraits;

internal sealed record IntRange(int Min, int Max)
{
    internal bool Within(int limit) => Min >= 0 && Min <= Max && Max <= limit;

    internal IEnumerable<int> Values(int limit) =>
        Enumerable.Range(Min, Math.Max(0, Math.Min(Max, limit) - Min + 1));
}

internal sealed record TraitProfile(int Schema, string Build, int Seed, IntRange? AmbitionRange,
    IntRange? DangerRange, int Count, int? RollSeed, int? Ambition, int? Danger, string? Modifier,
    IntRange? CountRange = null)
{
    internal IntRange? Ambitions => AmbitionRange ?? (Ambition is int value ? new(value, value) : null);
    internal IntRange? Dangers => DangerRange ?? (Danger is int value ? new(value, value) : null);
    internal IntRange Counts => CountRange ?? new(Count, Count);

    internal bool IsValid(string build)
    {
        if (Build != build || Seed <= 0)
            return false;
        return Schema switch
        {
            1 => AmbitionRange is null && DangerRange is null && CountRange is null && Count == 0 && RollSeed is null &&
                 Ambition is not (< 0 or > 10) && Danger is not (< 0 or > 4) &&
                 (Ambition.HasValue || Danger.HasValue || !string.IsNullOrWhiteSpace(Modifier)),
            2 or 3 => Ambition is null && Danger is null && Modifier is null && RollSeed is >= 0 &&
                      (Schema == 2 ? CountRange is null && Count >= 0
                                   : Count == 0 && CountRange?.Within(int.MaxValue) == true) &&
                      AmbitionRange?.Within(10) != false && DangerRange?.Within(4) != false &&
                      (Counts.Max > 0 || AmbitionRange is not null || DangerRange is not null),
            _ => false
        };
    }
}

internal sealed record Effect(string Id, string? Family, float AmbitionMin, float AmbitionMax,
    float DangerMin, float DangerMax)
{
    internal (bool HasFamily, string Key) Group =>
        string.IsNullOrEmpty(Family) ? (false, Id) : (true, Family);

    internal bool Fits(float ambition, int danger) =>
        ambition >= AmbitionMin && ambition <= AmbitionMax && danger >= DangerMin && danger <= DangerMax;
}

internal sealed record TraitRoll(float Ambition, int Danger, string[] AddedModifiers);

internal static class TraitLottery
{
    internal static (TraitRoll? Roll, string? Error, int AvailableCount) Pick(TraitProfile profile,
        float originalAmbition, int originalDanger, int dangerLimit,
        IReadOnlyList<Effect> catalogue, IReadOnlyList<Effect> active)
    {
        var ambitions = profile.Ambitions?.Values(10).Select(value => (float)value) ?? new[] { originalAmbition };
        var dangers = profile.Dangers?.Values(dangerLimit).ToArray() ?? new[] { originalDanger };
        if (dangers.Length == 0)
            return (null, "extra_danger_range", 0);

        var activeIds = active.Select(effect => effect.Id).ToHashSet(StringComparer.Ordinal);
        var activeGroups = active.Select(effect => effect.Group).ToHashSet();
        var available = catalogue.Where(effect => !activeIds.Contains(effect.Id) &&
                                                  !activeGroups.Contains(effect.Group))
            .OrderBy(effect => effect.Id, StringComparer.Ordinal).ToArray();

        // Build 26049821.5 stored one specific effect and fixed values.
        if (profile.Modifier is { } id && !activeIds.Contains(id))
        {
            var effect = catalogue.FirstOrDefault(item => item.Id == id);
            if (effect is null)
                return (null, "modifier_unavailable", 0);
            if (activeGroups.Contains(effect.Group))
                return (null, "modifier_family_conflict", 0);
            var ambition = ambitions.Single();
            return effect.Fits(ambition, dangers[0])
                ? (new TraitRoll(ambition, dangers[0], new[] { id }), null, 1)
                : (null, "modifier_range", 0);
        }

        var random = new Random(profile.Seed ^ (profile.RollSeed ?? 0));
        var counts = profile.Counts;
        var choices = new List<(float Ambition, int Danger, Effect[][] Groups)>();
        var maximum = 0;
        foreach (var ambition in ambitions)
        foreach (var danger in dangers)
        {
            var groups = counts.Max == 0
                ? Array.Empty<Effect[]>()
                : available.Where(effect => effect.Fits(ambition, danger))
                    .GroupBy(effect => effect.Group).Select(group => group.ToArray()).ToArray();
            maximum = Math.Max(maximum, groups.Length);
            if (groups.Length >= counts.Min)
                choices.Add((ambition, danger, groups));
        }
        if (choices.Count == 0)
            return (null, "not_enough_modifiers", maximum);

        var countLimit = Math.Min(counts.Max, maximum);
        var count = countLimit == counts.Min ? counts.Min : (int)random.NextInt64(counts.Min, (long)countLimit + 1);
        var eligibleChoices = count == counts.Min ? choices : choices.Where(choice => choice.Groups.Length >= count).ToList();
        var choice = eligibleChoices[random.Next(eligibleChoices.Count)];
        var selected = new string[count];
        for (var i = 0; i < selected.Length; i++)
        {
            var index = random.Next(i, choice.Groups.Length);
            (choice.Groups[i], choice.Groups[index]) = (choice.Groups[index], choice.Groups[i]);
            var group = choice.Groups[i];
            selected[i] = group[random.Next(group.Length)].Id;
        }
        return (new TraitRoll(choice.Ambition, choice.Danger, selected), null, maximum);
    }
}
