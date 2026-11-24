using System;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows.Forms;

internal static class CaptureScreen
{
    [DllImport("user32.dll")]
    private static extern bool SetProcessDpiAwarenessContext(IntPtr context);
    [DllImport("user32.dll")]
    private static extern bool SetProcessDPIAware();

    [STAThread]
    private static int Main(string[] args)
    {
        Console.OutputEncoding = new UTF8Encoding(false);
        if (args.Length != 0) return 2;
        try
        {
            try
            {
                if (!SetProcessDpiAwarenessContext(new IntPtr(-4))) SetProcessDPIAware();
            }
            catch (EntryPointNotFoundException) { SetProcessDPIAware(); }

            string folder = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "captures");
            Directory.CreateDirectory(folder);
            Rectangle bounds = Screen.PrimaryScreen.Bounds;
            string capturedAt = DateTimeOffset.Now.ToString("o");
            string output = Path.Combine(folder, "screen_" + DateTime.Now.ToString("yyyyMMdd_HHmmss_fff") + "_" + Guid.NewGuid().ToString("N").Substring(0, 8) + ".png");
            using (Bitmap bitmap = new Bitmap(bounds.Width, bounds.Height))
            using (Graphics graphics = Graphics.FromImage(bitmap))
            {
                graphics.CopyFromScreen(bounds.Location, Point.Empty, bounds.Size);
                bitmap.Save(output, ImageFormat.Png);
            }
            Console.WriteLine(new JavaScriptSerializer().Serialize(new {
                ok = true, path = output, width = bounds.Width, height = bounds.Height,
                captured_at = capturedAt, monitor = "primary", mime_type = "image/png"
            }));
            return 0;
        }
        catch
        {
            Console.WriteLine("{\"ok\":false,\"message\":\"Screen capture failed. Keep the Windows user session unlocked and active.\"}");
            return 1;
        }
    }
}
