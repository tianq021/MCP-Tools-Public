// ChatOverlay —— 游戏内隐藏聊天框（默认不可见，按回车呼出，发送给 DSH 管家）
//
// v0.9.1 修复：输入法（IME）可能吞掉 Enter/Escape 导致无法发送/关闭。
//   - 主路径：Update 里读 Input（Enter/F2 发送，Esc/F4 关闭）
//   - 兜底：OnGUI 的 Event.current 里再拦一遍按键（两条路都记录诊断计数）
//   - 功能键 F2/F4 不会被输入法吃掉；打开聊天时解锁鼠标，可点击「发送/关闭」按钮
//   - 所有按键/提交事件写入 out\bridge\chat.log，并暴露计数给 sr_chat_status
//
// 约束：C# 5 / .NET 4.0 —— 不能用字符串插值、?.、表达式体成员。
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using UnityEngine;

namespace SlimeRancherBridge
{
    public class ChatMessage
    {
        public int seq;
        public string at;
        public string text;
    }

    /// <summary>聊天诊断日志：写 out\bridge\chat.log（不依赖 Unity 日志，方便外部排查）。</summary>
    public static class ChatDiag
    {
        public const string Path = @"E:\SlimeRancherMCP\out\bridge\chat.log";

        public static void Log(string message)
        {
            try
            {
                string dir = System.IO.Path.GetDirectoryName(Path);
                if (!string.IsNullOrEmpty(dir))
                {
                    Directory.CreateDirectory(dir);
                }
                File.AppendAllText(Path,
                    DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss.fff") + "  " + message + "\r\n",
                    new UTF8Encoding(false));
            }
            catch
            {
            }
        }
    }

    /// <summary>聊天消息总线（只在主线程读写；加锁是为了以后接后台线程时安全）。</summary>
    public static class ChatBus
    {
        // 玩家消息同时落盘：即使 DSH 没在监听、游戏/对话重启，消息也不会丢。
        // 文件有上限：超过 1MB 时把当前文件改名为 inbox.<时间戳>.jsonl 归档并继续写新文件；
        // 归档文件全部保留（不删除），读取端按「归档→当前」顺序消费。
        public const string InboxPath = @"E:\SlimeRancherMCP\out\chat\inbox.jsonl";
        public const long MaxInboxBytes = 1024 * 1024;

        static readonly object gate = new object();
        static readonly List<ChatMessage> outgoing = new List<ChatMessage>();
        static readonly List<ChatMessage> incoming = new List<ChatMessage>();
        static int nextSeq = 1;
        static DateTime lastPollUtc = DateTime.MinValue;
        static DateTime lastSendUtc = DateTime.MinValue;

        public static ChatMessage AddOutgoing(string text)
        {
            var msg = new ChatMessage();
            lock (gate)
            {
                msg.seq = nextSeq++;
                msg.at = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss");
                msg.text = text;
                outgoing.Add(msg);
                while (outgoing.Count > 50)
                {
                    outgoing.RemoveAt(0);
                }
            }
            WriteInbox(msg);
            return msg;
        }

        static void WriteInbox(ChatMessage msg)
        {
            try
            {
                string dir = Path.GetDirectoryName(InboxPath);
                if (!string.IsNullOrEmpty(dir))
                {
                    Directory.CreateDirectory(dir);
                }
                RotateInboxIfNeeded();
                var sb = new StringBuilder(160);
                sb.Append("{\"seq\":").Append(msg.seq)
                  .Append(",\"at\":\"").Append(msg.at)
                  .Append("\",\"text\":\"").Append(Escape(msg.text)).Append("\"}");
                File.AppendAllText(InboxPath, sb.ToString() + "\r\n", new UTF8Encoding(false));
            }
            catch (Exception e)
            {
                ChatDiag.Log("inbox write failed: " + e.Message);
            }
        }

        static void RotateInboxIfNeeded()
        {
            try
            {
                var info = new FileInfo(InboxPath);
                if (!info.Exists || info.Length <= MaxInboxBytes)
                {
                    return;
                }
                string dir = Path.GetDirectoryName(InboxPath);
                string stamp = DateTime.Now.ToString("yyyyMMdd_HHmmss");
                string archivePath = Path.Combine(dir, "inbox." + stamp + ".jsonl");
                int suffix = 1;
                while (File.Exists(archivePath))
                {
                    archivePath = Path.Combine(dir, "inbox." + stamp + "_" + suffix + ".jsonl");
                    suffix++;
                }
                try
                {
                    File.Move(InboxPath, archivePath);
                    ChatDiag.Log("inbox rotated to " + archivePath);
                }
                catch (Exception e)
                {
                    ChatDiag.Log("inbox rotate failed: " + e.Message);
                }
            }
            catch (Exception)
            {
            }
        }

