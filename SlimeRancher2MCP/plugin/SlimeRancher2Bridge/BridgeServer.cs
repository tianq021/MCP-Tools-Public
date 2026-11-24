// BridgeServer —— 本地 HTTP 通道（127.0.0.1 + token），与 1 代同协议。
//
//   GET  /health   → 静态快照（不依赖主线程）
//   POST /rpc      → {"tool":"sr2_xxx","args":{...},"request_id":"..."}
//   Header: X-Bridge-Token
//
// 返回：{"ok":true,"bridge":"0.1.0","request_id":"...","cached":false,"elapsed_ms":3,"data":{...}}
// 线程：
//   - 后台 accept 线程：解析 HTTP / 鉴权 / request_id 幂等 / 把 mainThread 任务入队
//   - 主线程（MelonMod.OnUpdate → Pump）：执行需要 Unity API 的工具
// 不依赖 HttpListener（避免 Windows URL ACL 问题），手写最小 HTTP/1.1。
using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Threading;

namespace SlimeRancher2Bridge
{
    public class BridgeServer
    {
        public const string Version = SR2BridgeMod.ModVersion;

        public static readonly JsonSerializerOptions JsonOptions = new JsonSerializerOptions
        {
            Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
            WriteIndented = false,
        };

        public class Request
        {
            public string Method = "GET";
            public string Path = "/";
            public Dictionary<string, string> Headers = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            public string Body = "";
        }

        class Handler
        {
            public Func<Dictionary<string, object>, Dictionary<string, object>> Fn;
            public bool MainThread;
            public bool Write;
        }

        class Job
        {
            public string Tool;
            public string RequestId;
            public Dictionary<string, object> Args;
            public Dictionary<string, object> Result;
            public bool Failed;
            public string ErrorCode;
            public string ErrorMessage;
            public readonly ManualResetEventSlim Done = new ManualResetEventSlim(false);
        }

        class CacheEntry
        {
            public string Fingerprint;
            public Job Job;
            public DateTime CompletedUtc;
        }

        readonly BridgeConfig cfg;
        readonly Dictionary<string, Handler> handlers = new Dictionary<string, Handler>(StringComparer.Ordinal);
        readonly ConcurrentQueue<Job> jobs = new ConcurrentQueue<Job>();
        readonly Dictionary<string, CacheEntry> cache = new Dictionary<string, CacheEntry>(StringComparer.Ordinal);
        readonly object logLock = new object();
        TcpListener listener;
        Thread acceptThread;
        volatile bool running;
        DateTime startedAt = DateTime.Now;
        long totalRequests;
        long failedRequests;

        public volatile string LastError = "";
        public volatile Dictionary<string, object> Snapshot = new Dictionary<string, object>(StringComparer.Ordinal);
        public bool IsRunning { get { return running; } }
        public int PendingJobs { get { return jobs.Count; } }
        public long TotalRequests { get { return Interlocked.Read(ref totalRequests); } }
        public long FailedRequests { get { return Interlocked.Read(ref failedRequests); } }
        public DateTime StartedAt { get { return startedAt; } }
        public BridgeConfig Config { get { return cfg; } }

        public BridgeServer(BridgeConfig config)
        {
            cfg = config;
        }

        public void Register(string tool, Func<Dictionary<string, object>, Dictionary<string, object>> handler,
            bool mainThread = false, bool write = false)
        {
            handlers[tool] = new Handler { Fn = handler, MainThread = mainThread, Write = write };
        }

        public void Log(string message)
        {
            try
            {
                lock (logLock)
                {
                    string dir = Path.GetDirectoryName(cfg.log_path);
                    if (!Directory.Exists(dir)) Directory.CreateDirectory(dir);
                    File.AppendAllText(cfg.log_path,
                        DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss.fff") + "  " + message + "\r\n",
                        new UTF8Encoding(false));
                }
            }
            catch
            {
            }
        }

