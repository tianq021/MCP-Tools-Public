"""写工具运行中保护审计测试（不执行真实恢复；只用临时文件 + monkeypatch）。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bl2toolkit import paths  # noqa: E402


class RestoreGuardTest(unittest.TestCase):
    def _fake_backup(self, tmp: Path, target: Path) -> Path:
        backup = tmp / "backup"
        backup.mkdir()
        (backup / "fake.sav").write_bytes(b"FAKE SAVE DATA")
        (backup / "meta.json").write_text(
            json.dumps({"source": str(target), "created": "2026-11-15"}), encoding="utf-8"
        )
        return backup

    def test_restore_blocked_while_game_running(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            target = tmp / "target.sav"
            target.write_bytes(b"ORIGINAL")
            backup = self._fake_backup(tmp, target)
            with mock.patch.object(paths, "is_game_running", return_value=True):
                with self.assertRaises(RuntimeError):
                    paths.restore_backup(backup)
            # 关键：运行中保护触发时不能改动目标文件
            self.assertEqual(target.read_bytes(), b"ORIGINAL")

    def test_restore_allowed_offline_on_temp_copy(self):
        """离线时只对临时文件验证恢复链路，不碰真实存档。"""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            target = tmp / "target.sav"
            target.write_bytes(b"ORIGINAL")
            backup = self._fake_backup(tmp, target)
            with mock.patch.object(paths, "is_game_running", return_value=False), mock.patch.object(paths, "backup_save", return_value=None):
                dest = paths.restore_backup(backup)
            self.assertEqual(dest, target)
            self.assertEqual(target.read_bytes(), b"FAKE SAVE DATA")

    def test_mcp_restore_returns_error_while_running(self):
        try:
            from bl2toolkit import mcp_server
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"无法导入 mcp_server: {exc}")
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            target = tmp / "target.sav"
            target.write_bytes(b"ORIGINAL")
            backup = self._fake_backup(tmp, target)
            with mock.patch.object(paths, "is_game_running", return_value=True):
                result = mcp_server.bl2_restore_backup(str(backup))
            self.assertFalse(result.get("written", True))
            self.assertIn("error", result)
            self.assertEqual(target.read_bytes(), b"ORIGINAL")


class WriteGuardAuditTest(unittest.TestCase):
    def test_save_apis_have_guard(self):
        """审计性测试：sav/profile/exe/backup 四条写路径都必须有运行中保护。"""
        import bl2toolkit.saves as saves
        import bl2toolkit.profile as profile
        import bl2toolkit.space as space

        for fn, name in (
            (saves.SaveFile.save, "SaveFile.save"),
            (profile.ProfileFile.save, "ProfileFile.save"),
            (space.patch_exe, "space.patch_exe"),
            (space.restore_exe_backup, "space.restore_exe_backup"),
            (paths.restore_backup, "paths.restore_backup"),
        ):
            src = Path(fn.__code__.co_filename).read_text(encoding="utf-8", errors="replace")
            self.assertIn("is_game_running", src, f"{name} 缺少 is_game_running 保护")


if __name__ == "__main__":
    unittest.main()
