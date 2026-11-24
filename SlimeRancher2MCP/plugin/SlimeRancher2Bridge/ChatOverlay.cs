// ChatOverlay —— 游戏内聊天框（移植自一代 SlimeRancherBridge/ChatOverlay.cs）
//
//   平时隐藏；按 Enter（或 F2）呼出；Enter/F2 发送，Esc/F4 关闭。
//   玩家消息进 ChatBus.outgoing（落盘 out\chat\inbox.jsonl），DSH 侧用 sr2_chat_pending 取；
//   DSH 用 sr2_chat_send 推回的回复显示在面板里，关闭状态下弹 15 秒提示条。
//
// 与一代的差异：
//   ① 不走 MonoBehaviour/AddComponent（IL2CPP 下不便注册新托管组件），改为静态类，
//      由 SR2BridgeMod.OnUpdate / OnGUI 直接驱动；
//   ② 输入同时走两条路：UnityEngine.Input 主路径 + IMGUI Event 兜底路径（输入法/焦点异常时）；
//   ③ 样式用 new GUIStyle() 自建（互操作程序集里没有 GUIStyle(GUIStyle) 拷贝构造），
//      文字高度用字符宽度估算，不依赖 GUIContent/CalcHeight；
//   ④ 所有按键、提交、异常都写 out\chat\chat.log，并暴露诊断计数给 sr2_chat_status。
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using UnityEngine;

namespace SlimeRancher2Bridge
{
    /// <summary>聊天诊断日志（独立文件，方便外部排查按键/焦点问题）。</summary>
    internal static class ChatDiag
    {
        static string path;
        static readonly object gate = new object();

        public static string Path_
        {
            get
            {
                if (path != null) return path;
                try
                {
                    string chatDir = System.IO.Path.GetDirectoryName(ChatBus.InboxPath);
                    path = System.IO.Path.Combine(chatDir, "chat.log");
                }
                catch
                {
                    path = "chat.log";
                }
                return path;
            }
        }

        public static void Log(string message)
        {
            try
            {
                lock (gate)
                {
                    string dir = System.IO.Path.GetDirectoryName(Path_);
                    if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
                    File.AppendAllText(Path_,
                        DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss.fff") + "  " + message + "\r\n",
                        new UTF8Encoding(false));
                }
            }
            catch
            {
            }
        }
    }

    internal static class ChatOverlay
    {
        const int MaxTextLength = 300;
        const int ShownIncoming = 4;
        const int DefaultFontSize = 26;

        // ---- 状态 ----
        static bool open;
        static string draft = "";
        static bool focusPending;
        static string toast = "";
        static float toastUntil;
        static int lastSeenSeq = -1;
        static int lastActionFrame = -1;
        static float openedAt;
        static bool cursorTouched;
        static bool legacyInputBroken;
        static Font chatFont;
        static int fontSize = DefaultFontSize;
        static float lastKeyLogAt;
        static bool keyboardBlocked;
        static float lastGuiKeyAt;
        static bool keyboardBlockFailed;
        static bool imeHookTried;
        static bool textInputHooked;
        static bool imeEnabled;
        static int imeChars;
        static string lastImeText = "";
        static bool imguiImeOn;
        static string imeCompositionState = "";
        static string lastComposition = "";

        // ---- 诊断计数（sr2_chat_status 可查）----
        public static int UpdateTicks;
        public static int OpenCount;
        public static int SubmitCount;
        public static int CloseCount;
        public static int InputKeyEvents;
        public static int GuiKeyEvents;
        public static string LastInputKey = "";
        public static string LastGuiKey = "";
        public static string LastAction = "";
        public static string LastSubmitDraft = "";
        public static string LastError = "";

