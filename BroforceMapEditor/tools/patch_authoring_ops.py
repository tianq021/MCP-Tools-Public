"""补丁：桥接增加创作命令 new_map / set_tiles / add_doodads / dump_triggers，并让备注写入 header+levelDescription。"""
from __future__ import annotations

from pathlib import Path

PATH = Path(r"E:\BroforceMapEditor\bridge\BroforceMapBridge.cs")
text = PATH.read_text(encoding="utf-8")

# 1) 命令分发
text = text.replace(
    '''                case "clone_level":
                    return CloneLevel(command);''',
    '''                case "clone_level":
                    return CloneLevel(command);
                case "new_map":
                    return NewMap(command);
                case "set_tiles":
                    return SetTiles(command);
                case "add_doodads":
                    return AddDoodads(command);
                case "dump_triggers":
                    return DumpTriggers(command);''',
)

new_methods = '''        private static string NewMap(Dictionary<string, string> command)
        {
            int width = GetInt(command, "width", 100);
            int height = GetInt(command, "height", 40);
            if (width < 8 || height < 8 || width > 512 || height > 256)
                return "ok=0\\nerror=size out of range";
            string name = Get(command, "name");
            if (string.IsNullOrEmpty(name)) name = "AI MAP";
            string author = Get(command, "author");
            if (string.IsNullOrEmpty(author)) author = "BROFORCEMAPBRIDGE";
            string note = Get(command, "description");
            if (note == null) note = "";

            MapData map = new MapData(width, height);
            map.ResetMap(width, height);
            map.theme = (LevelTheme)GetInt(command, "theme", 0);
            map.musicType = (MusicType)GetInt(command, "music", 3);
            map.weatherType = (WeatherType)GetInt(command, "weather", 2);
            map.cameraFollowMode = (CameraFollowMode)GetInt(command, "camera", 0);
            map.heroSpawnMode = (HeroSpawnMode)GetInt(command, "spawn", 0);
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
            return "ok=1\\nwidth=" + map.Width + "\\nheight=" + map.Height
                + "\\nname=" + OneLine(name) + "\\ndescription=" + OneLine(note);
        }

        // tiles 文件格式：首行可选的 "legend=char:TerrainTypeName,char:..."，其余每行一个字符串。
        // 行从上到下书写（第一行 = 顶部），桥接自动翻转为 row 0 = 底部。
        private static string SetTiles(Dictionary<string, string> command)
        {
            if (currentMap == null) return "ok=0\\nerror=no map; call new_map first";
            string tilesPath = Get(command, "tiles");
            if (string.IsNullOrEmpty(tilesPath) || !File.Exists(tilesPath))
                return "ok=0\\nerror=tiles file missing: " + OneLine(tilesPath);
            string legendText = Get(command, "legend");
            Dictionary<char, TerrainType> legend = new Dictionary<char, TerrainType>();
            legend[' '] = TerrainType.Empty;
            legend['.'] = TerrainType.Empty;
            if (!string.IsNullOrEmpty(legendText))
            {
                string[] pairs = legendText.Split(',');
                for (int index = 0; index < pairs.Length; index++)
                {
                    string pair = pairs[index].Trim();
                    int split = pair.IndexOf(':');
                    if (split <= 0) continue;
                    string key = pair.Substring(0, split);
                    if (key.Length == 0) continue;
                    string valueName = pair.Substring(split + 1).Trim();
                    try { legend[key[0]] = (TerrainType)Enum.Parse(typeof(TerrainType), valueName, true); }
                    catch (Exception) { return "ok=0\\nerror=unknown TerrainType: " + OneLine(valueName); }
                }
            }

            string[] lines = File.ReadAllLines(tilesPath, Utf8NoBom);
            TerrainType[,] foreground = currentMap.foregroundBlocks;
            bool columnFirst = foreground.GetLength(0) == currentMap.Width;
            int columns = columnFirst ? foreground.GetLength(0) : foreground.GetLength(1);
            int rows = columnFirst ? foreground.GetLength(1) : foreground.GetLength(0);
            int painted = 0;
            for (int lineIndex = 0; lineIndex < lines.Length; lineIndex++)
            {
                string line = lines[lineIndex];
                if (line.StartsWith("legend=")) continue;
                if (line.Length == 0) continue;
                int row = rows - 1 - lineIndex; // 文件第一行是顶部
                if (row < 0) break;
                for (int column = 0; column < line.Length && column < columns; column++)
                {
                    TerrainType value;
                    if (!legend.TryGetValue(line[column], out value)) value = TerrainType.Empty;
                    if (columnFirst) foreground[column, row] = value;
                    else foreground[row, column] = value;
                    painted++;
                }
            }
            return "ok=1\\npainted=" + painted + "\\ncolumns=" + columns + "\\nrows=" + rows;
        }

        // doodads 文件：每行 x,y,DoodadTypeName[,variation]
        private static string AddDoodads(Dictionary<string, string> command)
        {
            if (currentMap == null) return "ok=0\\nerror=no map; call new_map first";
            string path = Get(command, "doodads");
            if (string.IsNullOrEmpty(path) || !File.Exists(path))
                return "ok=0\\nerror=doodads file missing: " + OneLine(path);
            if (currentMap.DoodadList == null)
                currentMap.DoodadList = new List<DoodadInfo>();
            string[] lines = File.ReadAllLines(path, Utf8NoBom);
            int added = 0;
            for (int index = 0; index < lines.Length; index++)
            {
                string line = lines[index].Trim();
                if (line.Length == 0 || line.StartsWith("#")) continue;
                string[] parts = line.Split(',');
                if (parts.Length < 3) continue;
                int x, y, variation = 0;
                if (!Int32.TryParse(parts[0].Trim(), out x)) continue;
                if (!Int32.TryParse(parts[1].Trim(), out y)) continue;
                if (parts.Length > 3) Int32.TryParse(parts[3].Trim(), out variation);
                DoodadType type;
                try { type = (DoodadType)Enum.Parse(typeof(DoodadType), parts[2].Trim(), true); }
                catch (Exception) { continue; }
                currentMap.DoodadList.Add(new DoodadInfo(new GridPoint(x, y), type, variation));
                added++;
            }
            return "ok=1\\nadded=" + added + "\\ntotal=" + currentMap.DoodadList.Count;
        }

        private static string DumpTriggers(Dictionary<string, string> command)
        {
            if (currentMap == null) return "ok=0\\nerror=no map in memory";
            StringBuilder builder = new StringBuilder("ok=1");
            List<TriggerInfo> triggers = currentMap.TriggerList;
            builder.Append("\\ntriggers=").Append(triggers == null ? 0 : triggers.Count);
            if (triggers != null)
            {
                for (int index = 0; index < triggers.Count; index++)
                {
                    TriggerInfo info = triggers[index];
                    if (info == null) continue;
                    builder.Append("\\ntrigger.").Append(index)
                        .Append("=type:").Append(info.type)
                        .Append(" bl:").Append(info.bottomLeft == null ? "?" : info.bottomLeft.collumn + "," + info.bottomLeft.row)
                        .Append(" ur:").Append(info.upperRight == null ? "?" : info.upperRight.collumn + "," + info.upperRight.row)
                        .Append(" name:").Append(OneLine(info.name))
                        .Append(" actions:").Append(info.actions == null ? 0 : info.actions.Count);
                    if (info.actions != null)
                    {
                        for (int actionIndex = 0; actionIndex < info.actions.Count; actionIndex++)
                        {
                            TriggerActionInfo action = info.actions[actionIndex];
                            if (action == null) continue;
                            builder.Append(" [").Append(action.type).Append(':').Append(OneLine(action.name)).Append(']');
                        }
                    }
                }
            }
            List<DoodadInfo> doodads = currentMap.DoodadList;
            if (doodads != null)
            {
                for (int index = 0; index < doodads.Count && index < 20; index++)
                {
                    DoodadInfo info = doodads[index];
                    if (info == null) continue;
                    builder.Append("\\ndoodad.").Append(index).Append('=').Append(info.type)
                        .Append(" at ").Append(info.position == null ? "?" : info.position.collumn + "," + info.position.row)
                        .Append(" var ").Append(info.variation);
                }
            }
            return builder.ToString();
        }

        private static string InspectLevel(Dictionary<string, string> command)'''

text = text.replace("        private static string InspectLevel(Dictionary<string, string> command)", new_methods, 1)
PATH.write_text(text, encoding="utf-8")
print("patched authoring ops")
