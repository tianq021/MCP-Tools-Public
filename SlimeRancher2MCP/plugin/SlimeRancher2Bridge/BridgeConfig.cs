// BridgeConfig —— 与 MCP server 共用的配置文件。
// 位置固定在本项目目录下（游戏 mod 与 python 两侧都读这一份）：
//     E:\SlimeRancher2MCP\config\bridge.json
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Text.Json;

namespace SlimeRancher2Bridge
{
    public class BridgeConfig
    {
        public const string ConfigPath = @"E:\SlimeRancher2MCP\config\bridge.json";
        public const string DefaultOutDir = @"E:\SlimeRancher2MCP\out\bridge";

        public string host = "127.0.0.1";
        public int port = 18762;
        public string token = "";
        public int request_timeout_ms = 9000;
        public int idempotency_ttl_s = 600;
        public string log_path = DefaultOutDir + @"\server.log";

        public static BridgeConfig LoadOrCreate()
        {
            var cfg = new BridgeConfig();
            try
            {
                if (File.Exists(ConfigPath))
                {
                    string raw = File.ReadAllText(ConfigPath, Encoding.UTF8);
                    if (raw.Length > 0 && raw[0] == '\uFEFF') raw = raw.Substring(1);
                    using (JsonDocument doc = JsonDocument.Parse(raw))
                    {
                        JsonElement r = doc.RootElement;
                        cfg.host = Str(r, "host", cfg.host);
                        cfg.port = Int(r, "port", cfg.port);
                        cfg.token = Str(r, "token", cfg.token);
                        cfg.request_timeout_ms = Int(r, "request_timeout_ms", cfg.request_timeout_ms);
                        cfg.idempotency_ttl_s = Int(r, "idempotency_ttl_s", cfg.idempotency_ttl_s);
                        cfg.log_path = Str(r, "log_path", cfg.log_path);
                    }
                    if (string.IsNullOrEmpty(cfg.token))
                    {
                        cfg.token = NewToken();
                        cfg.Save();
                    }
                    return cfg;
                }
            }
            catch
            {
                // 读坏了就退回默认配置，不让插件起不来
            }
            cfg.token = NewToken();
            cfg.Save();
            return cfg;
        }

        static string Str(JsonElement root, string name, string fallback)
        {
            JsonElement v;
            if (root.TryGetProperty(name, out v) && v.ValueKind == JsonValueKind.String)
            {
                return v.GetString();
            }
            return fallback;
        }

        static int Int(JsonElement root, string name, int fallback)
        {
            JsonElement v;
            if (root.TryGetProperty(name, out v) && v.ValueKind == JsonValueKind.Number)
            {
                int n;
                if (v.TryGetInt32(out n)) return n;
            }
            return fallback;
        }

        public static string NewToken()
        {
            var bytes = new byte[16];
            new Random(Guid.NewGuid().GetHashCode()).NextBytes(bytes);
            var sb = new StringBuilder(32);
            for (int i = 0; i < bytes.Length; i++) sb.Append(bytes[i].ToString("x2"));
            return sb.ToString();
        }

        public void Save()
        {
            try
            {
                string dir = Path.GetDirectoryName(ConfigPath);
                if (!Directory.Exists(dir)) Directory.CreateDirectory(dir);
                var dict = new Dictionary<string, object>
                {
                    { "host", host },
                    { "port", port },
                    { "token", token },
                    { "request_timeout_ms", request_timeout_ms },
                    { "idempotency_ttl_s", idempotency_ttl_s },
                    { "log_path", log_path },
                };
                string json = JsonSerializer.Serialize(dict, BridgeServer.JsonOptions);
                File.WriteAllText(ConfigPath, json, new UTF8Encoding(false));
            }
            catch
            {
            }
        }
    }
}