        static string Escape(string text)
        {
            if (text == null)
            {
                return "";
            }
            var sb = new StringBuilder(text.Length + 16);
            for (int i = 0; i < text.Length; i++)
            {
                char c = text[i];
                if (c == '\\') { sb.Append("\\\\"); }
                else if (c == '"') { sb.Append("\\\""); }
                else if (c == '\r') { sb.Append("\\r"); }
                else if (c == '\n') { sb.Append("\\n"); }
                else if (c == '\t') { sb.Append("\\t"); }
                else if (c < ' ') { sb.Append(' '); }
                else { sb.Append(c); }
            }
            return sb.ToString();
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

        public static void AddIncoming(string text)
        {
            var msg = new ChatMessage();
            lock (gate)
            {
                msg.seq = nextSeq++;
                msg.at = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss");
                msg.text = text;
                incoming.Add(msg);
                while (incoming.Count > 50)
                {
                    incoming.RemoveAt(0);
                }
            }
        }

        public static List<ChatMessage> LastIncoming(int count)
        {
            lock (gate)
            {
                var result = new List<ChatMessage>();
                int from = incoming.Count - count;
                if (from < 0)
                {
                    from = 0;
                }
                for (int i = from; i < incoming.Count; i++)
                {
                    result.Add(incoming[i]);
                }
                return result;
            }
        }

        public static int PendingCount
        {
            get { lock (gate) { return outgoing.Count; } }
        }

        public static int IncomingCount
        {
            get { lock (gate) { return incoming.Count; } }
        }

        public static void MarkPoll()
        {
            lock (gate) { lastPollUtc = DateTime.UtcNow; }
        }

        public static double SecondsSincePoll()
        {
            lock (gate)
            {
                if (lastPollUtc == DateTime.MinValue)
                {
                    return double.MaxValue;
                }
                return (DateTime.UtcNow - lastPollUtc).TotalSeconds;
            }
        }
    }

    /// <summary>隐藏聊天框：回车呼出；Enter/F2 发送，Esc/F4 关闭；平时只在收到回复时弹提示。</summary>
    public class ChatOverlay : MonoBehaviour
    {
        public static ChatOverlay Instance;

        // ---- 诊断计数（sr_chat_status 可查，用来判断 Update/按键是否真的到达）----
        public static int UpdateTicks;
        public static int OpenCount;
        public static int SubmitCount;
        public static int CloseCount;
        public static int InputKeyEvents;
        public static int GuiKeyEvents;
        public static string LastInputKey = "";
        public static string LastGuiKey = "";
        public static string LastSubmitDraft = "";
        public static string LastAction = "";
        public static string LastError = "";

        const int ShownIncoming = 4;
        const int MaxTextLength = 400;
        const int MinFontSize = 16;
        const int MaxFontSize = 48;
        const int DefaultFontSize = 30;
        public const string FontSizePath = @"E:\SlimeRancherMCP\out\chat\ui_scale.txt";

        static Font chatFont;
        static bool fontWarned;
        static int fontSize = DefaultFontSize;
        static bool fontSizeLoaded;
        static float bottomOffsetExtra;
        public const string UiOffsetPath = @"E:\SlimeRancherMCP\out\chat\ui_offset.txt";

        bool open;
        string draft = "";
        bool focusPending;
        string toast = "";
        float toastUntil;
        int lastSeenSeq = -1;
        int lastActionFrame = -1;
        bool cursorTouched;
        float lastKeyLogAt;
        float openedAt;

        public static bool IsOpen
        {
            get { return Instance != null && Instance.open; }
        }

        public static void Ensure()
        {
            if (Instance != null)
            {
                return;
            }
            try
            {
                var go = new GameObject("SlimeRancherChatOverlay");
                UnityEngine.Object.DontDestroyOnLoad(go);
                go.AddComponent<ChatOverlay>();
            }
            catch (Exception e)
            {
                ChatDiag.Log("Ensure failed: " + e);
            }
        }

