"""一次性补丁：更新 BroforceMapBridge.cs 的 GameType 回退、SceneLoader 类型名、SaveBfg（.bfc/.bfg）。"""
from __future__ import annotations

import re
from pathlib import Path

PATH = Path(r"E:\BroforceMapEditor\bridge\BroforceMapBridge.cs")
text = PATH.read_text(encoding="utf-8")

old_gametype = """        private static Type GameType(string name)
        {
            return Type.GetType(name + ", Assembly-CSharp") ?? Type.GetType(name);
        }"""
new_gametype = """        private static Type GameType(string name)
        {
            Type type = Type.GetType(name + ", Assembly-CSharp") ?? Type.GetType(name);
            if (type != null) return type;
            Assembly assembly = typeof(MapData).Assembly;
            Type[] types;
            try { types = assembly.GetTypes(); }
            catch (ReflectionTypeLoadException exception) { types = exception.Types; }
            for (int index = 0; index < types.Length; index++)
            {
                Type candidate = types[index];
                if (candidate == null) continue;
                if (candidate.FullName == name || candidate.Name == name) return candidate;
            }
            return null;
        }"""
if old_gametype not in text:
    raise SystemExit("GameType anchor not found")
text = text.replace(old_gametype, new_gametype)
text = text.replace('GameType("Utility+SceneLoader")', 'GameType("Utility.SceneLoader")')

new_save = '''        private static string SaveBfg(Dictionary<string, string> command)
        {
            if (currentCampaign == null) return "ok=0\\nerror=no map in memory";
            bool workshopFormat = Get(command, "format") == "bfg";
            string path = Get(command, "path");
            if (string.IsNullOrEmpty(path))
                path = Path.Combine(Path.Combine(AppDataDirectory, "maps"),
                    workshopFormat ? "ai_test.bfg" : "ai_test.bfc");
            Directory.CreateDirectory(Path.GetDirectoryName(path));

            MethodInfo serialize = GameType("FileIO").GetMethod("SerializeCampaign", Static | BindingFlags.NonPublic);
            if (serialize == null) return "ok=0\\nerror=FileIO.SerializeCampaign not found";
            // .bfc（本地自制战役）= 不加密 + LZF；.bfg（工坊发布）= 加密 + LZF。
            byte[] bytes = (byte[])serialize.Invoke(null, new object[] { currentCampaign, workshopFormat, true });
            if (bytes == null || bytes.Length == 0) return "ok=0\\nerror=empty result";
            File.WriteAllBytes(path, bytes);

            string extra;
            MethodInfo loadCampaignBytes = GameType("FileIO").GetMethod("LoadCampaignBytes", Static);
            Campaign roundTrip = (Campaign)loadCampaignBytes.Invoke(null, new object[] { bytes, workshopFormat, true });
            if (roundTrip == null || roundTrip.levels == null || roundTrip.levels.Length == 0)
                return "ok=0\\nerror=roundtrip returned no levels\\npath=" + path;
            extra = "\\nroundtrip=" + roundTrip.levels[0].Width + "x" + roundTrip.levels[0].Height
                + "\\nlevels=" + roundTrip.levels.Length;

            bool copyToGame = Get(command, "copy_to_levels") == "1";
            if (copyToGame)
            {
                string levels = Path.Combine(Path.GetDirectoryName(UnityEngine.Application.dataPath), "Levels");
                Directory.CreateDirectory(levels);
                string fileName = Path.GetFileNameWithoutExtension(path);
                string extension = workshopFormat ? ".bfg" : ".bfc";
                string target = Path.Combine(levels, fileName + extension);
                File.Copy(path, target, true);
                MethodInfo loadCampaignFromDisk = GameType("FileIO").GetMethod("LoadCampaignFromDisk", Static);
                Campaign disk = (Campaign)loadCampaignFromDisk.Invoke(null, new object[] { fileName });
                if (disk == null || disk.levels == null || disk.levels.Length == 0)
                    return "ok=0\\nerror=game failed to reopen copied campaign\\ncopied=" + target;
                extra += "\\ncopied=" + OneLine(target)
                    + "\\ndiskRoundtrip=" + disk.levels[0].Width + "x" + disk.levels[0].Height;
            }
            return "ok=1\\npath=" + OneLine(path) + "\\nformat=" + (workshopFormat ? "bfg" : "bfc")
                + "\\nsize=" + bytes.Length + extra;
        }
'''

pattern = re.compile(
    r"        private static string SaveBfg\(Dictionary<string, string> command\).*?\n        \}\n\n        private static string LoadCurrentCampaign",
    re.S,
)
match = pattern.search(text)
if not match:
    raise SystemExit("SaveBfg block not found")
replacement = new_save + "\n        private static string LoadCurrentCampaign"
text = text[: match.start()] + replacement + text[match.end():]

# list_levels 同时列出 .bfc
text = text.replace(
    'string[] files = Directory.GetFiles(directory, "*.bfg");',
    'string[] files = Directory.GetFiles(directory, "*.bfc");\n                string[] bfgFiles = Directory.GetFiles(directory, "*.bfg");\n                if (bfgFiles.Length > 0) { string[] merged = new string[files.Length + bfgFiles.Length]; Array.Copy(files, merged, files.Length); Array.Copy(bfgFiles, 0, merged, files.Length, bfgFiles.Length); files = merged; }',
)

PATH.write_text(text, encoding="utf-8")
print("patched", PATH)
