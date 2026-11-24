// HudMaterials —— 屏幕右上角「附近科技材料」悬浮列表
//
//   显示玩家周围 150 米内：
//     · 已加载且 READY 的野外料点（天然蜂蜜巢 / 蜂蜡巢 / 辉光矿 / 细沙 / 深卤水…）
//     · 地上可直接真空吸取的 *Craft 科技材料，按物品分组显示数量
//   每秒刷新一次；F6 开关（默认开，只在 gameplay 有内容时绘制）。
//
// 数据来源与 sr2_resource_nodes / sr2_nearby 相同：存档模型 + 当前场景组件，
// 全部在主线程读取；绘制走 IMGUI（与 WallClock / ChatOverlay 同一套自建样式）。
using System;
using System.Collections.Generic;
using System.Linq;
using Il2Cpp;
using Il2CppMonomiPark.SlimeRancher.DataModel;
using UnityEngine;

namespace SlimeRancher2Bridge
{
    internal static class HudMaterials
    {
        const float Radius = 150f;
        const int MaxRows = 8;
        const float RefreshInterval = 0.25f;

        static bool visible = true;
        static float nextRefresh;
        static float toggleGuard;
        static bool failed;
        static readonly List<string> rows = new();
        static string status = "";

        static Font font;
        static GUIStyle textStyle;
        static GUIStyle titleStyle;
        static int fontSize = 22;          // F5 循环切换：18 → 21 → 24 → 27 → 22 …
        static int styleFontSize;
        static readonly int[] fontSteps = { 18, 21, 24, 27 };

        internal static bool Visible => visible;

        internal static void SetVisible(bool on)
        {
            visible = on;
            nextRefresh = 0f;
        }

        internal static void Toggle()
        {
            if (Time.unscaledTime < toggleGuard) return;
            toggleGuard = Time.unscaledTime + 0.3f;
            visible = !visible;
            nextRefresh = 0f;
        }

        // ==================== 每帧（OnUpdate 调用）====================

        internal static void Update()
        {
            if (failed) return;
            // IMGUI 事件通道拿不到时，这里兜底（旧 Input 类被禁用会抛异常，捕获后不再尝试）。
            if (inputAvailable)
            {
                try
                {
                    if (Input.GetKeyDown(KeyCode.F6)) Toggle();
                }
                catch
                {
                    inputAvailable = false;
                }
            }
            if (!visible) return;
            if (Time.unscaledTime < nextRefresh) return;
            nextRefresh = Time.unscaledTime + RefreshInterval;
            Refresh();
        }

        static bool inputAvailable = true;

        // ==================== 收集数据 ====================

        static readonly Dictionary<string, string> labels = new(StringComparer.OrdinalIgnoreCase)
        {
            ["WildHoneyNode"] = "天然蜂蜜",
            ["BuzzWaxNode"] = "蜂蜡",
            ["RadiantOreNode"] = "辉光矿",
            ["SilkySandNode"] = "细沙",
            ["DeepBrineNode"] = "深卤水",
            ["StrangeDiamondNode"] = "奇异钻石",
            ["LavaDustNode"] = "熔岩尘",
            ["JellystoneNode"] = "凝结石",
            ["SunSapNode"] = "太阳树液",
            ["SlimeFossilNode"] = "史莱姆化石",
            ["BlackIndigoniumNode"] = "黑靛蓝",
            ["TinPetalNode"] = "锡花瓣",
            ["MagmaCombNode"] = "熔岩蜂巢",
            ["PrimordyOilNode"] = "原初油",
            ["PerfectSnowflakeNode"] = "完美雪花",
            ["AquaGlassNode"] = "水玻璃",
            ["DreamBubbleNode"] = "梦幻泡泡",
            ["RoyalJellyNode"] = "蜂王浆",
            ["DriftCrystalNode"] = "漂浮水晶",
            ["LightningMoteNode"] = "闪电微粒",
            ["StormGlassNode"] = "风暴玻璃",
        };

        static string NodeLabel(string definitionName)
        {
            if (string.IsNullOrEmpty(definitionName)) return "料点";
            if (labels.TryGetValue(definitionName, out var known)) return known;
            return definitionName.Replace("Node", "").Trim();
        }

        static string CraftLabel(string itemId)
        {
            if (string.IsNullOrEmpty(itemId)) return "材料";
            string shortName = itemId.StartsWith("IdentifiableType.", StringComparison.Ordinal)
                ? itemId.Substring("IdentifiableType.".Length) : itemId;
            return NodeLabel(shortName.Replace("Craft", "Node"));
        }

        static float Distance(float[] a, float[] b)
        {
            float dx = a[0] - b[0], dy = a[1] - b[1], dz = a[2] - b[2];
            return (float)Math.Sqrt(dx * dx + dy * dy + dz * dz);
        }

