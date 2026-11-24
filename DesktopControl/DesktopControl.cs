using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Imaging;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using System.Windows.Automation;

internal static class DesktopControl
{
    [StructLayout(LayoutKind.Sequential)] struct POINT { public int X, Y; }
    [StructLayout(LayoutKind.Sequential)] struct RECT { public int Left, Top, Right, Bottom; }
    [StructLayout(LayoutKind.Sequential)] struct MOUSEINPUT { public int dx, dy; public uint mouseData, dwFlags, time; public UIntPtr extra; }
    [StructLayout(LayoutKind.Sequential)] struct KEYBDINPUT { public ushort vk, scan; public uint flags, time; public UIntPtr extra; }
    [StructLayout(LayoutKind.Explicit)] struct UNION { [FieldOffset(0)] public MOUSEINPUT mouse; [FieldOffset(0)] public KEYBDINPUT key; }
    [StructLayout(LayoutKind.Sequential)] struct INPUT { public uint type; public UNION data; }
    delegate bool EnumCallback(IntPtr hwnd, IntPtr param);
    [DllImport("user32.dll")] static extern bool SetProcessDpiAwarenessContext(IntPtr context);
    [DllImport("user32.dll")] static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr hwnd, out RECT rect);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetWindowText(IntPtr hwnd, StringBuilder title, int max);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
    [DllImport("user32.dll")] static extern bool EnumWindows(EnumCallback callback, IntPtr param);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr hwnd);
    [DllImport("user32.dll")] static extern bool IsWindow(IntPtr hwnd);
    [DllImport("user32.dll")] static extern bool IsIconic(IntPtr hwnd);
    [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr hwnd, int command);
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr hwnd);
    [DllImport("user32.dll")] static extern bool GetCursorPos(out POINT point);
    [DllImport("user32.dll")] static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] static extern short GetAsyncKeyState(int key);
    [DllImport("user32.dll", SetLastError = true)] static extern uint SendInput(uint count, INPUT[] inputs, int size);

    static readonly JavaScriptSerializer Json = new JavaScriptSerializer();
    static readonly string Root = AppDomain.CurrentDomain.BaseDirectory;
    static readonly string DataRoot = @"G:\Codex_mmx\desktop-control-data";
    static readonly string StatePath = Path.Combine(DataRoot, "observation.json");
    static readonly string StopPath = Path.Combine(DataRoot, "paused.flag");
    static readonly string CaptureDirectory = Path.Combine(DataRoot, "captures");
    static bool Applied;

    sealed class Observation
    {
        public string id { get; set; }
        public string created_utc { get; set; }
        public long hwnd { get; set; }
        public uint pid { get; set; }
        public int[] window_rect { get; set; }
        public int[] screen_rect { get; set; }
    }
    sealed class ControlError : Exception { public ControlError(string message) : base(message) {} }
    static void Fail(string message) { throw new ControlError(message); }
    static string Text(Dictionary<string, object> req, string key, string fallback = "")
    {
        object value;
        if (!req.TryGetValue(key, out value)) return fallback;
        if (!(value is string)) Fail("参数必须是字符串：" + key);
        return (string)value;
    }
    static int Number(Dictionary<string, object> req, string key, int min, int max, int fallback)
    {
        object value;
        if (!req.TryGetValue(key, out value)) return fallback;
        if (!(value is int) && !(value is long)) Fail("参数必须是整数：" + key);
        long parsed = Convert.ToInt64(value);
        if (parsed < min || parsed > max) Fail("参数超出范围：" + key);
        return (int)parsed;
    }
    static bool Flag(Dictionary<string, object> req, string key, bool fallback)
    {
        object value;
        if (!req.TryGetValue(key, out value)) return fallback;
        if (!(value is bool)) Fail("参数必须是布尔值：" + key);
        return (bool)value;
    }
    static string Title(IntPtr hwnd)
    {
        StringBuilder title = new StringBuilder(1024);
        GetWindowText(hwnd, title, title.Capacity);
        return title.ToString();
    }
    static int[] WindowRect(IntPtr hwnd)
    {
        RECT rect;
        if (!GetWindowRect(hwnd, out rect)) Fail("无法读取前台窗口位置，请重新观察。");
        return new int[] { rect.Left, rect.Top, rect.Right, rect.Bottom };
    }
    static bool Same(int[] a, int[] b)
    {
        if (a == null || b == null || a.Length != b.Length) return false;
        for (int i = 0; i < a.Length; i++) if (a[i] != b[i]) return false;
        return true;
    }
    static int[] ScreenRect()
    {
        Rectangle b = Screen.PrimaryScreen.Bounds;
        return new int[] { b.X, b.Y, b.Width, b.Height };
    }
    static void AbortCheck(bool checkPointer)
    {
        if (File.Exists(StopPath)) Fail("桌面控制已暂停；只有用户要求恢复后才能继续。");
        if ((GetAsyncKeyState(0x1B) & 0x8000) != 0)
        {
            File.WriteAllText(StopPath, "escape");
            Fail("检测到 Esc，桌面控制已暂停。");
        }
        if (checkPointer)
        {
            POINT p; GetCursorPos(out p);
            Rectangle b = Screen.PrimaryScreen.Bounds;
            if (p.X <= b.Left + 1 && p.Y <= b.Top + 1 && p.X >= b.Left && p.Y >= b.Top)
            {
                File.WriteAllText(StopPath, "pointer-failsafe");
                Fail("鼠标位于主屏左上角，已暂停控制。");
            }
        }
    }
    static Observation Verify(Dictionary<string, object> req)
    {
        AbortCheck(true);
        if (!File.Exists(StatePath)) Fail("请先调用 observe 获取新的 observation_id。");
        Observation saved = Json.Deserialize<Observation>(File.ReadAllText(StatePath));
        if (saved.id != Text(req, "observation_id")) Fail("观察已被其他操作更新，请重新观察。");
        DateTime created;
        if (!DateTime.TryParse(saved.created_utc, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out created) || (DateTime.UtcNow - created.ToUniversalTime()).TotalSeconds > 120)
            Fail("观察已超过 120 秒，请重新观察。");
        IntPtr hwnd = GetForegroundWindow();
        uint pid; GetWindowThreadProcessId(hwnd, out pid);
        if (hwnd.ToInt64() != saved.hwnd || pid != saved.pid || !Same(WindowRect(hwnd), saved.window_rect) || !Same(ScreenRect(), saved.screen_rect))
            Fail("前台窗口、位置或显示器已变化，请重新观察。");
        foreach (int vk in new int[] { 0x10, 0x11, 0x12, 0x5B, 0x5C, 1, 2, 4 })
            if ((GetAsyncKeyState(vk) & 0x8000) != 0) Fail("检测到用户正在按键或按住鼠标，请松开后重新观察。");
        // Consume the observation before input: a partially completed action cannot be replayed.
        saved.id = "consumed-" + Guid.NewGuid().ToString("N");
        File.WriteAllText(StatePath, Json.Serialize(saved), new UTF8Encoding(false));
        return saved;
    }
    static Dictionary<string, object> Observe(string action, bool includeImage = true)
    {
        IntPtr before = GetForegroundWindow();
        int[] beforeRect = WindowRect(before);
        Dictionary<string, object> capture = null;
        if (includeImage)
        {
            Directory.CreateDirectory(CaptureDirectory);
            Rectangle bounds = Screen.PrimaryScreen.Bounds;
            string output = Path.Combine(CaptureDirectory, "control_" + DateTime.Now.ToString("yyyyMMdd_HHmmss_fff") + "_" + Guid.NewGuid().ToString("N").Substring(0, 8) + ".png");
            using (Bitmap bitmap = new Bitmap(bounds.Width, bounds.Height))
            using (Graphics graphics = Graphics.FromImage(bitmap))
            {
                graphics.CopyFromScreen(bounds.Location, Point.Empty, bounds.Size);
                using (MemoryStream encoded = new MemoryStream())
                {
                    bitmap.Save(encoded, ImageFormat.Png);
                    File.WriteAllBytes(output, encoded.ToArray());
                }
            }
            capture = new Dictionary<string, object> { { "path", output }, { "width", bounds.Width }, { "height", bounds.Height }, { "mime_type", "image/png" } };
        }
        IntPtr after = GetForegroundWindow();
        if (before != after || !Same(beforeRect, WindowRect(after))) Fail("截图期间窗口发生变化，请重新观察。");
        uint pid; GetWindowThreadProcessId(after, out pid);
        Observation state = new Observation { id = Guid.NewGuid().ToString("N"), created_utc = DateTime.UtcNow.ToString("o"), hwnd = after.ToInt64(), pid = pid, window_rect = beforeRect, screen_rect = ScreenRect() };
        File.WriteAllText(StatePath, Json.Serialize(state), new UTF8Encoding(false));
        POINT cursor; GetCursorPos(out cursor);
        return new Dictionary<string, object> {
            { "ok", true }, { "action", action }, { "applied", Applied }, { "observation_id", state.id },
            { "foreground", new { hwnd = state.hwnd, pid = state.pid, title = Title(after), rect = state.window_rect } },
            { "image", capture }, { "coordinates", new { min = 0, max = 1000, screen = "primary", width_px = state.screen_rect[2], height_px = state.screen_rect[3] } },
            { "cursor", new { x_px = cursor.X, y_px = cursor.Y } }, { "paused", File.Exists(StopPath) }, { "screenshot_taken", includeImage }
        };
    }
    static string Bounded(string value) { return value == null ? "" : value.Substring(0, Math.Min(value.Length, 160)); }
    static string RuntimeId(AutomationElement element) { return String.Join(".", element.GetRuntimeId()); }
    static List<AutomationElement> Elements(AutomationElement root)
    {
        List<AutomationElement> result = new List<AutomationElement>();
        Queue<AutomationElement> queue = new Queue<AutomationElement>(); queue.Enqueue(root);
        TreeWalker walker = TreeWalker.ControlViewWalker;
        while (queue.Count > 0 && result.Count < 300)
        {
            AutomationElement element = queue.Dequeue(); result.Add(element);
            AutomationElement child = walker.GetFirstChild(element);
            while (child != null && queue.Count + result.Count < 300) { queue.Enqueue(child); child = walker.GetNextSibling(child); }
        }
        return result;
    }
    static Dictionary<string, object> InspectControls(Dictionary<string, object> req)
    {
        Stopwatch timer = Stopwatch.StartNew();
        Dictionary<string, object> state = Observe("inspect_controls", Flag(req, "include_screenshot", false));
        List<object> controls = new List<object>();
        int limit = Number(req, "max_controls", 1, 100, 60);
        string query = Text(req, "query").ToLowerInvariant();
        AutomationElement root = AutomationElement.FromHandle(GetForegroundWindow());
        List<AutomationElement> elements = Elements(root);
        foreach (AutomationElement element in elements)
        {
            try
            {
                AutomationElement.AutomationElementInformation current = element.Current;
                string name = current.Name ?? "", automationId = current.AutomationId ?? "";
                if (query.Length > 0 && (name + " " + automationId + " " + current.ControlType.ProgrammaticName).ToLowerInvariant().IndexOf(query, StringComparison.Ordinal) < 0) continue;
                List<string> patterns = new List<string>();
                foreach (AutomationPattern pattern in element.GetSupportedPatterns()) patterns.Add(pattern.ProgrammaticName.Replace("PatternIdentifiers.Pattern", ""));
                string value = null;
                object valuePattern;
                if (!current.IsPassword && element.TryGetCurrentPattern(ValuePattern.Pattern, out valuePattern)) value = Bounded(((ValuePattern)valuePattern).Current.Value);
                controls.Add(new { control_id = RuntimeId(element), name = Bounded(name), automation_id = automationId, type = current.ControlType.ProgrammaticName.Replace("ControlType.", ""), enabled = current.IsEnabled, offscreen = current.IsOffscreen, password = current.IsPassword, value = value, patterns = patterns });
                if (controls.Count >= limit) break;
            }
            catch (ElementNotAvailableException) { }
        }
        state["controls"] = controls; state["uia_nodes_scanned"] = elements.Count; state["controls_limit"] = limit;
        state["native_elapsed_ms"] = timer.ElapsedMilliseconds;
        return state;
    }
    static object ControlAction(Dictionary<string, object> req)
    {
        Stopwatch timer = Stopwatch.StartNew();
        Verify(req);
        string id = Text(req, "control_id"), operation = Text(req, "operation");
        AutomationElement target = null;
        foreach (AutomationElement element in Elements(AutomationElement.FromHandle(GetForegroundWindow())))
        {
            try { if (RuntimeId(element) == id) { target = element; break; } } catch (ElementNotAvailableException) { }
        }
        if (target == null) Fail("控件已不存在，请重新读取控件。");
        if (!target.Current.IsEnabled) Fail("控件当前不可用。");
        object pattern;
        bool verified = false;
        if (operation == "set_value")
        {
            string value = Text(req, "text");
            if (value.Length > 2000) Fail("每次最多设置 2000 个字符。");
            if (!target.TryGetCurrentPattern(ValuePattern.Pattern, out pattern) || ((ValuePattern)pattern).Current.IsReadOnly) Fail("该控件不支持直接设置值，可改用已授权的键盘输入。");
            Applied = true; ((ValuePattern)pattern).SetValue(value);
            verified = ((ValuePattern)pattern).Current.Value == value;
        }
        else if (operation == "invoke")
        {
            if (!target.TryGetCurrentPattern(InvokePattern.Pattern, out pattern)) Fail("该控件不支持直接调用。");
            Applied = true; ((InvokePattern)pattern).Invoke();
        }
        else if (operation == "select")
        {
            if (!target.TryGetCurrentPattern(SelectionItemPattern.Pattern, out pattern)) Fail("该控件不支持选择。");
            Applied = true; ((SelectionItemPattern)pattern).Select(); verified = ((SelectionItemPattern)pattern).Current.IsSelected;
        }
        else if (operation == "focus") { Applied = true; target.SetFocus(); verified = target.Current.HasKeyboardFocus; }
        else Fail("支持的控件操作为 set_value、invoke、select、focus。");
        Dictionary<string, object> state = InspectControls(req);
        state["action"] = "control_action"; state["operation"] = operation; state["verified"] = verified;
        state["native_elapsed_ms"] = timer.ElapsedMilliseconds;
        return state;
    }
    static INPUT Key(ushort vk, ushort scan, uint flags)
    {
        INPUT input = new INPUT(); input.type = 1; input.data.key = new KEYBDINPUT { vk = vk, scan = scan, flags = flags }; return input;
    }
    static INPUT Mouse(uint flags, uint data)
    {
        INPUT input = new INPUT(); input.type = 0; input.data.mouse = new MOUSEINPUT { dwFlags = flags, mouseData = data }; return input;
    }
    static void Inject(INPUT[] inputs)
    {
        Applied = true;
        if (SendInput((uint)inputs.Length, inputs, Marshal.SizeOf(typeof(INPUT))) != inputs.Length)
            Fail("Windows 未接受全部输入，操作可能已部分执行；不要自动重试。目标程序权限可能高于工具。");
    }
    static void Move(Dictionary<string, object> req)
    {
        int x = Number(req, "x", 0, 1000, -1), y = Number(req, "y", 0, 1000, -1);
        if (x < 0 || y < 0) Fail("点击或移动需要 0 到 1000 的 x、y 坐标。");
        Rectangle b = Screen.PrimaryScreen.Bounds;
        int px = b.X + (int)Math.Round(x * (b.Width - 1) / 1000.0);
        int py = b.Y + (int)Math.Round(y * (b.Height - 1) / 1000.0);
        Applied = true;
        if (!SetCursorPos(px, py)) Fail("Windows 拒绝移动鼠标。");
    }
    static ushort VirtualKey(string name)
    {
        name = name.ToLowerInvariant();
        if (name.Length == 1 && (name[0] >= 'a' && name[0] <= 'z' || name[0] >= '0' && name[0] <= '9')) return (ushort)Char.ToUpperInvariant(name[0]);
        int f; if (name.StartsWith("f") && Int32.TryParse(name.Substring(1), out f) && f >= 1 && f <= 12) return (ushort)(0x70 + f - 1);
        switch (name) {
            case "ctrl": return 0x11; case "shift": return 0x10; case "alt": return 0x12; case "win": return 0x5B;
            case "enter": return 0x0D; case "tab": return 0x09; case "escape": case "esc": return 0x1B;
            case "space": return 0x20; case "backspace": return 0x08; case "delete": return 0x2E;
            case "left": return 0x25; case "up": return 0x26; case "right": return 0x27; case "down": return 0x28;
            case "home": return 0x24; case "end": return 0x23; case "pageup": return 0x21; case "pagedown": return 0x22;
            default: Fail("不支持的按键名称。"); return 0;
        }
    }
    static uint KeyFlags(ushort vk) { return vk >= 0x21 && vk <= 0x2E || vk == 0x5B ? 1u : 0u; }
    static void Hotkey(string value)
    {
        string[] names = value.Split('+');
        if (names.Length < 1 || names.Length > 4) Fail("快捷键最多包含 4 个键。");
        List<ushort> keys = new List<ushort>();
        foreach (string name in names) { ushort vk = VirtualKey(name.Trim()); if (keys.Contains(vk)) Fail("快捷键包含重复按键。"); keys.Add(vk); }
        List<INPUT> down = new List<INPUT>(), up = new List<INPUT>();
        foreach (ushort vk in keys) down.Add(Key(vk, 0, KeyFlags(vk)));
        for (int i = keys.Count - 1; i >= 0; i--) up.Add(Key(keys[i], 0, KeyFlags(keys[i]) | 2));
        try { Inject(down.ToArray()); Thread.Sleep(40); }
        finally { if (Applied) SendInput((uint)up.Count, up.ToArray(), Marshal.SizeOf(typeof(INPUT))); }
    }
    static void TypeText(string text, long hwnd)
    {
        if (text.Length == 0 || text.Length > 2000) Fail("每次输入 1 到 2000 个字符。");
        text = text.Replace("\r\n", "\n").Replace("\r", "\n");
        foreach (char c in text)
        {
            AbortCheck(false);
            if (GetForegroundWindow().ToInt64() != hwnd) Fail("输入期间前台窗口变化，已停止；不要自动重试整段文字。");
            INPUT[] pair = c == '\n' ? new INPUT[] { Key(0x0D, 0, 0), Key(0x0D, 0, 2) } : new INPUT[] { Key(0, c, 4), Key(0, c, 6) };
            Inject(pair);
        }
    }
    static object Execute(Dictionary<string, object> req)
    {
        string action = Text(req, "action");
        if (action == "pause") { File.WriteAllText(StopPath, "user-request"); return new { ok = true, paused = true, applied = false }; }
        if (action == "resume") { if (File.Exists(StopPath)) File.Delete(StopPath); return Observe(action); }
        if (action == "observe") return Observe(action, Flag(req, "include_screenshot", true));
        if (action == "inspect_controls") return InspectControls(req);
        if (action == "control_action") return ControlAction(req);
        if (action == "list_windows")
        {
            List<object> windows = new List<object>();
            EnumWindows(delegate(IntPtr hwnd, IntPtr ignored) {
                string title = Title(hwnd);
                if (IsWindowVisible(hwnd) && title.Length > 0) windows.Add(new { hwnd = hwnd.ToInt64(), title = title, foreground = hwnd == GetForegroundWindow(), rect = WindowRect(hwnd) });
                return true;
            }, IntPtr.Zero);
            return new { ok = true, windows = windows, paused = File.Exists(StopPath) };
        }
        if (action != "move" && action != "click" && action != "scroll" && action != "type_text" && action != "hotkey" && action != "focus_window") Fail("不支持的桌面操作。");
        Observation saved = Verify(req);
        if (action == "move") Move(req);
        else if (action == "click")
        {
            string button = Text(req, "button", "left");
            if (button != "left" && button != "right" && button != "middle") Fail("鼠标按钮必须是 left、right 或 middle。");
            int count = Number(req, "clicks", 1, 2, 1);
            Move(req); AbortCheck(false);
            uint down = button == "left" ? 2u : button == "right" ? 8u : 32u;
            uint up = down * 2;
            List<INPUT> events = new List<INPUT>();
            for (int i = 0; i < count; i++) { events.Add(Mouse(down, 0)); events.Add(Mouse(up, 0)); }
            try { Inject(events.ToArray()); } finally { SendInput(1, new INPUT[] { Mouse(up, 0) }, Marshal.SizeOf(typeof(INPUT))); }
        }
        else if (action == "scroll")
        {
            int amount = Number(req, "amount", -10, 10, 0);
            if (amount == 0) Fail("滚动量不能为零。");
            Move(req); Inject(new INPUT[] { Mouse(0x0800, unchecked((uint)(amount * 120))) });
        }
        else if (action == "type_text") TypeText(Text(req, "text"), saved.hwnd);
        else if (action == "hotkey") Hotkey(Text(req, "keys"));
        else if (action == "focus_window")
        {
            long target;
            if (!Int64.TryParse(Text(req, "hwnd"), out target)) Fail("窗口句柄格式错误。");
            IntPtr hwnd = new IntPtr(target);
            if (!IsWindow(hwnd) || !IsWindowVisible(hwnd)) Fail("目标窗口已不存在或不可见，请重新列出窗口。");
            Applied = true;
            if (IsIconic(hwnd)) ShowWindow(hwnd, 9);
            if (!SetForegroundWindow(hwnd)) Fail("Windows 拒绝切换前台窗口，请手动选择目标窗口后重新观察。");
        }
        bool screenshot = Flag(req, "include_screenshot", false);
        if (screenshot) Thread.Sleep(120);
        return Observe(action, screenshot);
    }
    [MTAThread]
    static int Main()
    {
        Console.InputEncoding = new UTF8Encoding(false); Console.OutputEncoding = new UTF8Encoding(false);
        try { if (!SetProcessDpiAwarenessContext(new IntPtr(-4))) SetProcessDPIAware(); }
        catch (EntryPointNotFoundException) { SetProcessDPIAware(); }
        bool held = false;
        // Pause must reach a local game session even while it owns the input mutex.
        string raw = Console.In.ReadToEnd();
        if (raw.Length > 65536) { Console.WriteLine("{\"ok\":false,\"applied\":false,\"message\":\"请求过长。\"}"); return 1; }
        Dictionary<string, object> request;
        try { request = Json.Deserialize<Dictionary<string, object>>(raw); }
        catch { Console.WriteLine("{\"ok\":false,\"applied\":false,\"message\":\"请求格式错误。\"}"); return 1; }
        object requestedAction;
        if (request != null && request.TryGetValue("action", out requestedAction) && requestedAction is string && (string)requestedAction == "pause")
        {
            Directory.CreateDirectory(DataRoot);
            Console.WriteLine(Json.Serialize(Execute(request)));
            return 0;
        }
        using (Mutex mutex = new Mutex(false, @"Local\CodexDshDesktopInput"))
        {
            try
            {
                try { held = mutex.WaitOne(0); } catch (AbandonedMutexException) { held = true; }
                if (!held) Fail("另一个智能体正在控制桌面，请稍后重新观察。");
                Directory.CreateDirectory(DataRoot);
                Console.WriteLine(Json.Serialize(Execute(request)));
                return 0;
            }
            catch (Exception error)
            {
                if (Environment.GetEnvironmentVariable("DESKTOP_CONTROL_DIAGNOSTICS") == "1") Console.Error.WriteLine(error.GetType().FullName + ": " + error.Message);
                string message = error is ControlError ? error.Message : "桌面工具执行失败；如已发生输入，请先重新观察，不要重复执行。";
                Console.WriteLine(Json.Serialize(new { ok = false, applied = Applied, message = message }));
                return 1;
            }
            finally { if (held) mutex.ReleaseMutex(); }
        }
    }
}
