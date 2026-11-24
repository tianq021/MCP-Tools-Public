// BridgeServer 协议冒烟测试（不需要游戏 / MelonLoader）。
using System;
using System.Collections.Generic;
using System.Net;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;
using SlimeRancher2Bridge;
using Il2CppMonomiPark.SlimeRancher.DataModel;

internal static partial class Program
{
    static int passed;
    static int failed;

    static void Check(string name, bool ok, string detail = "")
    {
        if (ok) { passed++; Console.WriteLine("  PASS  " + name); }
        else { failed++; Console.WriteLine("  FAIL  " + name + (detail.Length > 0 ? "  <- " + detail : "")); }
    }

    static async Task<int> Main()
    {
        var portProbe = new System.Net.Sockets.TcpListener(IPAddress.Loopback, 0);
        portProbe.Start();
        int port = ((IPEndPoint)portProbe.LocalEndpoint).Port;
        portProbe.Stop();
        var cfg = new BridgeConfig { port = port, token = Guid.NewGuid().ToString("N"),
            log_path = System.IO.Path.Combine(System.IO.Path.GetTempPath(), "sr2-smoketest.log") };
        var server = new BridgeServer(cfg);
        // 注册 P0 的真实工具（sr2_health / sr2_gameinfo / sr2_saves / sr2_echo）
        ToolsCore.Register(server);
        ToolsSession.Register(server);
        server.Register("sr2_vacpack", ToolsInventory.Read, mainThread: true);
        ToolsWorld.Register(server);
        ToolsRanch.Register(server);
        ToolsExploration.Register(server);
        ToolsFabrication.Register(server);
        ToolsActions.Register(server);
        ToolsProgress.Register(server);
        ToolsNavigation.Register(server);
        server.Register("sr2_market", ToolsMarket.Read, mainThread: true);
        // 额外的写工具探针（验证 request_id 保护）
        server.Register("sr2_write_probe", delegate (Dictionary<string, object> a)
        {
            var d = new Dictionary<string, object>();
            d["dry_run"] = a.ContainsKey("dry_run") ? a["dry_run"] : null;
            return d;
        }, false, true);

        if (!server.Start())
        {
            Console.WriteLine("server start failed: " + server.LastError);
            return 1;
        }
        string baseUrl = "http://" + cfg.host + ":" + cfg.port;
        using (var http = new HttpClient())
        {
            http.Timeout = TimeSpan.FromSeconds(6);
            string token = cfg.token;
            Console.WriteLine("smoketest against " + baseUrl);

            // 1) GET /health（与 1 代一致：健康检查不需要 token，只暴露本机信息）
            var r = await http.GetAsync(baseUrl + "/health");
            string body = await r.Content.ReadAsStringAsync();
            Check("GET /health 200", r.StatusCode == HttpStatusCode.OK, ((int)r.StatusCode).ToString());
            Check("GET /health has bridge_version", body.Contains("bridge_version"), body.Substring(0, Math.Min(120, body.Length)));

            // 2) 无 token → 401
            var req = new HttpRequestMessage(HttpMethod.Post, baseUrl + "/rpc");
            req.Content = new StringContent("{\"tool\":\"sr2_echo\",\"args\":{}}", Encoding.UTF8, "application/json");
            var r2 = await http.SendAsync(req);
            string b2 = await r2.Content.ReadAsStringAsync();
            Check("POST /rpc no token -> 401", r2.StatusCode == HttpStatusCode.Unauthorized, ((int)r2.StatusCode).ToString());
            Check("401 body code UNAUTHORIZED", b2.Contains("UNAUTHORIZED"), b2);

            // 3) 正常 echo（含中文）
            string rid = Guid.NewGuid().ToString("N");
            string echoBody = "{\"tool\":\"sr2_echo\",\"args\":{\"text\":\"中文测试\"},\"request_id\":\"" + rid + "\"}";
            var r3 = await PostRpc(http, baseUrl, token, echoBody);
            Check("echo ok=true", r3.Contains("\"ok\":true"), r3);
            Check("echo 中文往返", r3.Contains("中文测试"), r3);
            Check("echo request_id 回传", r3.Contains(rid), r3);

            // 4) 同一 request_id 重复调用 → cached=true
            var r4 = await PostRpc(http, baseUrl, token, echoBody);
            Check("repeat request_id cached=true", r4.Contains("\"cached\":true"), r4);

            // 5) 同一 request_id 不同参数 → 409
            string conflictBody = "{\"tool\":\"sr2_echo\",\"args\":{\"text\":\"other\"},\"request_id\":\"" + rid + "\"}";
            var response = await SendRpc(http, baseUrl, token, conflictBody);
            string b5 = await response.Content.ReadAsStringAsync();
            Check("request_id conflict -> 409", response.StatusCode == HttpStatusCode.Conflict, ((int)response.StatusCode).ToString());
            Check("conflict body code REQUEST_ID_CONFLICT", b5.Contains("REQUEST_ID_CONFLICT"), b5);

            // 6) 未知工具
            var r6 = await PostRpc(http, baseUrl, token, "{\"tool\":\"sr2_nope\",\"args\":{}}");
            Check("unknown tool -> UNKNOWN_TOOL", r6.Contains("UNKNOWN_TOOL"), r6);

            // 7) 写工具：dry_run=false 且无 request_id → REQUEST_ID_REQUIRED
            var r7 = await PostRpc(http, baseUrl, token, "{\"tool\":\"sr2_write_probe\",\"args\":{\"dry_run\":false}}");
            Check("write without request_id -> REQUEST_ID_REQUIRED", r7.Contains("REQUEST_ID_REQUIRED"), r7);

            // 8) 写工具：dry_run=false + request_id → 正常
            var r8 = await PostRpc(http, baseUrl, token,
                "{\"tool\":\"sr2_write_probe\",\"args\":{\"dry_run\":false},\"request_id\":\"" + Guid.NewGuid().ToString("N") + "\"}");
            Check("write with request_id ok", r8.Contains("\"ok\":true"), r8);

            // 9) health 计数递增
            Check("requests counted", server.TotalRequests >= 8, "total=" + server.TotalRequests);

            // 10) 真实工具：sr2_gameinfo 读真实游戏目录（run.ps1 里用 SR2_GAME_DIR 覆盖）
            var infoResp = await PostRpc(http, baseUrl, token, "{\"tool\":\"sr2_gameinfo\",\"args\":{}}");
            Check("sr2_gameinfo ok", infoResp.Contains("\"ok\":true"), infoResp.Substring(0, Math.Min(160, infoResp.Length)));
            Check("sr2_gameinfo buildid=24131940", infoResp.Contains("24131940"), infoResp);
            Check("sr2_gameinfo IL2CPP", infoResp.Contains("IL2CPP"), infoResp);
            Check("sr2_gameinfo unity 6000.0.73", infoResp.Contains("6000.0.73"), infoResp);
            Check("sr2_gameinfo melonloader_installed", infoResp.Contains("melonloader_installed"), infoResp);

            // 11) 真实工具：sr2_saves（本机预期无存档目录）
            var savesResp = await PostRpc(http, baseUrl, token, "{\"tool\":\"sr2_saves\",\"args\":{\"limit\":5}}");
            Check("sr2_saves ok", savesResp.Contains("\"ok\":true"), savesResp.Substring(0, Math.Min(160, savesResp.Length)));
            Check("sr2_saves has save_dir", savesResp.Contains("save_dir"), savesResp);

            foreach (string tool in new[] { "sr2_player_position", "sr2_minimap" })
            {
                var navigationResponse = PostRpc(http, baseUrl, token, JsonSerializer.Serialize(new { tool, args = new { } }));
                while (!navigationResponse.IsCompleted)
                {
                    server.Pump(8);
                    await Task.Delay(5);
                }
                using var json = JsonDocument.Parse(await navigationResponse);
                var data = json.RootElement.GetProperty("data");
                if (tool == "sr2_player_position")
                    Check("position without a session", !data.GetProperty("available").GetBoolean()
                        && data.GetProperty("reason").GetString() == "session_unavailable");
                else
                    Check("minimap without loaded mod", !data.GetProperty("loaded").GetBoolean()
                        && data.GetProperty("version").ValueKind == JsonValueKind.Null);
            }
            var clock = await PostRpc(http, baseUrl, token, "{\"tool\":\"sr2_real_time\"}");
            using var clockJson = JsonDocument.Parse(clock);
            var time = clockJson.RootElement.GetProperty("data");
            var instant = DateTimeOffset.Parse(time.GetProperty("time").GetString());
            Check("wall time offset and epoch", instant.Offset == TimeSpan.FromHours(8)
                && instant.ToUnixTimeSeconds() == time.GetProperty("unix_seconds").GetInt64()
                && Math.Abs((DateTimeOffset.UtcNow - instant).TotalSeconds) < 10);

            async Task<JsonElement> Query(string tool, object arguments = null)
            {
                var pending = PostRpc(http, baseUrl, token, JsonSerializer.Serialize(new { tool, args = arguments ?? new { } }));
                while (!pending.IsCompleted) { server.Pump(8); await Task.Delay(5); }
                using var document = JsonDocument.Parse(await pending);
                return document.RootElement.GetProperty("data").Clone();
            }
            Check("state before initialization", (await Query("sr2_state")).GetProperty("reason").GetString() == "session_unavailable");
            var loader = Il2Cpp.SystemContext.Instance = new() { MainMenu = true };
            var menu = await Query("sr2_state");
            Check("menu has no active save", menu.GetProperty("phase").GetString() == "main_menu"
                && menu.GetProperty("save_name").ValueKind == JsonValueKind.Null);
            Check("time unavailable in menu", (await Query("sr2_time")).GetProperty("reason").GetString() == "outside_gameplay");
            loader.IsSceneLoadInProgress = true;
            Check("loading takes precedence", (await Query("sr2_state")).GetProperty("phase").GetString() == "loading"
                && (await Query("sr2_player")).GetProperty("reason").GetString() == "loading");
            loader.MainMenu = loader.IsSceneLoadInProgress = false;
            var world = Il2Cpp.SceneContext.Instance.GameModel;
            Check("world still initializing", (await Query("sr2_plots")).GetProperty("reason").GetString() == "game_model_unavailable");
            world.isInitialized = true;
            await TestFabrication(world, Query);
            await TestProgress(world, Query);
            var ore = new Il2Cpp.IdentifiableType { ReferenceId = "Ore" };
            world.resourceNodeSpawnerModels["loaded"] = new(new IntPtr(1), "READY", null, new() { ore, ore });
            world.resourceNodeSpawnerModels["far"] = new(new IntPtr(2), "READY", null, null);
            world.researchDrones["log1"] = new(new IntPtr(3), false);
            world.researchDrones["log2"] = new(new IntPtr(4), true);
            UnityEngine.Object.Active = new UnityEngine.Component[] {
                new Il2Cpp.ResourceNodeSpawner { _model = world.resourceNodeSpawnerModels["loaded"] },
                new Il2CppMonomiPark.SlimeRancher.World.ResearchDrone.ResearchDroneController {
                    _model = world.researchDrones["log1"], ResearchDroneEntry = new("entry1", new[] { "page" }) }
            };
            var nodes = await Query("sr2_resource_nodes", new { state = "ready", loaded_only = true });
            var node = nodes.GetProperty("items")[0];
            Check("node joins live component and preserves repeated drops", nodes.GetProperty("matched_count").GetInt32() == 1
                && node.GetProperty("position").GetProperty("x").GetSingle() == 3 && node.GetProperty("resources_to_spawn").GetArrayLength() == 2);
            var far = (await Query("sr2_resource_nodes")).GetProperty("items")[0];
            Check("unloaded node keeps state without coordinates", far.GetProperty("id").GetString() == "far"
                && far.GetProperty("state").GetString() == "READY" && far.GetProperty("position").ValueKind == JsonValueKind.Null);
            var logs = (await Query("sr2_research_logs", new { unread_only = true })).GetProperty("items");
            Check("unread logs and linked page count", logs.GetArrayLength() == 1 && logs[0].GetProperty("entry_id").GetString() == "entry1"
                && logs[0].GetProperty("page_count").GetInt32() == 1);
            world.teleporters["portal"] = new(new(new("A"), new("group1")), new() { ["route"] = new(new("B"), new("group2")) }, true);
            world.teleporters["unpaired"] = new(null, null, false);
            var routes = (await Query("sr2_teleporters", new { source_group = "GROUP1" })).GetProperty("items");
            Check("source group filter returns directed link", routes.GetArrayLength() == 1 && routes[0].GetProperty("source_node").GetString() == "A"
                && routes[0].GetProperty("destinations").GetProperty("route").GetProperty("node").GetString() == "B");
            var pedia = Il2Cpp.SceneContext.Instance.PediaDirector;
            Check("uninitialized pedia is unavailable", !(await Query("sr2_pedia")).GetProperty("available").GetBoolean());
            pedia._pediaModel = new(new() { "known" });
            pedia.catalog.Add(new("known", "KnownAsset", "已发现"));
            pedia.catalog.Add(new("hidden", "RareAsset", "待发现"));
            var entries = await Query("sr2_pedia", new { query = "rAre", locked_only = true });
            Check("filtered pedia retains full catalog progress", entries.GetProperty("unlocked_count").GetInt32() == 1
                && entries.GetProperty("record_count").GetInt32() == 2 && entries.GetProperty("matched_count").GetInt32() == 1
                && entries.GetProperty("items")[0].GetProperty("id").GetString() == "hidden");
            world.gordos["gordo1"] = new();
            world.pods["C"] = new(new(State.OPEN), null);
            world.pods["B"] = new(new(State.LOCKED), new());
            world.pods["A"] = new(new(State.OPEN), null);
            world.doors["door1"] = new(State.CLOSED, new());
            world.landPlots["garden1"] = new(Il2Cpp.LandPlot.Id.GARDEN, new(new("Cuberry")), null, new() { "SPRINKLER" }, new());
            var gordo = (await Query("sr2_gordos")).GetProperty("items")[0];
            Check("unloaded gordo retains progress", gordo.GetProperty("position").ValueKind == JsonValueKind.Null
                && gordo.GetProperty("state").GetString() == "seen" && gordo.GetProperty("feed_count").GetInt32() == 7
                && gordo.GetProperty("feed_target").GetInt32() == 30);
            var pods = await Query("sr2_pods", new { state = "open", limit = 1 });
            var more = await Query("sr2_pods", new { state = "OPEN", limit = 1, offset = pods.GetProperty("next_offset").GetInt32() });
            Check("filtered pods page in stable ID order", pods.GetProperty("record_count").GetInt32() == 3
                && pods.GetProperty("matched_count").GetInt32() == 2 && pods.GetProperty("items")[0].GetProperty("id").GetString() == "A"
                && more.GetProperty("items")[0].GetProperty("id").GetString() == "C" && more.GetProperty("next_offset").ValueKind == JsonValueKind.Null);
            var loadedPods = (await Query("sr2_pods", new { loaded_only = true })).GetProperty("items");
            Check("loaded-only records have coordinates", loadedPods.GetArrayLength() == 1
                && loadedPods[0].GetProperty("position").GetProperty("z").GetSingle() == 5);
            var doors = (await Query("sr2_doors", new { state = "closed" })).GetProperty("items");
            Check("door retains CLOSED state", doors.GetArrayLength() == 1 && doors[0].GetProperty("state").GetString() == "CLOSED");
            var plot = (await Query("sr2_plots", new { type = "garden" })).GetProperty("items")[0];
            Check("plot crop and upgrade", plot.GetProperty("crop").GetString() == "Cuberry"
                && plot.GetProperty("upgrades")[0].GetString() == "SPRINKLER" && plot.GetProperty("tracked_actor_count").ValueKind == JsonValueKind.Null);
            var session = await Query("sr2_state");
            var carrot = new Il2Cpp.IdentifiableType { ReferenceId = "Carrot" };
            var lettuce = new Il2Cpp.IdentifiableType { ReferenceId = "WaterLettuce" };
            var cotton = new Il2Cpp.SlimeDefinition { ReferenceId = "CottonSlime", Diet = new() { FavoriteIdents = new[] { lettuce } } };
            world.slimeDefinitions = new(new[] { cotton, new Il2Cpp.SlimeDefinition { ReferenceId = "CottonLargo", IsLargo = true } });
            world.actors[new(1)] = new() { ident = cotton };
            world.actors[new(2)] = new() { ident = carrot };
            world.landPlots["pen"] = new(Il2Cpp.LandPlot.Id.CORRAL, null, new(new() { new(1), new(2), new(3) }), null, null);
            world.landPlots["unknown"] = new(Il2Cpp.LandPlot.Id.CORRAL, null, null, null, null);
            var pens = (await Query("sr2_corrals")).GetProperty("items");
            Check("corral tracks by type and accounts for unknown actors", pens.GetArrayLength() == 2
                && pens[0].GetProperty("slimes").GetProperty("CottonSlime").GetInt32() == 1 && pens[0].GetProperty("other_count").GetInt32() == 1
                && pens[0].GetProperty("unresolved_count").GetInt32() == 1 && pens[1].GetProperty("tracked_count").ValueKind == JsonValueKind.Null);
            var diets = (await Query("sr2_slime_diet", new { query = "cotton", include_largos = false })).GetProperty("items");
            Check("diet query excludes largos and reads favorites", diets.GetArrayLength() == 1
                && diets[0].GetProperty("favorites")[0].GetString() == "WaterLettuce");
            world.gadgets.Add(new() { actorId = new(10), ident = new() { ReferenceId = "Fan" }, Transform = new() });
            world.gadgets.Add(new DroneStationGadgetModel { actorId = new(20), ident = new() { ReferenceId = "Station" },
                Initialized = true, Type = "RANCH_DRONE", lastPosition = new(1, 2, 3), EnergyDepleteTime = new(double.PositiveInfinity),
                IsDroneAtStation = new(false), TaskData = new("GARDEN", "ITEM", carrot, "SILO") });
            var gadget = (await Query("sr2_gadgets", new { query = "fAn" })).GetProperty("items")[0];
            Check("gadget name filter and live location", gadget.GetProperty("id").GetString() == "10" && gadget.GetProperty("position_source").GetString() == "live");
            var droneRows = (await Query("sr2_drones")).GetProperty("items");
            var station = droneRows[0];
            Check("station task and saved location", droneRows.GetArrayLength() == 1 && station.GetProperty("task").GetProperty("sink").GetString() == "SILO"
                && station.GetProperty("task").GetProperty("item").GetString() == "Carrot" && station.GetProperty("position_source").GetString() == "last_position"
                && station.GetProperty("position").GetProperty("z").GetSingle() == 3 && !station.GetProperty("at_station").GetBoolean()
                && station.GetProperty("energy_deplete_time").ValueKind == JsonValueKind.Null);
            Check("active save and group", session.GetProperty("phase").GetString() == "gameplay"
                && session.GetProperty("save_name").GetString() == "test-save" && session.GetProperty("scene_group").GetString() == "group-test");
            var gameTime = await Query("sr2_time");
            Check("game time and pause flags", gameTime.GetProperty("day").GetInt32() == 4
                && gameTime.GetProperty("hour").GetSingle() == 13.75f && gameTime.GetProperty("paused").GetBoolean()
                && !gameTime.GetProperty("fast_forwarding").GetBoolean());
            var player = await Query("sr2_player");
            Check("player resources and location", player.GetProperty("health").GetInt32() == 37
                && player.GetProperty("max_health").GetInt32() == 100 && player.GetProperty("energy").GetInt32() == 82
                && player.GetProperty("max_energy").GetInt32() == 150 && player.GetProperty("coins").GetInt32() == 123
                && player.GetProperty("position").GetProperty("z").GetSingle() == -3 && player.GetProperty("zone").GetString() == "zone-test");
            Check("market not initialized", (await Query("sr2_market")).GetProperty("reason").GetString() == "market_not_ready");
            Il2Cpp.SceneContext.Instance.PlortEconomyDirector._hasInitializedMarketPrices = true;
            var market = await Query("sr2_market");
            var prices = market.GetProperty("prices");
            Check("native market quote and delta", market.GetProperty("available").GetBoolean()
                && prices[0].GetProperty("price").GetInt32() == 19 && prices[0].GetProperty("change").GetInt32() == -3
                && prices[0].GetProperty("base_price").GetSingle() == 7 && prices[0].GetProperty("full_saturation").GetSingle() == 50);
            Check("closed market and missing quote", market.GetProperty("market_closed").GetBoolean()
                && market.GetProperty("quoted_count").GetInt32() == 1 && prices.GetArrayLength() == 2
                && prices[1].GetProperty("price").ValueKind == JsonValueKind.Null && prices[1].GetProperty("change").ValueKind == JsonValueKind.Null);
            var controller = Il2Cpp.SceneContext.Instance.Controller;
            async Task<JsonElement> Write(string tool, object args, string id)
            {
                string body = JsonSerializer.Serialize(new { tool, args, request_id = id });
                var pending = PostRpc(http, baseUrl, token, body);
                while (!pending.IsCompleted) { server.Pump(8); await Task.Delay(5); }
                using var document = JsonDocument.Parse(await pending);
                return document.RootElement.Clone();
            }
            await TestActions(world, Write);
            Task<JsonElement> Teleport(object args, string id = null) => Write("sr2_teleport", args, id);
            var preview = await Teleport(new { x = 3.5, y = 7, z = -2 });
            Check("teleport defaults to preview", preview.GetProperty("data").GetProperty("dry_run").GetBoolean()
                && controller.MoveCount == 0);
            var move = new { x = 3.5, y = 7, z = -2, dry_run = false };
            var rejected = await Teleport(move);
            Check("teleport requires request ID", rejected.GetProperty("error").GetProperty("code").GetString() == "REQUEST_ID_REQUIRED"
                && controller.MoveCount == 0);
            var moved = await Teleport(move, "test-move");
            var repeated = await Teleport(move, "test-move");
            Check("teleport readback", moved.GetProperty("data").GetProperty("reached").GetBoolean()
                && controller.TransientPosition.x == 3.5f && controller.TransientPosition.z == -2);
            Check("teleport retry moves once", repeated.GetProperty("cached").GetBoolean() && controller.MoveCount == 1);
            await TestRescue(Write);
            await TestRanchControls(world, Write);
            TestStorageMovement();
        }
        server.Stop();
        Console.WriteLine();
        Console.WriteLine("RESULT: passed=" + passed + " failed=" + failed);
        return failed == 0 ? 0 : 2;
    }

    static async Task<string> PostRpc(HttpClient http, string baseUrl, string token, string json)
    {
        var resp = await SendRpc(http, baseUrl, token, json);
        return await resp.Content.ReadAsStringAsync();
    }

    static async Task<HttpResponseMessage> SendRpc(HttpClient http, string baseUrl, string token, string json)
    {
        var req = new HttpRequestMessage(HttpMethod.Post, baseUrl + "/rpc");
        req.Headers.Add("X-Bridge-Token", token);
        req.Content = new StringContent(json, Encoding.UTF8, "application/json");
        return await http.SendAsync(req);
    }
}
