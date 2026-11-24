using BepInEx;
using BepInEx.Logging;
using BepInEx.Unity.IL2CPP;
using System.Collections.Concurrent;
using System.Net;
using System.Text;
using System.Text.Json;
using UnityEngine;

namespace AfterIncBridge;

[BepInPlugin("local.afterinc.sharedbridge", "After Inc Shared Bridge", "0.6.8")]
public sealed class Plugin : BasePlugin
{
    internal static ManualLogSource? LogSource;
    internal static bool ProbeAutopause;
    private HttpListener? listener;
    public override void Load()
    {
        LogSource = Log;
        // Autopause/Counter interop access is probed only when explicitly enabled: touching it
        // has been suspected of destabilising the game's UI/rendering path.
        ProbeAutopause = Config.Bind("Bridge", "ProbeAutopause", false, "Read AutopauseController state in status/debug_pause.").Value;
        // Forcing background rendering is opt-in: it has been followed by FunkyCode lighting
        // crashes in this game build, so the default keeps the game's own focus behaviour.
        if (Config.Bind("Bridge", "RunInBackground", false, "Force Unity to keep rendering while unfocused.").Value)
            Application.runInBackground = true;
        var port = Config.Bind("Bridge", "Port", 17840, "Loopback HTTP port").Value;
        NativePump.Install();
        listener = new HttpListener();
        listener.Prefixes.Add($"http://127.0.0.1:{port}/");
        listener.Start();
        _ = Task.Run(async () => {
            while (listener.IsListening) {
                try { var context = await listener.GetContextAsync(); _ = Task.Run(() => Serve(context)); }
                catch (HttpListenerException) { break; }
                catch (ObjectDisposedException) { break; }
            }
        });
        Log.LogInfo($"After Inc shared bridge 0.6.8 listening on 127.0.0.1:{port}");
    }

    private static async Task Serve(HttpListenerContext context)
    {
        object result;
        try {
            var request = context.Request;
            if (request.HttpMethod != "POST" || request.Url?.AbsolutePath != "/v1/call" ||
                request.Headers["X-IncMcp"] != "1" || request.Headers["Origin"] != null)
                throw new BridgeError("BAD_REQUEST", "Use the local MCP client.");
            if (request.ContentLength64 < 0 || request.ContentLength64 > 65536)
                throw new BridgeError("BAD_REQUEST", "Body must be at most 64 KiB.");
            using var reader = new StreamReader(request.InputStream, Encoding.UTF8);
            var body = await reader.ReadToEndAsync();
            using var doc = JsonDocument.Parse(body);
            var root = doc.RootElement;
            var command = root.GetProperty("command").GetString() ?? "";
            var client = root.GetProperty("client_id").GetString() ?? "";
            var id = root.GetProperty("request_id").GetString() ?? "";
            if (client.Length is < 8 or > 100 || id.Length is < 8 or > 100)
                throw new BridgeError("BAD_REQUEST", "Invalid client or request ID.");
            var args = root.GetProperty("args").Clone();
            var work = Broker.Submit(command, client, id, args);
            var completed = await Task.WhenAny(work.Done.Task, Task.Delay(25000));
            if (completed == work.Done.Task) result = await work.Done.Task;
            else if (Interlocked.CompareExchange(ref work.Status, 2, 0) == 0) {
                result = Api.Fail("TIMEOUT", "Game did not process the request; it was cancelled before execution.");
                work.Done.TrySetResult(result);
            } else result = Api.Fail("OUTCOME_PENDING", "Request is still running. Retry with the same request_id, never a new ID.", null);
        }
        catch (BridgeError e) { result = Api.Fail(e.Code, e.Message); }
        catch (Exception e) { result = Api.Fail("BRIDGE_ERROR", e.Message); }
        try {
            var bytes = JsonSerializer.SerializeToUtf8Bytes(result, Api.JsonOptions);
            context.Response.ContentType = "application/json; charset=utf-8";
            context.Response.ContentLength64 = bytes.Length;
            await context.Response.OutputStream.WriteAsync(bytes);
        } catch (Exception) { /* Client disconnected; command result remains in ledger. */ }
        finally { context.Response.Close(); }
    }

    public override bool Unload() { listener?.Close(); Broker.Stop(); NativePump.Stop(); return true; }
}

internal sealed class BridgeError : Exception
{
    public string Code { get; }
    public BridgeError(string code, string message) : base(message) { Code = code; }
}

internal static class Api
{
    public static readonly JsonSerializerOptions JsonOptions = new() {
        NumberHandling = System.Text.Json.Serialization.JsonNumberHandling.AllowNamedFloatingPointLiterals
    };
    public static object Ok(object data) => new { ok = true, data };
    public static object Fail(string code, string message, bool? applied = false) => new { ok = false, error = new { code, message }, applied };
}

internal sealed class Work
{
    public string Command = "", Client = "", Id = "", Fingerprint = "";
    public JsonElement Args;
    public int Status;
    public DateTime Created = DateTime.UtcNow;
    public TaskCompletionSource<object> Done = new(TaskCreationOptions.RunContinuationsAsynchronously);
}

internal static class Broker
{
    public static readonly ConcurrentQueue<Work> Queue = new();
    private static readonly Dictionary<string, Work> Ledger = new();
    private static readonly object Gate = new();
    public static readonly HashSet<string> Mutations = new() { "acquire_control", "release_control", "set_speed", "execute_action", "advance", "unpause", "step_turn", "open_campaign_map", "select_campaign_region", "enter_campaign_level", "choose_campaign_option", "activate_game_control", "activate_map_marker", "settle_zone", "deploy_soldier", "move_soldier", "build_outpost", "purchase_settlement", "scout_zone", "claim_zone", "open_settlement", "close_settlement", "open_morale", "buy_native" };
    public static Work Submit(string command, string client, string id, JsonElement args)
    {
        lock (Gate) {
            var key = client + ":" + id;
            var fingerprint = command + "\n" + args.GetRawText();
            if (Mutations.Contains(command) && Ledger.TryGetValue(key, out var old)) {
                if (old.Fingerprint != fingerprint) throw new BridgeError("REQUEST_ID_REUSED", "request_id was already used with different arguments.");
                return old;
            }
            foreach (var expired in Ledger.Where(p => p.Value.Done.Task.IsCompleted && DateTime.UtcNow - p.Value.Created > TimeSpan.FromMinutes(10)).Select(p => p.Key).ToArray()) Ledger.Remove(expired);
            if (Queue.Count >= 64 || Ledger.Count >= 2048) throw new BridgeError("BUSY", "Bridge queue or request ledger is full.");
            var work = new Work { Command = command, Client = client, Id = id, Args = args, Fingerprint = fingerprint };
            if (Mutations.Contains(command)) Ledger[key] = work;
            Queue.Enqueue(work);
            return work;
        }
    }
    public static void Stop() { while (Queue.TryDequeue(out var work)) work.Done.TrySetResult(Api.Fail("DISCONNECTED", "Game bridge stopped.")); }
}





