using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;

internal sealed class MenuBridge : IDisposable
{
    private const string MainRoute = "/v1/main-menu";
    private const string SinglePlayerRoute = "/v1/single-player-menu";
    private const string DiseaseRoute = "/v1/disease-menu";
    private const string SetupRoute = "/v1/game-setup";
    private const string CountryRoute = "/v1/start-country";
    private const string EvolutionRoute = "/v1/evolution";
    private const string StatusRoute = "/v1/game-status";
    private readonly string token = Guid.NewGuid().ToString("N");
    private readonly TcpListener listener = new TcpListener(IPAddress.Loopback, 0);
    private readonly ConcurrentQueue<MenuRequest> requests = new ConcurrentQueue<MenuRequest>();
    private readonly string endpointFile;
    private volatile bool disposed;

    public MenuBridge()
    {
        using var process = Process.GetCurrentProcess();
        var pid = process.Id;
        var directory = Path.Combine(Path.GetTempPath(), "plague-inc-evolved-mcp");
        Directory.CreateDirectory(directory);
        endpointFile = Path.Combine(directory, $"{pid}.json");

        listener.Start();
        try
        {
            var port = ((IPEndPoint)listener.LocalEndpoint).Port;
            var temporaryFile = endpointFile + ".tmp";
            File.WriteAllText(temporaryFile, JsonConvert.SerializeObject(new { protocol = 1, pid, port, token }));
            File.Delete(endpointFile);
            File.Move(temporaryFile, endpointFile);
            Task.Run(Serve);
        }
        catch
        {
            listener.Stop();
            throw;
        }
    }

    public bool TryTake(out MenuRequest request) => requests.TryDequeue(out request);

    private void Serve()
    {
        while (!disposed)
        {
            try
            {
                using var client = listener.AcceptTcpClient();
                client.ReceiveTimeout = 2000;
                client.SendTimeout = 2000;
                using var stream = client.GetStream();
                var body = Handle(stream);
                var bytes = Encoding.UTF8.GetBytes(body);
                var header = Encoding.ASCII.GetBytes("HTTP/1.1 200 OK\r\nContent-Type: application/json; charset=utf-8\r\n" +
                    $"Content-Length: {bytes.Length}\r\nConnection: close\r\n\r\n");
                stream.Write(header, 0, header.Length);
                stream.Write(bytes, 0, bytes.Length);
            }
            catch (IOException) { }
            catch (SocketException) { }
            catch (ObjectDisposedException) when (disposed) { }
        }
    }

