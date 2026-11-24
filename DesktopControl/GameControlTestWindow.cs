using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows.Forms;

internal sealed class GameControlTestWindow : Form
{
    [DllImport("user32.dll")] static extern bool SetProcessDpiAwarenessContext(IntPtr context);
    [DllImport("user32.dll")] static extern short GetAsyncKeyState(int key);
    readonly Stopwatch age = Stopwatch.StartNew();
    readonly HashSet<int> keys = new HashSet<int>();
    readonly string statePath = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "game-test-state.json");
    readonly JavaScriptSerializer json = new JavaScriptSerializer();
    readonly Timer timer = new Timer { Interval = 16 };
    readonly Rectangle cue = new Rectangle(360, 150, 140, 80);
    int keyDowns, keyUps, clicks, moves, wheel, drags, holds;
    int x = 70;
    bool dragging;

    GameControlTestWindow()
    {
        Text = "DSH / Codex 通用游戏控制验证";
        StartPosition = FormStartPosition.Manual;
        Location = new Point(600, 260);
        ClientSize = new Size(800, 480);
        KeyPreview = true; DoubleBuffered = true;
        KeyDown += delegate(object sender, KeyEventArgs e) { keys.Add((int)e.KeyCode); keyDowns++; };
        KeyUp += delegate(object sender, KeyEventArgs e) { keys.Remove((int)e.KeyCode); keyUps++; };
        MouseMove += delegate { moves++; };
        MouseWheel += delegate(object sender, MouseEventArgs e) { wheel += e.Delta; };
        MouseDown += delegate(object sender, MouseEventArgs e) { dragging = true; };
        MouseUp += delegate(object sender, MouseEventArgs e) {
            dragging = false; drags++;
            if (cue.Contains(e.Location)) clicks++;
        };
        timer.Tick += delegate {
            if ((GetAsyncKeyState(0x57) & 0x8000) != 0) { x += 2; holds++; }
            if (x > 740) x = 70;
            Point pointer = PointToClient(Cursor.Position);
            File.WriteAllText(statePath, json.Serialize(new {
                pid = Process.GetCurrentProcess().Id, hwnd = Handle.ToInt64(),
                keyDowns, keyUps, clicks, moves, wheel, drags, holds, x,
                keys = new List<int>(keys), dragging,
                w_pressed = (GetAsyncKeyState(0x57) & 0x8000) != 0,
                shift_pressed = (GetAsyncKeyState(0x10) & 0x8000) != 0,
                left_pressed = (GetAsyncKeyState(1) & 0x8000) != 0,
                pointer = new { x = pointer.X, y = pointer.Y },
                age_s = age.Elapsed.TotalSeconds
            }), new UTF8Encoding(false));
            Invalidate();
            if (File.Exists(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "game-test-minimize.flag")))
                WindowState = FormWindowState.Minimized;
            if (age.Elapsed.TotalMinutes > 3) Close();
        };
        Shown += delegate { Activate(); Focus(); timer.Start(); };
    }

    protected override void OnPaint(PaintEventArgs e)
    {
        base.OnPaint(e);
        Graphics g = e.Graphics;
        g.Clear(Color.FromArgb(24, 30, 45));
        using (Font font = new Font("Microsoft YaHei", 16))
        {
            g.DrawString("键鼠与视觉反馈验证窗口", font, Brushes.White, 24, 24);
            g.FillRectangle(Brushes.SteelBlue, cue);
            g.DrawRectangle(Pens.White, cue);
            g.DrawLine(Pens.Gold, 365, 154, 495, 225);
            g.DrawString("GO 目标", font, Brushes.White, 374, 174);
            g.DrawString("点击次数: " + clicks + "    持续按 W: " + holds, font, Brushes.White, 24, 290);
            g.FillRectangle(Brushes.Lime, x, 360, 28, 28);
            g.DrawString("输入在此窗口内验证，结束后自动关闭", font, Brushes.LightGray, 24, 420);
            g.DrawEllipse(Pens.Silver, 700 + (int)(age.ElapsedMilliseconds % 300) / 20, 75, 12, 12);
        }
    }

    [STAThread] static void Main()
    {
        try { SetProcessDpiAwarenessContext(new IntPtr(-4)); } catch (EntryPointNotFoundException) {}
        Application.EnableVisualStyles();
        Application.Run(new GameControlTestWindow());
    }
}