        public static Dictionary<string, object> Error(string code, string message)
        {
            var err = new Dictionary<string, object>(StringComparer.Ordinal);
            err["code"] = code;
            err["message"] = message;
            return err;
        }

        public bool Start()
        {
            if (running) return true;
            try
            {
                IPAddress addr = IPAddress.Parse(cfg.host);
                listener = new TcpListener(addr, cfg.port);
                listener.Start();
                running = true;
                startedAt = DateTime.Now;
                acceptThread = new Thread(AcceptLoop);
                acceptThread.IsBackground = true;
                acceptThread.Name = "SR2BridgeAccept";
                acceptThread.Start();
                Log("bridge listening on " + cfg.host + ":" + cfg.port + " (v" + Version + ")");
                return true;
            }
            catch (Exception e)
            {
                running = false;
                LastError = e.GetType().Name + ": " + e.Message;
                Log("bridge start failed: " + LastError);
                return false;
            }
        }

        public void Stop()
        {
            running = false;
            try
            {
                if (listener != null) listener.Stop();
            }
            catch
            {
            }
            listener = null;
            Log("bridge stopped");
        }

        void AcceptLoop()
        {
            while (running)
            {
                TcpClient client = null;
                try
                {
                    client = listener.AcceptTcpClient();
                    client.ReceiveTimeout = 15000;
                    client.SendTimeout = 15000;
                    HandleClient(client);
                }
                catch (SocketException)
                {
                    // listener.Stop() 的正常路径
                }
                catch (Exception e)
                {
                    LastError = e.GetType().Name + ": " + e.Message;
                    Log("accept loop: " + LastError);
                }
                finally
                {
                    if (client != null)
                    {
                        try { client.Close(); } catch { }
                    }
                }
            }
        }

        void HandleClient(TcpClient client)
        {
            Interlocked.Increment(ref totalRequests);
            NetworkStream stream = client.GetStream();
            Request req = ReadRequest(stream);
            if (req == null) return;

            int status = 200;
            Dictionary<string, object> resp;
            try
            {
                if (req.Method == "GET" && (req.Path == "/" || req.Path == "/health"))
                {
                    resp = BuildHealthResponse();
                }
                else if (req.Method == "POST" && req.Path == "/rpc")
                {
                    resp = HandleRpc(req, out status);
                }
                else
                {
                    status = 404;
                    resp = Envelope(null, false, false, 0, null, Error("NOT_FOUND", "unknown path: " + req.Path));
                }
            }
            catch (Exception e)
            {
                status = 500;
                LastError = e.GetType().Name + ": " + e.Message;
                resp = Envelope(null, false, false, 0, null, Error("BRIDGE_EXCEPTION", LastError));
            }
            WriteResponse(stream, status, JsonSerializer.Serialize(resp, JsonOptions));
        }

