"""补丁：桥接增加 player_pos / probe_solid 两个只读诊断命令。"""
from pathlib import Path

PATH = Path(r"E:\BroforceMapEditor\bridge\BroforceMapBridge.cs")
text = PATH.read_text(encoding="utf-8")

text = text.replace(
    '''                case "ensure_player":
                    return EnsurePlayer();''',
    '''                case "ensure_player":
                    return EnsurePlayer();
                case "player_pos":
                    return PlayerPos();
                case "probe_solid":
                    return ProbeSolid(command);''',
)

extra = '''        private static string PlayerPos()
        {
            if (HeroController.players == null || HeroController.players.Length == 0)
                return "ok=0\\nerror=no players";
            Player player = HeroController.players[0];
            if (player == null || player.character == null) return "ok=0\\nerror=no hero";
            UnityEngine.Vector3 p = player.character.transform.position;
            int cx = (int)Math.Floor(p.x / 16.0 + 0.5);
            int cy = (int)Math.Floor(p.y / 16.0 + 0.5);
            bool solid = Map.IsBlockSolid(cx, cy);
            bool below = Map.IsBlockSolid(cx, cy - 1);
            return "ok=1\\nx=" + p.x + "\\ny=" + p.y + "\\ncellX=" + cx + "\\ncellY=" + cy
                + "\\nsolidHere=" + solid + "\\nsolidBelow=" + below
                + "\\nmapSize=" + Map.GetMaxCollumns() + "x" + Map.GetMaxRows();
        }

        private static string ProbeSolid(Dictionary<string, string> command)
        {
            string path = Get(command, "cells");
            if (string.IsNullOrEmpty(path) || !File.Exists(path))
                return "ok=0\\nerror=cells file missing";
            StringBuilder builder = new StringBuilder("ok=1");
            string[] lines = File.ReadAllLines(path, Utf8NoBom);
            for (int index = 0; index < lines.Length; index++)
            {
                string line = lines[index].Trim();
                if (line.Length == 0 || line.StartsWith("#")) continue;
                string[] parts = line.Split(',');
                if (parts.Length < 2) continue;
                int x, y;
                if (!Int32.TryParse(parts[0].Trim(), out x)) continue;
                if (!Int32.TryParse(parts[1].Trim(), out y)) continue;
                builder.Append('\\n').Append(x).Append(',').Append(y).Append('=')
                    .Append(Map.IsBlockSolid(x, y) ? "1" : "0");
            }
            return builder.ToString();
        }

        private static string EnsurePlayer()'''
text = text.replace("        private static string EnsurePlayer()", extra, 1)
PATH.write_text(text, encoding="utf-8")
print("patched player_pos / probe_solid")
