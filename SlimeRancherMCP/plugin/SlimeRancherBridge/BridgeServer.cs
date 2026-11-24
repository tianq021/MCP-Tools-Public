// BridgeServer —— 本地 HTTP 通道（127.0.0.1 + token）
// 线程模型：
//   - 接受线程（后台）：解析 HTTP 请求、鉴权、request_id 去重、把任务塞进队列等主线程结果
//   - 主线程（Update 里的 Pump）：按帧取任务执行，因此所有 Unity API 调用都在主线程
// 协议：
//   GET  /health                  → 静态快照（不依赖主线程，游戏卡住时也能回）
//   POST /rpc                     body: {"tool":"sr_xxx","args":{...},"request_id":"..."}
//   Header: X-Bridge-Token
// 返回：
//   {"ok":true,"bridge":"0.4.2","request_id":"...","cached":false,"elapsed_ms":12,"data":{...}}
//   {"ok":false,...,"error":{"code":"...","message":"..."}}
using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;

namespace SlimeRancherBridge
{
    public class BridgeConfig
    {
        public string host = "127.0.0.1";
        public int port = 18760;
        public string token = "";
        public int request_timeout_ms = 9000;
        public int idempotency_ttl_s = 600;
        public string log_path = @"E:\SlimeRancherMCP\out\bridge\server.log";

        public const string ConfigPath = @"E:\SlimeRancherMCP\config\bridge.json";

        public static BridgeConfig LoadOrCreate()
        {
            try
            {
                if (File.Exists(ConfigPath))
                {
                    var raw = File.ReadAllText(ConfigPath, Encoding.UTF8);
                    if (raw.Length > 0 && raw[0] == '\uFEFF')
                    {
                        raw = raw.Substring(1);
                    }
                    var obj = Json.AsObject(Json.Parse(raw));
                    var cfg = new BridgeConfig();
                    object v;
                    if (obj.TryGetValue("host", out v) && v != null) cfg.host = Json.AsString(v);
                    if (obj.TryGetValue("port", out v) && v != null) cfg.port = Json.AsInt(v, cfg.port);
                    if (obj.TryGetValue("token", out v) && v != null) cfg.token = Json.AsString(v);
                    if (obj.TryGetValue("request_timeout_ms", out v) && v != null) cfg.request_timeout_ms = Json.AsInt(v, cfg.request_timeout_ms);
                    if (obj.TryGetValue("idempotency_ttl_s", out v) && v != null) cfg.idempotency_ttl_s = Json.AsInt(v, cfg.idempotency_ttl_s);
                    if (obj.TryGetValue("log_path", out v) && v != null) cfg.log_path = Json.AsString(v);
                    if (string.IsNullOrEmpty(cfg.token))
                    {
                        cfg.token = NewToken();
                        cfg.Save();
                    }
                    return cfg;
                }
                var def = new BridgeConfig();
                def.token = NewToken();
                def.Save();
                return def;
            }
            catch
            {
                var fallback = new BridgeConfig();
                fallback.token = NewToken();
                return fallback;
            }
        }

        public static string NewToken()
        {
            // 32 位十六进制随机串
            var bytes = new byte[16];
            var rng = new System.Random(Guid.NewGuid().GetHashCode());
            rng.NextBytes(bytes);
            var sb = new StringBuilder(32);
            for (int i = 0; i < bytes.Length; i++)
            {
                sb.Append(bytes[i].ToString("x2"));
            }
            return sb.ToString();
        }

