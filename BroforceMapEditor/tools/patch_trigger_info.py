"""补丁：add_trigger 用具体 TriggerActionInfo 子类（抽象类不能直接 new）。"""
from pathlib import Path

PATH = Path(r"E:\BroforceMapEditor\bridge\BroforceMapBridge.cs")
text = PATH.read_text(encoding="utf-8")

old = """                string[] parts = raw.Split(':');
                TriggerActionInfo action = new TriggerActionInfo();
                try { action.type = (TriggerActionType)Enum.Parse(typeof(TriggerActionType), parts[0].Trim(), true); }
                catch (Exception) { return "ok=0\\nerror=unknown TriggerActionType: " + OneLine(parts[0]); }
                action.name = parts.Length > 1 ? parts[1].Trim() : "";
                action.timeOffset = 0f;
                info.actions.Add(action);"""
new = """                string[] parts = raw.Split(':');
                TriggerActionType actionType;
                try { actionType = (TriggerActionType)Enum.Parse(typeof(TriggerActionType), parts[0].Trim(), true); }
                catch (Exception) { return "ok=0\\nerror=unknown TriggerActionType: " + OneLine(parts[0]); }
                string infoTypeName = actionType.ToString() + "ActionInfo";
                if (actionType == TriggerActionType.CameraMove) infoTypeName = "CameraActionInfo";
                Type infoType = GameType(infoTypeName);
                if (infoType == null || infoType.IsAbstract)
                    return "ok=0\\nerror=no concrete info class for " + actionType;
                TriggerActionInfo action = (TriggerActionInfo)Activator.CreateInstance(infoType);
                action.type = actionType;
                action.name = parts.Length > 1 ? parts[1].Trim() : "";
                action.timeOffset = 0f;
                info.actions.Add(action);"""
if old not in text:
    raise SystemExit("add_trigger anchor not found")
PATH.write_text(text.replace(old, new), encoding="utf-8")
print("patched add_trigger concrete info")