        Dictionary<string, object> HandleRpc(Request req, out int status)
        {
            status = 200;
            Dictionary<string, object> body;
            try
            {
                using (JsonDocument doc = JsonDocument.Parse(string.IsNullOrWhiteSpace(req.Body) ? "{}" : req.Body))
                {
                    body = AsDict(doc.RootElement) ?? new Dictionary<string, object>(StringComparer.Ordinal);
                }
            }
            catch (Exception e)
            {
                status = 400;
                return Envelope(null, false, false, 0, null, Error("BAD_JSON", e.Message));
            }

            string token = HeaderOr(req, "X-Bridge-Token");
            if (string.IsNullOrEmpty(token) && body.ContainsKey("token"))
            {
                token = AsString(body["token"]);
            }
            if (string.IsNullOrEmpty(cfg.token) || token != cfg.token)
            {
                status = 401;
                Interlocked.Increment(ref failedRequests);
                return Envelope(null, false, false, 0, null, Error("UNAUTHORIZED", "bad or missing token"));
            }

            string tool = AsString(body.ContainsKey("tool") ? body["tool"] : null);
            if (string.IsNullOrEmpty(tool))
            {
                status = 400;
                return Envelope(null, false, false, 0, null, Error("BAD_REQUEST", "missing tool"));
            }

            Dictionary<string, object> args = new Dictionary<string, object>(StringComparer.Ordinal);
            if (body.ContainsKey("args") && body["args"] != null)
            {
                args = body["args"] as Dictionary<string, object>;
                if (args == null)
                {
                    status = 400;
                    return Envelope(null, false, false, 0, null, Error("BAD_REQUEST", "args must be an object"));
                }
            }

            string requestId = null;
            if (body.ContainsKey("request_id") && body["request_id"] != null)
            {
                requestId = AsString(body["request_id"]);
                if (string.IsNullOrWhiteSpace(requestId) || requestId.Length > 128)
                {
                    status = 400;
                    return Envelope(null, false, false, 0, null,
                        Error("BAD_REQUEST", "request_id must contain 1 to 128 characters"));
                }
            }

            Handler handler;
            if (!handlers.TryGetValue(tool, out handler))
            {
                Interlocked.Increment(ref failedRequests);
                return Envelope(requestId, false, false, 0, null,
                    Error("UNKNOWN_TOOL", "unknown tool: " + tool + " (known: " + KnownTools() + ")"));
            }

            bool dryRun;
            if (handler.Write && args.ContainsKey("dry_run") && TryAsBool(args["dry_run"], out dryRun) && !dryRun
                && requestId == null)
            {
                status = 400;
                return Envelope(null, false, false, 0, null,
                    Error("REQUEST_ID_REQUIRED", "executing a write requires a stable request_id"));
            }
            if (requestId == null) requestId = Guid.NewGuid().ToString("N");

            SweepCache();
            string fingerprint = tool + "\n" + StableSerialize(args);
            CacheEntry entry;
            bool existing;
            lock (cache) existing = cache.TryGetValue(requestId, out entry);
            if (existing && entry.Fingerprint != fingerprint)
            {
                status = 409;
                return Envelope(requestId, false, false, 0, null,
                    Error("REQUEST_ID_CONFLICT", "this request_id belongs to a different tool or arguments"));
            }
            if (!existing)
            {
                if (jobs.Count >= 128)
                {
                    return Envelope(requestId, false, false, 0, null, Error("BRIDGE_BUSY", "bridge queue is full"));
                }
                entry = new CacheEntry
                {
                    Fingerprint = fingerprint,
                    Job = new Job { Tool = tool, RequestId = requestId, Args = args },
                };
                lock (cache) cache[requestId] = entry;
                if (handler.MainThread) jobs.Enqueue(entry.Job);
                else
                {
                    RunJob(entry.Job, handler);
                }
            }

            Job job = entry.Job;
            DateTime t0 = DateTime.UtcNow;
            bool finished = job.Done.Wait(cfg.request_timeout_ms);
            long elapsed = (long)(DateTime.UtcNow - t0).TotalMilliseconds;
            if (!finished)
            {
                Interlocked.Increment(ref failedRequests);
                return Envelope(requestId, false, true, elapsed, null,
                    Error("TIMEOUT", "game main thread did not respond within " + cfg.request_timeout_ms + " ms"));
            }
            if (job.Failed)
            {
                Interlocked.Increment(ref failedRequests);
                return Envelope(requestId, false, existing, elapsed, null, Error(job.ErrorCode, job.ErrorMessage));
            }
            return Envelope(requestId, true, existing, elapsed, job.Result, null);
        }

        public void Pump(int maxJobs)
        {
            Handler handler;
            for (int i = 0; i < maxJobs; i++)
            {
                Job job;
                if (!jobs.TryDequeue(out job)) return;
                if (!handlers.TryGetValue(job.Tool, out handler)) continue;
                RunJob(job, handler);
            }
        }

