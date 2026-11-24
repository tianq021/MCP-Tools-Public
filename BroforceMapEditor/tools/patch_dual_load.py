"""补丁：TryLoadLevel 同时尝试 .bfc（不加密）与 .bfg（加密）。"""
from pathlib import Path

PATH = Path(r"E:\BroforceMapEditor\bridge\BroforceMapBridge.cs")
text = PATH.read_text(encoding="utf-8")

old = """                if (!string.IsNullOrEmpty(path))
                {
                    MethodInfo loadCampaignBytes = GameType("FileIO").GetMethod("LoadCampaignBytes", Static);
                    if (loadCampaignBytes == null) return false;
                    Campaign campaign = loadCampaignBytes.Invoke(null,
                        new object[] { File.ReadAllBytes(path), false, true }) as Campaign;
                    if (campaign == null || campaign.levels == null || campaign.levels.Length == 0) return false;
                    map = campaign.levels[0];
                    return map != null;
                }"""
new = """                if (!string.IsNullOrEmpty(path))
                {
                    MethodInfo loadCampaignBytes = GameType("FileIO").GetMethod("LoadCampaignBytes", Static);
                    if (loadCampaignBytes == null) return false;
                    byte[] bytes = File.ReadAllBytes(path);
                    // .bfc = 不加密；.bfg = 加密。两种都试一次。
                    bool[] encryptedOptions = new bool[] { false, true };
                    for (int index = 0; index < encryptedOptions.Length; index++)
                    {
                        try
                        {
                            Campaign campaign = loadCampaignBytes.Invoke(null,
                                new object[] { bytes, encryptedOptions[index], true }) as Campaign;
                            if (campaign == null || campaign.levels == null || campaign.levels.Length == 0) continue;
                            map = campaign.levels[0];
                            if (map != null) return true;
                        }
                        catch (Exception) { }
                    }
                    return false;
                }"""
if old not in text:
    raise SystemExit("anchor not found")
PATH.write_text(text.replace(old, new), encoding="utf-8")
print("patched dual-format load")
