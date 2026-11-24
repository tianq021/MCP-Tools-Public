using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace AfterIncLevelTraits;

internal sealed record MapLayout(string Json, int Land, int Links, int Rivers, int Bridges, string Fingerprint);

internal static partial class ProceduralMap
{
    internal readonly record struct Point(double X, double Y)
    {
        public static Point operator +(Point a, Point b) => new(a.X + b.X, a.Y + b.Y);
        public static Point operator -(Point a, Point b) => new(a.X - b.X, a.Y - b.Y);
        public static Point operator *(Point a, double scale) => new(a.X * scale, a.Y * scale);
        public double Dot(Point other) => X * other.X + Y * other.Y;
        [System.Text.Json.Serialization.JsonIgnore]
        public double Length => Math.Sqrt(Dot(this));
    }

    private static readonly JsonSerializerOptions Options = new(JsonSerializerDefaults.Web);
    private sealed record Cell(Point Site, List<Point> Polygon, List<int> Neighbours);
    private static Point Site(JsonNode zone) => new((double)zone["midX"]!, (double)zone["midY"]!);
    private static JsonNode ToNode<T>(T value) => JsonSerializer.SerializeToNode(value, Options)!;

    // Clip a convex polygon to the half-plane nearer a than b.
    private static List<Point> Bisect(List<Point> polygon, Point a, Point b)
    {
        Point normal = b - a, middle = (a + b) * 0.5;
        var clipped = new List<Point>();
        var last = polygon[^1];
        double before = (last - middle).Dot(normal);
        foreach (var next in polygon)
        {
            double after = (next - middle).Dot(normal);
            if ((before > 0) != (after > 0))
                clipped.Add(last + (next - last) * (before / (before - after)));
            if (after <= 0) clipped.Add(next);
            last = next;
            before = after;
        }
        return clipped;
    }

    private static List<Cell> Partition(List<Point> sites, double halfWidth, double halfHeight)
    {
        var result = new List<Cell>();
        for (int i = 0; i < sites.Count; i++)
        {
            var polygon = new List<Point> { new(-halfWidth,-halfHeight), new(halfWidth,-halfHeight),
                new(halfWidth,halfHeight), new(-halfWidth,halfHeight) };
            for (int j = 0; j < sites.Count; j++)
                if (j != i) polygon = Bisect(polygon, sites[i], sites[j]);
            if (polygon.Count < 3) throw new InvalidDataException("生成了无面积的地块");
            var neighbours = new List<int>();
            for (int edge = 0; edge < polygon.Count; edge++)
            {
                var a = polygon[edge];
                var b = polygon[(edge + 1) % polygon.Count];
                if ((a-b).Length < 0.0001) continue;
                var mid = (a+b) * 0.5;
                for (int j = 0; j < sites.Count; j++)
                {
                    if (i == j) continue;
                    if (Math.Abs((mid - (sites[i]+sites[j])*0.5).Dot(sites[j]-sites[i])) < 0.00001)
                    {
                        neighbours.Add(j);
                        break;
                    }
                }
            }
            result.Add(new(sites[i], polygon, neighbours));
        }
        return result;
    }

    private static void Validate(List<Cell> cells, int land)
    {
        var visited = new HashSet<int> { 0 };
        var queue = new Queue<int>();
        queue.Enqueue(0);
        while (queue.Count != 0)
        {
            int i = queue.Dequeue();
            foreach (int j in cells[i].Neighbours)
            {
                if (!cells[j].Neighbours.Contains(i)) throw new InvalidDataException("地块邻接不对称");
                if (j < land && visited.Add(j)) queue.Enqueue(j);
            }
        }
        if (visited.Count != land) throw new InvalidDataException("地图存在不可达的陆地");
    }