        void Awake()
        {
            Instance = this;
            LoadFontSize();
            LoadUiOffset();
            ChatDiag.Log("overlay awake (font=" + fontSize + ")");
            // 进游戏后先亮 20 秒提示，告诉玩家怎么呼出（避免不知道键位）
            toast = "管家聊天已就绪：按 F2（或回车）呼出，F2 发送、F4 关闭";
            toastUntil = Time.unscaledTime + 20f;
        }

        static float LineHeight()
        {
            return fontSize + 10f;
        }

        static void LoadFontSize()
        {
            if (fontSizeLoaded)
            {
                return;
            }
            fontSizeLoaded = true;
            bool loaded = false;
            try
            {
                if (File.Exists(FontSizePath))
                {
                    int parsed;
                    if (int.TryParse(File.ReadAllText(FontSizePath).Trim(), out parsed))
                    {
                        fontSize = Mathf.Clamp(parsed, MinFontSize, MaxFontSize);
                        loaded = true;
                    }
                }
            }
            catch (Exception)
            {
            }
            if (!loaded)
            {
                // 没保存过偏好时按分辨率自适应：1440p ≈ 30，1080p ≈ 23
                int adaptive = Mathf.RoundToInt(Screen.height / 48f);
                fontSize = Mathf.Clamp(adaptive, 22, 36);
            }
        }

        // 底部留白：避开游戏快捷栏（约屏幕高度 15%），面板整体上移；可用 Ctrl+PageUp/PageDown 微调
        static float BottomMargin()
        {
            return Mathf.Clamp(Screen.height * 0.155f + bottomOffsetExtra, 120f, 640f);
        }

        static void LoadUiOffset()
        {
            try
            {
                if (File.Exists(UiOffsetPath))
                {
                    float parsed;
                    if (float.TryParse(File.ReadAllText(UiOffsetPath).Trim(), out parsed))
                    {
                        bottomOffsetExtra = Mathf.Clamp(parsed, 0f, 400f);
                    }
                }
            }
            catch (Exception)
            {
            }
        }

        static void SaveUiOffset()
        {
            try
            {
                string dir = Path.GetDirectoryName(UiOffsetPath);
                if (!string.IsNullOrEmpty(dir))
                {
                    Directory.CreateDirectory(dir);
                }
                File.WriteAllText(UiOffsetPath, bottomOffsetExtra.ToString("F0"), new UTF8Encoding(false));
            }
            catch (Exception)
            {
            }
        }

        static void SaveFontSize()
        {
            try
            {
                string dir = Path.GetDirectoryName(FontSizePath);
                if (!string.IsNullOrEmpty(dir))
                {
                    Directory.CreateDirectory(dir);
                }
                File.WriteAllText(FontSizePath, fontSize.ToString(), new UTF8Encoding(false));
            }
            catch (Exception)
            {
            }
        }

        void OnDestroy()
        {
            if (Instance == this)
            {
                Instance = null;
            }
            ChatDiag.Log("overlay destroyed");
        }