        public static bool IsOpen => open;
        public static bool LegacyInputBroken => legacyInputBroken;
        public static bool KeyboardBlocked => keyboardBlocked;
        public static bool KeyboardBlockFailed => keyboardBlockFailed;
        public static bool TextInputHooked => textInputHooked;
        public static bool ImeEnabled => imeEnabled;
        public static int ImeChars => imeChars;
        public static string LastImeText => lastImeText;
        public static string ImeComposition => imeCompositionState;
        public static bool ImguiImeOn => imguiImeOn;
        public static bool Win32ImeAvailable => Win32Ime.Available;
        public static bool Win32ImeOpen => Win32Ime.ImeOpen;
        public static string Win32ImeError => Win32Ime.LastReadError;
        public static bool Win32ImeEverWorked => Win32Ime.EverWorked;
        public static int Win32ImeCommits => Win32Ime.CommitCount;
        public static int FontSize => fontSize;

        public static void EnsureReady()
        {
            if (chatFont == null) chatFont = BuildFont();
        }

        // ==================== 每帧逻辑（OnUpdate 调用）====================

        public static void Update()
        {
            UpdateTicks++;
            try
            {
                EnsureReady();
                bool enter = KeyDown(KeyCode.Return) || KeyDown(KeyCode.KeypadEnter);
                bool esc = KeyDown(KeyCode.Escape);
                bool f2 = KeyDown(KeyCode.F2);
                bool f4 = KeyDown(KeyCode.F4);
                bool any = AnyKeyDown();
                if (enter || esc || f2 || f4 || any)
                {
                    InputKeyEvents++;
                    LastInputKey = DescribeKey(enter, esc, f2, f4);
                }

                if (open)
                {
                    if (enter) TrySubmit("Input:Enter");
                    else if (f2) TrySubmit("Input:F2");
                    else if (esc) TryClose("Input:Esc");
                    else if (f4) TryClose("Input:F4");
                    if (KeyDown(KeyCode.PageUp)) SetFontSize(fontSize + 2);
                    else if (KeyDown(KeyCode.PageDown)) SetFontSize(fontSize - 2);
                    KeepCursorFree();
                }
                else if (enter || f2)
                {
                    Open("Input:" + (enter ? "Enter" : "F2"));
                }

                // 关闭状态下收到新回复：弹提示条
                if (!open)
                {
                    var last = ChatBus.LastIncoming(1);
                    if (last.Count > 0 && last[0].seq != lastSeenSeq)
                    {
                        lastSeenSeq = last[0].seq;
                        toast = "管家：" + Trim(last[0].text, 300);
                        toastUntil = Time.unscaledTime + 15f;
                    }
                    RestoreCursor();
                }

                // 低频记录按键，便于排查「哪些键真的到达」
                // 看门狗：屏蔽键盘后若 15 秒没有任何 IMGUI 按键事件，说明可能把输入通道也挡住了，自动恢复
                if (keyboardBlocked && Time.unscaledTime - lastGuiKeyAt > 15f)
                {
                    ChatDiag.Log("keyboard watchdog: no GUI key events, restoring");
                    SetKeyboardBlocked(false);
                }

                if (!open && any && Time.unscaledTime > lastKeyLogAt + 1f)
                {
                    lastKeyLogAt = Time.unscaledTime;
                    ChatDiag.Log("key while closed: " + LastInputKey + " inputString=" + Trim(SafeInputString(), 20));
                }
            }
            catch (Exception e)
            {
                LastError = e.GetType().Name + ": " + e.Message;
                ChatDiag.Log("Update EX: " + e);
            }
        }

        /// <summary>读 Input；若游戏禁用旧输入类会抛异常，此时只依赖 IMGUI 兜底并记一次日志。</summary>
        static bool KeyDown(KeyCode key)
        {
            if (legacyInputBroken) return false;
            try
            {
                return UnityEngine.Input.GetKeyDown(key);
            }
            catch (Exception e)
            {
                legacyInputBroken = true;
                LastError = "legacy input unavailable: " + e.GetBaseException().Message;
                ChatDiag.Log(LastError);
                return false;
            }
        }

        static bool AnyKeyDown()
        {
            if (legacyInputBroken) return false;
            try { return UnityEngine.Input.anyKeyDown; }
            catch { return false; }
        }

        static string SafeInputString()
        {
            if (legacyInputBroken) return "";
            try { return UnityEngine.Input.inputString; }
            catch { return ""; }
        }