    internal static MapLayout Generate(string original, int seed)
    {
        var world = JsonNode.Parse(original)!.AsObject();
        var source = world["zones"]!["zones"]!.AsArray();
        int count = source.Count;
        if (count < 4 || count > 250) throw new InvalidDataException($"不支持的底图区域数 {count}");
        var random = new Random(seed);
        int rows = Math.Clamp((int)Math.Round(Math.Sqrt(count / 1.7)), 2, count / 2);
        const double width = 1540, height = 880, rim = 205;
        double Jitter() => (random.NextDouble() - 0.5) * 0.78;
        var sites = new List<Point>();
        for (int row = 0; row < rows; row++)
        {
            int columns = (row + 1) * count / rows - row * count / rows;
            for (int col = 0; col < columns; col++)
                sites.Add(new Point(((col + 0.5 + Jitter()) / columns - 0.5) * width,
                                    ((row + 0.5 + Jitter()) / rows - 0.5) * height));
        }
        // Land IDs keep native objective/resource references, but their geographic locations vary by seed.
        for (int i = sites.Count - 1; i > 0; i--)
        {
            int j = random.Next(i+1);
            (sites[i], sites[j]) = (sites[j], sites[i]);
        }
        for (int side = -1; side <= 1; side += 2)
        {
            for (int i = 0; i < 8; i++)
                sites.Add(new Point(((i + 0.5 + Jitter()*0.4)/8 - 0.5)*(width+rim),
                                    side*(height*0.5+rim*(0.25+random.NextDouble()*0.45))));
            for (int i = 0; i < 4; i++)
                sites.Add(new Point(side*(width*0.5+rim*(0.25+random.NextDouble()*0.45)),
                                    ((i+0.5+Jitter()*0.4)/4-0.5)*height));
        }
        var cells = Partition(sites, width*0.5+rim*1.6, height*0.5+rim*1.6);
        var edges = SharedEdges(cells);
        var river = CreateRiver(cells, count, edges, random);
        var offsets = cells.Select(cell => river.Offset(cell.Site)).ToArray();
        for (int i = 0; i < cells.Count; i++)
        {
            var originalCell = cells[i];
            var offset = offsets[i];
            cells[i] = new Cell(originalCell.Site + offset, originalCell.Polygon.Select(p => p + offset).ToList(),
                originalCell.Neighbours.Where(j => offsets[i] == offsets[j]).ToList());
        }
        var land = new JsonArray();
        var margin = new JsonArray();
        var oldCenters = new Dictionary<string, Point>();
        var geometry = new Dictionary<string, Cell>();
        int links = 0;
        string Id(int i) => i < count ? (string)source[i]!["id"]! : $"Water Zone {i-count}";
        for (int i = 0; i < cells.Count; i++)
        {
            var cell = cells[i];
            string id = Id(i);
            var node = new JsonObject {
                ["id"] = id, ["x"] = cell.Site.X, ["y"] = cell.Site.Y,
                ["midX"] = cell.Site.X, ["midY"] = cell.Site.Y,
                ["pos"] = ToNode(cell.Polygon.Select(p => p - cell.Site)),
                ["nc"] = ToNode(cell.Neighbours.Where(j => j < count).Select(j => new { id = Id(j) })),
                ["wnc"] = ToNode(cell.Neighbours.Where(j => j >= count).Select(j => new { id = Id(j) }))
            };
            if (i >= count)
            {
                // Hinterland is part of the outer border used by the native infestation rules.
                node["hint"] = true; node["sea"] = false;
                margin.Add(node);
                continue;
            }
            var old = source[i]!;
            oldCenters.Add(id, Site(old));
            geometry.Add(id, cell);
            node["terrainType"] = (int)old["terrainType"]!;
            node["resourceType"] = (int)old["resourceType"]!;
            node["edge"] = cell.Neighbours.Any(j => j >= count);
            node["cliff"] = false;
            node["bc"] = new JsonArray();
            node["lscape"] = new JsonArray();
            node["nPos"] = ToNode(cell.Site);
            var corners = cell.Polygon;
            var anchors = corners.Count == 3
                ? corners.Append((corners[2] + corners[0]) * 0.5)
                : Enumerable.Range(0, 4).Select(k => corners[k * corners.Count / 4]);
            node["bPos"] = ToNode(anchors.Select(point => cell.Site + (point - cell.Site) * 0.42));
            land.Add(node);
            links += cell.Neighbours.Count(j => j < count && j > i);
        }
        RepositionWidgets(world["widgets"]!, oldCenters, geometry);
        int bridgeCount = WriteRiver(world, river, land, margin, count, Id);
        var paths = cells.Select(cell => cell.Neighbours.ToList()).ToList();
        foreach (var cross in river.Segments.Where(segment => segment.Bridge))
        {
            paths[cross.West].Add(cross.East);
            paths[cross.East].Add(cross.West);
        }
        Validate(cells.Select((cell, i) => cell with { Neighbours = paths[i] }).ToList(), count);
        RebuildRoads(world, cells, count, paths, edges, offsets);
        world["zones"] = new JsonObject { ["zones"] = land, ["waterZones"] = margin };
        world["beaches"]!["beaches"] = new JsonArray();
        var json = world.ToJsonString();
        var hash = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(json)));
        return new(json, count, links + bridgeCount, 1, bridgeCount, hash);
    }

    private static void RepositionWidgets(JsonNode root, Dictionary<string,Point> origins, Dictionary<string,Cell> cells)
    {
        var layers = root["widgetLayers"]!.AsArray().ToList();
        layers.Add(root["background"]);
        layers.Add(root["foreground"]);
        foreach (var layer in layers)
        {
            var widgets = layer!["widgets"]!.AsArray();
            for (int w = widgets.Count-1; w >= 0; w--)
            {
                var widget = widgets[w]!;
                if (((string?)widget["res"])?.Contains("bridge", StringComparison.OrdinalIgnoreCase) == true)
                {
                    widgets.RemoveAt(w);
                    continue;
                }
                var positions = widget["posData"]!.AsArray();
                var scales = widget["scaleData"]?.AsArray();
                for (int i = positions.Count-1; i >= 0; i--)
                {
                    var item = positions[i]!;
                    var id = (string?)item["zId"] ?? "";
                    if (!cells.TryGetValue(id, out var cell))
                    {
                        positions.RemoveAt(i);
                        if (scales != null && i < scales.Count) scales.RemoveAt(i);
                        continue;
                    }
                    var delta = new Point((double)item["x"]!, (double)item["y"]!) - origins[id];
                    // Clamp the offset to the cell's inscribed circle so decorations stay on their own tile.
                    double radius = double.PositiveInfinity;
                    for (int k = 0; k < cell.Polygon.Count; k++)
                    {
                        var edge = cell.Polygon[(k+1)%cell.Polygon.Count] - cell.Polygon[k];
                        var local = cell.Site - cell.Polygon[k];
                        if (edge.Length > 0.0001)
                            radius = Math.Min(radius, Math.Abs(edge.X*local.Y-edge.Y*local.X)/edge.Length);
                    }
                    var at = cell.Site + delta * Math.Min(0.8, radius*0.85/Math.Max(1,delta.Length));
                    item["x"] = (int)Math.Round(at.X);
                    item["y"] = (int)Math.Round(at.Y);
                }
                if (positions.Count == 0) widgets.RemoveAt(w);
            }
        }
    }

    private static void RebuildRoads(JsonObject world, List<Cell> cells, int count, List<List<int>> paths,
        Dictionary<(int, int), SharedEdge> edges, Point[] offsets)
    {
        var roadList = world["roads"]!["roads"]!.AsArray();
        var templates = roadList.Where(x => x is not null && (bool?)x["br"] != true).ToList();
        var basic = templates.FirstOrDefault(x => (int?)x!["type"] == 0);
        var motorway = templates.FirstOrDefault(x => (int?)x!["type"] == 2);
        roadList.Clear();
        if (basic is null) return;
        // Place the main road along a connected west-to-east path if the palette includes a motorway.
        int start = Enumerable.Range(0,count).MinBy(i => cells[i].Site.X);
        int end = Enumerable.Range(0,count).MaxBy(i => cells[i].Site.X);
        var parent = new Dictionary<int,int> { [start] = -1 };
        var queue = new Queue<int>(); queue.Enqueue(start);
        while (queue.Count > 0 && !parent.ContainsKey(end))
        {
            int i = queue.Dequeue();
            foreach (int j in paths[i].Where(n => n < count))
                if (parent.TryAdd(j,i)) queue.Enqueue(j);
        }
        var trunk = new HashSet<(int,int)>();
        for (int i = end; parent[i] >= 0; i = parent[i])
            trunk.Add((Math.Min(i,parent[i]), Math.Max(i,parent[i])));
        for (int i = 0; i < count; i++)
        foreach (int j in paths[i].Where(n => n < count && n > i))
        {
            var template = motorway is not null && trunk.Contains((i,j)) ? motorway : basic;
            var crossing = edges[(i, j)].Middle;
            Point bankA = crossing + offsets[i], bankB = crossing + offsets[j];
            AddRoadLeg(template!, cells[i].Site, bankA);
            AddRoadLeg(template!, bankB, cells[j].Site);
        }

        void AddRoadLeg(JsonNode template, Point a, Point b)
        {
            var road = JsonNode.Parse(template.ToJsonString())!;
            var mid = (a + b) * 0.5;
            double width = (double)template["w"]![0]!;
            road["pos"] = ToNode(mid);
            road["coords"] = ToNode(new[] { a-mid, (a-mid)*0.33, (b-mid)*0.33, b-mid });
            road["w"] = ToNode(Enumerable.Repeat(width, 4));
            roadList.Add(road);
        }
    }
}