        void Update()
        {
            UpdateTicks++;
            try
            {
                bool enter = Input.GetKeyDown(KeyCode.Return) || Input.GetKeyDown(KeyCode.KeypadEnter);
                bool esc = Input.GetKeyDown(KeyCode.Escape);
                bool f2 = Input.GetKeyDown(KeyCode.F2);
                bool f4 = Input.GetKeyDown(KeyCode.F4);
                if (enter || esc || f2 || f4 || Input.anyKeyDown)
                {
                    InputKeyEvents++;
                    LastInputKey = KeyName(enter, esc, f2, f4);
                }

                if (open)
                {
                    if (enter) { TrySubmit("Input:Enter"); }
                    else if (f2) { TrySubmit("Input:F2"); }
                    else if (esc) { TryClose("Input:Esc"); }
                    else if (f4) { TryClose("Input:F4"); }
                    if (Input.GetKeyDown(KeyCode.PageUp))
                    {
                        if (Input.GetKey(KeyCode.LeftControl) || Input.GetKey(KeyCode.RightControl))
                        {
                            bottomOffsetExtra = Mathf.Clamp(bottomOffsetExtra + 16f, 0f, 400f);
                            SaveUiOffset();
                            ChatDiag.Log("ui offset -> " + bottomOffsetExtra);
                        }
                        else
                        {
                            SetFontSize(fontSize + 2);
                        }
                    }
                    else if (Input.GetKeyDown(KeyCode.PageDown))
                    {
                        if (Input.GetKey(KeyCode.LeftControl) || Input.GetKey(KeyCode.RightControl))
                        {
                            bottomOffsetExtra = Mathf.Clamp(bottomOffsetExtra - 16f, 0f, 400f);
                            SaveUiOffset();
                            ChatDiag.Log("ui offset -> " + bottomOffsetExtra);
                        }
                        else
                        {
                            SetFontSize(fontSize - 2);
                        }
                    }
                    KeepCursorFree();
                }
                else
                {
                    bool newlineInString = Input.inputString != null && Input.inputString.IndexOf('\n') >= 0;
                    if (enter || f2 || newlineInString)
                    {
                        ChatDiag.Log("open requested by Input key=" + LastInputKey
                            + " inputString=" + Truncate(Input.inputString, 20));
                        Open();
                    }
                }

                // 关闭状态下低频记录按键，便于排查「哪些键真的到达」（每秒最多一条）
                if (!open && Input.anyKeyDown && Time.unscaledTime > lastKeyLogAt + 1f)
                {
                    lastKeyLogAt = Time.unscaledTime;
                    ChatDiag.Log("key while closed: " + LastInputKey
                        + " inputString=" + Truncate(Input.inputString, 20));
                }

                // 收到新回复且聊天框关着：弹一条短提示（完整内容，多留几秒）
                if (!open)
                {
                    var last = ChatBus.LastIncoming(1);
                    if (last.Count > 0 && last[0].seq != lastSeenSeq)
                    {
                        lastSeenSeq = last[0].seq;
                        toast = "管家：" + (last[0].text ?? "");
                        toastUntil = Time.unscaledTime + 15f;
                    }
                    RestoreCursorIfNeeded();
                }
            }
            catch (Exception e)
            {
                LastError = e.GetType().Name + ": " + e.Message;
                ChatDiag.Log("Update EX: " + e);
            }
        }

        string KeyName(bool enter, bool esc, bool f2, bool f4)
        {
            var parts = new List<string>();
            if (enter) { parts.Add("Enter"); }
            if (esc) { parts.Add("Esc"); }
            if (f2) { parts.Add("F2"); }
            if (f4) { parts.Add("F4"); }
            if (parts.Count == 0)
            {
                parts.Add(Truncate(Input.inputString ?? "", 24));
            }
            return string.Join("+", parts.ToArray());
        }

        void KeepCursorFree()
        {
            try
            {
                if (!cursorTouched)
                {
                    cursorTouched = true;
                    ChatDiag.Log("cursor unlocked for chat");
                }
                Cursor.visible = true;
                Cursor.lockState = CursorLockMode.None;
            }
            catch (Exception)
            {
            }
        }

        void RestoreCursorIfNeeded()
        {
            if (!cursorTouched)
            {
                return;
            }
            try
            {
                cursorTouched = false;
                Cursor.visible = false;
                Cursor.lockState = CursorLockMode.Locked;
                ChatDiag.Log("cursor restored");
            }
            catch (Exception)
            {
            }
        }

        static void SetFontSize(int value)
        {
            int next = Mathf.Clamp(value, MinFontSize, MaxFontSize);
            if (next == fontSize)
            {
                return;
            }
            fontSize = next;
            chatFont = null;
            SaveFontSize();
            ChatDiag.Log("font size -> " + fontSize);
        }

        void Open()
        {
            if (open)
            {
                return;
            }
            open = true;
            OpenCount++;
            openedAt = Time.unscaledTime;
            lastActionFrame = Time.frameCount;  // 吞掉同一次按键的后续处理
            draft = "";
            focusPending = true;
            LastAction = "open";
            ChatDiag.Log("open (count=" + OpenCount + ")");
            var last = ChatBus.LastIncoming(1);
            if (last.Count > 0)
            {
                lastSeenSeq = last[0].seq;
            }
        }

        bool TooSoonAfterOpen()
        {
            // 呼出和发送共用 F2：打开后的 0.35 秒内忽略发送/关闭，避免「一按就开、同一按键又立刻发/关」
            return Time.unscaledTime - openedAt < 0.35f;
        }