        // 相对朝向：把「目标 - 玩家」投到玩家前/右两个水平轴上，按 8 个扇区给出中文方位。
        static string DirectionWord(float[] forward, float dx, float dz)
        {
            float horiz = (float)Math.Sqrt(dx * dx + dz * dz);
            if (horiz < 0.5f) return "脚下";
            if (forward == null) return "";
            float fx = forward[0], fz = forward[2];
            float flen = (float)Math.Sqrt(fx * fx + fz * fz);
            if (flen < 0.001f) return "";
            fx /= flen;
            fz /= flen;
            float ahead = (fx * dx + fz * dz) / horiz;
            float right = (fz * dx - fx * dz) / horiz;   // Unity: right = (fz, 0, -fx)
            float angle = (float)(Math.Atan2(right, ahead) * 180.0 / Math.PI);   // +右 / -左
            if (angle >= -22.5f && angle <= 22.5f) return "前";
            if (angle > 22.5f && angle <= 67.5f) return "右前";
            if (angle > 67.5f && angle <= 112.5f) return "右";
            if (angle > 112.5f && angle <= 157.5f) return "右后";
            if (angle < -22.5f && angle >= -67.5f) return "左前";
            if (angle < -67.5f && angle >= -112.5f) return "左";
            if (angle < -112.5f && angle >= -157.5f) return "左后";
            return "后";
        }

        static string HeightHint(float dy)
        {
            if (dy >= 4f) return " ↑" + Mathf.RoundToInt(dy) + "m";
            if (dy <= -4f) return " ↓" + Mathf.RoundToInt(-dy) + "m";
            return "";
        }

        // 一行 = 名称 + 水平距离(0.1m) + 方位 + 高度差，距离走 1 位小数才能边走边看趋势。
        static string FormatRow(string label, float[] playerPos, float[] forward, float[] target)
        {
            float dx = target[0] - playerPos[0];
            float dy = target[1] - playerPos[1];
            float dz = target[2] - playerPos[2];
            float horiz = (float)Math.Sqrt(dx * dx + dz * dz);
            string dir = DirectionWord(forward, dx, dz);
            return label + " " + horiz.ToString("0.0") + "m"
                + (dir.Length == 0 ? "" : " " + dir) + HeightHint(dy);
        }

        static void Refresh()
        {
            var collected = new List<(float Dist, string Text)>();
            bool inGameplay = false;
            try
            {
                ToolsNavigation.WithScene(raw =>
                {
                    inGameplay = true;
                    var scene = (SceneContext)raw;
                    var player = Il2CppUtil.Try(scene, "Player");
                    float[] playerPos = player == null ? null : Il2CppUtil.PositionOf(player);
                    if (playerPos == null) return new Dictionary<string, object>();
                    float[] playerFwd = Il2CppUtil.ForwardOf(player);

                    // ① 野外料点：存档模型里 READY 且当前场景已加载的生成点
                    try
                    {
                        var game = scene.GameModel;
                        var spawners = new Dictionary<IntPtr, ResourceNodeSpawner>();
                        foreach (var spawner in UnityEngine.Object.FindObjectsByType<ResourceNodeSpawner>(FindObjectsSortMode.None))
                        {
                            if (spawner == null) continue;
                            IntPtr ptr = spawner._model?.Pointer ?? IntPtr.Zero;
                            if (ptr != IntPtr.Zero) spawners[ptr] = spawner;
                        }
                        var models = game?.resourceNodeSpawnerModels;
                        if (models != null)
                        {
                            foreach (var entry in models)
                            {
                                var model = entry.Value;
                                if (model == null) continue;
                                if (!string.Equals(model.nodeState.ToString(), "READY", StringComparison.OrdinalIgnoreCase)) continue;
                                if (!spawners.TryGetValue(model.Pointer, out var spawner) || spawner == null) continue;
                                float[] pos = Il2CppUtil.PositionOf(spawner);
                                if (pos == null) continue;
                                float dist = Distance(playerPos, pos);
                                if (dist > Radius) continue;
                                int amount = model.resourcesToSpawn?.Count ?? 0;
                                collected.Add((dist, FormatRow(NodeLabel(model.resourceNodeDefinition?.name) + " " + amount
                                    + "份", playerPos, playerFwd, pos)));
                            }
                        }
                    }
                    catch
                    {
                        // 料点接口缺失时不影响地上材料列表
                    }

                    // ② 地上的 *Craft 科技材料（可直接真空吸）
                    try
                    {
                        var grouped = new Dictionary<string, (int Count, float Nearest, float[] Pos)>(StringComparer.OrdinalIgnoreCase);
                        foreach (var vacuumable in Il2CppUtil.FindSceneObjects("Vacuumable", out _))
                        {
                            try
                            {
                                if (Il2CppUtil.BoolOf(Il2CppUtil.Try(vacuumable, "_held"))) continue;
                                var identifiable = Il2CppUtil.Try(vacuumable, "_identifiable")
                                    ?? Il2CppUtil.Try(vacuumable, "Identifiable");
                                var identType = identifiable == null ? null : Il2CppUtil.Try(identifiable, "identType");
                                if (identType == null) continue;
                                string id = Il2CppUtil.Try(identType, "ReferenceId") as string
                                    ?? Il2CppUtil.Try(identType, "name") as string;
                                if (string.IsNullOrEmpty(id)
                                    || id.IndexOf("Craft", StringComparison.OrdinalIgnoreCase) < 0) continue;
                                float[] pos = Il2CppUtil.PositionOf(vacuumable);
                                if (pos == null) continue;
                                float dist = Distance(playerPos, pos);
                                if (dist > Radius) continue;
                                if (grouped.TryGetValue(id, out var old))
                                    grouped[id] = (old.Count + 1,
                                        Math.Min(old.Nearest, dist),
                                        dist < old.Nearest ? pos : old.Pos);
                                else
                                    grouped[id] = (1, dist, pos);
                            }
                            catch
                            {
                                // 单个对象读取失败不影响其它
                            }
                        }
                        foreach (var pair in grouped)
                            collected.Add((pair.Value.Nearest,
                                FormatRow(CraftLabel(pair.Key) + " ×" + pair.Value.Count,
                                    playerPos, playerFwd, pair.Value.Pos)));
                    }
                    catch
                    {
                    }
                    return new Dictionary<string, object>();
                });
            }
            catch
            {
            }

            rows.Clear();
            if (!inGameplay)
            {
                status = "";
                return;
            }
            collected.Sort((a, b) => a.Dist.CompareTo(b.Dist));
            foreach (var row in collected.Take(MaxRows)) rows.Add(row.Text);
            if (collected.Count > MaxRows) rows.Add("…还有 " + (collected.Count - MaxRows) + " 个");
            status = "";
        }

