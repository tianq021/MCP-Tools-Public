"""补丁：桥接增加 inspect_level / clone_level（读内置战役关卡作为范本并克隆成自制图）。"""
from __future__ import annotations

import re
from pathlib import Path

PATH = Path(r"E:\BroforceMapEditor\bridge\BroforceMapBridge.cs")
text = PATH.read_text(encoding="utf-8")

# 1) 命令分发
text = text.replace(
    '''                case "list_levels":
                    return ListLevels(command);''',
    '''                case "list_levels":
                    return ListLevels(command);
                case "inspect_level":
                    return InspectLevel(command);
                case "clone_level":
                    return CloneLevel(command);''',
)

# 2) 新方法插入到 ListLevels 之前
new_methods = '''        private static string InspectLevel(Dictionary<string, string> command)
        {
            MapData map;
            if (!TryLoadLevel(command, out map)) return "ok=0\\nerror=level not found";
            return "ok=1\\n" + DescribeMap(map);
        }

        private static string CloneLevel(Dictionary<string, string> command)
        {
            MapData map;
            if (!TryLoadLevel(command, out map)) return "ok=0\\nerror=level not found";
            string resource = Get(command, "resource");
            string name = Get(command, "name");
            if (string.IsNullOrEmpty(name)) name = "AI REAL MAP " + resource;
            string author = Get(command, "author");
            if (string.IsNullOrEmpty(author)) author = "BROFORCEMAPBRIDGE";
            string note = Get(command, "description");
            if (note == null) note = "克隆内置关卡 " + resource + "，2026-11-15 V0.1";
            map.levelDescription = note;

            CampaignHeader header = new CampaignHeader();
            header.name = name;
            header.author = author;
            header.description = note;
            header.length = 1;
            header.gameMode = GameMode.Campaign;
            header.hasTimeScoreBoard = true;

            Campaign campaign = new Campaign();
            campaign.name = name;
            campaign.header = header;
            campaign.levels = new MapData[] { map };
            campaign.brodownLevel = 0;

            currentMap = map;
            currentCampaign = campaign;
            return SaveBfg(command) + "\\n" + DescribeMap(map);
        }

        private static bool TryLoadLevel(Dictionary<string, string> command, out MapData map)
        {
            map = null;
            string resource = Get(command, "resource");
            string path = Get(command, "path");
            try
            {
                if (!string.IsNullOrEmpty(resource))
                {
                    MethodInfo load = GameType("FileIO").GetMethod("LoadLevelFromResources", Static);
                    if (load == null) return false;
                    map = load.Invoke(null, new object[] { resource }) as MapData;
                    return map != null;
                }
                if (!string.IsNullOrEmpty(path))
                {
                    MethodInfo loadCampaignBytes = GameType("FileIO").GetMethod("LoadCampaignBytes", Static);
                    if (loadCampaignBytes == null) return false;
                    Campaign campaign = loadCampaignBytes.Invoke(null,
                        new object[] { File.ReadAllBytes(path), false, true }) as Campaign;
                    if (campaign == null || campaign.levels == null || campaign.levels.Length == 0) return false;
                    map = campaign.levels[0];
                    return map != null;
                }
            }
            catch (Exception)
            {
                return false;
            }
            return false;
        }

        private static string DescribeMap(MapData map)
        {
            TerrainType[,] foreground = map.foregroundBlocks;
            int first = foreground == null ? 0 : foreground.GetLength(0);
            int second = foreground == null ? 0 : foreground.GetLength(1);
            Dictionary<TerrainType, int> terrain = new Dictionary<TerrainType, int>();
            for (int a = 0; a < first; a++)
            {
                for (int b = 0; b < second; b++)
                {
                    TerrainType value = foreground[a, b];
                    int count;
                    terrain.TryGetValue(value, out count);
                    terrain[value] = count + 1;
                }
            }
            Dictionary<DoodadType, int> doodads = new Dictionary<DoodadType, int>();
            List<DoodadInfo> doodadList = map.DoodadList;
            if (doodadList != null)
            {
                for (int index = 0; index < doodadList.Count; index++)
                {
                    DoodadInfo info = doodadList[index];
                    if (info == null) continue;
                    int count;
                    doodads.TryGetValue(info.type, out count);
                    doodads[info.type] = count + 1;
                }
            }
            int triggerCount = map.TriggerList == null ? 0 : map.TriggerList.Count;
            StringBuilder builder = new StringBuilder();
            builder.Append("width=").Append(map.Width).Append("\\nheight=").Append(map.Height);
            builder.Append("\\narray=").Append(first).Append('x').Append(second);
            builder.Append("\\ndoodads=").Append(doodadList == null ? 0 : doodadList.Count);
            builder.Append("\\ntriggers=").Append(triggerCount);
            builder.Append("\\nentities=").Append(map.entityTriggers == null ? 0 : map.entityTriggers.Count);
            foreach (KeyValuePair<TerrainType, int> pair in terrain)
                builder.Append("\\nterrain.").Append(pair.Key).Append('=').Append(pair.Value);
            foreach (KeyValuePair<DoodadType, int> pair in doodads)
                builder.Append("\\ndoodad.").Append(pair.Key).Append('=').Append(pair.Value);
            return builder.ToString();
        }

        private static string ListLevels(Dictionary<string, string> command)'''

text = text.replace("        private static string ListLevels(Dictionary<string, string> command)", new_methods, 1)
PATH.write_text(text, encoding="utf-8")
print("patched bridge: inspect_level / clone_level")
