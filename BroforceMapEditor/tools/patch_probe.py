"""补丁：新增 probe_doodads（查某类物件的坐标与所在列地形高度，用来反推摆放偏移）。"""
from pathlib import Path

PATH = Path(r"E:\BroforceMapEditor\bridge\BroforceMapBridge.cs")
text = PATH.read_text(encoding="utf-8")

text = text.replace(
    '''                case "add_trigger":
                    return AddTrigger(command);''',
    '''                case "add_trigger":
                    return AddTrigger(command);
                case "probe_doodads":
                    return ProbeDoodads(command);''',
)

probe = '''        private static string ProbeDoodads(Dictionary<string, string> command)
        {
            if (currentMap == null) return "ok=0\\nerror=no map in memory";
            string typeName = Get(command, "type");
            if (string.IsNullOrEmpty(typeName)) return "ok=0\\nerror=type required";
            DoodadType wanted;
            try { wanted = (DoodadType)Enum.Parse(typeof(DoodadType), typeName, true); }
            catch (Exception) { return "ok=0\\nerror=unknown DoodadType: " + OneLine(typeName); }
            int limit = GetInt(command, "limit", 8);
            TerrainType[,] foreground = currentMap.foregroundBlocks;
            bool columnFirst = foreground.GetLength(0) == currentMap.Width;
            int columns = columnFirst ? foreground.GetLength(0) : foreground.GetLength(1);
            int rows = columnFirst ? foreground.GetLength(1) : foreground.GetLength(0);
            int[] tops = new int[columns];
            for (int column = 0; column < columns; column++)
            {
                int top = -1;
                for (int row = rows - 1; row >= 0; row--)
                {
                    TerrainType value = columnFirst ? foreground[column, row] : foreground[row, column];
                    if (value != TerrainType.Empty) { top = row; break; }
                }
                tops[column] = top;
            }
            StringBuilder builder = new StringBuilder("ok=1");
            List<DoodadInfo> doodads = currentMap.DoodadList;
            int found = 0;
            if (doodads != null)
            {
                for (int index = 0; index < doodads.Count && found < limit; index++)
                {
                    DoodadInfo info = doodads[index];
                    if (info == null || info.type != wanted || info.position == null) continue;
                    int column = info.position.collumn;
                    int row = info.position.row;
                    int top = (column >= 0 && column < columns) ? tops[column] : -2;
                    builder.Append("\\n").Append(column).Append(',').Append(row)
                        .Append(" top=").Append(top).Append(" delta=").Append(row - top);
                    found++;
                }
            }
            builder.Append("\\nmatched=").Append(found);
            return builder.ToString();
        }

        private static string AddTrigger(Dictionary<string, string> command)'''

text = text.replace("        private static string AddTrigger(Dictionary<string, string> command)", probe, 1)
PATH.write_text(text, encoding="utf-8")
print("patched probe_doodads")