        static string DescribeKey(bool enter, bool esc, bool f2, bool f4)
        {
            var parts = new List<string>();
            if (enter) parts.Add("Enter");
            if (esc) parts.Add("Esc");
            if (f2) parts.Add("F2");
            if (f4) parts.Add("F4");
            if (parts.Count == 0) parts.Add(Trim(SafeInputString(), 24));
            return string.Join("+", parts);
        }

        static void Open(string source)
        {
            if (open) return;
            open = true;
            OpenCount++;
            openedAt = Time.unscaledTime;
            lastActionFrame = Time.frameCount;
            draft = "";
            focusPending = true;
            LastAction = "open@" + source;
            ChatDiag.Log("open (" + source + ", count=" + OpenCount + ")");
            lastGuiKeyAt = Time.unscaledTime;
            SetKeyboardBlocked(true);
            SetImguiIme(true);
            lastComposition = "";
            Win32Ime.Open();
            var last = ChatBus.LastIncoming(1);
            if (last.Count > 0) lastSeenSeq = last[0].seq;
        }

        static void Close(string source)
        {
            if (!open) return;
            open = false;
            CloseCount++;
            draft = "";
            focusPending = false;
            LastAction = "close@" + source;
            ChatDiag.Log("close (" + source + ")");
            SetImguiIme(false);
            Win32Ime.Close();
            SetKeyboardBlocked(false);
            RestoreCursor();
        }

        static bool TooSoonAfterOpen() => Time.unscaledTime - openedAt < 0.35f;

        static void TrySubmit(string source)
        {
            if (!open) return;
            if (Time.frameCount == lastActionFrame || TooSoonAfterOpen()) return;
            lastActionFrame = Time.frameCount;
            LastAction = "submit@" + source;
            Submit();
        }

        static void TryClose(string source)
        {
            if (!open) return;
            if (Time.frameCount == lastActionFrame || TooSoonAfterOpen()) return;
            lastActionFrame = Time.frameCount;
            Close(source);
        }

        static void Submit()
        {
            string text = (draft ?? "").Trim();
            LastSubmitDraft = Trim(text, 80);
            if (text.Length == 0)
            {
                ChatDiag.Log("submit ignored: empty draft");
                return;
            }
            if (text.Length > MaxTextLength) text = text.Substring(0, MaxTextLength);
            draft = "";
            ChatBus.AddOutgoing(text);
            SubmitCount++;
            ChatDiag.Log("submitted #" + SubmitCount + " len=" + text.Length + " text=" + Trim(text, 60));
            var last = ChatBus.LastIncoming(1);
            if (last.Count > 0) lastSeenSeq = last[0].seq;
            Close("submit");
            toast = "已发送给管家，等待回复…（按 Enter 查看）";
            toastUntil = Time.unscaledTime + 6f;
        }

        /// <summary>走 Windows IMM32 读输入法：组合串用于显示，提交串追加进草稿。</summary>
        static void PollWin32Ime()
        {
            try
            {
                string committed = Win32Ime.Poll(out string composing);
                if (!string.IsNullOrEmpty(composing)) imeCompositionState = composing;
                else if (imeCompositionState == null || !Win32Ime.Available) imeCompositionState = "";
                if (!string.IsNullOrEmpty(committed))
                {
                    bool hasCjk = false;
                    foreach (char c in committed) { if (c > 0x7F) { hasCjk = true; break; } }
                    if (!hasCjk)
                    {
                        ChatDiag.Log("Win32Ime skip ascii-ish commit: '" + committed + "'");
                        return;
                    }
                    if (draft == null) draft = "";
                    draft += committed;
                    imeChars += committed.Length;
                    lastImeText += committed;
                    if (lastImeText.Length > 40) lastImeText = lastImeText.Substring(lastImeText.Length - 40);
                    ChatDiag.Log("draft after Win32 commit len=" + draft.Length);
                }
            }
            catch (Exception e)
            {
                ChatDiag.Log("PollWin32Ime failed: " + e.GetBaseException().Message);
            }
        }
        /// <summary>打开/关闭 IMGUI 的输入法组合模式（中文走这条路）。</summary>
        static void SetImguiIme(bool on)
        {
            try
            {
                var guiUtilityType = Il2CppUtil.FindType("UnityEngine.GUIUtility");
                var modeType = Il2CppUtil.FindType("UnityEngine.IMECompositionMode");
                if (guiUtilityType == null || modeType == null)
                {
                    ChatDiag.Log("imgui IME: types missing");
                    return;
                }
                var prop = guiUtilityType.GetProperty("imeCompositionMode");
                if (prop == null)
                {
                    ChatDiag.Log("imgui IME: imeCompositionMode missing");
                    return;
                }
                object mode = Enum.Parse(modeType, on ? "On" : "Auto");
                prop.SetValue(null, mode);
                imguiImeOn = on;
                ChatDiag.Log("imgui IME mode=" + (on ? "On" : "Auto"));
            }
            catch (Exception e)
            {
                ChatDiag.Log("SetImguiIme failed: " + e.GetBaseException().Message);
            }
        }

