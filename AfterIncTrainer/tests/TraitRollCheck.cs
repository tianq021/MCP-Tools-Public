using System.Text.Json;
using AfterIncLevelTraits;

const string build = "26049821";
var profile = new TraitProfile(2, build, 80702, new(2, 8), new(1, 4), 2, 22, null, null, null);
Effect[] catalogue =
{
    new("ice", "weather", 0, 10, 0, 4),
    new("rain", "weather", 0, 10, 0, 4),
    new("coastal", "terrain", 0, 10, 0, 4),
    new("minerals", "terrain", 0, 10, 0, 4),
    new("delayed", null, 6, 8, 2, 3)
};
Effect[] active = { catalogue[0] };
var observed = new HashSet<(float Ambition, int Danger)>();
for (var token = 0; token < 300; token++)
{
    var current = profile with { RollSeed = token };
    var result = TraitLottery.Pick(current, 0, 0, 4, catalogue, active);
    Check(result.Error is null && result.Roll is not null, "eligible combination was rejected");
    var roll = result.Roll!;
    observed.Add((roll.Ambition, roll.Danger));
    Check(roll.AddedModifiers.Length == 2 && roll.AddedModifiers.Distinct().Count() == 2 &&
          roll.AddedModifiers.Contains("delayed") &&
          !roll.AddedModifiers.Contains("rain") && !roll.AddedModifiers.Contains("ice"),
          "wrong count, duplicate, or conflicting family");
    var repeat = TraitLottery.Pick(current, 0, 0, 4, catalogue.Reverse().ToArray(), active).Roll;
    Check(repeat?.Ambition == roll.Ambition && repeat.Danger == roll.Danger &&
          repeat.AddedModifiers.SequenceEqual(roll.AddedModifiers), "roll is unstable");
}
Check(observed.SetEquals(from ambition in Enumerable.Range(6, 3)
                        from danger in Enumerable.Range(2, 2)
                        select ((float)ambition, danger)), "did not cover the feasible inclusive ranges");
var shortage = TraitLottery.Pick(profile with { Count = 3 }, 0, 0, 4, catalogue, active);
Check(shortage.Roll is null && shortage.AvailableCount == 2 &&
      shortage.Error == "not_enough_modifiers", "shortage returned an incomplete roll");
var variable = profile with { Schema = 3, Count = 0, CountRange = new(0, 5) };
Check(variable.IsValid(build), "valid count range rejected");
var counts = new HashSet<int>();
for (var token = 0; token < 300; token++)
{
    var current = variable with { RollSeed = token };
    var roll = TraitLottery.Pick(current, 0, 0, 4, catalogue, active).Roll!;
    counts.Add(roll.AddedModifiers.Length);
    if (roll.AddedModifiers.Length == 2)
        Check(roll.Ambition is >= 6 and <= 8 && roll.Danger is >= 2 and <= 3,
              "drawn values do not support the chosen count");
}
Check(counts.SetEquals(new[] { 0, 1, 2 }), "count lottery did not use its feasible range");
Check(!((variable with { CountRange = new(3, 2) }).IsValid(build)), "reversed count range accepted");
var fixedCount = variable with { CountRange = new(2, 2) };
var fixedRoll = TraitLottery.Pick(fixedCount, 0, 0, 4, catalogue, active).Roll!;
var previousRoll = TraitLottery.Pick(profile, 0, 0, 4, catalogue, active).Roll!;
Check(fixedRoll.Ambition == previousRoll.Ambition && fixedRoll.Danger == previousRoll.Danger &&
      fixedRoll.AddedModifiers.SequenceEqual(previousRoll.AddedModifiers), "fixed count changed the previous draw");
Check(TraitLottery.Pick(variable with { CountRange = new(3, 5) }, 0, 0, 4, catalogue, active)
      .Error == "not_enough_modifiers", "minimum count was not respected");
var extra = profile with { AmbitionRange = null, DangerRange = new(3, 4), Count = 0 };
var extraRoll = TraitLottery.Pick(extra, 2.5f, 0, 3, catalogue, active).Roll;
Check(extraRoll?.Danger == 3 && extraRoll.Ambition == 2.5f &&
      extraRoll.AddedModifiers.Length == 0, "extra cap, zero count or omitted range failed");
Check(TraitLottery.Pick(extra with { DangerRange = new(4, 4) }, 2.5f, 0, 3, catalogue, active)
      .Error == "extra_danger_range", "unsupported extra range was accepted");
var legacy = JsonSerializer.Deserialize<TraitProfile>(
    """{"schema":1,"build":"26049821","seed":80702,"ambition":3,"danger":1,"modifier":"coastal"}""",
    new JsonSerializerOptions(JsonSerializerDefaults.Web));
Check(legacy is not null && legacy.IsValid(build), "legacy profile did not load");
var legacyRoll = TraitLottery.Pick(legacy!, 0, 0, 4, catalogue, active).Roll;
Check(legacyRoll?.Ambition == 3 && legacyRoll.Danger == 1 &&
      legacyRoll.AddedModifiers.SequenceEqual(new[] { "coastal" }), "legacy profile changed");
Console.WriteLine("Trait lottery checks passed.");
RandomEffectsCheck.Run();
if (args.Length >= 1) RandomEffectsCheck.CheckCatalogue(args[0]);
if (args.Length == 2) ProceduralMapCheck.Run(args[1]);

static void Check(bool condition, string error)
{
    if (!condition)
        throw new InvalidOperationException(error);
}