        // ==================== 绘制（OnGUI 调用）====================

        internal static void Draw()
        {
            if (failed) return;
            try
            {
                var e = Event.current;
                if (e != null && e.type == EventType.KeyDown && e.keyCode == KeyCode.F6)
                {
                    Toggle();
                    e.Use();
                }
                if (e != null && e.type == EventType.KeyDown && e.keyCode == KeyCode.F5)
                {
                    CycleFontSize();
                    e.Use();
                }
                if (!visible) return;
                if (rows.Count == 0) return;   // 没有材料时不画，避免空面板挡视野
                EnsureStyle();
                string body = string.Join("\n", rows);
                int lineCount = rows.Count;
                float width = Mathf.Max(380f, fontSize * 22f);
                float lineH = fontSize + 6f;
                float titleH = fontSize + 4f;
                float height = titleH + 6f + lineCount * lineH + 2f;
                // 左上角时钟（WallClock 占 12,48 320x46）下方，避开 SlimeMap 右上角小地图。
                var panel = new Rect(14f, 100f, width, height);
                Color oldBackground = GUI.backgroundColor;
                Color oldColor = GUI.color;
                GUI.backgroundColor = new Color(0.04f, 0.07f, 0.11f, 0.62f);
                GUI.Box(panel, GUIContent.none);
                GUI.backgroundColor = oldBackground;
                GUI.color = new Color(0.55f, 0.85f, 1f);
                GUI.Label(new Rect(panel.x + 8f, panel.y + 2f, width - 16f, titleH),
                    "科技材料 · F6关 · F5字号" + (status.Length == 0 ? "" : " · " + status), titleStyle);
                GUI.color = Color.white;
                GUI.Label(new Rect(panel.x + 8f, panel.y + titleH + 4f, width - 16f, lineCount * lineH), body, textStyle);
                GUI.color = oldColor;
            }
            catch (Exception e)
            {
                failed = true;
                visible = false;
                MelonLoader.MelonLogger.Warning("[SR2Bridge] HUD materials unavailable: "
                    + e.GetBaseException().Message);
            }
        }

        static void CycleFontSize()
        {
            int index = Array.IndexOf(fontSteps, fontSize);
            fontSize = fontSteps[(index + 1) % fontSteps.Length];
            textStyle = null;   // 下次绘制时按新字号重建
        }

        static void EnsureStyle()
        {
            if (textStyle != null && styleFontSize == fontSize) return;
            styleFontSize = fontSize;
            font = BuildFont();
            textStyle = new GUIStyle
            {
                font = font,
                fontSize = fontSize,
                wordWrap = true,
                alignment = TextAnchor.UpperLeft,
                clipping = TextClipping.Clip,
                richText = false,
            };
            titleStyle = new GUIStyle
            {
                font = font,
                fontSize = fontSize,
                wordWrap = false,
                alignment = TextAnchor.MiddleLeft,
                clipping = TextClipping.Clip,
                richText = false,
            };
            try
            {
                textStyle.normal.textColor = Color.white;
                titleStyle.normal.textColor = Color.white;
            }
            catch
            {
            }
        }

        static Font BuildFont()
        {
            foreach (string name in new[] { "Microsoft YaHei", "微软雅黑", "SimHei", "黑体", "SimSun", "宋体", "Arial" })
            {
                try
                {
                    var built = Font.CreateDynamicFontFromOSFont(name, fontSize);
                    if (built != null) return built;
                }
                catch
                {
                }
            }
            return null;
        }
    }
}