        void TrySubmit(string source)
        {
            if (!open)
            {
                return;
            }
            if (Time.frameCount == lastActionFrame || TooSoonAfterOpen())
            {
                ChatDiag.Log("submit ignored via " + source + " (too soon)");
                return;
            }
            lastActionFrame = Time.frameCount;
            LastAction = "submit@" + source;
            ChatDiag.Log("submit attempt via " + source + " draft_len=" + (draft == null ? 0 : draft.Length));
            Submit();
        }

        void TryClose(string source)
        {
            if (!open)
            {
                return;
            }
            if (Time.frameCount == lastActionFrame || TooSoonAfterOpen())
            {
                ChatDiag.Log("close ignored via " + source + " (too soon)");
                return;
            }
            lastActionFrame = Time.frameCount;
            LastAction = "close@" + source;
            ChatDiag.Log("close attempt via " + source);
            Close();
        }

        void Close()
        {
            if (!open)
            {
                return;
            }
            open = false;
            CloseCount++;
            draft = "";
            focusPending = false;
            RestoreCursorIfNeeded();
        }

        void Submit()
        {
            string text = draft == null ? "" : draft.Trim();
            LastSubmitDraft = Truncate(text, 80);
            if (text.Length == 0)
            {
                // 空草稿不关闭聊天框（否则「F2 呼出」会被同一次按键当成「空发送」立刻关掉）
                ChatDiag.Log("submit ignored: empty draft (keep open)");
                return;
            }
            draft = "";
            if (text.Length > MaxTextLength)
            {
                text = text.Substring(0, MaxTextLength);
            }
            ChatBus.AddOutgoing(text);
            SubmitCount++;
            ChatDiag.Log("submitted #" + SubmitCount + " len=" + text.Length + " text=" + Truncate(text, 60));
            var lastReply = ChatBus.LastIncoming(1);
            if (lastReply.Count > 0)
            {
                lastSeenSeq = lastReply[0].seq;
            }
            Close();
            toast = "已发送给管家，等待回复…（按 Enter 查看）";
            toastUntil = Time.unscaledTime + 5f;
        }

        void OnGUI()
        {
            Font f = GetFont();
            Font oldSkin = GUI.skin.font;
            Font oldLabel = GUI.skin.label.font;
            Font oldField = GUI.skin.textField.font;
            Font oldBox = GUI.skin.box.font;
            Font oldBtn = GUI.skin.button.font;
            try
            {
                GUI.skin.font = f;
                GUI.skin.label.font = f;
                GUI.skin.textField.font = f;
                GUI.skin.box.font = f;
                GUI.skin.button.font = f;

                // 兜底：IMGUI 事件里再拦一遍按键（Input 被输入法吞掉时仍可能到这里）
                HandleGuiEvent();

                if (open)
                {
                    DrawPanel();
                }
                else if (Time.unscaledTime < toastUntil && toast.Length > 0)
                {
                    DrawToast();
                }
            }
            catch (Exception e)
            {
                LastError = "OnGUI " + e.GetType().Name + ": " + e.Message;
                if (!fontWarned)
                {
                    fontWarned = true;
                    ChatDiag.Log("OnGUI EX: " + e);
                }
            }
            finally
            {
                GUI.skin.font = oldSkin;
                GUI.skin.label.font = oldLabel;
                GUI.skin.textField.font = oldField;
                GUI.skin.box.font = oldBox;
                GUI.skin.button.font = oldBtn;
            }
        }

        void HandleGuiEvent()
        {
            Event e = Event.current;
            if (e == null || e.type != EventType.KeyDown)
            {
                return;
            }
            GuiKeyEvents++;
            LastGuiKey = e.keyCode.ToString();
            if (!open)
            {
                // 关闭状态只用 F2 呼出（F1 是游戏史莱姆百科快捷键，不能占用）
                if (e.keyCode == KeyCode.F2)
                {
                    ChatDiag.Log("open requested by GUI key=" + e.keyCode);
                    Open();
                    e.Use();
                }
                return;
            }
            if (e.keyCode == KeyCode.Return || e.keyCode == KeyCode.KeypadEnter)
            {
                TrySubmit("GUI:Enter");
                e.Use();
            }
            else if (e.keyCode == KeyCode.Escape)
            {
                TryClose("GUI:Esc");
                e.Use();
            }
            else if (e.keyCode == KeyCode.F2)
            {
                TrySubmit("GUI:F2");
                e.Use();
            }
            else if (e.keyCode == KeyCode.F4)
            {
                TryClose("GUI:F4");
                e.Use();
            }
        }