        /// <summary>每帧读 GUIUtility.compositionString；组合结束（变空）时把上一步组合串提交到草稿。</summary>
        static void PollImeComposition()
        {
            try
            {
                if (!open) return;
                var guiUtilityType = Type.GetType("UnityEngine.GUIUtility, UnityEngine.IMGUIModule");
                var prop = guiUtilityType?.GetProperty("compositionString");
                if (prop == null) return;
                string composing = prop.GetValue(null) as string ?? "";
                imeCompositionState = composing;
                if (composing == lastComposition) return;
                // 只记录组合状态用于显示；文本提交统一交给 Win32 IMM32 通道，避免重复
                ChatDiag.Log("imgui composing: '" + composing + "'");
                lastComposition = composing;
            }
            catch (Exception e)
            {
                ChatDiag.Log("PollImeComposition failed: " + e.GetBaseException().Message);
            }
        }
        /// <summary>挂 Keyboard.onTextInput（Input System 的 IME 提交字符会走这里）并打开 IME。</summary>
        static void EnsureImeHook()
        {
            if (imeHookTried) return;
            imeHookTried = true;
            try
            {
                var keyboardType = Type.GetType("UnityEngine.InputSystem.Keyboard, Unity.InputSystem");
                if (keyboardType == null)
                {
                    ChatDiag.Log("IME hook: Keyboard type not found");
                    return;
                }
                object keyboard = keyboardType.GetProperty("current")?.GetValue(null);
                if (keyboard == null)
                {
                    ChatDiag.Log("IME hook: Keyboard.current is null");
                    return;
                }
                var addMethod = keyboardType.GetMethod("add_onTextInput");
                if (addMethod == null)
                {
                    ChatDiag.Log("IME hook: add_onTextInput missing");
                    return;
                }
                var delegateType = addMethod.GetParameters()[0].ParameterType;
                var managed = new Action<char>(OnImeText);
                var convert = typeof(Il2CppInterop.Runtime.DelegateSupport).GetMethod("ConvertDelegate");
                if (convert == null)
                {
                    ChatDiag.Log("IME hook: DelegateSupport.ConvertDelegate missing");
                    return;
                }
                object nativeDelegate = convert.MakeGenericMethod(delegateType).Invoke(null, new object[] { managed });
                addMethod.Invoke(keyboard, new[] { nativeDelegate });
                textInputHooked = true;
                ChatDiag.Log("IME hook: onTextInput subscribed");
            }
            catch (Exception e)
            {
                ChatDiag.Log("IME hook failed: " + e.GetBaseException().Message);
            }
        }

        static void OnImeText(char ch)
        {
            try
            {
                if (!open) return;
                if (char.IsControl(ch)) return;
                if (draft == null) draft = "";
                if (draft.Length >= MaxTextLength) return;
                draft += ch;
                imeChars++;
                lastImeText += ch;
                if (lastImeText.Length > 40) lastImeText = lastImeText.Substring(lastImeText.Length - 40);
                ChatDiag.Log("ime char: '" + ch + "' len=" + draft.Length);
            }
            catch (Exception e)
            {
                ChatDiag.Log("OnImeText failed: " + e.GetBaseException().Message);
            }
        }

