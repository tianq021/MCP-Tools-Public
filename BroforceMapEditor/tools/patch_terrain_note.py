"""补丁：修正脚手架地形方向 + 支持地图备注（name/author/description）。"""
from __future__ import annotations

import re
from pathlib import Path

PATH = Path(r"E:\BroforceMapEditor\bridge\BroforceMapBridge.cs")
text = PATH.read_text(encoding="utf-8")

new_impl = '''        private static string MakeTestMap(Dictionary<string, string> command)
        {
            int width = GetInt(command, "width", 40);
            int height = GetInt(command, "height", 20);
            int theme = GetInt(command, "theme", 0);
            string name = Get(command, "name");
            if (string.IsNullOrEmpty(name)) name = "AI Test Map";
            string author = Get(command, "author");
            if (string.IsNullOrEmpty(author)) author = "BroforceMapBridge";
            string note = Get(command, "description");
            if (note == null) note = "";
            if (width < 8 || height < 8 || width > 512 || height > 256)
                return "ok=0\\nerror=size out of range";

            MapData map = new MapData(width, height);
            map.ResetMap(width, height);
            TerrainType[,] fg = map.foregroundBlocks;
            int first = fg.GetLength(0);
            int second = fg.GetLength(1);
            // 行号 0 = 底部（Map.GetBlockCenterY 用 row 向上），所以地板放在 0..1 行。
            bool columnFirst = first == width;
            int columns = columnFirst ? first : second;
            int rows = columnFirst ? second : first;
            for (int c = 0; c < columns; c++)
            {
                for (int r = 0; r < rows; r++)
                {
                    bool floor = r <= 1;
                    bool wall = c == 0 || c == columns - 1;
                    TerrainType value = (floor || wall) ? TerrainType.Earth : TerrainType.Empty;
                    if (columnFirst) fg[c, r] = value;
                    else fg[r, c] = value;
                }
            }
            // 中间加一段平台，方便肉眼确认方向与比例。
            int midColumn = columns / 2;
            int platformRow = rows / 2;
            for (int c = midColumn - 3; c <= midColumn + 3; c++)
            {
                if (c <= 0 || c >= columns - 1) continue;
                if (columnFirst) fg[c, platformRow] = TerrainType.Earth;
                else fg[platformRow, c] = TerrainType.Earth;
            }

            map.theme = (LevelTheme)theme;
            map.musicType = MusicType.JungleBlueSky;
            map.weatherType = WeatherType.Day;
            map.cameraFollowMode = CameraFollowMode.Normal;
            map.heroSpawnMode = HeroSpawnMode.Auto;
            map.levelDescription = note;

            CampaignHeader header = new CampaignHeader();
            header.name = name;
            header.author = author;
            header.description = note;
            header.length = 1;
            header.gameMode = GameMode.Campaign;

            Campaign campaign = new Campaign();
            campaign.name = name;
            campaign.header = header;
            campaign.levels = new MapData[] { map };
            campaign.brodownLevel = 0;

            currentMap = map;
            currentCampaign = campaign;
            return "ok=1\\nwidth=" + columns + "\\nheight=" + rows
                + "\\narray=" + first + "x" + second + "\\ncolumnFirst=" + columnFirst
                + "\\nname=" + OneLine(name) + "\\nauthor=" + OneLine(author)
                + "\\ndescription=" + OneLine(note);
        }

'''

pattern = re.compile(
    r"        private static string MakeTestMap\(Dictionary<string, string> command\).*?\n        \}\n\n        private static string SaveBfg",
    re.S,
)
match = pattern.search(text)
if not match:
    raise SystemExit("MakeTestMap block not found")
text = text[: match.start()] + new_impl + "        private static string SaveBfg" + text[match.end():]
PATH.write_text(text, encoding="utf-8")
print("patched MakeTestMap")

# 客户端：make-test-map 支持 --name/--author/--note
client = Path(r"E:\BroforceMapEditor\tools\bridge_client.py")
source = client.read_text(encoding="utf-8")
source = source.replace(
    '    parser.add_argument("--theme", type=int, default=0)',
    '    parser.add_argument("--theme", type=int, default=0)\n'
    '    parser.add_argument("--name", default="")\n'
    '    parser.add_argument("--author", default="BroforceMapBridge")\n'
    '    parser.add_argument("--note", default="")',
)
source = source.replace(
    '            result = send_command({"op": "make_test_map", "width": args.width,\n'
    '                                   "height": args.height, "theme": args.theme})',
    '            result = send_command({"op": "make_test_map", "width": args.width,\n'
    '                                   "height": args.height, "theme": args.theme,\n'
    '                                   "name": args.name, "author": args.author,\n'
    '                                   "description": args.note})',
)
client.write_text(source, encoding="utf-8")
print("patched bridge_client")
