using AfterIncLevelTraits;

internal static class RandomEffectsCheck
{
    private static Choice Trait(string? family = null, string[]? excludes = null) =>
        new("effect", family, excludes ?? Array.Empty<string>(), 0, 100, 0, 4);

    private static void Require(bool pass, string message)
    {
        if (!pass) throw new Exception(message);
    }

    public static void CheckCatalogue(string path)
    {
        var choices = System.Text.Json.JsonSerializer.Deserialize<Dictionary<string, Choice>>(
            File.ReadAllText(path), new System.Text.Json.JsonSerializerOptions(System.Text.Json.JsonSerializerDefaults.Web))!;
        var occupied = new Dictionary<string, Choice> { ["zone_production_drop_sheep"] = choices["zone_production_drop_sheep"] };
        var counts = new HashSet<int>();
        for (int seed = 20001; seed < 20101; seed++)
        {
            var rolled = RandomEffects.Draw(seed, 2, 4, 15, 2, choices, occupied);
            var all = rolled.Concat(occupied.Keys).ToArray();
            Require(rolled.All(id => choices[id].CanRoll && choices[id].Fits(15, 2)), "live catalogue range");
            Require(all.Distinct().Count() == all.Length &&
                    all.All(a => all.All(b => !choices[a].Conflicts(a, b, choices[b]))), "live catalogue conflict");
            counts.Add(rolled.Length);
        }
        Require(counts.SetEquals(new[] { 2, 3, 4 }), "catalogue range endpoints");
        // Replay the five maps reported on 2026-11-23 with their recorded native rolls.
        (int seed, float ambition, int danger, string[] native)[] batch = {
            (26774, 13.227709f, 2, Array.Empty<string>()), (35507, 8.74588f, 1, Array.Empty<string>()),
            (26740, 2.6924098f, 2, new[] { "highyield_production_boost", "explore_discount_forest" }),
            (58283, 12.427188f, 2, Array.Empty<string>()),
            (21988, 4.6847196f, 2, new[] { "soldier_consumption_distance", "zone_type_boost_fruit" })
        };
        var options = new AddOptions(Array.Empty<string>(), null, null, TotalMin: 2, TotalMax: 4, BatchId: "test");
        var planner = new AddedTraitPlanner();
        var manual = new Dictionary<string, Choice>();
        var seen = new HashSet<string>();
        foreach (var row in batch)
        {
            var native = row.native.Select(id => KeyValuePair.Create(id, choices[id]));
            var plan = planner.Choose(row.seed, options, row.ambition, row.danger, manual, native, choices);
            Require(plan.Final.Count is >= 2 and <= 4 && plan.Final.Values.All(item => seen.Add(item.Title)), "batch limit or duplicate");
            planner.Remember(row.seed, options, plan);
            Console.WriteLine($"Replay {row.seed}: {plan.Final.Count} [{string.Join(", ", plan.Final.Values.Select(c => c.Title))}]");
        }
        foreach (var row in batch.Reverse())
        {
            var native = row.native.Select(id => KeyValuePair.Create(id, choices[id]));
            var plan = planner.Choose(row.seed, options, row.ambition, row.danger, manual, native, choices);
            Require(planner.Matches(row.seed, plan.Final.Keys.ToHashSet()), "refresh changed the batch");
        }
        var fixedTwo = options with { TotalMin = 2, TotalMax = 2, BatchId = "manual" };
        manual["zone_type_boost_fruit"] = choices["zone_type_boost_fruit"];
        for (int seed = 1; seed <= 2; seed++)
        {
            var plan = planner.Choose(seed, fixedTwo, 15, 3, manual, choices, choices);
            Require(plan.Final.Count == 2 && plan.Final.ContainsKey("zone_type_boost_fruit"), "manual and native cap");
            planner.Remember(seed, fixedTwo, plan);
        }
        var zero = options with { TotalMin = 0, TotalMax = 0, BatchId = "zero" };
        Require(planner.Choose(42, zero, 10, 3, new(), choices, choices).Final.Count == 0, "explicit zero retained traits");
        Console.WriteLine($"Verified 100 rolls against {choices.Count} game catalogue entries.");
    }