        static void SetImeEnabled(bool enabled)
        {
            try
            {
                var keyboardType = Type.GetType("UnityEngine.InputSystem.Keyboard, Unity.InputSystem");
                object keyboard = keyboardType?.GetProperty("current")?.GetValue(null);
                if (keyboard == null) return;
                var method = keyboardType.GetMethod("SetIMEEnabled");
                if (method == null) return;
                method.Invoke(keyboard, new object[] { enabled });
                imeEnabled = enabled;
                ChatDiag.Log("IME enabled=" + enabled);
            }
            catch (Exception e)
            {
                ChatDiag.Log("SetImeEnabled failed: " + e.GetBaseException().Message);
            }
        }
        /// <summary>聊天打开时屏蔽物理键盘，避免打字把角色也带着走；关闭时恢复。</summary>
        static void SetKeyboardBlocked(bool blocked)
        {
            try
            {
                var inputSystemType = Type.GetType("UnityEngine.InputSystem.InputSystem, Unity.InputSystem");
                var keyboardType = Type.GetType("UnityEngine.InputSystem.Keyboard, Unity.InputSystem");
                if (inputSystemType == null || keyboardType == null)
                {
                    keyboardBlockFailed = true;
                    return;
                }
                var current = keyboardType.GetProperty("current")?.GetValue(null);
                if (current == null)
                {
                    keyboardBlockFailed = true;
                    return;
                }
                if (blocked)
                {
                    var disable = inputSystemType.GetMethod("DisableDevice", new[] { current.GetType(), typeof(bool) })
                        ?? inputSystemType.GetMethod("DisableDevice");
                    if (disable == null) { keyboardBlockFailed = true; return; }
                    disable.Invoke(null, new[] { current, (object)false });
                }
                else
                {
                    var enable = inputSystemType.GetMethod("EnableDevice");
                    if (enable == null) { keyboardBlockFailed = true; return; }
                    enable.Invoke(null, new[] { current });
                }
                keyboardBlocked = blocked;
                ChatDiag.Log("keyboard blocked=" + blocked);
            }
            catch (Exception e)
            {
                keyboardBlockFailed = true;
                ChatDiag.Log("keyboard block failed: " + e.GetBaseException().Message);
            }
        }
        static void KeepCursorFree()
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
            catch
            {
            }
        }

        static void RestoreCursor()
        {
            if (!cursorTouched) return;
            try
            {
                cursorTouched = false;
                Cursor.visible = false;
                Cursor.lockState = CursorLockMode.Locked;
                ChatDiag.Log("cursor restored");
            }
            catch
            {
            }
        }

        static void SetFontSize(int value)
        {
            int next = Mathf.Clamp(value, 16, 48);
            if (next == fontSize) return;
            fontSize = next;
            chatFont = BuildFont();
            ChatDiag.Log("font size -> " + fontSize);
        }

        // ==================== 绘制（OnGUI 调用）====================

        public static void Draw()
        {
            try
            {
                HandleGuiEvent();
                PollImeComposition();
                if (open) PollWin32Ime();
                if (open) DrawPanel();
                else if (Time.unscaledTime < toastUntil && toast.Length > 0) DrawToast();
            }
            catch (Exception e)
            {
                string key = e.GetType().Name + ": " + e.Message;
                if (key != LastError)
                {
                    LastError = key;
                    ChatDiag.Log("OnGUI EX: " + e);
                }
            }
        }