        void RunJob(Job job, Handler handler)
        {
            try
            {
                job.Result = handler.Fn(job.Args);
            }
            catch (Exception e)
            {
                job.Failed = true;
                job.ErrorCode = "TOOL_EXCEPTION";
                job.ErrorMessage = e.GetType().Name + ": " + e.Message;
                LastError = job.ErrorMessage;
                Log("tool " + job.Tool + " failed: " + job.ErrorMessage);
            }
            finally
            {
                lock (cache)
                {
                    CacheEntry ce;
                    if (job.RequestId != null && cache.TryGetValue(job.RequestId, out ce))
                    {
                        ce.CompletedUtc = DateTime.UtcNow;
                    }
                }
                job.Done.Set();
            }
        }

        void SweepCache()
        {
            DateTime cutoff = DateTime.UtcNow.AddSeconds(-Math.Max(1, cfg.idempotency_ttl_s));
            List<string> dead = null;
            lock (cache)
            {
                foreach (var kv in cache)
                {
                    if (kv.Value.Job.Done.IsSet && kv.Value.CompletedUtc != default(DateTime) && kv.Value.CompletedUtc < cutoff)
                    {
                        if (dead == null) dead = new List<string>();
                        dead.Add(kv.Key);
                    }
                }
                if (dead != null)
                {
                    for (int i = 0; i < dead.Count; i++) cache.Remove(dead[i]);
                }
            }
        }

        string KnownTools()
        {
            var names = new List<string>(handlers.Keys);
            names.Sort(StringComparer.Ordinal);
            return string.Join(",", names.ToArray());
        }

        Dictionary<string, object> BuildHealthResponse()
        {
            var data = new Dictionary<string, object>(StringComparer.Ordinal);
            foreach (var kv in Snapshot) data[kv.Key] = kv.Value;
            data["bridge_version"] = Version;
            data["mod_version"] = SR2BridgeMod.ModVersion;
            data["ok"] = true;
            return data;
        }

        Dictionary<string, object> Envelope(string requestId, bool ok, bool cached, long elapsedMs,
            Dictionary<string, object> data, Dictionary<string, object> error)
        {
            var env = new Dictionary<string, object>(StringComparer.Ordinal);
            env["ok"] = ok;
            env["bridge"] = Version;
            env["request_id"] = requestId;
            env["cached"] = cached;
            env["elapsed_ms"] = elapsedMs;
            if (data != null) env["data"] = data;
            if (error != null) env["error"] = error;
            return env;
        }

        // ---------------- HTTP 解析（最小实现） ----------------

        static Request ReadRequest(NetworkStream stream)
        {
            var head = new MemoryStream();
            var buf = new byte[4096];
            int headerEnd = -1;
            while (headerEnd < 0 && head.Length <= 64 * 1024)
            {
                int n;
                try { n = stream.Read(buf, 0, buf.Length); }
                catch { return null; }
                if (n <= 0) return null;
                head.Write(buf, 0, n);
                headerEnd = FindHeaderEnd(head.GetBuffer(), (int)head.Length);
            }
            if (headerEnd < 0) return null;

            byte[] all = head.GetBuffer();
            string headerText = Encoding.ASCII.GetString(all, 0, headerEnd);
            string[] lines = headerText.Split(new[] { "\r\n" }, StringSplitOptions.None);
            if (lines.Length == 0) return null;
            string[] first = lines[0].Split(' ');
            if (first.Length < 2) return null;

            var req = new Request { Method = first[0].ToUpperInvariant(), Path = first[1] };
            int q = req.Path.IndexOf('?');
            if (q >= 0) req.Path = req.Path.Substring(0, q);
            for (int i = 1; i < lines.Length; i++)
            {
                int colon = lines[i].IndexOf(':');
                if (colon <= 0) continue;
                req.Headers[lines[i].Substring(0, colon).Trim()] = lines[i].Substring(colon + 1).Trim();
            }

            int bodyStart = headerEnd + 4;
            int contentLength = 0;
            string cl;
            if (req.Headers.TryGetValue("Content-Length", out cl))
            {
                int.TryParse(cl, out contentLength);
            }
            if (contentLength > 4 * 1024 * 1024) return null;
            var body = new byte[contentLength];
            int have = (int)head.Length - bodyStart;
            if (have > 0) Array.Copy(all, bodyStart, body, 0, Math.Min(have, contentLength));
            int read = Math.Max(0, have);
            while (read < contentLength)
            {
                int n;
                try { n = stream.Read(body, read, contentLength - read); }
                catch { return null; }
                if (n <= 0) break;
                read += n;
            }
            req.Body = Encoding.UTF8.GetString(body, 0, Math.Min(read, contentLength));
            return req;
        }

