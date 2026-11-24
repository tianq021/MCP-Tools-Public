"""补丁：inspect_level 缓存到内存（供 dump_triggers 使用）；新增 add_trigger 命令。"""
from pathlib import Path

PATH = Path(r"E:\BroforceMapEditor\bridge\BroforceMapBridge.cs")
text = PATH.read_text(encoding="utf-8")

# 1) InspectLevel 缓存
old = """            MapData map;
            if (!TryLoadLevel(command, out map)) return "ok=0\\nerror=level not found";
            return "ok=1\\n" + DescribeMap(map);"""
new = """            MapData map;
            if (!TryLoadLevel(command, out map)) return "ok=0\\nerror=level not found";
            currentMap = map;
            string cacheName = Get(command, "name");
            if (string.IsNullOrEmpty(cacheName)) cacheName = "INSPECTED LEVEL";
            CampaignHeader cacheHeader = new CampaignHeader();
            cacheHeader.name = cacheName;
            cacheHeader.author = "BROFORCEMAPBRIDGE";
            cacheHeader.description = Get(command, "description") ?? "";
            cacheHeader.length = 1;
            cacheHeader.gameMode = GameMode.Campaign;
            Campaign cacheCampaign = new Campaign();
            cacheCampaign.name = cacheName;
            cacheCampaign.header = cacheHeader;
            cacheCampaign.levels = new MapData[] { map };
            cacheCampaign.brodownLevel = 0;
            currentCampaign = cacheCampaign;
            return "ok=1\\n" + DescribeMap(map);"""
if old not in text:
    raise SystemExit("InspectLevel anchor not found")
text = text.replace(old, new, 1)

# 2) add_trigger 分发
text = text.replace(
    '''                case "dump_triggers":
                    return DumpTriggers(command);''',
    '''                case "dump_triggers":
                    return DumpTriggers(command);
                case "add_trigger":
                    return AddTrigger(command);''',
)

new_method = '''        // add_trigger：type,blx,bly,urx,ury[,action=ActionTypeName:actionName][,action=...]
        private static string AddTrigger(Dictionary<string, string> command)
        {
            if (currentMap == null) return "ok=0\\nerror=no map in memory";
            if (currentMap.TriggerList == null) currentMap.TriggerList = new List<TriggerInfo>();
            TriggerInfo info = new TriggerInfo();
            info.type = (TriggerType)GetInt(command, "type", 0);
            info.bottomLeft = new GridPoint(GetInt(command, "blx", 0), GetInt(command, "bly", 0));
            info.upperRight = new GridPoint(GetInt(command, "urx", 0), GetInt(command, "ury", 0));
            info.startEnabled = true;
            info.onlyTriggerOnce = true;
            info.actions = new List<TriggerActionInfo>();
            for (int index = 0; ; index++)
            {
                string raw = Get(command, "action" + (index == 0 ? "" : index.ToString()));
                if (string.IsNullOrEmpty(raw)) break;
                string[] parts = raw.Split(':');
                TriggerActionInfo action = new TriggerActionInfo();
                try { action.type = (TriggerActionType)Enum.Parse(typeof(TriggerActionType), parts[0].Trim(), true); }
                catch (Exception) { return "ok=0\\nerror=unknown TriggerActionType: " + OneLine(parts[0]); }
                action.name = parts.Length > 1 ? parts[1].Trim() : "";
                action.timeOffset = 0f;
                info.actions.Add(action);
            }
            currentMap.TriggerList.Add(info);
            return "ok=1\\ntriggers=" + currentMap.TriggerList.Count
                + "\\nlast.type=" + info.type + "\\nlast.actions=" + info.actions.Count;
        }

        private static string DumpTriggers(Dictionary<string, string> command)'''

text = text.replace("        private static string DumpTriggers(Dictionary<string, string> command)", new_method, 1)
PATH.write_text(text, encoding="utf-8")
print("patched inspect cache + add_trigger")