        /// <summary>IMGUI 兜底按键：旧输入类被禁用或输入法吞键时，这里仍可能收到按键。</summary>
        static void HandleGuiEvent()
        {
            Event e = Event.current;
            if (e == null || e.type != EventType.KeyDown) return;
            GuiKeyEvents++;
            lastGuiKeyAt = Time.unscaledTime;
            LastGuiKey = e.keyCode + (e.character == '\0' ? "" : "  '" + e.character + "'");
            if (!open)
            {
                if (e.keyCode == KeyCode.Return || e.keyCode == KeyCode.KeypadEnter || e.keyCode == KeyCode.F2)
                {
                    Open("GUI:" + e.keyCode);
                    e.Use();
                }
                return;
            }

            // 聊天框打开时：自己收字符（不依赖 TextField 焦点 / IME）
            if (e.keyCode == KeyCode.Return || e.keyCode == KeyCode.KeypadEnter || e.character == '\n')
            {
                TrySubmit("GUI:Enter");
                e.Use();
                return;
            }
            if (e.keyCode == KeyCode.Escape) { TryClose("GUI:Esc"); e.Use(); return; }
            if (e.keyCode == KeyCode.F4) { TryClose("GUI:F4"); e.Use(); return; }
            if (e.keyCode == KeyCode.F2) { TrySubmit("GUI:F2"); e.Use(); return; }
            if (e.keyCode == KeyCode.Backspace)
            {
                if (!string.IsNullOrEmpty(draft)) draft = draft.Substring(0, draft.Length - 1);
                e.Use();
                return;
            }
            if (e.keyCode == KeyCode.Delete) { draft = ""; e.Use(); return; }
            if (textInputHooked) return;   // 字符由 Input System onTextInput 通道负责
            if ((imeCompositionState ?? "").Length > 0) return;  // 组合中的拼音不进草稿
            char ch = e.character;
            // CJK / 非 ASCII 字符已经由 Win32 IMM32 通道提交过，IMGUI 只收 ASCII，避免重复
            if (ch > 0x7F && Win32Ime.Available)
            {
                ChatDiag.Log("skip non-ascii char from IMGUI: '" + ch + "'");
                return;
            }
            if (ch != '\0' && !char.IsControl(ch) && (draft == null || draft.Length < MaxTextLength))
            {
                draft = (draft ?? "") + ch;
                e.Use();
                ChatDiag.Log("char added: '" + ch + "' len=" + draft.Length);
            }
        }

        // ---- 样式：全部自建（互操作程序集不支持 GUIStyle 拷贝构造）----

        static GUIStyle MakeStyle(bool wrap, TextAnchor anchor)
        {
            var style = new GUIStyle();
            style.font = chatFont;
            style.fontSize = fontSize;
            style.wordWrap = wrap;
            style.alignment = anchor;
            style.clipping = TextClipping.Clip;
            style.richText = false;
            try { style.normal.textColor = new Color(1f, 1f, 1f, 1f); } catch { }
            return style;
        }

        static GUIStyle wrapStyle;
        static GUIStyle titleStyle;
        static GUIStyle rightStyle;
        static GUIStyle fieldStyle;

        static GUIStyle WrapStyle()
        {
            EnsureReady();
            if (wrapStyle == null) wrapStyle = MakeStyle(true, TextAnchor.UpperLeft);
            wrapStyle.font = chatFont;
            wrapStyle.fontSize = fontSize;
            return wrapStyle;
        }

        static GUIStyle TitleStyle()
        {
            EnsureReady();
            if (titleStyle == null) titleStyle = MakeStyle(false, TextAnchor.MiddleLeft);
            titleStyle.font = chatFont;
            titleStyle.fontSize = fontSize;
            return titleStyle;
        }

        static GUIStyle RightStyle()
        {
            EnsureReady();
            if (rightStyle == null) rightStyle = MakeStyle(false, TextAnchor.MiddleRight);
            rightStyle.font = chatFont;
            rightStyle.fontSize = Mathf.Max(12, fontSize - 6);
            return rightStyle;
        }

        static GUIStyle FieldStyle()
        {
            EnsureReady();
            if (fieldStyle == null) fieldStyle = MakeStyle(false, TextAnchor.MiddleLeft);
            fieldStyle.font = chatFont;
            fieldStyle.fontSize = fontSize;
            return fieldStyle;
        }