        public void Save()
        {
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(ConfigPath));
                var dict = new Dictionary<string, object>();
                dict["host"] = host;
                dict["port"] = port;
                dict["token"] = token;
                dict["request_timeout_ms"] = request_timeout_ms;
                dict["idempotency_ttl_s"] = idempotency_ttl_s;
                dict["log_path"] = log_path;
                File.WriteAllText(ConfigPath, Json.Serialize(dict), new UTF8Encoding(false));
            }
            catch
            {
            }
        }
    }

    public class BridgeServer
    {
        public const string Version = "0.9.0";

        public class Request
        {
            public string Method = "GET";
            public string Path = "/";
            public Dictionary<string, string> Headers = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            public string Body = "";
        }

        public class Job
        {
            public string Tool;
            public Dictionary<string, object> Args;
            public Dictionary<string, object> Result;
            public Dictionary<string, object> Error;
            public DateTime CompletedAt;
            public readonly ManualResetEventSlim Done = new ManualResetEventSlim(false);
            int state;

            public bool TryStart() { return Interlocked.CompareExchange(ref state, 1, 0) == 0; }
            public bool TryCancel() { return Interlocked.CompareExchange(ref state, 2, 0) == 0; }
        }

        class CacheEntry
        {
            public string Fingerprint;
            public Job Job;
        }

        readonly BridgeConfig cfg;
        readonly object logLock = new object();
        readonly ConcurrentQueue<Job> jobs = new ConcurrentQueue<Job>();
        readonly Dictionary<string, CacheEntry> cache = new Dictionary<string, CacheEntry>(StringComparer.Ordinal);
        readonly Dictionary<string, Func<Dictionary<string, object>, Dictionary<string, object>>> handlers =
            new Dictionary<string, Func<Dictionary<string, object>, Dictionary<string, object>>>(StringComparer.Ordinal);
        readonly HashSet<string> writes = new HashSet<string>(StringComparer.Ordinal);

        TcpListener listener;
        Thread acceptThread;
        volatile bool running;
        DateTime startedAt = DateTime.Now;
        long totalRequests;
        long failedRequests;
        string lastError = "";
        DateTime nextCacheSweep;

        public volatile Dictionary<string, object> Snapshot = new Dictionary<string, object>();
        public int PendingJobs { get { return jobs.Count; } }
        public bool IsRunning { get { return running; } }
        public BridgeConfig Config { get { return cfg; } }

        public BridgeServer(BridgeConfig config)
        {
            cfg = config;
        }

        public void Register(string tool, Func<Dictionary<string, object>, Dictionary<string, object>> handler, bool write = false)
        {
            handlers[tool] = handler;
            if (write) writes.Add(tool);
        }

        public void Log(string message)
        {
            try
            {
                lock (logLock)
                {
                    Directory.CreateDirectory(Path.GetDirectoryName(cfg.log_path));
                    File.AppendAllText(cfg.log_path,
                        DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss.fff") + "  " + message + "\r\n",
                        new UTF8Encoding(false));
                }
            }
            catch
            {
            }
        }

        public void SetLastError(string message)
        {
            lastError = message;
        }

        public bool Start()
        {
            if (running)
            {
                return true;
            }
            try
            {
                IPAddress addr = IPAddress.Parse(cfg.host);
                listener = new TcpListener(addr, cfg.port);
                listener.Start();
                running = true;
                startedAt = DateTime.Now;
                acceptThread = new Thread(AcceptLoop);
                acceptThread.IsBackground = true;
                acceptThread.Name = "SlimeRancherBridgeAccept";
                acceptThread.Start();
                Log("bridge listening on " + cfg.host + ":" + cfg.port + " (v" + Version + ")");
                return true;
            }
            catch (Exception e)
            {
                running = false;
                lastError = e.GetType().Name + ": " + e.Message;
                Log("bridge start failed: " + lastError);
                return false;
            }
        }

        public void Stop()
        {
            running = false;
            try
            {
                if (listener != null)
                {
                    listener.Stop();
                }
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
                    client.ReceiveTimeout = 10000;
                    client.SendTimeout = 10000;
                    HandleClient(client);
                }
                catch (SocketException)
                {
                    // listener 停止时会走到这里
                }
                catch (Exception e)
                {
                    lastError = e.GetType().Name + ": " + e.Message;
                    Log("accept loop: " + lastError);
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
            NetworkStream stream = client.GetStream();
            Request req = ReadRequest(stream);
            if (req == null)
            {
                return;
            }
            Dictionary<string, object> resp;
            int status = 200;
            try
            {
                if (req.Method == "GET" && (req.Path == "/health" || req.Path == "/"))
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
                lastError = e.GetType().Name + ": " + e.Message;
                resp = Envelope(null, false, false, 0, null, Error("BRIDGE_EXCEPTION", lastError));
            }
            WriteResponse(stream, status, Json.Serialize(resp));
        }

        Dictionary<string, object> HandleRpc(Request req, out int status)
        {
            status = 200;
            Dictionary<string, object> body;
            try
            {
                body = Json.AsObject(Json.Parse(req.Body));
            }
            catch (Exception e)
            {
                status = 400;
                return Envelope(null, false, false, 0, null, Error("BAD_JSON", e.Message));
            }

            string token = HeaderOr(req, "X-Bridge-Token");
            if (string.IsNullOrEmpty(token))
            {
                object v;
                if (body.TryGetValue("token", out v))
                {
                    token = Json.AsString(v);
                }
            }
            if (string.IsNullOrEmpty(cfg.token) || token != cfg.token)
            {
                status = 401;
                failedRequests++;
                return Envelope(null, false, false, 0, null, Error("UNAUTHORIZED", "bad or missing token"));
            }

            string tool;
            object tv;
            if (!body.TryGetValue("tool", out tv) || Json.AsString(tv) == null)
            {
                status = 400;
                return Envelope(null, false, false, 0, null, Error("BAD_REQUEST", "missing tool"));
            }
            tool = Json.AsString(tv);

            Dictionary<string, object> args = new Dictionary<string, object>(StringComparer.Ordinal);
            object av;
            if (body.TryGetValue("args", out av) && av != null)
            {
                args = av as Dictionary<string, object>;
                if (args == null)
                {
                    status = 400;
                    return Envelope(null, false, false, 0, null, Error("BAD_REQUEST", "args must be an object"));
                }
            }

            string requestId = null;
            object rv;
            if (body.TryGetValue("request_id", out rv))
            {
                requestId = rv as string;
                if (String.IsNullOrWhiteSpace(requestId) || requestId.Length > 128)
                {
                    status = 400;
                    return Envelope(null, false, false, 0, null, Error("BAD_REQUEST", "request_id must contain 1 to 128 characters"));
                }
            }
            object dryRun;
            if (writes.Contains(tool) && args.TryGetValue("dry_run", out dryRun) && dryRun is bool && !(bool)dryRun && requestId == null)
            {
                status = 400;
                return Envelope(null, false, false, 0, null, Error("REQUEST_ID_REQUIRED", "Executing a write requires a stable request_id"));
            }
            if (requestId == null) requestId = Guid.NewGuid().ToString("N");

            SweepCache();
            string fingerprint = tool + "\n" + Json.Serialize(new SortedDictionary<string, object>(args, StringComparer.Ordinal));
            CacheEntry entry;
            bool existing = cache.TryGetValue(requestId, out entry);
            if (existing && entry.Job.Done.IsSet && Expired(entry.Job, DateTime.UtcNow))
            {
                cache.Remove(requestId);
                existing = false;
            }
            if (existing && entry.Fingerprint != fingerprint)
            {
                status = 409;
                return Envelope(requestId, false, false, 0, null, Error("REQUEST_ID_CONFLICT", "This request_id belongs to a different tool or arguments"));
            }
            if (!existing)
            {
                if (!handlers.ContainsKey(tool))
                {
                    failedRequests++;
                    return Envelope(requestId, false, false, 0, null,
                        Error("UNKNOWN_TOOL", "unknown tool: " + tool + " (known: " + KnownTools() + ")"));
                }
                if (cache.Count >= 4096 || jobs.Count >= 128)
                    return Envelope(requestId, false, false, 0, null, Error("BRIDGE_BUSY", "The bridge queue or retry cache is full."));
                entry = new CacheEntry { Fingerprint = fingerprint, Job = new Job { Tool = tool, Args = args } };
                cache.Add(requestId, entry);
                jobs.Enqueue(entry.Job);
            }
            Job job = entry.Job;
            DateTime t0 = DateTime.UtcNow;
            bool finished = job.Done.Wait(cfg.request_timeout_ms);
            long elapsed = (long)(DateTime.UtcNow - t0).TotalMilliseconds;
            totalRequests++;
            if (!finished && !job.Done.IsSet)
            {
                failedRequests++;
                lastError = "tool timeout: " + tool;
                bool cancelled = job.TryCancel();
                if (cancelled) cache.Remove(requestId);
                return Envelope(requestId, false, false, elapsed, null,
                    cancelled
                        ? Error("TIMEOUT", "Main thread did not start the request; it was cancelled.")
                        : Error("IN_PROGRESS", "Request already started. Retry with the same request_id and arguments to retrieve its result."));
            }

            if (job.Error != null) failedRequests++;
            return Envelope(requestId, job.Error == null, existing, elapsed, job.Result, job.Error);
        }

        bool Expired(Job job, DateTime now)
        {
            return (now - job.CompletedAt).TotalSeconds > cfg.idempotency_ttl_s;
        }

        void SweepCache()
        {
            DateTime now = DateTime.UtcNow;
            if (now < nextCacheSweep) return;
            nextCacheSweep = now.AddSeconds(15);
            var expired = new List<string>();
            foreach (var entry in cache)
                if (entry.Value.Job.Done.IsSet && Expired(entry.Value.Job, now))
                    expired.Add(entry.Key);
            foreach (string id in expired) cache.Remove(id);
        }

        string KnownTools()
        {
            var sb = new StringBuilder();
            foreach (var kv in handlers)
            {
                if (sb.Length > 0)
                {
                    sb.Append(",");
                }
                sb.Append(kv.Key);
            }
            return sb.ToString();
        }

        public void Pump(int maxJobs)
        {
            Job job;
            int n = 0;
            while (n < maxJobs && jobs.TryDequeue(out job))
            {
                n++;
                if (!job.TryStart()) continue;
                try
                {
                    Func<Dictionary<string, object>, Dictionary<string, object>> handler;
                    if (handlers.TryGetValue(job.Tool, out handler))
                    {
                        job.Result = handler(job.Args);
                    }
                    else
                    {
                        job.Error = Error("UNKNOWN_TOOL", "unknown tool: " + job.Tool);
                    }
                    object error;
                    if (job.Result != null && job.Result.TryGetValue("__error", out error))
                    {
                        job.Error = (Dictionary<string, object>)error;
                        job.Result.Remove("__error");
                    }
                }
                catch (Exception e)
                {
                    lastError = job.Tool + ": " + e.GetType().Name + ": " + e.Message;
                    job.Result = null;
                    job.Error = Error("TOOL_EXCEPTION", e.GetType().Name + ": " + e.Message);
                }
                finally
                {
                    job.CompletedAt = DateTime.UtcNow;
                    job.Done.Set();
                }
            }
        }

        public Dictionary<string, object> BuildHealthResponse()
        {
            var data = new Dictionary<string, object>();
            foreach (var kv in Snapshot)
            {
                data[kv.Key] = kv.Value;
            }
            data["bridge_version"] = Version;
            data["tools"] = new List<string>(handlers.Keys);
            data["uptime_s"] = (int)(DateTime.Now - startedAt).TotalSeconds;
            try
            {
                object su;
                if (Snapshot.TryGetValue("snapshot_unix", out su) && su != null)
                {
                    long unix = Convert.ToInt64(su, CultureInfo.InvariantCulture);
                    long now = (long)(DateTime.UtcNow - new DateTime(1970, 1, 1)).TotalSeconds;
                    data["snapshot_age_s"] = Math.Max(0, now - unix);
                }
            }
            catch
            {
            }
            data["requests_total"] = totalRequests;
            data["requests_failed"] = failedRequests;
            data["pending_jobs"] = jobs.Count;
            data["cache_entries"] = cache.Count;
            data["last_error"] = lastError;
            data["port"] = cfg.port;
            return Envelope(null, true, false, 0, data, null);
        }

        public static Dictionary<string, object> Error(string code, string message)
        {
            var e = new Dictionary<string, object>(StringComparer.Ordinal);
            e["code"] = code;
            e["message"] = message;
            return e;
        }

        public static Dictionary<string, object> Envelope(string requestId, bool ok, bool cached,
            long elapsedMs, Dictionary<string, object> data, Dictionary<string, object> error)
        {
            var dict = new Dictionary<string, object>(StringComparer.Ordinal);
            dict["ok"] = ok;
            dict["bridge"] = Version;
            dict["request_id"] = requestId;
            dict["cached"] = cached;
            dict["elapsed_ms"] = elapsedMs;
            if (data != null)
            {
                dict["data"] = data;
            }
            if (error != null)
            {
                dict["error"] = error;
            }
            return dict;
        }

        static string HeaderOr(Request req, string name)
        {
            string v;
            if (req.Headers.TryGetValue(name, out v))
            {
                return v;
            }
            return null;
        }

        static Request ReadRequest(NetworkStream stream)
        {
            var buffer = new List<byte>(4096);
            byte[] chunk = new byte[4096];
            int headerEnd = -1;
            while (headerEnd < 0)
            {
                int n = stream.Read(chunk, 0, chunk.Length);
                if (n <= 0)
                {
                    return null;
                }
                for (int i = 0; i < n; i++)
                {
                    buffer.Add(chunk[i]);
                }
                headerEnd = IndexOfHeaderEnd(buffer);
                if (buffer.Count > 131072)
                {
                    return null;
                }
            }

            byte[] all = buffer.ToArray();
            string headerText = Encoding.ASCII.GetString(all, 0, headerEnd);
            var lines = headerText.Split(new string[] { "\r\n" }, StringSplitOptions.None);
            if (lines.Length == 0)
            {
                return null;
            }
            string[] first = lines[0].Split(' ');
            if (first.Length < 2)
            {
                return null;
            }
            var req = new Request();
            req.Method = first[0].ToUpperInvariant();
            req.Path = first[1];
            int q = req.Path.IndexOf('?');
            if (q >= 0)
            {
                req.Path = req.Path.Substring(0, q);
            }
            for (int i = 1; i < lines.Length; i++)
            {
                int colon = lines[i].IndexOf(':');
                if (colon <= 0)
                {
                    continue;
                }
                req.Headers[lines[i].Substring(0, colon).Trim()] = lines[i].Substring(colon + 1).Trim();
            }

            int contentLength = 0;
            string cl;
            if (req.Headers.TryGetValue("Content-Length", out cl))
            {
                int.TryParse(cl, NumberStyles.Integer, CultureInfo.InvariantCulture, out contentLength);
            }
            if (contentLength < 0 || contentLength > 262144)
            {
                return null;
            }

            int bodyStart = headerEnd + 4;
            while (all.Length - bodyStart < contentLength)
            {
                int n = stream.Read(chunk, 0, chunk.Length);
                if (n <= 0)
                {
                    break;
                }
                var grown = new byte[all.Length + n];
                Buffer.BlockCopy(all, 0, grown, 0, all.Length);
                Buffer.BlockCopy(chunk, 0, grown, all.Length, n);
                all = grown;
            }
            int available = all.Length - bodyStart;
            int take = Math.Min(available, contentLength);
            if (take > 0)
            {
                req.Body = Encoding.UTF8.GetString(all, bodyStart, take);
            }
            return req;
        }

        static int IndexOfHeaderEnd(List<byte> buffer)
        {
            for (int i = 0; i + 3 < buffer.Count; i++)
            {
                if (buffer[i] == 13 && buffer[i + 1] == 10 && buffer[i + 2] == 13 && buffer[i + 3] == 10)
                {
                    return i;
                }
            }
            return -1;
        }

        static void WriteResponse(NetworkStream stream, int status, string json)
        {
            byte[] body = Encoding.UTF8.GetBytes(json);
            string reason = status == 200 ? "OK" : (status == 400 ? "Bad Request" : (status == 401 ? "Unauthorized" : (status == 404 ? "Not Found" : "Internal Server Error")));
            var head = new StringBuilder();
            head.Append("HTTP/1.1 ").Append(status).Append(' ').Append(reason).Append("\r\n");
            head.Append("Content-Type: application/json; charset=utf-8\r\n");
            head.Append("Content-Length: ").Append(body.Length).Append("\r\n");
            head.Append("Connection: close\r\n");
            head.Append("\r\n");
            byte[] headBytes = Encoding.ASCII.GetBytes(head.ToString());
            stream.Write(headBytes, 0, headBytes.Length);
            stream.Write(body, 0, body.Length);
            stream.Flush();
        }
    }
}
