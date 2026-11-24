// ChatTools —— 聊天框的桥接工具（供 MCP / bridge_cli 调用）
//
//   sr_chat_pending  取走玩家在游戏内发出的消息（轮询；每次调用会清空待取队列）
//   sr_chat_send     把管家回复推进游戏内聊天框（显示 + 提示音/弹条）
//   sr_chat_status   查询聊天框状态（是否打开、排队数、最近轮询时间）
//
// 说明：这三个工具只操作内存队列和 UI，不写存档，因此注册为只读工具（不要求 dry_run）。
using System;
using System.Collections.Generic;

namespace SlimeRancherBridge
{
    static class ChatTools
    {
        public static void Register(BridgeServer server)
        {
            server.Register("sr_chat_pending", Pending);
            server.Register("sr_chat_send", Send);
            server.Register("sr_chat_status", Status);
        }

        static Dictionary<string, object> Row(params object[] fields)
        {
            var result = new Dictionary<string, object>(StringComparer.Ordinal);
            for (int i = 0; i < fields.Length; i += 2)
            {
                result.Add((string)fields[i], fields[i + 1]);
            }
            return result;
        }

        static Dictionary<string, object> Fail(string code, string message)
        {
            return Row("__error", BridgeServer.Error(code, message));
        }

        static string Arg(Dictionary<string, object> args, string key)
        {
            object value;
            return args.TryGetValue(key, out value) ? (value as string ?? "").Trim() : "";
        }

        static Dictionary<string, object> Pending(Dictionary<string, object> args)
        {
            ChatOverlay.Ensure();
            ChatBus.MarkPoll();
            List<ChatMessage> list = ChatBus.DrainOutgoing();
            var rows = new List<object>();
            for (int i = 0; i < list.Count; i++)
            {
                rows.Add(Row("seq", list[i].seq, "at", list[i].at, "text", list[i].text));
            }
            return Row(
                "count", list.Count,
                "messages", rows,
                "overlay_open", ChatOverlay.IsOpen,
                "wall", DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss"));
        }

        static Dictionary<string, object> Send(Dictionary<string, object> args)
        {
            string text = Arg(args, "text");
            if (string.IsNullOrEmpty(text))
            {
                return Fail("BAD_ARGUMENT", "text is required (1-400 chars).");
            }
            if (text.Length > 400)
            {
                text = text.Substring(0, 400);
            }
            ChatOverlay.Ensure();
            ChatBus.AddIncoming(text);
            ChatBus.MarkPoll();  // 回复本身也说明管家在线（喂给聊天框的「在线」指示灯）
            return Row(
                "delivered", ChatOverlay.Instance != null,
                "length", text.Length,
                "incoming_total", ChatBus.IncomingCount,
                "wall", DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss"));
        }

        static Dictionary<string, object> Status(Dictionary<string, object> args)
        {
            ChatOverlay.Ensure();
            ChatBus.MarkPoll();  // 状态查询兼作在线心跳（DSH 侧监听会周期性调用）
            double age = ChatBus.SecondsSincePoll();
            long inboxBytes = -1;
            long archiveBytes = -1;
            try
            {
                var fi = new System.IO.FileInfo(ChatBus.InboxPath);
                if (fi.Exists) inboxBytes = fi.Length;
            }
            catch (Exception)
            {
            }
            try
            {
                var fa = new System.IO.FileInfo(ChatBus.InboxPath + ".1");
                if (fa.Exists) archiveBytes = fa.Length;
            }
            catch (Exception)
            {
            }
            return Row(
                "overlay_alive", ChatOverlay.Instance != null,
                "overlay_open", ChatOverlay.IsOpen,
                "pending_outgoing", ChatBus.PendingCount,
                "incoming_total", ChatBus.IncomingCount,
                "last_poll_age_s", age == double.MaxValue ? -1 : Math.Round(age, 1),
                "inbox_bytes", inboxBytes,
                "inbox_archive_bytes", archiveBytes,
                "inbox_max_bytes", ChatBus.MaxInboxBytes,
                "update_ticks", ChatOverlay.UpdateTicks,
                "open_count", ChatOverlay.OpenCount,
                "submit_count", ChatOverlay.SubmitCount,
                "close_count", ChatOverlay.CloseCount,
                "input_key_events", ChatOverlay.InputKeyEvents,
                "gui_key_events", ChatOverlay.GuiKeyEvents,
                "last_input_key", ChatOverlay.LastInputKey,
                "last_gui_key", ChatOverlay.LastGuiKey,
                "last_action", ChatOverlay.LastAction,
                "last_submit_draft", ChatOverlay.LastSubmitDraft,
                "last_error", ChatOverlay.LastError,
                "wall", DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss"));
        }
    }
}