        static void DrawPanel()
        {
            EnsureReady();
            float lineH = fontSize + 8f;
            float titleH = fontSize + 10f;
            float fieldH = fontSize + 14f;
            float hintH = fontSize + 4f;
            float width = Mathf.Clamp(Screen.width * 0.72f, 700f, 1500f);
            float avail = width - 24f;

            var replies = ChatBus.LastIncoming(ShownIncoming);
            var shown = new List<ChatMessage>(replies);
            var heights = new List<float>();
            float repliesH = 0f;
            while (shown.Count > 0)
            {
                heights.Clear();
                repliesH = 0f;
                for (int i = 0; i < shown.Count; i++)
                {
                    float h = TextHeight("管家：" + Trim(shown[i].text, 1200), avail);
                    heights.Add(h);
                    repliesH += h;
                }
                if (titleH + repliesH + fieldH + hintH * 2f + 26f <= Screen.height * 0.72f || shown.Count <= 1) break;
                shown.RemoveAt(0);
            }
            if (shown.Count == 0) repliesH = lineH;

            float height = titleH + repliesH + fieldH + hintH * 2f + 26f;
            float x = (Screen.width - width) * 0.5f;
            float bottom = Mathf.Clamp(Screen.height * 0.16f, 120f, 640f);
            float y = Screen.height - height - bottom;
            if (y < 16f) y = 16f;

            Color oldBg = GUI.backgroundColor;
            Color oldColor = GUI.color;
            try
            {
                GUI.backgroundColor = new Color(0.06f, 0.08f, 0.12f, 0.94f);
                GUI.Box(new Rect(x, y, width, height), GUIContent.none);
                GUI.backgroundColor = oldBg;

                GUI.color = new Color(0.55f, 0.82f, 1f);
                GUI.Label(new Rect(x + 12, y + 5, width * 0.56f, titleH), "管家聊天 · Enter 发送 · F4 关闭", TitleStyle());
                GUI.color = new Color(0.62f, 0.66f, 0.72f);
                GUI.Label(new Rect(x + width * 0.42f, y + 5, width * 0.58f - 24f, titleH), StatusText(), RightStyle());
                GUI.color = oldColor;

                float lineY = y + titleH;
                var style = WrapStyle();
                for (int i = 0; i < shown.Count; i++)
                {
                    GUI.Label(new Rect(x + 12, lineY, avail, heights[i]), "管家：" + Trim(shown[i].text, 1200), style);
                    lineY += heights[i];
                }
                if (shown.Count == 0)
                {
                    GUI.color = new Color(0.5f, 0.52f, 0.56f);
                    GUI.Label(new Rect(x + 12, lineY, avail, lineH), "（还没有回复：输入内容后按 Enter 发送）", style);
                    GUI.color = oldColor;
                }

                float fieldY = y + height - fieldH - hintH * 2f - 12f;
                float btnW = Mathf.Max(96f, fontSize * 5.4f);
                float fieldW = width - 24f - btnW * 2f - 16f;
                GUI.backgroundColor = new Color(0.10f, 0.13f, 0.18f, 1f);
                GUI.Box(new Rect(x + 12, fieldY, fieldW, fieldH), GUIContent.none);
                GUI.backgroundColor = oldBg;
                // 不用 GUI.TextField（IL2CPP + Input System 下焦点/IME 不可靠），
                // 字符由 HandleGuiEvent 自己收进 draft，这里只负责显示。
                string shownDraft = (draft ?? "");
                if (shownDraft.Length > 60) shownDraft = "…" + shownDraft.Substring(shownDraft.Length - 60);
                GUI.color = shownDraft.Length == 0 ? new Color(0.5f, 0.54f, 0.6f) : Color.white;
                GUI.Label(new Rect(x + 18, fieldY, fieldW - 12f, fieldH),
                    shownDraft.Length == 0 ? "在这里打字（Enter 发送，F4 关闭）" : shownDraft + "▌", FieldStyle());
                GUI.color = oldColor;
                if (GUI.Button(new Rect(x + width - 12f - btnW * 2f - 8f, fieldY, btnW, fieldH), "发送(Enter)"))
                {
                    TrySubmit("Button:Send");
                }
                if (GUI.Button(new Rect(x + width - 12f - btnW, fieldY, btnW - 4f, fieldH), "关闭(F4)"))
                {
                    TryClose("Button:Close");
                }

                GUI.color = new Color(0.55f, 0.6f, 0.66f);
                GUI.Label(new Rect(x + 12, fieldY + fieldH + 4f, width - 24, hintH),
                    "PageUp/PageDown 调字号（当前 " + fontSize + "） · 按 Esc 或 F4 关闭", WrapStyle());
                GUI.Label(new Rect(x + 12, fieldY + fieldH + 4f + hintH, width - 24, hintH),
                    "按键：Input " + InputKeyEvents + " / GUI " + GuiKeyEvents + " · 开 " + OpenCount + " · 发 " + SubmitCount + " · 关 " + CloseCount
                    + " · 最近 " + LastInputKey + "/" + LastGuiKey + " · 旧输入类 " + (legacyInputBroken ? "不可用" : "可用"),
                    WrapStyle());
                GUI.color = oldColor;
            }
            finally
            {
                GUI.backgroundColor = oldBg;
                GUI.color = oldColor;
            }
        }

