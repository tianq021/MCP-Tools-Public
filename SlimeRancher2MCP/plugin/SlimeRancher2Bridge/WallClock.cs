using System;
using System.Collections.Generic;
using System.Globalization;
using System.Reflection;

namespace SlimeRancher2Bridge
{
    internal static class WallClock
    {
        static readonly TimeSpan Offset = TimeSpan.FromHours(8);
        static MethodInfo box;
        static MethodInfo label;
        static readonly object[] background = new object[2];
        static readonly object[] caption = new object[3];
        static long lastCentisecond = -1;

        internal static string DisplayText(DateTimeOffset instant) =>
            instant.ToOffset(Offset).ToString("MM'/'dd'/' HH:mm:ss.ff", CultureInfo.InvariantCulture);

        internal static Dictionary<string, object> Read(Dictionary<string, object> args)
        {
            var now = DateTimeOffset.UtcNow.ToOffset(Offset);
            return new() { ["time"] = now.ToString("yyyy-MM-ddTHH:mm:sszzz", CultureInfo.InvariantCulture),
                ["timezone"] = "Asia/Shanghai", ["unix_seconds"] = now.ToUnixTimeSeconds() };
        }

        internal static void Draw()
        {
            var now = DateTimeOffset.UtcNow;
            long centisecond = now.ToUnixTimeMilliseconds() / 10;
            if (centisecond != lastCentisecond)
            {
                lastCentisecond = centisecond;
                if (label == null)
                {
                    var rect = Type.GetType("UnityEngine.Rect, UnityEngine.CoreModule");
                    var gui = Type.GetType("UnityEngine.GUI, UnityEngine.IMGUIModule");
                    var styleType = Type.GetType("UnityEngine.GUIStyle, UnityEngine.IMGUIModule");
                    if (rect == null || gui == null || styleType == null) return;
                    var style = Activator.CreateInstance(styleType);
                    styleType.GetProperty("fontSize").SetValue(style, 28);
                    var alignment = styleType.GetProperty("alignment");
                    alignment.SetValue(style, Enum.Parse(alignment.PropertyType, "MiddleCenter"));
                    var normal = styleType.GetProperty("normal").GetValue(style);
                    var color = normal.GetType().GetProperty("textColor");
                    color.SetValue(normal, color.PropertyType.GetProperty("white").GetValue(null));
                    caption[0] = background[0] = Activator.CreateInstance(rect, 12f, 48f, 320f, 46f);
                    background[1] = string.Empty;
                    caption[2] = style;
                    box = gui.GetMethod("Box", new[] { rect, typeof(string) });
                    label = gui.GetMethod("Label", new[] { rect, typeof(string), styleType });
                }
                caption[1] = DisplayText(now);
            }
            box?.Invoke(null, background);
            label?.Invoke(null, caption);
        }
    }
}
