using System.Text.Json.Nodes;

namespace AfterIncLevelTraits;

internal static partial class ProceduralMap
{
    private readonly record struct SharedEdge(Point Lower, Point Upper)
    {
        internal Point Middle => (Lower + Upper) * 0.5;
    }

    private sealed record RiverSegment(int West, int East, SharedEdge Edge, bool Bridge = false);
    private sealed record River(double Divide, Point BankOffset, List<RiverSegment> Segments)
    {
        internal Point Offset(Point site) => BankOffset * (site.X < Divide ? -1 : 1);
    }

    private static Dictionary<(int, int), SharedEdge> SharedEdges(List<Cell> cells)
    {
        var edges = new Dictionary<(int, int), SharedEdge>();
        for (int i = 0; i < cells.Count; i++)
        foreach (int j in cells[i].Neighbours.Where(j => j > i))
        {
            var normal = cells[j].Site - cells[i].Site;
            var centre = (cells[j].Site + cells[i].Site) * 0.5;
            var ends = cells[i].Polygon.Where(p => Math.Abs((p - centre).Dot(normal)) < 0.00001)
                .OrderBy(p => p.Y).ThenBy(p => p.X).ToArray();
            if (ends.Length < 2) throw new InvalidDataException("找不到相邻地块的公共边");
            edges.Add((i, j), new(ends[0], ends[^1]));
        }
        return edges;
    }

    private static River CreateRiver(List<Cell> cells, int count, Dictionary<(int, int), SharedEdge> edges, Random random)
    {
        double divide = (random.NextDouble() - 0.5) * 320;
        var offset = new Point(19 + random.NextDouble() * 5, 0);
        var segments = new List<RiverSegment>();
        foreach (var (pair, edge) in edges)
        {
            var (i, j) = pair;
            if ((cells[i].Site.X < divide) == (cells[j].Site.X < divide)) continue;
            segments.Add(cells[i].Site.X < divide ? new(i, j, edge) : new(j, i, edge));
        }
        // Along a horizontal scanline the nearest site can switch banks only west to east.
        segments.Sort((a, b) => a.Edge.Middle.Y.CompareTo(b.Edge.Middle.Y));
        for (int i = 1; i < segments.Count; i++)
            if ((segments[i-1].Edge.Upper - segments[i].Edge.Lower).Length > 0.00001)
                throw new InvalidDataException("河段不连续");
        var possible = segments.Select((s, i) => (s, i)).Where(x => x.s.West < count && x.s.East < count).ToList();
        if (possible.Count == 0) throw new InvalidDataException("河岸上找不到可通行的桥址");
        double south = possible.First().s.Edge.Lower.Y, north = possible.Last().s.Edge.Upper.Y;
        int bridges = Math.Min(count >= 8 ? 2 : 1, possible.Count);
        for (int n = 0; n < bridges; n++)
        {
            double target = south + (north - south) * (n + 0.5) / bridges;
            var chosen = possible.MinBy(x => Math.Abs(x.s.Edge.Middle.Y - target)
                + Math.Max(0, 85 - (x.s.Edge.Upper.Y - x.s.Edge.Lower.Y)) * 2);
            segments[chosen.i] = chosen.s with { Bridge = true };
            possible.Remove(chosen);
        }
        return new(divide, offset, segments);
    }

    private static int WriteRiver(JsonObject world, River river, JsonArray land, JsonArray margin, int count, Func<int,string> id)
    {
        int originalMargin = margin.Count;
        string WaterId(int i) => $"Water Zone {originalMargin + i}";
        JsonNode Bank(int i) => i < count ? land[i]! : margin[i - count]!;
        var bridgeSites = new List<Point>();
        var centreline = new List<Point>();
        var widths = new List<double>();
        for (int i = 0; i < river.Segments.Count; i++)
        {
            var segment = river.Segments[i];
            var edge = segment.Edge;
            var offset = river.BankOffset;
            var origin = edge.Middle;
            var corners = new[] { edge.Lower-offset, edge.Lower+offset, edge.Upper+offset, edge.Upper-offset };
            var node = new JsonObject {
                ["id"] = WaterId(i), ["x"] = origin.X, ["y"] = origin.Y, ["midX"] = origin.X, ["midY"] = origin.Y,
                ["pos"] = ToNode(corners.Select(p => p - origin)), ["sea"] = false, ["hint"] = false,
                ["nc"] = new JsonArray(), ["wnc"] = new JsonArray()
            };
            foreach (int bank in new[] { segment.West, segment.East })
            {
                node[bank < count ? "nc" : "wnc"]!.AsArray().Add(new JsonObject { ["id"] = id(bank) });
                Bank(bank)["wnc"]!.AsArray().Add(new JsonObject { ["id"] = WaterId(i) });
            }
            if (i > 0) node["wnc"]!.AsArray().Add(new JsonObject { ["id"] = WaterId(i-1) });
            if (i+1 < river.Segments.Count) node["wnc"]!.AsArray().Add(new JsonObject { ["id"] = WaterId(i+1) });
            margin.Add(node);

            if (segment.Bridge)
            {
                Bank(segment.West)["bc"]!.AsArray().Add(new JsonObject { ["id"] = id(segment.East) });
                Bank(segment.East)["bc"]!.AsArray().Add(new JsonObject { ["id"] = id(segment.West) });
                bridgeSites.Add(origin);
            }

            var direction = edge.Upper - edge.Lower;
            double width = Math.Max(1, offset.X * direction.Y / direction.Length);
            if (i == 0) { centreline.Add(edge.Lower); widths.Add(width); }
            else widths[^1] = (widths[^1] + width) * 0.5;
            centreline.Add(origin); widths.Add(width);
            centreline.Add(edge.Upper); widths.Add(width);
        }
        var riverList = world["rivers"]!["rivers"]!.AsArray();
        var spline = riverList.FirstOrDefault() is JsonNode template
            ? JsonNode.Parse(template.ToJsonString())!
            : new JsonObject { ["gradient"] = ToNode(new {
                col = Enumerable.Repeat(new { r=1, g=1, b=1, a=1 }, 2), ct = new[] {0,1}, a = new[] {1,1}, at = new[] {0,1}
            }) };
        var centre = (centreline[0] + centreline[^1]) * 0.5;
        spline["pos"] = ToNode(centre);
        spline["coords"] = ToNode(centreline.Select(p => p - centre));
        spline["w"] = ToNode(widths);
        spline["br"] = false; spline["lvl"] = 0; spline["si"] = 0; spline["isWaterSpline"] = false;
        riverList.Clear();
        riverList.Add(spline);
        var bridgeLayer = world["widgets"]!["widgetLayers"]![0]!["widgets"]!.AsArray();
        bridgeLayer.Add(new JsonObject {
            ["res"] = "long_arched_bridge_3",
            ["posData"] = ToNode(bridgeSites.Select(p => new { x = (int)Math.Round(p.X), y = (int)Math.Round(p.Y), fx = 0, zId = "" })),
            ["scaleData"] = ToNode(bridgeSites.Select(p => new { scale = 1.0 }))
        });
        return bridgeSites.Count;
    }
}