    private string Handle(Stream stream)
    {
        using var reader = new StreamReader(stream, Encoding.ASCII, false, 1024, true);
        var line = reader.ReadLine()?.Split(' ');
        if (line == null || line.Length != 3 || !line[2].StartsWith("HTTP/1.", StringComparison.Ordinal))
            return Error("BAD_REQUEST");

        var headers = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        for (var count = 0; ; count++)
        {
            var header = reader.ReadLine();
            if (header == null || header.Length > 2048 || count > 32) return Error("BAD_REQUEST");
            if (header.Length == 0) break;
            var separator = header.IndexOf(':');
            if (separator < 1) return Error("BAD_REQUEST");
            headers[header.Substring(0, separator)] = header.Substring(separator + 1).Trim();
        }

        if (headers.ContainsKey("Origin") || !headers.TryGetValue("X-Plague-Token", out var supplied) || supplied != token)
            return Error("UNAUTHORIZED");

        var route = MatchesRoute(line[1], StatusRoute) ? StatusRoute
            : MatchesRoute(line[1], EvolutionRoute) ? EvolutionRoute
            : MatchesRoute(line[1], CountryRoute) ? CountryRoute
            : MatchesRoute(line[1], SetupRoute) ? SetupRoute
            : MatchesRoute(line[1], DiseaseRoute) ? DiseaseRoute
            : MatchesRoute(line[1], SinglePlayerRoute) ? SinglePlayerRoute : MainRoute;
        var view = route switch
        {
            StatusRoute => MenuView.Status,
            EvolutionRoute => MenuView.Evolution,
            CountryRoute => MenuView.Country,
            SetupRoute => MenuView.Setup,
            DiseaseRoute => MenuView.Disease,
            SinglePlayerRoute => MenuView.SinglePlayer,
            _ => MenuView.Main
        };
        MenuRequest request;
        if (line[0] == "GET" && line[1] == route)
        {
            request = new MenuRequest(view, null, null, null);
        }
        else if (line[0] == "POST" && line[1].StartsWith(route + "/", StringComparison.Ordinal))
        {
            var choice = line[1].Substring(route.Length + 1);
            var validChoice = view switch
            {
                MenuView.SinglePlayer => choice is "classic" or "outbreak" or "cure" or "speed_run" or "official_scenarios" or "custom_scenarios" or "start",
                MenuView.Disease or MenuView.Setup => System.Text.RegularExpressions.Regex.IsMatch(choice, @"\A[a-z][a-z0-9_]{0,63}\z"),
                MenuView.Country => choice == "dialog_close" ||
                    System.Text.RegularExpressions.Regex.IsMatch(choice, @"\A[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\z"),
                MenuView.Evolution => System.Text.RegularExpressions.Regex.IsMatch(choice, @"\A[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\z"),
                MenuView.Status => choice is "paused" or "normal" or "faster" or "fastest" or
                    "dialog_a" or "dialog_b" or "dialog_close" or "popup_close" or
                    "collect" or "autocollect" or "autocollect_off" or "tech_tree",
                _ => choice is "single_player" or "load_game" or "back"
            };
            if (!validChoice) return Error("BAD_REQUEST");
            if (!headers.TryGetValue("X-Plague-State", out var stateId) || stateId.Length > 64 ||
                !headers.TryGetValue("X-Plague-Request", out var requestId) || !Guid.TryParseExact(requestId, "D", out _))
                return Error("BAD_REQUEST");
            var hasName = headers.TryGetValue("X-Plague-Name", out var encodedName);
            if (hasName != (view == MenuView.Setup && choice == "set_name")) return Error("BAD_REQUEST");
            string name = null;
            if (hasName)
            {
                try { name = new UTF8Encoding(false, true).GetString(Convert.FromBase64String(encodedName)); }
                catch (FormatException) { return Error("BAD_REQUEST"); }
                catch (DecoderFallbackException) { return Error("BAD_REQUEST"); }
                if (name.Length == 0 || name.Length > 64) return Error("BAD_REQUEST");
            }
            request = new MenuRequest(view, choice, stateId, requestId, name);
        }
        else return Error("BAD_REQUEST");

        if (requests.Count >= 8) return Error("BRIDGE_BUSY", false);
        requests.Enqueue(request);
        var timeout = (request.View == MenuView.Setup || request.View == MenuView.Country) && request.Choice != null ? 9500 : 3500;
        if (request.Response.Wait(timeout)) return request.Response.Result;
        return Error("GAME_THREAD_TIMEOUT", request.CancelPending() ? false : (bool?)null);
    }

    private static bool MatchesRoute(string path, string route) => path == route || path.StartsWith(route + "/", StringComparison.Ordinal);

    internal static string Error(string code, bool? applied = false) =>
        JsonConvert.SerializeObject(new { ok = false, error = code, applied });

    public void Dispose()
    {
        disposed = true;
        listener.Stop();
        File.Delete(endpointFile);
    }
}

internal enum MenuView { Main, SinglePlayer, Disease, Setup, Country, Evolution, Status }

internal sealed class MenuRequest
{
    private readonly TaskCompletionSource<string> completion = new TaskCompletionSource<string>();
    private int state;
    public MenuView View { get; }
    public string Choice { get; }
    public string StateId { get; }
    public string Id { get; }
    public string Text { get; }
    public Task<string> Response => completion.Task;

    public MenuRequest(MenuView view, string choice, string stateId, string id, string text = null)
    {
        View = view;
        Choice = choice;
        StateId = stateId;
        Id = id;
        Text = text;
    }

    public bool TryBegin() => Interlocked.CompareExchange(ref state, 1, 0) == 0;
    public bool CancelPending() => Interlocked.CompareExchange(ref state, 2, 0) == 0;
    public void Complete(string response) => completion.SetResult(response);
}
