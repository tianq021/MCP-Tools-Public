// ChatTools —— 聊天框的桥接工具（供 MCP / bridge_cli 调用）
//
//   sr2_chat_pending  取走玩家在游戏内发出的消息（轮询；每次调用清空待取队列）
//   sr2_chat_send     把管家回复推进游戏内聊天框（显示 + 提示条）
//   sr2_chat_status   聊天框状态 / 按键诊断
//
// 这三个工具只操作内存队列与 UI，不写存档，因此注册为只读工具（不要求 dry_run / request_id）。
using System;
using System.Collections.Generic;

namespace SlimeRancher2Bridge
{
    internal static class ChatTools
    {
        internal static void Register(BridgeServer server)
        {
            server.Register("sr2_chat_pending", Pending);
            server.Register("sr2_chat_send", Send);
            server.Register("sr2_chat_status", Status);
        }

        static Dictionary<string, object> Row(params object[] fields)
        {
            var result = new Dictionary<string, object>(StringComparer.Ordinal);
            for (int i = 0; i + 1 < fields.Length; i += 2) result[(string)fields[i]] = fields[i + 1];
            return result;
        }

        static string Arg(Dictionary<string, object> args, string key)
        {
            object value;
            return args.TryGetValue(key, out value) && value != null ? (value as string ?? "").Trim() : "";
        }

        static Dictionary<string, object> Pending(Dictionary<string, object> args)
        {
            ChatOverlay.EnsureReady();
            ChatBus.MarkPoll();
            List<ChatMessage> list = ChatBus.DrainOutgoing();
            var rows = new List<object>();
            foreach (var m in list)
                rows.Add(Row("seq", m.seq, "at", m.at, "text", m.text));
            return Row(
                "count", list.Count,
                "messages", rows,
                "overlay_open", ChatOverlay.IsOpen,
                "wall", DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss"));
        }

        static Dictionary<string, object> Send(Dictionary<string, object> args)
        {
            string text = Arg(args, "text");
            if (text.Length == 0)
                return Row("available", false, "reason", "text_required", "hint", "传入 1~400 字符的 text");
            if (text.Length > 400) text = text.Substring(0, 400);
            ChatOverlay.EnsureReady();
            ChatBus.AddIncoming(text);
            ChatBus.MarkPoll();
            return Row(
                "available", true,
                "delivered", true,
                "length", text.Length,
                "incoming_total", ChatBus.IncomingTotal,
                "overlay_open", ChatOverlay.IsOpen,
                "wall", DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss"));
        }

        static Dictionary<string, object> Status(Dictionary<string, object> args)
        {
            ChatOverlay.EnsureReady();
            ChatBus.MarkPoll();
            double age = ChatBus.SecondsSincePoll();
            long inboxBytes = -1;
            try
            {
                var fi = new System.IO.FileInfo(ChatBus.InboxPath);
                if (fi.Exists) inboxBytes = fi.Length;
            }
            catch
            {
            }
            return Row(
                "available", true,
                "overlay_open", ChatOverlay.IsOpen,
                "overlay_font_size", ChatOverlay.FontSize,
                "legacy_input_broken", ChatOverlay.LegacyInputBroken,
                "keyboard_blocked", ChatOverlay.KeyboardBlocked,
                "keyboard_block_failed", ChatOverlay.KeyboardBlockFailed,
                "ime_text_input_hooked", ChatOverlay.TextInputHooked,
                "ime_enabled", ChatOverlay.ImeEnabled,
                "ime_chars", ChatOverlay.ImeChars,
                "ime_last_text", ChatOverlay.LastImeText,
                "imgui_ime_on", ChatOverlay.ImguiImeOn,
                "ime_composition", ChatOverlay.ImeComposition,
                "win32_ime_available", ChatOverlay.Win32ImeAvailable,
                "win32_ime_open", ChatOverlay.Win32ImeOpen,
                "win32_ime_error", ChatOverlay.Win32ImeError,
                "win32_ime_ever_worked", ChatOverlay.Win32ImeEverWorked,
                "win32_ime_commits", ChatOverlay.Win32ImeCommits,
                "pending_outgoing", ChatBus.PendingCount,
                "submitted_total", ChatBus.SubmittedTotal,
                "incoming_total", ChatBus.IncomingTotal,
                "last_poll_age_s", age == double.MaxValue ? -1 : Math.Round(age, 1),
                "inbox_path", ChatBus.InboxPath,
                "inbox_bytes", inboxBytes,
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
                "inbox_write_error", ChatBus.LastWriteError,
                "diag_log", ChatDiag.Path_,
                "wall", DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss"));
        }
    }
}





