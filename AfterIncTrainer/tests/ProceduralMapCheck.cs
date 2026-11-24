using System.Text.Json;
using AfterIncLevelTraits;

internal static class ProceduralMapCheck
{
    public static void Run(string directory)
    {
        int maps = 0;
        var testedSizes = new HashSet<int>();
        int additionalSeeds = 0;
        foreach (var file in Directory.GetFiles(directory, "*.json"))
        {
            string source = File.ReadAllText(file);
            var first = ProceduralMap.Generate(source, 20031);
            var second = ProceduralMap.Generate(source, 41297);
            if (first.Json != ProceduralMap.Generate(source, 20031).Json || first.Fingerprint == second.Fingerprint)
                throw new Exception("Map seed stability");
            var oldGraph = Edges(source);
            var newGraph = Edges(first.Json);
            if (newGraph.SetEquals(oldGraph) || newGraph.SetEquals(Edges(second.Json)))
                throw new Exception("Map graph unchanged");
            using var doc = JsonDocument.Parse(first.Json);
            var zones = doc.RootElement.GetProperty("zones").GetProperty("zones").EnumerateArray().ToList();
            var positions = zones.Select(z => (z.GetProperty("x").GetDouble(), z.GetProperty("y").GetDouble())).ToHashSet();
            if (positions.Count != first.Land || newGraph.Count != first.Links) throw new Exception("Map counts");
            CheckRiver(doc.RootElement, first);
            foreach (var zone in zones)
            {
                var polygon = zone.GetProperty("pos").EnumerateArray().Select(Point).ToArray();
                var origin = Point(zone);
                var buildings = zone.GetProperty("bPos").EnumerateArray().Select(Point).ToArray();
                if (buildings.Length != 4 || buildings.Distinct().Count() != 4)
                    throw new Exception("A tile needs four distinct building anchors");
                foreach (var point in buildings.Append(Point(zone.GetProperty("nPos"))))
                {
                    for (int i = 0; i < polygon.Length; i++)
                    {
                        var a = polygon[i]; var b = polygon[(i+1)%polygon.Length];
                        if ((b.X-a.X)*(point.Y-origin.Y-a.Y) - (b.Y-a.Y)*(point.X-origin.X-a.X) < -1e-5)
                            throw new Exception("Building outside tile");
                    }
                }
            }
            if (testedSizes.Add(first.Land))
            {
                var seeds = new Random(83211 + first.Land);
                for (int trial = 0; trial < 24; trial++)
                {
                    var sample = ProceduralMap.Generate(source, seeds.Next());
                    using var sampleDoc = JsonDocument.Parse(sample.Json);
                    CheckRiver(sampleDoc.RootElement, sample);
                    additionalSeeds++;
                }
            }
            maps++;
        }
        if (maps == 0) throw new Exception("No original maps found");
        Console.WriteLine($"Procedural geometry, adjacency, connectivity and reproducibility verified on {maps} base maps; river and bridge invariants passed {additionalSeeds} additional seeds.");
    }

    private static void CheckRiver(JsonElement world, MapLayout layout)
    {
        var zones = world.GetProperty("zones");
        var land = zones.GetProperty("zones").EnumerateArray().ToDictionary(z => z.GetProperty("id").GetString()!);
        var water = zones.GetProperty("waterZones").EnumerateArray().ToDictionary(z => z.GetProperty("id").GetString()!);
        var river = water.Where(pair => !pair.Value.GetProperty("hint").GetBoolean()).ToDictionary(p => p.Key, p => p.Value);
        static string[] Ids(JsonElement z, string key) => z.GetProperty(key).EnumerateArray().Select(n => n.GetProperty("id").GetString()!).ToArray();
        int crossings = 0;
        foreach (var (id, z) in land)
        foreach (var other in Ids(z, "bc"))
        {
            if (!Ids(land[other], "bc").Contains(id) || Ids(z, "nc").Contains(other))
                throw new Exception("Bridge adjacency must be symmetric and distinct from land adjacency");
            if (Ids(z, "wnc").Intersect(Ids(land[other], "wnc")).Count(river.ContainsKey) != 1)
                throw new Exception("Each bridge needs one river segment between its banks");
            crossings++;
        }
        int riverLinks = 0;
        foreach (var (id, segment) in river)
        {
            foreach (var n in Ids(segment, "nc"))
                if (!Ids(land[n], "wnc").Contains(id)) throw new Exception("Missing water neighbour");
            foreach (var n in Ids(segment, "wnc"))
                if (!Ids(water[n], "wnc").Contains(id)) throw new Exception("Water links not symmetric");
            riverLinks += Ids(segment, "wnc").Count(river.ContainsKey);
        }
        var sprites = world.GetProperty("widgets").GetProperty("widgetLayers")[0].GetProperty("widgets").EnumerateArray()
            .Where(w => w.GetProperty("res").GetString()!.StartsWith("long_arched_bridge")).SelectMany(w => w.GetProperty("posData").EnumerateArray()).ToList();
        foreach (var sprite in sprites)
        {
            var (x, y) = Point(sprite);
            if (!river.Values.Any(r => {
                var banks = Ids(r, "nc");
                return banks.Length == 2 && Ids(land[banks[0]], "bc").Contains(banks[1])
                    && Math.Abs(x - Point(r).X) <= 0.501 && Math.Abs(y - Point(r).Y) <= 0.501;
            })) throw new Exception("Bridge artwork off crossing");
        }
        var rivers = world.GetProperty("rivers").GetProperty("rivers");
        if (river.Count < 2 || riverLinks != (river.Count-1)*2 || rivers.GetArrayLength() != layout.Rivers
            || crossings != layout.Bridges*2 || sprites.Count != layout.Bridges || layout.Bridges < 1)
            throw new Exception("River or bridge count mismatch");
        var visited = new HashSet<string>();
        int banksCount = 0;
        foreach (var id in land.Keys)
        {
            if (!visited.Add(id)) continue;
            banksCount++;
            var queue = new Queue<string>(); queue.Enqueue(id);
            while (queue.Count > 0)
                foreach (var n in Ids(land[queue.Dequeue()], "nc"))
                    if (visited.Add(n)) queue.Enqueue(n);
        }
        if (banksCount != 2) throw new Exception("River must separate exactly two banks");
    }

    private static (double X, double Y) Point(JsonElement p) => (p.GetProperty("x").GetDouble(), p.GetProperty("y").GetDouble());
    private static HashSet<string> Edges(string json)
    {
        using var doc = JsonDocument.Parse(json);
        var edges = new HashSet<string>();
        foreach (var zone in doc.RootElement.GetProperty("zones").GetProperty("zones").EnumerateArray())
        {
            var a = zone.GetProperty("id").GetString()!;
            foreach (var n in zone.GetProperty("nc").EnumerateArray().Concat(zone.GetProperty("bc").EnumerateArray()))
            {
                var b = n.GetProperty("id").GetString()!;
                edges.Add(string.CompareOrdinal(a,b) < 0 ? a + "|" + b : b + "|" + a);
            }
        }
        return edges;
    }
}

