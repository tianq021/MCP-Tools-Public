using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows.Forms;

internal static class ControlTestWindow
{
    [DllImport("user32.dll")] static extern bool SetProcessDpiAwarenessContext(IntPtr context);
    [STAThread]
    static void Main()
    {
        try { SetProcessDpiAwarenessContext(new IntPtr(-4)); } catch (EntryPointNotFoundException) {}
        Application.EnableVisualStyles();
        Form form = new Form { Text = "DSH / Codex 鼠标键盘验证窗口", StartPosition = FormStartPosition.Manual, Location = new Point(720, 340), Size = new Size(720, 460) };
        Label heading = new Label { Text = "仅用于验证鼠标点击、中文输入和快捷键", Location = new Point(20, 20), Size = new Size(650, 30), Font = new Font("Microsoft YaHei", 13) };
        TextBox text = new TextBox { Name = "InputText", AccessibleName = "输入测试框", Multiline = false, Location = new Point(20, 65), Size = new Size(660, 50), Font = new Font("Microsoft YaHei", 14) };
        Button button = new Button { Name = "ClickTest", Text = "点击测试", Location = new Point(20, 320), Size = new Size(170, 50) };
        Label status = new Label { Text = "点击次数：0", Location = new Point(220, 335), Size = new Size(200, 30) };
        int clicks = 0;
        button.Click += delegate { clicks++; status.Text = "点击次数：" + clicks; };
        form.Controls.AddRange(new Control[] { heading, text, button, status });
        string output = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "test-window-state.json");
        JavaScriptSerializer json = new JavaScriptSerializer();
        Stopwatch age = Stopwatch.StartNew();
        Timer timer = new Timer { Interval = 100 };
        timer.Tick += delegate {
            Point t = text.PointToScreen(new Point(text.Width / 2, text.Height / 2));
            Point b = button.PointToScreen(new Point(button.Width / 2, button.Height / 2));
            Rectangle screen = Screen.PrimaryScreen.Bounds;
            File.WriteAllText(output, json.Serialize(new {
                hwnd = form.Handle.ToInt64(), pid = Process.GetCurrentProcess().Id, text = text.Text, clicks = clicks,
                textbox = new { x = (int)Math.Round((t.X - screen.X) * 1000.0 / (screen.Width - 1)), y = (int)Math.Round((t.Y - screen.Y) * 1000.0 / (screen.Height - 1)) },
                button = new { x = (int)Math.Round((b.X - screen.X) * 1000.0 / (screen.Width - 1)), y = (int)Math.Round((b.Y - screen.Y) * 1000.0 / (screen.Height - 1)) }
            }), new UTF8Encoding(false));
            if (age.Elapsed.TotalMinutes > 3) form.Close();
        };
        form.Shown += delegate { text.Focus(); timer.Start(); };
        Application.Run(form);
        timer.Dispose();
    }
}
