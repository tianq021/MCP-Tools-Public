using System;
using System.Globalization;
using UnityEngine;

namespace SlimeRancherBridge
{
    public sealed class SystemClockOverlay : MonoBehaviour
    {
        readonly GUIContent clockText = new GUIContent();
        long displayedTenth = -1;
        GUIStyle textStyle;
        GUIStyle shadowStyle;

        void OnGUI()
        {
            if (Event.current.type != EventType.Repaint)
            {
                return;
            }

            HudUI hud = HudUI.Instance;
            if (hud == null || !hud.isActiveAndEnabled || hud.uiContainer == null || !hud.uiContainer.activeInHierarchy)
            {
                return;
            }

            DateTime localTime = DateTime.Now;
            long tenth = localTime.Ticks / (TimeSpan.TicksPerSecond / 10);
            if (tenth != displayedTenth)
            {
                clockText.text = localTime.ToString("HH:mm:ss.f", CultureInfo.InvariantCulture);
                displayedTenth = tenth;
            }

            if (textStyle == null)
            {
                textStyle = new GUIStyle(GUI.skin.label)
                {
                    alignment = TextAnchor.MiddleRight,
                    fontStyle = FontStyle.Bold
                };
                textStyle.normal.textColor = Color.white;
                shadowStyle = new GUIStyle(textStyle);
                shadowStyle.normal.textColor = new Color(0f, 0f, 0f, 0.85f);
            }

            float scale = Mathf.Max(0.75f, Screen.height / 1080f);
            int fontSize = Mathf.RoundToInt(22f * scale);
            if (textStyle.fontSize != fontSize)
            {
                textStyle.fontSize = fontSize;
                shadowStyle.fontSize = fontSize;
            }

            float width = 180f * scale;
            float height = 32f * scale;
            float x = Screen.width - 18f * scale - width;
            float y = 12f * scale;
            GUI.Label(new Rect(x + scale, y + scale, width, height), clockText, shadowStyle);
            GUI.Label(new Rect(x, y, width, height), clockText, textStyle);
        }
    }
}