        void DrawPanel()
        {
            var replies = ChatBus.LastIncoming(ShownIncoming);
            var style = WrapLabel();
            float lh = LineHeight();
            float titleH = fontSize + 10f;
            float fieldH = fontSize + 14f;
            float hintH = fontSize + 6f;
            float width = Mathf.Clamp(Screen.width * 0.72f, 700f, 1500f);
            float avail = width - 24f;
            // 用 GUIStyle.CalcHeight 精确计算每条回复换行后的高度；整体太高就丢掉最早的消息，只显示最新
            var shown = new List<ChatMessage>(replies);
            var heights = new List<float>();
            float repliesH = 0f;
            while (shown.Count > 0)
            {
                heights.Clear();
                repliesH = 0f;
                for (int i = 0; i < shown.Count; i++)
                {
                    float hh = style.CalcHeight(new GUIContent("管家：" + Truncate(shown[i].text, 1200)), avail);
                    if (hh < lh)
                    {
                        hh = lh;
                    }
                    heights.Add(hh);
                    repliesH += hh;
                }
                bool fits = titleH + repliesH + fieldH + hintH * 2f + 26f <= Screen.height * 0.72f;
                if (fits || shown.Count <= 1)
                {
                    break;
                }
                shown.RemoveAt(0);
            }
            if (shown.Count == 0)
            {
                repliesH = lh;
            }
            float height = titleH + repliesH + fieldH + hintH * 2f + 26f;
            float x = (Screen.width - width) * 0.5f;
            float y = Screen.height - height - BottomMargin();
            if (y < 16f)
            {
                y = 16f;
            }

            Color oldBg = GUI.backgroundColor;
            GUI.backgroundColor = new Color(0.06f, 0.08f, 0.12f, 0.94f);
            GUI.Box(new Rect(x, y, width, height), GUIContent.none);
            GUI.backgroundColor = oldBg;

            Color oldColor = GUI.color;
            GUI.color = new Color(0.55f, 0.82f, 1f);
            GUI.Label(new Rect(x + 12, y + 6, width * 0.56f, titleH), "管家聊天  ·  F2 发送  ·  F4 关闭");
            GUI.color = new Color(0.62f, 0.66f, 0.72f);
            GUI.Label(new Rect(x + width * 0.42f, y + 6, width * 0.58f - 24f, titleH), StatusText(), LabelRight());
            GUI.color = oldColor;

            float lineY = y + titleH;
            for (int i = 0; i < shown.Count; i++)
            {
                string line = "管家：" + Truncate(shown[i].text, 1200);
                GUI.Label(new Rect(x + 12, lineY, avail, heights[i]), line, style);
                lineY += heights[i];
            }
            if (shown.Count == 0)
            {
                GUI.color = new Color(0.5f, 0.52f, 0.56f);
                GUI.Label(new Rect(x + 12, lineY, avail, lh), "（还没有回复：输入内容后按 F2 发送）");
                GUI.color = oldColor;
            }

            float fieldY = y + height - fieldH - hintH * 2f - 12f;
            float btnW = Mathf.Max(96f, fontSize * 5.4f);
            float fieldW = width - 24f - btnW * 2f - 16f;
            GUI.SetNextControlName("ChatInput");
            string edited = GUI.TextField(new Rect(x + 12, fieldY, fieldW, fieldH), draft, MaxTextLength);
            if (edited != draft)
            {
                draft = edited;
            }
            if (focusPending)
            {
                GUI.FocusControl("ChatInput");
                focusPending = false;
            }
            if (GUI.Button(new Rect(x + width - 12f - btnW * 2f - 8f, fieldY, btnW, fieldH), "发送(F2)"))
            {
                TrySubmit("Button:Send");
            }
            if (GUI.Button(new Rect(x + width - 12f - btnW, fieldY, btnW - 4f, fieldH), "关闭(F4)"))
            {
                TryClose("Button:Close");
            }

            GUI.color = new Color(0.55f, 0.6f, 0.66f);
            GUI.Label(new Rect(x + 12, fieldY + fieldH + 4f, width - 24, hintH),
                "PageUp/PageDown 调字号（当前 " + fontSize + "） · Ctrl+PageUp/PageDown 调面板高度 · Esc 也能关闭");
            string diag = "按键诊断：Input " + InputKeyEvents + " / GUI " + GuiKeyEvents
                + " · 打开 " + OpenCount + " · 发送 " + SubmitCount + " · 关闭 " + CloseCount
                + " · 最近键 " + LastInputKey + "/" + LastGuiKey;
            GUI.Label(new Rect(x + 12, fieldY + fieldH + 4f + hintH, width - 24, hintH), Truncate(diag, 160));
            GUI.color = oldColor;
        }