        static void DrawToast()
        {
            var style = WrapStyle();
            float width = Mathf.Clamp(Screen.width * 0.78f, 620f, 1500f);
            float avail = width - 24f;
            string body = Trim(toast, 1200);
            float textH = TextHeight(body, avail);
            float h = Mathf.Min(textH + 16f, Screen.height * 0.45f);
            float x = (Screen.width - width) * 0.5f;
            float bottom = Mathf.Clamp(Screen.height * 0.16f, 120f, 640f);
            float y = Screen.height - bottom - h - 8f;
            if (y < 16f) y = 16f;
            Color oldBg = GUI.backgroundColor;
            Color oldColor = GUI.color;
            try
            {
                GUI.backgroundColor = new Color(0.06f, 0.08f, 0.12f, 0.92f);
                GUI.Box(new Rect(x, y, width, h), GUIContent.none);
                GUI.backgroundColor = oldBg;
                GUI.color = new Color(0.75f, 0.9f, 1f);
                GUI.Label(new Rect(x + 12, y + 8, avail, h - 10f), body, style);
                GUI.color = oldColor;
            }
            finally
            {
                GUI.backgroundColor = oldBg;
                GUI.color = oldColor;
            }
        }

        static string StatusText()
        {
            double age = ChatBus.SecondsSincePoll();
            if (age < 15) return "管家在线（" + (int)age + " 秒前检查）";
            if (ChatBus.PendingCount > 0) return "管家离线，消息排队中（" + ChatBus.PendingCount + " 条）";
            return "管家离线（消息会排队）";
        }

        // ---- 文本测量：不依赖 GUIContent/CalcHeight，按字符宽度估算（中文按 1em，ASCII 按 0.55em）----

        static float TextHeight(string text, float width)
        {
            int lines = EstimateLines(text, width);
            return Mathf.Max(fontSize + 8f, lines * (fontSize + 4f));
        }

        static int EstimateLines(string text, float width)
        {
            if (string.IsNullOrEmpty(text)) return 1;
            float perChar = Mathf.Max(8f, fontSize * 0.98f);
            int total = 0;
            foreach (string raw in text.Split('\n'))
            {
                float used = 0f;
                int lines = 1;
                foreach (char c in raw)
                {
                    float w = c > 0x2E7F ? perChar : perChar * 0.55f;
                    if (used + w > width)
                    {
                        lines++;
                        used = 0f;
                    }
                    used += w;
                }
                total += lines;
            }
            return Mathf.Max(1, total);
        }

        static Font BuildFont()
        {
            foreach (string name in new[] { "Microsoft YaHei", "微软雅黑", "SimHei", "黑体", "SimSun", "宋体", "Arial" })
            {
                try
                {
                    var f = Font.CreateDynamicFontFromOSFont(name, fontSize);
                    if (f != null) return f;
                }
                catch
                {
                }
            }
            return null;
        }

        static string Trim(string text, int max)
        {
            if (text == null) return "";
            string flat = text.Replace("\r", " ").Replace("\n", " ");
            return flat.Length <= max ? flat : flat.Substring(0, max) + "…";
        }
    }
}







