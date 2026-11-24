// ChatBus —— 游戏内聊天消息总线（移植自一代 SlimeRancherBridge/ChatOverlay.cs 的 ChatBus）
//
//   outgoing：玩家在游戏里发出的消息（等 DSH 通过 sr2_chat_pending 取走）
//   incoming：DSH 通过 sr2_chat_send 推回来的回复（游戏内面板显示）
//
// 玩家消息同时落盘式追加到 out\chat\inbox.jsonl：即使 DSH 没在监听、游戏/对话重启，
// 消息也不会丢；文件超过 1 MB 归档为 inbox.<时间戳>.jsonl 后继续写新文件（不删旧档）。
//
// 线程：队列本身加锁（后台线程安全）；文件写入在调用线程完成。
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;

namespace SlimeRancher2Bridge
{
    public class ChatMessage
    {
        public int seq;
        public string at;
        public string text;
    }

    public static class ChatBus
    {
        public const long MaxInboxBytes = 1024 * 1024;

        static readonly object gate = new object();
        static readonly List<ChatMessage> outgoing = new List<ChatMessage>();
        static readonly List<ChatMessage> incoming = new List<ChatMessage>();
        static int nextSeq = 1;
        static DateTime lastPollUtc = DateTime.MinValue;
        static string inboxPath;
        static long submittedTotal;
        static long incomingTotal;

        /// <summary>inbox 路径：&lt;项目&gt;\out\chat\inbox.jsonl（由 bridge.json 的 log_path 推导）。</summary>
        public static string InboxPath
        {
            get
            {
                if (inboxPath != null) return inboxPath;
                string outDir = null;
                try
                {
                    var cfg = SR2BridgeMod.Config;
                    string logDir = cfg != null && !string.IsNullOrEmpty(cfg.log_path)
                        ? Path.GetDirectoryName(cfg.log_path) : null;
                    if (!string.IsNullOrEmpty(logDir)) outDir = Path.GetDirectoryName(logDir);
                }
                catch
                {
                }
                if (string.IsNullOrEmpty(outDir)) outDir = Path.GetDirectoryName(BridgeConfig.DefaultOutDir);
                inboxPath = Path.GetFullPath(Path.Combine(outDir, "chat", "inbox.jsonl"));
                return inboxPath;
            }
        }

        public static ChatMessage AddOutgoing(string text)
        {
            var msg = new ChatMessage();
            lock (gate)
            {
                msg.seq = nextSeq++;
                msg.at = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss");
                msg.text = text;
                outgoing.Add(msg);
                while (outgoing.Count > 50) outgoing.RemoveAt(0);
                submittedTotal++;
            }
            WriteInbox(msg);
            return msg;
        }

        public static void AddIncoming(string text)
        {
            lock (gate)
            {
                var msg = new ChatMessage
                {
                    seq = nextSeq++,
                    at = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss"),
                    text = text
                };
                incoming.Add(msg);
                while (incoming.Count > 50) incoming.RemoveAt(0);
                incomingTotal++;
            }
        }

        public static List<ChatMessage> DrainOutgoing()
        {
            lock (gate)
            {
                var copy = new List<ChatMessage>(outgoing);
                outgoing.Clear();
                return copy;
            }
        }

        public static List<ChatMessage> LastIncoming(int count)
        {
            lock (gate)
            {
                var result = new List<ChatMessage>();
                int from = incoming.Count - count;
                if (from < 0) from = 0;
                for (int i = from; i < incoming.Count; i++) result.Add(incoming[i]);
                return result;
            }
        }

        public static int PendingCount { get { lock (gate) { return outgoing.Count; } } }
        public static int IncomingCount { get { lock (gate) { return incoming.Count; } } }
        public static long SubmittedTotal { get { lock (gate) { return submittedTotal; } } }
        public static long IncomingTotal { get { lock (gate) { return incomingTotal; } } }

        public static void MarkPoll()
        {
            lock (gate) { lastPollUtc = DateTime.UtcNow; }
        }

        /// <summary>DSH 上次取消息距今秒数；从未取过返回 double.MaxValue。</summary>
        public static double SecondsSincePoll()
        {
            lock (gate)
            {
                if (lastPollUtc == DateTime.MinValue) return double.MaxValue;
                return (DateTime.UtcNow - lastPollUtc).TotalSeconds;
            }
        }

        // ---------------- 落盘 ----------------

        static void WriteInbox(ChatMessage msg)
        {
            try
            {
                string dir = Path.GetDirectoryName(InboxPath);
                if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
                RotateInboxIfNeeded();
                var sb = new StringBuilder(160);
                sb.Append("{\"seq\":").Append(msg.seq)
                  .Append(",\"at\":\"").Append(msg.at)
                  .Append("\",\"text\":\"").Append(Escape(msg.text)).Append("\"}");
                File.AppendAllText(InboxPath, sb.ToString() + "\r\n", new UTF8Encoding(false));
            }
            catch (Exception e)
            {
                LastWriteError = e.GetType().Name + ": " + e.Message;
            }
        }

        public static string LastWriteError;

        static void RotateInboxIfNeeded()
        {
            try
            {
                var info = new FileInfo(InboxPath);
                if (!info.Exists || info.Length <= MaxInboxBytes) return;
                string dir = Path.GetDirectoryName(InboxPath);
                string stamp = DateTime.Now.ToString("yyyyMMdd_HHmmss");
                string archive = Path.Combine(dir, "inbox." + stamp + ".jsonl");
                int suffix = 1;
                while (File.Exists(archive))
                {
                    archive = Path.Combine(dir, "inbox." + stamp + "_" + suffix + ".jsonl");
                    suffix++;
                }
                File.Move(InboxPath, archive);
            }
            catch
            {
            }
        }

        static string Escape(string text)
        {
            if (text == null) return "";
            var sb = new StringBuilder(text.Length + 16);
            foreach (char c in text)
            {
                if (c == '\\') sb.Append("\\\\");
                else if (c == '"') sb.Append("\\\"");
                else if (c == '\r') sb.Append("\\r");
                else if (c == '\n') sb.Append("\\n");
                else if (c == '\t') sb.Append("\\t");
                else if (c < ' ') sb.Append(' ');
                else sb.Append(c);
            }
            return sb.ToString();
        }
    }
}