        void DrawToast()
        {
            var style = WrapLabel();
            float width = Mathf.Clamp(Screen.width * 0.78f, 620f, 1500f);
            float avail = width - 24f;
            string body = Truncate(toast, 1200);
            float textH = style.CalcHeight(new GUIContent(body), avail);
            if (textH < fontSize)
            {
                textH = fontSize;
            }
            float h = Mathf.Min(textH + 16f, Screen.height * 0.45f);
            float x = (Screen.width - width) * 0.5f;
            float y = Screen.height - BottomMargin() - h - 8f;
            if (y < 16f)
            {
                y = 16f;
            }
            Color oldBg = GUI.backgroundColor;
            GUI.backgroundColor = new Color(0.06f, 0.08f, 0.12f, 0.92f);
            GUI.Box(new Rect(x, y, width, h), GUIContent.none);
            GUI.backgroundColor = oldBg;
            GUI.color = new Color(0.75f, 0.9f, 1f);
            GUI.Label(new Rect(x + 12, y + 8, avail, h - 10f), body, style);
            GUI.color = Color.white;
        }

        string StatusText()
        {
            double age = ChatBus.SecondsSincePoll();
            if (age < 10)
            {
                return "管家在线（" + ((int)age) + " 秒前检查）";
            }
            if (ChatBus.PendingCount > 0)
            {
                return "管家离线，消息排队中（" + ChatBus.PendingCount + " 条）";
            }
            return "管家离线（消息会排队，等它上线再取）";
        }

        static GUIStyle labelRight;
        static GUIStyle wrapLabel;

        static GUIStyle LabelRight()
        {
            if (labelRight == null)
            {
                labelRight = new GUIStyle(GUI.skin.label);
                labelRight.alignment = TextAnchor.UpperRight;
            }
            else
            {
                labelRight.font = GUI.skin.label.font;
            }
            return labelRight;
        }

        static GUIStyle WrapLabel()
        {
            if (wrapLabel == null)
            {
                wrapLabel = new GUIStyle(GUI.skin.label);
                wrapLabel.wordWrap = true;
                wrapLabel.alignment = TextAnchor.UpperLeft;
                wrapLabel.clipping = TextClipping.Clip;
            }
            else
            {
                wrapLabel.font = GUI.skin.label.font;
            }
            return wrapLabel;
        }

        /// <summary>按像素宽度粗估文本需要几行（中文按 1em 计，偏保守）。</summary>
        static int EstimateLines(string text, float width, int size)
        {
            if (string.IsNullOrEmpty(text))
            {
                return 1;
            }
            float perChar = Mathf.Max(9f, size * 0.95f);
            int perLine = Mathf.Max(8, Mathf.FloorToInt(width / perChar));
            int lines = 0;
            string[] parts = text.Split('\n');
            for (int i = 0; i < parts.Length; i++)
            {
                int len = parts[i].Length;
                lines += Mathf.Max(1, Mathf.CeilToInt(len / (float)perLine));
            }
            return Mathf.Max(1, lines);
        }

        static string Truncate(string text, int max)
        {
            if (text == null)
            {
                return "";
            }
            string flat = text.Replace("\r", " ").Replace("\n", " ");
            if (flat.Length <= max)
            {
                return flat;
            }
            return flat.Substring(0, max) + "…";
        }

        static Font GetFont()
        {
            if (chatFont != null)
            {
                return chatFont;
            }
            try
            {
                chatFont = Font.CreateDynamicFontFromOSFont(
                    new string[] { "Microsoft YaHei", "微软雅黑", "SimHei", "黑体", "SimSun", "宋体", "Arial" }, fontSize);
            }
            catch (Exception)
            {
            }
            if (chatFont == null)
            {
                try
                {
                    chatFont = Resources.GetBuiltinResource(typeof(Font), "Arial.ttf") as Font;
                }
                catch (Exception)
                {
                }
            }
            return chatFont;
        }
    }
}
