// ToolsSafety —— 存档安全操作（备份 / 立即保存）
//
//   sr2_backup  把当前存档目录里的 .sav 文件复制到 <项目>\backups\saves\<时间戳>_<标签>，
//               逐个算 SHA256 并在返回里核对。写的是项目目录，不碰游戏状态。
//   sr2_save    调用游戏原生 AutoSaveDirector.SaveGameAndFlush()，然后回读存档目录里的
//               最新文件，确认真的写盘了。默认预演。
//
// 两个工具都默认 dry_run=true；执行需 dry_run=false + 唯一 request_id。
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using Il2Cpp;
using Row = System.Collections.Generic.Dictionary<string, object>;

namespace SlimeRancher2Bridge
{
    internal static class ToolsSafety
    {
        internal static void Register(BridgeServer server)
        {
            server.Register("sr2_backup", Backup, mainThread: true, write: true);
            server.Register("sr2_save", SaveNow, mainThread: true, write: true);
        }

        static string ProjectRoot => Path.GetDirectoryName(Path.GetDirectoryName(BridgeConfig.ConfigPath));

        static bool IsPreview(Row args)
            => !(args.TryGetValue("dry_run", out var dry) && dry is bool flag && !flag);

        static string Sha256(string path)
        {
            using (var stream = File.OpenRead(path))
            using (var sha = SHA256.Create())
                return BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "");
        }

        static string NewestSave(string directory)
        {
            if (string.IsNullOrEmpty(directory) || !Directory.Exists(directory)) return null;
            var newest = Directory.GetFiles(directory, "*.sav", SearchOption.AllDirectories)
                .OrderByDescending(File.GetLastWriteTimeUtc).FirstOrDefault();
            return newest == null ? null : Path.GetFileName(newest) + " @ " + File.GetLastWriteTime(newest).ToString("yyyy-MM-dd HH:mm:ss");
        }

        static Row Backup(Row args)
        {
            string directory = ToolsCore.SaveDirectory;
            if (string.IsNullOrEmpty(directory) || !Directory.Exists(directory))
                return new Row { ["available"] = false, ["reason"] = "save_dir_missing", ["save_dir"] = directory };
            var files = Directory.GetFiles(directory, "*.sav", SearchOption.AllDirectories)
                .OrderByDescending(File.GetLastWriteTimeUtc).ToList();
            if (files.Count == 0) return new Row { ["available"] = false, ["reason"] = "no_save_files", ["save_dir"] = directory };

            string saveName = null;
            try { saveName = GameContext.Instance?.AutoSaveDirector?.CurrentSaveGameName(); } catch { }
            bool preview = IsPreview(args);
            var rows = files.Select(f => new Row
            {
                ["name"] = Path.GetFileName(f),
                ["bytes"] = new FileInfo(f).Length,
                ["modified"] = File.GetLastWriteTime(f).ToString("yyyy-MM-dd HH:mm:ss"),
            }).ToList();
            var row = new Row
            {
                ["available"] = true, ["dry_run"] = preview, ["save_dir"] = directory,
                ["save_name"] = saveName, ["file_count"] = files.Count, ["files"] = rows,
            };
            if (preview) return row;

            string label = args.TryGetValue("label", out var value) && value != null ? Convert.ToString(value).Trim() : "";
            string stamp = DateTime.Now.ToString("yyyyMMdd_HHmmss");
            string target = Path.Combine(ProjectRoot, "backups", "saves",
                stamp + (label.Length > 0 ? "_" + label : ""));
            Directory.CreateDirectory(target);
            var copied = new List<Row>();
            foreach (var file in files)
            {
                string destination = Path.Combine(target, Path.GetFileName(file));
                File.Copy(file, destination, true);
                copied.Add(new Row
                {
                    ["name"] = Path.GetFileName(file),
                    ["bytes"] = new FileInfo(destination).Length,
                    ["sha256"] = Sha256(destination),
                });
            }
            row["backup_dir"] = target;
            row["copied"] = copied;
            row["copied_count"] = copied.Count;
            return row;
        }

        static Row SaveNow(Row args) => ToolsNavigation.WithScene(raw =>
        {
            var director = GameContext.Instance?.AutoSaveDirector;
            if (director == null) return new Row { ["available"] = false, ["reason"] = "autosave_director_unavailable" };
            string directory = ToolsCore.SaveDirectory;
            string before = NewestSave(directory);
            bool preview = IsPreview(args);
            var row = new Row
            {
                ["available"] = true, ["dry_run"] = preview,
                ["save_name"] = director.CurrentSaveGameName(),
                ["newest_before"] = before,
            };
            if (preview) return row;

            Il2CppUtil.Invoke(director, "SaveGameAndFlush", null);
            row["invoke_error"] = Il2CppUtil.LastInvokeError;
            string after = NewestSave(directory);
            row["newest_after"] = after;
            row["saved"] = after != null && after != before;
            if (!(row["saved"] is true)) row["reason"] = "newest_file_unchanged";
            return row;
        });
    }
}