    public static void Run()
    {
        var quarry = Trait() with { Title = "废弃采石场", AmbitionMin = 9 };
        var guard = Trait() with { Title = "城市守卫", DangerMin = 2 };
        Choice[] manual = { guard, quarry };
        var settings = new AddOptions(new[] { "guard", "quarry" }, null, 4);
        Require(settings.ResolveLevels(6.2676663f, 2, manual) == (9f, 4), "seed 37057");
        var automatic = settings with { Danger = null };
        Require(automatic.ResolveLevels(2.6201856f, 1, manual) == (9f, 2), "seed 33883");
        Require(automatic.ResolveLevels(10.5f, 3, manual) == (10.5f, 3), "compatible game roll changed");
        Require((settings with { Ambition = 15 }).ResolveLevels(2, 1, manual) == (15f, 4), "explicit values");
        try {
            (settings with { Ambition = 6 }).ResolveLevels(10f, 3, manual);
            throw new Exception("explicit incompatible ambition was overwritten");
        } catch (InvalidDataException) { }
        try {
            automatic.ResolveLevels(10, 4, new[] { guard, Trait() with { DangerMax = 1 } });
            throw new Exception("disjoint danger ranges accepted");
        } catch (InvalidDataException) { }
        var pool = new Dictionary<string, Choice> {
            ["native"] = Trait("native-family", new[] { "native-blocked" }), ["manual"] = Trait(),
            ["native-blocked"] = Trait(), ["related"] = Trait("native-family"),
            ["reverse"] = Trait(excludes: new[] { "manual" }), ["bad-range"] = Trait() with { DangerMin = 3 },
            ["disabled-roll"] = Trait() with { CanRoll = false },
            ["center"] = Trait(excludes: new[] { "left", "right" }),
            ["left"] = Trait(), ["right"] = Trait()
        };
        foreach (var id in pool.Keys.ToArray()) pool[id] = pool[id] with { Title = id };
        var occupied = new Dictionary<string, Choice> { ["native"] = pool["native"], ["manual"] = pool["manual"] };
        var counts = new HashSet<int>();
        for (int seed = 0; seed < 100; seed++)
        {
            var exact = RandomEffects.Draw(seed, 2, 2, 15, 2, pool, occupied);
            Require(exact.ToHashSet().SetEquals(new[] { "left", "right" }), "exact compatible set");
            var rolled = RandomEffects.Draw(seed, 0, 1000, 15, 2, pool, occupied);
            Require(rolled.All(x => x == "left" || x == "right" || x == "center"), "forbidden candidate");
            Require(rolled.SequenceEqual(RandomEffects.Draw(seed, 0, 1000, 15, 2, pool, occupied)), "unstable roll");
            counts.Add(rolled.Length);
        }
        Require(counts.SetEquals(new[] { 0, 1, 2 }), "inclusive range");
        try {
            RandomEffects.Draw(42, 3, 5, 15, 2, pool, occupied);
            throw new Exception("unreachable minimum accepted");
        } catch (InvalidDataException e) { Require(e.Message.Contains("最多可追加 2"), "wrong capacity"); }

        // Check the capacity search against exhaustive enumeration on small mixed exclusion/family graphs.
        var random = new Random(109);
        for (int trial = 0; trial < 100; trial++)
        {
            var ids = Enumerable.Range(0, 8).Select(i => i.ToString()).ToArray();
            var choices = ids.ToDictionary(id => id, id => Trait(
                random.Next(3) == 0 ? "family" : null,
                ids.Where(other => other != id && random.Next(6) == 0).ToArray()) with { Title = id });
            bool Compatible(string[] set) => set.All(a => set.All(b => !choices[a].Conflicts(a, b, choices[b])));
            int best = 0;
            for (int mask = 1; mask < 256; mask++)
            {
                var set = ids.Where((_, i) => (mask & (1 << i)) != 0).ToArray();
                if (set.Length > best && Compatible(set)) best = set.Length;
            }
            var drawn = RandomEffects.Draw(trial, best, best, 5, 2, choices, new Dictionary<string, Choice>());
            Require(drawn.Distinct().Count() == best && Compatible(drawn), "maximum capacity mismatch");
            try {
                RandomEffects.Draw(trial, best + 1, best + 1, 5, 2, choices, new Dictionary<string, Choice>());
                throw new Exception("impossible choice");
            } catch (InvalidDataException) { }
        }
        Console.WriteLine("Added-level random effects checks passed.");
    }
}

