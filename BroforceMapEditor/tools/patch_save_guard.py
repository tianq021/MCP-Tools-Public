"""补丁：防止格式/扩展名不匹配时覆盖文件（上次把 .bfg 覆盖成 .bfc 的教训）。"""
from pathlib import Path

PATH = Path(r"E:\BroforceMapEditor\bridge\BroforceMapBridge.cs")
text = PATH.read_text(encoding="utf-8")

# SaveBfg 开头加扩展名校验
old = """            Directory.CreateDirectory(Path.GetDirectoryName(path));

            MethodInfo serialize = GameType("FileIO").GetMethod("SerializeCampaign", Static | BindingFlags.NonPublic);"""
new = """            Directory.CreateDirectory(Path.GetDirectoryName(path));

            string expectedExtension = workshopFormat ? ".bfg" : ".bfc";
            if (!path.EndsWith(expectedExtension, StringComparison.OrdinalIgnoreCase))
                return "ok=0\\nerror=extension/format mismatch: expected " + expectedExtension
                    + " for format=" + (workshopFormat ? "bfg" : "bfc") + ", got " + OneLine(path);

            MethodInfo serialize = GameType("FileIO").GetMethod("SerializeCampaign", Static | BindingFlags.NonPublic);"""
if old not in text:
    raise SystemExit("SaveBfg anchor not found")
text = text.replace(old, new, 1)

# CloneLevel：没给 path 时按名字生成 maps/<name>.bfc；给了 path 必须 .bfc
old_clone = """            string note = Get(command, "description");
            if (note == null) note = "克隆内置关卡 " + resource + "，2026-11-15 V0.1";"""
new_clone = """            string note = Get(command, "description");
            if (note == null) note = "克隆关卡范本，2026-11-15 V0.1";
            if (string.IsNullOrEmpty(Get(command, "path")))
            {
                string safeName = name;
                char[] invalid = Path.GetInvalidFileNameChars();
                for (int index = 0; index < invalid.Length; index++)
                    safeName = safeName.Replace(invalid[index], '_');
                command["path"] = Path.Combine(Path.Combine(AppDataDirectory, "maps"), safeName + ".bfc");
            }"""
if old_clone not in text:
    raise SystemExit("CloneLevel anchor not found")
text = text.replace(old_clone, new_clone, 1)

PATH.write_text(text, encoding="utf-8")
print("patched save guard + clone default path")