        static int FindHeaderEnd(byte[] data, int length)
        {
            for (int i = 0; i + 3 < length; i++)
            {
                if (data[i] == 13 && data[i + 1] == 10 && data[i + 2] == 13 && data[i + 3] == 10) return i;
            }
            return -1;
        }

        static void WriteResponse(NetworkStream stream, int status, string json)
        {
            byte[] body = Encoding.UTF8.GetBytes(json);
            string reason = status == 200 ? "OK" : status == 400 ? "Bad Request" : status == 401 ? "Unauthorized"
                : status == 404 ? "Not Found" : status == 409 ? "Conflict" : "Internal Server Error";
            string head = "HTTP/1.1 " + status + " " + reason + "\r\n"
                + "Content-Type: application/json; charset=utf-8\r\n"
                + "Content-Length: " + body.Length + "\r\n"
                + "Connection: close\r\n\r\n";
            byte[] headBytes = Encoding.ASCII.GetBytes(head);
            try
            {
                stream.Write(headBytes, 0, headBytes.Length);
                stream.Write(body, 0, body.Length);
                stream.Flush();
            }
            catch
            {
            }
        }

        static string HeaderOr(Request req, string name)
        {
            string v;
            return req.Headers.TryGetValue(name, out v) ? v : null;
        }

        // ---------------- JSON 值转换 ----------------

        public static Dictionary<string, object> AsDict(object value)
        {
            return value as Dictionary<string, object>;
        }

        public static string AsString(object value)
        {
            if (value == null) return null;
            var s = value as string;
            if (s != null) return s;
            return value.ToString();
        }

        public static int AsInt(object value, int fallback = 0)
        {
            if (value == null) return fallback;
            if (value is long) return (int)(long)value;
            if (value is int) return (int)value;
            if (value is double) return (int)(double)value;
            int n;
            if (int.TryParse(Convert.ToString(value), out n)) return n;
            return fallback;
        }

        public static bool TryAsBool(object value, out bool result)
        {
            result = false;
            if (value is bool) { result = (bool)value; return true; }
            if (value == null) return false;
            return bool.TryParse(Convert.ToString(value), out result);
        }

        static Dictionary<string, object> AsDict(JsonElement el)
        {
            if (el.ValueKind != JsonValueKind.Object) return null;
            var d = new Dictionary<string, object>(StringComparer.Ordinal);
            foreach (JsonProperty p in el.EnumerateObject()) d[p.Name] = ToClr(p.Value);
            return d;
        }

        static object ToClr(JsonElement el)
        {
            switch (el.ValueKind)
            {
                case JsonValueKind.Object:
                    return AsDict(el);
                case JsonValueKind.Array:
                    var list = new List<object>();
                    foreach (JsonElement item in el.EnumerateArray()) list.Add(ToClr(item));
                    return list;
                case JsonValueKind.String:
                    return el.GetString();
                case JsonValueKind.Number:
                    long l;
                    if (el.TryGetInt64(out l)) return l;
                    return el.GetDouble();
                case JsonValueKind.True:
                    return true;
                case JsonValueKind.False:
                    return false;
                default:
                    return null;
            }
        }

        static string StableSerialize(Dictionary<string, object> args)
        {
            var sorted = new SortedDictionary<string, object>(args, StringComparer.Ordinal);
            return JsonSerializer.Serialize(sorted, JsonOptions);
        }
    }
}
